"""Coverage tests for the sensor platform.

Complements ``test_thermometer.py`` (conversion math through stubs) and
``test_connection_mode_sensor.py``: this file walks every entity-creation
branch of ``async_setup_entry`` and exercises the entity classes that had no
direct tests — the hub-level MQTT sensors, the per-probe and second-probe
temperature entities, the simple reading sensors, and the leak-sensor
diagnostics that ride the ``<domain>_leak_update`` dispatcher signal.
"""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest
from homeassistant.components.sensor import SensorDeviceClass
from homeassistant.const import EntityCategory, UnitOfTemperature
from homeassistant.helpers.dispatcher import DATA_DISPATCHER

from custom_components.govee.api.probe_thermometer import probes_for_sku
from custom_components.govee.const import CONF_API_TEMPERATURE_UNIT, DOMAIN, hub_device_identifier
from custom_components.govee.models import GoveeCapability, GoveeDevice, GoveeDeviceState, ProbeReading
from custom_components.govee.models.device import (
    CAPABILITY_PROPERTY,
    DEVICE_TYPE_DEHUMIDIFIER,
    DEVICE_TYPE_LIGHT,
    DEVICE_TYPE_PURIFIER,
    DEVICE_TYPE_THERMOMETER,
    INSTANCE_AIR_QUALITY,
    INSTANCE_CO2,
    INSTANCE_FILTER_LIFE,
    INSTANCE_SENSOR_HUMIDITY,
    INSTANCE_SENSOR_TEMPERATURE,
    GoveeLeakSensor,
    GoveeLeakSensorState,
)
from custom_components.govee.sensor import (
    GoveeAirQualitySensor,
    GoveeAllDataLastUpdatedSensor,
    GoveeCO2Sensor,
    GoveeConnectionModeSensor,
    GoveeDehumidifierModeSensor,
    GoveeFilterLifeSensor,
    GoveeHumiditySensor,
    GoveeLeakAlertStatusSensor,
    GoveeLeakBatterySensor,
    GoveeLeakDeviceAddressSensor,
    GoveeLeakHubAddressSensor,
    GoveeLeakLastWetSensor,
    GoveeMqttLastReceivedPerDeviceSensor,
    GoveeMqttLastReceivedSensor,
    GoveeMqttStatusSensor,
    GoveeProbeTemperatureSensor,
    GoveeSecondProbeTemperatureSensor,
    GoveeSensorReadingTimestampSensor,
    GoveeTemperatureSensor,
    GoveeThermoBatterySensor,
    async_setup_entry,
)

LEAK_SIGNAL = f"{DOMAIN}_leak_update"

THERMO_ID = "AA:BB:CC:DD:EE:FF:00:01"
CO2_ID = "AA:BB:CC:DD:EE:FF:00:02"
PURIFIER_ID = "AA:BB:CC:DD:EE:FF:00:03"
PUMP_ID = "AA:BB:CC:DD:EE:FF:00:04"
PROBE_ID = "AA:BB:CC:DD:EE:FF:00:05"
DUAL_ID = "AA:BB:CC:DD:EE:FF:00:06"
LIGHT_ID = "AA:BB:CC:DD:EE:FF:00:07"
GROUP_ID = "11825917"

HUB_A = "09:C2:60:74:F4:64:AB:FA"
HUB_B = "09:C2:60:74:F4:64:AB:FB"


def _prop(instance: str) -> GoveeCapability:
    return GoveeCapability(type=CAPABILITY_PROPERTY, instance=instance, parameters={})


def _device(device_id: str, sku: str, device_type: str, *caps: GoveeCapability, is_group: bool = False) -> GoveeDevice:
    return GoveeDevice(
        device_id=device_id,
        sku=sku,
        name=f"Device {sku}",
        device_type=device_type,
        capabilities=caps,
        is_group=is_group,
    )


def _thermometer(device_id: str = THERMO_ID, sku: str = "H5109") -> GoveeDevice:
    return _device(
        device_id,
        sku,
        DEVICE_TYPE_THERMOMETER,
        _prop(INSTANCE_SENSOR_TEMPERATURE),
        _prop(INSTANCE_SENSOR_HUMIDITY),
    )


