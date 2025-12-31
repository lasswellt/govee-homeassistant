"""Govee device model with capabilities and feature detection."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..api.const import (
    CAPABILITY_COLOR_SETTING,
    CAPABILITY_DYNAMIC_SCENE,
    CAPABILITY_MUSIC_SETTING,
    CAPABILITY_ON_OFF,
    CAPABILITY_RANGE,
    CAPABILITY_SEGMENT_COLOR,
    CAPABILITY_TOGGLE,
    INSTANCE_BRIGHTNESS,
    INSTANCE_COLOR_RGB,
    INSTANCE_COLOR_TEMP,
    INSTANCE_DIY_SCENE,
    INSTANCE_LIGHT_SCENE,
    INSTANCE_MUSIC_MODE,
    INSTANCE_NIGHTLIGHT_TOGGLE,
    INSTANCE_POWER_SWITCH,
    INSTANCE_SEGMENTED_BRIGHTNESS,
    INSTANCE_SEGMENTED_COLOR,
)
from .capability import DeviceCapability


@dataclass
class GoveeDevice:
    """Govee device from API discovery.

    Represents a single Govee device with its capabilities, providing
    feature detection helpers and capability querying methods.

    Attributes:
        device_id: Device MAC address / identifier
        sku: Product model (e.g., "H6160")
        device_name: User-assigned device name
        device_type: Device type identifier (e.g., "devices.types.light")
        capabilities: List of device capabilities
        firmware_version: Device firmware version (optional)
    """

    device_id: str  # MAC address / identifier
    sku: str  # Product model (e.g., "H6160")
    device_name: str  # User-assigned name
    device_type: str  # e.g., "devices.types.light"
    capabilities: list[DeviceCapability] = field(default_factory=list)
    firmware_version: str | None = None

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> GoveeDevice:
        """Create from API response.

        Args:
            data: Device dictionary from API discovery

        Returns:
            GoveeDevice instance with parsed capabilities
        """
        capabilities = [
            DeviceCapability.from_api(cap) for cap in data.get("capabilities", [])
        ]

        return cls(
            device_id=data.get("device", ""),
            sku=data.get("sku", ""),
            device_name=data.get("deviceName", data.get("device", "")),
            device_type=data.get("type", ""),
            capabilities=capabilities,
            firmware_version=data.get("version"),
        )

    # === Capability Helpers ===

    def has_capability(self, cap_type: str, instance: str | None = None) -> bool:
        """Check if device has a specific capability.

        Args:
            cap_type: Capability type to check
            instance: Optional instance name to match

        Returns:
            True if device has the capability
        """
        for cap in self.capabilities:
            if cap.type == cap_type:
                if instance is None or cap.instance == instance:
                    return True
        return False

    def get_capability(
        self, cap_type: str, instance: str | None = None
    ) -> DeviceCapability | None:
        """Get a specific capability by type and optionally instance.

        Args:
            cap_type: Capability type to find
            instance: Optional instance name to match

        Returns:
            DeviceCapability if found, None otherwise
        """
        for cap in self.capabilities:
            if cap.type == cap_type:
                if instance is None or cap.instance == instance:
                    return cap
        return None

    def get_capability_by_instance(self, instance: str) -> DeviceCapability | None:
        """Get a capability by instance name only.

        Args:
            instance: Instance name to find

        Returns:
            DeviceCapability if found, None otherwise
        """
        for cap in self.capabilities:
            if cap.instance == instance:
                return cap
        return None

    # === Feature Detection ===

    @property
    def supports_on_off(self) -> bool:
        """Check if device supports power on/off."""
        return self.has_capability(CAPABILITY_ON_OFF, INSTANCE_POWER_SWITCH)

    @property
    def supports_brightness(self) -> bool:
        """Check if device supports brightness control."""
        return self.has_capability(CAPABILITY_RANGE, INSTANCE_BRIGHTNESS)

    @property
    def supports_color(self) -> bool:
        """Check if device supports RGB color."""
        return self.has_capability(CAPABILITY_COLOR_SETTING, INSTANCE_COLOR_RGB)

    @property
    def supports_color_temp(self) -> bool:
        """Check if device supports color temperature."""
        return self.has_capability(CAPABILITY_COLOR_SETTING, INSTANCE_COLOR_TEMP)

    @property
    def supports_scenes(self) -> bool:
        """Check if device supports dynamic scenes."""
        return self.has_capability(CAPABILITY_DYNAMIC_SCENE, INSTANCE_LIGHT_SCENE)

    @property
    def supports_diy_scenes(self) -> bool:
        """Check if device supports DIY scenes."""
        return self.has_capability(CAPABILITY_DYNAMIC_SCENE, INSTANCE_DIY_SCENE)

    @property
    def supports_segments(self) -> bool:
        """Check if device supports segment control (RGBIC)."""
        return self.has_capability(CAPABILITY_SEGMENT_COLOR)

    @property
    def supports_music_mode(self) -> bool:
        """Check if device supports music reactive mode."""
        return self.has_capability(CAPABILITY_MUSIC_SETTING, INSTANCE_MUSIC_MODE)

    @property
    def supports_nightlight(self) -> bool:
        """Check if device supports nightlight toggle mode."""
        return self.has_capability(CAPABILITY_TOGGLE, INSTANCE_NIGHTLIGHT_TOGGLE)

    # === Range Helpers ===

    def get_brightness_range(self) -> tuple[int, int]:
        """Get brightness range (min, max).

        Returns:
            Tuple of (min, max) brightness values
        """
        cap = self.get_capability(CAPABILITY_RANGE, INSTANCE_BRIGHTNESS)
        if cap and cap.min_value is not None and cap.max_value is not None:
            return (cap.min_value, cap.max_value)
        return (0, 100)  # Default v2.0 range

    def get_color_temp_range(self) -> tuple[int, int]:
        """Get color temperature range in Kelvin (min, max).

        Returns:
            Tuple of (min, max) color temperature in Kelvin
        """
        cap = self.get_capability(CAPABILITY_COLOR_SETTING, INSTANCE_COLOR_TEMP)
        if cap and cap.min_value is not None and cap.max_value is not None:
            return (cap.min_value, cap.max_value)
        return (2000, 9000)  # Default range

    def get_scene_options(self) -> list[dict[str, Any]]:
        """Get available scene options from capability parameters.

        Returns:
            List of scene option dictionaries
        """
        cap = self.get_capability(CAPABILITY_DYNAMIC_SCENE, INSTANCE_LIGHT_SCENE)
        if cap and cap.parameters and cap.parameters.options:
            return cap.parameters.options
        return []

    def get_segment_count(self) -> int:
        """Get number of segments for RGBIC devices.

        Returns:
            Number of segments (0 if not an RGBIC device)
        """
        cap = self.get_capability(CAPABILITY_SEGMENT_COLOR, INSTANCE_SEGMENTED_COLOR)
        if cap and cap.parameters and cap.parameters.fields:
            for fld in cap.parameters.fields:
                if fld.get("fieldName") == "segment":
                    elem_range: dict[str, int] = fld.get("elementRange", {})
                    max_val: int = elem_range.get("max", 0)
                    return max_val + 1
        return 0
