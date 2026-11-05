"""Newtron Smart — Home Assistant integration."""
from __future__ import annotations

import logging
from datetime import timedelta

import voluptuous as vol

from homeassistant.components import mqtt
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse, callback
from homeassistant.exceptions import ConfigEntryNotReady, HomeAssistantError
from homeassistant.helpers import (
    config_validation as cv,
    device_registry as dr,
    entity_registry as er,
)
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.service import async_register_admin_service
from homeassistant.helpers.typing import ConfigType

from .const import (
    CONF_SCAN_INTERVAL,
    CONF_TOPIC,
    DATA_INVENTORY,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    FAMILY_PRO,
    FAMILY_RELAY,
    PLATFORMS,
)
from .hub import NewtronHub
from .inventory import NewtronInventory
from .panel import async_register_panel
from .websocket import async_register_websocket_api
from .wrappers import async_setup_wrappers

_LOGGER = logging.getLogger(__name__)

# A bare "newtron:" line in configuration.yaml loads the integration before
# any card is added, so the panel and its Configuration tab (inventory) are
# there from the start and cards are picked up as they come online.
CONFIG_SCHEMA = vol.Schema(
    {vol.Optional(DOMAIN): vol.Any(None, {})}, extra=vol.ALLOW_EXTRA
)

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
    """Set up shared services, websocket API, panel and the card inventory."""
    hass.data.setdefault(DOMAIN, {})
    inventory = NewtronInventory(hass)
    await inventory.async_load()
    hass.data[DATA_INVENTORY] = inventory
    async_register_websocket_api(hass)
    await async_register_panel(hass)
    hass.async_create_background_task(
        inventory.async_start_watch(), "newtron broker watch"
    )
    async_setup_wrappers(hass)

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

    _async_adopt_serial(hass, entry, hub)
    _async_remove_stale_outputs(hass, entry, hub)

    inventory: NewtronInventory = hass.data[DATA_INVENTORY]
    hub.on_layout_changed = lambda layout: inventory.async_card_changed(
        hub.serial_number, layout
    )

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))

    # Names prepared in the inventory are written to the card, entities go
    # to their areas. Writing to the card can take a few seconds.
    hass.async_create_background_task(
        inventory.async_apply(hub.serial_number, at_setup=True),
        f"newtron apply {hub.serial_number}",
    )
    return True


@callback
def _async_adopt_serial(hass: HomeAssistant, entry: ConfigEntry, hub: NewtronHub) -> None:
    """Key the entry on the card's serial number.

    An entry added while the card did not answer Discover is keyed (and
    titled) on its MQTT topic; fix that once the card has identified itself.
    """
    serial = hub.serial_number
    if not serial or serial == hub.topic or entry.unique_id == serial:
        return
    if any(
        e.unique_id == serial
        for e in hass.config_entries.async_entries(DOMAIN)
        if e.entry_id != entry.entry_id
    ):
        _LOGGER.warning("Card %s is configured twice (topic %s)", serial, hub.topic)
        return
    title = serial if entry.title in (entry.unique_id, hub.topic) else entry.title
    hass.config_entries.async_update_entry(entry, unique_id=serial, title=title)


@callback
def _async_remove_stale_outputs(hass: HomeAssistant, entry: ConfigEntry, hub: NewtronHub) -> None:
    """Drop relays now driving a shutter, and shutters removed from the card.

    Otherwise they stay in Home Assistant as "unavailable" for good.
    """
    if hub.family not in (FAMILY_RELAY, FAMILY_PRO) or not hub.relay_layout:
        return
    free = set(hub.free_relay_indices())
    shutters = len(hub.relay_layout.get("roller_shutters", []))
    ent_reg = er.async_get(hass)
    for entity in er.async_entries_for_config_entry(ent_reg, entry.entry_id):
        kind, _, idx = (entity.unique_id or "").rpartition("_")
        if not idx.isdigit():
            continue
        if (kind.endswith("_relay") and int(idx) not in free) or (
            kind.endswith("_shutter") and int(idx) > shutters
        ):
            ent_reg.async_remove(entity.entity_id)


async def _async_options_updated(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        data = hass.data[DOMAIN].pop(entry.entry_id)
        data["unsub_poll"]()
        await data["hub"].async_unload()
    return unload_ok


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """A removed card that is still online is offered again under Discovered."""
    if inventory := hass.data.get(DATA_INVENTORY):
        inventory.async_rescan(removed_entry_id=entry.entry_id)