def _leak_sensor(
    device_id: str = "01:32:7A:C4:06:03:0D:0C",
    hub_device_id: str = HUB_A,
    **versions: str,
) -> GoveeLeakSensor:
    return GoveeLeakSensor(
        device_id=device_id,
        name="Kitchen sink",
        sku="H5058",
        hub_device_id=hub_device_id,
        sno=3,
        **versions,
    )


def _leak_coordinator(sensor: GoveeLeakSensor, state: GoveeLeakSensorState | None) -> MagicMock:
    coordinator = MagicMock()
    coordinator.leak_sensors = {sensor.device_id: sensor}
    coordinator.leak_states = {} if state is None else {sensor.device_id: state}
    return coordinator


async def _added_with_mock_hass(entity: Any) -> MagicMock:
    """Run ``async_added_to_hass`` against a mock hass with a real data dict."""
    hass = MagicMock()
    hass.data = {}
    entity.hass = hass
    await entity.async_added_to_hass()
    return hass


async def _assert_leak_subscription(entity: Any) -> None:
    """The entity subscribes to the leak signal and unsubscribes on removal."""
    hass = await _added_with_mock_hass(entity)
    assert entity._handle_leak_update in hass.data[DATA_DISPATCHER][LEAK_SIGNAL]

    entity.async_write_ha_state = MagicMock()
    entity._handle_leak_update()
    entity.async_write_ha_state.assert_called_once()

    # async_on_remove registered exactly the dispatcher unsubscribe.
    assert len(entity._on_remove) == 1
    entity._on_remove[0]()
    assert LEAK_SIGNAL not in hass.data[DATA_DISPATCHER]


# --------------------------------------------------------------------------- #
# async_setup_entry — every entity-creation branch
# --------------------------------------------------------------------------- #


def _fleet() -> dict[str, GoveeDevice]:
    devices = [
        _thermometer(),
        _device(CO2_ID, "H5140", DEVICE_TYPE_THERMOMETER, _prop(INSTANCE_CO2)),
        _device(PURIFIER_ID, "H7126", DEVICE_TYPE_PURIFIER, _prop(INSTANCE_AIR_QUALITY), _prop(INSTANCE_FILTER_LIFE)),
        # Pump-model dehumidifier: temperature/humidity/pump-state are SKU-gated.
        _device(PUMP_ID, "H7152", DEVICE_TYPE_DEHUMIDIFIER),
        GoveeDevice.synthetic_probe_thermometer(PROBE_ID, "H5192", "Grill"),
        _device(DUAL_ID, "H5112", DEVICE_TYPE_THERMOMETER, _prop(INSTANCE_SENSOR_TEMPERATURE)),
        _device(LIGHT_ID, "H6072", DEVICE_TYPE_LIGHT),
        _device(GROUP_ID, "GROUP", "devices.types.group", is_group=True),
    ]
    return {device.device_id: device for device in devices}


def _fleet_coordinator(*, mqtt: bool) -> MagicMock:
    thermo_state = GoveeDeviceState.create_empty(THERMO_ID)
    thermo_state.battery = 80
    dual_state = GoveeDeviceState.create_empty(DUAL_ID)
    dual_state.sensor_temperature_2 = 4.0
    states = {THERMO_ID: thermo_state, DUAL_ID: dual_state}

    coordinator = MagicMock()
    coordinator.devices = _fleet()
    coordinator.mqtt_client = MagicMock() if mqtt else None
    coordinator.get_state.side_effect = states.get
    coordinator.is_bff_leak_sensor.return_value = False
    coordinator.leak_sensors = {
        sensor.device_id: sensor
        for sensor in (
            _leak_sensor("01:32:7A:C4:06:03:0D:0C", HUB_A),
            _leak_sensor("01:32:7A:C4:06:03:0D:0D", HUB_A),
            _leak_sensor("01:32:7A:C4:06:03:0D:0E", HUB_B),
            # A sensor Govee reported without a hub gets no hub-address entity.
            _leak_sensor("01:32:7A:C4:06:03:0D:0F", ""),
        )
    }
    return coordinator


