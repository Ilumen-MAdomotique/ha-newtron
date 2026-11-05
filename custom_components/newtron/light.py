"""Light platform: dimmer board analog outputs (AO1-AO8, 0-10 V) and the
lights and groups of a SMART DALI bus.

Semantics follow doc §23-§26:
- turn_on without brightness  -> AOOn<n> (restores memorised level)
- turn_on with brightness     -> AO<n> <pct>
- turn_off                    -> AOOff<n> (memorises current level)
A channel in Bistable switch mode (AOMode=1) snaps values at the 50 %
threshold firmware-side; the response value is authoritative.

DALI (doc §21): DaliDimmer<N> <0..100> / DaliPower<N> 0|1, N = light 1-64 or
100 + group 1-16. The reply is only the frame sent, so the real level is read
back with DaliState<N> (light) or comes with the next StatusAll (group).
"""
from __future__ import annotations

from typing import Any

from homeassistant.components.light import ATTR_BRIGHTNESS, ColorMode, LightEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, FAMILY_DALI, FAMILY_DIMMER
from .entity import NewtronBoardNamedEntity, NewtronEntity
from .hub import NewtronHub

# DaliDimmer / DaliPower target of group G
DALI_GROUP_OFFSET = 100
# Light states where the ballast does not answer on the bus
DALI_UNREACHABLE = ("Missing", "NoReply", "Unknown")


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    hub: NewtronHub = hass.data[DOMAIN][entry.entry_id]["hub"]
    if hub.family == FAMILY_DALI:
        async_add_entities(
            [NewtronDaliLight(hub, idx) for idx in sorted(hub.dali_lights)]
            + [NewtronDaliGroup(hub, idx) for idx in sorted(hub.dali_groups)]
        )
        return
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


def _pct_from_brightness(brightness: int) -> int:
    return max(1, round(brightness * 100 / 255))


class NewtronDaliLight(NewtronBoardNamedEntity, LightEntity):
    """One DALI light (control gear), named on the board."""

    _attr_color_mode = ColorMode.BRIGHTNESS
    _attr_supported_color_modes = {ColorMode.BRIGHTNESS}

    def __init__(self, hub: NewtronHub, idx: int) -> None:
        super().__init__(hub)
        self._idx = idx
        self._attr_unique_id = f"{hub.serial_number}_dali_light_{idx}"
        self._attr_name = self._board_name()

    def _board_name(self) -> str:
        return (self._hub.dali_lights.get(self._idx, {}).get("name") or "").strip() or f"DALI {self._idx}"

    @property
    def _light(self) -> dict[str, Any]:
        return self._hub.dali_lights.get(self._idx, {})

    @property
    def available(self) -> bool:
        return super().available and self._light.get("state") not in DALI_UNREACHABLE

    @property
    def is_on(self) -> bool | None:
        level = self._light.get("level")
        return None if level is None else level > 0

    @property
    def brightness(self) -> int | None:
        level = self._light.get("level")
        return None if level is None else round(level * 255 / 100)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {k: self._light[k] for k in ("state", "fault") if k in self._light}

    async def _async_set(self, command: str, value: int, level: int | None) -> None:
        await self._hub.async_command(f"{command}{self._idx}", str(value), wait=False)
        self._hub.async_dali_follow([self._idx], level)

    async def async_turn_on(self, **kwargs: Any) -> None:
        if ATTR_BRIGHTNESS in kwargs:
            pct = _pct_from_brightness(kwargs[ATTR_BRIGHTNESS])
            await self._async_set("DaliDimmer", pct, pct)
        else:
            await self._async_set("DaliPower", 1, None)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._async_set("DaliPower", 0, 0)


class NewtronDaliGroup(NewtronBoardNamedEntity, LightEntity):
    """One DALI group: on while a member is on, at the level of its brightest member."""

    _attr_color_mode = ColorMode.BRIGHTNESS
    _attr_supported_color_modes = {ColorMode.BRIGHTNESS}
    _attr_icon = "mdi:lightbulb-group"

    def __init__(self, hub: NewtronHub, idx: int) -> None:
        super().__init__(hub)
        self._idx = idx
        self._attr_unique_id = f"{hub.serial_number}_dali_group_{idx}"
        self._attr_name = self._board_name()

    def _board_name(self) -> str:
        return (self._hub.dali_groups.get(self._idx, {}).get("name") or "").strip() or f"Groupe DALI {self._idx}"

    @property
    def _members(self) -> list[int]:
        return list(self._hub.dali_groups.get(self._idx, {}).get("members") or [])

    @property
    def _levels(self) -> list[int]:
        return [
            level for n in self._members
            if (level := self._hub.dali_lights.get(n, {}).get("level")) is not None
        ]

    @property
    def is_on(self) -> bool | None:
        levels = self._levels
        return None if not levels else max(levels) > 0

    @property
    def brightness(self) -> int | None:
        levels = self._levels
        return None if not levels else round(max(levels) * 255 / 100)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {"lights": self._members}

    async def _async_set(self, command: str, value: int, level: int | None) -> None:
        await self._hub.async_command(
            f"{command}{DALI_GROUP_OFFSET + self._idx}", str(value), wait=False
        )
        self._hub.async_dali_follow(self._members, level)

    async def async_turn_on(self, **kwargs: Any) -> None:
        if ATTR_BRIGHTNESS in kwargs:
            pct = _pct_from_brightness(kwargs[ATTR_BRIGHTNESS])
            await self._async_set("DaliDimmer", pct, pct)
        else:
            await self._async_set("DaliPower", 1, None)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._async_set("DaliPower", 0, 0)
