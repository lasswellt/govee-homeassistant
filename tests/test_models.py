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
    create_dreamview_command,
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
    INSTANCE_DREAMVIEW,
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
        cap = GoveeCapability(
            type=CAPABILITY_ON_OFF, instance=INSTANCE_POWER, parameters={}
        )
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
        cap = GoveeCapability(
            type=CAPABILITY_COLOR_SETTING, instance=INSTANCE_COLOR_RGB, parameters={}
        )
        assert cap.is_color_rgb is True
        assert cap.is_color_temp is False

    def test_is_color_temp(self):
        """Test color temperature capability detection."""
        cap = GoveeCapability(
            type=CAPABILITY_COLOR_SETTING, instance=INSTANCE_COLOR_TEMP, parameters={}
        )
        assert cap.is_color_temp is True
        assert cap.is_color_rgb is False

    def test_is_scene(self):
        """Test scene capability detection."""
        cap = GoveeCapability(
            type=CAPABILITY_DYNAMIC_SCENE, instance=INSTANCE_SCENE, parameters={}
        )
        assert cap.is_scene is True

    def test_is_oscillation(self):
        """Test oscillation capability detection (fans)."""
        cap = GoveeCapability(
            type=CAPABILITY_TOGGLE, instance=INSTANCE_OSCILLATION, parameters={}
        )
        assert cap.is_oscillation is True
        assert cap.is_toggle is True
        assert cap.is_night_light is False

    def test_is_work_mode(self):
        """Test work mode capability detection (fans)."""
        cap = GoveeCapability(
            type=CAPABILITY_WORK_MODE, instance=INSTANCE_WORK_MODE, parameters={}
        )
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

    def test_is_dreamview(self):
        """Test DreamView toggle capability detection."""
        cap = GoveeCapability(
            type=CAPABILITY_TOGGLE,
            instance=INSTANCE_DREAMVIEW,
            parameters={
                "dataType": "ENUM",
                "options": [{"name": "on", "value": 1}, {"name": "off", "value": 0}],
            },
        )
        assert cap.is_dreamview is True
        assert cap.is_toggle is True
        assert cap.is_night_light is False
        assert cap.is_oscillation is False

    def test_immutable(self):
        """Test that GoveeCapability is immutable (frozen)."""
        cap = GoveeCapability(
            type=CAPABILITY_ON_OFF, instance=INSTANCE_POWER, parameters={}
        )
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

    def test_plain_plug_not_light_device(self, mock_plug_device):
        """A plain plug (on/off only) must NOT be a light device (guards #54)."""
        assert mock_plug_device.is_plug is True
        assert mock_plug_device.supports_rgb is False
        assert mock_plug_device.is_light_device is False

    def test_socket_with_color_nightlight_is_light_device(self):
        """An outlet extender with an RGB nightlight (H5089) IS a light (#59).

        The #54 appliance filter excluded all sockets, removing the H5089's
        color light entity. A socket with color capability must be a light.
        """
        device = GoveeDevice(
            device_id="03:9C:DC:06:75:4B:10:7C",
            sku="H5089",
            name="Smart Outlet Extender",
            device_type="devices.types.socket",
            capabilities=(
                GoveeCapability(
                    type=CAPABILITY_ON_OFF, instance=INSTANCE_POWER, parameters={}
                ),
                GoveeCapability(
                    type=CAPABILITY_COLOR_SETTING,
                    instance=INSTANCE_COLOR_RGB,
                    parameters={},
                ),
            ),
            is_group=False,
        )
        assert device.is_plug is True
        assert device.supports_rgb is True
        assert device.is_light_device is True

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

    def test_air_purifier_is_purifier(self, mock_air_purifier_device):
        """H7126 (devices.types.air_purifier) must match is_purifier."""
        assert mock_air_purifier_device.is_purifier is True

    def test_air_purifier_is_fan(self, mock_air_purifier_device):
        """Air purifiers should also match is_fan so a fan entity gets created.

        Air purifiers expose the same workMode/gearMode capability shape as
        fans, so they are represented in Home Assistant via the fan platform.
        """
        assert mock_air_purifier_device.is_fan is True

    def test_air_purifier_not_light(self, mock_air_purifier_device):
        """Air purifiers must not be detected as light devices."""
        assert mock_air_purifier_device.is_light_device is False
        assert mock_air_purifier_device.is_plug is False

    def test_air_purifier_mode_options(self, mock_air_purifier_device):
        """get_purifier_mode_options should extract nested gearMode options."""
        options = mock_air_purifier_device.get_purifier_mode_options()
        names = [o.get("name") for o in options]
        assert "Sleep" in names
        assert "Low" in names
        assert "High" in names

    def test_fan_is_not_purifier(self, mock_fan_device):
        """A plain fan (devices.types.fan) must not match is_purifier."""
        assert mock_fan_device.is_purifier is False

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

    def test_supports_dreamview(self, mock_dreamview_device):
        """Test DreamView support detection."""
        assert mock_dreamview_device.supports_dreamview is True

    def test_no_dreamview_support(self, mock_light_device):
        """Test that regular lights don't have DreamView support."""
        assert mock_light_device.supports_dreamview is False

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

    def test_get_fan_speed_options_named(self, mock_fan_device):
        """Test get_fan_speed_options with named sub-options (3-speed)."""
        options = mock_fan_device.get_fan_speed_options()
        gear_options = [o for o in options if o["work_mode"] == 1]
        assert len(gear_options) == 3
        assert gear_options[0] == {"name": "Low", "work_mode": 1, "mode_value": 1}
        assert gear_options[1] == {"name": "Medium", "work_mode": 1, "mode_value": 2}
        assert gear_options[2] == {"name": "High", "work_mode": 1, "mode_value": 3}

    def test_get_fan_speed_options_unnamed(self, mock_fan_8speed_device):
        """Test get_fan_speed_options with unnamed sub-options (H7101 8-speed)."""
        options = mock_fan_8speed_device.get_fan_speed_options()
        gear_options = [o for o in options if o["work_mode"] == 1]
        assert len(gear_options) == 8
        for i in range(1, 9):
            assert gear_options[i - 1] == {
                "name": f"Speed {i}",
                "work_mode": 1,
                "mode_value": i,
            }

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

    def test_api_empty_string_int_fields_do_not_crash_parse(self):
        """#83 (mattdengler): the Govee cloud returns "" for a capability value
        when the device is offline. An unguarded int("") raised ValueError and
        failed the WHOLE device-state fetch, so the device stopped updating.
        Empty-string brightness/hdmiSource must be tolerated, and later valid
        capabilities in the same response must still parse."""
        state = GoveeDeviceState.create_empty("test_id")
        api_response = {
            "capabilities": [
                {
                    "type": "devices.capabilities.range",
                    "instance": "brightness",
                    "state": {"value": ""},
                },
                {
                    "type": "devices.capabilities.mode",
                    "instance": "hdmiSource",
                    "state": {"value": ""},
                },
                {
                    "type": "devices.capabilities.on_off",
                    "instance": "powerSwitch",
                    "state": {"value": 1},
                },
            ],
        }
        # Must not raise.
        state.update_from_api(api_response)
        assert state.brightness == 100  # default, not a crash
        assert state.hdmi_source is None
        assert state.power_state is True  # later capability still parsed

    def test_mqtt_empty_brightness_keeps_previous(self):
        """Empty-string MQTT brightness must not crash; keep prior value."""
        state = GoveeDeviceState.create_empty("test_id")
        state.brightness = 60
        state.update_from_mqtt({"onOff": 1, "brightness": ""})
        assert state.brightness == 60

    def test_api_color_temp_zero_becomes_none(self):
        """Test that API colorTemperatureK=0 is treated as None (no color temp set).

        The Govee API returns 0 when the device is in RGB mode. Passing 0 through
        causes a ZeroDivisionError in HomeKit Bridge (1000000 / 0).
        """
        state = GoveeDeviceState.create_empty("test_id")
        api_response = {
            "capabilities": [
                {
                    "type": "devices.capabilities.color_setting",
                    "instance": "colorTemperatureK",
                    "state": {"value": 0},
                },
            ],
        }
        state.update_from_api(api_response)
        assert state.color_temp_kelvin is None

    def test_api_color_temp_valid_preserved(self):
        """Test that valid API colorTemperatureK values are preserved."""
        state = GoveeDeviceState.create_empty("test_id")
        api_response = {
            "capabilities": [
                {
                    "type": "devices.capabilities.color_setting",
                    "instance": "colorTemperatureK",
                    "state": {"value": 4000},
                },
            ],
        }
        state.update_from_api(api_response)
        assert state.color_temp_kelvin == 4000

    def test_mqtt_color_temp_zero_becomes_none(self):
        """Test that MQTT colorTemInKelvin=0 is treated as None."""
        state = GoveeDeviceState.create_empty("test_id")
        state.update_from_mqtt({"colorTemInKelvin": 0})
        assert state.color_temp_kelvin is None

    def test_mqtt_color_temp_valid_preserved(self):
        """Test that valid MQTT colorTemInKelvin values are preserved."""
        state = GoveeDeviceState.create_empty("test_id")
        state.update_from_mqtt({"colorTemInKelvin": 4000})
        assert state.color_temp_kelvin == 4000

    def test_mqtt_push_restores_online(self):
        """Regression for issue #68 — MQTT push must reset online=True.

        When a device is power-cycled, the Govee cloud caches `online: false`
        long after the device returns. Receiving any MQTT push from the device
        is direct proof of life, so update_from_mqtt should flip online back
        to True so HA stops showing the entity as unavailable.
        """
        state = GoveeDeviceState.create_empty("test_id")
        state.online = False  # simulate stale cloud "offline" state

        state.update_from_mqtt({"onOff": 1, "brightness": 80})

        assert state.online is True
        assert state.power_state is True
        assert state.brightness == 80

    def test_mqtt_empty_push_still_restores_online(self):
        """Even an MQTT push without recognised state fields proves the
        device is alive — issue #68 recovery should not require a specific
        capability to be present."""
        state = GoveeDeviceState.create_empty("test_id")
        state.online = False

        state.update_from_mqtt({})

        assert state.online is True

    def test_mqtt_sensor_temperature_and_humidity_applied(self):
        """#83: AWS IoT pushes for thermometers (H5179, H5110, H5075 via H5151)
        carry temperature/humidity, but update_from_mqtt previously dropped
        them so the entity froze on its first REST read. The flat sensor keys
        must now feed sensor_temperature/sensor_humidity."""
        state = GoveeDeviceState.create_empty("test_id")
        state.update_from_mqtt({"sensorTemperature": 22.4, "sensorHumidity": 48.0})
        assert state.sensor_temperature == 22.4
        assert state.sensor_humidity == 48.0

    def test_mqtt_sensor_short_key_aliases(self):
        """Accept the short ``tem``/``hum`` spellings seen on some SKUs."""
        state = GoveeDeviceState.create_empty("test_id")
        state.update_from_mqtt({"tem": 19, "hum": 55})
        assert state.sensor_temperature == 19.0
        assert state.sensor_humidity == 55.0

    def test_mqtt_sensor_absent_does_not_clobber_existing(self):
        """A light push (no sensor fields) must not wipe a known reading."""
        state = GoveeDeviceState.create_empty("test_id")
        state.sensor_temperature = 21.5
        state.sensor_humidity = 47.0
        state.update_from_mqtt({"onOff": 1, "brightness": 80, "value": 999})
        assert state.sensor_temperature == 21.5
        assert state.sensor_humidity == 47.0

    def test_api_sensor_temperature_from_struct(self):
        """REST may wrap the reading in a STRUCT under various keys."""
        state = GoveeDeviceState.create_empty("test_id")
        api_response = {
            "capabilities": [
                {
                    "type": "devices.capabilities.property",
                    "instance": "sensorTemperature",
                    "state": {"value": {"currentTemperature": 23.4}},
                },
                {
                    "type": "devices.capabilities.property",
                    "instance": "sensorHumidity",
                    "state": {"value": 50},
                },
            ],
        }
        state.update_from_api(api_response)
        assert state.sensor_temperature == 23.4
        assert state.sensor_humidity == 50.0

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

    def test_optimistic_dreamview(self):
        """Test optimistic DreamView update."""
        state = GoveeDeviceState.create_empty("test_id")
        state.apply_optimistic_dreamview(True)
        assert state.dreamview_enabled is True
        assert state.source == "optimistic"

    def test_optimistic_dreamview_off(self):
        """Test optimistic DreamView off does not clear other modes."""
        state = GoveeDeviceState.create_empty("test_id")
        state.music_mode_enabled = True
        state.active_scene = "123"
        state.apply_optimistic_dreamview(False)
        assert state.dreamview_enabled is False
        # Other modes should NOT be cleared when turning off
        assert state.music_mode_enabled is True
        assert state.active_scene == "123"

    def test_dreamview_clears_music_mode(self):
        """Test enabling DreamView clears music mode (mutual exclusion)."""
        state = GoveeDeviceState.create_empty("test_id")
        state.music_mode_enabled = True
        state.music_mode_value = 5
        state.music_mode_name = "Spectrum"
        state.apply_optimistic_dreamview(True)
        assert state.dreamview_enabled is True
        assert state.music_mode_enabled is False
        assert state.music_mode_value is None
        assert state.music_mode_name is None

    def test_dreamview_clears_scene(self):
        """Test enabling DreamView clears active scene (mutual exclusion)."""
        state = GoveeDeviceState.create_empty("test_id")
        state.active_scene = "123"
        state.active_diy_scene = "456"
        state.apply_optimistic_dreamview(True)
        assert state.dreamview_enabled is True
        assert state.active_scene is None
        assert state.active_diy_scene is None

    def test_music_mode_clears_dreamview(self):
        """Test enabling music mode clears DreamView (mutual exclusion)."""
        state = GoveeDeviceState.create_empty("test_id")
        state.dreamview_enabled = True
        state.apply_optimistic_music_mode(True)
        assert state.music_mode_enabled is True
        assert state.dreamview_enabled is False

    def test_music_mode_clears_scene(self):
        """Test enabling music mode clears scene (mutual exclusion)."""
        state = GoveeDeviceState.create_empty("test_id")
        state.active_scene = "123"
        state.active_diy_scene = "456"
        state.apply_optimistic_music_mode(True)
        assert state.music_mode_enabled is True
        assert state.active_scene is None
        assert state.active_diy_scene is None

    def test_music_mode_struct_clears_dreamview(self):
        """Test enabling STRUCT music mode clears DreamView (mutual exclusion)."""
        state = GoveeDeviceState.create_empty("test_id")
        state.dreamview_enabled = True
        state.active_scene = "123"
        state.apply_optimistic_music_mode_struct(5, 75, "Spectrum")
        assert state.music_mode_enabled is True
        assert state.music_mode_value == 5
        assert state.dreamview_enabled is False
        assert state.active_scene is None

    def test_scene_clears_dreamview_and_music(self):
        """Test selecting scene clears DreamView and music mode (mutual exclusion)."""
        state = GoveeDeviceState.create_empty("test_id")
        state.dreamview_enabled = True
        state.music_mode_enabled = True
        state.music_mode_value = 5
        state.apply_optimistic_scene("123")
        assert state.active_scene == "123"
        assert state.dreamview_enabled is False
        assert state.music_mode_enabled is False
        assert state.music_mode_value is None

    def test_scene_clears_diy_scene(self):
        """Test selecting regular scene clears DIY scene."""
        state = GoveeDeviceState.create_empty("test_id")
        state.active_diy_scene = "456"
        state.apply_optimistic_scene("123")
        assert state.active_scene == "123"
        assert state.active_diy_scene is None

    def test_diy_scene_clears_regular_scene(self):
        """Test selecting DIY scene clears regular scene."""
        state = GoveeDeviceState.create_empty("test_id")
        state.active_scene = "123"
        state.apply_optimistic_diy_scene("456")
        assert state.active_diy_scene == "456"
        assert state.active_scene is None

    def test_diy_scene_clears_dreamview_and_music(self):
        """Test selecting DIY scene clears DreamView and music mode."""
        state = GoveeDeviceState.create_empty("test_id")
        state.dreamview_enabled = True
        state.music_mode_enabled = True
        state.apply_optimistic_diy_scene("456")
        assert state.active_diy_scene == "456"
        assert state.dreamview_enabled is False
        assert state.music_mode_enabled is False

    def test_active_scene_name_set_with_scene(self):
        """Test active_scene_name is set alongside active_scene."""
        state = GoveeDeviceState.create_empty("test_id")
        state.apply_optimistic_scene("123", "Sunrise")
        assert state.active_scene == "123"
        assert state.active_scene_name == "Sunrise"

    def test_active_scene_name_preserved_on_power_off(self):
        """Test active_scene_name is preserved when turning off."""
        state = GoveeDeviceState.create_empty("test_id")
        state.apply_optimistic_scene("123", "Sunrise")
        state.apply_optimistic_power(False)
        assert state.active_scene == "123"
        assert state.active_scene_name == "Sunrise"

    def test_active_scene_name_cleared_on_color_change(self):
        """Test active_scene_name is cleared when setting RGB color."""
        state = GoveeDeviceState.create_empty("test_id")
        state.apply_optimistic_scene("123", "Sunrise")
        state.apply_optimistic_color(RGBColor(255, 0, 0))
        assert state.active_scene is None
        assert state.active_scene_name is None

    def test_active_scene_name_cleared_on_color_temp_change(self):
        """Test active_scene_name is cleared when setting color temp."""
        state = GoveeDeviceState.create_empty("test_id")
        state.apply_optimistic_scene("123", "Sunrise")
        state.apply_optimistic_color_temp(4000)
        assert state.active_scene is None
        assert state.active_scene_name is None

    def test_scene_clears_color_and_color_temp(self):
        """Test activating scene clears stale color and color temp.

        Scenes run dynamic patterns so the previous RGB/color-temp is
        misleading.  Clearing lets the light card show on + brightness only.
        """
        state = GoveeDeviceState.create_empty("test_id")
        state.color = RGBColor(255, 0, 0)
        state.color_temp_kelvin = 4000
        state.apply_optimistic_scene("123", "Sunrise")
        assert state.active_scene == "123"
        assert state.color is None
        assert state.color_temp_kelvin is None

    def test_active_scene_name_not_cleared_by_brightness(self):
        """Test brightness change does NOT clear scene state."""
        state = GoveeDeviceState.create_empty("test_id")
        state.apply_optimistic_scene("123", "Sunrise")
        state.apply_optimistic_brightness(50)
        assert state.active_scene == "123"
        assert state.active_scene_name == "Sunrise"

    def test_active_scene_name_cleared_by_music_mode(self):
        """Test music mode clears active_scene_name (mutual exclusion)."""
        state = GoveeDeviceState.create_empty("test_id")
        state.apply_optimistic_scene("123", "Sunrise")
        state.apply_optimistic_music_mode(True)
        assert state.active_scene is None
        assert state.active_scene_name is None

    def test_active_scene_name_cleared_by_music_mode_struct(self):
        """Test STRUCT music mode clears active_scene_name (mutual exclusion)."""
        state = GoveeDeviceState.create_empty("test_id")
        state.apply_optimistic_scene("123", "Sunrise")
        state.apply_optimistic_music_mode_struct(5, 75, "Spectrum")
        assert state.active_scene is None
        assert state.active_scene_name is None

    def test_active_scene_name_cleared_by_dreamview(self):
        """Test DreamView clears active_scene_name (mutual exclusion)."""
        state = GoveeDeviceState.create_empty("test_id")
        state.apply_optimistic_scene("123", "Sunrise")
        state.apply_optimistic_dreamview(True)
        assert state.active_scene is None
        assert state.active_scene_name is None

    def test_active_scene_name_cleared_by_diy_scene(self):
        """Test DIY scene clears active_scene_name (mutual exclusion)."""
        state = GoveeDeviceState.create_empty("test_id")
        state.apply_optimistic_scene("123", "Sunrise")
        state.apply_optimistic_diy_scene("456")
        assert state.active_scene is None
        assert state.active_scene_name is None

    def test_scene_saves_last_color(self):
        """Test activating a scene saves the current color for later restore."""
        state = GoveeDeviceState.create_empty("test_id")
        state.color = RGBColor(255, 0, 0)
        state.apply_optimistic_scene("123", "Sunrise")
        assert state.last_color == RGBColor(255, 0, 0)
        assert state.last_color_temp_kelvin is None
        assert state.color is None

    def test_scene_saves_last_color_temp(self):
        """Test activating a scene saves the current color_temp for later restore."""
        state = GoveeDeviceState.create_empty("test_id")
        state.color_temp_kelvin = 4000
        state.apply_optimistic_scene("123", "Sunrise")
        assert state.last_color_temp_kelvin == 4000
        assert state.last_color is None
        assert state.color_temp_kelvin is None

    def test_scene_chain_preserves_first_color(self):
        """Test scene A → scene B doesn't overwrite the saved color from before A."""
        state = GoveeDeviceState.create_empty("test_id")
        state.color = RGBColor(0, 255, 0)
        state.apply_optimistic_scene("1", "Scene A")
        assert state.last_color == RGBColor(0, 255, 0)
        # Scene B: color is now None, so last_color should NOT be overwritten
        state.apply_optimistic_scene("2", "Scene B")
        assert state.last_color == RGBColor(0, 255, 0)

    def test_diy_scene_saves_last_color(self):
        """Test activating a DIY scene saves the current color for later restore."""
        state = GoveeDeviceState.create_empty("test_id")
        state.color = RGBColor(0, 0, 255)
        state.apply_optimistic_diy_scene("456")
        assert state.last_color == RGBColor(0, 0, 255)
        assert state.last_color_temp_kelvin is None

    def test_diy_scene_saves_last_color_temp(self):
        """Test activating a DIY scene saves the current color_temp."""
        state = GoveeDeviceState.create_empty("test_id")
        state.color_temp_kelvin = 5000
        state.apply_optimistic_diy_scene("456")
        assert state.last_color_temp_kelvin == 5000
        assert state.last_color is None

    def test_diy_scene_chain_preserves_first_color(self):
        """Test DIY scene A → DIY scene B preserves original saved color."""
        state = GoveeDeviceState.create_empty("test_id")
        state.color = RGBColor(128, 128, 0)
        state.apply_optimistic_diy_scene("1")
        assert state.last_color == RGBColor(128, 128, 0)
        state.apply_optimistic_diy_scene("2")
        assert state.last_color == RGBColor(128, 128, 0)

    def test_scene_does_not_save_black(self):
        """Test that RGBColor(0,0,0) is not saved as last_color.

        The API returns colorRgb=0 when a scene is running, which is not a
        meaningful color to restore.
        """
        state = GoveeDeviceState.create_empty("test_id")
        state.color = RGBColor(0, 0, 0)
        state.apply_optimistic_scene("123", "Sunrise")
        assert state.last_color is None

    def test_diy_scene_does_not_save_black(self):
        """Test that RGBColor(0,0,0) is not saved as last_color for DIY scenes."""
        state = GoveeDeviceState.create_empty("test_id")
        state.color = RGBColor(0, 0, 0)
        state.apply_optimistic_diy_scene("456")
        assert state.last_color is None


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

    def test_dreamview_command_on(self):
        """Test create_dreamview_command for turning DreamView on."""
        cmd = create_dreamview_command(enabled=True)
        assert cmd.toggle_instance == "dreamViewToggle"
        assert cmd.enabled is True
        assert cmd.get_value() == 1
        payload = cmd.to_api_payload()
        assert payload["type"] == "devices.capabilities.toggle"
        assert payload["instance"] == "dreamViewToggle"
        assert payload["value"] == 1

    def test_dreamview_command_off(self):
        """Test create_dreamview_command for turning DreamView off."""
        cmd = create_dreamview_command(enabled=False)
        assert cmd.enabled is False
        assert cmd.get_value() == 0
        payload = cmd.to_api_payload()
        assert payload["value"] == 0
