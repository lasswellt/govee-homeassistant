"""Tests for stand-alone temperature/humidity sensor support (issue #62)."""

from __future__ import annotations

import pytest

from custom_components.govee.models import (
    GoveeCapability,
    GoveeDevice,
    GoveeDeviceState,
)
from custom_components.govee.models.device import (
    CAPABILITY_PROPERTY,
    DEVICE_TYPE_THERMOMETER,
    INSTANCE_SENSOR_HUMIDITY,
    INSTANCE_SENSOR_TEMPERATURE,
)


@pytest.fixture
def thermometer_caps():
    return (
        GoveeCapability(
            type=CAPABILITY_PROPERTY,
            instance=INSTANCE_SENSOR_TEMPERATURE,
            parameters={},
        ),
        GoveeCapability(
            type=CAPABILITY_PROPERTY,
            instance=INSTANCE_SENSOR_HUMIDITY,
            parameters={},
        ),
    )


@pytest.fixture
def h5179_device(thermometer_caps):
    """H5179 WiFi Thermometer (canonical) — proves we don't need
    SKU-specific handling, only capability detection."""
    return GoveeDevice(
        device_id="AA:BB:CC:DD:EE:FF:00:11",
        sku="H5179",
        name="Living Room Thermometer",
        device_type=DEVICE_TYPE_THERMOMETER,
        capabilities=thermometer_caps,
        is_group=False,
    )


@pytest.fixture
def h5109_device(thermometer_caps):
    """H5109 Smart Temperature Sensor — issue #62 reporter's device."""
    return GoveeDevice(
        device_id="11:22:33:44:55:66:77:88",
        sku="H5109",
        name="Garage Thermometer",
        device_type=DEVICE_TYPE_THERMOMETER,
        capabilities=thermometer_caps,
        is_group=False,
    )


class TestThermometerDetection:
    def test_h5179_supports_temperature_and_humidity(self, h5179_device):
        assert h5179_device.supports_temperature_sensor is True
        assert h5179_device.supports_humidity_sensor is True

    def test_h5109_supports_temperature_and_humidity(self, h5109_device):
        # Same capabilities, different SKU — capability-based detection
        # means H5109 lights up for free once H5179 works.
        assert h5109_device.supports_temperature_sensor is True
        assert h5109_device.supports_humidity_sensor is True

    def test_thermometer_is_thermometer(self, h5109_device):
        assert h5109_device.is_thermometer is True

    def test_light_device_is_not_thermometer_supports_nothing(self):
        """A regular light must not pick up sensor entities by accident."""
        from custom_components.govee.models.device import (
            CAPABILITY_ON_OFF,
            INSTANCE_POWER,
        )

        light = GoveeDevice(
            device_id="00:11:22:33:44:55:66:77",
            sku="H6072",
            name="Bedroom Lamp",
            device_type="devices.types.light",
            capabilities=(
                GoveeCapability(
                    type=CAPABILITY_ON_OFF,
                    instance=INSTANCE_POWER,
                    parameters={},
                ),
            ),
            is_group=False,
        )
        assert light.supports_temperature_sensor is False
        assert light.supports_humidity_sensor is False
        assert light.is_thermometer is False


class TestThermometerStateParsing:
    def _api_payload(self, *caps):
        return {"capabilities": list(caps)}

    def test_parses_plain_number_value(self):
        state = GoveeDeviceState.create_empty("dev")
        state.update_from_api(
            self._api_payload(
                {
                    "type": CAPABILITY_PROPERTY,
                    "instance": INSTANCE_SENSOR_TEMPERATURE,
                    "state": {"value": 21.5},
                },
                {
                    "type": CAPABILITY_PROPERTY,
                    "instance": INSTANCE_SENSOR_HUMIDITY,
                    "state": {"value": 47.0},
                },
            )
        )
        assert state.sensor_temperature == 21.5
        assert state.sensor_humidity == 47.0

    def test_parses_struct_value(self):
        """Some H5XXX SKUs return a STRUCT under value with currentX
        named fields (legacy shape). Accept both."""
        state = GoveeDeviceState.create_empty("dev")
        state.update_from_api(
            self._api_payload(
                {
                    "type": CAPABILITY_PROPERTY,
                    "instance": INSTANCE_SENSOR_TEMPERATURE,
                    "state": {"value": {"currentTemperature": 19.4}},
                },
                {
                    "type": CAPABILITY_PROPERTY,
                    "instance": INSTANCE_SENSOR_HUMIDITY,
                    "state": {"value": {"currentHumidity": 55.2}},
                },
            )
        )
        assert state.sensor_temperature == 19.4
        assert state.sensor_humidity == 55.2

    def test_missing_value_leaves_state_unchanged(self):
        state = GoveeDeviceState.create_empty("dev")
        state.sensor_temperature = 20.0
        state.update_from_api(
            self._api_payload(
                {
                    "type": CAPABILITY_PROPERTY,
                    "instance": INSTANCE_SENSOR_TEMPERATURE,
                    "state": {},
                }
            )
        )
        assert state.sensor_temperature == 20.0

    def test_non_numeric_value_is_ignored(self):
        state = GoveeDeviceState.create_empty("dev")
        state.update_from_api(
            self._api_payload(
                {
                    "type": CAPABILITY_PROPERTY,
                    "instance": INSTANCE_SENSOR_TEMPERATURE,
                    "state": {"value": "not a number"},
                }
            )
        )
        assert state.sensor_temperature is None


