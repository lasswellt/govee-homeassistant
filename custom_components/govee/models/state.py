from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from ..api.const import (
    INSTANCE_AIR_DEFLECTOR_TOGGLE,
    INSTANCE_BRIGHTNESS,
    INSTANCE_COLOR_RGB,
    INSTANCE_COLOR_TEMP,
    INSTANCE_DIY_SCENE,
    INSTANCE_GRADIENT_TOGGLE,
    INSTANCE_LIGHT_SCENE,
    INSTANCE_NIGHTLIGHT_TOGGLE,
    INSTANCE_OSCILLATION_TOGGLE,
    INSTANCE_POWER_SWITCH,
    INSTANCE_SNAPSHOT,
    INSTANCE_THERMOSTAT_TOGGLE,
    INSTANCE_WARM_MIST_TOGGLE,
)


@dataclass
class GoveeDeviceState:
    """Tracks device state from API with support for optimistic updates."""

    device_id: str
    online: bool = True
    power_state: bool | None = None
    brightness: int | None = None
    color_rgb: tuple[int, int, int] | None = None
    color_temp_kelvin: int | None = None

    # Scene tracking (optimistic - can't query from API)
    current_scene: str | None = None
    current_scene_name: str | None = None
    scene_set_time: float | None = None

    segment_colors: dict[int, tuple[int, int, int]] | None = None
    segment_brightness: dict[int, int] | None = None

    nightlight_on: bool | None = None
    oscillation_on: bool | None = None
    thermostat_on: bool | None = None
    gradient_on: bool | None = None
    warm_mist_on: bool | None = None
    air_deflector_on: bool | None = None

    humidity: int | None = None
    temperature: float | None = None
    fan_speed: int | None = None
    mode: str | None = None

    # MQTT event data storage (for sensor devices with event capabilities)
    mqtt_events: dict[str, dict[str, Any]] | None = None

    @classmethod
    def from_api(cls, device_id: str, data: dict[str, Any]) -> GoveeDeviceState:
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
            elif instance == INSTANCE_OSCILLATION_TOGGLE:
                state.oscillation_on = value == 1
            elif instance == INSTANCE_THERMOSTAT_TOGGLE:
                state.thermostat_on = value == 1
            elif instance == INSTANCE_GRADIENT_TOGGLE:
                state.gradient_on = value == 1
            elif instance == INSTANCE_WARM_MIST_TOGGLE:
                state.warm_mist_on = value == 1
            elif instance == INSTANCE_AIR_DEFLECTOR_TOGGLE:
                state.air_deflector_on = value == 1
            elif instance == "online":
                state.online = value

        return state

    @classmethod
    def from_state(cls, other: GoveeDeviceState) -> GoveeDeviceState:
        """Create a deep copy for rollback purposes.

        Creates an independent copy of all state fields, including
        deep copies of mutable containers (dicts).
        """
        return cls(
            device_id=other.device_id,
            online=other.online,
            power_state=other.power_state,
            brightness=other.brightness,
            color_rgb=other.color_rgb,
            color_temp_kelvin=other.color_temp_kelvin,
            current_scene=other.current_scene,
            current_scene_name=other.current_scene_name,
            scene_set_time=other.scene_set_time,
            segment_colors=dict(other.segment_colors) if other.segment_colors else None,
            segment_brightness=dict(other.segment_brightness) if other.segment_brightness else None,
            nightlight_on=other.nightlight_on,
            oscillation_on=other.oscillation_on,
            thermostat_on=other.thermostat_on,
            gradient_on=other.gradient_on,
            warm_mist_on=other.warm_mist_on,
            air_deflector_on=other.air_deflector_on,
            humidity=other.humidity,
            temperature=other.temperature,
            fan_speed=other.fan_speed,
            mode=other.mode,
            mqtt_events=dict(other.mqtt_events) if other.mqtt_events else None,
        )

    def update_from_api(self, data: dict[str, Any]) -> None:
        """Only updates fields present in response, preserving optimistic state."""
        new_state = GoveeDeviceState.from_api(self.device_id, data)

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
        if new_state.oscillation_on is not None:
            self.oscillation_on = new_state.oscillation_on
        if new_state.thermostat_on is not None:
            self.thermostat_on = new_state.thermostat_on
        if new_state.gradient_on is not None:
            self.gradient_on = new_state.gradient_on
        if new_state.warm_mist_on is not None:
            self.warm_mist_on = new_state.warm_mist_on
        if new_state.air_deflector_on is not None:
            self.air_deflector_on = new_state.air_deflector_on
        self.online = new_state.online

    def apply_optimistic_update(
        self,
        instance: str,
        value: Any,
    ) -> None:
        """Update local state immediately without waiting for API poll."""
        if instance == INSTANCE_POWER_SWITCH:
            self.power_state = value == 1
        elif instance == INSTANCE_BRIGHTNESS:
            self.brightness = value
            self._clear_scene_state()
        elif instance == INSTANCE_COLOR_RGB:
            if isinstance(value, int):
                r = (value >> 16) & 0xFF
                g = (value >> 8) & 0xFF
                b = value & 0xFF
                self.color_rgb = (r, g, b)
            self._clear_scene_state()
        elif instance == INSTANCE_COLOR_TEMP:
            self.color_temp_kelvin = value
            self._clear_scene_state()
        elif instance == INSTANCE_LIGHT_SCENE:
            if isinstance(value, dict):
                scene_id = value.get("id") or value.get("paramId") or str(value)
                self.current_scene = str(scene_id)
                self.current_scene_name = value.get("name")
            else:
                self.current_scene = str(value)
                self.current_scene_name = None
            self.scene_set_time = time.time()
        elif instance == INSTANCE_DIY_SCENE:
            self.current_scene = f"diy_{value}"
            self.current_scene_name = None
            self.scene_set_time = time.time()
        elif instance == INSTANCE_SNAPSHOT:
            if isinstance(value, dict):
                scene_id = value.get("id") or value.get("paramId") or str(value)
                self.current_scene = f"snapshot_{scene_id}"
                self.current_scene_name = value.get("name")
            else:
                self.current_scene = f"snapshot_{value}"
                self.current_scene_name = None
            self.scene_set_time = time.time()
        elif instance == INSTANCE_NIGHTLIGHT_TOGGLE:
            self.nightlight_on = value == 1
        elif instance == INSTANCE_OSCILLATION_TOGGLE:
            self.oscillation_on = value == 1
        elif instance == INSTANCE_THERMOSTAT_TOGGLE:
            self.thermostat_on = value == 1
        elif instance == INSTANCE_GRADIENT_TOGGLE:
            self.gradient_on = value == 1
        elif instance == INSTANCE_WARM_MIST_TOGGLE:
            self.warm_mist_on = value == 1
        elif instance == INSTANCE_AIR_DEFLECTOR_TOGGLE:
            self.air_deflector_on = value == 1

    def _clear_scene_state(self) -> None:
        self.current_scene = None
        self.current_scene_name = None
        self.scene_set_time = None

    def apply_segment_update(
        self,
        segment_index: int,
        rgb: tuple[int, int, int],
    ) -> None:
        if self.segment_colors is None:
            self.segment_colors = {}
        self.segment_colors[segment_index] = rgb

    def apply_segment_brightness_update(
        self,
        segment_index: int,
        brightness: int,
    ) -> None:
        if self.segment_brightness is None:
            self.segment_brightness = {}
        self.segment_brightness[segment_index] = brightness

    def clear_segment_states(self) -> None:
        """Called when main light changes override individual segment colors."""
        self.segment_colors = None
        self.segment_brightness = None

    def apply_mqtt_event(self, instance: str, state_item: dict[str, Any]) -> None:
        """Apply state update from MQTT event.

        MQTT events contain sensor/alert data for devices with event capabilities.
        These are typically presence sensors, ice makers, dehumidifiers, etc.

        Args:
            instance: The capability instance (e.g., "lackWaterEvent", "bodyAppearedEvent")
            state_item: State item dict with "name", "value", and optionally "message"

        Example event state_item:
            {"name": "lack", "value": 1, "message": "Lack of Water"}
        """
        # MQTT events are primarily for sensor devices (presence, alerts, etc.)
        # The event structure is different from REST API state
        name = state_item.get("name")
        value = state_item.get("value")

        # Initialize mqtt_events dict if needed
        if self.mqtt_events is None:
            self.mqtt_events = {}

        # Store the event keyed by instance for entity access
        self.mqtt_events[instance] = {
            "name": name,
            "value": value,
            "message": state_item.get("message"),
            "timestamp": time.time(),
        }
