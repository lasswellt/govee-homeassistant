"""A fresh local reading should suppress the cloud read, not follow it.

LAN/MQTT/BLE readings used to be overlaid *after* the cloud call had already
been spent. These cover the inversion: check first, and only pay for a cloud
read when no local source has answered recently.
"""

from __future__ import annotations

import zlib

from datetime import timedelta
from types import SimpleNamespace
from typing import Any

from homeassistant.util import dt as dt_util

from custom_components.govee.const import IDLE_DEVICE_POLL_DIVISOR, MAX_LOCAL_FRESH_SKIPS
from custom_components.govee.coordinator import GoveeCoordinator
from custom_components.govee.models.transport import TransportHealth
from custom_components.govee.request_budget import local_reading_is_fresh
from custom_components.govee.transport_health import TransportHealthTracker


def test_fresh_reading_qualifies_and_a_stale_one_does_not() -> None:
    common: dict[str, Any] = {
        "freshness_window": 60.0,
        "consecutive_skips": 0,
        "max_consecutive_skips": MAX_LOCAL_FRESH_SKIPS,
    }
    assert local_reading_is_fresh(seconds_since_local_reading=5.0, **common) is True
    assert local_reading_is_fresh(seconds_since_local_reading=59.9, **common) is True
    assert local_reading_is_fresh(seconds_since_local_reading=60.0, **common) is False
    assert local_reading_is_fresh(seconds_since_local_reading=600.0, **common) is False


def test_device_with_no_local_transport_is_always_polled() -> None:
    assert (
        local_reading_is_fresh(
            seconds_since_local_reading=None,
            freshness_window=60.0,
            consecutive_skips=0,
            max_consecutive_skips=MAX_LOCAL_FRESH_SKIPS,
        )
        is False
    )


def test_skip_run_is_capped_so_the_cloud_still_reconciles() -> None:
    """Even a permanently fresh local reading forces a cloud read eventually."""
    assert (
        local_reading_is_fresh(
            seconds_since_local_reading=1.0,
            freshness_window=60.0,
            consecutive_skips=MAX_LOCAL_FRESH_SKIPS,
            max_consecutive_skips=MAX_LOCAL_FRESH_SKIPS,
        )
        is False
    )


class _FreshnessStub(SimpleNamespace):
    """Only what _locally_fresh_devices and _local_last_updated read.

    ``_local_last_updated`` is bound onto the stub so the two methods
    collaborate exactly as they do on the real coordinator.
    """

    def _local_last_updated(self, device_id: str) -> Any:
        return GoveeCoordinator._local_last_updated(self, device_id)


def _coordinator(*, ages: dict[str, float | None], has_state: bool = True) -> Any:
    """A stub carrying transport health of the given ages, per device."""
    tracker = TransportHealthTracker()
    now = dt_util.utcnow()
    for device_id, age in ages.items():
        tracker.ensure(device_id)
        if age is not None:
            health: TransportHealth = tracker.health[device_id]["lan"]
            health.last_read_ts = now - timedelta(seconds=age)
        # A cloud read is always recent; it must not count as "local".
        tracker.health[device_id]["cloud_api"].last_success_ts = now
    return _FreshnessStub(
        _transport=tracker,
        _states={device_id: object() for device_id in ages} if has_state else {},
        _local_fresh_skips={},
        update_interval=timedelta(seconds=60),
        _original_update_interval=timedelta(seconds=60),
    )


def _pollable(ages: dict[str, float | None]) -> dict[str, Any]:
    return {device_id: object() for device_id in ages}


def test_poll_skips_the_device_with_a_fresh_lan_reading_only() -> None:
    ages = {"fresh": 5.0, "stale": 600.0, "never": None}
    coordinator = _coordinator(ages=ages)
    skipped = GoveeCoordinator._locally_fresh_devices(coordinator, _pollable(ages))
    assert skipped == {"fresh"}


def test_a_recent_cloud_read_alone_never_earns_a_skip() -> None:
    """cloud_api is deliberately excluded: it cannot vouch for itself."""
    ages: dict[str, float | None] = {"cloud_only": None}
    coordinator = _coordinator(ages=ages)
    assert GoveeCoordinator._local_last_updated(coordinator, "cloud_only") is None
    assert GoveeCoordinator._locally_fresh_devices(coordinator, _pollable(ages)) == set()


def test_device_without_state_yet_is_never_skipped() -> None:
    ages = {"fresh": 1.0}
    coordinator = _coordinator(ages=ages, has_state=False)
    assert GoveeCoordinator._locally_fresh_devices(coordinator, _pollable(ages)) == set()