class TestTemperatureSensorFahrenheitConversion:
    """Regression for #72/#78: H5179/H5109/H5110/HS5108/HS5106 report °F via
    cloud API. Verifies the GoveeTemperatureSensor.native_value path honors
    the api_temperature_unit option."""

    def _make_sensor_stub(self, raw_value, api_unit, sku="H6072", account_unit=None):
        from types import SimpleNamespace

        from custom_components.govee.sensor import GoveeTemperatureSensor

        state = SimpleNamespace(sensor_temperature=raw_value)
        coordinator = SimpleNamespace(
            config_entry=SimpleNamespace(options={"api_temperature_unit": api_unit}),
            # Developer-API thermometer, not a BFF-sourced one (#141).
            is_bff_thermometer=lambda _device_id: False,
            # No fahOpen flag seen for this device unless a test says otherwise.
            account_temperature_unit=lambda _device_id: account_unit,
        )
        stub = SimpleNamespace(
            device_state=state,
            coordinator=coordinator,
            _device=SimpleNamespace(sku=sku),
            _device_id="AA:BB:CC:DD:EE:FF:00:11",
        )
        return GoveeTemperatureSensor.native_value.fget(stub)

    def test_celsius_passthrough(self):
        assert self._make_sensor_stub(21.5, "celsius") == 21.5

    def test_celsius_forces_no_conversion_for_known_sku(self):
        # Explicit celsius overrides auto-detection for a °F-reporting SKU.
        assert self._make_sensor_stub(100.83, "celsius", sku="H5109") == 100.83

    def test_fahrenheit_converts(self):
        # 70°F -> 21.111…°C
        result = self._make_sensor_stub(70.0, "fahrenheit")
        assert abs(result - 21.111111) < 1e-4

    def test_fahrenheit_freezing(self):
        # 32°F -> 0°C
        assert abs(self._make_sensor_stub(32.0, "fahrenheit") - 0.0) < 1e-9

    def test_none_passthrough(self):
        assert self._make_sensor_stub(None, "celsius") is None
        assert self._make_sensor_stub(None, "fahrenheit") is None

    def test_auto_converts_known_fahrenheit_sku(self):
        # Issue #96: H5109 reports 100.83°F -> ~38.2°C under auto mode.
        result = self._make_sensor_stub(100.83, "auto", sku="H5109")
        assert abs(result - 38.238889) < 1e-4

    def test_auto_case_insensitive_sku_match(self):
        result = self._make_sensor_stub(100.83, "auto", sku="h5109")
        assert abs(result - 38.238889) < 1e-4

    def test_auto_passthrough_for_unknown_sku(self):
        # A SKU not in FAHRENHEIT_REPORTING_SKUS is trusted as °C under auto.
        assert self._make_sensor_stub(21.5, "auto", sku="H6072") == 21.5

    def test_default_when_option_missing(self):
        from types import SimpleNamespace

        from custom_components.govee.sensor import GoveeTemperatureSensor

        state = SimpleNamespace(sensor_temperature=100.83)
        coordinator = SimpleNamespace(
            config_entry=SimpleNamespace(options={}),
            is_bff_thermometer=lambda _device_id: False,
            account_temperature_unit=lambda _device_id: None,
        )
        stub = SimpleNamespace(
            device_state=state,
            coordinator=coordinator,
            _device=SimpleNamespace(sku="H5109"),
            _device_id="AA:BB:CC:DD:EE:FF:00:11",
        )
        # Default is auto -> known °F SKU converts.
        result = GoveeTemperatureSensor.native_value.fget(stub)
        assert abs(result - 38.238889) < 1e-4

    def test_bff_sourced_h5179_skips_fahrenheit_conversion(self):
        # H5179 is in FAHRENHEIT_REPORTING_SKUS for its Developer-API path, but
        # when its reading comes via BFF (already canonical °C from _bff_reading)
        # the conversion must NOT apply — else 4.9°C would be mangled (#141).
        from types import SimpleNamespace

        from custom_components.govee.sensor import GoveeTemperatureSensor

        state = SimpleNamespace(sensor_temperature=4.9)
        coordinator = SimpleNamespace(
            config_entry=SimpleNamespace(options={"api_temperature_unit": "auto"}),
            is_bff_thermometer=lambda _device_id: True,
            account_temperature_unit=lambda _device_id: None,
        )
        stub = SimpleNamespace(
            device_state=state,
            coordinator=coordinator,
            _device=SimpleNamespace(sku="H5179"),
            _device_id="AA:BB:CC:DD:EE:FF:00:11",
        )
        assert GoveeTemperatureSensor.native_value.fget(stub) == 4.9

    def test_h717a_kettle_auto_converts_fahrenheit(self):
        # Issue #115: H717A kettle reports 187°F under the °C-tagged unit
        # (187°C is impossible — water boils at 100°C). Auto mode converts to
        # ~86.1°C, the real tea temperature.
        result = self._make_sensor_stub(187.0, "auto", sku="H717A")
        assert abs(result - 86.111111) < 1e-4

    def test_h717a_celsius_override_passthrough(self):
        # An account whose Govee app is set to °C can opt out via the option.
        assert self._make_sensor_stub(86.0, "celsius", sku="H717A") == 86.0

    def test_h5106_air_quality_monitor_auto_converts_fahrenheit(self):
        # Issue #116: reporter diagnostics show H5106 reports a plain °F float
        # (73.76°F ≈ 23.2°C), surfaced under the °C unit as a "wrong large
        # value". NOT centi-encoded — just Fahrenheit. Auto mode converts it.
        result = self._make_sensor_stub(73.76, "auto", sku="H5106")
        assert abs(result - 23.2) < 1e-1

    def test_h5140_co2_monitor_auto_converts_fahrenheit(self):
        # H5140 reports 73.94°F ≈ 23.3°C (issue #116 diagnostics).
        result = self._make_sensor_stub(73.94, "auto", sku="H5140")
        assert abs(result - 23.3) < 1e-1

    def test_h5106_celsius_override_passthrough(self):
        # An account whose Govee app is set to °C can opt out via the option.
        assert self._make_sensor_stub(23.2, "celsius", sku="H5106") == 23.2

    def test_h5220_gateway_thermometer_auto_converts_fahrenheit(self):
        # Issue #128 follow-up: H5220 reports ~75°F under the °C-tagged unit
        # (reporter's actual room temp ~75-77°F / ~24°C). A captured BFF
        # sample independently confirms the device itself is °F-configured
        # ("fahOpen": true).
        result = self._make_sensor_stub(75.0, "auto", sku="H5220")
        assert abs(result - 23.888889) < 1e-4

    def test_h5220_celsius_override_passthrough(self):
        # An account whose Govee app is set to °C can opt out via the option.
        assert self._make_sensor_stub(23.9, "celsius", sku="H5220") == 23.9

    def test_h5111_freezer_thermometer_auto_converts_fahrenheit(self):
        # H5111 fridge/freezer thermometer reports °F under the °C-tagged
        # unit: app shows ~6.1°F, HA surfaced 43.3°F (6.28°C→°F). Auto mode
        # converts the raw 6.28°F back to ~-14.3°C so HA renders 6.3°F.
        result = self._make_sensor_stub(6.28, "auto", sku="H5111")
        assert abs(result - (-14.288889)) < 1e-4

    def test_h5111_celsius_override_passthrough(self):
        # An account whose Govee app is set to °C can opt out via the option.
        assert self._make_sensor_stub(-14.3, "celsius", sku="H5111") == -14.3

    def test_h5310_pool_thermometer_auto_converts_fahrenheit(self):
        # Issue #157: an 88°F pool surfaced as ~191°F because the Developer API
        # had already returned °F. With no fahOpen flag to go on, the SKU
        # allowlist converts it.
        result = self._make_sensor_stub(88.34, "auto", sku="H5310")
        assert abs(result - 31.3) < 0.05

    def test_account_fahrenheit_hint_converts_unknown_sku(self):
        # A device outside the allowlist still converts when Govee tells us the
        # account reports in °F (issue #157).
        result = self._make_sensor_stub(
            70.0, "auto", sku="H6072", account_unit="fahrenheit"
        )
        assert abs(result - 21.111111) < 1e-4

    def test_account_celsius_hint_beats_sku_allowlist(self):
        # The protection for °C accounts: an H5310 on a Celsius account reports
        # °C, and the fahOpen=false hint must stop the allowlist from mangling
        # it (issue #157 vs #151).
        assert (
            self._make_sensor_stub(29.4, "auto", sku="H5310", account_unit="celsius")
            == 29.4
        )

    def test_explicit_option_still_beats_account_hint(self):
        # "celsius"/"fahrenheit" are user overrides — they outrank any hint.
        assert (
            self._make_sensor_stub(
                88.34, "celsius", sku="H5310", account_unit="fahrenheit"
            )
            == 88.34
        )


