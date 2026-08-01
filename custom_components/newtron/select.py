"""Select platform: AO input acquisition mode (dimmer boards, doc §27)."""
from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, FAMILY_DIMMER
from .entity import NewtronEntity
from .hub import NewtronHub

MODE_PUSH = "Push button"
MODE_BISTABLE = "Bistable switch"
_MODE_TO_VALUE = {MODE_PUSH: 0, MODE_BISTABLE: 1}
_VALUE_TO_MODE = {0: MODE_PUSH, 1: MODE_BISTABLE}


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    hub: NewtronHub = hass.data[DOMAIN][entry.entry_id]["hub"]
    if hub.family != FAMILY_DIMMER:
        return
    channels = sorted(hub.ao_modes) or sorted(hub.ao) or list(range(1, 9))
    async_add_entities(NewtronAOModeSelect(hub, idx) for idx in channels)


class NewtronAOModeSelect(NewtronEntity, SelectEntity):
    """Acquisition mode for the DI controlling one AO."""

    _attr_entity_category = EntityCategory.CONFIG
    _attr_options = [MODE_PUSH, MODE_BISTABLE]
    _attr_icon = "mdi:gesture-tap-button"

    def __init__(self, hub: NewtronHub, idx: int) -> None:
        super().__init__(hub)
        self._idx = idx
        self._attr_unique_id = f"{hub.serial_number}_ao_mode_{idx}"
        self._attr_name = f"AO{idx} input mode"

    @property
    def current_option(self) -> str | None:
        mode = self._hub.ao_modes.get(self._idx)
        return _VALUE_TO_MODE.get(mode) if mode is not None else None

    async def async_select_option(self, option: str) -> None:
        result = await self._hub.async_command(
            f"AOMode{self._idx}", str(_MODE_TO_VALUE[option])
        )
        if result is not None:
            # Push button -> Bistable transition snaps the AO value
            # firmware-side; refresh to reflect it.
            await self._hub.async_refresh()