async def _setup(coordinator: MagicMock) -> list[Any]:
    entry = MagicMock()
    entry.entry_id = "entry1"
    entry.runtime_data = coordinator
    added: list[Any] = []
    await async_setup_entry(MagicMock(), entry, lambda entities: added.extend(entities))
    return added


def _per_device_diagnostics(device_id: str, *, mqtt: bool) -> set[str]:
    ids = {
        f"{device_id}_connection_mode",
        f"{device_id}_all_data_last_updated",
        f"{device_id}_last_command_sent",
    }
    if mqtt:
        ids.add(f"{device_id}_mqtt_last_received")
    return ids


def _expected_unique_ids(*, mqtt: bool) -> set[str]:
    expected = {"entry1_rate_limit"}
    if mqtt:
        expected |= {"entry1_mqtt_status", "entry1_mqtt_last_received"}
    for device_id in (THERMO_ID, CO2_ID, PURIFIER_ID, PUMP_ID, PROBE_ID, DUAL_ID, LIGHT_ID):
        expected |= _per_device_diagnostics(device_id, mqtt=mqtt)
    expected.add(f"{GROUP_ID}_connection_mode")
    expected |= {
        f"{THERMO_ID}_temperature",
        f"{THERMO_ID}_humidity",
        f"{THERMO_ID}_reading_changed",
        f"{THERMO_ID}_battery",
        f"{CO2_ID}_co2",
        f"{PURIFIER_ID}_air_quality",
        f"{PURIFIER_ID}_filter_life",
        f"{PUMP_ID}_temperature",
        f"{PUMP_ID}_humidity",
        f"{PUMP_ID}_dehumidifier_mode",
        f"{PUMP_ID}_reading_changed",
        f"{DUAL_ID}_temperature",
        f"{DUAL_ID}_temperature_2",
        f"{DUAL_ID}_reading_changed",
    }
    expected |= {
        f"{PROBE_ID}_probe{probe}_{channel}" for probe in probes_for_sku("H5192") for channel in ("core", "ambient")
    }
    for leak_id in (
        "01:32:7A:C4:06:03:0D:0C",
        "01:32:7A:C4:06:03:0D:0D",
        "01:32:7A:C4:06:03:0D:0E",
        "01:32:7A:C4:06:03:0D:0F",
    ):
        expected |= {f"{leak_id}_battery", f"{leak_id}_last_wet", f"{leak_id}_alert_status", f"{leak_id}_address"}
    expected |= {f"{HUB_A}_address", f"{HUB_B}_address"}
    return expected


