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

    def _make_sensor_stub(self, raw_value, api_unit, sku="H6072"):
        from types import SimpleNamespace

        from custom_components.govee.sensor import GoveeTemperatureSensor

        state = SimpleNamespace(sensor_temperature=raw_value)
        coordinator = SimpleNamespace(
            config_entry=SimpleNamespace(options={"api_temperature_unit": api_unit})
        )
        stub = SimpleNamespace(
            device_state=state,
            coordinator=coordinator,
            _device=SimpleNamespace(sku=sku),
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
        coordinator = SimpleNamespace(config_entry=SimpleNamespace(options={}))
        stub = SimpleNamespace(
            device_state=state,
            coordinator=coordinator,
            _device=SimpleNamespace(sku="H5109"),
        )
        # Default is auto -> known °F SKU converts.
        result = GoveeTemperatureSensor.native_value.fget(stub)
        assert abs(result - 38.238889) < 1e-4

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

    def test_apply_bff_water_full_from_bff(self):
        # #118: dehumidifier water-tank-full is sourced from BFF deviceSettings.
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
        assert state.water_full is True
        GoveeCoordinator._apply_bff_thermo_battery(fake, {did: {"water_full": 0}})
        assert state.water_full is False

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
