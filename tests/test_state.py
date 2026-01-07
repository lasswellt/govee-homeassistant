"""Test GoveeDeviceState model."""
from __future__ import annotations

import time
import pytest

from custom_components.govee.models.state import GoveeDeviceState
from custom_components.govee.api.const import (
    INSTANCE_BRIGHTNESS,
    INSTANCE_COLOR_RGB,
    INSTANCE_COLOR_TEMP,
    INSTANCE_DIY_SCENE,
    INSTANCE_LIGHT_SCENE,
    INSTANCE_NIGHTLIGHT_TOGGLE,
    INSTANCE_OSCILLATION_TOGGLE,
    INSTANCE_THERMOSTAT_TOGGLE,
    INSTANCE_GRADIENT_TOGGLE,
    INSTANCE_WARM_MIST_TOGGLE,
    INSTANCE_AIR_DEFLECTOR_TOGGLE,
    INSTANCE_POWER_SWITCH,
    INSTANCE_SNAPSHOT,
)


class TestGoveeDeviceStateFromApi:
    """Test GoveeDeviceState.from_api factory method."""

    def test_from_api_basic_state(self):
        """Test basic state parsing from API."""
        data = {
            "capabilities": [
                {"instance": "online", "state": {"value": True}},
                {"instance": "powerSwitch", "state": {"value": 1}},
                {"instance": "brightness", "state": {"value": 75}},
            ]
        }

        state = GoveeDeviceState.from_api("device_123", data)

        assert state.device_id == "device_123"
        assert state.online is True
        assert state.power_state is True
        assert state.brightness == 75

    def test_from_api_color_rgb(self):
        """Test RGB color parsing from API."""
        # Red: 255 << 16 = 16711680
        data = {
            "capabilities": [
                {"instance": "colorRgb", "state": {"value": 16711680}},
            ]
        }

        state = GoveeDeviceState.from_api("device_123", data)

        assert state.color_rgb == (255, 0, 0)

    def test_from_api_color_temp(self):
        """Test color temperature parsing from API."""
        data = {
            "capabilities": [
                {"instance": "colorTemperatureK", "state": {"value": 5000}},
            ]
        }

        state = GoveeDeviceState.from_api("device_123", data)

        assert state.color_temp_kelvin == 5000

    def test_from_api_nightlight_toggle(self):
        """Test nightlight toggle parsing."""
        data = {
            "capabilities": [
                {"instance": INSTANCE_NIGHTLIGHT_TOGGLE, "state": {"value": 1}},
            ]
        }

        state = GoveeDeviceState.from_api("device_123", data)

        assert state.nightlight_on is True

    def test_from_api_oscillation_toggle(self):
        """Test oscillation toggle parsing."""
        data = {
            "capabilities": [
                {"instance": INSTANCE_OSCILLATION_TOGGLE, "state": {"value": 1}},
            ]
        }

        state = GoveeDeviceState.from_api("device_123", data)

        assert state.oscillation_on is True

    def test_from_api_thermostat_toggle(self):
        """Test thermostat toggle parsing."""
        data = {
            "capabilities": [
                {"instance": INSTANCE_THERMOSTAT_TOGGLE, "state": {"value": 1}},
            ]
        }

        state = GoveeDeviceState.from_api("device_123", data)

        assert state.thermostat_on is True

    def test_from_api_gradient_toggle(self):
        """Test gradient toggle parsing."""
        data = {
            "capabilities": [
                {"instance": INSTANCE_GRADIENT_TOGGLE, "state": {"value": 1}},
            ]
        }

        state = GoveeDeviceState.from_api("device_123", data)

        assert state.gradient_on is True

    def test_from_api_warm_mist_toggle(self):
        """Test warm mist toggle parsing."""
        data = {
            "capabilities": [
                {"instance": INSTANCE_WARM_MIST_TOGGLE, "state": {"value": 1}},
            ]
        }

        state = GoveeDeviceState.from_api("device_123", data)

        assert state.warm_mist_on is True

    def test_from_api_air_deflector_toggle(self):
        """Test air deflector toggle parsing."""
        data = {
            "capabilities": [
                {"instance": INSTANCE_AIR_DEFLECTOR_TOGGLE, "state": {"value": 1}},
            ]
        }

        state = GoveeDeviceState.from_api("device_123", data)

        assert state.air_deflector_on is True


