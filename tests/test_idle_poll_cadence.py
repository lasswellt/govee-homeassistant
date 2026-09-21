"""Idle devices are asked about less often; recently commanded ones more.

On a large install most devices are off most of the time, and each one still
cost a request every cycle. These cover the cadence that changed that, and
the guard that a command always restores the fast cadence.
"""

from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

from homeassistant.util import dt as dt_util

from custom_components.govee.const import (
    IDLE_DEVICE_AFTER_SECONDS,
    IDLE_DEVICE_POLL_DIVISOR,
    RECENT_COMMAND_WINDOW_SECONDS,
)
from custom_components.govee.coordinator import GoveeCoordinator
from custom_components.govee.models import GoveeDeviceState
from custom_components.govee.request_budget import cloud_poll_divisor, poll_exceeds_budget

LONG_IDLE = IDLE_DEVICE_AFTER_SECONDS + 60


def _divisor(**overrides: Any) -> int:
    kwargs: dict[str, Any] = {
        "is_off": True,
        "seconds_since_change": LONG_IDLE,
        "seconds_since_command": None,
        "idle_after": IDLE_DEVICE_AFTER_SECONDS,
        "recent_command_window": RECENT_COMMAND_WINDOW_SECONDS,
        "idle_divisor": IDLE_DEVICE_POLL_DIVISOR,
    }
    kwargs.update(overrides)
    return cloud_poll_divisor(**kwargs)


def test_long_idle_off_device_is_polled_less_often() -> None:
    assert _divisor() == IDLE_DEVICE_POLL_DIVISOR
    assert IDLE_DEVICE_POLL_DIVISOR > 1


def test_a_device_that_is_on_keeps_the_normal_cadence() -> None:
    assert _divisor(is_off=False) == 1


def test_recently_changed_device_keeps_the_normal_cadence() -> None:
    assert _divisor(seconds_since_change=30.0) == 1


def test_device_with_no_observed_change_yet_keeps_the_normal_cadence() -> None:
    """Nothing seen since startup is not evidence of idleness."""
    assert _divisor(seconds_since_change=None) == 1


def test_a_recent_command_beats_idleness() -> None:
    """The poll right after a write is the one that confirms it landed."""
    assert _divisor(seconds_since_command=5.0) == 1
    assert _divisor(seconds_since_command=RECENT_COMMAND_WINDOW_SECONDS + 1) == IDLE_DEVICE_POLL_DIVISOR


class _CadenceStub(SimpleNamespace):
    """Only what _idle_devices_to_skip reads."""

    def device_last_command_sent(self, device_id: str) -> Any:
        return self._commands.get(device_id)


def _coordinator(*, off: bool, idle_seconds: float | None, commanded_seconds: float | None = None) -> Any:
    now = dt_util.utcnow()
    state = GoveeDeviceState.create_empty("dev")
    state.power_state = not off
    return _CadenceStub(
        _states={"dev": state},
        _poll_cycle_counts={},
        _state_changed_at=({} if idle_seconds is None else {"dev": now - timedelta(seconds=idle_seconds)}),
        _commands=({} if commanded_seconds is None else {"dev": now - timedelta(seconds=commanded_seconds)}),
    )


def _run_cycles(coordinator: Any, cycles: int) -> int:
    """How many of ``cycles`` polls the device actually cost."""
    polled = 0
    for _ in range(cycles):
        if "dev" not in GoveeCoordinator._idle_devices_to_skip(coordinator, {"dev": MagicMock()}):
            polled += 1
    return polled


def test_idle_device_costs_a_quarter_of_the_requests() -> None:
    coordinator = _coordinator(off=True, idle_seconds=LONG_IDLE)
    assert _run_cycles(coordinator, 12) == 12 // IDLE_DEVICE_POLL_DIVISOR


def test_active_device_is_polled_every_cycle() -> None:
    coordinator = _coordinator(off=False, idle_seconds=LONG_IDLE)
    assert _run_cycles(coordinator, 12) == 12