class TestAccountTemperatureUnit:
    """coordinator.account_temperature_unit maps BFF fahOpen -> unit (#157)."""

    def _coordinator(self):
        from custom_components.govee.coordinator import GoveeCoordinator

        coordinator = GoveeCoordinator.__new__(GoveeCoordinator)
        coordinator._display_fahrenheit = {}
        return coordinator

    def test_unknown_device_returns_none(self):
        assert self._coordinator().account_temperature_unit("dev") is None

    def test_fah_open_true_is_fahrenheit(self):
        coordinator = self._coordinator()
        coordinator._note_display_unit("dev", {"fah_open": True})
        assert coordinator.account_temperature_unit("dev") == "fahrenheit"

    def test_fah_open_false_is_celsius(self):
        coordinator = self._coordinator()
        coordinator._note_display_unit("dev", {"fah_open": False})
        assert coordinator.account_temperature_unit("dev") == "celsius"

    def test_missing_flag_is_not_recorded(self):
        # A device Govee didn't report fahOpen for must stay unknown, so the
        # SKU allowlist keeps its say rather than being overridden by a guess.
        coordinator = self._coordinator()
        coordinator._note_display_unit("dev", {"fah_open": None})
        coordinator._note_display_unit("dev2", {})
        assert coordinator.account_temperature_unit("dev") is None
        assert coordinator.account_temperature_unit("dev2") is None


class TestSyntheticThermometer:
    """GoveeDevice.synthetic_thermometer backs BFF-only H5301 discovery (#86)."""

    def test_synthesizes_thermometer_with_sensor_capabilities(self):
        device = GoveeDevice.synthetic_thermometer(
            device_id="AA:BB:CC:DD:EE:FF:00:11", sku="H5301", name="Office"
        )
        assert device.device_id == "AA:BB:CC:DD:EE:FF:00:11"
        assert device.sku == "H5301"
        assert device.name == "Office"
        assert device.device_type == DEVICE_TYPE_THERMOMETER
        assert device.is_thermometer
        assert device.supports_temperature_sensor
        assert device.supports_humidity_sensor
        assert not device.is_group

    def test_temp_only_sku_omits_humidity_capability(self):
        # H5310 pool thermometer has no hygrometer -> no humidity entity (#97).
        device = GoveeDevice.synthetic_thermometer(
            device_id="03:55:01:25:00:00:00:0D", sku="H5310", name="Pool"
        )
        assert device.supports_temperature_sensor
        assert not device.supports_humidity_sensor

    def test_hub_device_id_default_empty(self):
        device = GoveeDevice.synthetic_thermometer(
            device_id="AA:BB:CC:DD:EE:FF:00:11", sku="H5301", name="Office"
        )
        assert device.hub_device_id == ""

    def test_hub_device_id_propagates(self):
        # H5310 via H5044 -> hub_device_id carried for via_device linkage (#86).
        device = GoveeDevice.synthetic_thermometer(
            device_id="03:55:01:25:00:00:00:0D",
            sku="H5310",
            name="Pool",
            hub_device_id="11:22:33:44:55:66:77:88",
        )
        assert device.hub_device_id == "11:22:33:44:55:66:77:88"


class TestBffReadingSentinel:
    """_bff_reading filters the 0xFFFF no-value sentinel (issue #97)."""

    def test_humidity_sentinel_returns_none(self):
        from custom_components.govee.api.auth import _BFF_HUMIDITY_KEYS, _bff_reading

        # H5310 with no hygrometer reports hum == 0xFFFF (65535 centi).
        assert _bff_reading({"hum": 65535}, _BFF_HUMIDITY_KEYS) is None

    def test_temperature_sentinel_returns_none(self):
        from custom_components.govee.api.auth import _BFF_TEMP_KEYS, _bff_reading

        assert _bff_reading({"tem": 65535}, _BFF_TEMP_KEYS) is None
        assert _bff_reading({"tem": 32767}, _BFF_TEMP_KEYS) is None

    def test_real_centi_values_still_descale(self):
        from custom_components.govee.api.auth import (
            _BFF_HUMIDITY_KEYS,
            _BFF_TEMP_KEYS,
            _bff_reading,
        )

        assert _bff_reading({"tem": 2640}, _BFF_TEMP_KEYS) == 26.4
        assert _bff_reading({"tem": -500}, _BFF_TEMP_KEYS) == -5.0
        assert _bff_reading({"hum": 5550}, _BFF_HUMIDITY_KEYS) == 55.5


