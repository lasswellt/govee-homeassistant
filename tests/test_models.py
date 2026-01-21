"""Test Govee data models."""

from __future__ import annotations

import pytest

from custom_components.govee.models import (
    GoveeDevice,
    GoveeDeviceState,
    GoveeCapability,
    RGBColor,
    PowerCommand,
    BrightnessCommand,
    ColorCommand,
    ColorTempCommand,
    SceneCommand,
    SegmentColorCommand,
    OscillationCommand,
    WorkModeCommand,
    ModeCommand,
)
from custom_components.govee.models.device import (
    CAPABILITY_ON_OFF,
    CAPABILITY_RANGE,
    CAPABILITY_COLOR_SETTING,
    CAPABILITY_DYNAMIC_SCENE,
    CAPABILITY_TOGGLE,
    CAPABILITY_WORK_MODE,
    CAPABILITY_MODE,
    INSTANCE_POWER,
    INSTANCE_BRIGHTNESS,
    INSTANCE_COLOR_RGB,
    INSTANCE_COLOR_TEMP,
    INSTANCE_SCENE,
    INSTANCE_OSCILLATION,
    INSTANCE_WORK_MODE,
    INSTANCE_HDMI_SOURCE,
)


# ==============================================================================
# RGBColor Tests
# ==============================================================================


class TestRGBColor:
    """Test RGBColor model."""

    def test_create_color(self):
        """Test creating an RGB color."""
        color = RGBColor(r=255, g=128, b=64)
        assert color.r == 255
        assert color.g == 128
        assert color.b == 64

    def test_color_clamping(self):
        """Test that color values are clamped to 0-255."""
        color = RGBColor(r=300, g=-10, b=128)
        assert color.r == 255
        assert color.g == 0
        assert color.b == 128

    def test_as_tuple(self):
        """Test getting color as tuple."""
        color = RGBColor(r=255, g=128, b=64)
        assert color.as_tuple == (255, 128, 64)

    def test_as_packed_int(self):
        """Test packing color as integer."""
        color = RGBColor(r=255, g=128, b=64)
        # (255 << 16) + (128 << 8) + 64 = 16744512
        assert color.as_packed_int == 16744512

    def test_from_packed_int(self):
        """Test creating color from packed integer."""
        color = RGBColor.from_packed_int(16744512)
        assert color.r == 255
        assert color.g == 128
        assert color.b == 64

    def test_from_dict(self):
        """Test creating color from dict."""
        color = RGBColor.from_dict({"r": 255, "g": 128, "b": 64})
        assert color.as_tuple == (255, 128, 64)

    def test_immutable(self):
        """Test that RGBColor is immutable (frozen)."""
        color = RGBColor(r=255, g=128, b=64)
        with pytest.raises(AttributeError):
            color.r = 100


# ==============================================================================
# GoveeCapability Tests
# ==============================================================================


class TestGoveeCapability:
    """Test GoveeCapability model."""

    def test_is_power(self):
        """Test power capability detection."""
        cap = GoveeCapability(type=CAPABILITY_ON_OFF, instance=INSTANCE_POWER, parameters={})
        assert cap.is_power is True
        assert cap.is_brightness is False

    def test_is_brightness(self):
        """Test brightness capability detection."""
        cap = GoveeCapability(
            type=CAPABILITY_RANGE,
            instance=INSTANCE_BRIGHTNESS,
            parameters={"range": {"min": 0, "max": 100}},
        )
        assert cap.is_brightness is True
        assert cap.brightness_range == (0, 100)

    def test_is_color_rgb(self):
        """Test RGB color capability detection."""
        cap = GoveeCapability(type=CAPABILITY_COLOR_SETTING, instance=INSTANCE_COLOR_RGB, parameters={})
        assert cap.is_color_rgb is True
        assert cap.is_color_temp is False

    def test_is_color_temp(self):
        """Test color temperature capability detection."""
        cap = GoveeCapability(type=CAPABILITY_COLOR_SETTING, instance=INSTANCE_COLOR_TEMP, parameters={})
        assert cap.is_color_temp is True
        assert cap.is_color_rgb is False

    def test_is_scene(self):
        """Test scene capability detection."""
        cap = GoveeCapability(type=CAPABILITY_DYNAMIC_SCENE, instance=INSTANCE_SCENE, parameters={})
        assert cap.is_scene is True

    def test_is_oscillation(self):
        """Test oscillation capability detection (fans)."""
        cap = GoveeCapability(type=CAPABILITY_TOGGLE, instance=INSTANCE_OSCILLATION, parameters={})
        assert cap.is_oscillation is True
        assert cap.is_toggle is True
        assert cap.is_night_light is False

    def test_is_work_mode(self):
        """Test work mode capability detection (fans)."""
        cap = GoveeCapability(type=CAPABILITY_WORK_MODE, instance=INSTANCE_WORK_MODE, parameters={})
        assert cap.is_work_mode is True

    def test_is_hdmi_source(self):
        """Test HDMI source mode capability detection."""
        cap = GoveeCapability(
            type=CAPABILITY_MODE,
            instance=INSTANCE_HDMI_SOURCE,
            parameters={
                "options": [
                    {"name": "HDMI 1", "value": 1},
                    {"name": "HDMI 2", "value": 2},
                ],
            },
        )
        assert cap.is_hdmi_source is True
        assert cap.is_work_mode is False

    def test_immutable(self):
        """Test that GoveeCapability is immutable (frozen)."""
        cap = GoveeCapability(type=CAPABILITY_ON_OFF, instance=INSTANCE_POWER, parameters={})
        with pytest.raises(AttributeError):
            cap.type = "other"


