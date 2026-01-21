"""Device state models.

Mutable state that changes with device updates from API or MQTT.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class RGBColor:
    """Immutable RGB color representation."""

    r: int
    g: int
    b: int

    def __post_init__(self) -> None:
        """Validate color values are in range."""
        # Use object.__setattr__ because dataclass is frozen
        object.__setattr__(self, "r", max(0, min(255, self.r)))
        object.__setattr__(self, "g", max(0, min(255, self.g)))
        object.__setattr__(self, "b", max(0, min(255, self.b)))

    @property
    def as_tuple(self) -> tuple[int, int, int]:
        """Return as (r, g, b) tuple."""
        return (self.r, self.g, self.b)

    @property
    def as_packed_int(self) -> int:
        """Return as packed integer for Govee API: (R << 16) + (G << 8) + B."""
        return (self.r << 16) + (self.g << 8) + self.b

    @classmethod
    def from_packed_int(cls, value: int) -> RGBColor:
        """Create from Govee API packed integer."""
        r = (value >> 16) & 0xFF
        g = (value >> 8) & 0xFF
        b = value & 0xFF
        return cls(r=r, g=g, b=b)

    @classmethod
    def from_dict(cls, data: dict[str, int]) -> RGBColor:
        """Create from dict with r, g, b keys."""
        return cls(
            r=data.get("r", 0),
            g=data.get("g", 0),
            b=data.get("b", 0),
        )


@dataclass(frozen=True)
class SegmentState:
    """State of a single segment in RGBIC device."""

    index: int
    color: RGBColor
    brightness: int = 100

    @classmethod
    def from_dict(cls, data: dict[str, Any], index: int) -> SegmentState:
        """Create from segment dict."""
        color = RGBColor.from_dict(data.get("color", {}))
        brightness = data.get("brightness", 100)
        return cls(index=index, color=color, brightness=brightness)


@dataclass
class GoveeDeviceState:
    """Mutable device state updated from API or MQTT.

    Unlike GoveeDevice (frozen), state changes frequently and needs
    to be updated in-place for performance.
    """

    device_id: str
    online: bool = True
    power_state: bool = False
    brightness: int = 100
    color: RGBColor | None = None
    color_temp_kelvin: int | None = None
    active_scene: str | None = None
    segments: list[SegmentState] = field(default_factory=list)
    diy_speed: int | None = None  # DIY scene playback speed 0-100
    diy_style: str | None = None  # DIY animation style (Fade, Jumping, etc.)
    diy_style_value: int | None = None  # DIY animation style numeric value (0-4)
    music_mode_enabled: bool | None = None  # Music mode on/off state

    # Scene speed (for regular scenes that support speed control)
    scene_speed: int | None = None  # Current speed value
    scene_speed_range: tuple[int, int] | None = None  # (min, max) from speed_info

    # Fan state
    oscillating: bool | None = None  # Fan oscillation on/off
    work_mode: int | None = None  # Fan work mode: 1=gearMode, 3=Auto, 9=Fan
    mode_value: int | None = None  # Fan speed for gearMode: 1=Low, 2=Medium, 3=High

    # HDMI source state (for devices like AI Sync Box H6604)
    hdmi_source: int | None = None  # HDMI port: 1, 2, 3, 4

    # Source tracking for state management
    # "api" = from REST poll, "mqtt" = from push, "optimistic" = from command
    source: str = "api"

    def update_from_api(self, data: dict[str, Any]) -> None:
        """Update state from API response.

        Args:
            data: Device state dict from /device/state endpoint.
        """
        self.source = "api"

        # Parse capabilities array for state values
        capabilities = data.get("capabilities", [])
        for cap in capabilities:
            cap_type = cap.get("type", "")
            instance = cap.get("instance", "")
            state = cap.get("state", {})
            value = state.get("value")

            if cap_type == "devices.capabilities.online":
                self.online = bool(value)

            elif cap_type == "devices.capabilities.on_off":
                if instance == "powerSwitch":
                    self.power_state = bool(value)

            elif cap_type == "devices.capabilities.range":
                if instance == "brightness":
                    self.brightness = int(value) if value is not None else 100

            elif cap_type == "devices.capabilities.color_setting":
                if instance == "colorRgb":
                    if isinstance(value, int):
                        self.color = RGBColor.from_packed_int(value)
                    elif isinstance(value, dict):
                        self.color = RGBColor.from_dict(value)
                elif instance == "colorTemperatureK":
                    self.color_temp_kelvin = int(value) if value is not None else None

            elif cap_type == "devices.capabilities.toggle":
                if instance == "oscillationToggle":
                    self.oscillating = bool(value)

            elif cap_type == "devices.capabilities.work_mode":
                if instance == "workMode" and isinstance(value, dict):
                    self.work_mode = value.get("workMode")
                    self.mode_value = value.get("modeValue")

            elif cap_type == "devices.capabilities.mode":
                if instance == "hdmiSource":
                    self.hdmi_source = int(value) if value is not None else None

    def update_from_mqtt(self, data: dict[str, Any]) -> None:
        """Update state from MQTT push message.

        MQTT format differs from REST API - uses onOff/brightness/color keys.

        Args:
            data: State dict from MQTT message.
        """
        self.source = "mqtt"

        if "onOff" in data:
            self.power_state = bool(data["onOff"])

        if "brightness" in data:
            self.brightness = int(data["brightness"])

        if "color" in data:
            color_data = data["color"]
            if isinstance(color_data, dict):
                self.color = RGBColor.from_dict(color_data)
            elif isinstance(color_data, int):
                self.color = RGBColor.from_packed_int(color_data)

        if "colorTemInKelvin" in data:
            temp = data["colorTemInKelvin"]
            self.color_temp_kelvin = int(temp) if temp else None

    def apply_optimistic_power(self, power_on: bool) -> None:
        """Apply optimistic power state update."""
        self.power_state = power_on
        self.source = "optimistic"
        # Clear scene when turning off (scene is no longer active)
        if not power_on:
            self.active_scene = None

    def apply_optimistic_brightness(self, brightness: int) -> None:
        """Apply optimistic brightness update."""
        self.brightness = brightness
        self.source = "optimistic"

    def apply_optimistic_color(self, color: RGBColor) -> None:
        """Apply optimistic color update."""
        self.color = color
        self.color_temp_kelvin = None  # RGB mode
        self.source = "optimistic"

    def apply_optimistic_color_temp(self, kelvin: int) -> None:
        """Apply optimistic color temperature update."""
        self.color_temp_kelvin = kelvin
        self.color = None  # Color temp mode
        self.source = "optimistic"

    def apply_optimistic_scene(self, scene_id: str) -> None:
        """Apply optimistic scene activation."""
        self.active_scene = scene_id
        self.source = "optimistic"

    def apply_optimistic_diy_style(self, style: str, style_value: int | None = None) -> None:
        """Apply optimistic DIY style update.

        Args:
            style: Style name (Fade, Jumping, Flicker, Marquee, Music).
            style_value: Style numeric value (0-4). If None, will be looked up.
        """
        self.diy_style = style
        self.diy_style_value = style_value
        self.source = "optimistic"

    def apply_optimistic_music_mode(self, enabled: bool) -> None:
        """Apply optimistic music mode update."""
        self.music_mode_enabled = enabled
        self.source = "optimistic"

    def apply_optimistic_oscillation(self, oscillating: bool) -> None:
        """Apply optimistic oscillation update (fans)."""
        self.oscillating = oscillating
        self.source = "optimistic"

    def apply_optimistic_work_mode(self, work_mode: int, mode_value: int) -> None:
        """Apply optimistic work mode update (fans)."""
        self.work_mode = work_mode
        self.mode_value = mode_value
        self.source = "optimistic"

    def apply_optimistic_hdmi_source(self, source: int) -> None:
        """Apply optimistic HDMI source update."""
        self.hdmi_source = source
        self.source = "optimistic"

    def apply_optimistic_scene_speed(self, speed: int) -> None:
        """Apply optimistic scene speed update."""
        self.scene_speed = speed
        self.source = "optimistic"

    @classmethod
    def create_empty(cls, device_id: str) -> GoveeDeviceState:
        """Create empty state for a device."""
        return cls(device_id=device_id)
