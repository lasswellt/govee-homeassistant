"""Coverage tests for the binary_sensor and event platforms.

Walks every entity-creation branch of both ``async_setup_entry`` functions and
exercises the entity classes without direct tests: the per-transport
connectivity attributes, the group short-circuit of the aggregate
connectivity sensor, and the leak-sensor entities (moisture, link, hub
connectivity, button press) that ride the ``<domain>_leak_update`` signal.
"""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest
from homeassistant.components.binary_sensor import BinarySensorDeviceClass
from homeassistant.components.event import EventDeviceClass
from homeassistant.const import EntityCategory
from homeassistant.helpers.dispatcher import DATA_DISPATCHER

from custom_components.govee import event as event_mod
from custom_components.govee.binary_sensor import (
    _TRANSPORT_SPECS,
    GoveeDeviceConnectivity,
    GoveeLeakBinarySensor,
    GoveeLeakHubOnlineSensor,
    GoveeLeakOnlineSensor,
    GoveeOccupancyBinarySensor,
    GoveePumpStateBinarySensor,
    GoveeTransportConnectivity,
    GoveeWaterFullBinarySensor,
    GoveeWaterLeakBinarySensor,
    async_setup_entry,
)
from custom_components.govee.const import CONF_EXPOSE_TRANSPORT_ENTITIES, DOMAIN
from custom_components.govee.event import GoveeLeakButtonEvent
from custom_components.govee.models import GoveeCapability, GoveeDevice, TransportHealth
from custom_components.govee.models.device import (
    CAPABILITY_EVENT,
    CAPABILITY_ON_OFF,
    DEVICE_TYPE_DEHUMIDIFIER,
    DEVICE_TYPE_LIGHT,
    INSTANCE_BODY_APPEARED_EVENT,
    INSTANCE_POWER,
    INSTANCE_WATER_FULL_EVENT,
    GoveeLeakSensor,
    GoveeLeakSensorState,
)

LEAK_SIGNAL = f"{DOMAIN}_leak_update"

TANK_ID = "AA:BB:CC:DD:EE:FF:71:50"
PUMP_ID = "AA:BB:CC:DD:EE:FF:71:52"
WATER_ID = "AA:BB:CC:DD:EE:FF:50:54"
PRESENCE_ID = "AA:BB:CC:DD:EE:FF:51:27"
LIGHT_ID = "AA:BB:CC:DD:EE:FF:60:72"
GROUP_ID = "11825917"

HUB_A = "09:C2:60:74:F4:64:AB:FA"
HUB_B = "09:C2:60:74:F4:64:AB:FB"
LEAK_A1 = "01:32:7A:C4:06:03:0D:0C"
LEAK_A2 = "01:32:7A:C4:06:03:0D:0D"
LEAK_B1 = "01:32:7A:C4:06:03:0D:0E"

_POWER = GoveeCapability(type=CAPABILITY_ON_OFF, instance=INSTANCE_POWER)
_BODY_APPEARED = GoveeCapability(type=CAPABILITY_EVENT, instance=INSTANCE_BODY_APPEARED_EVENT)


def _device(device_id: str, sku: str, device_type: str, *caps: GoveeCapability, is_group: bool = False) -> GoveeDevice:
    return GoveeDevice(
        device_id=device_id,
        sku=sku,
        name=f"Device {sku}",
        device_type=device_type,
        capabilities=caps,
        is_group=is_group,
    )


def _light() -> GoveeDevice:
    return _device(LIGHT_ID, "H6072", DEVICE_TYPE_LIGHT, _POWER)


def _fleet() -> dict[str, GoveeDevice]:
    devices = [
        _device(
            TANK_ID,
            "H7150",
            DEVICE_TYPE_DEHUMIDIFIER,
            _POWER,
            GoveeCapability(type=CAPABILITY_EVENT, instance=INSTANCE_WATER_FULL_EVENT),
        ),
        _device(PUMP_ID, "H7152", DEVICE_TYPE_DEHUMIDIFIER, _POWER),
        _device(WATER_ID, "H5054", "devices.types.sensor", _BODY_APPEARED),
        _device(PRESENCE_ID, "H5127", "devices.types.sensor", _BODY_APPEARED),
        _light(),
        _device(GROUP_ID, "GROUP", "devices.types.group", _POWER, is_group=True),
    ]
    return {device.device_id: device for device in devices}