def test_consecutive_skips_are_counted_and_then_forced_to_reconcile() -> None:
    ages = {"fresh": 1.0}
    coordinator = _coordinator(ages=ages)
    coordinator._local_fresh_skips["fresh"] = 0  # pin the stagger offset for this run
    for cycle in range(MAX_LOCAL_FRESH_SKIPS):
        assert GoveeCoordinator._locally_fresh_devices(coordinator, _pollable(ages)) == {"fresh"}
        assert coordinator._local_fresh_skips["fresh"] == cycle + 1
    # The cap is reached: this cycle pays for a cloud read and the run restarts.
    assert GoveeCoordinator._locally_fresh_devices(coordinator, _pollable(ages)) == set()
    assert coordinator._local_fresh_skips["fresh"] == 0
    assert GoveeCoordinator._locally_fresh_devices(coordinator, _pollable(ages)) == {"fresh"}


def test_devices_start_at_different_points_in_their_run_of_skips() -> None:
    """Otherwise every device reaches the cap together and the cloud reads arrive in one burst."""
    ids = [f"AA:BB:CC:DD:EE:FF:00:{n:02X}" for n in range(24)]
    ages = {device_id: 1.0 for device_id in ids}
    coordinator = _coordinator(ages=ages)

    GoveeCoordinator._locally_fresh_devices(coordinator, _pollable(ages))

    starts = {count for count in coordinator._local_fresh_skips.values()}
    assert len(starts) > 1


def test_the_stagger_offset_is_stable_per_device() -> None:
    ages = {"AA:BB:CC:DD:EE:FF:00:01": 1.0}
    first = _coordinator(ages=ages)
    second = _coordinator(ages=ages)

    GoveeCoordinator._locally_fresh_devices(first, _pollable(ages))
    GoveeCoordinator._locally_fresh_devices(second, _pollable(ages))

    assert first._local_fresh_skips == second._local_fresh_skips


def test_a_solicited_lan_read_from_the_previous_cycle_still_counts() -> None:
    """LAN reads run at the tail of a cycle, so at the next cycle's start they are a full interval old.

    A window of exactly one poll interval would never admit them.
    """
    ages = {"lan": 63.0}  # 60 s poll + a few seconds of tail
    coordinator = _coordinator(ages=ages)

    assert GoveeCoordinator._locally_fresh_devices(coordinator, _pollable(ages)) == {"lan"}


def test_a_reading_older_than_the_widened_window_does_not_count() -> None:
    ages = {"quiet": 100.0}  # 1.5 x 60 s = 90 s
    coordinator = _coordinator(ages=ages)

    assert GoveeCoordinator._locally_fresh_devices(coordinator, _pollable(ages)) == set()


def test_a_send_or_a_discarded_reply_is_not_a_reading() -> None:
    """last_success_ts also moves on writes and mismatched replies; only last_read_ts vouches for state."""
    tracker = TransportHealthTracker()
    tracker.record_success("dev", "lan")
    tracker.record_send("dev", "lan")
    tracker.record_success("dev", "ble")
    coordinator = _FreshnessStub(
        _transport=tracker,
        _states={"dev": object()},
        _local_fresh_skips={},
        update_interval=timedelta(seconds=60),
        _original_update_interval=timedelta(seconds=60),
    )

    assert GoveeCoordinator._local_last_updated(coordinator, "dev") is None
    assert GoveeCoordinator._locally_fresh_devices(coordinator, {"dev": object()}) == set()


class _FakeRegistryEntry:
    """Minimal stand-in for an entity registry entry."""

    def __init__(self, unique_id: str, disabled_by: str | None = None) -> None:
        self.unique_id = unique_id
        self.disabled_by = disabled_by


