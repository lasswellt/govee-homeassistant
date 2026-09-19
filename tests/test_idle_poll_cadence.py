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
from custom_components.govee.request_budget import cloud_poll_divisor

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


def test_first_cycle_of_an_idle_device_still_polls() -> None:
    """The cadence starts by polling, so idleness never delays the first read."""
    coordinator = _coordinator(off=True, idle_seconds=LONG_IDLE)
    assert GoveeCoordinator._idle_devices_to_skip(coordinator, {"dev": MagicMock()}) == set()


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
