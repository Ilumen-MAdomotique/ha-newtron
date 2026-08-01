"""Base entity for Newtron devices."""
from __future__ import annotations

from homeassistant.core import callback
from homeassistant.helpers.device_registry import DeviceInfo, format_mac
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import Entity

from .const import DOMAIN, MANUFACTURER, SIGNAL_UPDATE
from .hub import NewtronHub


class NewtronEntity(Entity):
    """Common behaviour: device info, availability, push updates."""

    _attr_should_poll = False
    _attr_has_entity_name = True

    def __init__(self, hub: NewtronHub) -> None:
        self._hub = hub
        info = hub.device_info_raw.get("device_info", {}) or {}
        connections = set()
        if info.get("mac_address"):
            connections.add(("mac", format_mac(info["mac_address"])))
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, hub.serial_number)},
            connections=connections,
            manufacturer=MANUFACTURER,
            model=hub.board_name or None,
            name=hub.serial_number,
            sw_version=hub.firmware_version or None,
            configuration_url=f"http://{hub.ip_address}" if hub.ip_address else None,
        )

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                SIGNAL_UPDATE.format(self._hub.entry_id),
                self._handle_update,
            )
        )

    @callback
    def _handle_update(self) -> None:
        self.async_write_ha_state()

    @property
    def available(self) -> bool:
        return self._hub.available