def _leak_sensor(device_id: str = LEAK_A1, hub_device_id: str = HUB_A) -> GoveeLeakSensor:
    return GoveeLeakSensor(
        device_id=device_id,
        name="Kitchen sink",
        sku="H5058",
        hub_device_id=hub_device_id,
        sno=3,
        hw_version="1.00.01",
    )


def _fleet_coordinator() -> MagicMock:
    coordinator = MagicMock()
    coordinator.devices = _fleet()
    coordinator.is_bff_leak_sensor.return_value = False
    # Account-data thermometers (#258) are covered in test_issue_258.py.
    coordinator.is_bff_thermometer.return_value = False
    coordinator.leak_sensors = {
        sensor.device_id: sensor
        for sensor in (_leak_sensor(LEAK_A1, HUB_A), _leak_sensor(LEAK_A2, HUB_A), _leak_sensor(LEAK_B1, HUB_B))
    }
    return coordinator


async def _setup(coordinator: MagicMock, *, expose: bool) -> list[Any]:
    entry = MagicMock()
    entry.runtime_data = coordinator
    entry.options = {CONF_EXPOSE_TRANSPORT_ENTITIES: expose}
    added: list[Any] = []
    await async_setup_entry(MagicMock(), entry, lambda entities: added.extend(entities))
    return added


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

    assert len(entity._on_remove) == 1
    entity._on_remove[0]()
    assert LEAK_SIGNAL not in hass.data[DATA_DISPATCHER]


# --------------------------------------------------------------------------- #
# binary_sensor.async_setup_entry
# --------------------------------------------------------------------------- #


_BASE_EXPECTED = {
    f"{TANK_ID}_water_full",
    f"{PUMP_ID}_pump_state",
    f"{WATER_ID}_water_leak",
    f"{PRESENCE_ID}_occupancy",
    *(f"{device_id}_connectivity" for device_id in (TANK_ID, PUMP_ID, WATER_ID, PRESENCE_ID, LIGHT_ID)),
    *(f"{leak_id}_leak" for leak_id in (LEAK_A1, LEAK_A2, LEAK_B1)),
    *(f"{leak_id}_online" for leak_id in (LEAK_A1, LEAK_A2, LEAK_B1)),
    f"{HUB_A}_hub_online",
    f"{HUB_B}_hub_online",
}


