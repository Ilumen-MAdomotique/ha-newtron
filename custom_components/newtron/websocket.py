"""WebSocket API backing the Newtron panel."""
from __future__ import annotations

import voluptuous as vol

from homeassistant.components import websocket_api
from homeassistant.core import HomeAssistant, callback

from .const import DOMAIN, WS_COMMAND, WS_DEVICES


@callback
def async_register_websocket_api(hass: HomeAssistant) -> None:
    websocket_api.async_register_command(hass, ws_devices)
    websocket_api.async_register_command(hass, ws_command)


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