class TestBffThermometerAvailability:
    """BFF thermo-hygrometer availability ignores flapping online (issue #97)."""

    def _available(self, *, is_bff, online, has_reading, update_success=True):
        from types import SimpleNamespace

        from custom_components.govee.sensor import GoveeTemperatureSensor

        state = (
            SimpleNamespace(online=online, sensor_temperature=26.4)
            if has_reading
            else None
        )
        coordinator = SimpleNamespace(
            last_update_success=update_success,
            is_bff_thermometer=lambda _id: is_bff,
        )
        stub = SimpleNamespace(
            _device_id="dev",
            coordinator=coordinator,
            device_state=state,
        )
        return GoveeTemperatureSensor.available.fget(stub)

    def test_available_when_online_false_but_reading_present(self):
        # H5310: online flaps false yet a fresh 26.4 reading exists -> available.
        assert self._available(is_bff=True, online=False, has_reading=True) is True

    def test_unavailable_when_no_reading(self):
        assert self._available(is_bff=True, online=False, has_reading=False) is False

    def test_unavailable_when_coordinator_failed(self):
        assert (
            self._available(
                is_bff=True, online=True, has_reading=True, update_success=False
            )
            is False
        )


class TestThermoBatterySensor:
    """GoveeThermoBatterySensor surfaces BFF battery level (issue #86)."""

    def _native(self, battery):
        from types import SimpleNamespace

        from custom_components.govee.sensor import GoveeThermoBatterySensor

        state = (
            SimpleNamespace(battery=battery) if battery is not None else None
        )
        stub = SimpleNamespace(device_state=state)
        return GoveeThermoBatterySensor.native_value.fget(stub)

    def test_reports_battery_level(self):
        assert self._native(88) == 88

    def test_none_when_no_state(self):
        assert self._native(None) is None

    def test_inherits_bff_availability_mixin(self):
        from custom_components.govee.sensor import (
            GoveeThermoBatterySensor,
            _BffThermometerAvailabilityMixin,
        )

        assert issubclass(
            GoveeThermoBatterySensor, _BffThermometerAvailabilityMixin
        )


class TestThermoDeviceInfoViaDevice:
    """GoveeEntity.device_info links gateway-bridged thermo to its hub (#86)."""

    def _device_info(self, hub_device_id):
        from types import SimpleNamespace

        from custom_components.govee.entity import GoveeEntity

        device = GoveeDevice.synthetic_thermometer(
            device_id="03:55:01:25:00:00:00:0D",
            sku="H5310",
            name="Pool",
            hub_device_id=hub_device_id,
        )
        stub = SimpleNamespace(
            _device=device,
            _infer_area_from_name=GoveeEntity._infer_area_from_name,
        )
        return GoveeEntity.device_info.fget(stub)

    def test_via_device_set_when_bridged(self):
        info = self._device_info("11:22:33:44:55:66:77:88")
        assert info["via_device"] == ("govee", "11:22:33:44:55:66:77:88")

    def test_no_via_device_when_not_bridged(self):
        info = self._device_info("")
        assert "via_device" not in info


