"""Test Govee data models."""
from __future__ import annotations

import pytest

from custom_components.govee.models import (
    GoveeDevice,
    GoveeDeviceState,
    DeviceCapability,
    CapabilityParameter,
)


# ==============================================================================
# CapabilityParameter Tests
# ==============================================================================


class TestCapabilityParameter:
    """Test CapabilityParameter model."""

    def test_create_integer_parameter(self):
        """Test creating an INTEGER parameter."""
        param = CapabilityParameter(
            data_type="INTEGER",
            min_value=0,
            max_value=100,
            precision=1,
        )

        assert param.data_type == "INTEGER"
        assert param.min_value == 0
        assert param.max_value == 100
        assert param.precision == 1

    def test_create_enum_parameter(self):
        """Test creating an ENUM parameter."""
        options = [
            {"name": "Option 1", "value": 1},
            {"name": "Option 2", "value": 2},
        ]
        param = CapabilityParameter(
            data_type="ENUM",
            options=options,
        )

        assert param.data_type == "ENUM"
        assert param.options == options
        assert len(param.options) == 2

    def test_create_struct_parameter(self):
        """Test creating a STRUCT parameter."""
        fields = [
            {"fieldName": "r", "type": "INTEGER"},
            {"fieldName": "g", "type": "INTEGER"},
            {"fieldName": "b", "type": "INTEGER"},
        ]
        param = CapabilityParameter(
            data_type="STRUCT",
            fields=fields,
        )

        assert param.data_type == "STRUCT"
        assert param.fields == fields
        assert len(param.fields) == 3


# ==============================================================================
# DeviceCapability Tests
# ==============================================================================


class TestDeviceCapability:
    """Test DeviceCapability model."""

    def test_create_capability_without_parameters(self):
        """Test creating a simple capability without parameters."""
        cap = DeviceCapability(
            type="devices.capabilities.on_off",
            instance="powerSwitch",
            parameters=None,
        )

        assert cap.type == "devices.capabilities.on_off"
        assert cap.instance == "powerSwitch"
        assert cap.parameters is None

    def test_create_capability_with_parameters(self):
        """Test creating a capability with parameters."""
        param = CapabilityParameter(
            data_type="INTEGER",
            min_value=0,
            max_value=100,
        )
        cap = DeviceCapability(
            type="devices.capabilities.range",
            instance="brightness",
            parameters=param,
        )

        assert cap.type == "devices.capabilities.range"
        assert cap.instance == "brightness"
        assert cap.parameters is not None
        assert cap.parameters.min_value == 0
        assert cap.parameters.max_value == 100

    def test_from_api_simple_capability(self):
        """Test creating capability from API response without parameters."""
        api_data = {
            "type": "devices.capabilities.on_off",
            "instance": "powerSwitch",
        }

        cap = DeviceCapability.from_api(api_data)

        assert cap.type == "devices.capabilities.on_off"
        assert cap.instance == "powerSwitch"
        assert cap.parameters is None

    def test_from_api_capability_with_range(self):
        """Test creating capability from API response with range parameters."""
        api_data = {
            "type": "devices.capabilities.range",
            "instance": "brightness",
            "parameters": {
                "dataType": "INTEGER",
                "range": {"min": 0, "max": 100, "precision": 1},
            },
        }

        cap = DeviceCapability.from_api(api_data)

        assert cap.type == "devices.capabilities.range"
        assert cap.instance == "brightness"
        assert cap.parameters is not None
        assert cap.parameters.data_type == "INTEGER"
        assert cap.parameters.min_value == 0
        assert cap.parameters.max_value == 100
        assert cap.parameters.precision == 1

    def test_from_api_capability_with_options(self):
        """Test creating capability from API response with options."""
        api_data = {
            "type": "devices.capabilities.dynamic_scene",
            "instance": "lightScene",
            "parameters": {
                "dataType": "ENUM",
                "options": [
                    {"name": "Sunrise", "value": 1},
                    {"name": "Sunset", "value": 2},
                ],
            },
        }

        cap = DeviceCapability.from_api(api_data)

        assert cap.type == "devices.capabilities.dynamic_scene"
        assert cap.parameters is not None
        assert cap.parameters.data_type == "ENUM"
        assert len(cap.parameters.options) == 2

    @property
    def min_value(self) -> int | None:
        """Get minimum value for range capabilities."""
        if self.parameters and self.parameters.min_value is not None:
            return self.parameters.min_value
        return None

    @property
    def max_value(self) -> int | None:
        """Get maximum value for range capabilities."""
        if self.parameters and self.parameters.max_value is not None:
            return self.parameters.max_value
        return None


# ==============================================================================
# GoveeDevice Tests
# ==============================================================================


