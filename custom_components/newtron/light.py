"""Light platform: dimmer board analog outputs (AO1-AO8, 0-10 V).

Semantics follow doc §23-§26:
- turn_on without brightness  -> AOOn<n> (restores memorised level)
- turn_on with brightness     -> AO<n> <pct>
- turn_off                    -> AOOff<n> (memorises current level)
A channel in Bistable switch mode (AOMode=1) snaps values at the 50 %
threshold firmware-side; the response value is authoritative.
"""
from __future__ import annotations

from typing import Any

from homeassistant.components.light import ATTR_BRIGHTNESS, ColorMode, LightEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, FAMILY_DIMMER
from .entity import NewtronEntity
from .hub import NewtronHub


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    hub: NewtronHub = hass.data[DOMAIN][entry.entry_id]["hub"]
    if hub.family != FAMILY_DIMMER:
        return
    channels = sorted(hub.ao) or list(
        range(1, int(hub.capabilities.get("outputs", 8) or 8) + 1)
    )
    async_add_entities(NewtronAOLight(hub, idx) for idx in channels)


class NewtronAOLight(NewtronEntity, LightEntity):
    """One 0-10 V analog output exposed as a dimmable light."""

    _attr_color_mode = ColorMode.BRIGHTNESS
    _attr_supported_color_modes = {ColorMode.BRIGHTNESS}

    def __init__(self, hub: NewtronHub, idx: int) -> None:
        super().__init__(hub)
        self._idx = idx
        self._attr_unique_id = f"{hub.serial_number}_ao_{idx}"
        self._attr_name = f"Output AO{idx}"

    @property
    def _pct(self) -> int | None:
        return self._hub.ao.get(self._idx)

    @property
    def is_on(self) -> bool | None:
        pct = self._pct
        return None if pct is None else pct > 0

    @property
    def brightness(self) -> int | None:
        pct = self._pct
        return None if pct is None else round(pct * 255 / 100)

    async def async_turn_on(self, **kwargs: Any) -> None:
        if ATTR_BRIGHTNESS in kwargs:
            pct = max(1, round(kwargs[ATTR_BRIGHTNESS] * 100 / 255))
            await self._hub.async_command(f"AO{self._idx}", str(pct))
        else:
            await self._hub.async_command(f"AOOn{self._idx}")

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._hub.async_command(f"AOOff{self._idx}")
