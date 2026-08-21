"""Test Govee data models."""

from __future__ import annotations

import pytest

from custom_components.govee.models import (
    GoveeDevice,
    GoveeDeviceState,
    GoveeCapability,
    RGBColor,
    SegmentState,
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
    CAPABILITY_SEGMENT_COLOR,
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
from dataclasses import dataclass


@dataclass(frozen=True)
class FakeLanStatus:
    """Minimal LanDevStatus-like object for update_from_lan tests.

    Mirrors the four-field LAN data ceiling (onOff, device-native brightness,
    color, colorTemInKelvin) without importing the api layer.
    """

    on: bool | None = None
    brightness: int | None = None
    color: RGBColor | None = None
    color_temp_kelvin: int | None = None


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

    # --------------------------------------------------------------------------
    # update_from_lan — mode-aware partial overlay (Sprint 6, LAN issue #57)
    # --------------------------------------------------------------------------

    def test_lan_sets_source_and_online(self):
        """A devStatus reply is proof of life: source=lan, online=True."""
        state = GoveeDeviceState.create_empty("test_id")
        state.online = False  # simulate stale cloud "offline"
        state.source = "api"

        state.update_from_lan(FakeLanStatus(on=True))

        assert state.source == "lan"
        assert state.online is True

    def test_lan_writes_only_the_four_readable_fields(self):
        """Plain-mode LAN overlay updates only power/brightness/color/CT.

        Every other field — scenes, segments, sensors, heater, music STRUCT,
        HDMI, toggles — must be preserved by simply not being referenced.
        """
        state = GoveeDeviceState.create_empty("dev")
        # Non-targeted fields populated; none of them put us in an effect mode.
        state.segments = [SegmentState(index=0, color=RGBColor(1, 2, 3))]
        state.sensor_temperature = 21.5
        state.sensor_humidity = 47.0
        state.battery = 88
        state.heater_temperature = 25
        state.hdmi_source = 2
        state.music_mode_value = 4  # STRUCT value, not the enabled flag
        state.toggles = {"socketToggle1": True}
        state.active_snapshot = 7
        state.last_scene_id = "keep-me"

        state.update_from_lan(
            FakeLanStatus(on=True, brightness=50, color=RGBColor(10, 20, 30))
        )

        # The four readable fields were overlaid...
        assert state.power_state is True
        assert state.brightness == 50
        assert state.color == RGBColor(10, 20, 30)
        assert state.color_temp_kelvin is None  # nulled by mutual exclusion
        # ...and nothing else was touched.
        assert state.segments == [SegmentState(index=0, color=RGBColor(1, 2, 3))]
        assert state.sensor_temperature == 21.5
        assert state.sensor_humidity == 47.0
        assert state.battery == 88
        assert state.heater_temperature == 25
        assert state.hdmi_source == 2
        assert state.music_mode_value == 4
        assert state.toggles == {"socketToggle1": True}
        assert state.active_snapshot == 7
        assert state.last_scene_id == "keep-me"

    def test_lan_brightness_written_device_native(self):
        """Brightness is stored verbatim — no 0-255 conversion here."""
        state = GoveeDeviceState.create_empty("dev")
        state.update_from_lan(FakeLanStatus(brightness=37))
        assert state.brightness == 37

    def test_lan_none_fields_preserve_existing(self):
        """Absent (None) readable fields must not clobber known values."""
        state = GoveeDeviceState.create_empty("dev")
        state.power_state = True
        state.brightness = 64
        state.color = RGBColor(9, 9, 9)

        state.update_from_lan(FakeLanStatus())  # all four None

        assert state.power_state is True
        assert state.brightness == 64
        assert state.color == RGBColor(9, 9, 9)

    @pytest.mark.parametrize(
        "effect_field",
        ["active_scene", "active_diy_scene", "music_mode_enabled", "dreamview_enabled"],
    )
    def test_lan_effect_mode_skips_color_ct_and_brightness(self, effect_field):
        """During any effect, LAN must not write color, CT, or brightness.

        LAN reports the live per-frame RGB of a running scene, so adopting it
        would churn the color attribute every poll. Power still updates.
        """
        state = GoveeDeviceState.create_empty("dev")
        setattr(state, effect_field, True if "enabled" in effect_field else "scene-x")
        state.brightness = 100
        state.color = None
        state.color_temp_kelvin = None

        state.update_from_lan(
            FakeLanStatus(
                on=True,
                brightness=42,
                color=RGBColor(200, 100, 50),  # live scene frame
                color_temp_kelvin=3500,
            )
        )

        # Color/CT/brightness all skipped...
        assert state.color is None
        assert state.color_temp_kelvin is None
        assert state.brightness == 100
        # ...but power and the effect itself survive.
        assert state.power_state is True
        assert getattr(state, effect_field)

    def test_lan_zero_color_sentinel_preserves_existing_color(self):
        """An incoming {0,0,0} must not erase a real non-zero color."""
        state = GoveeDeviceState.create_empty("dev")
        state.color = RGBColor(255, 0, 0)

        state.update_from_lan(
            FakeLanStatus(color=RGBColor(0, 0, 0), color_temp_kelvin=0)
        )

        assert state.color == RGBColor(255, 0, 0)

    def test_lan_zero_color_sentinel_leaves_color_none(self):
        """{0,0,0} with no prior color leaves color as None (no fake black)."""
        state = GoveeDeviceState.create_empty("dev")
        assert state.color is None

        state.update_from_lan(FakeLanStatus(color=RGBColor(0, 0, 0)))

        assert state.color is None

    def test_lan_color_temp_positive_nulls_color(self):
        """colorTemInKelvin>0 adopts CT and clears RGB (mutual exclusion)."""
        state = GoveeDeviceState.create_empty("dev")
        state.color = RGBColor(255, 0, 0)

        state.update_from_lan(
            FakeLanStatus(color=RGBColor(10, 10, 10), color_temp_kelvin=4000)
        )

        assert state.color_temp_kelvin == 4000
        assert state.color is None

    def test_lan_nonzero_color_nulls_color_temp(self):
        """A non-zero color adopts RGB and clears CT (inverse exclusion)."""
        state = GoveeDeviceState.create_empty("dev")
        state.color_temp_kelvin = 4000
        state.color = None

        state.update_from_lan(
            FakeLanStatus(color=RGBColor(0, 128, 255), color_temp_kelvin=0)
        )

        assert state.color == RGBColor(0, 128, 255)
        assert state.color_temp_kelvin is None

    def test_lan_color_temp_zero_is_not_a_mode(self):
        """colorTemInKelvin==0 is treated as unset, not a CT mode switch."""
        state = GoveeDeviceState.create_empty("dev")
        state.color = RGBColor(1, 2, 3)

        state.update_from_lan(FakeLanStatus(color=None, color_temp_kelvin=0))

        # Neither branch fires: existing color is preserved, CT stays unset.
        assert state.color == RGBColor(1, 2, 3)
        assert state.color_temp_kelvin is None

    def test_lan_clears_optimistic_window_when_not_grace(self):
        """A normal read confirms state and ends the optimistic window."""
        state = GoveeDeviceState.create_empty("dev")
        state.apply_optimistic_brightness(50)
        assert state.last_optimistic_update is not None

        state.update_from_lan(FakeLanStatus(on=True, brightness=50))

        assert state.last_optimistic_update is None

    def test_lan_skip_power_brightness_preserves_grace_window(self):
        """skip_power_brightness keeps the optimistic window and value.

        During the grace window an in-flight optimistic write must not be
        overwritten by a racing LAN read, and the window must stay open.
        """
        state = GoveeDeviceState.create_empty("dev")
        state.apply_optimistic_brightness(50)
        state.apply_optimistic_power(False)
        opened_at = state.last_optimistic_update
        assert opened_at is not None

        state.update_from_lan(
            FakeLanStatus(on=True, brightness=99),
            skip_power_brightness=True,
        )

        # Power/brightness untouched, grace window intact.
        assert state.power_state is False
        assert state.brightness == 50
        assert state.last_optimistic_update == opened_at
        # Proof of life flips online, but source stays "optimistic" so the
        # grace window keeps gating subsequent LAN reads in the same window
        # (a "lan" stamp here would end grace and revert the next read).
        assert state.source == "optimistic"
        assert state.online is True

    def test_lan_skip_power_brightness_still_adopts_color(self):
        """The grace flag gates only power/brightness, not color/CT."""
        state = GoveeDeviceState.create_empty("dev")
        state.color = None

        state.update_from_lan(
            FakeLanStatus(color=RGBColor(5, 6, 7)),
            skip_power_brightness=True,
        )

        assert state.color == RGBColor(5, 6, 7)


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


# ==============================================================================
# TestSegmentCountOverride — H7075 SKU override + size.max clamp (REQ-001..004)
# ==============================================================================


def _make_rgbic_segment_capability(
    *,
    element_range_max: int | None = 14,
    size_max: int | None = None,
    segment_count: int | None = None,
) -> GoveeCapability:
    """Build an RGBIC ``segment_color_setting`` capability for tests.

    Models the fields/elementRange/size/segmentCount shapes that drive
    ``GoveeDevice.segment_count``. Only the fields the parser + the new
    property actually read are populated; the rest mirror the live API
    shape so the existing parser tests still apply.
    """
    fields: list[dict] = []
    if element_range_max is not None or size_max is not None:
        field: dict = {"fieldName": "segment"}
        if element_range_max is not None:
            field["elementRange"] = {"min": 0, "max": element_range_max}
        if size_max is not None:
            field["size"] = {"min": 1, "max": size_max}
        fields.append(field)
    params: dict = {"dataType": "STRUCT", "fields": fields}
    if segment_count is not None:
        params["segmentCount"] = segment_count
    return GoveeCapability(
        type=CAPABILITY_SEGMENT_COLOR,
        instance="segmentedColorRgb",
        parameters=params,
    )


def _make_rgbic_device(
    sku: str,
    capability: GoveeCapability,
    device_id: str = "AA:BB:CC:DD:EE:FF:00:99",
) -> GoveeDevice:
    """Build a minimal RGBIC GoveeDevice for segment_count tests."""
    return GoveeDevice(
        device_id=device_id,
        sku=sku,
        name=f"Test {sku}",
        device_type="devices.types.light",
        capabilities=(capability,),
        is_group=False,
    )


class TestSegmentCountOverride:
    """Test GoveeDevice.segment_count with SKU_SEGMENT_OVERRIDES + size.max clamp.

    The H7075 LED strip reports elementRange.max=14 (so 15 by the legacy
    parser) but only has 3 physical sections, and exposes size.max=3.
    ``SKU_SEGMENT_OVERRIDES`` collapses this to 3; size.max is the
    defensive clamp for any future SKU the override table hasn't seen yet.
    """

    def test_h7075_returns_3_segments(self):
        """H7075 with elementRange.max=14 + size.max=3 collapses to 3 (REQ-001)."""
        cap = _make_rgbic_segment_capability(
            element_range_max=14, size_max=3,
        )
        device = _make_rgbic_device("H7075", cap)
        assert device.segment_count == 3

    def test_h7076_returns_4_segments_where_the_clamp_cannot_help(self):
        """H7076 reports elementRange.max=14 AND size.max=15 (issue #160).

        The counter-case to the H7075: the two fields agree, so the clamp is a
        no-op and only the explicit override collapses the count. Indices 4-14
        are accepted by the cloud with HTTP 200 "success" and move nothing.
        """
        cap = _make_rgbic_segment_capability(element_range_max=14, size_max=15)
        device = _make_rgbic_device("H7076", cap)
        assert device.segment_count == 4

    def test_unknown_sku_returns_api_count(self):
        """SKUs not in the override table keep the parser's API count (REQ-002)."""
        cap = _make_rgbic_segment_capability(element_range_max=14, size_max=None)
        device = _make_rgbic_device("H6000", cap)
        assert device.segment_count == 15

    def test_size_max_clamp_without_override(self):
        """size.max clamps the API count when no override exists (REQ-001 defense)."""
        cap = _make_rgbic_segment_capability(element_range_max=20, size_max=15)
        device = _make_rgbic_device("H6000", cap)
        assert device.segment_count == 15

    def test_sku_uppercase_normalization(self, monkeypatch):
        """SKU lookup is case-insensitive: 'h7075' resolves identically to 'H7075' (REQ-004)."""
        import custom_components.govee.models.device as device_module

        monkeypatch.setattr(
            device_module, "SKU_SEGMENT_OVERRIDES", {"H7075": 3},
        )
        cap = _make_rgbic_segment_capability(element_range_max=14, size_max=3)
        device = _make_rgbic_device("h7075", cap)
        assert device.segment_count == 3

    def test_segmentCount_direct_wins_when_no_override(self):
        """API ``segmentCount`` parameter is respected when SKU has no override (REQ-002)."""
        cap = _make_rgbic_segment_capability(
            element_range_max=None, size_max=None, segment_count=10,
        )
        device = _make_rgbic_device("H7028", cap)
        assert device.segment_count == 10

    def test_override_wins_over_segmentCount(self, monkeypatch):
        """Override is the source of truth even when API exposes segmentCount (REQ-001)."""
        import custom_components.govee.models.device as device_module

        monkeypatch.setattr(
            device_module, "SKU_SEGMENT_OVERRIDES", {"H7075": 3},
        )
        cap = _make_rgbic_segment_capability(
            element_range_max=None, size_max=None, segment_count=10,
        )
        device = _make_rgbic_device("H7075", cap)
        assert device.segment_count == 3

    def test_override_is_authoritative_over_size_max(self, monkeypatch):
        """SKU_SEGMENT_OVERRIDES is the source of truth for known SKUs.

        The size.max clamp applies to the API-reported count *before* the
        override lookup, so an explicit override entry wins even when it
        exceeds size.max. Operators adding a new override are responsible
        for keeping it accurate; this is preferable to silently clamping a
        known-good value.
        """
        import custom_components.govee.models.device as device_module

        monkeypatch.setattr(
            device_module, "SKU_SEGMENT_OVERRIDES", {"H7075": 5},
        )
        cap = _make_rgbic_segment_capability(element_range_max=14, size_max=3)
        device = _make_rgbic_device("H7075", cap)
        # Override says 5; size.max (3) clamps the api_count from 15 to 3
        # but the explicit override still wins → 5.
        assert device.segment_count == 5

    def test_size_max_clamp_protects_unknown_sku(self, monkeypatch):
        """Unknown SKU with bogus elementRange.max gets clamped by size.max.

        This is the automatic safety-net path: when no override exists for
        the SKU, size.max catches devices that follow the H7075
        over-reporting pattern without requiring a manual dict entry.
        """
        import custom_components.govee.models.device as device_module

        monkeypatch.setattr(
            device_module, "SKU_SEGMENT_OVERRIDES", {},
        )
        cap = _make_rgbic_segment_capability(element_range_max=14, size_max=3)
        device = _make_rgbic_device("H9999", cap)
        # No override → api_count (15) is clamped by size.max (3) → 3.
        assert device.segment_count == 3

    def test_no_segment_capability_returns_zero(self):
        """Device without any segment capability returns 0 (REQ-008 fallback).

        Covers the ``return 0`` branch in GoveeDevice.segment_count when
        no capability has ``is_segment_color=True``. Non-RGBIC lights and
        grouped devices fall here.
        """
        cap = _make_rgbic_segment_capability(
            element_range_max=None, size_max=None, segment_count=None,
        )
        device = _make_rgbic_device("H6000", cap)
        # No segment capability → 0, not an exception.
        assert device.segment_count == 0
