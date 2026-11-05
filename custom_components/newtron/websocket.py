"""WebSocket API backing the Newtron panel."""
from __future__ import annotations

import voluptuous as vol

from homeassistant.components import websocket_api
from homeassistant.components.binary_sensor import BinarySensorDeviceClass
from homeassistant.core import HomeAssistant, callback

from .const import (
    DATA_INVENTORY,
    DOMAIN,
    WS_COMMAND,
    WS_DEVICES,
    WS_INVENTORY,
    WS_INVENTORY_DELETE,
    WS_INVENTORY_LAYOUT,
    WS_INVENTORY_SAVE,
    WS_MODELS,
)
from .models import MODELS


@callback
def async_register_websocket_api(hass: HomeAssistant) -> None:
    websocket_api.async_register_command(hass, ws_devices)
    websocket_api.async_register_command(hass, ws_command)
    websocket_api.async_register_command(hass, ws_models)
    websocket_api.async_register_command(hass, ws_inventory)
    websocket_api.async_register_command(hass, ws_inventory_layout)
    websocket_api.async_register_command(hass, ws_inventory_save)
    websocket_api.async_register_command(hass, ws_inventory_delete)


@websocket_api.require_admin
@websocket_api.websocket_command({vol.Required("type"): WS_INVENTORY})
@callback
def ws_inventory(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict,
) -> None:
    """Inventory cards with their status, and broker cards not listed."""
    connection.send_result(msg["id"], hass.data[DATA_INVENTORY].async_snapshot())


@websocket_api.require_admin
@websocket_api.websocket_command({vol.Required("type"): WS_MODELS})
@callback
def ws_models(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict,
) -> None:
    """Catalogue of Newtron boards (models.py)."""
    connection.send_result(msg["id"], MODELS)


@websocket_api.require_admin
@websocket_api.websocket_command(
    {
        vol.Required("type"): WS_INVENTORY_LAYOUT,
        vol.Required("serial"): str,
        vol.Optional("model_id"): vol.Any(None, vol.In(MODELS)),
    }
)
@websocket_api.async_response
async def ws_inventory_layout(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict,
) -> None:
    """Outputs, inputs, shutters, GPIO probes and variables of a card for the editor."""
    connection.send_result(
        msg["id"],
        await hass.data[DATA_INVENTORY].async_card_layout_live(msg["serial"], msg.get("model_id")),
    )


_SLOT = vol.Schema(
    {
        vol.Optional("used", default=True): bool,
        vol.Optional("name"): vol.Any(None, vol.All(str, vol.Length(max=32))),
        vol.Optional("area_id"): vol.Any(None, str),
        # shutters only: first relay (the next one closes) and travel times
        vol.Optional("relay1"): vol.Any(None, vol.All(int, vol.Range(min=1))),
        vol.Optional("open_duration"): vol.Any(None, vol.All(vol.Coerce(float), vol.Range(min=1))),
        vol.Optional("close_duration"): vol.Any(None, vol.All(vol.Coerce(float), vol.Range(min=1))),
        # inputs only: what is wired on it (binary sensor device class)
        vol.Optional("device_class"): vol.Any(None, vol.In([c.value for c in BinarySensorDeviceClass])),
    }
)


@websocket_api.require_admin
@websocket_api.websocket_command(
    {
        vol.Required("type"): WS_INVENTORY_SAVE,
        vol.Required("serial"): vol.All(str, vol.Strip, vol.Length(min=1)),
        vol.Optional("name"): vol.Any(None, str),
        vol.Optional("ip"): vol.Any(None, str),
        vol.Optional("model"): vol.Any(None, str),
        vol.Optional("model_id"): vol.Any(None, vol.In(MODELS)),
        vol.Optional("area_id"): vol.Any(None, str),
        vol.Optional("notes"): vol.Any(None, str),
        vol.Optional("city"): vol.Any(None, str),
        vol.Optional("country"): vol.Any(None, str),
        vol.Optional("hw_rev"): vol.Any(None, vol.In([1, 2])),
        vol.Optional("outputs", default={}): {str: _SLOT},
        vol.Optional("shutters", default={}): {str: _SLOT},
        vol.Optional("inputs", default={}): {str: _SLOT},
        vol.Optional("variables", default={}): {str: _SLOT},
        vol.Optional("sensors", default={}): {str: _SLOT},
        # GPIO number -> firmware component code of the probe wired on it
        vol.Optional("gpios", default={}): {str: vol.All(int, vol.Range(min=0, max=100000))},
    }
)
@websocket_api.async_response
async def ws_inventory_save(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict,
) -> None:
    """Add or update a card; names go to the card, entities to their areas."""
    card = {k: v for k, v in msg.items() if k not in ("type", "id")}
    saved = await hass.data[DATA_INVENTORY].async_save_card(card)
    connection.send_result(msg["id"], saved)


@websocket_api.require_admin
@websocket_api.websocket_command(
    {vol.Required("type"): WS_INVENTORY_DELETE, vol.Required("serial"): str}
)
@websocket_api.async_response
async def ws_inventory_delete(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict,
) -> None:
    """Remove a card from the inventory (the HA device, if any, stays)."""
    await hass.data[DATA_INVENTORY].async_delete_card(msg["serial"])
    connection.send_result(msg["id"], None)


@websocket_api.require_admin
@websocket_api.websocket_command({vol.Required("type"): WS_DEVICES})
@websocket_api.async_response
async def ws_devices(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict,
) -> None:
    """Return the snapshot of every configured Newtron device."""
    devices = [
        data["hub"].snapshot()
        for data in hass.data.get(DOMAIN, {}).values()
        if isinstance(data, dict) and "hub" in data
    ]
    connection.send_result(msg["id"], {"devices": devices})


@websocket_api.require_admin
@websocket_api.websocket_command(
    {
        vol.Required("type"): WS_COMMAND,
        vol.Required("entry_id"): str,
        vol.Required("command"): str,
        vol.Optional("payload", default=""): str,
        vol.Optional("wait", default=True): bool,
        vol.Optional("timeout", default=8.0): vol.Coerce(float),
    }
)
@websocket_api.async_response
async def ws_command(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict,
) -> None:
    """Send a documented MQTT command to a device and return its response."""
    data = hass.data.get(DOMAIN, {}).get(msg["entry_id"])
    if not data:
        connection.send_error(msg["id"], "not_found", "Unknown Newtron device")
        return
    hub = data["hub"]
    result = await hub.async_command(
        msg["command"],
        msg.get("payload", ""),
        wait=msg.get("wait", True),
        timeout=msg.get("timeout", 8.0),
    )
    connection.send_result(
        msg["id"], {"response": result, "timed_out": result is None and msg.get("wait", True)}
    )
