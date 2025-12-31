"""Govee device state model with optimistic state tracking."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from ..api.const import (
    INSTANCE_BRIGHTNESS,
    INSTANCE_COLOR_RGB,
    INSTANCE_COLOR_TEMP,
    INSTANCE_DIY_SCENE,
    INSTANCE_LIGHT_SCENE,
    INSTANCE_NIGHTLIGHT_TOGGLE,
    INSTANCE_POWER_SWITCH,
)


@dataclass
class GoveeDeviceState:
    """Current state of a Govee device.

    Tracks device state from API with support for optimistic updates.
    Optimistic updates allow immediate UI feedback while waiting for
    cloud API confirmation.

    Attributes:
        device_id: Device identifier
        online: Whether device is online and reachable
        power_state: Power on (True) or off (False)
        brightness: Brightness level (0-100)
        color_rgb: Current RGB color as (r, g, b) tuple
        color_temp_kelvin: Current color temperature in Kelvin
        current_scene: Current scene identifier (optimistic tracking)
        current_scene_name: Current scene display name
        scene_set_time: Unix timestamp when scene was set (for staleness tracking)
        segment_colors: Per-segment RGB colors (for RGBIC devices)
        segment_brightness: Per-segment brightness values
        nightlight_on: Nightlight mode enabled
        humidity: Humidity percentage (for applicable devices)
        temperature: Temperature in Celsius
        fan_speed: Fan speed setting
        mode: Device operating mode
    """

    device_id: str
    online: bool = True
    power_state: bool | None = None
    brightness: int | None = None  # 0-100
    color_rgb: tuple[int, int, int] | None = None
    color_temp_kelvin: int | None = None

    # Scene tracking (optimistic - can't query from API)
    current_scene: str | None = None
    current_scene_name: str | None = None
    scene_set_time: float | None = None  # Unix timestamp when scene was set

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
        """Create from API state response.

        Args:
            device_id: Device identifier
            data: State payload from API

        Returns:
            GoveeDeviceState instance with parsed state
        """
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
        """Update state from API response.

        Only updates fields that are present in the response,
        preserving optimistic state for fields not reported by API.

        Args:
            data: State payload from API
        """
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
        Scene state is cleared when manual color/brightness changes are made.

        Args:
            instance: Capability instance being updated
            value: New value for the capability
        """
        if instance == INSTANCE_POWER_SWITCH:
            self.power_state = value == 1
        elif instance == INSTANCE_BRIGHTNESS:
            self.brightness = value
            # Clear scene when brightness changes (manual control invalidates scene)
            self._clear_scene_state()
        elif instance == INSTANCE_COLOR_RGB:
            if isinstance(value, int):
                r = (value >> 16) & 0xFF
                g = (value >> 8) & 0xFF
                b = value & 0xFF
                self.color_rgb = (r, g, b)
            # Clear scene when color changes (manual control invalidates scene)
            self._clear_scene_state()
        elif instance == INSTANCE_COLOR_TEMP:
            self.color_temp_kelvin = value
            # Clear scene when color temp changes (manual control invalidates scene)
            self._clear_scene_state()
        elif instance == INSTANCE_LIGHT_SCENE:
            if isinstance(value, dict):
                scene_id = value.get("id") or value.get("paramId") or str(value)
                self.current_scene = str(scene_id)
                # Store scene name if provided in the value
                self.current_scene_name = value.get("name")
            else:
                self.current_scene = str(value)
                self.current_scene_name = None
            self.scene_set_time = time.time()
        elif instance == INSTANCE_DIY_SCENE:
            self.current_scene = f"diy_{value}"
            self.current_scene_name = None
            self.scene_set_time = time.time()
        elif instance == INSTANCE_NIGHTLIGHT_TOGGLE:
            self.nightlight_on = value == 1

    def _clear_scene_state(self) -> None:
        """Clear scene tracking state.

        Called when manual color/brightness changes are made, which
        invalidate any previously set scene.
        """
        self.current_scene = None
        self.current_scene_name = None
        self.scene_set_time = None

    def apply_segment_update(
        self,
        segment_index: int,
        rgb: tuple[int, int, int],
    ) -> None:
        """Update segment color optimistically.

        Called when a segment color is changed to track the state locally.
        Segment state is optimistic since the API doesn't report per-segment state.

        Args:
            segment_index: Zero-based segment index
            rgb: RGB color tuple (0-255 per channel)
        """
        if self.segment_colors is None:
            self.segment_colors = {}
        self.segment_colors[segment_index] = rgb

    def apply_segment_brightness_update(
        self,
        segment_index: int,
        brightness: int,
    ) -> None:
        """Update segment brightness optimistically.

        Args:
            segment_index: Zero-based segment index
            brightness: Brightness level (0-100)
        """
        if self.segment_brightness is None:
            self.segment_brightness = {}
        self.segment_brightness[segment_index] = brightness

    def clear_segment_states(self) -> None:
        """Clear all segment state tracking.

        Called when the main light changes (scene, effect, color mode)
        which would override individual segment colors.
        """
        self.segment_colors = None
        self.segment_brightness = None
