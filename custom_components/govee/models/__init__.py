"""Domain models for Govee integration.

All models are frozen dataclasses for immutability.
"""

from .commands import (
    BrightnessCommand,
    ColorCommand,
    ColorTempCommand,
    DeviceCommand,
    DIYSceneCommand,
    ModeCommand,
    OscillationCommand,
    PowerCommand,
    SceneCommand,
    SegmentColorCommand,
    ToggleCommand,
    WorkModeCommand,
    create_night_light_command,
)
from .device import (
    ColorTempRange,
    GoveeCapability,
    GoveeDevice,
    SegmentCapability,
)
from .state import GoveeDeviceState, RGBColor, SegmentState

__all__ = [
    # Device
    "GoveeDevice",
    "GoveeCapability",
    "ColorTempRange",
    "SegmentCapability",
    # State
    "GoveeDeviceState",
    "RGBColor",
    "SegmentState",
    # Commands
    "DeviceCommand",
    "PowerCommand",
    "BrightnessCommand",
    "ColorCommand",
    "ColorTempCommand",
    "SceneCommand",
    "DIYSceneCommand",
    "SegmentColorCommand",
    "ToggleCommand",
    "OscillationCommand",
    "WorkModeCommand",
    "ModeCommand",
    "create_night_light_command",
]
