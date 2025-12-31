"""Entity descriptions for Govee integration."""

from __future__ import annotations

from .button import BUTTON_DESCRIPTIONS
from .light import LIGHT_DESCRIPTIONS, SEGMENT_LIGHT_DESCRIPTION
from .select import SELECT_DESCRIPTIONS
from .sensor import SENSOR_DESCRIPTIONS
from .switch import SWITCH_DESCRIPTIONS

__all__ = [
    "BUTTON_DESCRIPTIONS",
    "LIGHT_DESCRIPTIONS",
    "SEGMENT_LIGHT_DESCRIPTION",
    "SELECT_DESCRIPTIONS",
    "SENSOR_DESCRIPTIONS",
    "SWITCH_DESCRIPTIONS",
]
