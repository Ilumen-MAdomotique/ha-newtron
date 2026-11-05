"""Scene platform: the scenes of a SMART DALI bus (doc §21.5).

DaliScene<S> sends DALI GO TO SCENE on the whole bus: each ballast goes to
the level it stores for that scene, lights out of the scene do not move.
"""
from __future__ import annotations

from typing import Any

from homeassistant.components.scene import Scene
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, FAMILY_DALI
from .entity import NewtronBoardNamedEntity
from .hub import NewtronHub


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    hub: NewtronHub = hass.data[DOMAIN][entry.entry_id]["hub"]
    if hub.family != FAMILY_DALI:
        return
    async_add_entities(NewtronDaliScene(hub, idx) for idx in sorted(hub.dali_scenes))


class NewtronDaliScene(NewtronBoardNamedEntity, Scene):
    """One DALI scene, named on the board."""

    def __init__(self, hub: NewtronHub, idx: int) -> None:
        super().__init__(hub)
        self._idx = idx
        self._attr_unique_id = f"{hub.serial_number}_dali_scene_{idx}"
        self._attr_name = self._board_name()

    def _board_name(self) -> str:
        return (self._hub.dali_scenes.get(self._idx, {}).get("name") or "").strip() or f"Scène DALI {self._idx}"

    async def async_activate(self, **kwargs: Any) -> None:
        await self._hub.async_command(f"DaliScene{self._idx}")
        self._hub.async_dali_follow(sorted(self._hub.dali_lights))
