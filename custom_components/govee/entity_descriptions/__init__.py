"""Entity descriptions for Govee integration."""

from __future__ import annotations

from .light import LIGHT_DESCRIPTIONS, SEGMENT_LIGHT_DESCRIPTION
from .select import SELECT_DESCRIPTIONS
from .switch import SWITCH_DESCRIPTIONS

__all__ = [
    "LIGHT_DESCRIPTIONS",
    "SEGMENT_LIGHT_DESCRIPTION",
    "SELECT_DESCRIPTIONS",
    "SWITCH_DESCRIPTIONS",
]
