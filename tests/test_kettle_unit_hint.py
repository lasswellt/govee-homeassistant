"""The device's own declared unit beats the SKU allowlist (issue #171).

An H7170 kettle sitting in a 77 °F room reported 170.6 °F — exactly
``(77 x 9/5) + 32``, a Fahrenheit value converted as though it were Celsius.

The device declares its unit in the *same* API response, in a sibling
capability. It was being ignored because the unit hint was only read from the
``targetTemperature`` instance that heaters use; the kettle carries the
identical ``{"unit": ..., "targetTemperature": ...}`` shape under
``sliderTemperature``. With no hint, ``resolve_fahrenheit_conversion`` fell
through to the SKU allowlist, which lists the H717A kettle but not the H7170 —
so the reading was converted a second time.
"""

from __future__ import annotations

import pytest

from custom_components.govee.const import resolve_fahrenheit_conversion
from custom_components.govee.models.state import GoveeDeviceState

# Verbatim from the raw_api_state in the reporter's diagnostics.
KETTLE_CAPABILITIES = [
    {
        "type": "devices.capabilities.property",
        "instance": "sensorTemperature",
        "state": {"value": 77},
    },
    {
        "type": "devices.capabilities.temperature_setting",
        "instance": "sliderTemperature",
        "state": {"value": {"unit": "Fahrenheit", "targetTemperature": 205}},
    },
]


def _state(capabilities):
    state = GoveeDeviceState.create_empty("AA:BB:CC:DD:EE:FF:00:11")
    state.update_from_api({"capabilities": capabilities})
    return state


class TestKettleUnitHint:
    def test_slider_temperature_declares_the_unit(self):
        """The hint is taken from the kettle's instance, not just the heater's."""
        assert _state(KETTLE_CAPABILITIES).device_temperature_unit == "Fahrenheit"

    def test_reading_is_not_converted_twice(self):
        """77 °F stays 77 °F rather than becoming 170.6 °F."""
        state = _state(KETTLE_CAPABILITIES)
        assert state.sensor_temperature == 77

        # "auto" + a device that says Fahrenheit -> the entity converts F->C
        # once, rather than treating the value as Celsius and converting up.
        assert (
            resolve_fahrenheit_conversion(
                "H7170", "auto", state.device_temperature_unit
            )
            is True
        )
        converted = (float(state.sensor_temperature) - 32.0) * (5.0 / 9.0)
        assert abs(converted - 25.0) < 0.1  # 77 °F == 25 °C

    def test_heater_instance_still_read(self):
        """Regression: the heater path that this hint came from is unchanged."""
        state = _state(
            [
                {
                    "type": "devices.capabilities.temperature_setting",
                    "instance": "targetTemperature",
                    "state": {"value": {"unit": "Fahrenheit", "targetTemperature": 68}},
                }
            ]
        )
        assert state.device_temperature_unit == "Fahrenheit"
        # heater_temperature is canonical °C — 68 °F == 20 °C (issue #129).
        assert state.heater_temperature == 20

    def test_no_unit_field_leaves_the_hint_unset(self):
        """A STRUCT without a unit must not invent one.

        The SKU allowlist has to keep its say for devices that declare nothing.
        """
        state = _state(
            [
                {
                    "type": "devices.capabilities.temperature_setting",
                    "instance": "sliderTemperature",
                    "state": {"value": {"targetTemperature": 205}},
                }
            ]
        )
        assert state.device_temperature_unit is None

    @pytest.mark.parametrize("declared", ["Celsius", "celsius"])
    def test_celsius_declaration_blocks_the_allowlist(self, declared):
        """A device saying Celsius overrides a Fahrenheit allowlist entry.

        This is the direction that matters for correctness: the allowlist is a
        guess about a SKU, the device's own statement is evidence about this
        unit.
        """
        state = _state(
            [
                {
                    "type": "devices.capabilities.temperature_setting",
                    "instance": "sliderTemperature",
                    "state": {"value": {"unit": declared, "targetTemperature": 90}},
                }
            ]
        )
        assert (
            resolve_fahrenheit_conversion(
                "H717A", "auto", state.device_temperature_unit
            )
            is False
        )