class TestSetupEntry:
    @pytest.mark.asyncio
    async def test_creates_every_entity_kind_with_mqtt(self):
        coordinator = _fleet_coordinator(mqtt=True)
        added = await _setup(coordinator)

        assert {entity.unique_id for entity in added} == _expected_unique_ids(mqtt=True)
        # No duplicates hide behind the set comparison.
        assert len(added) == len(_expected_unique_ids(mqtt=True))
        # Gateway hubs are registered before the entities are added so
        # via_device links resolve.
        coordinator.register_thermo_hubs.assert_called_once_with()
        coordinator.register_leak_hubs.assert_called_once_with()

    @pytest.mark.asyncio
    async def test_no_mqtt_sensors_without_a_client(self):
        added = await _setup(_fleet_coordinator(mqtt=False))

        assert {entity.unique_id for entity in added} == _expected_unique_ids(mqtt=False)
        assert not any(isinstance(entity, GoveeMqttLastReceivedPerDeviceSensor) for entity in added)
        assert not any(isinstance(entity, (GoveeMqttStatusSensor, GoveeMqttLastReceivedSensor)) for entity in added)

    @pytest.mark.asyncio
    async def test_probe_thermometer_gets_only_per_probe_entities(self):
        added = await _setup(_fleet_coordinator(mqtt=False))

        probe_entities = [entity for entity in added if isinstance(entity, GoveeProbeTemperatureSensor)]
        assert {(entity._probe, entity._channel) for entity in probe_entities} == {
            (probe, channel) for probe in probes_for_sku("H5192") for channel in ("core", "ambient")
        }
        assert all(entity._device.device_id == PROBE_ID for entity in probe_entities)
        # The generic temperature sensor would sit at unknown forever.
        assert not any(
            isinstance(entity, GoveeTemperatureSensor) and entity._device.device_id == PROBE_ID for entity in added
        )

    @pytest.mark.asyncio
    async def test_h5194_gets_all_four_probes_worth_of_entities(self):
        """The 4-probe sibling of the H5192 (issue #197) must get entities for
        every probe it actually has, not the H5192's two.
        """
        grill_id = "AA:BB:CC:DD:EE:FF:00:08"
        coordinator = MagicMock()
        coordinator.devices = {grill_id: GoveeDevice.synthetic_probe_thermometer(grill_id, "H5194", "Big Grill")}
        coordinator.mqtt_client = None
        coordinator.get_state.return_value = None
        coordinator.is_bff_leak_sensor.return_value = False
        coordinator.leak_sensors = {}

        added = await _setup(coordinator)

        probe_entities = [entity for entity in added if isinstance(entity, GoveeProbeTemperatureSensor)]
        assert {(entity._probe, entity._channel) for entity in probe_entities} == {
            (probe, channel) for probe in (1, 2, 3, 4) for channel in ("core", "ambient")
        }

    @pytest.mark.asyncio
    async def test_second_probe_entity_requires_a_reading(self):
        coordinator = _fleet_coordinator(mqtt=False)
        state = GoveeDeviceState.create_empty(DUAL_ID)  # probe 2 never reported
        coordinator.get_state.side_effect = {DUAL_ID: state}.get

        added = await _setup(coordinator)

        assert not any(isinstance(entity, GoveeSecondProbeTemperatureSensor) for entity in added)

    @pytest.mark.asyncio
    async def test_one_hub_address_entity_per_hub(self):
        added = await _setup(_fleet_coordinator(mqtt=False))

        hubs = [entity for entity in added if isinstance(entity, GoveeLeakHubAddressSensor)]
        assert sorted(entity.native_value for entity in hubs) == sorted([HUB_A, HUB_B])

    @pytest.mark.asyncio
    async def test_group_devices_get_only_the_connection_mode_sensor(self):
        added = await _setup(_fleet_coordinator(mqtt=True))

        for_group = [entity for entity in added if getattr(entity, "_device_id", None) == GROUP_ID]
        assert len(for_group) == 1
        assert isinstance(for_group[0], GoveeConnectionModeSensor)


# --------------------------------------------------------------------------- #
# Hub-level MQTT sensors
# --------------------------------------------------------------------------- #


class TestMqttStatusSensor:
    def _entity(self, client: Any) -> GoveeMqttStatusSensor:
        coordinator = MagicMock()
        coordinator.mqtt_client = client
        return GoveeMqttStatusSensor(coordinator, "entry1")

    def test_unavailable_without_a_client(self):
        entity = self._entity(None)
        assert entity.native_value == "unavailable"
        assert entity.unique_id == "entry1_mqtt_status"
        assert entity.device_info["identifiers"] == {(DOMAIN, hub_device_identifier("entry1"))}
        assert entity.device_class == SensorDeviceClass.ENUM
        assert entity.entity_category == EntityCategory.DIAGNOSTIC
        assert entity.options == ["connected", "disconnected", "unavailable"]

    @pytest.mark.parametrize(("connected", "expected"), [(True, "connected"), (False, "disconnected")])
    def test_reflects_client_connection(self, connected: bool, expected: str):
        client = MagicMock()
        client.connected = connected
        assert self._entity(client).native_value == expected


