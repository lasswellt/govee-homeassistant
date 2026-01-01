"""Govee integration entity classes.

This package contains all entity implementations for the Govee integration,
organized by platform for better maintainability.

Backward Compatibility:
All entity classes are re-exported at the package level to maintain backward
compatibility with existing imports.
"""

from __future__ import annotations

from .base import GoveeEntity
from .button import GoveeIdentifyButton, GoveeRefreshScenesButton
from .light import GoveeLightEntity
from .segment import GoveeSegmentLight
from .select import GoveeSceneSelect
from .sensor import GoveeRateLimitSensor
from .switch import (
    GoveeAirDeflectorSwitch,
    GoveeGradientSwitch,
    GoveeNightLightSwitch,
    GoveeOscillationSwitch,
    GoveeSwitchEntity,
    GoveeThermostatSwitch,
    GoveeWarmMistSwitch,
)

__all__ = [
    # Base entity
    "GoveeEntity",
    # Button entities
    "GoveeIdentifyButton",
    "GoveeRefreshScenesButton",
    # Light entities
    "GoveeLightEntity",
    "GoveeSegmentLight",
    # Select entities
    "GoveeSceneSelect",
    # Sensor entities
    "GoveeRateLimitSensor",
    # Switch entities
    "GoveeAirDeflectorSwitch",
    "GoveeGradientSwitch",
    "GoveeNightLightSwitch",
    "GoveeOscillationSwitch",
    "GoveeSwitchEntity",
    "GoveeThermostatSwitch",
    "GoveeWarmMistSwitch",
]
