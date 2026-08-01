"""Cover platform: roller shutters (relay boards, doc §10).

Control: ShutterOpen<N> / ShutterClose<N> / ShutterStop<N> /
ShutterPosition<N> <pct>. State comes from StatusAll.roller_shutters
(Position 0-100, Direction 1=opening / -1=closing / 0=stopped).
"""
from __future__ import annotations

import asyncio
from typing import Any

from homeassistant.components.cover import (
    ATTR_POSITION,
    CoverDeviceClass,
    CoverEntity,
    CoverEntityFeature,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, FAMILY_RELAY
from .entity import NewtronEntity
from .hub import NewtronHub


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    hub: NewtronHub = hass.data[DOMAIN][entry.entry_id]["hub"]
    if hub.family != FAMILY_RELAY:
        return
    entities = [
        NewtronShutter(hub, idx + 1, cfg)
        for idx, cfg in enumerate(hub.shutter_configs)
    ]
    # Fallback when RelayLayout was unavailable but StatusAll shows shutters
    if not entities:
        entities = [NewtronShutter(hub, idx, {}) for idx in sorted(hub.shutters)]
    async_add_entities(entities)


class NewtronShutter(NewtronEntity, CoverEntity):
    """One configured roller shutter."""

    _attr_device_class = CoverDeviceClass.SHUTTER
    _attr_supported_features = (
        CoverEntityFeature.OPEN
        | CoverEntityFeature.CLOSE
        | CoverEntityFeature.STOP
        | CoverEntityFeature.SET_POSITION
    )

    def __init__(self, hub: NewtronHub, idx: int, config: dict[str, Any]) -> None:
        super().__init__(hub)
        self._idx = idx
        self._attr_unique_id = f"{hub.serial_number}_shutter_{idx}"
        name = (config.get("name") or "").strip()
        self._attr_name = name or f"Shutter {idx}"

    @property
    def _state(self) -> dict[str, Any]:
        return self._hub.shutters.get(self._idx, {})

    @property
    def current_cover_position(self) -> int | None:
        return self._state.get("Position")

    @property
    def is_closed(self) -> bool | None:
        pos = self.current_cover_position
        return None if pos is None else pos == 0

    @property
    def is_opening(self) -> bool:
        return self._state.get("Direction") == 1

    @property
    def is_closing(self) -> bool:
        return self._state.get("Direction") == -1

    async def async_open_cover(self, **kwargs: Any) -> None:
        await self._hub.async_command(f"ShutterOpen{self._idx}", wait=False)
        self._schedule_refresh()

    async def async_close_cover(self, **kwargs: Any) -> None:
        await self._hub.async_command(f"ShutterClose{self._idx}", wait=False)
        self._schedule_refresh()

    async def async_stop_cover(self, **kwargs: Any) -> None:
        await self._hub.async_command(f"ShutterStop{self._idx}", wait=False)
        self._schedule_refresh()

    async def async_set_cover_position(self, **kwargs: Any) -> None:
        await self._hub.async_command(
            f"ShutterPosition{self._idx}", str(kwargs[ATTR_POSITION]), wait=False
        )
        self._schedule_refresh()

    def _schedule_refresh(self) -> None:
        """Movement state only appears in StatusAll; poll shortly after a command."""

        async def _delayed() -> None:
            await asyncio.sleep(1.0)
            await self._hub.async_refresh()

        self.hass.async_create_task(_delayed())
