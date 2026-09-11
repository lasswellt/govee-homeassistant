"""Tests for per-device connectivity diagnostic entities.

Covers the user-visible entities that surface directional freshness:
- GoveeLastCommandSentSensor (TIMESTAMP) — last outbound command
- GoveeDeviceConnectivity (CONNECTIVITY) — overall reachability + the
  per-transport last-received / last-sent breakdown as attributes.
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from custom_components.govee.binary_sensor import GoveeDeviceConnectivity
from custom_components.govee.models import TransportHealth
from custom_components.govee.sensor import (
    GoveeLastCommandSentSensor,
    GoveeMqttLastReceivedPerDeviceSensor,
)


def _device() -> MagicMock:
    device = MagicMock()
    device.device_id = "AA:BB:CC:DD:EE:FF:00:11"
    device.sku = "H6072"
    device.name = "Test Lamp"
    device.is_group = False
    return device


def _connectivity(coordinator: MagicMock) -> GoveeDeviceConnectivity:
    device = _device()
    with patch.object(GoveeDeviceConnectivity, "__init__", lambda self, *a, **k: None):
        entity = GoveeDeviceConnectivity.__new__(GoveeDeviceConnectivity)
    entity.coordinator = coordinator
    entity._device = device
    entity._device_id = device.device_id
    return entity


class TestLastCommandSentSensor:
    def test_native_value_delegates(self):
        ts = datetime(2026, 6, 5, tzinfo=timezone.utc)
        coordinator = MagicMock()
        coordinator.device_last_command_sent.return_value = ts
        device = _device()
        with patch.object(
            GoveeLastCommandSentSensor, "__init__", lambda self, *a, **k: None
        ):
            entity = GoveeLastCommandSentSensor.__new__(GoveeLastCommandSentSensor)
        entity.coordinator = coordinator
        entity._device = device
        assert entity.native_value == ts
        coordinator.device_last_command_sent.assert_called_once_with(device.device_id)


class TestMqttLastReceivedPerDeviceSensor:
    """Isolates the MQTT-only freshness signal from "Last Update Received"
    (the max across every transport), which stays "just now" from a healthy
    Cloud API poll alone even when MQTT specifically has gone quiet.
    """

    def test_native_value_delegates(self):
        ts = datetime(2026, 9, 11, tzinfo=timezone.utc)
        coordinator = MagicMock()
        coordinator.mqtt_last_receive_for.return_value = ts
        device = _device()
        with patch.object(
            GoveeMqttLastReceivedPerDeviceSensor,
            "__init__",
            lambda self, *a, **k: None,
        ):
            entity = GoveeMqttLastReceivedPerDeviceSensor.__new__(
                GoveeMqttLastReceivedPerDeviceSensor
            )
        entity.coordinator = coordinator
        entity._device = device
        assert entity.native_value == ts
        coordinator.mqtt_last_receive_for.assert_called_once_with(device.device_id)

    def test_native_value_none_before_any_mqtt_push(self):
        coordinator = MagicMock()
        coordinator.mqtt_last_receive_for.return_value = None
        device = _device()
        with patch.object(
            GoveeMqttLastReceivedPerDeviceSensor,
            "__init__",
            lambda self, *a, **k: None,
        ):
            entity = GoveeMqttLastReceivedPerDeviceSensor.__new__(
                GoveeMqttLastReceivedPerDeviceSensor
            )
        entity.coordinator = coordinator
        entity._device = device
        assert entity.native_value is None

    def test_unique_id_is_scoped_to_the_device(self):
        """The real __init__ (unpatched), for the unique_id it sets."""
        coordinator = MagicMock()
        device = _device()
        entity = GoveeMqttLastReceivedPerDeviceSensor(coordinator, device)
        assert entity.unique_id == f"{device.device_id}_mqtt_last_received"


class TestDeviceConnectivity:
    def test_is_on_true_when_any_transport_available(self):
        coordinator = MagicMock()
        coordinator.get_transport_health.side_effect = lambda did, kind: (
            TransportHealth(transport=kind, is_available=(kind == "mqtt"))
        )
        entity = _connectivity(coordinator)
        assert entity.is_on is True

    def test_is_on_false_when_all_unavailable(self):
        coordinator = MagicMock()
        coordinator.get_transport_health.side_effect = lambda did, kind: (
            TransportHealth(transport=kind, is_available=False)
        )
        entity = _connectivity(coordinator)
        assert entity.is_on is False

    def test_is_on_none_when_untracked(self):
        coordinator = MagicMock()
        coordinator.get_transport_health.return_value = None
        entity = _connectivity(coordinator)
        assert entity.is_on is None

    def test_attributes_directional_breakdown(self):
        recv = datetime(2026, 6, 5, 12, 0, tzinfo=timezone.utc)
        sent = datetime(2026, 6, 5, 12, 1, tzinfo=timezone.utc)
        cloud = TransportHealth(
            transport="cloud_api",
            is_available=True,
            last_success_ts=recv,
            last_send_ts=sent,
        )

        def _health(did, kind):
            return cloud if kind == "cloud_api" else None

        coordinator = MagicMock()
        coordinator.get_transport_health.side_effect = _health
        coordinator.mqtt_last_receive_for.return_value = None
        entity = _connectivity(coordinator)

        attrs = entity.extra_state_attributes
        assert attrs["cloud_api_last_received"] == recv.isoformat()
        assert attrs["cloud_api_last_sent"] == sent.isoformat()
        assert attrs["cloud_api_available"] is True
        # Untracked transports produce no keys.
        assert "mqtt_last_received" not in attrs

    def test_mqtt_per_device_receive_preferred(self):
        hub_recv = datetime(2026, 6, 5, 12, 0, tzinfo=timezone.utc)
        per_device = datetime(2026, 6, 5, 12, 5, tzinfo=timezone.utc)
        mqtt = TransportHealth(
            transport="mqtt", is_available=True, last_success_ts=hub_recv
        )

        def _health(did, kind):
            return mqtt if kind == "mqtt" else None

        coordinator = MagicMock()
        coordinator.get_transport_health.side_effect = _health
        coordinator.mqtt_last_receive_for.return_value = per_device
        entity = _connectivity(coordinator)

        attrs = entity.extra_state_attributes
        # Per-device MQTT receive wins over the hub-level last_success_ts.
        assert attrs["mqtt_last_received"] == per_device.isoformat()
