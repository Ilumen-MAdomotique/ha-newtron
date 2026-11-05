"""Sensor platform: GPIO probes (temperature, humidity) and device diagnostics."""
from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE, EntityCategory, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .entity import NewtronEntity
from .hub import NewtronHub


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    hub: NewtronHub = hass.data[DOMAIN][entry.entry_id]["hub"]
    entities: list[SensorEntity] = [
        NewtronTemperature(hub, key) for key in sorted(hub.sensors)
        if hub.sensors[key].get("temperature") is not None
    ]
    entities.extend(
        NewtronHumidity(hub, key) for key in sorted(hub.sensors)
        if hub.sensors[key].get("humidity") is not None
    )
    entities.extend(
        [
            NewtronDiagnostic(hub, "ip", "IP address", "mdi:ip-network-outline"),
            NewtronDiagnostic(hub, "connection", "Connection", "mdi:lan-connect"),
            NewtronDiagnostic(hub, "uptime", "Uptime", "mdi:timer-outline"),
            NewtronDiagnostic(hub, "free_ram_kb", "Free RAM", "mdi:memory"),
        ]
    )
    async_add_entities(entities)


class NewtronTemperature(NewtronEntity, SensorEntity):
    """One DS18x20 probe (key like 'DS18B20-1')."""

    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, hub: NewtronHub, key: str) -> None:
        super().__init__(hub)
        self._key = key
        rom = (hub.sensors.get(key) or {}).get("id") or key
        self._attr_unique_id = f"{hub.serial_number}_temp_{rom}"
        self._attr_name = f"Temperature {key}"

    @property
    def native_unit_of_measurement(self) -> str:
        return (
            UnitOfTemperature.FAHRENHEIT
            if self._hub.temp_unit == "F"
            else UnitOfTemperature.CELSIUS
        )

    @property
    def native_value(self) -> float | None:
        return (self._hub.sensors.get(self._key) or {}).get("temperature")

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {"sensor_rom": (self._hub.sensors.get(self._key) or {}).get("id")}


class NewtronHumidity(NewtronEntity, SensorEntity):
    """Humidity of a DHT / AM2301 / SI7021 probe (key like 'AM2301')."""

    _attr_device_class = SensorDeviceClass.HUMIDITY
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = PERCENTAGE

    def __init__(self, hub: NewtronHub, key: str) -> None:
        super().__init__(hub)
        self._key = key
        self._attr_unique_id = f"{hub.serial_number}_hum_{key}"
        self._attr_name = f"Humidity {key}"

    @property
    def native_value(self) -> float | None:
        return (self._hub.sensors.get(self._key) or {}).get("humidity")


class NewtronDiagnostic(NewtronEntity, SensorEntity):
    """Read-only diagnostics extracted from DeviceStatus."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False

    def __init__(self, hub: NewtronHub, field: str, name: str, icon: str) -> None:
        super().__init__(hub)
        self._field = field
        self._attr_unique_id = f"{hub.serial_number}_{field}"
        self._attr_name = name
        self._attr_icon = icon
        if field == "free_ram_kb":
            self._attr_native_unit_of_measurement = "kB"

    @property
    def native_value(self) -> Any:
        raw = self._hub.device_info_raw
        if self._field in ("ip", "connection"):
            return (raw.get("network") or {}).get(self._field)
        if self._field == "uptime":
            return (raw.get("device_info") or {}).get("uptime")
        if self._field == "free_ram_kb":
            return (raw.get("system") or {}).get("free_ram_kb")
        return None
