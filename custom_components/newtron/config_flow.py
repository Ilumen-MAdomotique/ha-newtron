"""Config flow for Newtron Smart.

Three paths:
- Auto discovery: publish ``Discover`` on the Tasmota group topic and
  listen on ``stat/+/RESULT`` for the (unauthenticated, doc §14) reply
  carrying the serial number and MAC.
- Manual: the user types the card serial number; the flow finds the card
  on the broker by asking it (or every card seen there) for ``Discover``.
- Broker watch (inventory.py): a card that comes online starts a flow on
  its own. Cards listed in the inventory are added without asking; others
  wait for a one-click confirmation under "Discovered".
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

import voluptuous as vol

from homeassistant.components import mqtt
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import callback

from .const import (
    CONF_MAC,
    CONF_SCAN_INTERVAL,
    CONF_SERIAL,
    CONF_TOPIC,
    CONF_TOPIC_SCHEME,
    DATA_INVENTORY,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_TOPIC_SCHEME,
    DOMAIN,
)
from .inventory import identity_from_payload

_LOGGER = logging.getLogger(__name__)

DISCOVERY_WINDOW = 5.0  # seconds listening for Discover replies
RETAINED_WINDOW = 2.5  # seconds collecting retained broker announcements
GROUP_TOPIC = "newtrons"  # every card answers d/newtrons/cmnd/<Cmd> (checked on fw 2.6.0)


class NewtronConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the Newtron config flow."""

    VERSION = 1

    def __init__(self) -> None:
        self._discovered: dict[str, dict[str, str]] = {}
        self._discovery_info: dict[str, Any] = {}

    async def async_step_integration_discovery(
        self, discovery_info: dict[str, Any]
    ) -> ConfigFlowResult:
        """A card came online on the broker (see inventory.py)."""
        serial = discovery_info["serial"]
        # A card listed in the inventory goes through even if it is already
        # waiting under "Discovered"; creating the entry closes that offer.
        await self.async_set_unique_id(
            serial, raise_on_progress=not discovery_info.get("inventory_name")
        )
        # A known card that moved to another topic just follows it.
        self._abort_if_unique_id_configured(
            updates={CONF_TOPIC: discovery_info[CONF_TOPIC]}
        )
        self._discovery_info = discovery_info
        board = discovery_info.get("board") or ""
        self.context["title_placeholders"] = {
            "name": f"{serial} — {board}" if board else serial
        }
        if discovery_info.get("inventory_name"):
            return self._async_create_discovered()
        return await self.async_step_confirm()

    async def async_step_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            return self._async_create_discovered()
        self._set_confirm_only()
        info = self._discovery_info
        return self.async_show_form(
            step_id="confirm",
            description_placeholders={
                "serial": info["serial"],
                "board": info.get("board") or "?",
                "topic": info[CONF_TOPIC],
            },
        )

    @callback
    def _async_create_discovered(self) -> ConfigFlowResult:
        info = self._discovery_info
        return self.async_create_entry(
            title=info.get("inventory_name") or info["serial"],
            data={
                CONF_TOPIC: info[CONF_TOPIC],
                CONF_SERIAL: info["serial"],
                CONF_MAC: info.get("mac", ""),
                CONF_TOPIC_SCHEME: DEFAULT_TOPIC_SCHEME,
            },
        )

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if not await mqtt.async_wait_for_mqtt_client(self.hass):
            return self.async_abort(reason="mqtt_unavailable")
        return self.async_show_menu(step_id="user", menu_options=["discover", "manual"])

    async def async_step_discover(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """List the devices the broker knows about and let the user pick one.

        Newtron devices retain two announcements on the broker: their LWT
        (``d/<topic>/tele/LWT``) and, for Tasmota-based boards, a discovery
        payload (``tasmota/discovery/<MAC>/config``) carrying the topic and
        board model. Reading those gives the candidate topics without
        guessing, and each candidate is then probed with ``Discover`` to
        confirm it answers and to read its serial number.
        """
        if user_input is not None:
            topic = user_input[CONF_TOPIC]
            info = self._discovered[topic]
            return await self._async_create(topic, info["serial"], info["mac"])

        self._discovered = {}
        candidates: dict[str, dict[str, Any]] = {}

        @callback
        def _on_lwt(msg: mqtt.ReceiveMessage) -> None:
            parts = msg.topic.split("/")
            if len(parts) == 4 and parts[1]:
                candidates.setdefault(parts[1], {})["online"] = (
                    msg.payload == "Online"
                )

        @callback
        def _on_announce(msg: mqtt.ReceiveMessage) -> None:
            try:
                data = json.loads(msg.payload)
            except (ValueError, TypeError):
                return
            if not isinstance(data, dict) or not data.get("t"):
                return
            candidates.setdefault(data["t"], {}).update(
                model=data.get("md", ""), mac=data.get("mac", ""), announced=True
            )

        unsubs = [
            await mqtt.async_subscribe(self.hass, "d/+/tele/LWT", _on_lwt),
            await mqtt.async_subscribe(
                self.hass, "tasmota/discovery/+/config", _on_announce
            ),
        ]
        try:
            # Retained messages land as soon as the subscription is accepted.
            await asyncio.sleep(RETAINED_WINDOW)
        finally:
            for unsub in unsubs:
                unsub()

        existing = {
            entry.data.get(CONF_TOPIC)
            for entry in self._async_current_entries(include_ignore=False)
        }
        pending = [t for t in candidates if t not in existing]
        if pending:
            await self._async_probe_many(pending, candidates)

        # Devices that answered are offered first, with their serial number.
        # A device that stayed silent is still worth offering as long as its
        # Tasmota announcement is present: that message is keyed by MAC, so
        # there is exactly one per physical device and it always carries the
        # current topic. Topics seen only through a retained LWT are dropped —
        # renaming a device leaves its old LWT behind forever, and those stale
        # names would otherwise show up as devices that can never be set up.
        for topic in pending:
            if topic in self._discovered:
                continue
            if not candidates[topic].get("announced"):
                continue
            self._discovered[topic] = {
                "serial": topic,
                "mac": candidates[topic].get("mac", ""),
                "board": candidates[topic].get("model", ""),
                "offline": True,
            }

        if not self._discovered:
            stale = len(pending)
            return self.async_show_menu(
                step_id="user",
                menu_options=["discover", "manual"],
                description_placeholders={
                    "info": (
                        f"No new Newtron device found. {stale} obsolete topic(s) "
                        "are still retained on the broker and were ignored."
                        if stale
                        else "No new Newtron device found on this broker."
                    )
                },
            )

        def _label(topic: str, info: dict[str, Any]) -> str:
            parts = [info["serial"]] if info["serial"] != topic else []
            if info.get("board"):
                parts.append(info["board"])
            label = " — ".join(parts)
            label = f"{label} ({topic})" if label else topic
            return f"{label} — not answering" if info.get("offline") else label

        options = {
            topic: _label(topic, info)
            for topic, info in sorted(
                self._discovered.items(), key=lambda kv: (kv[1].get("offline", False), kv[0])
            )
        }
        return self.async_show_form(
            step_id="discover",
            data_schema=vol.Schema({vol.Required(CONF_TOPIC): vol.In(options)}),
        )

    async def _async_probe_many(
        self, topics: list[str], candidates: dict[str, dict[str, Any]]
    ) -> None:
        """Ask every candidate topic to identify itself, in one time window."""

        @callback
        def _on_result(msg: mqtt.ReceiveMessage) -> None:
            parts = msg.topic.split("/")
            if len(parts) != 4:
                return
            topic = parts[1]
            identity = identity_from_payload(msg.payload, topic)
            if identity is None:
                return
            if not identity.get("board"):
                identity["board"] = candidates.get(topic, {}).get("model", "")
            self._discovered[topic] = identity

        unsub = await mqtt.async_subscribe(self.hass, "d/+/stat/RESULT", _on_result)
        try:
            for topic in topics:
                await mqtt.async_publish(self.hass, f"d/{topic}/cmnd/Discover", "")
            await asyncio.sleep(DISCOVERY_WINDOW)
        finally:
            unsub()

    async def async_step_manual(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        # Every Newtron device observed in the field uses the same MQTT topic
        # layout (d/<topic>/cmnd|stat|tele/...) regardless of whether the
        # broker is the vendor's cloud one or a local broker such as this
        # Home Assistant instance, so the scheme is no longer asked here.
        # The card is identified by its serial number only: its topic is
        # whatever it answers on, the installer never has to know it.
        errors: dict[str, str] = {}
        if user_input is not None:
            found = await self._async_find_serial(user_input[CONF_SERIAL].strip())
            if found is None:
                errors["base"] = "no_response"
            else:
                topic, identity = found
                return await self._async_create(
                    topic, identity["serial"], identity["mac"], DEFAULT_TOPIC_SCHEME
                )

        return self.async_show_form(
            step_id="manual",
            data_schema=vol.Schema({vol.Required(CONF_SERIAL): str}),
            errors=errors,
        )

    async def _async_find_serial(
        self, serial: str
    ) -> tuple[str, dict[str, str]] | None:
        """Find the topic of the card that answers with this serial number.

        Topics are often unrelated to the serial (``newtron_B032A0`` for
        SMART16R-1523), so every card is asked at once through the group
        topic, plus each topic the broker watch knows in case a card's group
        topic was changed. The first reply carrying the serial wins.
        """
        wanted = serial.casefold()
        found: asyncio.Future = self.hass.loop.create_future()

        @callback
        def _on_result(msg: mqtt.ReceiveMessage) -> None:
            parts = msg.topic.split("/")
            if len(parts) != 4:
                return
            identity = identity_from_payload(msg.payload, parts[1])
            if (
                identity is not None
                and identity["serial"].casefold() == wanted
                and not found.done()
            ):
                found.set_result((parts[1], identity))

        inventory = self.hass.data.get(DATA_INVENTORY)
        topics = [GROUP_TOPIC, serial, *(inventory.broker if inventory else ())]
        unsub = await mqtt.async_subscribe(self.hass, "d/+/stat/RESULT", _on_result)
        try:
            for topic in dict.fromkeys(topics):
                await mqtt.async_publish(self.hass, f"d/{topic}/cmnd/Discover", "")
            return await asyncio.wait_for(found, DISCOVERY_WINDOW)
        except asyncio.TimeoutError:
            return None
        finally:
            unsub()

    async def _async_create(
        self, topic: str, serial: str, mac: str, scheme: str = DEFAULT_TOPIC_SCHEME
    ) -> ConfigFlowResult:
        await self.async_set_unique_id(serial)
        self._abort_if_unique_id_configured()
        return self.async_create_entry(
            title=serial,
            data={
                CONF_TOPIC: topic,
                CONF_SERIAL: serial,
                CONF_MAC: mac,
                CONF_TOPIC_SCHEME: scheme,
            },
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> NewtronOptionsFlow:
        return NewtronOptionsFlow()


class NewtronOptionsFlow(OptionsFlow):
    """Options: polling interval."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_SCAN_INTERVAL,
                        default=self.config_entry.options.get(
                            CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL
                        ),
                    ): vol.All(vol.Coerce(int), vol.Range(min=5, max=600)),
                }
            ),
        )