class TestSetupEntry:
    @pytest.mark.asyncio
    async def test_creates_one_entity_per_device_feature(self):
        coordinator = _fleet_coordinator()
        added = await _setup(coordinator, expose=False)

        assert {entity.unique_id for entity in added} == _BASE_EXPECTED
        assert len(added) == len(_BASE_EXPECTED)
        coordinator.register_leak_hubs.assert_called_once_with()

    @pytest.mark.asyncio
    async def test_entity_classes_match_their_device(self):
        added = {entity.unique_id: entity for entity in await _setup(_fleet_coordinator(), expose=False)}

        assert isinstance(added[f"{TANK_ID}_water_full"], GoveeWaterFullBinarySensor)
        assert isinstance(added[f"{PUMP_ID}_pump_state"], GoveePumpStateBinarySensor)
        assert isinstance(added[f"{WATER_ID}_water_leak"], GoveeWaterLeakBinarySensor)
        assert isinstance(added[f"{PRESENCE_ID}_occupancy"], GoveeOccupancyBinarySensor)
        assert isinstance(added[f"{LIGHT_ID}_connectivity"], GoveeDeviceConnectivity)
        assert isinstance(added[f"{LEAK_A1}_leak"], GoveeLeakBinarySensor)
        assert isinstance(added[f"{LEAK_A1}_online"], GoveeLeakOnlineSensor)
        assert isinstance(added[f"{HUB_A}_hub_online"], GoveeLeakHubOnlineSensor)

    @pytest.mark.asyncio
    async def test_presence_sensor_is_never_a_moisture_sensor(self):
        added = await _setup(_fleet_coordinator(), expose=False)
        water_leak = [entity for entity in added if isinstance(entity, GoveeWaterLeakBinarySensor)]
        assert [entity._device_id for entity in water_leak] == [WATER_ID]

    @pytest.mark.asyncio
    async def test_group_devices_get_nothing(self):
        added = await _setup(_fleet_coordinator(), expose=True)
        assert not any(getattr(entity, "_device_id", None) == GROUP_ID for entity in added)

    @pytest.mark.asyncio
    async def test_transport_entities_are_opt_in(self):
        added = await _setup(_fleet_coordinator(), expose=True)

        transport = [entity for entity in added if isinstance(entity, GoveeTransportConnectivity)]
        physical = (TANK_ID, PUMP_ID, WATER_ID, PRESENCE_ID, LIGHT_ID)
        assert {entity.unique_id for entity in transport} == {
            f"{device_id}_{kind}_connectivity" for device_id in physical for kind, _ in _TRANSPORT_SPECS
        }
        assert {entity.translation_key for entity in transport} == {key for _, key in _TRANSPORT_SPECS}
        assert {entity.unique_id for entity in added} == _BASE_EXPECTED | {entity.unique_id for entity in transport}

    @pytest.mark.asyncio
    async def test_nothing_is_added_for_an_empty_account(self):
        coordinator = MagicMock()
        coordinator.devices = {}
        coordinator.leak_sensors = {}
        entry = MagicMock()
        entry.runtime_data = coordinator
        entry.options = {}
        add_entities = MagicMock()

        await async_setup_entry(MagicMock(), entry, add_entities)

        add_entities.assert_not_called()


# --------------------------------------------------------------------------- #
# Aggregate + per-transport connectivity
# --------------------------------------------------------------------------- #


class TestDeviceConnectivityGroups:
    def test_group_is_always_reachable_without_consulting_health(self):
        coordinator = MagicMock()
        group = _device(GROUP_ID, "GROUP", "devices.types.group", is_group=True)

        entity = GoveeDeviceConnectivity(coordinator, group)

        assert entity.is_on is True
        coordinator.get_transport_health.assert_not_called()


def _transport_entity(coordinator: MagicMock, transport: str = "mqtt") -> GoveeTransportConnectivity:
    return GoveeTransportConnectivity(
        coordinator=coordinator,
        device=_light(),
        transport=transport,  # type: ignore[arg-type]
        translation_key=f"{transport}_connectivity",
    )


