"""Newtron device hub.

One hub instance per device (per config entry). The hub owns the MQTT
subscriptions for the device topic, maintains the device state model
(DeviceStatus / StatusAll / RelayLayout / VThermostat / AOMode), and
implements synchronous command semantics on top of MQTT by correlating
``cmnd/<topic>/<Command>`` publishes with the matching JSON key in
``stat/<topic>/...`` responses.

Reference: "Smart Newtron — REST API & MQTT Command Reference" (June 2026).
MQTT commands do not require the API key (doc §2), so the hub never
handles it.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from collections.abc import Callable
from typing import Any

from homeassistant.components import mqtt
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_send

from .const import (
    COMMAND_TIMEOUT,
    DEVICE_STATUS_TIMEOUTS,
    FAMILY_DIMMER,
    FAMILY_IR,
    FAMILY_DALI,
    FAMILY_PRO,
    FAMILY_RELAY,
    LAYOUT_REFRESH_EVERY,
    SIGNAL_UPDATE,
)

_LOGGER = logging.getLogger(__name__)

_TRAILING_DIGITS = re.compile(r"\d+$")
# DALI: the light fades to its new level; read it back after these waits (s)
DALI_READ_BACK = (2, 4)


def _base_command(command: str) -> str:
    """Strip a trailing index and any space-separated payload from a command.

    ``Power1`` -> ``power``; ``RollerConfig0`` -> ``rollerconfig``;
    ``NvLocalMode 1 {...}`` -> ``nvlocalmode``.
    """
    head = command.split(" ", 1)[0]
    return _TRAILING_DIGITS.sub("", head).lower()


def _sensor_reading(value: dict[str, Any]) -> dict[str, Any]:
    """One probe of StatusAll / SENSOR, whatever the casing."""
    return {
        "id": value.get("Id") or value.get("id"),
        "temperature": value.get("Temperature", value.get("temperature")),
        "humidity": value.get("Humidity", value.get("humidity")),
    }


class NewtronHub:
    """Manages one Newtron device over MQTT."""

    def __init__(self, hass: HomeAssistant, entry_id: str, topic: str) -> None:
        self.hass = hass
        self.entry_id = entry_id
        self.topic = topic
        # Newtron firmware ships with the full topic set to d/%topic%/%prefix%/
        # and keeps it whichever broker it points at, so the same layout works
        # for the vendor cloud and for a local broker. The plain Tasmota layout
        # (cmnd/<topic>/...) from the API reference is never used in practice.
        self._cmnd = f"d/{topic}/cmnd/"
        self._stat = f"d/{topic}/stat/"
        self._tele = f"d/{topic}/tele/"

        self._unsubs: list[Callable[[], None]] = []
        self._pending: dict[str, list[asyncio.Future]] = {}

        # --- state model -------------------------------------------------
        self.available: bool = True  # flips on tele/LWT
        self.device_info_raw: dict[str, Any] = {}
        self.capabilities: dict[str, Any] = {}
        self.family: str = FAMILY_RELAY

        self.status_all: dict[str, Any] = {}
        self.relay_layout: dict[str, Any] = {}
        self.relays: dict[int, bool] = {}            # relay index -> on/off
        self.ao: dict[int, int] = {}                  # AO index -> percent
        self.ao_modes: dict[int, int] = {}            # AO index -> 0 push / 1 bistable
        self.inputs: dict[int, bool] = {}
        self.vars: dict[int, str] = {}                # "1"/"0"/""
        self.mems: dict[int, str] = {}
        self.shutters: dict[int, dict[str, Any]] = {} # 1-based -> {Position, Direction, Target, Tilt}
        self.shutter_configs: list[dict[str, Any]] = []
        self.vthermostats: dict[int, dict[str, Any]] = {}
        self.sensors: dict[str, dict[str, Any]] = {}  # "DS18B20-1" -> {id, temperature}
        # SMART DALI: light N -> {name, state, status, level} (StatusAll / DaliState)
        self.dali_lights: dict[int, dict[str, Any]] = {}
        # SMART DALI: DaliConfig (lights with groups/scenes, groups, scenes, read)
        self.dali_config: dict[str, Any] = {}
        self._dali_shape: tuple | None = None
        self.temp_unit: str = "C"
        self.last_update: float = 0.0
        self._polls: int = 0
        # Called with the new RelayLayout when the board's names change.
        self.on_layout_changed: Callable[[dict[str, Any]], None] | None = None

    # ------------------------------------------------------------------
    # lifecycle
    # ------------------------------------------------------------------
    async def async_setup(self) -> None:
        """Subscribe and load the initial device model."""
        self._unsubs.append(
            await mqtt.async_subscribe(self.hass, f"{self._stat}#", self._handle_stat)
        )
        self._unsubs.append(
            await mqtt.async_subscribe(self.hass, f"{self._tele}#", self._handle_tele)
        )

        # Initial model. DeviceStatus is mandatory; the rest is best-effort
        # because sections are board-dependent. Right after Home Assistant
        # starts the first request is often lost (broker subscription not
        # active yet), so it is resent after a short wait, then with longer
        # waits for a slow card, before giving up.
        device_status = None
        for timeout in DEVICE_STATUS_TIMEOUTS:
            device_status = await self.async_command("DeviceStatus", timeout=timeout)
            if device_status:
                break
        if device_status:
            self._apply_device_status(device_status)

        status_all = await self.async_command("StatusAll")
        if status_all:
            self._apply_status_all(status_all.get("StatusAll", {}))

        self._detect_family()

        # Probes wired on the GPIO (DS18x20, DHT...) so that their entities
        # exist from the start, not only after the first SENSOR telemetry.
        if self.family in (FAMILY_RELAY, FAMILY_PRO):
            sns = await self.async_command("Status 10", timeout=4)
            if sns and isinstance(sns.get("StatusSNS"), dict):
                self._apply_sensor_telemetry(sns["StatusSNS"])

        if self.family in (FAMILY_RELAY, FAMILY_PRO):
            layout = await self.async_command("RelayLayout")
            if layout and "RelayLayout" in layout:
                self._set_relay_layout(layout["RelayLayout"])

        if self.family == FAMILY_DALI:
            await self.async_reload_dali_config()
            self._dali_shape = self._dali_layout_shape()

        if self.capabilities.get("max_vthermostats"):
            vt = await self.async_command("VThermostat")
            if vt:
                self._apply_vthermostats(vt)

        if self.family == FAMILY_DIMMER:
            modes = await self.async_command("AOMode0")
            if modes:
                self._apply_ao_modes(modes)

        self._notify()

    async def async_unload(self) -> None:
        for unsub in self._unsubs:
            unsub()
        self._unsubs.clear()
        for futures in self._pending.values():
            for fut in futures:
                if not fut.done():
                    fut.cancel()
        self._pending.clear()

    async def async_refresh(self, _now: Any = None) -> None:
        """Periodic refresh: StatusAll is the consolidated snapshot (doc §15).

        Every LAYOUT_REFRESH_EVERY polls the RelayLayout is read again so
        names edited on the board reach Home Assistant.
        """
        result = await self.async_command("StatusAll")
        if result:
            self._apply_status_all(result.get("StatusAll", {}))

        self._polls += 1
        if self.family in (FAMILY_RELAY, FAMILY_PRO) and (
            self._polls % LAYOUT_REFRESH_EVERY == 0
        ):
            await self.async_reload_layout()

        if self.family == FAMILY_DALI:
            # Groups and scenes are read from the ballasts in the background:
            # ask again until the board has them.
            if not (self.dali_config.get("read") or {}).get("groups"):
                await self.async_reload_dali_config()
            shape = self._dali_layout_shape()
            if shape != self._dali_shape:
                _LOGGER.info("%s: DALI lights, groups or scenes changed, reloading", self.topic)
                self._dali_shape = shape
                self.hass.config_entries.async_schedule_reload(self.entry_id)

        if result:
            self._notify()

    async def _async_back_online(self) -> None:
        """A card back from a restart may run a new firmware or revision:
        rebuild its entities and device info when it does."""
        before = (self.firmware_version, self.board_name, self.hardware_revision)
        for timeout in DEVICE_STATUS_TIMEOUTS:
            status = await self.async_command("DeviceStatus", timeout=timeout)
            if status:
                break
        else:
            return
        self._apply_device_status(status)
        if (self.firmware_version, self.board_name, self.hardware_revision) != before:
            _LOGGER.info(
                "%s now runs %s (%s), reloading", self.topic, self.firmware_version, self.board_name
            )
            self.hass.config_entries.async_schedule_reload(self.entry_id)

    # ------------------------------------------------------------------
    # SMART DALI
    # ------------------------------------------------------------------
    async def async_reload_dali_config(self) -> None:
        """Read DaliConfig: names, group members, scenes (doc §21.3)."""
        res = await self.async_command("DaliConfig", timeout=4)
        if res and isinstance(res.get("DaliConfig"), dict):
            self.dali_config = res["DaliConfig"]

    def _dali_layout_shape(self) -> tuple:
        """What decides which DALI entities exist: lights, groups, scenes."""
        return (
            tuple(sorted(self.dali_lights)),
            tuple(sorted(self.dali_groups)),
            tuple(sorted(self.dali_scenes)),
        )

    @property
    def dali_groups(self) -> dict[int, dict[str, Any]]:
        """Group G -> {name, members}: groups named or holding a light."""
        return {
            int(k[5:]): v for k, v in (self.dali_config.get("groups") or {}).items()
            if k.startswith("group") and k[5:].isdigit()
        }

    @property
    def dali_scenes(self) -> dict[int, dict[str, Any]]:
        """Scene S -> {name, enabled, lights}: scenes named, enabled or used."""
        return {
            int(k[5:]): v for k, v in (self.dali_config.get("scenes") or {}).items()
            if k.startswith("scene") and k[5:].isdigit()
        }

    @callback
    def async_dali_follow(self, lights: list[int], level: int | None = None) -> None:
        """After an order: show the level asked for at once, then read the real
        one when the fade is over (the light moves gradually to its level)."""
        if level is not None:
            for n in lights:
                if n in self.dali_lights:
                    self.dali_lights[n] = {**self.dali_lights[n], "level": level}
            self._notify()

        async def _read_back() -> None:
            for delay in DALI_READ_BACK:
                await asyncio.sleep(delay)
                for n in lights:
                    await self.async_dali_read_light(n)

        self.hass.async_create_background_task(_read_back(), f"newtron dali {self.topic}")

    async def async_dali_read_light(self, idx: int) -> None:
        """DaliState<N>: the real level of light N, read on the bus (~80 ms)."""
        res = await self.async_command(f"DaliState{idx}", timeout=3)
        state = (res or {}).get("DaliState")
        if isinstance(state, dict) and state.get("light") == idx:
            merged = dict(self.dali_lights.get(idx, {}))
            merged.update({k: v for k, v in state.items() if k != "light"})
            self.dali_lights[idx] = merged
            self._notify()

    async def async_read_gpios(self) -> dict[int, int] | None:
        """User-configurable GPIO and what is set on each: {25: 1312, ...}."""
        res = await self.async_command("Gpio", timeout=4)
        if res is None:
            return None
        gpios: dict[int, int] = {}
        for key, value in res.items():
            if key.upper().startswith("GPIO") and key[4:].isdigit() and isinstance(value, dict) and value:
                gpios[int(key[4:])] = int(next(iter(value)))
        return gpios

    async def async_reload_layout(self) -> None:
        """Read RelayLayout again (after a poll or after writing names)."""
        layout = await self.async_command("RelayLayout")
        if layout and "RelayLayout" in layout:
            self._apply_relay_layout(layout["RelayLayout"])

    def _set_relay_layout(self, layout: dict[str, Any]) -> None:
        self.relay_layout = layout
        self.shutter_configs = list(layout.get("roller_shutters", []))

    @staticmethod
    def _layout_shape(layout: dict[str, Any]) -> tuple:
        """What decides which entities exist: free outputs and shutter count."""
        return (
            tuple(sorted(out["output"] for out in layout.get("free_outputs", []))),
            len(layout.get("roller_shutters", [])),
        )

    def _apply_relay_layout(self, layout: dict[str, Any]) -> None:
        """Take a fresh RelayLayout; reload the entry if entities must change."""
        if layout == self.relay_layout:
            return
        reshaped = self._layout_shape(layout) != self._layout_shape(self.relay_layout)
        self._set_relay_layout(layout)
        if self.on_layout_changed is not None:
            self.on_layout_changed(layout)
        if reshaped:
            _LOGGER.info("%s: outputs or shutters changed on the board, reloading", self.topic)
            self.hass.config_entries.async_schedule_reload(self.entry_id)
        else:
            self._notify()

    # ------------------------------------------------------------------
    # command plumbing
    # ------------------------------------------------------------------
    async def async_command(
        self,
        command: str,
        payload: str = "",
        wait: bool = True,
        timeout: float = COMMAND_TIMEOUT,
    ) -> dict[str, Any] | None:
        """Publish cmnd/<topic>/<command> and optionally await the stat response."""
        fut: asyncio.Future | None = None
        key = _base_command(command)
        if wait:
            fut = self.hass.loop.create_future()
            self._pending.setdefault(key, []).append(fut)

        # Commands documented with an inline argument (e.g. "NvLocalMode 1 {...}",
        # "TimeDiscover 1 1609459200") arrive here already assembled in `command`
        # when payload is empty; otherwise payload goes in the MQTT payload.
        topic = f"{self._cmnd}{command.split(' ', 1)[0]}"
        body = payload
        if " " in command and not payload:
            body = command.split(" ", 1)[1]
        await mqtt.async_publish(self.hass, topic, body)

        if fut is None:
            return None
        try:
            return await asyncio.wait_for(fut, timeout)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            _LOGGER.debug("Timeout waiting for response to %s on %s", command, self.topic)
            return None
        finally:
            pending = self._pending.get(key, [])
            if fut in pending:
                pending.remove(fut)
            if not pending:
                self._pending.pop(key, None)

    @callback
    def _resolve_pending(self, data: dict[str, Any]) -> None:
        if not self._pending:
            return
        keys_lower = [k.lower() for k in data]
        # Some firmware replies are flat, with no key named after the command
        # (e.g. DeviceStatus -> {"device_info": ..., "capabilities": ...}).
        # Map those signatures back to the originating command so the awaiting
        # future resolves instead of timing out.
        if "device_info" in data:
            keys_lower.append("devicestatus")

        # "Not configured yet" replies (e.g. RollerConfig0 on a device with no
        # shutter saved) come back as a bare {"error": "No config found"} with
        # no key naming the command. Since only one command is normally in
        # flight from a given caller, resolve every pending future with this
        # reply rather than letting it time out — callers already handle a
        # missing config key by treating the result as empty.
        # A command the firmware does not know: {"Command":"Unknown","Input":"AONAME"}
        if data.get("Command") == "Unknown" and isinstance(data.get("Input"), str):
            unknown = _base_command(data["Input"])
            for fut in self._pending.get(unknown, []):
                if not fut.done():
                    fut.set_result(data)
            return

        if set(keys_lower) == {"error"}:
            for base, futures in list(self._pending.items()):
                for fut in futures:
                    if not fut.done():
                        fut.set_result(data)
            return

        for base, futures in list(self._pending.items()):
            if any(k.startswith(base) for k in keys_lower):
                for fut in futures:
                    if not fut.done():
                        fut.set_result(data)

    # ------------------------------------------------------------------
    # MQTT handlers
    # ------------------------------------------------------------------
    @callback
    def _handle_stat(self, msg: mqtt.ReceiveMessage) -> None:
        suffix = msg.topic[len(self._stat):]
        payload = msg.payload

        # Tasmota publishes plain ON/OFF on stat/<topic>/POWER<n>
        if suffix.startswith("POWER") and payload in ("ON", "OFF"):
            idx_str = suffix[5:] or "1"
            try:
                self.relays[int(idx_str)] = payload == "ON"
            except ValueError:
                return
            self._notify()
            return

        try:
            data = json.loads(payload)
        except (ValueError, TypeError):
            return
        if not isinstance(data, dict):
            return

        self._apply_result(data)
        self._resolve_pending(data)
        self._notify()

    @callback
    def _handle_tele(self, msg: mqtt.ReceiveMessage) -> None:
        suffix = msg.topic[len(self._tele):]
        if suffix == "LWT":
            was_available = self.available
            self.available = msg.payload == "Online"
            if self.available and not was_available:
                self.hass.async_create_background_task(
                    self._async_back_online(), f"newtron back online {self.topic}"
                )
            self._notify()
            return
        try:
            data = json.loads(msg.payload)
        except (ValueError, TypeError):
            return
        if not isinstance(data, dict):
            return
        if suffix == "SENSOR":
            self._apply_sensor_telemetry(data)
        else:  # STATE and others may carry POWER keys
            self._apply_result(data)
        self._notify()

    # ------------------------------------------------------------------
    # state application
    # ------------------------------------------------------------------
    def _apply_device_status(self, data: dict[str, Any]) -> None:
        body = data.get("DeviceStatus", data)
        if "device_info" in body:
            self.device_info_raw = body
            self.capabilities = body.get("capabilities", {}) or {}

    def _detect_family(self) -> None:
        board = (self.device_info_raw.get("device_info", {}) or {}).get("board_name", "")
        if re.search(r"\bIR\b", board.upper()):
            self.family = FAMILY_IR
        elif self.capabilities.get("dali"):
            self.family = FAMILY_DALI
        elif "ao" in self.status_all:
            self.family = FAMILY_DIMMER
        elif "max_roller_shutters" in self.capabilities or "max_alarms" in self.capabilities:
            self.family = FAMILY_RELAY
        elif "DIM" in board.upper():
            self.family = FAMILY_DIMMER
        elif "PRO" in board.upper():
            self.family = FAMILY_PRO
        else:
            self.family = FAMILY_RELAY
        _LOGGER.debug("%s detected as board family: %s", self.topic, self.family)

    def _apply_status_all(self, body: dict[str, Any]) -> None:
        self.status_all = body
        self.last_update = time.time()

        for key, value in (body.get("inputs") or {}).items():
            if key.startswith("input"):
                self.inputs[int(key[5:])] = value == "ON"
        for key, value in (body.get("relays") or {}).items():
            if key.startswith("relay"):
                self.relays[int(key[5:])] = value == "ON"
        for key, value in (body.get("ao") or {}).items():
            if key.startswith("ao"):
                self.ao[int(key[2:])] = int(value)
        for key, value in (body.get("volatile_variables") or {}).items():
            if key.startswith("var"):
                self.vars[int(key[3:])] = str(value)
        for key, value in (body.get("persistant_variables") or {}).items():
            if key.startswith("mem"):
                self.mems[int(key[3:])] = str(value)
        for key, value in (body.get("lights") or {}).items():
            if key.startswith("light") and key[5:].isdigit() and isinstance(value, dict):
                self.dali_lights[int(key[5:])] = value
        for key, value in (body.get("roller_shutters") or {}).items():
            if key.startswith("roller_shutter"):
                self.shutters[int(key[14:])] = value
        vts = body.get("vthermostats") or {}
        for key, value in vts.items():
            if key.startswith("vthermostat"):
                idx = int(key[11:])
                merged = dict(self.vthermostats.get(idx, {}))
                merged.update(value)
                self.vthermostats[idx] = merged
        sensors = body.get("sensors") or {}
        for key, value in sensors.items():
            if key == "temp_unit":
                self.temp_unit = value
            elif isinstance(value, dict):
                self.sensors[key] = _sensor_reading(value)

    def _apply_sensor_telemetry(self, data: dict[str, Any]) -> None:
        """tele/<topic>/SENSOR uses Tasmota casing: {"DS18B20-1":{"Id","Temperature"}},
        {"AM2301":{"Temperature","Humidity"}}."""
        for key, value in data.items():
            if key in ("ESP32", "ESP8266", "Global"):
                continue  # chip temperature / averages, not a wired probe
            if isinstance(value, dict) and any(
                k in value for k in ("Temperature", "temperature", "Humidity", "humidity")
            ):
                self.sensors[key] = _sensor_reading(value)
        if "TempUnit" in data:
            self.temp_unit = data["TempUnit"]

    def _apply_vthermostats(self, data: dict[str, Any]) -> None:
        for key, value in data.items():
            if key.lower().startswith("vthermostat") and isinstance(value, dict):
                suffix = key[11:]
                if suffix.isdigit():
                    idx = int(suffix)
                    merged = dict(self.vthermostats.get(idx, {}))
                    merged.update(value)
                    self.vthermostats[idx] = merged

    def _apply_ao_modes(self, data: dict[str, Any]) -> None:
        for key, value in data.items():
            if key.startswith("AOMode") and key[6:].isdigit():
                self.ao_modes[int(key[6:])] = int(value)

    def _apply_result(self, data: dict[str, Any]) -> None:
        if "StatusAll" in data:
            self._apply_status_all(data["StatusAll"])
            return
        if "DeviceStatus" in data or "device_info" in data:
            self._apply_device_status(data)
            return
        if "RelayLayout" in data:
            self.relay_layout = data["RelayLayout"]
            return
        self._apply_vthermostats(data)
        self._apply_ao_modes(data)
        for key, value in data.items():
            upper = key.upper()
            if upper.startswith("POWER") and value in ("ON", "OFF"):
                idx_str = key[5:] or "1"
                if idx_str.isdigit():
                    self.relays[int(idx_str)] = value == "ON"
            elif upper.startswith("AOON") and key[4:].isdigit():
                self.ao[int(key[4:])] = int(value)
            elif upper.startswith("AOOFF") and key[5:].isdigit():
                self.ao[int(key[5:])] = int(value)
            elif (
                upper.startswith("AO")
                and key[2:].isdigit()
                and isinstance(value, (int, float))
            ):
                self.ao[int(key[2:])] = int(value)

    # ------------------------------------------------------------------
    # convenience accessors used by entities / panel
    # ------------------------------------------------------------------
    @property
    def serial_number(self) -> str:
        return (self.device_info_raw.get("device_info", {}) or {}).get(
            "serial_number", self.topic
        )

    @property
    def hardware_revision(self) -> int | None:
        """8R / 16R: 0 = not declared, 1 = v1, 2 = v2; None on other boards."""
        rev = (self.device_info_raw.get("device_info", {}) or {}).get("hardware_revision")
        return int(rev) if rev is not None else None

    @property
    def board_name(self) -> str:
        return (self.device_info_raw.get("device_info", {}) or {}).get("board_name", "")

    @property
    def firmware_version(self) -> str:
        return (self.device_info_raw.get("device_info", {}) or {}).get(
            "firmware_version", ""
        )

    @property
    def ip_address(self) -> str | None:
        return (self.device_info_raw.get("network", {}) or {}).get("ip")

    def free_relay_indices(self) -> list[int]:
        """Relays not consumed by the roller shutter driver.

        Doc §3: shutter relays must not be toggled directly via Power.
        """
        if self.relay_layout.get("free_outputs"):
            return sorted(
                out["output"] for out in self.relay_layout["free_outputs"]
            )
        if self.relays:
            return sorted(self.relays)
        outputs = int(self.capabilities.get("outputs", 0) or 0)
        return list(range(1, outputs + 1))

    def relay_name(self, idx: int) -> str | None:
        for out in self.relay_layout.get("free_outputs", []):
            if out.get("output") == idx:
                name = (out.get("name") or "").strip()
                if name:
                    return name
        return None

    def shutter_name(self, idx: int) -> str | None:
        """Name of the 1-based shutter ``idx`` as set on the board."""
        if 0 < idx <= len(self.shutter_configs):
            return (self.shutter_configs[idx - 1].get("name") or "").strip() or None
        return None

    @callback
    def _notify(self) -> None:
        async_dispatcher_send(self.hass, SIGNAL_UPDATE.format(self.entry_id))

    def snapshot(self) -> dict[str, Any]:
        """Serializable snapshot for the panel websocket API."""
        return {
            "entry_id": self.entry_id,
            "topic": self.topic,
            "serial_number": self.serial_number,
            "board_name": self.board_name,
            "firmware_version": self.firmware_version,
            "family": self.family,
            "available": self.available,
            "capabilities": self.capabilities,
            "device_status": self.device_info_raw,
            "status_all": self.status_all,
            "relay_layout": self.relay_layout,
            "vthermostats": {str(k): v for k, v in self.vthermostats.items()},
            "ao_modes": {str(k): v for k, v in self.ao_modes.items()},
            "hardware_revision": self.hardware_revision,
            "dali_config": self.dali_config,
            "last_update": self.last_update,
        }