def test_a_command_puts_an_idle_device_back_on_every_cycle() -> None:
    coordinator = _coordinator(off=True, idle_seconds=LONG_IDLE, commanded_seconds=1.0)
    assert _run_cycles(coordinator, 12) == 12


def test_a_device_with_no_state_yet_is_never_held_back() -> None:
    """Idleness never delays the first read: with nothing held yet there is nothing to serve."""
    coordinator = _coordinator(off=True, idle_seconds=LONG_IDLE)
    coordinator._states = {}
    assert GoveeCoordinator._idle_devices_to_skip(coordinator, {"dev": MagicMock()}) == set()


def test_idle_devices_are_spread_across_the_cycle_not_polled_in_lock_step() -> None:
    """Counters that all started at zero would poll every idle device on the same one cycle in four."""
    ids = [f"AA:BB:CC:DD:EE:FF:00:{n:02X}" for n in range(24)]
    now = dt_util.utcnow()
    states = {}
    for device_id in ids:
        state = GoveeDeviceState.create_empty(device_id)
        state.power_state = False
        states[device_id] = state
    coordinator = _CadenceStub(
        _states=states,
        _poll_cycle_counts={},
        _state_changed_at={device_id: now - timedelta(seconds=LONG_IDLE) for device_id in ids},
        _commands={},
    )
    pollable = {device_id: MagicMock() for device_id in ids}

    polled_per_cycle = []
    for _ in range(IDLE_DEVICE_POLL_DIVISOR * 3):
        skipped = GoveeCoordinator._idle_devices_to_skip(coordinator, pollable)
        polled_per_cycle.append(len(ids) - len(skipped))

    # Every device still costs one cycle in N, but not all on the same cycle.
    assert sum(polled_per_cycle) == len(ids) * 3
    assert max(polled_per_cycle) < len(ids)
    assert min(polled_per_cycle) > 0


def test_state_change_stamp_ignores_a_repeat_reading() -> None:
    """Only a real change resets idleness; an identical poll must not."""
    stub = SimpleNamespace(_states={}, _state_changed_at={})
    first = GoveeDeviceState.create_empty("dev")
    first.power_state = False
    stub._states["dev"] = first

    same = GoveeDeviceState.create_empty("dev")
    same.power_state = False
    GoveeCoordinator._note_state_change(stub, "dev", same)
    assert "dev" not in stub._state_changed_at

    changed = GoveeDeviceState.create_empty("dev")
    changed.power_state = True
    GoveeCoordinator._note_state_change(stub, "dev", changed)
    assert "dev" in stub._state_changed_at


class TestPollExceedsBudget:
    """The gate that keeps the idle cadence off installs that can afford full cadence."""

    def test_nineteen_devices_at_60s_overspend_a_9000_budget(self) -> None:
        assert poll_exceeds_budget(requests_per_cycle=19, base_interval=60, daily_budget=9000) is True

    def test_a_handful_of_devices_fit(self) -> None:
        assert poll_exceeds_budget(requests_per_cycle=3, base_interval=60, daily_budget=9000) is False

    def test_the_boundary_is_not_an_overspend(self) -> None:
        # 6 devices at 60 s = 8,640 a day.
        assert poll_exceeds_budget(requests_per_cycle=6, base_interval=60, daily_budget=8640) is False
        assert poll_exceeds_budget(requests_per_cycle=6, base_interval=60, daily_budget=8639) is True

    def test_a_longer_configured_interval_can_bring_a_large_install_under(self) -> None:
        assert poll_exceeds_budget(requests_per_cycle=19, base_interval=300, daily_budget=9000) is False

    def test_nothing_to_poll_never_exceeds(self) -> None:
        assert poll_exceeds_budget(requests_per_cycle=0, base_interval=60, daily_budget=9000) is False
        assert poll_exceeds_budget(requests_per_cycle=5, base_interval=0, daily_budget=9000) is False
