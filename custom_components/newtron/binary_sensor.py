"""Binary sensor platform: physical inputs and Var/Mem virtual states.

Vars and Mems are read-only here: the API reference documents how to read
them (StatusAll) and configure them (SetVarConfig), but no direct
set-value command — they are driven by device-side logic.
"""
from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, FAMILY_RELAY
from .entity import NewtronEntity
from .hub import NewtronHub


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    hub: NewtronHub = hass.data[DOMAIN][entry.entry_id]["hub"]
    entities: list[BinarySensorEntity] = []

    inputs = sorted(hub.inputs) or list(
        range(1, int(hub.capabilities.get("inputs", 0) or 0) + 1)
    )
    entities.extend(NewtronInput(hub, idx) for idx in inputs)
    entities.extend(NewtronVar(hub, idx, persistent=False) for idx in sorted(hub.vars))
    if hub.family == FAMILY_RELAY:
        entities.extend(
            NewtronVar(hub, idx, persistent=True) for idx in sorted(hub.mems)
        )

    async_add_entities(entities)


class NewtronInput(NewtronEntity, BinarySensorEntity):
    """A physical input / digital button.

    Disabled by default: inputs drive outputs on the board itself and
    rarely need to show in Home Assistant.
    """

    _attr_entity_registry_enabled_default = False

    def __init__(self, hub: NewtronHub, idx: int) -> None:
        super().__init__(hub)
        self._idx = idx
        self._attr_unique_id = f"{hub.serial_number}_input_{idx}"
        self._attr_name = f"Input {idx}"

    @property
    def is_on(self) -> bool | None:
        return self._hub.inputs.get(self._idx)


class NewtronVar(NewtronEntity, BinarySensorEntity):
    """Volatile (Var) or persistent (Mem) variable state.

    Off by default; once ticked in the Configuration tab it goes to its
    area like any other entity (hence no diagnostic category).
    """

    _attr_entity_registry_enabled_default = False

    def __init__(self, hub: NewtronHub, idx: int, persistent: bool) -> None:
        super().__init__(hub)
        self._idx = idx
        self._persistent = persistent
        kind = "mem" if persistent else "var"
        self._attr_unique_id = f"{hub.serial_number}_{kind}_{idx}"
        self._attr_name = f"{'Mem' if persistent else 'Var'} {idx}"
        self._attr_icon = "mdi:variable-box" if persistent else "mdi:variable"

    @property
    def _raw(self) -> str:
        store = self._hub.mems if self._persistent else self._hub.vars
        return store.get(self._idx, "")

    @property
    def available(self) -> bool:
        # An empty Mem ("") is unset (doc §15 example) — show as unavailable
        return super().available and self._raw != ""

    @property
    def is_on(self) -> bool | None:
        raw = self._raw
        return None if raw == "" else raw != "0"