# ==============================================================================
# GoveeDevice Tests
# ==============================================================================


class TestGoveeDevice:
    """Test GoveeDevice model."""

    def test_create_device(self, light_capabilities):
        """Test creating a device."""
        device = GoveeDevice(
            device_id="AA:BB:CC:DD:EE:FF:00:11",
            sku="H6072",
            name="Living Room Light",
            device_type="devices.types.light",
            capabilities=light_capabilities,
            is_group=False,
        )
        assert device.device_id == "AA:BB:CC:DD:EE:FF:00:11"
        assert device.sku == "H6072"
        assert device.name == "Living Room Light"
        assert device.is_group is False

    def test_supports_power(self, mock_light_device):
        """Test power support detection."""
        assert mock_light_device.supports_power is True

    def test_supports_brightness(self, mock_light_device):
        """Test brightness support detection."""
        assert mock_light_device.supports_brightness is True

    def test_supports_rgb(self, mock_light_device):
        """Test RGB support detection."""
        assert mock_light_device.supports_rgb is True

    def test_supports_color_temp(self, mock_light_device):
        """Test color temperature support detection."""
        assert mock_light_device.supports_color_temp is True

    def test_supports_scenes(self, mock_light_device):
        """Test scene support detection."""
        assert mock_light_device.supports_scenes is True

    def test_supports_segments(self, mock_rgbic_device):
        """Test segment support detection."""
        assert mock_rgbic_device.supports_segments is True

    def test_is_plug(self, mock_plug_device):
        """Test plug detection."""
        assert mock_plug_device.is_plug is True

    def test_is_group(self, mock_group_device):
        """Test group device detection."""
        assert mock_group_device.is_group is True

    def test_is_fan(self, mock_fan_device):
        """Test fan device detection."""
        assert mock_fan_device.is_fan is True
        assert mock_fan_device.is_plug is False
        assert mock_fan_device.is_light_device is False

    def test_supports_oscillation(self, mock_fan_device):
        """Test oscillation support detection (fans)."""
        assert mock_fan_device.supports_oscillation is True

    def test_supports_work_mode(self, mock_fan_device):
        """Test work mode support detection (fans)."""
        assert mock_fan_device.supports_work_mode is True

    def test_fan_not_light(self, mock_fan_device):
        """Test that fan devices are not detected as lights."""
        assert mock_fan_device.is_light_device is False
        assert mock_fan_device.supports_power is True

    def test_supports_hdmi_source(self, mock_hdmi_device):
        """Test HDMI source support detection."""
        assert mock_hdmi_device.supports_hdmi_source is True

    def test_get_hdmi_source_options(self, mock_hdmi_device):
        """Test getting HDMI source options from device."""
        options = mock_hdmi_device.get_hdmi_source_options()
        assert len(options) == 4
        assert options[0]["name"] == "HDMI 1"
        assert options[0]["value"] == 1
        assert options[3]["name"] == "HDMI 4"
        assert options[3]["value"] == 4

    def test_get_hdmi_source_options_no_support(self, mock_light_device):
        """Test getting HDMI source options from device without HDMI support."""
        options = mock_light_device.get_hdmi_source_options()
        assert options == []

    def test_from_api_response(self, api_device_response):
        """Test creating device from API response."""
        device = GoveeDevice.from_api_response(api_device_response)
        assert device.device_id == "AA:BB:CC:DD:EE:FF:00:11"
        assert device.sku == "H6072"
        assert device.name == "Living Room Light"
        assert device.supports_power is True
        assert device.supports_brightness is True
        assert device.supports_rgb is True

    def test_from_api_response_fan(self, api_fan_device_response):
        """Test creating fan device from API response."""
        device = GoveeDevice.from_api_response(api_fan_device_response)
        assert device.device_id == "AA:BB:CC:DD:EE:FF:00:44"
        assert device.sku == "H7101"
        assert device.name == "Living Room Fan"
        assert device.is_fan is True
        assert device.is_light_device is False
        assert device.supports_power is True
        assert device.supports_oscillation is True
        assert device.supports_work_mode is True

    def test_immutable(self, mock_light_device):
        """Test that GoveeDevice is immutable (frozen)."""
        with pytest.raises(AttributeError):
            mock_light_device.name = "New Name"


