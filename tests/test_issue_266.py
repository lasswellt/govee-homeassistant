"""#266: probe reads are paced, temperatures first, limits every 6th sweep."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import custom_components.govee.coordinator as coord_mod
from custom_components.govee.api.probe_thermometer import build_limits_read_packet, build_probe_read_packet
from custom_components.govee.coordinator import GoveeCoordinator
from custom_components.govee.models import GoveeDevice
from tests.test_cov_coordinator_setup import PROBE, _add, _coordinator, _TaskCapture, _TimerCapture

H5194 = "AA:BB:CC:DD:EE:FF:51:94"
SPACING = 1.0


def _armed(*devices: tuple[str, str]) -> tuple[GoveeCoordinator, list[tuple[str, bytes]]]:
    """A coordinator with armed probes, a connected client and a recording BLE manager."""
    coord = _coordinator()
    for device_id, sku in devices:
        _add(coord, GoveeDevice.synthetic_probe_thermometer(device_id=device_id, sku=sku, name=sku))
        coord._probe_polling_enabled.add(device_id)
    coord._mqtt_client = MagicMock(connected=True)
    sent: list[tuple[str, bytes]] = []

    async def _send(device_id: str, sku: str, packet: bytes) -> bool:
        sent.append((device_id, packet))
        return True

    coord._ble_manager = MagicMock()
    coord._ble_manager.async_send_ble_packet = AsyncMock(side_effect=_send)
    return coord, sent


@pytest.fixture
def sleeps(monkeypatch) -> list[float]:
    """Record asyncio.sleep calls made by the coordinator without waiting."""
    recorded: list[float] = []

    async def _sleep(delay: float) -> None:
        recorded.append(delay)

    monkeypatch.setattr(coord_mod.asyncio, "sleep", _sleep)
    return recorded


@pytest.mark.asyncio
async def test_temperatures_go_before_limits(sleeps):
    coord, sent = _armed((H5194, "H5194"))

    await coord._poll_probe_thermometers()

    packets = [packet for _, packet in sent]
    assert packets == [build_probe_read_packet(p) for p in (1, 2, 3, 4)] + [
        build_limits_read_packet(p) for p in (1, 2, 3, 4)
    ]


@pytest.mark.asyncio
async def test_limits_only_on_every_sixth_tick(sleeps):
    coord, sent = _armed((PROBE, "H5192"))
    counts = []
    for _ in range(7):
        sent.clear()
        await coord._poll_probe_thermometers()
        counts.append(len(sent))

    assert counts == [4, 2, 2, 2, 2, 2, 4]


@pytest.mark.asyncio
async def test_h5192_and_h5194_packet_counts(sleeps):
    coord, sent = _armed((PROBE, "H5192"), (H5194, "H5194"))

    await coord._poll_probe_thermometers()

    assert sum(1 for device_id, _ in sent if device_id == PROBE) == 4
    assert sum(1 for device_id, _ in sent if device_id == H5194) == 8


@pytest.mark.asyncio
async def test_spacing_between_packets_only(sleeps):
    coord, sent = _armed((PROBE, "H5192"), (H5194, "H5194"))

    await coord._poll_probe_thermometers()

    assert sleeps == [coord_mod.PROBE_PACKET_SPACING] * (len(sent) - 1)
    assert coord_mod.PROBE_PACKET_SPACING == SPACING


@pytest.mark.asyncio
async def test_disarm_mid_sweep_stops_that_device(sleeps):
    coord, sent = _armed((H5194, "H5194"))
    original = coord._ble_manager.async_send_ble_packet.side_effect

    async def _send(device_id, sku, packet):
        await original(device_id, sku, packet)
        coord._probe_polling_enabled.discard(device_id)
        return True

    coord._ble_manager.async_send_ble_packet.side_effect = _send

    await coord._poll_probe_thermometers()

    assert len(sent) == 1


@pytest.mark.asyncio
async def test_disconnect_mid_sweep_stops_sending(sleeps):
    coord, sent = _armed((H5194, "H5194"))
    original = coord._ble_manager.async_send_ble_packet.side_effect

    async def _send(device_id, sku, packet):
        await original(device_id, sku, packet)
        coord._mqtt_client = None
        return True

    coord._ble_manager.async_send_ble_packet.side_effect = _send

    await coord._poll_probe_thermometers()

    assert len(sent) == 1


@pytest.mark.asyncio
async def test_unknown_device_is_skipped(sleeps):
    coord, sent = _armed((PROBE, "H5192"))
    coord._probe_polling_enabled.add("gone")

    await coord._poll_probe_thermometers()

    assert {device_id for device_id, _ in sent} == {PROBE}


@pytest.mark.asyncio
@pytest.mark.parametrize(("elapsed", "expected"), [(5.0, 25.0), (29.5, SPACING), (100.0, SPACING)])
async def test_rearm_delay_is_interval_minus_elapsed(monkeypatch, elapsed, expected):
    timers = _TimerCapture()
    monkeypatch.setattr(coord_mod, "async_call_later", timers)
    clock = iter([1000.0, 1000.0 + elapsed])
    coord, _ = _armed((PROBE, "H5192"))
    monkeypatch.setattr(coord_mod, "time", SimpleNamespace(monotonic=lambda: next(clock)))
    coord._poll_probe_thermometers = AsyncMock()
    assert coord._probe_poll_interval == 30

    await coord._run_probe_sweep()

    assert [delay for delay, _ in timers.calls] == [expected]


@pytest.mark.asyncio
async def test_timer_callback_runs_the_sweep_as_a_tracked_task():
    coord, _ = _armed((PROBE, "H5192"))
    tasks = _TaskCapture()
    coord._config_entry.async_create_background_task = tasks
    coord._run_probe_sweep = AsyncMock()

    await coord._probe_poll_callback()

    assert tasks.names == ["govee_probe_poll"]
    assert coord._probe_poll_task is not None
    await tasks.run_all()
    coord._run_probe_sweep.assert_awaited_once()


@pytest.mark.asyncio
async def test_shutdown_cancels_the_running_sweep():
    coord = _coordinator()
    coord._api_client.close = AsyncMock()
    task = MagicMock()
    task.done.return_value = False
    coord._probe_poll_task = task

    await coord.async_shutdown()

    task.cancel.assert_called_once()
    assert coord._probe_poll_task is None


@pytest.mark.asyncio
async def test_newly_armed_device_reads_limits_whatever_the_tick(sleeps, monkeypatch):
    monkeypatch.setattr(coord_mod, "async_call_later", _TimerCapture())
    coord, sent = _armed((PROBE, "H5192"))
    coord._probe_polling_enabled.discard(PROBE)
    coord._probe_poll_tick = 3
    tasks = _TaskCapture()
    coord._config_entry.async_create_background_task = tasks

    coord.set_probe_polling(PROBE, True)
    await tasks.run_all()

    assert len(sent) == 4
    assert coord._probe_poll_tick == 3
