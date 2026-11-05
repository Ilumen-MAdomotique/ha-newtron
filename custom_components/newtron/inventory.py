"""Card inventory and broker watch.

The technician lists the cards of an installation ahead of time: model
(models.py), serial number, and for each output its name and area. The
integration then watches the broker: when a card comes online it is asked
for its serial number (``Discover``). A card from the inventory is added on
its own and receives the prepared names and shutters; any other card is
offered under "Discovered" in Home Assistant.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

from homeassistant.components import mqtt
from homeassistant.config_entries import SOURCE_INTEGRATION_DISCOVERY, ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.storage import Store

from .const import CONF_TOPIC, DOMAIN, FAMILY_DALI, FAMILY_DIMMER
from .hub import NewtronHub
from .models import MODELS, model_from_board
from .wrappers import async_follow_output, async_wrappers_of

_LOGGER = logging.getLogger(__name__)

STORAGE_KEY = f"{DOMAIN}.inventory"
STORAGE_VERSION = 1
PROBE_TIMEOUT = 5.0
# Discover is sent again after each wait without an answer (seconds)
PROBE_RETRY_WAITS = (1.5, 3.5)
# A card that just booted says "Online" before it listens: probe it again
PROBE_AGAIN_AFTER = (10, 20)
CARD_FIELDS = (
    "serial", "name", "ip", "model", "model_id", "area_id", "notes", "city", "country",
)
# SetRollerConfig fields other than the name (relay2 is always relay1 + 1)
_SHUTTER_KEYS = ("relay1", "inverted", "open_duration", "close_duration", "mode")
DEFAULT_SHUTTER_DURATION = 30.0
# (platform, unique_id kind) of the entities behind a card's outputs
_OUTPUT_ENTITY = {"relay": ("switch", "relay"), "dimmer": ("light", "ao")}
# Without a live card, a relay card is listed with this many Var and Mem
DEFAULT_VARIABLES = 8
# The card restarts after a GPIO or revision change; its entities are rebuilt after that
GPIO_RELOAD_DELAY = 30
# AOName: longer names are cut by the dimmer board
AO_NAME_MAX = 24


def _clean_slot(slot: dict[str, Any]) -> dict[str, Any]:
    """One output or shutter of a card: used?, name, area (+ shutter wiring)."""
    clean = {
        "used": bool(slot.get("used", True)),
        "name": (slot.get("name") or "").strip() or None,
        "area_id": slot.get("area_id") or None,
    }
    if slot.get("relay1"):
        clean["relay1"] = int(slot["relay1"])
        clean["open_duration"] = float(slot.get("open_duration") or DEFAULT_SHUTTER_DURATION)
        clean["close_duration"] = float(slot.get("close_duration") or DEFAULT_SHUTTER_DURATION)
    # Inputs: what is wired on it (door, window, motion...); None = plain contact
    if "device_class" in slot:
        clean["device_class"] = slot["device_class"] or None
    return clean


def _extra_unique_id(serial: str, group: str, key: str) -> str:
    """unique_id of an input ("1"), a variable ("var1" / "mem1") or a probe
    measure ("temp_<ROM>" / "hum_<probe>")."""
    if group == "inputs":
        return f"{serial}_input_{key}"
    if group == "variables":
        return f"{serial}_{key[:3]}_{key[3:]}"
    return f"{serial}_{key}"


# Group of the card -> platform of its entities
_EXTRA_PLATFORM = {"inputs": "binary_sensor", "variables": "binary_sensor", "sensors": "sensor"}


def identity_from_payload(payload: Any, fallback_topic: str) -> dict[str, str] | None:
    """Parse a Discover reply.

    Firmware 2.x answers flat ({"serial_number": ..., "board_name": ...});
    older firmware wraps the same fields in a "Discover" object.
    """
    try:
        data = json.loads(payload)
    except (ValueError, TypeError):
        return None
    if not isinstance(data, dict):
        return None
    identity = data.get("Discover")
    if not isinstance(identity, dict):
        if "serial_number" not in data:
            return None
        identity = data
    return {
        "serial": identity.get("serial_number", fallback_topic),
        "mac": identity.get("mac_address", ""),
        "board": identity.get("board_name", ""),
    }


async def async_probe(hass: HomeAssistant, topic: str) -> dict[str, str] | None:
    """Send Discover to <topic> and wait for its identity."""
    fut: asyncio.Future = hass.loop.create_future()

    @callback
    def _on_msg(msg: mqtt.ReceiveMessage) -> None:
        identity = identity_from_payload(msg.payload, topic)
        if identity is not None and not fut.done():
            fut.set_result(identity)

    unsub = await mqtt.async_subscribe(hass, f"d/{topic}/stat/#", _on_msg)
    try:
        # Home Assistant sends the subscription to the broker a moment
        # later: an answer to the first Discover can be lost, so ask again.
        for wait in PROBE_RETRY_WAITS:
            await mqtt.async_publish(hass, f"d/{topic}/cmnd/Discover", "")
            try:
                return await asyncio.wait_for(asyncio.shield(fut), wait)
            except asyncio.TimeoutError:
                continue
        return None
    finally:
        unsub()


class NewtronInventory:
    """Cards planned for this installation, plus what the broker shows."""

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass
        self._store: Store = Store(hass, STORAGE_VERSION, STORAGE_KEY)
        self.cards: dict[str, dict[str, Any]] = {}
        # topic -> {topic, mac, model, ip, online, serial, board}
        self.broker: dict[str, dict[str, Any]] = {}
        self._probing: set[str] = set()
        self._unsubs: list = []

    # -- storage ---------------------------------------------------------
    async def async_load(self) -> None:
        data = await self._store.async_load() or {}
        self.cards = {c["serial"]: c for c in data.get("cards", [])}

    async def _async_store(self) -> None:
        await self._store.async_save({"cards": list(self.cards.values())})

    async def async_save_card(self, card: dict[str, Any]) -> dict[str, Any]:
        """Save a card with its outputs and shutters, then apply it.

        Names are written to the card right away when it is online,
        otherwise as soon as it is added to Home Assistant.
        """
        serial = card["serial"].strip()
        clean = {k: (card.get(k) or "").strip() or None for k in CARD_FIELDS}
        clean["serial"] = serial
        clean["added_at"] = self.cards.get(serial, {}).get("added_at") or time.time()
        clean["outputs"] = {
            str(n): _clean_slot(slot) for n, slot in (card.get("outputs") or {}).items()
        }
        clean["shutters"] = {
            str(n): _clean_slot(slot) for n, slot in (card.get("shutters") or {}).items()
        }
        for group in _EXTRA_PLATFORM:
            clean[group] = {
                str(k): _clean_slot(slot) for k, slot in (card.get(group) or {}).items()
            }
        # GPIO -> what is wired on it (firmware component code, 0 = nothing)
        clean["gpios"] = {str(int(k)): int(v) for k, v in (card.get("gpios") or {}).items()}
        # 8R / 16R hardware revision to declare (SmartHwRev): 1, 2 or None
        clean["hw_rev"] = int(card["hw_rev"]) if card.get("hw_rev") in (1, 2) else None
        clean["pushed"] = False
        self.cards[serial] = clean
        await self._async_store()
        await self.async_apply(serial)
        for info in self.broker.values():
            if info.get("serial") == serial:
                self._async_offer(info)
        self.async_rescan()
        return self.cards[serial]

    @callback
    def async_rescan(self, removed_entry_id: str | None = None) -> None:
        """Ask every online card that is not in Home Assistant to identify itself.

        A card only announces itself when it connects; this catches cards that
        were already online when they were listed or removed from HA.
        ``removed_entry_id`` is still registered while it is being removed.
        """
        configured = {
            e.data.get(CONF_TOPIC)
            for e in self.hass.config_entries.async_entries(DOMAIN)
            if e.entry_id != removed_entry_id
        }
        for topic, info in self.broker.items():
            if info.get("online") and topic not in configured:
                self._async_schedule_probe(topic)

    async def async_delete_card(self, serial: str) -> None:
        self.cards.pop(serial, None)
        await self._async_store()

    # -- applying a card -------------------------------------------------
    @callback
    def _entry_and_hub(self, serial: str) -> tuple[ConfigEntry | None, NewtronHub | None]:
        entry = next(
            (e for e in self.hass.config_entries.async_entries(DOMAIN) if e.unique_id == serial),
            None,
        )
        if entry is None:
            return None, None
        data = self.hass.data.get(DOMAIN, {}).get(entry.entry_id)
        return entry, (data or {}).get("hub")

    @callback
    def _kind(self, card: dict[str, Any], hub: NewtronHub | None) -> str:
        """relay / dimmer / ir / dali — from the live card, else the catalogue."""
        if hub is not None and hub.device_info_raw:
            if hub.family == FAMILY_DIMMER:
                return "dimmer"
            if hub.family == FAMILY_DALI:
                return "dali"
            model = MODELS.get(model_from_board(hub.board_name) or "")
            if model:
                return model["kind"]
            return "ir" if hub.family == "ir" else "relay"
        return MODELS.get(card.get("model_id") or "", {}).get("kind", "relay")

    async def async_apply(self, serial: str, at_setup: bool = False) -> None:
        """Apply the inventory to the card and to Home Assistant.

        - the device gets the card name (at setup, only if not renamed in HA)
          and, for IR / DALI cards, the card's area;
        - shutters and names not yet written to the card are sent to it;
        - each output / shutter goes to its area, unused outputs are hidden.
        """
        card = self.cards.get(serial)
        entry, hub = self._entry_and_hub(serial)
        if card is None or entry is None:
            return
        kind = self._kind(card, hub)

        registry = dr.async_get(self.hass)
        device = next(
            (
                d for d in dr.async_entries_for_config_entry(registry, entry.entry_id)
                if (DOMAIN, serial) in d.identifiers
            ),
            None,
        )
        if device is not None:
            changes: dict[str, Any] = {}
            if card.get("name") and (not at_setup or device.name_by_user is None):
                changes["name_by_user"] = card["name"]
            if card.get("area_id") and (not at_setup or device.area_id is None):
                changes["area_id"] = card["area_id"]
            if changes:
                registry.async_update_device(device.id, **changes)

        if hub is not None and hub.available and not card.get("pushed"):
            if await self._async_push(hub, card, kind):
                card["pushed"] = True
                await self._async_store()

        ent_reg = er.async_get(self.hass)
        targets = [("cover", "shutter", card.get("shutters", {}))]
        if kind in _OUTPUT_ENTITY:
            targets.append((*_OUTPUT_ENTITY[kind], card.get("outputs", {})))
        for platform, uid_kind, slots in targets:
            for idx, slot in slots.items():
                entity_id = ent_reg.async_get_entity_id(platform, DOMAIN, f"{serial}_{uid_kind}_{idx}")
                entity = entity_id and ent_reg.async_get(entity_id)
                if not entity:
                    continue
                # An output turned into a light (or fan...) by "Show as" stays
                # hidden behind it, and its new entity follows its area and name.
                wrappers = async_wrappers_of(self.hass, entity_id)
                updates: dict[str, Any] = {}
                if slot.get("area_id") and slot["area_id"] != entity.area_id:
                    updates["area_id"] = slot["area_id"]
                if not slot.get("used", True) and entity.hidden_by is None:
                    updates["hidden_by"] = er.RegistryEntryHider.INTEGRATION
                elif (
                    slot.get("used", True)
                    and entity.hidden_by == er.RegistryEntryHider.INTEGRATION
                    and not wrappers
                ):
                    updates["hidden_by"] = None
                # Dimmer outputs have no name on the card: name them in HA.
                if kind == "dimmer" and slot.get("name") and slot["name"] != entity.name:
                    updates["name"] = slot["name"]
                if updates:
                    entity = ent_reg.async_update_entity(entity_id, **updates)
                for wrapper in wrappers:
                    async_follow_output(self.hass, wrapper, entity)

        # Inputs (the card's sensors), variables and temperature probes: shown
        # in HA when ticked (inputs and variables are off by default), named,
        # placed in HA, and for inputs shown as door, window, motion...
        for group, platform in _EXTRA_PLATFORM.items():
            for key, slot in card.get(group, {}).items():
                entity_id = ent_reg.async_get_entity_id(
                    platform, DOMAIN, _extra_unique_id(serial, group, key)
                )
                entity = entity_id and ent_reg.async_get(entity_id)
                if not entity:
                    continue
                updates = {}
                if slot.get("area_id") and slot["area_id"] != entity.area_id:
                    updates["area_id"] = slot["area_id"]
                if slot.get("name") and slot["name"] != entity.name:
                    updates["name"] = slot["name"]
                if "device_class" in slot and slot["device_class"] != entity.device_class:
                    updates["device_class"] = slot["device_class"]
                if slot.get("used") and entity.disabled_by == er.RegistryEntryDisabler.INTEGRATION:
                    updates["disabled_by"] = None
                elif not slot.get("used") and entity.disabled_by is None:
                    updates["disabled_by"] = er.RegistryEntryDisabler.INTEGRATION
                if updates:
                    ent_reg.async_update_entity(entity_id, **updates)

    async def _async_push(self, hub: NewtronHub, card: dict[str, Any], kind: str) -> bool:
        """Write the inventory to the card. False: to be sent again later."""
        # The revision first: it restarts the card and, on a 16R, changes
        # which relay is which. The rest is sent once the card is back.
        if not await self._async_push_revision(hub, card):
            return False
        if not await self._async_push_location(hub, card):
            return False
        if kind == "relay":
            return await self._async_push_relay(hub, card)
        if kind == "dimmer":
            return await self._async_push_ao_names(hub, card)
        return True

    @callback
    def _async_reload_later(self, hub: NewtronHub) -> None:
        """Rebuild the card's entities once it is back from a restart."""
        entry_id = hub.entry_id
        async_call_later(
            self.hass, GPIO_RELOAD_DELAY,
            lambda _now: self.hass.config_entries.async_schedule_reload(entry_id),
        )

    async def _async_push_revision(self, hub: NewtronHub, card: dict[str, Any]) -> bool:
        """SmartHwRev 1|2 on an 8R / 16R (Smart16RRev before firmware 2.3.0)."""
        wanted = card.get("hw_rev")
        if not wanted or hub.hardware_revision is None or hub.hardware_revision == wanted:
            return True
        command = "Smart16RRev" if hub.firmware_version.startswith("2.2.") else "SmartHWRev"
        await hub.async_command(f"{command} {wanted}", wait=False)
        self._async_reload_later(hub)
        return False

    async def _async_push_location(self, hub: NewtronHub, card: dict[str, Any]) -> bool:
        """GeoCity / GeoCountry: where the board is, for the sun and prayer timers.

        Best effort: a firmware without these commands must not hold back
        the names and shutters.
        """
        for field, command in (("city", "GeoCity"), ("country", "GeoCountry")):
            wanted = card.get(field)
            if not wanted:
                continue
            current = await hub.async_command(command, timeout=4)
            if current is None:
                _LOGGER.warning("%s does not answer %s, location not written", hub.topic, command)
                continue
            if (current.get(command) or "") != wanted:
                await hub.async_command(f"{command} {wanted}")
        return True

    async def _async_push_ao_names(self, hub: NewtronHub, card: dict[str, Any]) -> bool:
        """AOName<n>: the names of a dimmer's outputs, written to the board.

        Firmware without AOName (1.2.0 answers "Unknown"): the names stay in
        Home Assistant only.
        """
        ent_reg = er.async_get(self.hass)
        for n, slot in sorted(card.get("outputs", {}).items(), key=lambda kv: int(kv[0])):
            if slot.get("name"):
                res = await hub.async_command(f"AOName{n}", slot["name"][:AO_NAME_MAX])
            else:
                # Name emptied in the inventory: empty it on the card too
                # ("AOName<n> 0" goes back to the default label), and in Home
                # Assistant when it still shows the card's old name.
                res = await hub.async_command(f"AOName{n}")
                old = (res or {}).get(f"AOName{n}")
                if old:
                    res = await hub.async_command(f"AOName{n}", "0")
                    entity_id = ent_reg.async_get_entity_id("light", DOMAIN, f"{card['serial']}_ao_{n}")
                    if entity_id and (entry := ent_reg.async_get(entity_id)) and entry.name == old:
                        ent_reg.async_update_entity(entity_id, name=None)
            if res is None:
                return False
            if res.get("Command") == "Unknown":
                _LOGGER.warning(
                    "%s (firmware %s) has no AOName: output names stay in Home Assistant",
                    hub.topic, hub.firmware_version,
                )
                return True
        return True

    async def _async_push_relay(self, hub: NewtronHub, card: dict[str, Any]) -> bool:
        """Write the prepared shutters and output names to a relay card."""
        current = hub.shutter_configs
        by_relay = {s.get("relay1"): s for s in current}
        slots = [card["shutters"][n] for n in sorted(card.get("shutters", {}), key=int)]
        if any(s.get("relay1") for s in slots):
            # Shutters defined in the inventory: that list is the reference.
            # Direction and wall-switch mode are kept from the card when the
            # same relays already drive a shutter.
            wanted = [
                {
                    "name": s.get("name") or f"Volet {i}",
                    "relay1": s["relay1"],
                    "inverted": by_relay.get(s["relay1"], {}).get("inverted", False),
                    "open_duration": s["open_duration"],
                    "close_duration": s["close_duration"],
                    "mode": by_relay.get(s["relay1"], {}).get("mode", "0"),
                }
                for i, s in enumerate((s for s in slots if s.get("relay1")), 1)
            ]
        else:
            wanted = [
                {
                    **{k: s[k] for k in _SHUTTER_KEYS if k in s},
                    "name": (slots[i - 1].get("name") if i <= len(slots) else None)
                    or s.get("name") or f"Volet {i}",
                }
                for i, s in enumerate(current, 1)
            ]
        have = [{**{k: s.get(k) for k in _SHUTTER_KEYS}, "name": s.get("name")} for s in current]
        want = [{**{k: s.get(k) for k in _SHUTTER_KEYS}, "name": s.get("name")} for s in wanted]
        if want != have:
            # SetRollerConfig replaces the whole list (no partial write).
            if await hub.async_command(
                "SetRollerConfig", json.dumps({"roller_shutters": wanted})
            ) is None:
                return False
            await hub.async_reload_layout()

        # Output names, on the outputs left free by the shutters.
        outputs = card.get("outputs", {})
        changed = [
            {
                "output": out["output"],
                "name": outputs[str(out["output"])]["name"][:32],
                "mode": out.get("mode", 0),
                "delay": out.get("delay", 0),
            }
            for out in hub.relay_layout.get("free_outputs", [])
            if (outputs.get(str(out["output"])) or {}).get("name")
            and outputs[str(out["output"])]["name"] != (out.get("name") or "")
        ]
        if changed:
            if await hub.async_command("SetOutputConfig", json.dumps(changed)) is None:
                return False
            await hub.async_reload_layout()
        return await self._async_push_gpios(hub, card)

    async def _async_push_gpios(self, hub: NewtronHub, card: dict[str, Any]) -> bool:
        """Write the probes chosen for each GPIO; the card restarts to apply them."""
        wanted = {int(k): v for k, v in card.get("gpios", {}).items()}
        if not wanted:
            return True
        current = await hub.async_read_gpios()
        if current is None:
            return False
        changed = {n: code for n, code in wanted.items() if n in current and current[n] != code}
        if not changed:
            return True
        await hub.async_command(
            "Backlog", "; ".join(f"Gpio{n} {code}" for n, code in sorted(changed.items())), wait=False
        )
        # New probes become entities once the card is back.
        self._async_reload_later(hub)
        return True

    @callback
    def async_card_changed(self, serial: str, layout: dict[str, Any]) -> None:
        """Names edited on the card itself replace those of the inventory."""
        card = self.cards.get(serial)
        if card is None or not card.get("pushed"):
            return  # names still waiting to be written to the card win
        for out in layout.get("free_outputs", []):
            slot = card.setdefault("outputs", {}).setdefault(str(out["output"]), _clean_slot({}))
            slot["name"] = (out.get("name") or "").strip() or None
        for i, sh in enumerate(layout.get("roller_shutters", []), 1):
            slot = card.setdefault("shutters", {}).setdefault(str(i), _clean_slot({}))
            slot["name"] = (sh.get("name") or "").strip() or None
        self.hass.async_create_task(self._async_store())

    @callback
    def async_card_layout(self, serial: str, model_id: str | None = None) -> dict[str, Any]:
        """Rows for the card editor: what the card has, merged with the inventory.

        Online card: its outputs and shutters, names read from the card.
        Card not there yet: the outputs of its catalogue model (16R -> 16).
        Every relay is listed; those driving a shutter carry its number.
        """
        card = self.cards.get(serial, {})
        entry, hub = self._entry_and_hub(serial)
        live = hub is not None and bool(hub.device_info_raw)
        model_id = (model_from_board(hub.board_name) if live else None) or model_id or card.get("model_id")
        model = MODELS.get(model_id or "", {})
        kind = self._kind({**card, "model_id": model_id}, hub)
        ent_reg = er.async_get(self.hass)

        def _entity(platform: str, uid_kind: str, idx: int) -> er.RegistryEntry | None:
            entity_id = ent_reg.async_get_entity_id(platform, DOMAIN, f"{serial}_{uid_kind}_{idx}")
            return ent_reg.async_get(entity_id) if entity_id else None

        outputs, shutters = card.get("outputs", {}), card.get("shutters", {})

        # Shutters: from the card when online, else from the inventory.
        shutter_rows: list[dict[str, Any]] = []
        sources = (
            list(enumerate(hub.shutter_configs, 1)) if live
            else [(int(n), s) for n, s in sorted(shutters.items(), key=lambda kv: int(kv[0]))]
        )
        for i, cfg in sources:
            slot = shutters.get(str(i), {})
            entity = _entity("cover", "shutter", i)
            shutter_rows.append({
                "index": i,
                "card_name": (cfg.get("name") or "").strip() or None if live else None,
                "name": slot.get("name") or ((cfg.get("name") or "").strip() or None),
                "area_id": slot.get("area_id") or (entity.area_id if entity else None),
                "used": slot.get("used", True),
                "relay1": cfg.get("relay1"),
                "open_duration": cfg.get("open_duration") or DEFAULT_SHUTTER_DURATION,
                "close_duration": cfg.get("close_duration") or DEFAULT_SHUTTER_DURATION,
            })
        in_shutter = {
            r: s["index"] for s in shutter_rows if s["relay1"] for r in (s["relay1"], s["relay1"] + 1)
        }

        # Outputs: every relay (or AO) of the card.
        card_names: dict[int, str | None] = {}
        if live and kind == "relay":
            card_names = {
                o["output"]: (o.get("name") or "").strip() or None
                for o in hub.relay_layout.get("free_outputs", [])
            }
            total = int(hub.capabilities.get("outputs") or 0) or max(
                [*card_names, *in_shutter, 0]
            )
        elif live and kind == "dimmer":
            total = len(hub.ao) or model.get("outputs", 0)
        else:
            total = model.get("outputs", 0) if model else 0
        platform, uid_kind = _OUTPUT_ENTITY.get(kind, ("switch", "relay"))
        output_rows = []
        for idx in range(1, total + 1):
            slot = outputs.get(str(idx), {})
            entity = _entity(platform, uid_kind, idx)
            card_name = card_names.get(idx)
            if kind == "dimmer" and entity is not None:
                card_name = entity.name or entity.original_name
            output_rows.append({
                "index": idx,
                "card_name": card_name,
                "name": slot.get("name") or card_name,
                "area_id": slot.get("area_id") or (entity.area_id if entity else None),
                "used": slot.get("used", True),
                "shutter": in_shutter.get(idx),
            })
        def _extra_row(group: str, key: str, entity: er.RegistryEntry | None, **row: Any) -> dict[str, Any]:
            slot = card.get(group, {}).get(key, {})
            return {
                **row,
                "key": key,
                "card_name": entity.original_name if entity else None,
                "name": slot.get("name") or (entity.name if entity else None),
                "area_id": slot.get("area_id") or (entity.area_id if entity else None),
                # Not in the inventory yet: as Home Assistant shows it today
                "used": slot["used"] if "used" in slot
                else bool(entity and entity.disabled_by is None),
            }

        # Inputs (relay and dimmer cards): the card's own count when online,
        # else the catalogue.
        n_inputs = 0
        if kind in ("relay", "dimmer"):
            n_inputs = (
                int(hub.capabilities.get("inputs") or 0) or len(hub.inputs) if live
                else model.get("inputs", 0)
            )
        input_rows = []
        for i in range(1, n_inputs + 1):
            entity = _entity("binary_sensor", "input", i)
            slot = card.get("inputs", {}).get(str(i), {})
            input_rows.append(_extra_row(
                "inputs", str(i), entity, index=i,
                device_class=slot["device_class"] if "device_class" in slot
                else (entity.device_class if entity else None),
            ))
        # Variables: volatile (Var) then persistent (Mem, relay cards only).
        variable_rows = []
        if kind in ("relay", "dimmer"):
            for prefix, persistent in (("var", False), ("mem", True))[: 2 if kind == "relay" else 1]:
                numbers = (
                    sorted(hub.mems if persistent else hub.vars) if live
                    else range(1, DEFAULT_VARIABLES + 1)
                )
                variable_rows.extend(
                    _extra_row(
                        "variables", f"{prefix}{n}", _entity("binary_sensor", prefix, n),
                        index=n, persistent=persistent,
                    )
                    for n in numbers
                )
        # Measures of the GPIO probes: only known once the card has reported them.
        sensor_rows = []
        if live:
            for label in sorted(hub.sensors):
                reading = hub.sensors[label] or {}
                measures = []
                if reading.get("temperature") is not None:
                    measures.append((f"temp_{reading.get('id') or label}", "température"))
                if reading.get("humidity") is not None:
                    measures.append((f"hum_{label}", "humidité"))
                for key, measure in measures:
                    entity_id = ent_reg.async_get_entity_id("sensor", DOMAIN, f"{serial}_{key}")
                    sensor_rows.append(_extra_row(
                        "sensors", key, ent_reg.async_get(entity_id) if entity_id else None,
                        label=label, measure=measure,
                    ))
        return {
            "source": "card" if live else "inventory",
            "online": bool(live and hub.available),
            "kind": kind,
            "model_id": model_id,
            "max_shutters": model.get("max_shutters", total // 2),
            "outputs": output_rows,
            "shutters": shutter_rows,
            "inputs": input_rows,
            "variables": variable_rows,
            "sensors": sensor_rows,
            # 8R / 16R: revision declared on the card (0 = not declared)
            "hardware_revision": hub.hardware_revision if live else None,
            "hw_rev": card.get("hw_rev"),
            # GPIO: what the inventory plans; the live card's own setting is
            # added by async_card_layout_live
            "gpios": [
                {"pin": int(n), "code": code, "card_code": None}
                for n, code in sorted(card.get("gpios", {}).items(), key=lambda kv: int(kv[0]))
            ],
        }

    async def async_card_layout_live(self, serial: str, model_id: str | None = None) -> dict[str, Any]:
        """async_card_layout, plus the GPIO list read from an online relay card."""
        layout = self.async_card_layout(serial, model_id)
        _, hub = self._entry_and_hub(serial)
        if hub is None or not layout["online"]:
            return layout
        # Location set on the card, shown when the inventory has none
        for field, command in (("card_city", "GeoCity"), ("card_country", "GeoCountry")):
            res = await hub.async_command(command, timeout=3)
            layout[field] = (res or {}).get(command) or None
        if layout["kind"] != "relay":
            return layout
        current = await hub.async_read_gpios()
        if current is not None:
            planned = {g["pin"]: g["code"] for g in layout["gpios"]}
            layout["gpios"] = [
                {"pin": n, "code": planned.get(n), "card_code": code}
                for n, code in sorted(current.items())
            ]
        return layout

    # -- broker watch ----------------------------------------------------
    async def async_start_watch(self) -> None:
        if not await mqtt.async_wait_for_mqtt_client(self.hass):
            _LOGGER.warning("MQTT not available, Newtron broker watch disabled")
            return
        self._unsubs = [
            await mqtt.async_subscribe(self.hass, "d/+/tele/LWT", self._on_lwt),
            await mqtt.async_subscribe(
                self.hass, "tasmota/discovery/+/config", self._on_announce
            ),
        ]

    @callback
    def async_stop_watch(self) -> None:
        for unsub in self._unsubs:
            unsub()
        self._unsubs = []

    @callback
    def _on_lwt(self, msg: mqtt.ReceiveMessage) -> None:
        parts = msg.topic.split("/")
        if len(parts) != 4 or not parts[1]:
            return
        info = self.broker.setdefault(parts[1], {"topic": parts[1]})
        info["online"] = msg.payload == "Online"
        configured = {
            e.data.get(CONF_TOPIC) for e in self.hass.config_entries.async_entries(DOMAIN)
        }
        if info["online"] and parts[1] not in configured:
            self._async_schedule_probe(parts[1])

    @callback
    def _on_announce(self, msg: mqtt.ReceiveMessage) -> None:
        try:
            data = json.loads(msg.payload)
        except (ValueError, TypeError):
            return
        if not isinstance(data, dict) or not data.get("t"):
            return
        info = self.broker.setdefault(data["t"], {"topic": data["t"]})
        info.update(
            announced=True, mac=data.get("mac", ""), model=data.get("md", ""),
            ip=data.get("ip", ""),
        )

    @callback
    def _async_schedule_probe(self, topic: str) -> None:
        if topic in self._probing:
            return
        self._probing.add(topic)
        self.hass.async_create_background_task(
            self._async_probe_and_offer(topic), f"newtron probe {topic}"
        )

    async def _async_probe_and_offer(self, topic: str) -> None:
        try:
            identity = await async_probe(self.hass, topic)
            for delay in PROBE_AGAIN_AFTER:
                if identity is not None or not self.broker.get(topic, {}).get("online"):
                    break
                await asyncio.sleep(delay)
                identity = await async_probe(self.hass, topic)
        finally:
            self._probing.discard(topic)
        if identity is None:
            return
        info = self.broker.setdefault(topic, {"topic": topic})
        info.update(serial=identity["serial"], board=identity["board"])
        if identity["mac"]:
            info["mac"] = identity["mac"]
        self._async_offer(info)

    @callback
    def _async_offer(self, info: dict[str, Any]) -> None:
        """Start a discovery flow; the flow adds inventory cards on its own."""
        serial = info.get("serial")
        if not serial or serial == info["topic"]:
            return
        card = self.cards.get(serial)
        self.hass.async_create_task(
            self.hass.config_entries.flow.async_init(
                DOMAIN,
                context={"source": SOURCE_INTEGRATION_DISCOVERY},
                data={
                    CONF_TOPIC: info["topic"],
                    "serial": serial,
                    "mac": info.get("mac", ""),
                    "board": info.get("board") or info.get("model", ""),
                    "inventory_name": (card.get("name") or serial) if card else None,
                },
            )
        )

    # -- panel -----------------------------------------------------------
    @callback
    def async_snapshot(self) -> dict[str, Any]:
        """Inventory cards with their live status, and unlisted broker cards."""
        entries = {
            e.unique_id: e for e in self.hass.config_entries.async_entries(DOMAIN)
        }
        by_serial = {i["serial"]: i for i in self.broker.values() if i.get("serial")}

        def _status(serial: str) -> str:
            if serial in entries:
                return "configured"
            info = by_serial.get(serial)
            return "online" if info and info.get("online") else "waiting"

        def _live(serial: str) -> dict[str, Any]:
            entry = entries.get(serial)
            info = by_serial.get(serial) or (
                self.broker.get(entry.data.get(CONF_TOPIC), {}) if entry else {}
            )
            board = info.get("board") or info.get("model")
            return {
                "topic": info.get("topic") or (entry.data.get(CONF_TOPIC) if entry else None),
                "ip": info.get("ip"),
                "model": board,
                "model_id": model_from_board(board),
                "online": info.get("online"),
                "entry_id": entry.entry_id if entry else None,
            }

        cards = [
            {**card, "status": _status(serial), "live": _live(serial)}
            for serial, card in sorted(self.cards.items())
        ]
        unlisted = [
            {
                "serial": serial,
                "status": _status(serial),
                "live": _live(serial),
            }
            for serial in sorted(set(by_serial) | {s for s in entries if s})
            if serial not in self.cards
        ]
        return {"cards": cards, "unlisted": unlisted}