class TestDeveloperThermometerBattery:
    """H5110-style Developer-API thermometers get battery from the BFF (#83).

    Battery is absent from the Developer API for these BLE-bridged sensors but
    present in the BFF deviceSettings; the coordinator applies it and the sensor
    platform creates a battery entity when present.
    """

    def _thermo_device(self, did="AA:BB:CC:DD:EE:FF:51:10"):
        from custom_components.govee.models import GoveeCapability, GoveeDevice
        from custom_components.govee.models.device import (
            CAPABILITY_PROPERTY,
            DEVICE_TYPE_THERMOMETER,
            INSTANCE_SENSOR_HUMIDITY,
            INSTANCE_SENSOR_TEMPERATURE,
        )

        return GoveeDevice(
            device_id=did,
            sku="H5110",
            name="Closet",
            device_type=DEVICE_TYPE_THERMOMETER,
            capabilities=(
                GoveeCapability(CAPABILITY_PROPERTY, INSTANCE_SENSOR_TEMPERATURE, {}),
                GoveeCapability(CAPABILITY_PROPERTY, INSTANCE_SENSOR_HUMIDITY, {}),
            ),
        )

    def test_apply_bff_thermo_battery_sets_state(self):
        from types import SimpleNamespace

        from custom_components.govee.coordinator import GoveeCoordinator
        from custom_components.govee.models import GoveeDeviceState

        did = "AA:BB:CC:DD:EE:FF:51:10"
        state = GoveeDeviceState(device_id=did)
        fake = SimpleNamespace(
            _states={did: state}, _devices={did: self._thermo_device(did)}
        )
        GoveeCoordinator._apply_bff_thermo_battery(
            fake, {did: {"tem": 2200, "hum": 500, "battery": 87}}
        )
        assert state.battery == 87

    def test_apply_bff_thermo_battery_skips_when_absent(self):
        from types import SimpleNamespace

        from custom_components.govee.coordinator import GoveeCoordinator
        from custom_components.govee.models import GoveeDeviceState

        did = "AA:BB:CC:DD:EE:FF:51:10"
        state = GoveeDeviceState(device_id=did)
        fake = SimpleNamespace(
            _states={did: state}, _devices={did: self._thermo_device(did)}
        )
        GoveeCoordinator._apply_bff_thermo_battery(
            fake, {did: {"tem": 2200, "hum": 500, "battery": None}}
        )
        assert state.battery is None

    def test_apply_bff_thermo_battery_skips_mains_powered(self):
        # #125/#114: a mains-powered device (e.g. H5106 air-quality monitor)
        # reports a bogus constant battery in the BFF — don't surface it.
        from types import SimpleNamespace

        from custom_components.govee.coordinator import GoveeCoordinator
        from custom_components.govee.models import GoveeDeviceState
        from custom_components.govee.models.device import (
            DEVICE_TYPE_AIR_QUALITY_MONITOR,
        )

        did = "AA:BB:CC:DD:EE:FF:51:06"
        state = GoveeDeviceState(device_id=did)
        device = GoveeDevice(
            device_id=did,
            sku="H5106",
            name="AQI Monitor",
            device_type=DEVICE_TYPE_AIR_QUALITY_MONITOR,
            capabilities=(),
        )
        fake = SimpleNamespace(_states={did: state}, _devices={did: device})
        GoveeCoordinator._apply_bff_thermo_battery(fake, {did: {"battery": 100}})
        assert state.battery is None

    def test_apply_bff_thermo_battery_no_reload_by_default(self):
        # allow_reload defaults to False (the initial-discovery call site) —
        # a fake `self` missing hass/_config_entry/_battery_reload_scheduled
        # must not be touched.
        from types import SimpleNamespace

        from custom_components.govee.coordinator import GoveeCoordinator
        from custom_components.govee.models import GoveeDeviceState

        did = "AA:BB:CC:DD:EE:FF:51:10"
        state = GoveeDeviceState(device_id=did)
        fake = SimpleNamespace(
            _states={did: state}, _devices={did: self._thermo_device(did)}
        )
        GoveeCoordinator._apply_bff_thermo_battery(fake, {did: {"battery": 87}})
        assert state.battery == 87

    def test_apply_bff_thermo_battery_reloads_on_first_battery_seen(self):
        # Issue #132: a device with no battery at startup (sensor.py's
        # one-shot gate skipped it) later gets one from the periodic BFF poll
        # -> schedule a reload so sensor.py gets a fresh chance to create it.
        from unittest.mock import MagicMock
        from types import SimpleNamespace

        from custom_components.govee.coordinator import GoveeCoordinator
        from custom_components.govee.models import GoveeDeviceState

        did = "AA:BB:CC:DD:EE:FF:51:10"
        state = GoveeDeviceState(device_id=did)  # battery=None, as at startup
        config_entries = MagicMock()
        fake = SimpleNamespace(
            _states={did: state},
            _devices={did: self._thermo_device(did)},
            _battery_reload_scheduled=False,
            hass=SimpleNamespace(config_entries=config_entries),
            _config_entry=SimpleNamespace(entry_id="test_entry"),
        )

        GoveeCoordinator._apply_bff_thermo_battery(
            fake, {did: {"battery": 87}}, allow_reload=True
        )

        assert state.battery == 87
        assert fake._battery_reload_scheduled is True
        config_entries.async_schedule_reload.assert_called_once_with("test_entry")

    def test_apply_bff_thermo_battery_no_reload_when_battery_already_known(self):
        # A device that already has a battery reading updating to a new value
        # is a routine refresh, not a "missed at startup" case -> no reload.
        from unittest.mock import MagicMock
        from types import SimpleNamespace

        from custom_components.govee.coordinator import GoveeCoordinator
        from custom_components.govee.models import GoveeDeviceState

        did = "AA:BB:CC:DD:EE:FF:51:10"
        state = GoveeDeviceState(device_id=did, battery=90)
        config_entries = MagicMock()
        fake = SimpleNamespace(
            _states={did: state},
            _devices={did: self._thermo_device(did)},
            _battery_reload_scheduled=False,
            hass=SimpleNamespace(config_entries=config_entries),
            _config_entry=SimpleNamespace(entry_id="test_entry"),
        )

        GoveeCoordinator._apply_bff_thermo_battery(
            fake, {did: {"battery": 88}}, allow_reload=True
        )

        assert state.battery == 88
        assert fake._battery_reload_scheduled is False
        config_entries.async_schedule_reload.assert_not_called()

    def test_apply_bff_thermo_battery_reload_scheduled_only_once(self):
        # A guard flag already set (a reload is pending) must not queue a
        # second one, mirroring the leak-sensor _bff_reload_scheduled pattern.
        from unittest.mock import MagicMock
        from types import SimpleNamespace

        from custom_components.govee.coordinator import GoveeCoordinator
        from custom_components.govee.models import GoveeDeviceState

        did = "AA:BB:CC:DD:EE:FF:51:10"
        state = GoveeDeviceState(device_id=did)
        config_entries = MagicMock()
        fake = SimpleNamespace(
            _states={did: state},
            _devices={did: self._thermo_device(did)},
            _battery_reload_scheduled=True,  # already pending
            hass=SimpleNamespace(config_entries=config_entries),
            _config_entry=SimpleNamespace(entry_id="test_entry"),
        )

        GoveeCoordinator._apply_bff_thermo_battery(
            fake, {did: {"battery": 87}}, allow_reload=True
        )

        assert state.battery == 87
        config_entries.async_schedule_reload.assert_not_called()

    def test_apply_bff_thermo_battery_skips_mains_powered_sku(self):
        # #114: the H5106 reports a bogus battery but its device_type is NOT one
        # of the mains types, so it's suppressed by SKU instead (@k-perri).
        from types import SimpleNamespace

        from custom_components.govee.coordinator import GoveeCoordinator
        from custom_components.govee.models import GoveeDeviceState

        did = "AA:BB:CC:DD:EE:FF:51:06"
        state = GoveeDeviceState(device_id=did)
        device = GoveeDevice(
            device_id=did,
            sku="H5106",
            name="AQI Monitor",
            device_type=DEVICE_TYPE_THERMOMETER,  # not a mains device_type
            capabilities=(),
        )
        fake = SimpleNamespace(_states={did: state}, _devices={did: device})
        GoveeCoordinator._apply_bff_thermo_battery(fake, {did: {"battery": 100}})
        assert state.battery is None

    def test_bff_water_full_not_applied(self):
        # #118 follow-up: BFF deviceSettings.waterFull is the app's "Full
        # Bucket Alert" notification SETTING, not live tank state — it must
        # never populate water_full (live state comes from the OpenAPI
        # waterFullEvent push instead; see test_openapi_events.py).
        from types import SimpleNamespace

        from custom_components.govee.coordinator import GoveeCoordinator
        from custom_components.govee.models import GoveeDeviceState
        from custom_components.govee.models.device import DEVICE_TYPE_DEHUMIDIFIER

        did = "AA:BB:CC:DD:EE:FF:71:52"
        state = GoveeDeviceState(device_id=did)
        device = GoveeDevice(
            device_id=did,
            sku="H7152",
            name="Dehumidifier",
            device_type=DEVICE_TYPE_DEHUMIDIFIER,
            capabilities=(),
        )
        fake = SimpleNamespace(_states={did: state}, _devices={did: device})
        GoveeCoordinator._apply_bff_thermo_battery(fake, {did: {"water_full": 1}})
        assert state.water_full is None

    async def test_battery_sensor_created_when_battery_present(self):
        from unittest.mock import MagicMock

        from custom_components.govee import sensor as sensor_mod
        from custom_components.govee.models import GoveeDeviceState

        did = "AA:BB:CC:DD:EE:FF:51:10"
        device = self._thermo_device(did)
        state = GoveeDeviceState(device_id=did)
        state.battery = 87

        coordinator = MagicMock()
        coordinator.devices = {did: device}
        coordinator.get_state = MagicMock(return_value=state)
        coordinator.is_bff_thermometer = MagicMock(return_value=False)  # Developer-API
        # Not a hub-discovered leak sensor, so no competing battery entity (#145).
        coordinator.is_bff_leak_sensor = MagicMock(return_value=False)
        coordinator.mqtt_client = None
        coordinator.leak_sensors = {}
        coordinator.register_thermo_hubs = MagicMock()
        coordinator.register_leak_hubs = MagicMock()
        entry = MagicMock()
        entry.runtime_data = coordinator
        added: list = []
        await sensor_mod.async_setup_entry(MagicMock(), entry, lambda e: added.extend(e))

        battery = [e for e in added if type(e).__name__ == "GoveeThermoBatterySensor"]
        assert len(battery) == 1
        assert battery[0].unique_id == f"{did}_battery"

    async def test_no_battery_sensor_when_absent(self):
        from unittest.mock import MagicMock

        from custom_components.govee import sensor as sensor_mod
        from custom_components.govee.models import GoveeDeviceState

        did = "AA:BB:CC:DD:EE:FF:51:10"
        device = self._thermo_device(did)
        state = GoveeDeviceState(device_id=did)  # battery None

        coordinator = MagicMock()
        coordinator.devices = {did: device}
        coordinator.get_state = MagicMock(return_value=state)
        coordinator.is_bff_thermometer = MagicMock(return_value=False)
        coordinator.mqtt_client = None
        coordinator.leak_sensors = {}
        coordinator.register_thermo_hubs = MagicMock()
        coordinator.register_leak_hubs = MagicMock()
        entry = MagicMock()
        entry.runtime_data = coordinator
        added: list = []
        await sensor_mod.async_setup_entry(MagicMock(), entry, lambda e: added.extend(e))

        assert not [e for e in added if type(e).__name__ == "GoveeThermoBatterySensor"]


