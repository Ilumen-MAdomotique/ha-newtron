"""Entities made from a Newtron output by "Show as" (switch_as_x).

Home Assistant files the new light, fan, valve... under the card's area,
which is empty: Newtron puts each output in its own area. The new entity
takes the output's area and name instead, when it is created and each time
the inventory is applied, so a converted relay stays where it was.
"""
from __future__ import annotations

import voluptuous as vol

from homeassistant.const import CONF_ENTITY_ID
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers import entity_registry as er

from .const import DOMAIN

WRAPPER_PLATFORM = "switch_as_x"


@callback
def async_wrapped_entity(
    hass: HomeAssistant, wrapper: er.RegistryEntry
) -> er.RegistryEntry | None:
    """The Newtron entity <wrapper> was made from, if any."""
    if wrapper.platform != WRAPPER_PLATFORM or not wrapper.config_entry_id:
        return None
    entry = hass.config_entries.async_get_entry(wrapper.config_entry_id)
    if entry is None:
        return None
    ent_reg = er.async_get(hass)
    try:
        # Stored as an entity_id or, in newer versions, a registry id.
        entity_id = er.async_validate_entity_id(ent_reg, entry.options[CONF_ENTITY_ID])
    except (KeyError, vol.Invalid):
        return None
    wrapped = ent_reg.async_get(entity_id)
    return wrapped if wrapped is not None and wrapped.platform == DOMAIN else None


@callback
def async_wrappers_of(hass: HomeAssistant, entity_id: str) -> list[er.RegistryEntry]:
    """The entities "Show as" made from the Newtron entity <entity_id>."""
    return [
        wrapper
        for wrapper in er.async_get(hass).entities.values()
        if wrapper.platform == WRAPPER_PLATFORM
        and (wrapped := async_wrapped_entity(hass, wrapper)) is not None
        and wrapped.entity_id == entity_id
    ]


@callback
def async_follow_output(
    hass: HomeAssistant, wrapper: er.RegistryEntry, wrapped: er.RegistryEntry
) -> None:
    """Give <wrapper> the area and the name of the output it was made from."""
    updates: dict[str, str] = {}
    if wrapped.area_id and wrapper.area_id != wrapped.area_id:
        updates["area_id"] = wrapped.area_id
    # A name of its own, otherwise HA shows "<card> <output>".
    name = wrapped.name or wrapped.original_name
    if name and wrapper.name != name:
        updates["name"] = name
    if updates:
        er.async_get(hass).async_update_entity(wrapper.entity_id, **updates)


@callback
def async_setup_wrappers(hass: HomeAssistant) -> None:
    """Fix the existing "Show as" entities, then each new one as it appears."""

    @callback
    def _is_created(data: er.EventEntityRegistryUpdatedData) -> bool:
        return data["action"] == "create"

    @callback
    def _on_created(event: Event[er.EventEntityRegistryUpdatedData]) -> None:
        wrapper = er.async_get(hass).async_get(event.data["entity_id"])
        if wrapper is not None and (wrapped := async_wrapped_entity(hass, wrapper)):
            async_follow_output(hass, wrapper, wrapped)

    hass.bus.async_listen(
        er.EVENT_ENTITY_REGISTRY_UPDATED, _on_created, event_filter=_is_created
    )
    for wrapper in list(er.async_get(hass).entities.values()):
        if (wrapped := async_wrapped_entity(hass, wrapper)) is not None:
            async_follow_output(hass, wrapper, wrapped)
