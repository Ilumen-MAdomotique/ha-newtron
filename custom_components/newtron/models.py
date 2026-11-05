"""Catalogue of Newtron boards, for preparing a card before it is online.

Figures come from the Newtron datasheets (newtron.tech, V2025-02 / V2025-12),
the "API REST - MQTT Commands" reference (capabilities, SmartHwRev, DALI)
and from what real boards report in DeviceStatus. ``confirmed`` is False
where neither source gave the figure; the technician can still adjust the
rows, and once the card is online its own layout replaces these numbers.

kind:
- relay:  outputs R1..Rn (2 consecutive relays can drive a shutter)
- dimmer: analog outputs AO1..AOn (0-10 V)
- ir:     no outputs, learned IR remotes; the whole device sits in one area
- dali:   lighting bus (up to 64 lights, 16 groups, 16 scenes); the whole
          device sits in one area

An undeclared 8R / 16R (SmartHwRev 0) reports "SMART 8R" / "SMART 16R" and
runs as a v1 8R / a v2 16R.
"""
from __future__ import annotations

from typing import Any

MODELS: dict[str, dict[str, Any]] = {
    # xR cards: 6 inputs + one per relay (Madomotique, 02/11/2026)
    "1R": {
        "label": "SMART 1R", "kind": "relay", "outputs": 1, "inputs": 7,
        "max_shutters": 0, "network": "WiFi", "confirmed": True,
    },
    "2R": {
        "label": "SMART 2R", "kind": "relay", "outputs": 2, "inputs": 8,
        "max_shutters": 1, "network": "WiFi", "confirmed": True,
    },
    "8R-V1": {
        "label": "SMART 8R-V1", "kind": "relay", "outputs": 8, "inputs": 8,
        "max_shutters": 4, "network": "Ethernet / WiFi", "confirmed": True,
    },
    "8R-V2": {
        "label": "SMART 8R-V2", "kind": "relay", "outputs": 8, "inputs": 8,
        "max_shutters": 4, "network": "Ethernet / WiFi", "confirmed": True,
    },
    "16R-V1": {
        "label": "SMART 16R-V1", "kind": "relay", "outputs": 16, "inputs": 16,
        "max_shutters": 8, "network": "Ethernet / WiFi", "confirmed": True,
    },
    "16R-V2": {
        "label": "SMART 16R-V2", "kind": "relay", "outputs": 16, "inputs": 16,
        "max_shutters": 8, "network": "Ethernet / WiFi", "confirmed": True,
    },
    "16R-EXT": {
        "label": "SMART 16R + carte d'extension", "kind": "relay", "outputs": 32,
        "inputs": 32, "max_shutters": 16, "network": "Ethernet / WiFi", "confirmed": True,
    },
    "IR-V1": {
        "label": "SMART IR-V1", "kind": "ir", "outputs": 0, "inputs": 0,
        "max_shutters": 0, "network": "WiFi", "confirmed": True,
    },
    "IR-V2": {
        "label": "SMART IR-V2", "kind": "ir", "outputs": 0, "inputs": 0,
        "max_shutters": 0, "network": "WiFi", "confirmed": True,
    },
    "DALI": {
        "label": "SMART DALI", "kind": "dali", "outputs": 0, "inputs": 0,
        "max_shutters": 0, "network": "?", "confirmed": True,
    },
    "DIMMER": {
        "label": "SMART DIMMER", "kind": "dimmer", "outputs": 8, "inputs": 8,
        "max_shutters": 0, "network": "?", "confirmed": True,
    },
    # No roller shutter nor alarm in the professional firmware
    "PRO": {
        "label": "SMART PRO", "kind": "relay", "outputs": 8, "inputs": 8,
        "max_shutters": 0, "network": "?", "confirmed": False,
    },
}


def model_from_board(board: str | None) -> str | None:
    """Catalogue key for a board name as reported by the card.

    "SMART 16R-v2" -> "16R-V2", "SMART 16R EXTENDED" -> "16R-EXT",
    "SMART 8R" -> "8R-V1".
    """
    if not board:
        return None
    name = board.upper().replace("SMART", "").strip()
    if "EXT" in name:
        return "16R-EXT"
    for key in sorted(MODELS, key=len, reverse=True):
        if name.replace(" ", "") == key or name.replace(" ", "").startswith(key):
            return key
    base = name.split("-")[0].replace(" ", "")
    # Revision not declared: a 16R runs as v2, an 8R as v1
    if base == "16R":
        return "16R-V2"
    if f"{base}-V1" in MODELS:
        return f"{base}-V1"
    return base if base in MODELS else None