class TestTransportConnectivity:
    def test_identity(self):
        entity = _transport_entity(MagicMock(), "ble")
        assert entity.unique_id == f"{LIGHT_ID}_ble_connectivity"
        assert entity.translation_key == "ble_connectivity"
        assert entity.device_class == BinarySensorDeviceClass.CONNECTIVITY
        assert entity.entity_category == EntityCategory.DIAGNOSTIC

    def test_untracked_transport_is_unknown_with_no_attributes(self):
        coordinator = MagicMock()
        coordinator.get_transport_health.return_value = None
        entity = _transport_entity(coordinator, "lan")

        assert entity.is_on is None
        assert entity.extra_state_attributes == {}
        coordinator.get_transport_health.assert_called_with(LIGHT_ID, "lan")

    @pytest.mark.parametrize("success", [True, False])
    def test_available_follows_the_coordinator_not_the_device(self, success: bool):
        coordinator = MagicMock()
        coordinator.last_update_success = success
        coordinator.get_state.return_value = MagicMock(online=False)
        assert _transport_entity(coordinator).available is success

    def test_attributes_carry_every_stamp(self):
        received = datetime(2026, 6, 5, 12, 0, tzinfo=timezone.utc)
        sent = datetime(2026, 6, 5, 12, 1, tzinfo=timezone.utc)
        failed = datetime(2026, 6, 5, 12, 2, tzinfo=timezone.utc)
        coordinator = MagicMock()
        coordinator.get_transport_health.return_value = TransportHealth(
            transport="cloud_api",
            is_available=False,
            last_success_ts=received,
            last_send_ts=sent,
            last_failure_ts=failed,
            last_failure_reason="http_500",
        )
        entity = _transport_entity(coordinator, "cloud_api")

        assert entity.is_on is False
        assert entity.extra_state_attributes == {
            "last_received": received.isoformat(),
            "last_success": received.isoformat(),  # deprecated alias
            "last_sent": sent.isoformat(),
            "last_failure": failed.isoformat(),
            "last_failure_reason": "http_500",
        }
        # The per-device MQTT stamp is only relevant to the mqtt transport.
        coordinator.mqtt_last_receive_for.assert_not_called()

    def test_attributes_are_empty_until_anything_happened(self):
        coordinator = MagicMock()
        coordinator.get_transport_health.return_value = TransportHealth(transport="ble")
        assert _transport_entity(coordinator, "ble").extra_state_attributes == {}

    def test_mqtt_prefers_the_per_device_receive_stamp(self):
        hub_stamp = datetime(2026, 6, 5, 12, 0, tzinfo=timezone.utc)
        device_stamp = datetime(2026, 6, 5, 12, 5, tzinfo=timezone.utc)
        coordinator = MagicMock()
        coordinator.get_transport_health.return_value = TransportHealth(
            transport="mqtt", is_available=True, last_success_ts=hub_stamp
        )
        coordinator.mqtt_last_receive_for.return_value = device_stamp
        entity = _transport_entity(coordinator, "mqtt")

        attrs = entity.extra_state_attributes
        assert entity.is_on is True
        assert attrs["last_received"] == device_stamp.isoformat()
        assert attrs["last_success"] == device_stamp.isoformat()
        coordinator.mqtt_last_receive_for.assert_called_once_with(LIGHT_ID)

    def test_mqtt_falls_back_to_the_hub_stamp(self):
        hub_stamp = datetime(2026, 6, 5, 12, 0, tzinfo=timezone.utc)
        coordinator = MagicMock()
        coordinator.get_transport_health.return_value = TransportHealth(transport="mqtt", last_success_ts=hub_stamp)
        coordinator.mqtt_last_receive_for.return_value = None

        assert _transport_entity(coordinator, "mqtt").extra_state_attributes["last_received"] == hub_stamp.isoformat()


# --------------------------------------------------------------------------- #
# Leak-sensor binary sensors
# --------------------------------------------------------------------------- #


class TestLeakBinarySensor:
    def test_reports_moisture_from_the_leak_state(self):
        sensor = _leak_sensor()
        entity = GoveeLeakBinarySensor(_leak_coordinator(sensor, GoveeLeakSensorState(is_wet=True)), sensor)

        assert entity.is_on is True
        assert entity.available is True
        assert entity.unique_id == f"{LEAK_A1}_leak"
        assert entity.device_class == BinarySensorDeviceClass.MOISTURE
        # name=None so the entity takes the device name ("Kitchen sink").
        assert entity.name is None
        assert entity.device_info["identifiers"] == {(DOMAIN, LEAK_A1)}
        assert entity.device_info["via_device"] == (DOMAIN, HUB_A)
        assert entity.device_info["hw_version"] == "1.00.01"

    def test_device_info_links_by_registry_id_on_new_ha(self, monkeypatch):
        from custom_components.govee import registry

        monkeypatch.setattr(registry, "supports_via_device_id", lambda: True)
        fake = MagicMock(spec=["async_get_device_by_identifier"])
        fake.async_get_device_by_identifier.return_value = SimpleNamespace(id="hub-id")
        monkeypatch.setattr(registry.dr, "async_get", lambda hass: fake)
        sensor = _leak_sensor()
        entity = GoveeLeakBinarySensor(_leak_coordinator(sensor, GoveeLeakSensorState()), sensor)
        entity.hass = MagicMock()

        info = entity.device_info

        assert info["via_device_id"] == "hub-id"
        assert "via_device" not in info

    def test_dry(self):
        sensor = _leak_sensor()
        assert GoveeLeakBinarySensor(_leak_coordinator(sensor, GoveeLeakSensorState()), sensor).is_on is False

    def test_unavailable_and_unknown_before_the_first_poll(self):
        sensor = _leak_sensor()
        entity = GoveeLeakBinarySensor(_leak_coordinator(sensor, None), sensor)
        assert entity.is_on is None
        assert entity.available is False

    @pytest.mark.asyncio
    async def test_subscribes_and_writes_state_on_signal(self):
        sensor = _leak_sensor()
        entity = GoveeLeakBinarySensor(_leak_coordinator(sensor, None), sensor)
        entity.async_write_ha_state = MagicMock()

        await _assert_leak_subscription(entity)
        entity._handle_leak_update()

        entity.async_write_ha_state.assert_called_once()