class TestResolveFahrenheitDeviceUnitHint:
    """auto mode: explicit device-reported unit metadata beats the static
    SKU allowlist (H713B heater, issue #129)."""

    def test_auto_with_fahrenheit_hint_converts(self):
        from custom_components.govee.const import resolve_fahrenheit_conversion

        assert resolve_fahrenheit_conversion("H713B", "auto", "Fahrenheit") is True

    def test_auto_with_celsius_hint_beats_allowlist(self):
        from custom_components.govee.const import resolve_fahrenheit_conversion

        # H5179 is in FAHRENHEIT_REPORTING_SKUS, but the device itself says
        # Celsius — explicit metadata wins.
        assert resolve_fahrenheit_conversion("H5179", "auto", "Celsius") is False

    def test_auto_without_hint_uses_allowlist(self):
        from custom_components.govee.const import resolve_fahrenheit_conversion

        assert resolve_fahrenheit_conversion("H5179", "auto", None) is True
        assert resolve_fahrenheit_conversion("H713B", "auto", None) is False

    def test_explicit_override_ignores_hint(self):
        from custom_components.govee.const import resolve_fahrenheit_conversion

        assert resolve_fahrenheit_conversion("H713B", "celsius", "Fahrenheit") is False
        assert resolve_fahrenheit_conversion("H713B", "fahrenheit", "Celsius") is True