class TestGoveeDevice:
    """Test GoveeDevice model."""

    def test_create_basic_device(self):
        """Test creating a basic device."""
        device = GoveeDevice(
            device_id="AA:BB:CC:DD:EE:FF:11:22",
            sku="H6160",
            device_name="Test Light",
            device_type="devices.types.light",
            capabilities=[],
        )

        assert device.device_id == "AA:BB:CC:DD:EE:FF:11:22"
        assert device.sku == "H6160"
        assert device.device_name == "Test Light"
        assert device.device_type == "devices.types.light"
        assert len(device.capabilities) == 0
        assert device.firmware_version is None

    def test_create_device_with_firmware(self):
        """Test creating a device with firmware version."""
        device = GoveeDevice(
            device_id="AA:BB:CC:DD:EE:FF:11:22",
            sku="H6160",
            device_name="Test Light",
            device_type="devices.types.light",
            capabilities=[],
            firmware_version="1.02.03",
        )

        assert device.firmware_version == "1.02.03"

    def test_from_api_basic(self, mock_api_device_response):
        """Test creating device from API response."""
        device = GoveeDevice.from_api(mock_api_device_response)

        assert device.device_id == "AA:BB:CC:DD:EE:FF:11:22"
        assert device.sku == "H6160"
        assert device.device_name == "Bedroom Strip"
        assert device.device_type == "devices.types.light"
        assert device.firmware_version == "1.02.03"
        assert len(device.capabilities) == 3

    def test_from_api_without_firmware(self):
        """Test creating device from API without firmware version."""
        api_data = {
            "device": "AA:BB:CC:DD:EE:FF:11:22",
            "sku": "H6160",
            "deviceName": "Test Light",
            "type": "devices.types.light",
            "capabilities": [],
        }

        device = GoveeDevice.from_api(api_data)

        assert device.firmware_version is None

    def test_has_capability_true(self, mock_device_light):
        """Test has_capability returns True when capability exists."""
        assert mock_device_light.has_capability("devices.capabilities.on_off")
        assert mock_device_light.has_capability(
            "devices.capabilities.on_off", "powerSwitch"
        )

    def test_has_capability_false(self, mock_device_light):
        """Test has_capability returns False when capability doesn't exist."""
        assert not mock_device_light.has_capability(
            "devices.capabilities.nonexistent"
        )

    def test_has_capability_wrong_instance(self, mock_device_light):
        """Test has_capability with wrong instance returns False."""
        assert not mock_device_light.has_capability(
            "devices.capabilities.on_off", "wrongInstance"
        )

    def test_get_capability_success(self, mock_device_light):
        """Test get_capability returns capability when it exists."""
        cap = mock_device_light.get_capability("devices.capabilities.on_off")
        assert cap is not None
        assert cap.type == "devices.capabilities.on_off"

    def test_get_capability_with_instance(self, mock_device_light):
        """Test get_capability with instance specified."""
        cap = mock_device_light.get_capability(
            "devices.capabilities.on_off", "powerSwitch"
        )
        assert cap is not None
        assert cap.instance == "powerSwitch"

    def test_get_capability_not_found(self, mock_device_light):
        """Test get_capability returns None when not found."""
        cap = mock_device_light.get_capability("devices.capabilities.nonexistent")
        assert cap is None

    def test_get_capability_by_instance(self, mock_device_light):
        """Test get_capability_by_instance."""
        cap = mock_device_light.get_capability_by_instance("brightness")
        assert cap is not None
        assert cap.instance == "brightness"

    def test_get_capability_by_instance_not_found(self, mock_device_light):
        """Test get_capability_by_instance returns None when not found."""
        cap = mock_device_light.get_capability_by_instance("nonexistent")
        assert cap is None

    # Feature Detection Tests

    def test_supports_on_off_true(self, mock_device_light):
        """Test supports_on_off returns True for light with power capability."""
        assert mock_device_light.supports_on_off is True

    def test_supports_on_off_false(self):
        """Test supports_on_off returns False without power capability."""
        device = GoveeDevice(
            device_id="TEST",
            sku="TEST",
            device_name="Test",
            device_type="devices.types.light",
            capabilities=[],
        )
        assert device.supports_on_off is False

    def test_supports_brightness_true(self, mock_device_light):
        """Test supports_brightness returns True for dimmable light."""
        assert mock_device_light.supports_brightness is True

    def test_supports_brightness_false(self, mock_device_switch):
        """Test supports_brightness returns False for switch."""
        assert mock_device_switch.supports_brightness is False

    def test_supports_color_true(self, mock_device_light):
        """Test supports_color returns True for RGB light."""
        assert mock_device_light.supports_color is True

    def test_supports_color_false(self, mock_device_brightness_only):
        """Test supports_color returns False for non-RGB light."""
        assert mock_device_brightness_only.supports_color is False

    def test_supports_color_temp_true(self, mock_device_light):
        """Test supports_color_temp returns True for CCT light."""
        assert mock_device_light.supports_color_temp is True

    def test_supports_color_temp_false(self, mock_device_brightness_only):
        """Test supports_color_temp returns False for non-CCT light."""
        assert mock_device_brightness_only.supports_color_temp is False

    def test_supports_scenes_true(self, mock_device_light_with_scenes):
        """Test supports_scenes returns True when scenes available."""
        assert mock_device_light_with_scenes.supports_scenes is True

    def test_supports_scenes_false(self, mock_device_light):
        """Test supports_scenes returns False without scene capability."""
        assert mock_device_light.supports_scenes is False

    def test_supports_segments_false(self, mock_device_light):
        """Test supports_segments returns False for non-RGBIC device."""
        assert mock_device_light.supports_segments is False

    def test_supports_music_mode_false(self, mock_device_light):
        """Test supports_music_mode returns False without music capability."""
        assert mock_device_light.supports_music_mode is False

    def test_supports_nightlight_false(self, mock_device_light):
        """Test supports_nightlight returns False without nightlight capability."""
        assert mock_device_light.supports_nightlight is False

    # Range Helper Tests

    def test_get_brightness_range_custom(self, mock_device_light):
        """Test get_brightness_range returns capability range."""
        min_val, max_val = mock_device_light.get_brightness_range()
        assert min_val == 0
        assert max_val == 100

    def test_get_brightness_range_default(self, mock_device_switch):
        """Test get_brightness_range returns default when no capability."""
        min_val, max_val = mock_device_switch.get_brightness_range()
        assert min_val == 0
        assert max_val == 100

    def test_get_color_temp_range_custom(self, mock_device_light):
        """Test get_color_temp_range returns capability range."""
        min_val, max_val = mock_device_light.get_color_temp_range()
        assert min_val == 2000
        assert max_val == 9000

    def test_get_color_temp_range_default(self, mock_device_brightness_only):
        """Test get_color_temp_range returns default when no capability."""
        min_val, max_val = mock_device_brightness_only.get_color_temp_range()
        assert min_val == 2000
        assert max_val == 9000

    def test_get_scene_options(self, mock_device_light_with_scenes):
        """Test get_scene_options returns scene list."""
        options = mock_device_light_with_scenes.get_scene_options()
        assert len(options) == 4
        assert options[0]["name"] == "Sunrise"

    def test_get_scene_options_empty(self, mock_device_light):
        """Test get_scene_options returns empty list without scenes."""
        options = mock_device_light.get_scene_options()
        assert options == []

    def test_get_segment_count_zero(self, mock_device_light):
        """Test get_segment_count returns 0 for non-RGBIC device."""
        count = mock_device_light.get_segment_count()
        assert count == 0