class TestLeakOnlineSensor:
    @pytest.mark.parametrize("online", [True, False])
    def test_reports_the_lora_link(self, online: bool):
        sensor = _leak_sensor()
        entity = GoveeLeakOnlineSensor(_leak_coordinator(sensor, GoveeLeakSensorState(online=online)), sensor)

        assert entity.is_on is online
        assert entity.unique_id == f"{LEAK_A1}_online"
        assert entity.device_class == BinarySensorDeviceClass.CONNECTIVITY
        assert entity.entity_category == EntityCategory.DIAGNOSTIC
        assert entity.device_info["via_device"] == (DOMAIN, HUB_A)

    def test_unknown_without_state(self):
        sensor = _leak_sensor()
        assert GoveeLeakOnlineSensor(_leak_coordinator(sensor, None), sensor).is_on is None

    @pytest.mark.asyncio
    async def test_subscribes_and_writes_state_on_signal(self):
        sensor = _leak_sensor()
        entity = GoveeLeakOnlineSensor(_leak_coordinator(sensor, None), sensor)
        entity.async_write_ha_state = MagicMock()

        await _assert_leak_subscription(entity)
        entity._handle_leak_update()

        entity.async_write_ha_state.assert_called_once()


class TestLeakHubOnlineSensor:
    def _coordinator(self, states: dict[str, GoveeLeakSensorState]) -> MagicMock:
        coordinator = MagicMock()
        coordinator.leak_sensors = {
            LEAK_B1: _leak_sensor(LEAK_B1, HUB_B),
            LEAK_A1: _leak_sensor(LEAK_A1, HUB_A),
            LEAK_A2: _leak_sensor(LEAK_A2, HUB_A),
        }
        coordinator.leak_states = states
        return coordinator

    def test_identity(self):
        entity = GoveeLeakHubOnlineSensor(self._coordinator({}), HUB_A)
        assert entity.unique_id == f"{HUB_A}_hub_online"
        assert entity.device_info == {"identifiers": {(DOMAIN, HUB_A)}}
        assert entity.device_class == BinarySensorDeviceClass.CONNECTIVITY
        assert entity.entity_category == EntityCategory.DIAGNOSTIC

    def test_reads_the_gateway_flag_of_its_own_children_only(self):
        # The hub-B child says offline; hub A must not pick that up.
        states = {
            LEAK_B1: GoveeLeakSensorState(gateway_online=False),
            LEAK_A2: GoveeLeakSensorState(gateway_online=True),
        }
        assert GoveeLeakHubOnlineSensor(self._coordinator(states), HUB_A).is_on is True
        assert GoveeLeakHubOnlineSensor(self._coordinator(states), HUB_B).is_on is False

    def test_first_child_with_state_decides(self):
        states = {LEAK_A1: GoveeLeakSensorState(gateway_online=False), LEAK_A2: GoveeLeakSensorState()}
        assert GoveeLeakHubOnlineSensor(self._coordinator(states), HUB_A).is_on is False

    def test_unknown_when_no_child_has_state(self):
        assert GoveeLeakHubOnlineSensor(self._coordinator({}), HUB_A).is_on is None

    def test_unknown_for_a_hub_without_children(self):
        states = {LEAK_A1: GoveeLeakSensorState(gateway_online=True)}
        assert GoveeLeakHubOnlineSensor(self._coordinator(states), "09:00:00:00:00:00:00:00").is_on is None

    @pytest.mark.asyncio
    async def test_subscribes_and_writes_state_on_signal(self):
        entity = GoveeLeakHubOnlineSensor(self._coordinator({}), HUB_A)
        entity.async_write_ha_state = MagicMock()

        await _assert_leak_subscription(entity)
        entity._handle_leak_update()

        entity.async_write_ha_state.assert_called_once()