class TestMqttLastReceivedSensor:
    def test_reports_the_hub_level_timestamp(self):
        stamp = datetime(2026, 9, 1, 8, 30, tzinfo=timezone.utc)
        coordinator = MagicMock()
        coordinator.mqtt_last_message_ts = stamp
        entity = GoveeMqttLastReceivedSensor(coordinator, "entry1")

        assert entity.native_value == stamp
        assert entity.unique_id == "entry1_mqtt_last_received"
        assert entity.device_class == SensorDeviceClass.TIMESTAMP
        assert entity.device_info["identifiers"] == {(DOMAIN, hub_device_identifier("entry1"))}

    def test_none_until_the_first_push(self):
        coordinator = MagicMock()
        coordinator.mqtt_last_message_ts = None
        assert GoveeMqttLastReceivedSensor(coordinator, "entry1").native_value is None


# --------------------------------------------------------------------------- #
# Availability mixin + temperature conversion on real entities
# --------------------------------------------------------------------------- #


class TestBffAvailabilityMixin:
    def _entity(
        self,
        *,
        online: bool,
        last_update_success: bool = True,
        water_detector: bool = False,
        has_state: bool = True,
    ) -> GoveeTemperatureSensor:
        device = _thermometer()
        coordinator = MagicMock()
        coordinator.last_update_success = last_update_success
        coordinator.is_bff_thermometer.return_value = False
        coordinator.is_water_detector.return_value = water_detector
        state = GoveeDeviceState(device_id=device.device_id, online=online) if has_state else None
        coordinator.get_state.return_value = state
        return GoveeTemperatureSensor(coordinator, device)

    def test_regular_thermometer_follows_the_online_flag(self):
        assert self._entity(online=True).available is True
        assert self._entity(online=False).available is False

    def test_regular_thermometer_unavailable_when_coordinator_failed(self):
        assert self._entity(online=True, last_update_success=False).available is False

    def test_water_detector_ignores_the_online_flag(self):
        # H5054s report online: false between pushes (issues #62, #145).
        assert self._entity(online=False, water_detector=True).available is True

    def test_water_detector_needs_a_state(self):
        assert self._entity(online=False, water_detector=True, has_state=False).available is False


def _temperature_entity(
    raw: float | None,
    *,
    sku: str = "H5109",
    api_unit: str | None = "auto",
    hint: str | None = None,
    account_unit: str | None = None,
    bff: bool = False,
) -> GoveeTemperatureSensor:
    device = _device(THERMO_ID, sku, DEVICE_TYPE_THERMOMETER, _prop(INSTANCE_SENSOR_TEMPERATURE))
    state = GoveeDeviceState.create_empty(THERMO_ID)
    state.sensor_temperature = raw
    state.device_temperature_unit = hint
    coordinator = MagicMock()
    coordinator.config_entry = (
        None if api_unit is None else SimpleNamespace(options={CONF_API_TEMPERATURE_UNIT: api_unit})
    )
    coordinator.get_state.return_value = state
    coordinator.is_bff_thermometer.return_value = bff
    coordinator.account_temperature_unit.return_value = account_unit
    return GoveeTemperatureSensor(coordinator, device)