# ==============================================================================
# GoveeDeviceState Tests
# ==============================================================================


class TestGoveeDeviceState:
    """Test GoveeDeviceState model."""

    def test_create_state(self):
        """Test creating a device state."""
        state = GoveeDeviceState(
            device_id="AA:BB:CC:DD:EE:FF:00:11",
            online=True,
            power_state=True,
            brightness=75,
        )
        assert state.device_id == "AA:BB:CC:DD:EE:FF:00:11"
        assert state.online is True
        assert state.power_state is True
        assert state.brightness == 75

    def test_create_empty(self):
        """Test creating empty state."""
        state = GoveeDeviceState.create_empty("test_id")
        assert state.device_id == "test_id"
        assert state.online is True
        assert state.power_state is False
        assert state.brightness == 100

    def test_update_from_api(self, api_state_response):
        """Test updating state from API response."""
        state = GoveeDeviceState.create_empty("AA:BB:CC:DD:EE:FF:00:11")
        state.update_from_api(api_state_response)
        assert state.online is True
        assert state.power_state is True
        assert state.brightness == 75
        assert state.color is not None
        assert state.color.as_tuple == (255, 128, 64)
        assert state.source == "api"

    def test_update_from_mqtt(self, mqtt_state_message):
        """Test updating state from MQTT message."""
        state = GoveeDeviceState.create_empty("AA:BB:CC:DD:EE:FF:00:11")
        state.update_from_mqtt(mqtt_state_message["state"])
        assert state.power_state is True
        assert state.brightness == 75
        assert state.color is not None
        assert state.color.as_tuple == (255, 128, 64)
        assert state.source == "mqtt"

    def test_optimistic_power(self):
        """Test optimistic power update."""
        state = GoveeDeviceState.create_empty("test_id")
        state.apply_optimistic_power(True)
        assert state.power_state is True
        assert state.source == "optimistic"

    def test_optimistic_brightness(self):
        """Test optimistic brightness update."""
        state = GoveeDeviceState.create_empty("test_id")
        state.apply_optimistic_brightness(50)
        assert state.brightness == 50
        assert state.source == "optimistic"

    def test_optimistic_color(self):
        """Test optimistic color update."""
        state = GoveeDeviceState.create_empty("test_id")
        color = RGBColor(r=255, g=0, b=0)
        state.apply_optimistic_color(color)
        assert state.color == color
        assert state.color_temp_kelvin is None  # Reset color temp
        assert state.source == "optimistic"

    def test_optimistic_color_temp(self):
        """Test optimistic color temperature update."""
        state = GoveeDeviceState.create_empty("test_id")
        state.apply_optimistic_color_temp(4000)
        assert state.color_temp_kelvin == 4000
        assert state.color is None  # Reset RGB
        assert state.source == "optimistic"

    def test_fan_state_fields(self):
        """Test fan-specific state fields."""
        state = GoveeDeviceState.create_empty("test_id")
        assert state.oscillating is None
        assert state.work_mode is None
        assert state.mode_value is None

    def test_update_fan_state_from_api(self, api_fan_state_response):
        """Test updating fan state from API response."""
        state = GoveeDeviceState.create_empty("AA:BB:CC:DD:EE:FF:00:44")
        state.update_from_api(api_fan_state_response)
        assert state.online is True
        assert state.power_state is True
        assert state.oscillating is True
        assert state.work_mode == 1
        assert state.mode_value == 2
        assert state.source == "api"

    def test_optimistic_oscillation(self):
        """Test optimistic oscillation update (fans)."""
        state = GoveeDeviceState.create_empty("test_id")
        state.apply_optimistic_oscillation(True)
        assert state.oscillating is True
        assert state.source == "optimistic"

        state.apply_optimistic_oscillation(False)
        assert state.oscillating is False

    def test_optimistic_work_mode(self):
        """Test optimistic work mode update (fans)."""
        state = GoveeDeviceState.create_empty("test_id")
        state.apply_optimistic_work_mode(work_mode=1, mode_value=3)
        assert state.work_mode == 1
        assert state.mode_value == 3
        assert state.source == "optimistic"

    def test_hdmi_source_state_field(self):
        """Test HDMI source state field."""
        state = GoveeDeviceState.create_empty("test_id")
        assert state.hdmi_source is None

    def test_update_hdmi_source_from_api(self):
        """Test updating HDMI source from API response."""
        state = GoveeDeviceState.create_empty("test_id")
        api_response = {
            "capabilities": [
                {
                    "type": "devices.capabilities.mode",
                    "instance": "hdmiSource",
                    "state": {"value": 2},
                },
            ],
        }
        state.update_from_api(api_response)
        assert state.hdmi_source == 2
        assert state.source == "api"

    def test_optimistic_hdmi_source(self):
        """Test optimistic HDMI source update."""
        state = GoveeDeviceState.create_empty("test_id")
        state.apply_optimistic_hdmi_source(3)
        assert state.hdmi_source == 3
        assert state.source == "optimistic"


