"""Tests for heater autoStop parsing + dispatch (issue #29)."""

from __future__ import annotations

from custom_components.govee.models import GoveeCapability, GoveeDevice
from custom_components.govee.models.state import GoveeDeviceState


def _heater_temperature_setting_cap() -> GoveeCapability:
    return GoveeCapability(
        type="devices.capabilities.temperature_setting",
        instance="targetTemperature",
        parameters={
            "dataType": "STRUCT",
            "fields": [
                {
                    "fieldName": "autoStop",
                    "defaultValue": 0,
                    "dataType": "ENUM",
                    "options": [
                        {"name": "Auto Stop", "value": 1},
                        {"name": "Maintain", "value": 0},
                    ],
                    "required": False,
                },
                {
                    "fieldName": "temperature",
                    "dataType": "INTEGER",
                    "range": {"min": 5, "max": 30, "precision": 1},
                    "required": True,
                },
                {
                    "fieldName": "unit",
                    "defaultValue": "Celsius",
                    "dataType": "ENUM",
                    "options": [
                        {"name": "Celsius", "value": "Celsius"},
                        {"name": "Fahrenheit", "value": "Fahrenheit"},
                    ],
                    "required": True,
                },
            ],
        },
    )


class TestTemperatureSettingStructParsing:
    def test_parses_autostop_and_temperature(self):
        state = GoveeDeviceState.create_empty("dev1")
        state.update_from_api(
            {
                "capabilities": [
                    {
                        "type": "devices.capabilities.temperature_setting",
                        "instance": "targetTemperature",
                        "state": {
                            "value": {
                                "temperature": 22,
                                "autoStop": 1,
                                "unit": "Celsius",
                            }
                        },
                    }
                ]
            }
        )
        assert state.heater_temperature == 22
        assert state.heater_auto_stop == 1

    def test_parses_autostop_zero(self):
        state = GoveeDeviceState.create_empty("dev1")
        state.update_from_api(
            {
                "capabilities": [
                    {
                        "type": "devices.capabilities.temperature_setting",
                        "instance": "targetTemperature",
                        "state": {"value": {"temperature": 18, "autoStop": 0}},
                    }
                ]
            }
        )
        assert state.heater_temperature == 18
        assert state.heater_auto_stop == 0

    def test_missing_value_leaves_state_untouched(self):
        state = GoveeDeviceState.create_empty("dev1")
        state.heater_auto_stop = 1
        state.update_from_api(
            {
                "capabilities": [
                    {
                        "type": "devices.capabilities.temperature_setting",
                        "instance": "targetTemperature",
                        "state": {"value": None},
                    }
                ]
            }
        )
        # No value — existing optimistic/cached state is preserved.
        assert state.heater_auto_stop == 1


class TestSupportsTemperatureSettingAutoStop:
    def _device_with_caps(self, caps: list[GoveeCapability]) -> GoveeDevice:
        return GoveeDevice(
            device_id="dev1",
            sku="H713C",
            name="Heater",
            device_type="devices.types.heater",
            capabilities=tuple(caps),
        )

    def test_true_when_struct_has_auto_stop_field(self):
        device = self._device_with_caps([_heater_temperature_setting_cap()])
        assert device.supports_temperature_setting_auto_stop is True

    def test_false_when_capability_missing(self):
        device = self._device_with_caps([])
        assert device.supports_temperature_setting_auto_stop is False

    def test_false_when_struct_has_no_auto_stop_field(self):
        cap = GoveeCapability(
            type="devices.capabilities.temperature_setting",
            instance="targetTemperature",
            parameters={
                "dataType": "STRUCT",
                "fields": [
                    {
                        "fieldName": "temperature",
                        "dataType": "INTEGER",
                        "required": True,
                    }
                ],
            },
        )
        device = self._device_with_caps([cap])
        assert device.supports_temperature_setting_auto_stop is False


class TestLightFilterExcludesAppliances:
    """issue #54 — is_light_device must reject appliance device types."""

    def _device(self, device_type: str, **extra) -> GoveeDevice:
        return GoveeDevice(
            device_id="dev1",
            sku=extra.get("sku", "HXXXX"),
            name="Appliance",
            device_type=device_type,
            capabilities=(
                GoveeCapability(
                    type="devices.capabilities.on_off",
                    instance="powerSwitch",
                    parameters={},
                ),
            ),
        )

    def test_humidifier_is_not_light(self):
        assert self._device("devices.types.humidifier").is_light_device is False
        assert self._device("devices.types.humidifier").is_humidifier is True

    def test_heater_is_not_light(self):
        assert self._device("devices.types.heater").is_light_device is False

    def test_purifier_is_not_light(self):
        assert self._device("devices.types.air_purifier").is_light_device is False

    def test_plain_light_is_still_light(self):
        device = GoveeDevice(
            device_id="dev1",
            sku="H6072",
            name="Light",
            device_type="devices.types.light",
            capabilities=(
                GoveeCapability(
                    type="devices.capabilities.on_off",
                    instance="powerSwitch",
                    parameters={},
                ),
            ),
        )
        assert device.is_light_device is True


class TestFahrenheitHeaterStructParsing:
    """Heaters that run in °F report the temperature_setting STRUCT with
    ``unit: Fahrenheit`` and the ``targetTemperature`` field name (H713B,
    issue #129)."""

    def test_parses_fahrenheit_state_shape_h713b(self):
        # Verbatim shape from H713B diagnostics: field is named
        # ``targetTemperature`` (not ``temperature``) and unit is Fahrenheit.
        state = GoveeDeviceState.create_empty("dev1")
        state.update_from_api(
            {
                "capabilities": [
                    {
                        "type": "devices.capabilities.temperature_setting",
                        "instance": "targetTemperature",
                        "state": {
                            "value": {
                                "unit": "Fahrenheit",
                                "targetTemperature": 41,
                            }
                        },
                    }
                ]
            }
        )
        # 41°F == 5°C — heater_temperature is canonical Celsius.
        assert state.heater_temperature == 5
        assert state.heater_temperature_unit == "Fahrenheit"

    def test_celsius_struct_stays_unconverted(self):
        state = GoveeDeviceState.create_empty("dev1")
        state.update_from_api(
            {
                "capabilities": [
                    {
                        "type": "devices.capabilities.temperature_setting",
                        "instance": "targetTemperature",
                        "state": {
                            "value": {
                                "temperature": 22,
                                "autoStop": 1,
                                "unit": "Celsius",
                            }
                        },
                    }
                ]
            }
        )
        assert state.heater_temperature == 22
        assert state.heater_temperature_unit == "Celsius"

    def test_unit_absent_defaults_to_no_conversion(self):
        state = GoveeDeviceState.create_empty("dev1")
        state.update_from_api(
            {
                "capabilities": [
                    {
                        "type": "devices.capabilities.temperature_setting",
                        "instance": "targetTemperature",
                        "state": {"value": {"temperature": 18}},
                    }
                ]
            }
        )
        assert state.heater_temperature == 18
        assert state.heater_temperature_unit is None