class TestGoveeDeviceStateFromState:
    """Test GoveeDeviceState.from_state deep copy."""

    def test_from_state_copies_all_fields(self):
        """Test from_state creates independent copy."""
        original = GoveeDeviceState(
            device_id="device_123",
            online=True,
            power_state=True,
            brightness=75,
            color_rgb=(255, 0, 0),
            color_temp_kelvin=5000,
            current_scene="scene_1",
            current_scene_name="Sunset",
            scene_set_time=1234567890.0,
            segment_colors={0: (255, 0, 0), 1: (0, 255, 0)},
            segment_brightness={0: 100, 1: 50},
            nightlight_on=True,
            oscillation_on=False,
            thermostat_on=True,
            gradient_on=False,
            warm_mist_on=True,
            air_deflector_on=False,
            humidity=50,
            temperature=22.5,
            fan_speed=3,
            mode="auto",
            mqtt_events={"event1": {"value": 1}},
        )

        copy = GoveeDeviceState.from_state(original)

        # All fields should be equal
        assert copy.device_id == original.device_id
        assert copy.online == original.online
        assert copy.power_state == original.power_state
        assert copy.brightness == original.brightness
        assert copy.color_rgb == original.color_rgb
        assert copy.color_temp_kelvin == original.color_temp_kelvin
        assert copy.current_scene == original.current_scene
        assert copy.current_scene_name == original.current_scene_name
        assert copy.scene_set_time == original.scene_set_time
        assert copy.nightlight_on == original.nightlight_on
        assert copy.oscillation_on == original.oscillation_on
        assert copy.thermostat_on == original.thermostat_on
        assert copy.gradient_on == original.gradient_on
        assert copy.warm_mist_on == original.warm_mist_on
        assert copy.air_deflector_on == original.air_deflector_on
        assert copy.humidity == original.humidity
        assert copy.temperature == original.temperature
        assert copy.fan_speed == original.fan_speed
        assert copy.mode == original.mode

        # Dicts should be independent copies
        assert copy.segment_colors == original.segment_colors
        assert copy.segment_colors is not original.segment_colors
        assert copy.segment_brightness == original.segment_brightness
        assert copy.segment_brightness is not original.segment_brightness
        assert copy.mqtt_events == original.mqtt_events
        assert copy.mqtt_events is not original.mqtt_events

    def test_from_state_handles_none_dicts(self):
        """Test from_state handles None dictionaries."""
        original = GoveeDeviceState(
            device_id="device_123",
            segment_colors=None,
            segment_brightness=None,
            mqtt_events=None,
        )

        copy = GoveeDeviceState.from_state(original)

        assert copy.segment_colors is None
        assert copy.segment_brightness is None
        assert copy.mqtt_events is None


class TestGoveeDeviceStateUpdateFromApi:
    """Test GoveeDeviceState.update_from_api method."""

    def test_update_from_api_updates_oscillation(self):
        """Test update_from_api updates oscillation state."""
        state = GoveeDeviceState(device_id="device_123", oscillation_on=False)

        state.update_from_api({
            "capabilities": [
                {"instance": INSTANCE_OSCILLATION_TOGGLE, "state": {"value": 1}},
            ]
        })

        assert state.oscillation_on is True

    def test_update_from_api_updates_thermostat(self):
        """Test update_from_api updates thermostat state."""
        state = GoveeDeviceState(device_id="device_123", thermostat_on=False)

        state.update_from_api({
            "capabilities": [
                {"instance": INSTANCE_THERMOSTAT_TOGGLE, "state": {"value": 1}},
            ]
        })

        assert state.thermostat_on is True

    def test_update_from_api_updates_gradient(self):
        """Test update_from_api updates gradient state."""
        state = GoveeDeviceState(device_id="device_123", gradient_on=False)

        state.update_from_api({
            "capabilities": [
                {"instance": INSTANCE_GRADIENT_TOGGLE, "state": {"value": 1}},
            ]
        })

        assert state.gradient_on is True

    def test_update_from_api_updates_warm_mist(self):
        """Test update_from_api updates warm mist state."""
        state = GoveeDeviceState(device_id="device_123", warm_mist_on=False)

        state.update_from_api({
            "capabilities": [
                {"instance": INSTANCE_WARM_MIST_TOGGLE, "state": {"value": 1}},
            ]
        })

        assert state.warm_mist_on is True

    def test_update_from_api_updates_air_deflector(self):
        """Test update_from_api updates air deflector state."""
        state = GoveeDeviceState(device_id="device_123", air_deflector_on=False)

        state.update_from_api({
            "capabilities": [
                {"instance": INSTANCE_AIR_DEFLECTOR_TOGGLE, "state": {"value": 1}},
            ]
        })

        assert state.air_deflector_on is True


