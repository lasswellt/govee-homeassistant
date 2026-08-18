"""Tests for the per-device connection-mode diagnostic sensor."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
from homeassistant.components.sensor import SensorDeviceClass
from homeassistant.const import EntityCategory

from custom_components.govee.models import GoveeDevice, GoveeDeviceState, TransportHealth
from custom_components.govee.sensor import GoveeConnectionModeSensor, async_setup_entry

_TRANSPORTS = ("ble", "lan", "mqtt", "cloud_api")


def _device(
    device_id: str = "AA:BB:CC:DD:EE:FF:00:11",
    *,
    is_group: bool = False,
    hub_device_id: str | None = "",
) -> GoveeDevice:
    """Build a minimal device for connection-mode tests."""
    return GoveeDevice(
        device_id=device_id,
        sku="GROUP" if is_group else "H6072",
        name="Test Device",
        device_type="devices.types.group" if is_group else "devices.types.light",
        capabilities=(),
        is_group=is_group,
        hub_device_id=hub_device_id,  # type: ignore[arg-type]
    )


def _health(**available: bool) -> dict[str, TransportHealth]:
    return {kind: TransportHealth(transport=kind, is_available=available.get(kind, False)) for kind in _TRANSPORTS}


def _coordinator(
    device: GoveeDevice,
    health: dict[str, TransportHealth] | None = None,
    *,
    online: bool = True,
    last_update_success: bool = True,
    route: dict[str, str] | None = None,
) -> MagicMock:
    coordinator = MagicMock()
    coordinator.devices = {device.device_id: device}
    coordinator.last_update_success = last_update_success
    coordinator.get_state.return_value = GoveeDeviceState(device_id=device.device_id, online=online)
    coordinator.gateway_route.return_value = route
    health_by_kind = health or {}
    coordinator.get_transport_health.side_effect = lambda _device_id, kind: health_by_kind.get(kind)
    return coordinator


def _sensor(
    device: GoveeDevice,
    health: dict[str, TransportHealth] | None = None,
    **kwargs: Any,
) -> GoveeConnectionModeSensor:
    return GoveeConnectionModeSensor(_coordinator(device, health, **kwargs), device)


@pytest.mark.parametrize(
    ("kind", "expected_icon"),
    [
        pytest.param("lan", "mdi:lan", id="lan-only"),
        pytest.param("ble", "mdi:bluetooth", id="ble-only"),
        pytest.param("mqtt", "mdi:cloud-sync", id="mqtt-only"),
        pytest.param("cloud_api", "mdi:cloud", id="cloud-only"),
    ],
)
def test_single_available_transport_wins(kind: str, expected_icon: str) -> None:
    """SCN-001..004: each transport is selected when it is the only healthy one."""
    entity = _sensor(_device(), _health(**{kind: True}))
    assert entity.native_value == kind
    assert entity.icon == expected_icon


def test_lan_beats_mqtt() -> None:
    """SCN-005: LAN precedes MQTT in the command priority."""
    assert _sensor(_device(), _health(lan=True, mqtt=True)).native_value == "lan"


def test_ble_beats_lan_and_mqtt() -> None:
    """SCN-006: BLE has the highest priority."""
    assert _sensor(_device(), _health(ble=True, lan=True, mqtt=True)).native_value == "ble"


def test_zero_reachability_returns_unavailable() -> None:
    """SCN-007: tracked but failed transports produce the catch-all value."""
    entity = _sensor(_device(), _health())
    assert entity.native_value == "unavailable"
    assert entity.icon == "mdi:lan-pending"


def test_missing_health_entries_returns_unavailable() -> None:
    """SCN-008: an unpopulated health tracker is unavailable."""
    assert _sensor(_device()).native_value == "unavailable"


@pytest.mark.parametrize(
    ("health", "expected"),
    [
        pytest.param(_health(cloud_api=True), "cloud_api", id="reachable"),
        pytest.param(_health(), "unavailable", id="unreachable"),
        pytest.param(_health(ble=True, lan=True, mqtt=True), "unavailable", id="non-rest-ignored"),
    ],
)
def test_group_uses_only_cloud_api(health: dict[str, TransportHealth], expected: str) -> None:
    """SCN-009..011: groups never consult non-REST health."""
    device = _device(is_group=True)
    coordinator = _coordinator(device, health)
    entity = GoveeConnectionModeSensor(coordinator, device)

    assert entity.native_value == expected
    coordinator.get_transport_health.assert_called_once_with(device.device_id, "cloud_api")


def test_hub_device_id_is_exposed() -> None:
    """SCN-012: the device-level bridge source wins."""
    device = _device(hub_device_id="HUB_A")
    entity = _sensor(device, _health(mqtt=True))
    assert entity.native_value == "mqtt"
    assert entity.extra_state_attributes["via_gateway"] == "HUB_A"


def test_gateway_route_is_used_when_hub_id_is_missing() -> None:
    """SCN-013: the route supplies the bridge identity as a fallback."""
    device = _device(hub_device_id=None)
    entity = _sensor(
        device,
        _health(mqtt=True),
        route={"device": "HUB_ROUTE", "topic": "commands"},
    )
    assert entity.extra_state_attributes["via_gateway"] == "HUB_ROUTE"


def test_hub_device_id_takes_precedence_over_route() -> None:
    """SCN-014: BFF-style bridge metadata has precedence."""
    device = _device(hub_device_id="HUB_A")
    entity = _sensor(device, _health(mqtt=True), route={"device": "HUB_B"})
    assert entity.extra_state_attributes["via_gateway"] == "HUB_A"


def test_unbridged_device_omits_via_gateway() -> None:
    """SCN-015: no bridge source means no bridge attribute."""
    assert "via_gateway" not in _sensor(_device(hub_device_id=None), _health(mqtt=True)).extra_state_attributes


def test_bridge_attribute_is_independent_of_transport() -> None:
    """SCN-016: bridge identity remains when cloud API is the selected route."""
    device = _device(hub_device_id="HUB_A")
    entity = _sensor(device, _health(cloud_api=True))
    assert entity.native_value == "cloud_api"
    assert entity.extra_state_attributes["via_gateway"] == "HUB_A"


def test_demoted_lan_is_skipped() -> None:
    """SCN-017: a read-driven LAN demotion lets MQTT win."""
    health = _health(mqtt=True)
    health["lan"].last_failure_reason = "stale_lan"
    assert _sensor(_device(), health).native_value == "mqtt"


def test_last_evaluated_at_is_utc_iso_timestamp() -> None:
    """SCN-018: the diagnostic timestamp is timezone-aware ISO 8601."""
    timestamp = _sensor(_device(), _health(lan=True)).extra_state_attributes["last_evaluated_at"]
    parsed = datetime.fromisoformat(timestamp)
    assert parsed.utcoffset() == timedelta(0)
    assert parsed <= datetime.now(timezone.utc) + timedelta(seconds=2)


@pytest.mark.parametrize(
    ("value", "expected_icon"),
    [
        ("lan", "mdi:lan"),
        ("mqtt", "mdi:cloud-sync"),
        ("cloud_api", "mdi:cloud"),
        ("ble", "mdi:bluetooth"),
        ("unavailable", "mdi:lan-pending"),
    ],
)
def test_icon_matches_every_value(value: str, expected_icon: str) -> None:
    """SCN-019: every enumerated value has its documented icon."""
    health = _health(**{value: True}) if value != "unavailable" else _health()
    entity = _sensor(_device(), health)
    assert entity.native_value == value
    assert entity.icon == expected_icon


def test_entity_is_diagnostic_enum_without_state_class() -> None:
    """SCN-020: classification is diagnostic, enumerated, and stateless."""
    entity = _sensor(_device(), _health(lan=True))
    assert entity.entity_category == EntityCategory.DIAGNOSTIC
    assert entity.device_class == SensorDeviceClass.ENUM
    assert entity.options == ["lan", "mqtt", "cloud_api", "ble", "unavailable"]
    assert entity.state_class is None
    assert entity.has_entity_name is True
    assert entity.translation_key == "connection_mode"


def test_available_ignores_device_online_flag() -> None:
    """SCN-021: this diagnostic remains visible when the cloud state is offline."""
    assert _sensor(_device(), _health(), online=False).available is True


class _TickCoordinator:
    def __init__(self, device: GoveeDevice, health: dict[str, TransportHealth]) -> None:
        self.devices = {device.device_id: device}
        self.last_update_success = True
        self._health = health
        self._listeners: list[Any] = []

    def get_transport_health(self, _device_id: str, kind: str) -> TransportHealth | None:
        return self._health.get(kind)

    def async_add_listener(self, listener: Any) -> Any:
        self._listeners.append(listener)
        return lambda: self._listeners.remove(listener)

    def async_set_updated_data(self, data: dict[str, Any]) -> None:
        del data
        for listener in tuple(self._listeners):
            listener()


@pytest.mark.asyncio
async def test_refresh_happens_on_coordinator_tick_only() -> None:
    """SCN-023: health changes render only through the coordinator listener."""
    device = _device()
    health = _health()
    coordinator = _TickCoordinator(device, health)
    entity = GoveeConnectionModeSensor(coordinator, device)  # type: ignore[arg-type]
    entity.async_write_ha_state = MagicMock()
    coordinator.async_add_listener(entity._handle_coordinator_update)

    health["mqtt"].is_available = True
    assert entity.native_value == "mqtt"
    entity.async_write_ha_state.assert_not_called()
    coordinator.async_set_updated_data({device.device_id: GoveeDeviceState(device_id=device.device_id)})
    entity.async_write_ha_state.assert_called_once()


@pytest.mark.asyncio
async def test_setup_creates_one_connection_mode_sensor_per_device() -> None:
    """SCN-022: setup includes exactly one sensor for every physical and group device."""
    devices = [_device(f"AA:BB:CC:DD:EE:FF:00:{i:02d}") for i in range(1, 4)] + [
        _device(f"GROUP:{i}", is_group=True) for i in range(1, 3)
    ]
    coordinator = MagicMock()
    coordinator.devices = {device.device_id: device for device in devices}
    coordinator.mqtt_client = None
    coordinator.leak_sensors = {}
    coordinator.get_state.return_value = None
    coordinator.is_bff_leak_sensor.return_value = False
    coordinator.register_thermo_hubs = MagicMock()
    coordinator.register_leak_hubs = MagicMock()
    entry = MagicMock()
    entry.runtime_data = coordinator
    added: list[Any] = []

    await async_setup_entry(MagicMock(), entry, lambda entities: added.extend(entities))
    connection_sensors = [e for e in added if isinstance(e, GoveeConnectionModeSensor)]
    assert len(connection_sensors) == len(devices)
    assert {e.unique_id for e in connection_sensors} == {f"{d.device_id}_connection_mode" for d in devices}


def test_translation_entry_has_all_connection_mode_states() -> None:
    """SCN-024: source and English translation register all five values."""
    base = Path(__file__).resolve().parent.parent / "custom_components" / "govee"
    expected = {
        "name": "Connection Mode",
        "state": {
            "lan": "LAN",
            "mqtt": "MQTT",
            "cloud_api": "Cloud API",
            "ble": "Bluetooth",
            "unavailable": "Unavailable",
        },
    }
    with (base / "strings.json").open() as strings_file, (base / "translations/en.json").open() as en_file:
        strings = json.load(strings_file)
        english = json.load(en_file)
    assert strings["entity"]["sensor"]["connection_mode"] == expected
    assert english["entity"]["sensor"]["connection_mode"] == expected
