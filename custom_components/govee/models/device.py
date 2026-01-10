"""Device model representing a Govee device and its capabilities.

Frozen dataclass for immutability - device properties don't change at runtime.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# Capability type constants (from Govee API v2.0)
CAPABILITY_ON_OFF = "devices.capabilities.on_off"
CAPABILITY_RANGE = "devices.capabilities.range"
CAPABILITY_COLOR_SETTING = "devices.capabilities.color_setting"
CAPABILITY_SEGMENT_COLOR = "devices.capabilities.segment_color_setting"
CAPABILITY_DYNAMIC_SCENE = "devices.capabilities.dynamic_scene"
CAPABILITY_DIY_SCENE = "devices.capabilities.diy_scene"
CAPABILITY_MUSIC_MODE = "devices.capabilities.music_setting"
CAPABILITY_TOGGLE = "devices.capabilities.toggle"
CAPABILITY_WORK_MODE = "devices.capabilities.work_mode"
CAPABILITY_PROPERTY = "devices.capabilities.property"

# Device type constants
DEVICE_TYPE_LIGHT = "devices.types.light"
DEVICE_TYPE_PLUG = "devices.types.socket"
DEVICE_TYPE_HEATER = "devices.types.heater"
DEVICE_TYPE_HUMIDIFIER = "devices.types.humidifier"

# Instance constants
INSTANCE_POWER = "powerSwitch"
INSTANCE_BRIGHTNESS = "brightness"
INSTANCE_COLOR_RGB = "colorRgb"
INSTANCE_COLOR_TEMP = "colorTemperatureK"
INSTANCE_SEGMENT_COLOR = "segmentedColorRgb"
INSTANCE_SCENE = "lightScene"
INSTANCE_DIY = "diyScene"
INSTANCE_NIGHT_LIGHT = "nightlightToggle"
INSTANCE_GRADUAL_ON = "gradientToggle"
INSTANCE_TIMER = "timer"


@dataclass(frozen=True)
class ColorTempRange:
    """Color temperature range in Kelvin."""

    min_kelvin: int
    max_kelvin: int

    @classmethod
    def from_capability(cls, capability: dict[str, Any]) -> ColorTempRange | None:
        """Parse from capability parameters."""
        params = capability.get("parameters", {})
        range_data = params.get("range", {})
        min_k = range_data.get("min")
        max_k = range_data.get("max")
        if min_k is not None and max_k is not None:
            return cls(min_kelvin=int(min_k), max_kelvin=int(max_k))
        return None


@dataclass(frozen=True)
class SegmentCapability:
    """Segment control capability for RGBIC devices."""

    segment_count: int

    @classmethod
    def from_capability(cls, capability: dict[str, Any]) -> SegmentCapability | None:
        """Parse from capability parameters.

        The segment count can be found in different places:
        1. Direct 'segmentCount' parameter
        2. In fields[].elementRange.max + 1 (0-based index)
        3. In fields[].size.max (max array size)
        """
        params = capability.get("parameters", {})

        # Try direct segmentCount parameter
        count = params.get("segmentCount", 0)

        if not count:
            # Try to get from fields array structure
            fields = params.get("fields", [])
            for f in fields:
                if f.get("fieldName") == "segment":
                    # Check elementRange (0-based max index)
                    element_range = f.get("elementRange", {})
                    if "max" in element_range:
                        count = element_range["max"] + 1  # Convert to count
                        break
                    # Fallback to size.max
                    size = f.get("size", {})
                    if "max" in size:
                        count = size["max"]
                        break

        return cls(segment_count=count) if count else None


@dataclass(frozen=True)
class GoveeCapability:
    """Represents a device capability from Govee API."""

    type: str
    instance: str
    parameters: dict[str, Any] = field(default_factory=dict)

    @property
    def is_power(self) -> bool:
        """Check if this is a power on/off capability."""
        return self.type == CAPABILITY_ON_OFF and self.instance == INSTANCE_POWER

    @property
    def is_brightness(self) -> bool:
        """Check if this is a brightness capability."""
        return self.type == CAPABILITY_RANGE and self.instance == INSTANCE_BRIGHTNESS

    @property
    def is_color_rgb(self) -> bool:
        """Check if this is an RGB color capability."""
        return self.type == CAPABILITY_COLOR_SETTING and self.instance == INSTANCE_COLOR_RGB

    @property
    def is_color_temp(self) -> bool:
        """Check if this is a color temperature capability."""
        return self.type == CAPABILITY_COLOR_SETTING and self.instance == INSTANCE_COLOR_TEMP

    @property
    def is_segment_color(self) -> bool:
        """Check if this is a segment color capability."""
        return self.type == CAPABILITY_SEGMENT_COLOR

    @property
    def is_scene(self) -> bool:
        """Check if this is a scene capability."""
        return self.type == CAPABILITY_DYNAMIC_SCENE

    @property
    def is_toggle(self) -> bool:
        """Check if this is a toggle capability."""
        return self.type == CAPABILITY_TOGGLE

    @property
    def is_night_light(self) -> bool:
        """Check if this is a night light toggle."""
        return self.type == CAPABILITY_TOGGLE and self.instance == INSTANCE_NIGHT_LIGHT

    @property
    def brightness_range(self) -> tuple[int, int]:
        """Get brightness min/max range. Default (0, 100)."""
        if not self.is_brightness:
            return (0, 100)
        range_data = self.parameters.get("range", {})
        return (
            int(range_data.get("min", 0)),
            int(range_data.get("max", 100)),
        )


@dataclass(frozen=True)
class GoveeDevice:
    """Represents a Govee device with its static properties.

    Frozen for immutability - device capabilities don't change at runtime.
    """

    device_id: str
    sku: str
    name: str
    device_type: str
    capabilities: tuple[GoveeCapability, ...] = field(default_factory=tuple)
    is_group: bool = False

    @property
    def supports_power(self) -> bool:
        """Check if device supports on/off control."""
        return any(cap.is_power for cap in self.capabilities)

    @property
    def supports_brightness(self) -> bool:
        """Check if device supports brightness control."""
        return any(cap.is_brightness for cap in self.capabilities)

    @property
    def supports_rgb(self) -> bool:
        """Check if device supports RGB color."""
        return any(cap.is_color_rgb for cap in self.capabilities)

    @property
    def supports_color_temp(self) -> bool:
        """Check if device supports color temperature."""
        return any(cap.is_color_temp for cap in self.capabilities)

    @property
    def supports_segments(self) -> bool:
        """Check if device supports segment control (RGBIC)."""
        return any(cap.is_segment_color for cap in self.capabilities)

    @property
    def supports_scenes(self) -> bool:
        """Check if device supports dynamic scenes."""
        return any(cap.is_scene for cap in self.capabilities)

    @property
    def supports_night_light(self) -> bool:
        """Check if device supports night light toggle."""
        return any(cap.is_night_light for cap in self.capabilities)

    @property
    def is_plug(self) -> bool:
        """Check if device is a smart plug."""
        return self.device_type == DEVICE_TYPE_PLUG

    @property
    def is_light_device(self) -> bool:
        """Check if device is a light (not a plug or other appliance)."""
        return self.device_type == DEVICE_TYPE_LIGHT or self.supports_rgb or self.supports_color_temp

    @property
    def brightness_range(self) -> tuple[int, int]:
        """Get brightness range from capability. Default (0, 100)."""
        for cap in self.capabilities:
            if cap.is_brightness:
                return cap.brightness_range
        return (0, 100)

    @property
    def color_temp_range(self) -> ColorTempRange | None:
        """Get color temperature range if supported."""
        for cap in self.capabilities:
            if cap.is_color_temp:
                return ColorTempRange.from_capability({"parameters": cap.parameters})
        return None

    @property
    def segment_count(self) -> int:
        """Get number of segments for RGBIC devices."""
        for cap in self.capabilities:
            if cap.is_segment_color:
                seg = SegmentCapability.from_capability({"parameters": cap.parameters})
                return seg.segment_count if seg else 0
        return 0

    def get_capability(self, cap_type: str, instance: str) -> GoveeCapability | None:
        """Get a specific capability by type and instance."""
        for cap in self.capabilities:
            if cap.type == cap_type and cap.instance == instance:
                return cap
        return None

    @classmethod
    def from_api_response(cls, data: dict[str, Any]) -> GoveeDevice:
        """Create GoveeDevice from API response data.

        Args:
            data: Device dict from /user/devices endpoint.

        Returns:
            GoveeDevice instance.
        """
        device_id = data.get("device", "")
        sku = data.get("sku", "")
        name = data.get("deviceName", sku)
        device_type = data.get("type", "devices.types.light")

        # Check for group device types
        # Groups can be identified by:
        # 1. Explicit group device types
        # 2. Numeric-only device IDs (no colons like MAC addresses)
        is_group = device_type in (
            "devices.types.group",
            "devices.types.same_mode_group",
            "devices.types.scenic_group",
        ) or (device_id.isdigit())

        # Parse capabilities
        raw_caps = data.get("capabilities", [])
        capabilities = []
        for raw_cap in raw_caps:
            cap = GoveeCapability(
                type=raw_cap.get("type", ""),
                instance=raw_cap.get("instance", ""),
                parameters=raw_cap.get("parameters", {}),
            )
            capabilities.append(cap)

        return cls(
            device_id=device_id,
            sku=sku,
            name=name,
            device_type=device_type,
            capabilities=tuple(capabilities),
            is_group=is_group,
        )
