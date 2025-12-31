"""Govee integration data models.

This package contains all data model classes for the Govee integration,
organized into focused modules for better maintainability.

Backward Compatibility:
All classes are re-exported at the package level to maintain backward
compatibility with existing imports (e.g., `from .models import GoveeDevice`).
"""

from __future__ import annotations

from .capability import CapabilityCommand, CapabilityParameter, DeviceCapability
from .config import GoveeConfigEntry, GoveeRuntimeData
from .device import GoveeDevice
from .scene import SceneOption
from .state import GoveeDeviceState

__all__ = [
    # Capability models
    "CapabilityCommand",
    "CapabilityParameter",
    "DeviceCapability",
    # Configuration types
    "GoveeConfigEntry",
    "GoveeRuntimeData",
    # Device model
    "GoveeDevice",
    # Scene model
    "SceneOption",
    # State model
    "GoveeDeviceState",
]
