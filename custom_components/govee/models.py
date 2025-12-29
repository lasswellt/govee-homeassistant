"""Data models for Govee integration."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .api.const import (
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


@dataclass
class CapabilityParameter:
    """Parameter definition for a capability."""

    data_type: str  # ENUM, INTEGER, STRUCT, etc.
    range: dict[str, int] | None = None  # min, max, precision
    options: list[dict[str, Any]] | None = None  # For ENUM types
    fields: list[dict[str, Any]] | None = None  # For STRUCT types

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> CapabilityParameter:
        """Create from API response."""
        return cls(
            data_type=data.get("dataType", ""),
            range=data.get("range"),
            options=data.get("options"),
            fields=data.get("fields"),
        )


@dataclass
class DeviceCapability:
    """A device capability from the API."""

    type: str  # e.g., "devices.capabilities.on_off"
    instance: str  # e.g., "powerSwitch"
    parameters: CapabilityParameter | None = None

    # Parsed constraints for convenience
    min_value: int | None = None
    max_value: int | None = None

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> DeviceCapability:
        """Create from API response."""
        params_data = data.get("parameters", {})
        parameters = CapabilityParameter.from_api(params_data) if params_data else None

        # Extract range constraints
        min_value = None
        max_value = None
        if parameters and parameters.range:
            min_value = parameters.range.get("min")
            max_value = parameters.range.get("max")

        return cls(
            type=data.get("type", ""),
            instance=data.get("instance", ""),
            parameters=parameters,
            min_value=min_value,
            max_value=max_value,
        )


@dataclass
class GoveeDevice:
    """Govee device from API discovery."""

    device_id: str  # MAC address / identifier
    sku: str  # Product model (e.g., "H6160")
    device_name: str  # User-assigned name
    device_type: str  # e.g., "devices.types.light"
    capabilities: list[DeviceCapability] = field(default_factory=list)
    firmware_version: str | None = None

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> GoveeDevice:
        """Create from API response."""
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
        """Check if device has a specific capability."""
        for cap in self.capabilities:
            if cap.type == cap_type:
                if instance is None or cap.instance == instance:
                    return True
        return False

    def get_capability(
        self, cap_type: str, instance: str | None = None
    ) -> DeviceCapability | None:
        """Get a specific capability by type and optionally instance."""
        for cap in self.capabilities:
            if cap.type == cap_type:
                if instance is None or cap.instance == instance:
                    return cap
        return None

    def get_capability_by_instance(self, instance: str) -> DeviceCapability | None:
        """Get a capability by instance name only."""
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
        """Check if device supports segment control."""
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
        """Get brightness range (min, max)."""
        cap = self.get_capability(CAPABILITY_RANGE, INSTANCE_BRIGHTNESS)
        if cap and cap.min_value is not None and cap.max_value is not None:
            return (cap.min_value, cap.max_value)
        return (0, 100)  # Default v2.0 range

    def get_color_temp_range(self) -> tuple[int, int]:
        """Get color temperature range in Kelvin (min, max)."""
        cap = self.get_capability(CAPABILITY_COLOR_SETTING, INSTANCE_COLOR_TEMP)
        if cap and cap.min_value is not None and cap.max_value is not None:
            return (cap.min_value, cap.max_value)
        return (2000, 9000)  # Default range

    def get_scene_options(self) -> list[dict[str, Any]]:
        """Get available scene options."""
        cap = self.get_capability(CAPABILITY_DYNAMIC_SCENE, INSTANCE_LIGHT_SCENE)
        if cap and cap.parameters and cap.parameters.options:
            return cap.parameters.options
        return []

    def get_segment_count(self) -> int:
        """Get number of segments for RGBIC devices."""
        cap = self.get_capability(CAPABILITY_SEGMENT_COLOR, INSTANCE_SEGMENTED_COLOR)
        if cap and cap.parameters and cap.parameters.fields:
            for fld in cap.parameters.fields:
                if fld.get("fieldName") == "segment":
                    elem_range = fld.get("elementRange", {})
                    return elem_range.get("max", 0) + 1
        return 0


@dataclass
class GoveeDeviceState:
    """Current state of a Govee device."""

    device_id: str
    online: bool = True
    power_state: bool | None = None
    brightness: int | None = None  # 0-100
    color_rgb: tuple[int, int, int] | None = None
    color_temp_kelvin: int | None = None

    # Scene tracking (optimistic - can't query from API)
    current_scene: str | None = None
    current_scene_name: str | None = None

    # Segment states (if applicable)
    segment_colors: dict[int, tuple[int, int, int]] | None = None
    segment_brightness: dict[int, int] | None = None

    # Toggle states
    nightlight_on: bool | None = None

    # Appliance-specific states
    humidity: int | None = None
    temperature: float | None = None
    fan_speed: int | None = None
    mode: str | None = None

    @classmethod
    def from_api(cls, device_id: str, data: dict[str, Any]) -> GoveeDeviceState:
        """Create from API state response."""
        state = cls(device_id=device_id)

        capabilities = data.get("capabilities", [])
        for cap in capabilities:
            instance = cap.get("instance", "")
            cap_state = cap.get("state", {})
            value = cap_state.get("value")

            if instance == INSTANCE_POWER_SWITCH:
                state.power_state = value == 1
            elif instance == INSTANCE_BRIGHTNESS:
                state.brightness = value
            elif instance == INSTANCE_COLOR_RGB:
                if isinstance(value, int):
                    # Convert 24-bit int to RGB tuple
                    r = (value >> 16) & 0xFF
                    g = (value >> 8) & 0xFF
                    b = value & 0xFF
                    state.color_rgb = (r, g, b)
            elif instance == INSTANCE_COLOR_TEMP:
                state.color_temp_kelvin = value
            elif instance == INSTANCE_NIGHTLIGHT_TOGGLE:
                state.nightlight_on = value == 1
            elif instance == "online":
                state.online = value

        return state

    def update_from_api(self, data: dict[str, Any]) -> None:
        """Update state from API response."""
        new_state = GoveeDeviceState.from_api(self.device_id, data)

        # Update fields if they have values
        if new_state.power_state is not None:
            self.power_state = new_state.power_state
        if new_state.brightness is not None:
            self.brightness = new_state.brightness
        if new_state.color_rgb is not None:
            self.color_rgb = new_state.color_rgb
        if new_state.color_temp_kelvin is not None:
            self.color_temp_kelvin = new_state.color_temp_kelvin
        if new_state.nightlight_on is not None:
            self.nightlight_on = new_state.nightlight_on
        self.online = new_state.online

    def apply_optimistic_update(
        self,
        instance: str,
        value: Any,
    ) -> None:
        """Apply optimistic state update after successful command.

        This updates local state immediately without waiting for API poll.
        """
        if instance == INSTANCE_POWER_SWITCH:
            self.power_state = value == 1
        elif instance == INSTANCE_BRIGHTNESS:
            self.brightness = value
            # Clear scene when brightness changes
            self.current_scene = None
            self.current_scene_name = None
        elif instance == INSTANCE_COLOR_RGB:
            if isinstance(value, int):
                r = (value >> 16) & 0xFF
                g = (value >> 8) & 0xFF
                b = value & 0xFF
                self.color_rgb = (r, g, b)
            # Clear scene when color changes
            self.current_scene = None
            self.current_scene_name = None
        elif instance == INSTANCE_COLOR_TEMP:
            self.color_temp_kelvin = value
            # Clear scene when color temp changes
            self.current_scene = None
            self.current_scene_name = None
        elif instance == INSTANCE_LIGHT_SCENE:
            if isinstance(value, dict):
                scene_id = value.get("id") or value.get("paramId") or str(value)
                self.current_scene = str(scene_id)
            else:
                self.current_scene = str(value)
            self.current_scene_name = None
        elif instance == INSTANCE_DIY_SCENE:
            self.current_scene = f"diy_{value}"
            self.current_scene_name = None
        elif instance == INSTANCE_NIGHTLIGHT_TOGGLE:
            self.nightlight_on = value == 1


@dataclass
class CapabilityCommand:
    """Command to send to a device."""

    type: str  # Capability type
    instance: str  # Capability instance
    value: Any  # Value to set

    def to_api(self) -> dict[str, Any]:
        """Convert to API format."""
        return {
            "type": self.type,
            "instance": self.instance,
            "value": self.value,
        }


@dataclass
class SceneOption:
    """A scene option from the API."""

    name: str
    value: Any  # int or dict depending on scene type
    category: str | None = None

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> SceneOption:
        """Create from API response."""
        return cls(
            name=data.get("name", ""),
            value=data.get("value"),
            category=data.get("category"),
        )

    def to_command_value(self) -> Any:
        """Get the value format for sending commands."""
        return self.value