# --------------------------------------------------------------------------- #
# event platform
# --------------------------------------------------------------------------- #


class TestEventSetupEntry:
    @pytest.mark.asyncio
    async def test_one_button_event_per_leak_sensor(self):
        coordinator = MagicMock()
        coordinator.leak_sensors = {LEAK_A1: _leak_sensor(LEAK_A1, HUB_A), LEAK_B1: _leak_sensor(LEAK_B1, HUB_B)}
        entry = MagicMock()
        entry.runtime_data = coordinator
        added: list[Any] = []

        await event_mod.async_setup_entry(MagicMock(), entry, lambda entities: added.extend(entities))

        assert all(isinstance(entity, GoveeLeakButtonEvent) for entity in added)
        assert {entity.unique_id for entity in added} == {f"{LEAK_A1}_button", f"{LEAK_B1}_button"}
        coordinator.register_leak_hubs.assert_called_once_with()

    @pytest.mark.asyncio
    async def test_nothing_is_added_without_leak_sensors(self):
        coordinator = MagicMock()
        coordinator.leak_sensors = {}
        entry = MagicMock()
        entry.runtime_data = coordinator
        add_entities = MagicMock()

        await event_mod.async_setup_entry(MagicMock(), entry, add_entities)

        add_entities.assert_not_called()
        coordinator.register_leak_hubs.assert_called_once_with()


class TestLeakButtonEvent:
    def _entity(self, *, pending: bool) -> GoveeLeakButtonEvent:
        sensor = _leak_sensor()
        coordinator = _leak_coordinator(sensor, None)
        coordinator.consume_button_press.return_value = pending
        entity = GoveeLeakButtonEvent(coordinator, sensor)
        entity.async_write_ha_state = MagicMock()
        return entity

    def test_identity(self):
        entity = self._entity(pending=False)
        assert entity.unique_id == f"{LEAK_A1}_button"
        assert entity.event_types == ["press"]
        assert entity.device_class == EventDeviceClass.BUTTON
        assert entity.translation_key == "leak_button"
        assert entity.should_poll is False
        assert entity.device_info["identifiers"] == {(DOMAIN, LEAK_A1)}
        assert entity.device_info["via_device"] == (DOMAIN, HUB_A)
        assert entity.device_info["hw_version"] == "1.00.01"

    def test_device_info_links_by_registry_id_on_new_ha(self, monkeypatch):
        from custom_components.govee import registry

        monkeypatch.setattr(registry, "supports_via_device_id", lambda: True)
        fake = MagicMock(spec=["async_get_device_by_identifier"])
        fake.async_get_device_by_identifier.return_value = SimpleNamespace(id="hub-id")
        monkeypatch.setattr(registry.dr, "async_get", lambda hass: fake)
        entity = self._entity(pending=False)
        entity.hass = MagicMock()

        info = entity.device_info

        assert info["via_device_id"] == "hub-id"
        assert "via_device" not in info

    def test_press_is_fired_when_a_press_was_queued(self):
        entity = self._entity(pending=True)
        assert entity.state is None

        entity._handle_leak_update()

        entity._coordinator.consume_button_press.assert_called_once_with(LEAK_A1)
        assert entity.state_attributes["event_type"] == "press"
        assert entity.state is not None
        entity.async_write_ha_state.assert_called_once()

    def test_unrelated_leak_update_does_not_fire(self):
        entity = self._entity(pending=False)

        entity._handle_leak_update()

        assert entity.state is None
        entity.async_write_ha_state.assert_not_called()

    @pytest.mark.asyncio
    async def test_subscribes_to_the_leak_signal(self):
        await _assert_leak_subscription(self._entity(pending=False))