# ==============================================================================
# Command Tests
# ==============================================================================


class TestCommands:
    """Test command models."""

    def test_power_command(self):
        """Test power command."""
        cmd = PowerCommand(power_on=True)
        assert cmd.power_on is True
        assert cmd.get_value() == 1
        payload = cmd.to_api_payload()
        assert payload["type"] == "devices.capabilities.on_off"
        assert payload["instance"] == "powerSwitch"
        assert payload["value"] == 1

    def test_power_command_off(self):
        """Test power off command."""
        cmd = PowerCommand(power_on=False)
        assert cmd.get_value() == 0

    def test_brightness_command(self):
        """Test brightness command."""
        cmd = BrightnessCommand(brightness=75)
        assert cmd.brightness == 75
        assert cmd.get_value() == 75

    def test_color_command(self):
        """Test color command."""
        color = RGBColor(r=255, g=128, b=64)
        cmd = ColorCommand(color=color)
        assert cmd.get_value() == 16744512  # Packed integer

    def test_color_temp_command(self):
        """Test color temperature command."""
        cmd = ColorTempCommand(kelvin=4000)
        assert cmd.kelvin == 4000
        assert cmd.get_value() == 4000

    def test_scene_command(self):
        """Test scene command."""
        cmd = SceneCommand(scene_id=123, scene_name="Sunrise")
        value = cmd.get_value()
        assert value["id"] == 123
        assert value["name"] == "Sunrise"

    def test_segment_color_command(self):
        """Test segment color command."""
        color = RGBColor(r=255, g=0, b=0)
        cmd = SegmentColorCommand(segment_indices=(0, 1, 2), color=color)
        value = cmd.get_value()
        assert value["segment"] == [0, 1, 2]
        assert value["rgb"] == 16711680  # Red

    def test_command_immutable(self):
        """Test that commands are immutable."""
        cmd = PowerCommand(power_on=True)
        with pytest.raises(AttributeError):
            cmd.power_on = False

    def test_oscillation_command_on(self):
        """Test oscillation command (on)."""
        cmd = OscillationCommand(oscillating=True)
        assert cmd.oscillating is True
        assert cmd.get_value() == 1
        payload = cmd.to_api_payload()
        assert payload["type"] == "devices.capabilities.toggle"
        assert payload["instance"] == "oscillationToggle"
        assert payload["value"] == 1

    def test_oscillation_command_off(self):
        """Test oscillation command (off)."""
        cmd = OscillationCommand(oscillating=False)
        assert cmd.get_value() == 0

    def test_work_mode_command_gear(self):
        """Test work mode command for manual gear mode."""
        cmd = WorkModeCommand(work_mode=1, mode_value=2)  # Medium speed
        assert cmd.work_mode == 1
        assert cmd.mode_value == 2
        payload = cmd.to_api_payload()
        assert payload["type"] == "devices.capabilities.work_mode"
        assert payload["instance"] == "workMode"
        assert payload["value"] == {"workMode": 1, "modeValue": 2}

    def test_work_mode_command_auto(self):
        """Test work mode command for auto mode."""
        cmd = WorkModeCommand(work_mode=3, mode_value=0)
        value = cmd.get_value()
        assert value["workMode"] == 3
        assert value["modeValue"] == 0

    def test_mode_command_hdmi_source(self):
        """Test mode command for HDMI source selection."""
        cmd = ModeCommand(mode_instance="hdmiSource", value=2)
        assert cmd.mode_instance == "hdmiSource"
        assert cmd.value == 2
        assert cmd.get_value() == 2
        payload = cmd.to_api_payload()
        assert payload["type"] == "devices.capabilities.mode"
        assert payload["instance"] == "hdmiSource"
        assert payload["value"] == 2

    def test_mode_command_immutable(self):
        """Test that ModeCommand is immutable."""
        cmd = ModeCommand(mode_instance="hdmiSource", value=1)
        with pytest.raises(AttributeError):
            cmd.value = 2