class TestGoveeDeviceStateApplyOptimisticUpdate:
    """Test GoveeDeviceState.apply_optimistic_update method."""

    def test_apply_optimistic_update_snapshot_dict(self):
        """Test snapshot scene optimistic update with dict value."""
        state = GoveeDeviceState(device_id="device_123")

        state.apply_optimistic_update(INSTANCE_SNAPSHOT, {
            "id": "snap_123",
            "name": "My Snapshot",
        })

        assert state.current_scene == "snapshot_snap_123"
        assert state.current_scene_name == "My Snapshot"
        assert state.scene_set_time is not None

    def test_apply_optimistic_update_snapshot_dict_with_param_id(self):
        """Test snapshot scene optimistic update with paramId."""
        state = GoveeDeviceState(device_id="device_123")

        state.apply_optimistic_update(INSTANCE_SNAPSHOT, {
            "paramId": "param_456",
        })

        assert state.current_scene == "snapshot_param_456"

    def test_apply_optimistic_update_snapshot_primitive(self):
        """Test snapshot scene optimistic update with primitive value."""
        state = GoveeDeviceState(device_id="device_123")

        state.apply_optimistic_update(INSTANCE_SNAPSHOT, "simple_snapshot")

        assert state.current_scene == "snapshot_simple_snapshot"
        assert state.current_scene_name is None

    def test_apply_optimistic_update_oscillation(self):
        """Test oscillation toggle optimistic update."""
        state = GoveeDeviceState(device_id="device_123", oscillation_on=False)

        state.apply_optimistic_update(INSTANCE_OSCILLATION_TOGGLE, 1)

        assert state.oscillation_on is True

    def test_apply_optimistic_update_thermostat(self):
        """Test thermostat toggle optimistic update."""
        state = GoveeDeviceState(device_id="device_123", thermostat_on=False)

        state.apply_optimistic_update(INSTANCE_THERMOSTAT_TOGGLE, 1)

        assert state.thermostat_on is True

    def test_apply_optimistic_update_gradient(self):
        """Test gradient toggle optimistic update."""
        state = GoveeDeviceState(device_id="device_123", gradient_on=False)

        state.apply_optimistic_update(INSTANCE_GRADIENT_TOGGLE, 1)

        assert state.gradient_on is True

    def test_apply_optimistic_update_warm_mist(self):
        """Test warm mist toggle optimistic update."""
        state = GoveeDeviceState(device_id="device_123", warm_mist_on=False)

        state.apply_optimistic_update(INSTANCE_WARM_MIST_TOGGLE, 1)

        assert state.warm_mist_on is True

    def test_apply_optimistic_update_air_deflector(self):
        """Test air deflector toggle optimistic update."""
        state = GoveeDeviceState(device_id="device_123", air_deflector_on=False)

        state.apply_optimistic_update(INSTANCE_AIR_DEFLECTOR_TOGGLE, 1)

        assert state.air_deflector_on is True

    def test_apply_optimistic_update_nightlight(self):
        """Test nightlight toggle optimistic update."""
        state = GoveeDeviceState(device_id="device_123", nightlight_on=False)

        state.apply_optimistic_update(INSTANCE_NIGHTLIGHT_TOGGLE, 1)

        assert state.nightlight_on is True


class TestGoveeDeviceStateApplyMqttEvent:
    """Test GoveeDeviceState.apply_mqtt_event method."""

    def test_apply_mqtt_event_creates_dict(self):
        """Test apply_mqtt_event creates mqtt_events dict if None."""
        state = GoveeDeviceState(device_id="device_123", mqtt_events=None)

        state.apply_mqtt_event("lackWaterEvent", {
            "name": "lack",
            "value": 1,
            "message": "Lack of Water",
        })

        assert state.mqtt_events is not None
        assert "lackWaterEvent" in state.mqtt_events
        assert state.mqtt_events["lackWaterEvent"]["name"] == "lack"
        assert state.mqtt_events["lackWaterEvent"]["value"] == 1
        assert state.mqtt_events["lackWaterEvent"]["message"] == "Lack of Water"
        assert "timestamp" in state.mqtt_events["lackWaterEvent"]

    def test_apply_mqtt_event_updates_existing(self):
        """Test apply_mqtt_event updates existing event."""
        state = GoveeDeviceState(
            device_id="device_123",
            mqtt_events={"existingEvent": {"name": "old", "value": 0}},
        )

        state.apply_mqtt_event("existingEvent", {
            "name": "new",
            "value": 1,
        })

        assert state.mqtt_events["existingEvent"]["name"] == "new"
        assert state.mqtt_events["existingEvent"]["value"] == 1

    def test_apply_mqtt_event_adds_timestamp(self):
        """Test apply_mqtt_event adds timestamp."""
        state = GoveeDeviceState(device_id="device_123")
        before_time = time.time()

        state.apply_mqtt_event("testEvent", {"name": "test", "value": 1})

        after_time = time.time()
        timestamp = state.mqtt_events["testEvent"]["timestamp"]
        assert before_time <= timestamp <= after_time


class TestGoveeDeviceStateSegmentMethods:
    """Test segment-related methods."""

    def test_apply_segment_update_creates_dict(self):
        """Test apply_segment_update creates segment_colors dict if None."""
        state = GoveeDeviceState(device_id="device_123", segment_colors=None)

        state.apply_segment_update(0, (255, 0, 0))

        assert state.segment_colors is not None
        assert state.segment_colors[0] == (255, 0, 0)

    def test_apply_segment_brightness_update_creates_dict(self):
        """Test apply_segment_brightness_update creates dict if None."""
        state = GoveeDeviceState(device_id="device_123", segment_brightness=None)

        state.apply_segment_brightness_update(0, 75)

        assert state.segment_brightness is not None
        assert state.segment_brightness[0] == 75

    def test_clear_segment_states(self):
        """Test clear_segment_states clears both dicts."""
        state = GoveeDeviceState(
            device_id="device_123",
            segment_colors={0: (255, 0, 0)},
            segment_brightness={0: 100},
        )

        state.clear_segment_states()

        assert state.segment_colors is None
        assert state.segment_brightness is None
