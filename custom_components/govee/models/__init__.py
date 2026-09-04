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
    MusicModeCommand,
    OscillationCommand,
    PowerCommand,
    RangeCommand,
    SceneCommand,
    SegmentColorCommand,
    SnapshotCommand,
    TemperatureSettingCommand,
    ToggleCommand,
    WorkModeCommand,
    create_dreamview_command,
    create_night_light_command,
)
from .device import (
    ColorTempRange,
    GoveeCapability,
    GoveeDevice,
    SegmentCapability,
)
from .state import GoveeDeviceState, ProbeReading, RGBColor, SegmentState
from .transport import TRANSPORT_KINDS, TransportHealth, TransportKind

__all__ = [
    # Device
    "GoveeDevice",
    "GoveeCapability",
    "ColorTempRange",
    "SegmentCapability",
    # State
    "GoveeDeviceState",
    "ProbeReading",
    "RGBColor",
    "SegmentState",
    # Commands
    "DeviceCommand",
    "PowerCommand",
    "BrightnessCommand",
    "RangeCommand",
    "ColorCommand",
    "ColorTempCommand",
    "SceneCommand",
    "DIYSceneCommand",
    "SnapshotCommand",
    "SegmentColorCommand",
    "ToggleCommand",
    "OscillationCommand",
    "WorkModeCommand",
    "ModeCommand",
    "MusicModeCommand",
    "TemperatureSettingCommand",
    "create_dreamview_command",
    "create_night_light_command",
    # Transport health
    "TransportHealth",
    "TransportKind",
    "TRANSPORT_KINDS",
]
