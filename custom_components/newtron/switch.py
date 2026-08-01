"""Switch platform for Newtron devices.

- One switch per *free* relay (relays consumed by the roller shutter
  driver are excluded — doc §3 forbids toggling them via Power).
- Global Timers enable (doc §12, two-level enable model).
- DynDNS enable (doc §7).
- DataLogger enable on professional boards (doc §20).
"""
from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, FAMILY_DIMMER, FAMILY_PRO
from .entity import NewtronEntity
from .hub import NewtronHub


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    hub: NewtronHub = hass.data[DOMAIN][entry.entry_id]["hub"]
    entities: list[SwitchEntity] = []

    if hub.family != FAMILY_DIMMER:
        for idx in hub.free_relay_indices():
            entities.append(NewtronRelaySwitch(hub, idx))

    entities.append(NewtronTimersSwitch(hub))
    entities.append(NewtronDynDnsSwitch(hub))
    if hub.family == FAMILY_PRO:
        entities.append(NewtronDataLoggerSwitch(hub))

    async_add_entities(entities)


class NewtronRelaySwitch(NewtronEntity, SwitchEntity):
    """A free relay output controlled via Power<N> (doc §3)."""

    def __init__(self, hub: NewtronHub, idx: int) -> None:
        super().__init__(hub)
        self._idx = idx
        self._attr_unique_id = f"{hub.serial_number}_relay_{idx}"
        self._attr_name = hub.relay_name(idx) or f"Relay {idx}"

    @property
    def is_on(self) -> bool | None:
        return self._hub.relays.get(self._idx)

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._hub.async_command(f"Power{self._idx}", "1")

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._hub.async_command(f"Power{self._idx}", "0")

    async def async_toggle(self, **kwargs: Any) -> None:
        await self._hub.async_command(f"Power{self._idx}", "TOGGLE")


class _ConfigSwitch(NewtronEntity, SwitchEntity):
    """Base for ON/OFF configuration switches read on demand."""

    _attr_entity_category = EntityCategory.CONFIG
    _read_command: str
    _write_command: str
    _response_key: str

    def __init__(self, hub: NewtronHub) -> None:
        super().__init__(hub)
        self._state: bool | None = None

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        await self._async_read()

    async def _async_read(self) -> None:
        result = await self._hub.async_command(self._read_command)
        if result is not None:
            self._apply(result)
            self.async_write_ha_state()

    def _apply(self, result: dict) -> None:
        raise NotImplementedError

    @property
    def is_on(self) -> bool | None:
        return self._state

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._hub.async_command(self._write_command, "1")
        self._state = True
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._hub.async_command(self._write_command, "0")
        self._state = False
        self.async_write_ha_state()


class NewtronTimersSwitch(_ConfigSwitch):
    """Global timers feature enable (doc §12)."""

    _read_command = "Timers"
    _write_command = "Timers"
    _attr_name = "Timers enabled"
    _attr_icon = "mdi:timer-cog-outline"

    def __init__(self, hub: NewtronHub) -> None:
        super().__init__(hub)
        self._attr_unique_id = f"{hub.serial_number}_timers_enable"

    def _apply(self, result: dict) -> None:
        value = result.get("Timers")
        if isinstance(value, str):
            self._state = value.upper() == "ON"


class NewtronDynDnsSwitch(_ConfigSwitch):
    """DynDNS enable (doc §7)."""

    _read_command = "DynDnsStatus"
    _write_command = "DynDns"
    _attr_name = "DynDNS"
    _attr_icon = "mdi:dns-outline"

    def __init__(self, hub: NewtronHub) -> None:
        super().__init__(hub)
        self._attr_unique_id = f"{hub.serial_number}_dyndns"

    def _apply(self, result: dict) -> None:
        status = result.get("DynDnsStatus")
        if isinstance(status, dict):
            self._state = str(status.get("Active", "")).lower() in ("yes", "on", "1", "true")


class NewtronDataLoggerSwitch(_ConfigSwitch):
    """Temperature data logger enable — professional boards only (doc §20)."""

    _read_command = "DataLogger"
    _write_command = "DataLogger"
    _attr_name = "Data logger"
    _attr_icon = "mdi:chart-line"

    def __init__(self, hub: NewtronHub) -> None:
        super().__init__(hub)
        self._attr_unique_id = f"{hub.serial_number}_datalogger"

    def _apply(self, result: dict) -> None:
        value = result.get("DataLogger")
        if isinstance(value, str):
            self._state = value.upper() == "ON"