# ==============================================================================
# GoveeDeviceState Tests
# ==============================================================================


class TestGoveeDeviceState:
    """Test GoveeDeviceState model."""

    def test_create_basic_state(self):
        """Test creating a basic device state."""
        state = GoveeDeviceState(
            online=True,
            power_state=True,
            brightness=100,
        )

        assert state.online is True
        assert state.power_state is True
        assert state.brightness == 100
        assert state.color_rgb is None
        assert state.color_temp is None

    def test_create_state_with_color(self):
        """Test creating state with RGB color."""
        state = GoveeDeviceState(
            online=True,
            power_state=True,
            brightness=75,
            color_rgb=(255, 128, 64),
        )

        assert state.color_rgb == (255, 128, 64)
        assert state.color_temp is None

    def test_create_state_with_color_temp(self):
        """Test creating state with color temperature."""
        state = GoveeDeviceState(
            online=True,
            power_state=True,
            brightness=50,
            color_temp=4000,
        )

        assert state.color_temp == 4000
        assert state.color_rgb is None

    def test_create_offline_state(self):
        """Test creating offline device state."""
        state = GoveeDeviceState(
            online=False,
            power_state=False,
            brightness=0,
        )

        assert state.online is False
        assert state.power_state is False

    def test_state_with_scene(self):
        """Test state with scene code."""
        state = GoveeDeviceState(
            online=True,
            power_state=True,
            brightness=100,
            scene_code=5,
        )

        assert state.scene_code == 5

    def test_from_api_response_power_on(self):
        """Test creating state from API response (power on)."""
        api_data = {
            "online": True,
            "powerState": "on",
            "brightness": 100,
            "color": {"r": 255, "g": 128, "b": 64},
        }

        state = GoveeDeviceState.from_api(api_data)

        assert state.online is True
        assert state.power_state is True
        assert state.brightness == 100
        assert state.color_rgb == (255, 128, 64)

    def test_from_api_response_power_off(self):
        """Test creating state from API response (power off)."""
        api_data = {
            "online": True,
            "powerState": "off",
        }

        state = GoveeDeviceState.from_api(api_data)

        assert state.online is True
        assert state.power_state is False

    def test_from_api_response_offline(self):
        """Test creating state from API response (offline)."""
        api_data = {
            "online": False,
        }

        state = GoveeDeviceState.from_api(api_data)

        assert state.online is False
        assert state.power_state is False

    def test_from_api_response_color_temp(self):
        """Test creating state from API with color temperature."""
        api_data = {
            "online": True,
            "powerState": "on",
            "brightness": 75,
            "colorTemperatureK": 4000,
        }

        state = GoveeDeviceState.from_api(api_data)

        assert state.color_temp == 4000
        assert state.color_rgb is None

    def test_from_api_missing_fields(self):
        """Test creating state from minimal API response."""
        api_data = {"online": True}

        state = GoveeDeviceState.from_api(api_data)

        assert state.online is True
        assert state.power_state is False  # Default when missing
        assert state.brightness is None
        assert state.color_rgb is None