class TestPollSuppressesRedundantCloudReads:
    """End-to-end: the poll itself must not spend the request.

    Guards the inversion, not just the predicate — the LAN/MQTT/BLE overlay
    used to run after ``_fetch_device_state`` had already been called.
    """

    LOCAL = "AA:BB:CC:DD:EE:FF:00:33"
    CLOUD_ONLY = "AA:BB:CC:DD:EE:FF:00:44"

    def _coord(self, device_ids: list[str]) -> Any:
        import custom_components.govee.coordinator as coord_mod
        from unittest.mock import MagicMock

        from custom_components.govee.models.device import CAPABILITY_ON_OFF, INSTANCE_POWER
        from custom_components.govee.models import GoveeCapability, GoveeDevice, GoveeDeviceState

        config_entry = MagicMock()
        config_entry.entry_id = "test_entry"
        config_entry.options = {}
        coord = coord_mod.GoveeCoordinator(
            hass=MagicMock(),
            config_entry=config_entry,
            api_client=MagicMock(),
            iot_credentials=None,
            poll_interval=60,
        )
        caps = (GoveeCapability(type=CAPABILITY_ON_OFF, instance=INSTANCE_POWER, parameters={}),)
        for device_id in device_ids:
            coord._devices[device_id] = GoveeDevice(
                device_id=device_id,
                sku="H6008",
                name=f"Bulb {device_id[-2:]}",
                device_type="devices.types.light",
                capabilities=caps,
                is_group=False,
            )
            coord._states[device_id] = GoveeDeviceState.create_empty(device_id)
        coord.async_set_updated_data = MagicMock()
        return coord, coord_mod

    async def test_a_fresh_lan_reading_costs_no_cloud_request(self, monkeypatch: Any) -> None:
        from unittest.mock import MagicMock

        from custom_components.govee.models import GoveeDeviceState

        coord, coord_mod = self._coord([self.LOCAL, self.CLOUD_ONLY])

        async def _noop(*args: Any, **kwargs: Any) -> None:
            return None

        monkeypatch.setattr(coord, "_async_maybe_rediscover_devices", _noop)
        monkeypatch.setattr(coord, "_async_maybe_rescan_lan", _noop)
        monkeypatch.setattr(coord, "_refresh_lan_reads", _noop)
        monkeypatch.setattr(coord._ble_handler, "enroll_from_cache", lambda: None)
        monkeypatch.setattr(coord_mod.er, "async_get", lambda hass: MagicMock())
        monkeypatch.setattr(
            coord_mod.er,
            "async_entries_for_config_entry",
            lambda registry, entry_id: [
                _FakeRegistryEntry(self.LOCAL, disabled_by=None),
                _FakeRegistryEntry(self.CLOUD_ONLY, disabled_by=None),
            ],
        )

        # One device answered over LAN a moment ago; the other has no local
        # transport at all.
        coord._record_transport_success(self.LOCAL, "lan")
        coord._transport.record_read(self.LOCAL, "lan")

        fetched: list[str] = []

        async def _fetch(device_id: str, device: Any) -> Any:
            fetched.append(device_id)
            fresh = GoveeDeviceState.create_empty(device_id)
            fresh.source = "api"
            return fresh

        monkeypatch.setattr(coord, "_fetch_device_state", _fetch)

        before = coord._states[self.LOCAL]

        result = await coord._async_update_data()

        # Only the cloud-only device cost a request.
        assert fetched == [self.CLOUD_ONLY]
        # The skipped device keeps the state object it already had, rather
        # than being dropped or replaced by a cloud read.
        assert result[self.LOCAL] is before
        assert coord._local_fresh_skips[self.LOCAL] == zlib.crc32(self.LOCAL.encode()) % MAX_LOCAL_FRESH_SKIPS + 1
        assert coord._local_fresh_skips[self.CLOUD_ONLY] == 0

    async def test_a_cycle_where_every_device_is_covered_still_runs_its_tail(self, monkeypatch: Any) -> None:
        """Nothing to fetch is a normal cycle now, and must not skip transport health, LAN reads or pacing."""
        from unittest.mock import AsyncMock, MagicMock

        coord, coord_mod = self._coord([self.LOCAL])

        monkeypatch.setattr(coord, "_async_maybe_rediscover_devices", AsyncMock())
        rescan = AsyncMock()
        lan_reads = AsyncMock()
        monkeypatch.setattr(coord, "_async_maybe_rescan_lan", rescan)
        monkeypatch.setattr(coord, "_refresh_lan_reads", lan_reads)
        monkeypatch.setattr(coord, "_refresh_lan_staleness", MagicMock())
        monkeypatch.setattr(coord, "_refresh_mqtt_health", MagicMock())
        monkeypatch.setattr(coord, "_refresh_ble_staleness", MagicMock())
        monkeypatch.setattr(coord._ble_handler, "enroll_from_cache", lambda: None)
        monkeypatch.setattr(coord_mod.er, "async_get", lambda hass: MagicMock())
        monkeypatch.setattr(
            coord_mod.er,
            "async_entries_for_config_entry",
            lambda registry, entry_id: [_FakeRegistryEntry(self.LOCAL, disabled_by=None)],
        )
        pacing = MagicMock()
        monkeypatch.setattr(coord, "_apply_budget_pacing", pacing)
        fetch = AsyncMock()
        monkeypatch.setattr(coord, "_fetch_device_state", fetch)

        coord._local_fresh_skips[self.LOCAL] = 0
        coord._record_transport_success(self.LOCAL, "lan")
        coord._transport.record_read(self.LOCAL, "lan")

        result = await coord._async_update_data()

        fetch.assert_not_awaited()
        assert result is coord._states
        rescan.assert_awaited_once()
        lan_reads.assert_awaited_once()
        coord._refresh_mqtt_health.assert_called_once()
        coord._refresh_ble_staleness.assert_called_once()
        # Sized on the whole pollable set, not the empty remainder.
        pacing.assert_called_once_with(1)