class TestBffThermoHandover:
    """A BFF entry with no reading must not silence a working Developer poll.

    Issue #151: an H5310 behind an H5044 is listed by the BFF with an empty
    ``lastDeviceData``. Claiming it there suppressed ``/device/state`` for good,
    so the sensor sat at ``unknown`` forever.
    """

    def _coordinator(self, devices=(), states=None):
        from unittest.mock import MagicMock

        from custom_components.govee.coordinator import GoveeCoordinator

        coordinator = GoveeCoordinator.__new__(GoveeCoordinator)
        coordinator._devices = {d: MagicMock() for d in devices}
        coordinator._states = dict(states or {})
        coordinator._bff_thermometer_ids = set()
        coordinator._bff_thermo_pending = set()
        coordinator._display_fahrenheit = {}
        coordinator._bff_thermo_hubs = {}
        coordinator._sensor_reading_changed_at = {}
        coordinator.hass = MagicMock()
        return coordinator

    def _sensor(self, device_id, temperature=None, humidity=None):
        return {
            "device_id": device_id,
            "name": "Pool",
            "sku": "H5310",
            "temperature": temperature,
            "humidity": humidity,
            "battery": None,
            "online": True,
            "hub_device_id": "",
            "hub_sku": "",
            "fah_open": None,
            "sw_version": "",
            "hw_version": "",
        }

    async def _discover(self, coordinator, sensors):
        """Drive _discover_bff_thermometers with a stubbed auth client."""
        from unittest.mock import AsyncMock, MagicMock, patch

        coordinator._iot_credentials = MagicMock(token="tok")
        coordinator._schedule_bff_poll = MagicMock()
        coordinator._ensure_transport_health = MagicMock()

        auth_client = MagicMock()
        auth_client.fetch_bff_thermo_hygrometers = AsyncMock(return_value=sensors)
        auth_client.bff_device_census = MagicMock(return_value=[])
        auth_client.bff_response_skeleton = MagicMock(return_value={})
        auth_client.bff_device_values = MagicMock(return_value=[])
        ctx = MagicMock()
        ctx.__aenter__ = AsyncMock(return_value=auth_client)
        ctx.__aexit__ = AsyncMock(return_value=False)

        with patch(
            "custom_components.govee.coordinator.GoveeAuthClient", return_value=ctx
        ):
            await coordinator._discover_bff_thermometers()

    async def _refresh(self, coordinator, sensors):
        from unittest.mock import AsyncMock, MagicMock, patch

        coordinator._iot_credentials = MagicMock(token="tok")
        coordinator._record_transport_success = MagicMock()
        coordinator._note_sensor_reading_change = MagicMock()
        coordinator.async_set_updated_data = MagicMock()

        auth_client = MagicMock()
        auth_client.fetch_bff_thermo_hygrometers = AsyncMock(return_value=sensors)
        ctx = MagicMock()
        ctx.__aenter__ = AsyncMock(return_value=auth_client)
        ctx.__aexit__ = AsyncMock(return_value=False)

        with patch(
            "custom_components.govee.coordinator.GoveeAuthClient", return_value=ctx
        ):
            await coordinator._refresh_bff_thermometers()

    @pytest.mark.asyncio
    async def test_reading_less_entry_leaves_developer_poll_alone(self):
        coordinator = self._coordinator(devices=["pool"])
        await self._discover(coordinator, [self._sensor("pool")])

        assert "pool" not in coordinator._bff_thermometer_ids
        assert "pool" in coordinator._bff_thermo_pending

    @pytest.mark.asyncio
    async def test_entry_with_a_reading_is_claimed(self):
        coordinator = self._coordinator(devices=["pool"])
        await self._discover(coordinator, [self._sensor("pool", temperature=29.4)])

        assert "pool" in coordinator._bff_thermometer_ids
        assert "pool" not in coordinator._bff_thermo_pending

    @pytest.mark.asyncio
    async def test_pending_device_is_promoted_once_a_reading_arrives(self):
        state = GoveeDeviceState.create_empty("pool")
        coordinator = self._coordinator(devices=["pool"], states={"pool": state})
        await self._discover(coordinator, [self._sensor("pool")])
        assert "pool" in coordinator._bff_thermo_pending

        await self._refresh(coordinator, [self._sensor("pool", temperature=29.4)])

        assert "pool" in coordinator._bff_thermometer_ids
        assert "pool" not in coordinator._bff_thermo_pending
        assert coordinator._states["pool"].sensor_temperature == 29.4

    @pytest.mark.asyncio
    async def test_pending_device_stays_pending_while_bff_is_empty(self):
        state = GoveeDeviceState.create_empty("pool")
        coordinator = self._coordinator(devices=["pool"], states={"pool": state})
        await self._discover(coordinator, [self._sensor("pool")])

        await self._refresh(coordinator, [self._sensor("pool")])

        assert coordinator._bff_thermo_pending == {"pool"}
        assert not coordinator._bff_thermometer_ids

    @pytest.mark.asyncio
    async def test_bff_only_device_is_still_synthesized_without_a_reading(self):
        # A device absent from the Developer API has no other source, so it must
        # keep being created even when the first BFF poll is empty (#86).
        coordinator = self._coordinator(devices=[])
        await self._discover(coordinator, [self._sensor("pool")])

        assert "pool" in coordinator._devices
        assert "pool" in coordinator._bff_thermometer_ids


class TestBatteryCandidateDevices:
    """The BFF poll must keep looking for a battery that hasn't arrived yet.

    Issue #132: an H5109 behind an H5042 lost its battery entity. Battery for
    gateway-bridged thermometers only ever comes from the BFF, and nothing
    retried once the first pass came back empty.
    """

    def _coordinator(self, devices, states=None):
        from custom_components.govee.coordinator import GoveeCoordinator

        coordinator = GoveeCoordinator.__new__(GoveeCoordinator)
        coordinator._devices = devices
        coordinator._states = dict(states or {})
        return coordinator

    def _thermometer(self, sku="H5109"):
        return GoveeDevice(
            device_id="11:22:33:44:55:66:77:88",
            sku=sku,
            name="Garage",
            device_type=DEVICE_TYPE_THERMOMETER,
            capabilities=(
                GoveeCapability(
                    type=CAPABILITY_PROPERTY,
                    instance=INSTANCE_SENSOR_TEMPERATURE,
                    parameters={},
                ),
            ),
            is_group=False,
        )

    def test_thermometer_without_a_battery_reading_is_a_candidate(self):
        device = self._thermometer()
        coordinator = self._coordinator({device.device_id: device})
        assert coordinator._battery_candidate_devices() == {device.device_id}

    def test_thermometer_that_already_has_battery_is_not_a_candidate(self):
        device = self._thermometer()
        state = GoveeDeviceState.create_empty(device.device_id)
        state.battery = 88
        coordinator = self._coordinator(
            {device.device_id: device}, {device.device_id: state}
        )
        assert coordinator._battery_candidate_devices() == set()

    def test_mains_powered_sku_is_never_a_candidate(self):
        # The H5106 reports a phantom battery: 100 while plugged in (#114).
        device = self._thermometer(sku="H5106")
        coordinator = self._coordinator({device.device_id: device})
        assert coordinator._battery_candidate_devices() == set()

    def test_non_thermometer_is_not_a_candidate(self):
        from custom_components.govee.models.device import (
            CAPABILITY_ON_OFF,
            INSTANCE_POWER,
        )

        light = GoveeDevice(
            device_id="00:11:22:33:44:55:66:77",
            sku="H6072",
            name="Lamp",
            device_type="devices.types.light",
            capabilities=(
                GoveeCapability(
                    type=CAPABILITY_ON_OFF, instance=INSTANCE_POWER, parameters={}
                ),
            ),
            is_group=False,
        )
        coordinator = self._coordinator({light.device_id: light})
        assert coordinator._battery_candidate_devices() == set()