class TestTemperatureSensorConversion:
    def test_identity(self):
        entity = _temperature_entity(21.0)
        assert entity.unique_id == f"{THERMO_ID}_temperature"
        assert entity.device_class == SensorDeviceClass.TEMPERATURE
        assert entity.native_unit_of_measurement == UnitOfTemperature.CELSIUS

    def test_none_without_a_reading(self):
        assert _temperature_entity(None).native_value is None

    def test_bff_readings_are_already_celsius(self):
        # H5179 is on the allowlist for its Developer path, but a BFF value is
        # canonical °C and must not be converted again (issue #141).
        entity = _temperature_entity(4.9, sku="H5179", bff=True)
        assert entity.native_value == 4.9
        entity.coordinator.account_temperature_unit.assert_not_called()

    def test_celsius_option_trusts_the_api_value(self):
        assert _temperature_entity(100.83, sku="H5109", api_unit="celsius").native_value == 100.83

    def test_fahrenheit_option_converts_any_sku(self):
        assert _temperature_entity(70.0, sku="H6072", api_unit="fahrenheit").native_value == pytest.approx(
            21.1111, abs=1e-3
        )

    def test_auto_uses_the_device_unit_hint_first(self):
        # H713B is not on the allowlist; its own STRUCT says Fahrenheit (#129).
        entity = _temperature_entity(68.0, sku="H713B", hint="Fahrenheit")
        assert entity.native_value == pytest.approx(20.0)
        entity.coordinator.account_temperature_unit.assert_not_called()

    def test_auto_celsius_hint_beats_the_allowlist(self):
        assert _temperature_entity(21.5, sku="H5109", hint="Celsius").native_value == 21.5

    def test_auto_falls_back_to_the_account_unit(self):
        # No device hint: the account's fahOpen preference decides (#157).
        entity = _temperature_entity(50.0, sku="H6072", account_unit="fahrenheit")
        assert entity.native_value == pytest.approx(10.0)
        entity.coordinator.account_temperature_unit.assert_called_once_with(THERMO_ID)

    def test_auto_account_celsius_beats_the_allowlist(self):
        assert _temperature_entity(21.5, sku="H5109", account_unit="celsius").native_value == 21.5

    def test_auto_uses_the_allowlist_when_nothing_else_is_known(self):
        assert _temperature_entity(100.83, sku="H5109").native_value == pytest.approx(38.2389, abs=1e-3)
        assert _temperature_entity(21.5, sku="H6072").native_value == 21.5

    def test_missing_config_entry_defaults_to_auto(self):
        assert _temperature_entity(212.0, sku="H5109", api_unit=None).native_value == pytest.approx(100.0)


class TestSecondProbeTemperatureSensor:
    def _entity(self, probe_two: float | None, api_unit: str = "auto") -> GoveeSecondProbeTemperatureSensor:
        device = _device(DUAL_ID, "H5112", DEVICE_TYPE_THERMOMETER, _prop(INSTANCE_SENSOR_TEMPERATURE))
        state = GoveeDeviceState.create_empty(DUAL_ID)
        state.sensor_temperature = -1.0  # probe 1 unplugged sentinel
        state.sensor_temperature_2 = probe_two
        coordinator = MagicMock()
        coordinator.config_entry = SimpleNamespace(options={CONF_API_TEMPERATURE_UNIT: api_unit})
        coordinator.get_state.return_value = state
        coordinator.is_bff_thermometer.return_value = False
        coordinator.account_temperature_unit.return_value = None
        return GoveeSecondProbeTemperatureSensor(coordinator, device)

    def test_identity(self):
        entity = self._entity(4.0)
        assert entity.unique_id == f"{DUAL_ID}_temperature_2"
        assert entity.translation_key == "sensor_temperature_2"

    def test_reads_probe_two_not_probe_one(self):
        assert self._entity(4.0).native_value == 4.0

    def test_none_when_probe_two_is_absent(self):
        assert self._entity(None).native_value is None

    def test_shares_the_fahrenheit_normalization(self):
        assert self._entity(39.2, api_unit="fahrenheit").native_value == pytest.approx(4.0)


class TestProbeTemperatureSensor:
    def _entity(self, state: GoveeDeviceState | None, probe: int = 1, channel: str = "core"):
        device = GoveeDevice.synthetic_probe_thermometer(PROBE_ID, "H5192", "Grill")
        coordinator = MagicMock()
        coordinator.get_state.return_value = state
        return GoveeProbeTemperatureSensor(coordinator, device, probe, channel)

    def test_identity(self):
        entity = self._entity(None, probe=2, channel="ambient")
        assert entity.unique_id == f"{PROBE_ID}_probe2_ambient"
        assert entity.translation_key == "probe_ambient"
        assert entity.translation_placeholders == {"probe": "2"}
        assert entity.device_class == SensorDeviceClass.TEMPERATURE
        assert entity.native_unit_of_measurement == UnitOfTemperature.CELSIUS

    def test_none_without_state(self):
        assert self._entity(None).native_value is None

    def test_none_until_the_probe_reports(self):
        assert self._entity(GoveeDeviceState.create_empty(PROBE_ID)).native_value is None

    def test_unplugged_channel_reads_none_while_the_other_reads(self):
        state = GoveeDeviceState.create_empty(PROBE_ID)
        state.probes[1] = ProbeReading(core=None, ambient=22.5)
        assert self._entity(state, channel="core").native_value is None
        assert self._entity(state, channel="ambient").native_value == 22.5

    def test_reading_is_a_float(self):
        state = GoveeDeviceState.create_empty(PROBE_ID)
        state.probes[2] = ProbeReading(core=63, ambient=180)
        value = self._entity(state, probe=2, channel="core").native_value
        assert value == 63.0
        assert isinstance(value, float)


