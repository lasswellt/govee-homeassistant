"""Issue #246: back off BLE after failures and prefer a healthy LAN for LAN-capable commands."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

import custom_components.govee.coordinator as coord_mod
from custom_components.govee.models import BrightnessCommand, PowerCommand, ToggleCommand

from .test_cov_coordinator_control import DEV, _add, _coordinator, _device, _lan_ready


def _ble_coord(monkeypatch, *, fail: bool = False):
    monkeypatch.setattr(coord_mod, "HAS_BLUETOOTH", True)
    coord = _coordinator()
    _add(coord, _device(sku="H1270"))
    ble = MagicMock()
    ble.turn_on = AsyncMock(side_effect=OSError("no connection slot") if fail else None)
    ble.turn_off = AsyncMock()
    ble.set_brightness = AsyncMock()
    coord._ble_devices[DEV] = ble
    coord._record_transport_success(DEV, "ble")
    return coord, ble


@pytest.mark.asyncio
async def test_failure_sets_backoff_and_blocks_eligibility(monkeypatch):
    coord, _ = _ble_coord(monkeypatch, fail=True)
    monkeypatch.setattr(coord_mod.time, "monotonic", lambda: 1000.0)
    assert coord._ble_write_eligible(DEV) is True
    assert await coord._try_ble_command(DEV, PowerCommand(power_on=True)) is False
    assert coord._ble_connect_backoff_until[DEV] == 1300.0
    assert coord._ble_connect_failures[DEV] == 1
    assert coord._ble_write_eligible(DEV) is False
    monkeypatch.setattr(coord_mod.time, "monotonic", lambda: 1301.0)
    coord._record_transport_success(DEV, "ble")
    assert coord._ble_write_eligible(DEV) is True


@pytest.mark.asyncio
async def test_backoff_doubles_and_caps(monkeypatch):
    coord, _ = _ble_coord(monkeypatch, fail=True)
    monkeypatch.setattr(coord_mod.time, "monotonic", lambda: 0.0)
    seen = []
    for _ in range(6):
        await coord._try_ble_command(DEV, PowerCommand(power_on=True))
        seen.append(coord._ble_connect_backoff_until[DEV])
    assert seen == [300.0, 600.0, 1200.0, 2400.0, 3600.0, 3600.0]


@pytest.mark.asyncio
async def test_success_resets_backoff(monkeypatch):
    coord, ble = _ble_coord(monkeypatch, fail=True)
    await coord._try_ble_command(DEV, PowerCommand(power_on=True))
    await coord._try_ble_command(DEV, PowerCommand(power_on=True))
    assert coord._ble_connect_failures[DEV] == 2
    ble.turn_on.side_effect = None
    assert await coord._try_ble_command(DEV, PowerCommand(power_on=True)) is True
    assert DEV not in coord._ble_connect_failures
    assert DEV not in coord._ble_connect_backoff_until


@pytest.mark.asyncio
async def test_lan_capable_command_skips_ble_when_lan_healthy(monkeypatch):
    coord, ble = _ble_coord(monkeypatch)
    _lan_ready(coord, MagicMock())
    coord._try_lan_command = AsyncMock(return_value=True)
    assert await coord.async_control_device(DEV, BrightnessCommand(brightness=50)) is True
    ble.set_brightness.assert_not_awaited()
    coord._try_lan_command.assert_awaited_once()


@pytest.mark.asyncio
async def test_lan_capable_command_uses_ble_when_lan_unhealthy(monkeypatch):
    coord, ble = _ble_coord(monkeypatch)
    assert await coord.async_control_device(DEV, BrightnessCommand(brightness=50)) is True
    ble.set_brightness.assert_awaited_once()


@pytest.mark.asyncio
async def test_non_lan_command_still_uses_ble_when_lan_healthy(monkeypatch):
    coord, _ = _ble_coord(monkeypatch)
    _lan_ready(coord, MagicMock())
    coord._try_ble_command = AsyncMock(return_value=True)
    await coord.async_control_device(DEV, ToggleCommand(toggle_instance="gradientToggle", enabled=True))
    coord._try_ble_command.assert_awaited_once()


@pytest.mark.asyncio
async def test_backoff_routes_command_to_lan(monkeypatch):
    coord, ble = _ble_coord(monkeypatch, fail=True)
    await coord._try_ble_command(DEV, PowerCommand(power_on=True))
    ble.turn_on.reset_mock()
    coord._try_lan_command = AsyncMock(return_value=True)
    assert await coord.async_control_device(DEV, PowerCommand(power_on=True)) is True
    ble.turn_on.assert_not_awaited()