class TestIdleCadenceAndDeferralThroughThePoll:
    """End to end through _async_update_data, not the helpers alone."""

    LOCAL = TestPollSuppressesRedundantCloudReads.LOCAL
    _coord = TestPollSuppressesRedundantCloudReads._coord

    def _ready(self, monkeypatch: Any, *, budget: int) -> Any:
        from unittest.mock import AsyncMock, MagicMock

        from custom_components.govee.models import GoveeDeviceState

        coord, coord_mod = self._coord([self.LOCAL])
        coord._daily_request_budget = budget
        monkeypatch.setattr(coord, "_async_maybe_rediscover_devices", AsyncMock())
        monkeypatch.setattr(coord, "_async_maybe_rescan_lan", AsyncMock())
        monkeypatch.setattr(coord, "_refresh_lan_reads", AsyncMock())
        monkeypatch.setattr(coord, "_refresh_lan_staleness", MagicMock())
        monkeypatch.setattr(coord, "_refresh_mqtt_health", MagicMock())
        monkeypatch.setattr(coord, "_refresh_ble_staleness", MagicMock())
        monkeypatch.setattr(coord._ble_handler, "enroll_from_cache", lambda: None)
        monkeypatch.setattr(coord_mod.er, "async_get", lambda hass: MagicMock())
        monkeypatch.setattr(
            coord_mod.er,
            "async_entries_for_config_entry",
            lambda registry, entry_id: [_FakeRegistryEntry(self.LOCAL, disabled_by=None)],
        )
        fetched: list[str] = []

        async def _fetch(device_id: str, device: Any) -> Any:
            fetched.append(device_id)
            fresh = GoveeDeviceState.create_empty(device_id)
            fresh.power_state = False
            return fresh

        monkeypatch.setattr(coord, "_fetch_device_state", _fetch)
        # Off, and unchanged for two hours: as idle as a device gets.
        coord._states[self.LOCAL].power_state = False
        coord._state_changed_at[self.LOCAL] = dt_util.utcnow() - timedelta(hours=2)
        return coord, fetched

    async def test_an_install_under_budget_polls_an_idle_device_every_cycle(self, monkeypatch: Any) -> None:
        coord, fetched = self._ready(monkeypatch, budget=9000)  # 1 device x 1,440 cycles fits easily

        for _ in range(8):
            await coord._async_update_data()

        assert fetched == [self.LOCAL] * 8

    async def test_an_install_over_budget_holds_an_idle_device_back(self, monkeypatch: Any) -> None:
        coord, fetched = self._ready(monkeypatch, budget=500)  # 1 device x 1,440 cycles does not

        for _ in range(8):
            await coord._async_update_data()

        assert len(fetched) == 8 // IDLE_DEVICE_POLL_DIVISOR

    async def test_a_deferred_cycle_runs_the_tail_and_keeps_the_interval_it_chose(self, monkeypatch: Any) -> None:
        from unittest.mock import MagicMock

        coord, fetched = self._ready(monkeypatch, budget=9000)
        coord._api_client = MagicMock()
        coord._api_client.rate_limit_remaining = 0
        coord._api_client.rate_limit_reset_in = 200
        coord._api_client.requests_today = 0
        pacing = MagicMock()
        monkeypatch.setattr(coord, "_apply_budget_pacing", pacing)

        await coord._async_update_data()

        assert fetched == []
        assert coord.update_interval == timedelta(seconds=200)
        pacing.assert_not_called()
        coord._refresh_mqtt_health.assert_called_once()
        coord._async_maybe_rescan_lan.assert_awaited_once()

        # The next cycle polls even though the headers cannot have refreshed.
        await coord._async_update_data()
        assert fetched == [self.LOCAL]
