"""Button platform: identify blink and dimmer reset."""
from __future__ import annotations

from homeassistant.components.button import ButtonDeviceClass, ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, FAMILY_DIMMER
from .entity import NewtronEntity
from .hub import NewtronHub


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    hub: NewtronHub = hass.data[DOMAIN][entry.entry_id]["hub"]
    entities: list[ButtonEntity] = [NewtronIdentifyButton(hub)]
    if hub.family == FAMILY_DIMMER:
        entities.append(NewtronAOResetButton(hub))
    async_add_entities(entities)


class NewtronIdentifyButton(NewtronEntity, ButtonEntity):
    """VisualDiscover: blinks relay 1 (or AO1 on dimmer) four times (doc §16)."""

    _attr_device_class = ButtonDeviceClass.IDENTIFY
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_name = "Identify"

    def __init__(self, hub: NewtronHub) -> None:
        super().__init__(hub)
        self._attr_unique_id = f"{hub.serial_number}_identify"

    async def async_press(self) -> None:
        await self._hub.async_command("VisualDiscover", wait=False)


class NewtronAOResetButton(NewtronEntity, ButtonEntity):
    """AOReset: all outputs to 0 % and memorised levels cleared (doc §24)."""

    _attr_entity_category = EntityCategory.CONFIG
    _attr_name = "Reset all outputs"
    _attr_icon = "mdi:restore-alert"

    def __init__(self, hub: NewtronHub) -> None:
        super().__init__(hub)
        self._attr_unique_id = f"{hub.serial_number}_ao_reset"

    async def async_press(self) -> None:
        await self._hub.async_command("AOReset")
