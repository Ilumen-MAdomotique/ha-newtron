"""Embedded Newtron configuration panel."""
from __future__ import annotations

import logging
from pathlib import Path

from homeassistant.components import frontend, panel_custom
from homeassistant.components.http import StaticPathConfig
from homeassistant.core import HomeAssistant, callback

from .const import DOMAIN, PANEL_ICON, PANEL_STATIC_PATH, PANEL_TITLE, PANEL_URL_PATH

_LOGGER = logging.getLogger(__name__)

_REGISTERED_KEY = f"{DOMAIN}_panel_registered"


async def async_register_panel(hass: HomeAssistant) -> None:
    """Serve the panel JS and add it to the sidebar (idempotent)."""
    if hass.data.get(_REGISTERED_KEY):
        return
    hass.data[_REGISTERED_KEY] = True

    frontend_dir = Path(__file__).parent / "frontend"
    await hass.http.async_register_static_paths(
        [
            StaticPathConfig(
                PANEL_STATIC_PATH, str(frontend_dir), cache_headers=False
            )
        ]
    )
    await panel_custom.async_register_panel(
        hass,
        webcomponent_name="newtron-panel",
        frontend_url_path=PANEL_URL_PATH,
        module_url=f"{PANEL_STATIC_PATH}/newtron-panel.js",
        sidebar_title=PANEL_TITLE,
        sidebar_icon=PANEL_ICON,
        require_admin=True,
        embed_iframe=False,
    )


@callback
def async_unregister_panel(hass: HomeAssistant) -> None:
    if hass.data.pop(_REGISTERED_KEY, None):
        frontend.async_remove_panel(hass, PANEL_URL_PATH)