class TestGatewayThermoFrameRouting:
    """Applying a decoded H5044 thermo frame to the right entity (issue #151).

    The frames name their sub-device by gateway slot only, so routing depends
    on the ``sno`` the BFF device list reports for each thermometer.
    """

    HUB = "07:23:5C:E7:53:5F:6F:0A"
    DEV = "03:55:01:25:00:00:00:0B:FF:FF:00:41:FF:FF:00:33"

    def _coordinator(self, *, options=None, sku="H5310"):
        from types import SimpleNamespace

        from custom_components.govee.coordinator import GoveeCoordinator
        from custom_components.govee.transport_health import TransportHealthTracker

        coordinator = GoveeCoordinator.__new__(GoveeCoordinator)
        coordinator._config_entry = SimpleNamespace(options=options or {})
        coordinator._devices = {
            self.DEV: GoveeDevice.synthetic_thermometer(
                device_id=self.DEV, sku=sku, name="Pool", hub_device_id=self.HUB
            )
        }
        coordinator._states = {}
        coordinator._sno_to_thermo_id = {}
        coordinator._sensor_reading_changed_at = {}
        coordinator._display_fahrenheit = {}
        coordinator._bff_thermometer_ids = set()
        coordinator._transport = TransportHealthTracker()
        coordinator.async_set_updated_data = lambda _data: None
        return coordinator

    def _frame(self, *, slot=0, temperature_c=24.9, battery=100):
        return {
            "_thermo_frame": True,
            "hub_device_id": self.HUB,
            "sensor_slot": slot,
            "temperature_c": temperature_c,
            "battery": battery,
            "frame_ts": 0x6A7ECA3C,
        }

    def test_slot_is_mapped_from_bff_listing(self):
        coordinator = self._coordinator()
        coordinator._note_thermo_slot(self.DEV, {"hub_device_id": self.HUB, "sno": 0})
        assert coordinator._sno_to_thermo_id == {(self.HUB, 0): self.DEV}

    def test_listing_without_gateway_is_not_mapped(self):
        """A direct-WiFi thermometer has no gateway slot to route to."""
        coordinator = self._coordinator()
        coordinator._note_thermo_slot(self.DEV, {"hub_device_id": "", "sno": 0})
        coordinator._note_thermo_slot(self.DEV, {"hub_device_id": self.HUB})
        assert coordinator._sno_to_thermo_id == {}

    def test_frame_lands_on_the_mapped_device(self):
        coordinator = self._coordinator()
        coordinator._note_thermo_slot(self.DEV, {"hub_device_id": self.HUB, "sno": 0})
        coordinator._handle_thermo_frame(self._frame())

        state = coordinator._states[self.DEV]
        assert state.battery == 100
        assert state.online is True
        assert self.DEV in coordinator._sensor_reading_changed_at

    def test_unmapped_slot_is_dropped(self):
        """A frame for a slot we have no thermometer for creates no state."""
        coordinator = self._coordinator()
        coordinator._handle_thermo_frame(self._frame(slot=3))
        assert coordinator._states == {}

    def test_reading_stored_as_fahrenheit_for_fahrenheit_skus(self):
        """The H5310's entity converts °F→°C, so the frame must store °F.

        Writing the decoded 24.9 °C straight through would surface the pool at
        -4 °C — the same double-conversion class as #96/#83.
        """
        coordinator = self._coordinator()
        coordinator._note_thermo_slot(self.DEV, {"hub_device_id": self.HUB, "sno": 0})
        coordinator._handle_thermo_frame(self._frame(temperature_c=24.9))

        stored = coordinator._states[self.DEV].sensor_temperature
        assert abs(stored - 76.82) < 0.01
        # Round-trips back to the decoded value through the entity's conversion.
        assert abs((stored - 32.0) * (5.0 / 9.0) - 24.9) < 0.01

    def test_reading_stored_as_celsius_when_account_reports_celsius(self):
        """A °C account's fahOpen=false hint wins over the SKU allowlist."""
        coordinator = self._coordinator()
        coordinator._note_display_unit(self.DEV, {"fah_open": False})
        coordinator._note_thermo_slot(self.DEV, {"hub_device_id": self.HUB, "sno": 0})
        coordinator._handle_thermo_frame(self._frame(temperature_c=24.9))
        assert coordinator._states[self.DEV].sensor_temperature == 24.9

    def test_reading_stored_as_celsius_for_bff_owned_device(self):
        """A BFF-owned device's entity trusts the stored value as °C."""
        coordinator = self._coordinator()
        coordinator._bff_thermometer_ids.add(self.DEV)
        coordinator._note_thermo_slot(self.DEV, {"hub_device_id": self.HUB, "sno": 0})
        coordinator._handle_thermo_frame(self._frame(temperature_c=24.9))
        assert coordinator._states[self.DEV].sensor_temperature == 24.9

    def test_frame_records_mqtt_transport_health(self):
        """The reading arrived over MQTT — diagnostics should say so.

        The #151 reporter saw ``mqtt.last_received = null`` while the frames
        were being received and discarded.
        """
        coordinator = self._coordinator()
        coordinator._note_thermo_slot(self.DEV, {"hub_device_id": self.HUB, "sno": 0})
        coordinator._handle_thermo_frame(self._frame())

        health = coordinator.get_transport_health(self.DEV, "mqtt")
        assert health is not None
        assert health.last_success_ts is not None

    def test_unchanged_reading_does_not_restamp_change_time(self):
        """Last Reading is a last-*change* timestamp, not last-poll (#83)."""
        coordinator = self._coordinator()
        coordinator._note_thermo_slot(self.DEV, {"hub_device_id": self.HUB, "sno": 0})
        coordinator._handle_thermo_frame(self._frame(temperature_c=24.9))
        first = coordinator._sensor_reading_changed_at[self.DEV]

        coordinator._handle_thermo_frame(self._frame(temperature_c=24.9))
        assert coordinator._sensor_reading_changed_at[self.DEV] == first

        coordinator._handle_thermo_frame(self._frame(temperature_c=25.1))
        assert coordinator._sensor_reading_changed_at[self.DEV] > first

    def test_dispatched_from_the_mqtt_state_callback(self):
        """The frame reaches its handler through the normal MQTT entry point."""
        coordinator = self._coordinator()
        coordinator._note_thermo_slot(self.DEV, {"hub_device_id": self.HUB, "sno": 0})
        coordinator._on_mqtt_state_update(self.HUB, self._frame())
        assert coordinator._states[self.DEV].sensor_temperature is not None
