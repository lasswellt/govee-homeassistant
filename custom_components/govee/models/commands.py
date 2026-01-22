"""Command pattern models for device control.

Each command encapsulates a single control action and knows how to
serialize itself for the Govee API.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

from .device import (
    CAPABILITY_COLOR_SETTING,
    CAPABILITY_DYNAMIC_SCENE,
    CAPABILITY_MODE,
    CAPABILITY_MUSIC_MODE,
    CAPABILITY_ON_OFF,
    CAPABILITY_RANGE,
    CAPABILITY_SEGMENT_COLOR,
    CAPABILITY_TOGGLE,
    CAPABILITY_WORK_MODE,
    INSTANCE_BRIGHTNESS,
    INSTANCE_COLOR_RGB,
    INSTANCE_COLOR_TEMP,
    INSTANCE_DIY,
    INSTANCE_DREAMVIEW,
    INSTANCE_MUSIC_MODE,
    INSTANCE_NIGHT_LIGHT,
    INSTANCE_OSCILLATION,
    INSTANCE_POWER,
    INSTANCE_SCENE,
    INSTANCE_SEGMENT_COLOR,
    INSTANCE_WORK_MODE,
)
from .state import RGBColor


@dataclass(frozen=True)
class DeviceCommand(ABC):
    """Base class for device commands.

    Commands are immutable value objects that know how to serialize
    themselves for the Govee API.
    """

    @property
    @abstractmethod
    def capability_type(self) -> str:
        """Get the capability type for this command."""
        ...

    @property
    @abstractmethod
    def instance(self) -> str:
        """Get the instance for this command."""
        ...

    @abstractmethod
    def get_value(self) -> Any:
        """Get the value to send to the API."""
        ...

    def to_api_payload(self) -> dict[str, Any]:
        """Convert to Govee API command payload.

        Returns:
            Dict matching Govee API v2.0 /device/control format.
        """
        return {
            "type": self.capability_type,
            "instance": self.instance,
            "value": self.get_value(),
        }


@dataclass(frozen=True)
class PowerCommand(DeviceCommand):
    """Command to turn device on or off."""

    power_on: bool

    @property
    def capability_type(self) -> str:
        return CAPABILITY_ON_OFF

    @property
    def instance(self) -> str:
        return INSTANCE_POWER

    def get_value(self) -> int:
        return 1 if self.power_on else 0


@dataclass(frozen=True)
class BrightnessCommand(DeviceCommand):
    """Command to set device brightness."""

    brightness: int  # Device-scale value (typically 0-100 or 0-254)

    @property
    def capability_type(self) -> str:
        return CAPABILITY_RANGE

    @property
    def instance(self) -> str:
        return INSTANCE_BRIGHTNESS

    def get_value(self) -> int:
        return self.brightness


@dataclass(frozen=True)
class ColorCommand(DeviceCommand):
    """Command to set device RGB color."""

    color: RGBColor

    @property
    def capability_type(self) -> str:
        return CAPABILITY_COLOR_SETTING

    @property
    def instance(self) -> str:
        return INSTANCE_COLOR_RGB

    def get_value(self) -> int:
        """Return packed RGB integer."""
        return self.color.as_packed_int


@dataclass(frozen=True)
class ColorTempCommand(DeviceCommand):
    """Command to set device color temperature."""

    kelvin: int

    @property
    def capability_type(self) -> str:
        return CAPABILITY_COLOR_SETTING

    @property
    def instance(self) -> str:
        return INSTANCE_COLOR_TEMP

    def get_value(self) -> int:
        return self.kelvin


@dataclass(frozen=True)
class SceneCommand(DeviceCommand):
    """Command to activate a scene."""

    scene_id: int
    scene_name: str = ""

    @property
    def capability_type(self) -> str:
        return CAPABILITY_DYNAMIC_SCENE

    @property
    def instance(self) -> str:
        return INSTANCE_SCENE

    def get_value(self) -> dict[str, Any]:
        return {
            "id": self.scene_id,
            "name": self.scene_name,
        }


@dataclass(frozen=True)
class DIYSceneCommand(DeviceCommand):
    """Command to activate a DIY scene."""

    scene_id: int
    scene_name: str = ""

    @property
    def capability_type(self) -> str:
        return CAPABILITY_DYNAMIC_SCENE

    @property
    def instance(self) -> str:
        return INSTANCE_DIY

    def get_value(self) -> int:
        return self.scene_id


@dataclass(frozen=True)
class SegmentColorCommand(DeviceCommand):
    """Command to set color for specific segments."""

    segment_indices: tuple[int, ...]
    color: RGBColor

    @property
    def capability_type(self) -> str:
        return CAPABILITY_SEGMENT_COLOR

    @property
    def instance(self) -> str:
        return INSTANCE_SEGMENT_COLOR

    def get_value(self) -> dict[str, Any]:
        return {
            "segment": list(self.segment_indices),
            "rgb": self.color.as_packed_int,
        }


@dataclass(frozen=True)
class ToggleCommand(DeviceCommand):
    """Command to toggle a feature (night light, gradual on, etc)."""

    toggle_instance: str
    enabled: bool

    @property
    def capability_type(self) -> str:
        return CAPABILITY_TOGGLE

    @property
    def instance(self) -> str:
        return self.toggle_instance

    def get_value(self) -> int:
        return 1 if self.enabled else 0


def create_night_light_command(enabled: bool) -> ToggleCommand:
    """Create a command to toggle night light mode."""
    return ToggleCommand(toggle_instance=INSTANCE_NIGHT_LIGHT, enabled=enabled)


def create_dreamview_command(enabled: bool) -> ToggleCommand:
    """Create a command to toggle DreamView (Movie Mode)."""
    return ToggleCommand(toggle_instance=INSTANCE_DREAMVIEW, enabled=enabled)


@dataclass(frozen=True)
class OscillationCommand(DeviceCommand):
    """Command to toggle fan oscillation."""

    oscillating: bool

    @property
    def capability_type(self) -> str:
        return CAPABILITY_TOGGLE

    @property
    def instance(self) -> str:
        return INSTANCE_OSCILLATION

    def get_value(self) -> int:
        return 1 if self.oscillating else 0


@dataclass(frozen=True)
class WorkModeCommand(DeviceCommand):
    """Command to set fan work mode and speed.

    Work modes:
    - 1 (gearMode): Manual speed control with mode_value 1=Low, 2=Medium, 3=High
    - 3 (Auto): Automatic mode
    - 9 (Fan): Fan mode

    Mode values (for gearMode):
    - 1: Low
    - 2: Medium
    - 3: High
    """

    work_mode: int  # 1=gearMode, 3=Auto, 9=Fan
    mode_value: int  # Speed for gearMode: 1=Low, 2=Medium, 3=High

    @property
    def capability_type(self) -> str:
        return CAPABILITY_WORK_MODE

    @property
    def instance(self) -> str:
        return INSTANCE_WORK_MODE

    def get_value(self) -> dict[str, int]:
        return {"workMode": self.work_mode, "modeValue": self.mode_value}


@dataclass(frozen=True)
class ModeCommand(DeviceCommand):
    """Command to set a mode value (e.g., HDMI source).

    This is a generic command for mode-type capabilities that take
    an integer value. Used for HDMI source selection on devices
    like the Govee AI Sync Box (H6604).
    """

    mode_instance: str  # e.g., "hdmiSource"
    value: int  # e.g., 1, 2, 3, 4 for HDMI ports

    @property
    def capability_type(self) -> str:
        return CAPABILITY_MODE

    @property
    def instance(self) -> str:
        return self.mode_instance

    def get_value(self) -> int:
        return self.value


@dataclass(frozen=True)
class MusicModeCommand(DeviceCommand):
    """Command to set music mode with STRUCT payload.

    This command is for devices with STRUCT-based music mode capability
    (devices.capabilities.music_setting with musicMode instance).

    The STRUCT payload includes:
    - musicMode (required): Integer 1-11 selecting the mode
    - sensitivity (required): Integer 0-100 for microphone sensitivity
    - autoColor (optional): 1=auto colors, 0=use specified RGB
    - rgb (optional): Packed RGB integer, only used when autoColor=0

    Example API payload:
    {
        "type": "devices.capabilities.music_setting",
        "instance": "musicMode",
        "value": {
            "musicMode": 1,
            "sensitivity": 50,
            "autoColor": 1
        }
    }
    """

    music_mode: int  # 1-11 (Rhythm, Spectrum, Rolling, etc.)
    sensitivity: int  # 0-100
    auto_color: int = 1  # 1=on (auto), 0=off (use rgb)
    rgb: int | None = None  # Packed RGB, only used when auto_color=0

    @property
    def capability_type(self) -> str:
        return CAPABILITY_MUSIC_MODE

    @property
    def instance(self) -> str:
        return INSTANCE_MUSIC_MODE

    def get_value(self) -> dict[str, Any]:
        """Return STRUCT value for music mode command."""
        value: dict[str, Any] = {
            "musicMode": self.music_mode,
            "sensitivity": self.sensitivity,
            "autoColor": self.auto_color,
        }
        if self.rgb is not None and self.auto_color == 0:
            value["rgb"] = self.rgb
        return value
