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
    CAPABILITY_ON_OFF,
    CAPABILITY_RANGE,
    CAPABILITY_SEGMENT_COLOR,
    CAPABILITY_TOGGLE,
    INSTANCE_BRIGHTNESS,
    INSTANCE_COLOR_RGB,
    INSTANCE_COLOR_TEMP,
    INSTANCE_NIGHT_LIGHT,
    INSTANCE_POWER,
    INSTANCE_SCENE,
    INSTANCE_SEGMENT_COLOR,
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
