"""Coverage tests for the domain models.

Targets the branches ``test_models.py`` leaves untouched: capability-parser
fallbacks and empty-result paths on ``GoveeDevice``, the defensive coercions
and secondary parse shapes in ``GoveeDeviceState``, the command classes that
had no serialization test, the leak-sensor ``DeviceInfo`` builder, and
``TransportHealth.mark_unavailable``.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pytest

from custom_components.govee.const import BFF_SEGMENT_COUNT_SKUS, SKU_SEGMENT_OVERRIDES
from custom_components.govee.models import (
    ColorTempRange,
    DIYSceneCommand,
    GoveeCapability,
    GoveeDevice,
    GoveeDeviceState,
    MusicModeCommand,
    RangeCommand,
    RGBColor,
    SceneCommand,
    SegmentCapability,
    SegmentColorCommand,
    SegmentState,
    TemperatureSettingCommand,
    TransportHealth,
    create_night_light_command,
)
from custom_components.govee.models.device import (
    CAPABILITY_COLOR_SETTING,
    CAPABILITY_DYNAMIC_SCENE,
    CAPABILITY_MUSIC_MODE,
    CAPABILITY_ON_OFF,
    CAPABILITY_RANGE,
    CAPABILITY_SEGMENT_COLOR,
    CAPABILITY_TEMPERATURE_SETTING,
    CAPABILITY_WORK_MODE,
    DEVICE_TYPE_LIGHT,
    INSTANCE_BRIGHTNESS,
    INSTANCE_COLOR_TEMP,
    INSTANCE_DIY,
    INSTANCE_MUSIC_MODE,
    INSTANCE_NIGHT_LIGHT,
    INSTANCE_POWER,
    INSTANCE_SCENE,
    INSTANCE_SEGMENT_COLOR,
    INSTANCE_TARGET_TEMPERATURE,
    INSTANCE_WORK_MODE,
    GoveeLeakSensor,
    leak_sensor_device_info,
)
from custom_components.govee.models.state import _coerce_int, _coerce_sensor_value

DEVICE_ID = "AA:BB:CC:DD:EE:FF:00:11"


def _device(*caps: GoveeCapability, sku: str = "H6072") -> GoveeDevice:
    return GoveeDevice(device_id=DEVICE_ID, sku=sku, name="Lamp", device_type=DEVICE_TYPE_LIGHT, capabilities=caps)


def _work_mode(fields: list[dict[str, Any]]) -> GoveeCapability:
    return GoveeCapability(type=CAPABILITY_WORK_MODE, instance=INSTANCE_WORK_MODE, parameters={"fields": fields})


def _music(parameters: dict[str, Any]) -> GoveeCapability:
    return GoveeCapability(type=CAPABILITY_MUSIC_MODE, instance=INSTANCE_MUSIC_MODE, parameters=parameters)


# --------------------------------------------------------------------------- #
# Capability parsers
# --------------------------------------------------------------------------- #


class TestColorTempRange:
    def test_none_when_either_bound_is_missing(self):
        assert ColorTempRange.from_capability({"parameters": {"range": {"min": 2000}}}) is None
        assert ColorTempRange.from_capability({"parameters": {}}) is None

    def test_device_color_temp_range_none_without_capability(self):
        assert _device().color_temp_range is None

    def test_device_color_temp_range_parses_the_capability(self):
        cap = GoveeCapability(
            type=CAPABILITY_COLOR_SETTING,
            instance=INSTANCE_COLOR_TEMP,
            parameters={"range": {"min": 2000, "max": 9000}},
        )
        assert _device(cap).color_temp_range == ColorTempRange(min_kelvin=2000, max_kelvin=9000)


class TestSegmentCapability:
    def test_size_max_is_the_fallback_when_element_range_is_absent(self):
        cap = {"parameters": {"fields": [{"fieldName": "segment", "size": {"min": 1, "max": 10}}]}}
        assert SegmentCapability.from_capability(cap) == SegmentCapability(segment_count=10)

    def test_element_range_wins_over_size(self):
        cap = {"parameters": {"fields": [{"fieldName": "segment", "elementRange": {"max": 6}, "size": {"max": 15}}]}}
        assert SegmentCapability.from_capability(cap) == SegmentCapability(segment_count=7)

    def test_direct_segment_count_parameter(self):
        assert SegmentCapability.from_capability({"parameters": {"segmentCount": 4}}) == SegmentCapability(4)

    def test_none_when_nothing_describes_the_segments(self):
        cap = {"parameters": {"fields": [{"fieldName": "segment"}, {"fieldName": "rgb", "size": {"max": 3}}]}}
        assert SegmentCapability.from_capability(cap) is None

    def test_device_segment_count_zero_without_capability(self):
        assert _device().segment_count == 0

    def test_device_segment_count_clamps_to_size_max_then_applies_override(self):
        sku, expected = next(iter(SKU_SEGMENT_OVERRIDES.items()))
        params = {"fields": [{"fieldName": "segment", "elementRange": {"min": 0, "max": 14}, "size": {"max": 15}}]}
        cap = GoveeCapability(type=CAPABILITY_SEGMENT_COLOR, instance=INSTANCE_SEGMENT_COLOR, parameters=params)
        assert _device(cap, sku=sku.lower()).segment_count == expected
        assert _device(cap, sku="H6172").segment_count == 15


class TestBffSegmentCount:
    """bff_segment_count wins for BFF_SEGMENT_COUNT_SKUS only (#301)."""

    PARAMS = {"fields": [{"fieldName": "segment", "elementRange": {"min": 0, "max": 8}, "size": {"max": 9}}]}

    def _make(self, sku: str, bff: int | None) -> GoveeDevice:
        cap = GoveeCapability(type=CAPABILITY_SEGMENT_COLOR, instance=INSTANCE_SEGMENT_COLOR, parameters=self.PARAMS)
        return GoveeDevice(
            device_id=DEVICE_ID,
            sku=sku,
            name="Lamp",
            device_type=DEVICE_TYPE_LIGHT,
            capabilities=(cap,),
            bff_segment_count=bff,
        )

    def test_allow_listed_sku_uses_bff_count(self):
        assert "H3A53" in BFF_SEGMENT_COUNT_SKUS
        device = self._make("h3a53", 11)
        resolution = device.segment_count_resolution
        assert resolution is not None
        assert resolution["source"] == "bff"
        assert resolution["bff"] == 11
        assert resolution["api_count"] == 9
        assert device.segment_count == 11

    @pytest.mark.parametrize("bff", [None, 0, -1])
    def test_allow_listed_sku_without_a_positive_count_falls_back(self, bff):
        device = self._make("H3A53", bff)
        assert device.segment_count == 9
        assert device.segment_count_resolution["source"] == "api"

    def test_other_skus_ignore_bff_count(self):
        sku, expected = next(iter(SKU_SEGMENT_OVERRIDES.items()))
        assert self._make(sku, 11).segment_count == expected
        device = self._make("H6172", 11)
        assert device.segment_count == 9
        assert device.segment_count_resolution["bff"] == 11


class TestGoveeCapabilityBrightnessRange:
    def test_non_brightness_capability_reports_the_default(self):
        cap = GoveeCapability(type=CAPABILITY_ON_OFF, instance=INSTANCE_POWER, parameters={"range": {"min": 5}})
        assert cap.brightness_range == (0, 100)

    def test_brightness_capability_reads_its_range(self):
        cap = GoveeCapability(
            type=CAPABILITY_RANGE, instance=INSTANCE_BRIGHTNESS, parameters={"range": {"min": 1, "max": 254}}
        )
        assert cap.brightness_range == (1, 254)


# --------------------------------------------------------------------------- #
# GoveeDevice option extractors — empty and fallback paths
# --------------------------------------------------------------------------- #


class TestDeviceEmptyExtractors:
    def test_extractors_are_empty_without_their_capability(self):
        device = _device()
        assert device.get_snapshot_options() == []
        assert device.get_humidifier_work_mode_options() == []
        assert device.get_humidifier_gear_options() == []
        assert device.get_ceiling_fan_speed_options() == []
        assert device.get_music_mode_options() == []
        assert device.get_nightlight_scene_options() == []
        assert device.get_fan_speed_options() == []
        assert device.has_struct_music_mode is False
        assert device.get_music_sensitivity_range() == (0, 100)

    def test_get_capability_by_type_and_instance(self):
        power = GoveeCapability(type=CAPABILITY_ON_OFF, instance=INSTANCE_POWER)
        device = _device(power)
        assert device.get_capability(CAPABILITY_ON_OFF, INSTANCE_POWER) is power
        assert device.get_capability(CAPABILITY_ON_OFF, "otherSwitch") is None
        assert device.get_capability(CAPABILITY_RANGE, INSTANCE_POWER) is None

    def test_from_api_response_rejects_a_device_without_id_or_sku(self):
        with pytest.raises(ValueError, match="missing required fields"):
            GoveeDevice.from_api_response({"sku": "H6072"})
        with pytest.raises(ValueError, match="missing required fields"):
            GoveeDevice.from_api_response({"device": DEVICE_ID, "sku": ""})


class TestAutoModeValueIsSetpoint:
    def _device(self, auto_option: dict[str, Any] | None) -> GoveeDevice:
        options = [{"name": "gearMode", "options": [{"name": "Low", "value": 1}]}]
        if auto_option is not None:
            options.append(auto_option)
        return _device(_work_mode([{"fieldName": "modeValue", "options": options}]))

    def test_false_without_a_work_mode_capability(self):
        assert _device().auto_mode_value_is_setpoint() is False

    def test_false_when_auto_has_no_range(self):
        assert self._device({"name": "Auto", "defaultValue": 0}).auto_mode_value_is_setpoint() is False
        assert self._device(None).auto_mode_value_is_setpoint() is False

    def test_h7150_style_real_range_is_the_setpoint(self):
        assert self._device({"name": "Auto", "range": {"min": 30, "max": 80}}).auto_mode_value_is_setpoint() is True

    def test_h7152_style_pinned_range_is_not(self):
        assert self._device({"name": "auto", "range": {"min": 80, "max": 80}}).auto_mode_value_is_setpoint() is False


class TestTemperatureSettingAutoStop:
    def _cap(self, parameters: dict[str, Any]) -> GoveeCapability:
        return GoveeCapability(
            type=CAPABILITY_TEMPERATURE_SETTING, instance=INSTANCE_TARGET_TEMPERATURE, parameters=parameters
        )

    def test_struct_without_fields_is_skipped(self):
        assert _device(self._cap({})).supports_temperature_setting_auto_stop is False
        assert _device(self._cap({"fields": []})).supports_temperature_setting_auto_stop is False

    def test_struct_with_auto_stop_field(self):
        cap = self._cap({"fields": [{"fieldName": "temperature"}, {"fieldName": "autoStop"}]})
        assert _device(cap).supports_temperature_setting_auto_stop is True


class TestMusicModeCapability:
    def test_struct_music_mode_is_detected_by_its_fields(self):
        assert _device(_music({"fields": [{"fieldName": "musicMode"}]})).has_struct_music_mode is True
        assert _device(_music({"dataType": "ENUM"})).has_struct_music_mode is False

    def test_music_mode_options_require_the_music_mode_field(self):
        options = [{"name": "Rhythm", "value": 1}, {"name": "Spectrum", "value": 2}]
        with_field = _music({"fields": [{"fieldName": "sensitivity"}, {"fieldName": "musicMode", "options": options}]})
        assert _device(with_field).get_music_mode_options() == options
        assert _device(_music({"fields": [{"fieldName": "sensitivity"}]})).get_music_mode_options() == []

    def test_sensitivity_range_from_the_struct(self):
        cap = _music({"fields": [{"fieldName": "sensitivity", "range": {"min": 10, "max": 90}}]})
        assert _device(cap).get_music_sensitivity_range() == (10, 90)

    def test_sensitivity_range_defaults_when_the_field_is_missing(self):
        cap = _music({"fields": [{"fieldName": "musicMode", "options": []}]})
        assert _device(cap).get_music_sensitivity_range() == (0, 100)
        partial = _music({"fields": [{"fieldName": "sensitivity", "range": {"max": 50}}]})
        assert _device(partial).get_music_sensitivity_range() == (0, 50)


class TestFanSpeedOptions:
    def test_work_mode_capability_without_a_work_mode_field(self):
        device = _device(_work_mode([{"fieldName": "modeValue", "options": []}]))
        assert device.get_fan_speed_options() == []

    def test_options_without_a_name_or_value_are_skipped(self):
        device = _device(
            _work_mode(
                [
                    {
                        "fieldName": "workMode",
                        "options": [
                            {"name": "", "value": 1},
                            {"name": "Ghost"},
                            {"name": "Auto", "value": 3},
                        ],
                    },
                    {"fieldName": "modeValue", "options": [{"name": "Auto", "defaultValue": 7}]},
                ]
            )
        )
        assert device.get_fan_speed_options() == [{"name": "Auto", "work_mode": 3, "mode_value": 7}]


# --------------------------------------------------------------------------- #
# Leak-sensor DeviceInfo
# --------------------------------------------------------------------------- #


class TestLeakSensorDeviceInfo:
    def _sensor(self, **versions: str) -> GoveeLeakSensor:
        return GoveeLeakSensor(
            device_id="01:32:7A:C4:06:03:0D:0C",
            name="Kitchen sink",
            sku="H5058",
            hub_device_id="09:C2:60:74:F4:64:AB:FA",
            sno=3,
            **versions,
        )

    def test_links_the_sensor_to_its_hub(self):
        info = leak_sensor_device_info(self._sensor(), "govee", {"via_device": ("govee", "09:C2:60:74:F4:64:AB:FA")})
        assert info == {
            "identifiers": {("govee", "01:32:7A:C4:06:03:0D:0C")},
            "name": "Kitchen sink",
            "manufacturer": "Govee",
            "model": "H5058",
            "via_device": ("govee", "09:C2:60:74:F4:64:AB:FA"),
        }

    def test_links_by_registry_id_on_new_ha(self):
        info = leak_sensor_device_info(self._sensor(), "govee", {"via_device_id": "hub-id"})
        assert info["via_device_id"] == "hub-id"
        assert "via_device" not in info

    def test_no_link_when_the_hub_is_unregistered(self):
        info = leak_sensor_device_info(self._sensor(), "govee", {})
        assert "via_device" not in info
        assert "via_device_id" not in info

    def test_versions_are_only_present_when_known(self):
        info = leak_sensor_device_info(self._sensor(hw_version="1.00.01", sw_version="2.03.00"), "govee", {})
        assert info["hw_version"] == "1.00.01"
        assert info["sw_version"] == "2.03.00"

        only_sw = leak_sensor_device_info(self._sensor(sw_version="2.03.00"), "govee", {})
        assert "hw_version" not in only_sw
        assert only_sw["sw_version"] == "2.03.00"


# --------------------------------------------------------------------------- #
# GoveeDeviceState — coercions and secondary parse shapes
# --------------------------------------------------------------------------- #


class TestCoercions:
    @pytest.mark.parametrize("value", ["abc", "12.5", [1], {"v": 1}, object()])
    def test_coerce_int_swallows_unparseable_values(self, value: Any):
        assert _coerce_int(value) is None

    def test_coerce_int_parses_numbers_and_numeric_strings(self):
        assert _coerce_int("42") == 42
        assert _coerce_int(7.9) == 7
        assert _coerce_int("") is None
        assert _coerce_int(None) is None

    def test_coerce_sensor_value_rejects_booleans(self):
        assert _coerce_sensor_value(True, ("value",)) is None
        assert _coerce_sensor_value(False, ("value",)) is None

    def test_coerce_sensor_value_skips_boolean_struct_fields(self):
        value = {"sensorTemperature": True, "temperature": 21.5}
        assert _coerce_sensor_value(value, ("sensorTemperature", "temperature")) == 21.5
        assert _coerce_sensor_value({"sensorTemperature": False}, ("sensorTemperature",)) is None

    def test_coerce_sensor_value_ignores_strings(self):
        assert _coerce_sensor_value("21.5", ("value",)) is None
        assert _coerce_sensor_value({"value": "21.5"}, ("value",)) is None


class TestSegmentState:
    def test_from_dict(self):
        segment = SegmentState.from_dict({"color": {"r": 10, "g": 20, "b": 30}, "brightness": 55}, index=4)
        assert segment == SegmentState(index=4, color=RGBColor(10, 20, 30), brightness=55)

    def test_from_dict_defaults(self):
        segment = SegmentState.from_dict({}, index=0)
        assert segment.color == RGBColor(0, 0, 0)
        assert segment.brightness == 100


def _api(cap_type: str, instance: str, value: Any) -> dict[str, Any]:
    return {"capabilities": [{"type": cap_type, "instance": instance, "state": {"value": value}}]}


class TestUpdateFromApiShapes:
    def test_color_rgb_as_a_struct(self):
        state = GoveeDeviceState.create_empty(DEVICE_ID)
        state.update_from_api(_api("devices.capabilities.color_setting", "colorRgb", {"r": 1, "g": 2, "b": 3}))
        assert state.color == RGBColor(1, 2, 3)

    def test_color_rgb_empty_string_is_ignored(self):
        state = GoveeDeviceState.create_empty(DEVICE_ID)
        state.color = RGBColor(9, 9, 9)
        state.update_from_api(_api("devices.capabilities.color_setting", "colorRgb", ""))
        assert state.color == RGBColor(9, 9, 9)

    @pytest.mark.parametrize(("value", "expected"), [(1, True), (0, False)])
    def test_dreamview_toggle(self, value: int, expected: bool):
        state = GoveeDeviceState.create_empty(DEVICE_ID)
        state.update_from_api(_api("devices.capabilities.toggle", "dreamViewToggle", value))
        assert state.dreamview_enabled is expected

    def test_unparseable_heater_fields_are_ignored(self):
        state = GoveeDeviceState.create_empty(DEVICE_ID)
        state.heater_temperature = 20
        state.heater_auto_stop = 1
        value = {"temperature": "warm", "autoStop": "yes", "unit": "Celsius"}
        state.update_from_api(_api("devices.capabilities.temperature_setting", "targetTemperature", value))
        assert state.heater_temperature == 20
        assert state.heater_auto_stop == 1
        assert state.device_temperature_unit == "Celsius"

    def test_fahrenheit_heater_target_is_normalized(self):
        state = GoveeDeviceState.create_empty(DEVICE_ID)
        value = {"targetTemperature": 68, "autoStop": 1, "unit": "Fahrenheit"}
        state.update_from_api(_api("devices.capabilities.temperature_setting", "targetTemperature", value))
        assert state.heater_temperature == 20
        assert state.heater_auto_stop == 1


class TestUpdateFromMqttShapes:
    def test_packed_int_color(self):
        state = GoveeDeviceState.create_empty(DEVICE_ID)
        state.update_from_mqtt({"color": 0xFF8040})
        assert state.color == RGBColor(255, 128, 64)

    def test_unknown_color_shape_is_ignored(self):
        state = GoveeDeviceState.create_empty(DEVICE_ID)
        state.color = RGBColor(1, 1, 1)
        state.update_from_mqtt({"color": "red"})
        assert state.color == RGBColor(1, 1, 1)


class TestOptimisticDiyStyle:
    def test_stamps_style_and_optimistic_source(self):
        state = GoveeDeviceState.create_empty(DEVICE_ID)
        state.apply_optimistic_diy_style("Jumping", 1)
        assert state.diy_style == "Jumping"
        assert state.diy_style_value == 1
        assert state.source == "optimistic"
        assert state.last_optimistic_update is not None

    def test_style_value_is_optional(self):
        state = GoveeDeviceState.create_empty(DEVICE_ID)
        state.apply_optimistic_diy_style("Fade")
        assert state.diy_style == "Fade"
        assert state.diy_style_value is None


# --------------------------------------------------------------------------- #
# Commands
# --------------------------------------------------------------------------- #


class TestCommandPayloads:
    def test_range_command(self):
        assert RangeCommand(range_instance="fanSpeed", value=3).to_api_payload() == {
            "type": CAPABILITY_RANGE,
            "instance": "fanSpeed",
            "value": 3,
        }

    def test_scene_command(self):
        assert SceneCommand(scene_id=3853, scene_name="Sunrise").to_api_payload() == {
            "type": CAPABILITY_DYNAMIC_SCENE,
            "instance": INSTANCE_SCENE,
            "value": {"id": 3853, "name": "Sunrise"},
        }

    def test_scene_command_with_param_id_sends_param_id_and_id(self):
        assert SceneCommand(scene_id=399, scene_name="Adventure Game-A", param_id=1001).to_api_payload() == {
            "type": CAPABILITY_DYNAMIC_SCENE,
            "instance": INSTANCE_SCENE,
            "value": {"paramId": 1001, "id": 399},
        }

    def test_diy_scene_command_sends_the_bare_id(self):
        assert DIYSceneCommand(scene_id=100, scene_name="Rainbow").to_api_payload() == {
            "type": CAPABILITY_DYNAMIC_SCENE,
            "instance": INSTANCE_DIY,
            "value": 100,
        }

    def test_segment_color_command(self):
        command = SegmentColorCommand(segment_indices=(0, 2), color=RGBColor(255, 0, 0))
        assert command.to_api_payload() == {
            "type": CAPABILITY_SEGMENT_COLOR,
            "instance": INSTANCE_SEGMENT_COLOR,
            "value": {"segment": [0, 2], "rgb": 0xFF0000},
        }

    @pytest.mark.parametrize(("enabled", "value"), [(True, 1), (False, 0)])
    def test_night_light_command(self, enabled: bool, value: int):
        command = create_night_light_command(enabled)
        assert command.toggle_instance == INSTANCE_NIGHT_LIGHT
        assert command.to_api_payload()["value"] == value

    def test_temperature_setting_command(self):
        assert TemperatureSettingCommand(temperature=22, auto_stop=1, unit="Celsius").to_api_payload() == {
            "type": CAPABILITY_TEMPERATURE_SETTING,
            "instance": INSTANCE_TARGET_TEMPERATURE,
            "value": {"autoStop": 1, "temperature": 22, "unit": "Celsius"},
        }


class TestMusicModeCommand:
    def test_auto_color_omits_rgb_even_when_given(self):
        assert MusicModeCommand(music_mode=1, sensitivity=50, auto_color=1, rgb=0xFF0000).to_api_payload() == {
            "type": CAPABILITY_MUSIC_MODE,
            "instance": INSTANCE_MUSIC_MODE,
            "value": {"musicMode": 1, "sensitivity": 50, "autoColor": 1},
        }

    def test_fixed_color_carries_rgb(self):
        value = MusicModeCommand(music_mode=3, sensitivity=80, auto_color=0, rgb=0x00FF00).get_value()
        assert value == {"musicMode": 3, "sensitivity": 80, "autoColor": 0, "rgb": 0x00FF00}

    def test_fixed_color_without_rgb_sends_no_rgb_key(self):
        assert "rgb" not in MusicModeCommand(music_mode=3, sensitivity=80, auto_color=0).get_value()


# --------------------------------------------------------------------------- #
# TransportHealth
# --------------------------------------------------------------------------- #


class TestMarkUnavailable:
    def test_keeps_the_previous_reason_when_none_is_given(self):
        health = TransportHealth(transport="lan")
        health.mark_failure(datetime(2026, 1, 1, tzinfo=timezone.utc), "send_failed")

        health.mark_unavailable()

        assert health.is_available is False
        assert health.last_failure_reason == "send_failed"

    def test_sets_a_reason_without_stamping_a_failure_time(self):
        health = TransportHealth(transport="mqtt")
        health.mark_success(datetime(2026, 1, 1, tzinfo=timezone.utc))

        health.mark_unavailable("disconnected")

        assert health.is_available is False
        assert health.last_failure_reason == "disconnected"
        assert health.last_failure_ts is None
        assert health.last_success_ts == datetime(2026, 1, 1, tzinfo=timezone.utc)