# --------------------------------------------------------------------------- #
# Simple reading sensors
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("cls", "field", "value", "suffix"),
    [
        pytest.param(GoveeHumiditySensor, "sensor_humidity", 55.5, "_humidity", id="humidity"),
        pytest.param(GoveeDehumidifierModeSensor, "dehumidifier_mode", "pump", "_dehumidifier_mode", id="mode"),
        pytest.param(GoveeAirQualitySensor, "air_quality", 2, "_air_quality", id="aqi"),
        pytest.param(GoveeCO2Sensor, "carbon_dioxide", 812, "_co2", id="co2"),
        pytest.param(GoveeFilterLifeSensor, "filter_life", 73, "_filter_life", id="filter"),
        pytest.param(GoveeThermoBatterySensor, "battery", 88, "_battery", id="battery"),
    ],
)
def test_reading_sensor_reports_its_state_field(cls: type, field: str, value: Any, suffix: str) -> None:
    device = _thermometer()
    state = GoveeDeviceState.create_empty(device.device_id)
    setattr(state, field, value)
    coordinator = MagicMock()
    coordinator.get_state.return_value = state

    entity = cls(coordinator, device)
    assert entity.native_value == value
    assert entity.unique_id == f"{device.device_id}{suffix}"

    coordinator.get_state.return_value = None
    assert entity.native_value is None


class TestTimestampDelegates:
    def test_reading_changed_delegates_to_the_coordinator(self):
        stamp = datetime(2026, 9, 2, tzinfo=timezone.utc)
        coordinator = MagicMock()
        coordinator.sensor_reading_changed_at.return_value = stamp
        entity = GoveeSensorReadingTimestampSensor(coordinator, _thermometer())

        assert entity.native_value == stamp
        assert entity.unique_id == f"{THERMO_ID}_reading_changed"
        coordinator.sensor_reading_changed_at.assert_called_once_with(THERMO_ID)

    def test_all_data_last_updated_delegates_to_the_coordinator(self):
        stamp = datetime(2026, 9, 3, tzinfo=timezone.utc)
        coordinator = MagicMock()
        coordinator.device_data_last_updated.return_value = stamp
        entity = GoveeAllDataLastUpdatedSensor(coordinator, _thermometer())

        assert entity.native_value == stamp
        assert entity.unique_id == f"{THERMO_ID}_all_data_last_updated"
        coordinator.device_data_last_updated.assert_called_once_with(THERMO_ID)


# --------------------------------------------------------------------------- #
# Leak-sensor diagnostics (dispatcher-driven)
# --------------------------------------------------------------------------- #


class TestLeakBatterySensor:
    def test_reads_the_battery_from_the_leak_state(self):
        sensor = _leak_sensor()
        entity = GoveeLeakBatterySensor(_leak_coordinator(sensor, GoveeLeakSensorState(battery=64)), sensor)

        assert entity.native_value == 64
        assert entity.unique_id == f"{sensor.device_id}_battery"
        assert entity.device_class == SensorDeviceClass.BATTERY
        assert entity.entity_category == EntityCategory.DIAGNOSTIC
        assert entity.should_poll is False

    def test_none_before_the_first_poll(self):
        sensor = _leak_sensor()
        assert GoveeLeakBatterySensor(_leak_coordinator(sensor, None), sensor).native_value is None

    def test_device_info_links_the_sensor_to_its_hub(self):
        sensor = _leak_sensor(hw_version="1.00.01", sw_version="2.03.00")
        info = GoveeLeakBatterySensor(_leak_coordinator(sensor, None), sensor).device_info

        assert info["identifiers"] == {(DOMAIN, sensor.device_id)}
        assert info["via_device"] == (DOMAIN, HUB_A)
        assert info["model"] == "H5058"
        assert info["name"] == "Kitchen sink"
        assert info["hw_version"] == "1.00.01"
        assert info["sw_version"] == "2.03.00"

    @pytest.mark.asyncio
    async def test_subscribes_to_the_leak_signal(self):
        sensor = _leak_sensor()
        await _assert_leak_subscription(GoveeLeakBatterySensor(_leak_coordinator(sensor, None), sensor))


