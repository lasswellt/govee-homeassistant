"""Tests for per-device transport health tracking + optimistic grace period."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.govee.const import LAN_STALE_SECONDS
from custom_components.govee.coordinator import GoveeCoordinator
from custom_components.govee.models import (
    TRANSPORT_KINDS,
    GoveeDeviceState,
    PowerCommand,
    TransportHealth,
)
from custom_components.govee.transport_health import TransportHealthTracker


def _bare_coordinator() -> GoveeCoordinator:
    """Construct a coordinator without running DataUpdateCoordinator.__init__."""
    coord = object.__new__(GoveeCoordinator)
    coord._devices = {}
    coord._states = {}
    coord._ble_devices = {}
    coord._transport = TransportHealthTracker()
    coord._mqtt_client = None
    # The LAN control tier (story LAN-012) reads self._lan_client first; a
    # bypassed __init__ must still provide it so async_control_device can fall
    # through LAN -> MQTT -> REST as these tests expect.
    coord._lan_client = None
    # The LAN write tier tracks write-confirm misses / suppression separately
    # from read-driven transport health (#57); provide them on the bypassed
    # __init__ so the control path can run without an AttributeError.
    coord._lan_write_misses = {}
    coord._lan_write_suppressed_until = {}
    return coord


class TestTransportHealth:
    def test_mark_success_and_failure(self):
        health = TransportHealth(transport="ble")
        now = datetime.now(timezone.utc)
        health.mark_success(now)
        assert health.is_available is True
        assert health.last_success_ts == now

        later = now + timedelta(seconds=5)
        health.mark_failure(later, "out_of_range")
        assert health.is_available is False
        assert health.last_failure_ts == later
        assert health.last_failure_reason == "out_of_range"

    def test_coordinator_records_success(self):
        coord = _bare_coordinator()
        coord._devices["dev1"] = MagicMock()
        coord._record_transport_success("dev1", "cloud_api")
        health = coord.get_transport_health("dev1", "cloud_api")
        assert health is not None
        assert health.is_available is True
        assert health.last_success_ts is not None

    def test_coordinator_records_failure(self):
        coord = _bare_coordinator()
        coord._devices["dev1"] = MagicMock()
        coord._record_transport_failure("dev1", "ble", "disconnected")
        health = coord.get_transport_health("dev1", "ble")
        assert health is not None
        assert health.is_available is False
        assert health.last_failure_reason == "disconnected"

    def test_get_transport_health_unknown_device(self):
        coord = _bare_coordinator()
        assert coord.get_transport_health("nope", "cloud_api") is None

    def test_refresh_mqtt_health_disconnected(self):
        coord = _bare_coordinator()
        coord._devices["dev1"] = MagicMock()
        coord._refresh_mqtt_health()
        health = coord.get_transport_health("dev1", "mqtt")
        assert health is not None
        assert health.is_available is False
        assert health.last_failure_reason == "not_configured"

    def test_refresh_mqtt_health_connected(self):
        coord = _bare_coordinator()
        coord._devices["dev1"] = MagicMock()
        mqtt_client = MagicMock()
        mqtt_client.connected = True
        coord._mqtt_client = mqtt_client
        coord._refresh_mqtt_health()
        health = coord.get_transport_health("dev1", "mqtt")
        assert health is not None
        assert health.is_available is True
        assert health.last_failure_reason is None


class TestDirectionalSplit:
    """Send (last_send_ts) and receive (last_success_ts) stay distinct."""

    def test_mark_send_stamps_send_not_receive(self):
        health = TransportHealth(transport="mqtt")
        now = datetime.now(timezone.utc)
        health.mark_send(now)
        assert health.is_available is True
        assert health.last_send_ts == now
        assert health.last_success_ts is None  # receive untouched

    def test_send_and_receive_do_not_overwrite(self):
        health = TransportHealth(transport="cloud_api")
        recv = datetime(2026, 1, 1, tzinfo=timezone.utc)
        send = datetime(2026, 6, 1, tzinfo=timezone.utc)
        health.mark_success(recv)
        health.mark_send(send)
        assert health.last_success_ts == recv
        assert health.last_send_ts == send

    def test_tracker_record_send(self):
        tracker = TransportHealthTracker()
        tracker.record_send("dev1", "cloud_api")
        health = tracker.get("dev1", "cloud_api")
        assert health is not None
        assert health.last_send_ts is not None
        assert health.last_success_ts is None

    def test_coordinator_records_send(self):
        coord = _bare_coordinator()
        coord._devices["dev1"] = MagicMock()
        coord._record_transport_send("dev1", "mqtt")
        health = coord.get_transport_health("dev1", "mqtt")
        assert health is not None
        assert health.is_available is True
        assert health.last_send_ts is not None
        assert health.last_success_ts is None

    @pytest.mark.asyncio
    async def test_cloud_api_control_stamps_both_directions(self):
        """A successful REST control sends and receives — stamp both."""
        coord = _bare_coordinator()
        coord._devices["dev1"] = MagicMock(is_group=False, decodes_kettle_frames=False, sku="H6072")
        coord._pending_power_off = set()
        coord._enable_mqtt_control = False
        coord._api_client = MagicMock()
        coord._api_client.control_device = AsyncMock(return_value=True)
        coord._apply_optimistic_update = MagicMock()
        coord.async_set_updated_data = MagicMock()

        ok = await coord.async_control_device("dev1", PowerCommand(power_on=True))

        assert ok is True
        health = coord.get_transport_health("dev1", "cloud_api")
        assert health is not None
        assert health.last_send_ts is not None  # command sent
        assert health.last_success_ts is not None  # response received

    def test_mqtt_last_receive_for_no_client(self):
        coord = _bare_coordinator()
        assert coord.mqtt_last_receive_for("dev1") is None

    def test_mqtt_last_receive_for_delegates(self):
        coord = _bare_coordinator()
        ts = datetime(2026, 6, 5, tzinfo=timezone.utc)
        client = MagicMock()
        client.last_message_ts_for.return_value = ts
        coord._mqtt_client = client
        assert coord.mqtt_last_receive_for("dev1") == ts
        client.last_message_ts_for.assert_called_once_with("dev1")


class TestDeviceDataLastUpdated:
    def test_none_until_any_transport_succeeds(self):
        coord = _bare_coordinator()
        coord._devices["dev1"] = MagicMock()
        coord._ensure_transport_health("dev1")
        assert coord.device_data_last_updated("dev1") is None

    def test_unknown_device_returns_none(self):
        coord = _bare_coordinator()
        assert coord.device_data_last_updated("nope") is None

    def test_returns_latest_across_transports(self):
        coord = _bare_coordinator()
        coord._devices["dev1"] = MagicMock()
        coord._ensure_transport_health("dev1")
        old = datetime(2026, 1, 1, tzinfo=timezone.utc)
        new = datetime(2026, 6, 1, tzinfo=timezone.utc)
        coord.get_transport_health("dev1", "cloud_api").mark_success(old)
        coord.get_transport_health("dev1", "mqtt").mark_success(new)
        coord.get_transport_health("dev1", "ble").mark_success(old)
        assert coord.device_data_last_updated("dev1") == new


class TestDeviceLastCommandSent:
    def test_none_until_any_send(self):
        coord = _bare_coordinator()
        coord._devices["dev1"] = MagicMock()
        coord._ensure_transport_health("dev1")
        assert coord.device_last_command_sent("dev1") is None

    def test_unknown_device_returns_none(self):
        coord = _bare_coordinator()
        assert coord.device_last_command_sent("nope") is None

    def test_returns_latest_send_across_transports(self):
        coord = _bare_coordinator()
        coord._devices["dev1"] = MagicMock()
        coord._ensure_transport_health("dev1")
        old = datetime(2026, 1, 1, tzinfo=timezone.utc)
        new = datetime(2026, 6, 1, tzinfo=timezone.utc)
        coord.get_transport_health("dev1", "cloud_api").mark_send(new)
        coord.get_transport_health("dev1", "mqtt").mark_send(old)
        assert coord.device_last_command_sent("dev1") == new

    def test_send_and_receive_independent(self):
        coord = _bare_coordinator()
        coord._devices["dev1"] = MagicMock()
        coord._ensure_transport_health("dev1")
        recv = datetime(2026, 1, 1, tzinfo=timezone.utc)
        sent = datetime(2026, 6, 1, tzinfo=timezone.utc)
        coord.get_transport_health("dev1", "cloud_api").mark_success(recv)
        coord.get_transport_health("dev1", "cloud_api").mark_send(sent)
        assert coord.device_data_last_updated("dev1") == recv
        assert coord.device_last_command_sent("dev1") == sent


class TestOptimisticGracePeriod:
    def test_apply_optimistic_stamps_timestamp(self):
        state = GoveeDeviceState.create_empty("dev1")
        assert state.last_optimistic_update is None
        state.apply_optimistic_power(True)
        assert state.last_optimistic_update is not None
        assert state.source == "optimistic"

    def test_mqtt_update_clears_optimistic_window(self):
        state = GoveeDeviceState.create_empty("dev1")
        state.apply_optimistic_power(True)
        assert state.last_optimistic_update is not None
        state.update_from_mqtt({"onOff": 1, "brightness": 50})
        assert state.last_optimistic_update is None
        assert state.source == "mqtt"

    def test_clear_optimistic_window_is_idempotent(self):
        state = GoveeDeviceState.create_empty("dev1")
        state.clear_optimistic_window()  # safe on fresh state
        assert state.last_optimistic_update is None
        state.apply_optimistic_power(True)
        state.clear_optimistic_window()
        assert state.last_optimistic_update is None


class TestSegmentLockInit:
    def test_segment_lock_dict_exists(self):
        """async_control_device assumes self._segment_locks is a dict."""
        coord = _bare_coordinator()
        coord._segment_locks = {}  # defensive — also set by real __init__
        assert isinstance(coord._segment_locks, dict)


class TestLanTransportKind:
    """The 'lan' transport kind is auto-provisioned alongside the others."""

    def test_lan_in_transport_kinds(self):
        assert "lan" in TRANSPORT_KINDS

    def test_ensure_provisions_lan_entry(self):
        tracker = TransportHealthTracker()
        tracker.ensure("dev1")
        health = tracker.get("dev1", "lan")
        assert health is not None
        assert isinstance(health, TransportHealth)
        assert health.transport == "lan"

    def test_lan_entry_defaults_unavailable(self):
        tracker = TransportHealthTracker()
        tracker.ensure("dev1")
        health = tracker.get("dev1", "lan")
        assert health is not None
        assert health.is_available is False
        assert health.last_success_ts is None
        assert health.last_send_ts is None
        assert health.last_failure_ts is None

    def test_ensure_provisions_every_kind_including_lan(self):
        tracker = TransportHealthTracker()
        tracker.ensure("dev1")
        for kind in TRANSPORT_KINDS:
            assert tracker.get("dev1", kind) is not None

    def test_lan_health_records_success(self):
        tracker = TransportHealthTracker()
        tracker.record_success("dev1", "lan")
        health = tracker.get("dev1", "lan")
        assert health is not None
        assert health.is_available is True
        assert health.last_success_ts is not None

    def test_coordinator_provisions_lan_via_ensure(self):
        coord = _bare_coordinator()
        coord._devices["dev1"] = MagicMock()
        coord._ensure_transport_health("dev1")
        assert coord.get_transport_health("dev1", "lan") is not None


class TestLanStaleness:
    """refresh_lan_staleness: no_lan_presence vs stale_lan, mirroring BLE."""

    def test_marks_no_lan_presence_for_non_lan_devices(self):
        """A device absent from lan_active_ids gets a meaningful reason."""
        tracker = TransportHealthTracker()
        tracker.refresh_lan_staleness(["dev1", "group1"], set())
        for device_id in ("dev1", "group1"):
            health = tracker.get(device_id, "lan")
            assert health is not None
            assert health.is_available is False
            assert health.last_failure_reason == "no_lan_presence"

    def test_marks_stale_lan_past_threshold(self):
        """A LAN-active device whose last read is too old goes stale_lan."""
        tracker = TransportHealthTracker()
        tracker.ensure("dev1")
        old = datetime.now(timezone.utc) - timedelta(seconds=LAN_STALE_SECONDS + 5)
        tracker.get("dev1", "lan").mark_success(old)

        tracker.refresh_lan_staleness(["dev1"], {"dev1"})

        health = tracker.get("dev1", "lan")
        assert health is not None
        assert health.is_available is False
        assert health.last_failure_reason == "stale_lan"

    def test_keeps_fresh_active_device_available(self):
        """A LAN-active device read within the window stays available."""
        tracker = TransportHealthTracker()
        tracker.record_success("dev1", "lan")  # fresh success, available

        tracker.refresh_lan_staleness(["dev1"], {"dev1"})

        health = tracker.get("dev1", "lan")
        assert health is not None
        assert health.is_available is True
        assert health.last_failure_reason is None

    def test_active_device_without_success_is_left_alone(self):
        """Active but never-read device is neither stale nor no_presence."""
        tracker = TransportHealthTracker()
        tracker.ensure("dev1")  # no success recorded -> last_success_ts None

        tracker.refresh_lan_staleness(["dev1"], {"dev1"})

        health = tracker.get("dev1", "lan")
        assert health is not None
        assert health.last_failure_reason is None  # not flagged stale

    def test_boundary_below_threshold_not_stale(self):
        """Just inside the window is still available (one missed poll tolerated)."""
        tracker = TransportHealthTracker()
        tracker.ensure("dev1")
        recent = datetime.now(timezone.utc) - timedelta(seconds=LAN_STALE_SECONDS - 5)
        tracker.get("dev1", "lan").mark_success(recent)

        tracker.refresh_lan_staleness(["dev1"], {"dev1"})

        health = tracker.get("dev1", "lan")
        assert health is not None
        assert health.is_available is True
        assert health.last_failure_reason is None

    def test_coordinator_refresh_lan_staleness_delegates(self):
        """Coordinator passes lan_active_ids = set(self._lan_devices)."""
        coord = _bare_coordinator()
        coord._devices["dev1"] = MagicMock()
        coord._devices["dev2"] = MagicMock()
        coord._lan_devices = {"dev1": MagicMock()}  # only dev1 is LAN-active
        coord._transport.record_success("dev1", "lan")  # fresh read

        coord._refresh_lan_staleness()

        h1 = coord.get_transport_health("dev1", "lan")
        h2 = coord.get_transport_health("dev2", "lan")
        assert h1 is not None and h1.is_available is True
        assert h2 is not None and h2.is_available is False
        assert h2.last_failure_reason == "no_lan_presence"

    def test_apply_lan_read_records_lan_success(self):
        """An applied devStatus read marks 'lan' health available (read=>success)."""
        from custom_components.govee.api.lan_client import LanDevStatus

        coord = _bare_coordinator()
        coord._devices["dev1"] = MagicMock(brightness_range=(0, 100))
        state = GoveeDeviceState.create_empty("dev1")
        coord._states["dev1"] = state
        coord.async_set_updated_data = MagicMock()

        coord._apply_lan_read(
            "dev1",
            LanDevStatus(on=True, brightness_0_100=50, color=None, color_temp_kelvin=None),
        )

        health = coord.get_transport_health("dev1", "lan")
        assert health is not None
        assert health.is_available is True
        assert health.last_success_ts is not None
