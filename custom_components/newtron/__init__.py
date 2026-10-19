"""Newtron Smart — Home Assistant integration."""
from __future__ import annotations

import logging
from datetime import timedelta

import voluptuous as vol

from homeassistant.components import mqtt
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse, callback
from homeassistant.exceptions import ConfigEntryNotReady, HomeAssistantError
from homeassistant.helpers import config_validation as cv, device_registry as dr
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.service import async_register_admin_service
from homeassistant.helpers.typing import ConfigType

from .const import (
    CONF_SCAN_INTERVAL,
    CONF_TOPIC,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    PLATFORMS,
)
from .hub import NewtronHub
from .panel import async_register_panel, async_unregister_panel
from .websocket import async_register_websocket_api

_LOGGER = logging.getLogger(__name__)

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

SERVICE_SEND_COMMAND = "send_command"
SERVICE_SEND_COMMAND_SCHEMA = vol.Schema(
    {
        vol.Required("device_id"): cv.string,
        vol.Required("command"): cv.string,
        vol.Optional("payload", default=""): cv.string,
        vol.Optional("wait", default=True): cv.boolean,
    }
)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up shared services and websocket API."""
    hass.data.setdefault(DOMAIN, {})
    async_register_websocket_api(hass)

    async def _handle_send_command(call: ServiceCall) -> dict | None:
        hub = _hub_for_device_id(hass, call.data["device_id"])
        result = await hub.async_command(
            call.data["command"],
            call.data.get("payload", ""),
            wait=call.data.get("wait", True),
        )
        if call.return_response:
            return result or {}
        return None

    async_register_admin_service(
        hass,
        DOMAIN,
        SERVICE_SEND_COMMAND,
        _handle_send_command,
        schema=SERVICE_SEND_COMMAND_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )
    return True


@callback
def _hub_for_device_id(hass: HomeAssistant, device_id: str) -> NewtronHub:
    device_registry = dr.async_get(hass)
    device = device_registry.async_get(device_id)
    if device is None:
        raise HomeAssistantError(f"Unknown device_id: {device_id}")
    for entry_id in device.config_entries:
        if entry_id in hass.data.get(DOMAIN, {}):
            return hass.data[DOMAIN][entry_id]["hub"]
    raise HomeAssistantError("Device is not a Newtron device")


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up one Newtron device from a config entry."""
    if not await mqtt.async_wait_for_mqtt_client(hass):
        raise ConfigEntryNotReady("MQTT integration is not available")

    hub = NewtronHub(hass, entry.entry_id, entry.data[CONF_TOPIC])
    await hub.async_setup()

    if not hub.device_info_raw:
        await hub.async_unload()
        raise ConfigEntryNotReady(
            f"Newtron device on topic '{entry.data[CONF_TOPIC]}' did not answer DeviceStatus"
        )

    scan_interval = entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
    unsub_poll = async_track_time_interval(
        hass, hub.async_refresh, timedelta(seconds=scan_interval)
    )

    hass.data[DOMAIN][entry.entry_id] = {"hub": hub, "unsub_poll": unsub_poll}

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))

    await async_register_panel(hass)
    return True


async def _async_options_updated(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        data = hass.data[DOMAIN].pop(entry.entry_id)
        data["unsub_poll"]()
        await data["hub"].async_unload()
        if not any(
            isinstance(v, dict) and "hub" in v for v in hass.data[DOMAIN].values()
        ):
            async_unregister_panel(hass)
    return unload_ok
