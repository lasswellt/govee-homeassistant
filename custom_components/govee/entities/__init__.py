"""Govee integration entity classes.

This package contains all entity implementations for the Govee integration,
organized by platform for better maintainability.

Backward Compatibility:
All entity classes are re-exported at the package level to maintain backward
compatibility with existing imports.
"""

from __future__ import annotations

from .base import GoveeEntity
from .light import GoveeLightEntity
from .segment import GoveeSegmentLight
from .select import GoveeSceneSelect
from .switch import GoveeNightLightSwitch, GoveeSwitchEntity

__all__ = [
    # Base entity
    "GoveeEntity",
    # Light entities
    "GoveeLightEntity",
    "GoveeSegmentLight",
    # Select entities
    "GoveeSceneSelect",
    # Switch entities
    "GoveeNightLightSwitch",
    "GoveeSwitchEntity",
]