class TestLeakLastWetSensor:
    def test_converts_epoch_milliseconds_to_utc(self):
        sensor = _leak_sensor()
        state = GoveeLeakSensorState(last_wet_time=1_720_000_000_000)
        entity = GoveeLeakLastWetSensor(_leak_coordinator(sensor, state), sensor)

        value = entity.native_value
        assert value == datetime.fromtimestamp(1_720_000_000, tz=timezone.utc)
        assert value.tzinfo == timezone.utc
        assert entity.unique_id == f"{sensor.device_id}_last_wet"
        assert entity.device_class == SensorDeviceClass.TIMESTAMP
        assert entity.device_info["via_device"] == (DOMAIN, HUB_A)

    def test_none_until_a_leak_was_ever_recorded(self):
        sensor = _leak_sensor()
        assert GoveeLeakLastWetSensor(_leak_coordinator(sensor, GoveeLeakSensorState()), sensor).native_value is None
        assert GoveeLeakLastWetSensor(_leak_coordinator(sensor, None), sensor).native_value is None

    @pytest.mark.asyncio
    async def test_subscribes_to_the_leak_signal(self):
        sensor = _leak_sensor()
        await _assert_leak_subscription(GoveeLeakLastWetSensor(_leak_coordinator(sensor, None), sensor))


class TestLeakAlertStatusSensor:
    @pytest.mark.parametrize(("read", "expected"), [(True, "acknowledged"), (False, "pending")])
    def test_maps_the_read_flag(self, read: bool, expected: str):
        sensor = _leak_sensor()
        entity = GoveeLeakAlertStatusSensor(_leak_coordinator(sensor, GoveeLeakSensorState(read=read)), sensor)

        assert entity.native_value == expected
        assert entity.options == ["pending", "acknowledged"]
        assert entity.unique_id == f"{sensor.device_id}_alert_status"
        assert entity.device_info["identifiers"] == {(DOMAIN, sensor.device_id)}

    def test_none_without_state(self):
        sensor = _leak_sensor()
        assert GoveeLeakAlertStatusSensor(_leak_coordinator(sensor, None), sensor).native_value is None

    @pytest.mark.asyncio
    async def test_subscribes_to_the_leak_signal(self):
        sensor = _leak_sensor()
        await _assert_leak_subscription(GoveeLeakAlertStatusSensor(_leak_coordinator(sensor, None), sensor))


class TestAddressSensors:
    def test_leak_device_address_is_the_ieee_id(self):
        sensor = _leak_sensor()
        entity = GoveeLeakDeviceAddressSensor(sensor)

        assert entity.native_value == sensor.device_id
        assert entity.unique_id == f"{sensor.device_id}_address"
        assert entity.translation_key == "ieee_address"
        assert entity.entity_category == EntityCategory.DIAGNOSTIC
        assert entity.device_info["identifiers"] == {(DOMAIN, sensor.device_id)}
        assert entity.device_info["via_device"] == (DOMAIN, HUB_A)

    def test_hub_address_attaches_to_the_hub_device(self):
        entity = GoveeLeakHubAddressSensor(HUB_A)

        assert entity.native_value == HUB_A
        assert entity.unique_id == f"{HUB_A}_address"
        assert entity.translation_key == "ieee_address"
        assert entity.device_info == {"identifiers": {(DOMAIN, HUB_A)}}
