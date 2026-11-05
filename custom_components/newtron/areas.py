"""Automatic area assignment from the names set on the board.

An output named "Spots chambre 2" lands in the "Chambre 2" area. Area
aliases count too, so a technician can add "ch 2" as an alias of
"Chambre 2" to catch "Tete de lit ch 2".

The area chosen automatically is remembered in the entity registry options
under this domain. An area picked by hand in Home Assistant differs from
that remembered value and is never touched again.
"""
from __future__ import annotations

import re
import unicodedata

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import area_registry as ar, entity_registry as er

from .const import DOMAIN

OPT_AUTO_AREA = "auto_area"

_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def _normalize(text: str) -> str:
    """'Séjour  Piscine' -> ' sejour piscine ' (padded for whole-word matching)."""
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return f" {_NON_ALNUM.sub(' ', text.lower()).strip()} "


@callback
def async_match_area(hass: HomeAssistant, name: str) -> str | None:
    """Return the id of the area whose name or alias appears in ``name``.

    The longest match wins, so "Chambre Parents" beats "Chambre".
    """
    haystack = _normalize(name)
    best: tuple[int, str] | None = None
    for area in ar.async_get(hass).async_list_areas():
        for candidate in (area.name, *area.aliases):
            needle = _normalize(candidate)
            if needle.strip() and needle in haystack:
                if best is None or len(needle) > best[0]:
                    best = (len(needle), area.id)
    return best[1] if best else None


@callback
def async_auto_assign_area(hass: HomeAssistant, entity_id: str, name: str) -> None:
    """Put ``entity_id`` in the area matching ``name`` unless set by hand."""
    registry = er.async_get(hass)
    entry = registry.async_get(entity_id)
    if entry is None:
        return
    previous_auto = entry.options.get(DOMAIN, {}).get(OPT_AUTO_AREA)
    if entry.area_id is not None and entry.area_id != previous_auto:
        return  # chosen by hand in Home Assistant
    area_id = async_match_area(hass, name)
    if area_id == entry.area_id and area_id == previous_auto:
        return
    registry.async_update_entity(entity_id, area_id=area_id)
    registry.async_update_entity_options(
        entity_id, DOMAIN, {**entry.options.get(DOMAIN, {}), OPT_AUTO_AREA: area_id}
    )
