"""Climate platform: virtual thermostats (SMART 8R / 16R only, doc §28).

Regulation: relay ON when T < setpoint - hyst, OFF when T > setpoint + hyst.
Setpoint is firmware-clamped to 19-27 °C. Writes use the partial-update
semantics of VThermostat<N> {json}: only provided fields are modified.
"""
from __future__ import annotations

import json
from typing import Any

from homeassistant.components.climate import (
    ClimateEntity,
    ClimateEntityFeature,
    HVACAction,
    HVACMode,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_TEMPERATURE, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .entity import NewtronEntity
from .hub import NewtronHub


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    hub: NewtronHub = hass.data[DOMAIN][entry.entry_id]["hub"]
    if not hub.capabilities.get("max_vthermostats"):
        return
    async_add_entities(
        NewtronThermostat(hub, idx) for idx in sorted(hub.vthermostats)
    )


class NewtronThermostat(NewtronEntity, ClimateEntity):
    """One virtual thermostat."""

    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_hvac_modes = [HVACMode.HEAT, HVACMode.OFF]
    _attr_supported_features = (
        ClimateEntityFeature.TARGET_TEMPERATURE
        | ClimateEntityFeature.TURN_ON
        | ClimateEntityFeature.TURN_OFF
    )
    _attr_min_temp = 19.0  # firmware clamp (doc §28)
    _attr_max_temp = 27.0
    _attr_target_temperature_step = 0.5

    def __init__(self, hub: NewtronHub, idx: int) -> None:
        super().__init__(hub)
        self._idx = idx
        self._attr_unique_id = f"{hub.serial_number}_vthermostat_{idx}"
        name = (hub.vthermostats.get(idx, {}).get("name") or "").strip()
        self._attr_name = name or f"Thermostat {idx}"

    @property
    def _data(self) -> dict[str, Any]:
        return self._hub.vthermostats.get(self._idx, {})

    @property
    def current_temperature(self) -> float | None:
        return self._data.get("ambient")

    @property
    def target_temperature(self) -> float | None:
        return self._data.get("set")

    @property
    def hvac_mode(self) -> HVACMode:
        return HVACMode.HEAT if self._data.get("active") else HVACMode.OFF

    @property
    def hvac_action(self) -> HVACAction | None:
        if not self._data.get("active"):
            return HVACAction.OFF
        relay_on = self._data.get("relay_state") == "ON"
        if self._data.get("inverse"):
            relay_on = not relay_on
        return HVACAction.HEATING if relay_on else HVACAction.IDLE

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {
            "hysteresis": self._data.get("hysteresis"),
            "sensor_rom": self._data.get("id_sensor"),
            "relay": self._data.get("relay"),
            "inverse": self._data.get("inverse"),
        }

    async def _write(self, fields: dict[str, Any]) -> None:
        await self._hub.async_command(
            f"VThermostat{self._idx}", json.dumps(fields)
        )

    async def async_set_temperature(self, **kwargs: Any) -> None:
        if (temp := kwargs.get(ATTR_TEMPERATURE)) is not None:
            await self._write({"set": round(float(temp), 1)})

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        await self._write({"active": 1 if hvac_mode == HVACMode.HEAT else 0})

    async def async_turn_on(self) -> None:
        await self._write({"active": 1})

    async def async_turn_off(self) -> None:
        await self._write({"active": 0})
