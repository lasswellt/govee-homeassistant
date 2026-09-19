"""Tests for budget-paced poll intervals (request_budget + coordinator wiring).

The regression these guard is the one that motivated the module: a ~19-device
install at the 60 s default spends ~27,600 developer-API requests a day
against a 10,000/day cap.
"""

from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

from custom_components.govee.const import (
    DEFAULT_DAILY_REQUEST_BUDGET,
    GOVEE_DAILY_REQUEST_LIMIT,
    MAX_BUDGET_PACED_INTERVAL,
)
from custom_components.govee.coordinator import GoveeCoordinator
from custom_components.govee.request_budget import budget_paced_interval

DAY = 86400


def _simulate_day(interval: int, devices: int) -> int:
    """Requests a whole day of polling costs at a fixed interval."""
    return (DAY // interval) * devices


def test_default_interval_with_19_devices_blows_the_cap() -> None:
    """The premise: 60 s x 19 devices is nearly three times the daily cap."""
    assert _simulate_day(60, 19) > GOVEE_DAILY_REQUEST_LIMIT
    assert _simulate_day(60, 19) == 27360


def test_paced_interval_keeps_a_19_device_day_inside_the_budget() -> None:
    """Pacing from the start of a UTC day lands the day's spend on budget."""
    interval = budget_paced_interval(
        base_interval=60,
        requests_today=0,
        requests_per_cycle=19,
        seconds_remaining_today=DAY,
        daily_budget=DEFAULT_DAILY_REQUEST_BUDGET,
        max_interval=MAX_BUDGET_PACED_INTERVAL,
    )
    # 9000 / 19 = 473 affordable cycles; 86400 / 473 = 182.7 -> 183 s.
    assert interval == 183
    spend = _simulate_day(interval, 19)
    assert spend <= DEFAULT_DAILY_REQUEST_BUDGET
    assert spend < GOVEE_DAILY_REQUEST_LIMIT


def test_small_install_is_never_slowed_below_the_configured_interval() -> None:
    """Three devices can afford 60 s all day, so the user's setting stands."""
    assert (
        budget_paced_interval(
            base_interval=60,
            requests_today=0,
            requests_per_cycle=3,
            seconds_remaining_today=DAY,
            daily_budget=DEFAULT_DAILY_REQUEST_BUDGET,
            max_interval=MAX_BUDGET_PACED_INTERVAL,
        )
        == 60
    )


def test_pacing_tightens_as_the_day_is_spent() -> None:
    """Half the budget gone with half the day left keeps the same spacing;
    three quarters gone at half past noon stretches it."""
    half = budget_paced_interval(
        base_interval=60,
        requests_today=4500,
        requests_per_cycle=19,
        seconds_remaining_today=DAY // 2,
        daily_budget=DEFAULT_DAILY_REQUEST_BUDGET,
        max_interval=MAX_BUDGET_PACED_INTERVAL,
    )
    overspent = budget_paced_interval(
        base_interval=60,
        requests_today=6750,
        requests_per_cycle=19,
        seconds_remaining_today=DAY // 2,
        daily_budget=DEFAULT_DAILY_REQUEST_BUDGET,
        max_interval=MAX_BUDGET_PACED_INTERVAL,
    )
    assert half == 183
    assert overspent > half


def test_exhausted_budget_backs_off_to_the_ceiling_not_to_a_stop() -> None:
    """Over budget backs all the way off, but the poll still runs."""
    assert (
        budget_paced_interval(
            base_interval=60,
            requests_today=DEFAULT_DAILY_REQUEST_BUDGET + 1,
            requests_per_cycle=19,
            seconds_remaining_today=3600,
            daily_budget=DEFAULT_DAILY_REQUEST_BUDGET,
            max_interval=MAX_BUDGET_PACED_INTERVAL,
        )
        == MAX_BUDGET_PACED_INTERVAL
    )


def test_zero_devices_leaves_the_interval_alone() -> None:
    assert (
        budget_paced_interval(
            base_interval=60,
            requests_today=0,
            requests_per_cycle=0,
            seconds_remaining_today=DAY,
            daily_budget=DEFAULT_DAILY_REQUEST_BUDGET,
            max_interval=MAX_BUDGET_PACED_INTERVAL,
        )
        == 60
    )


class _PacingStub(SimpleNamespace):
    """Carries only the attributes _apply_budget_pacing reads and writes.

    Built by hand rather than by instantiating the coordinator: the real
    constructor needs a config entry, an API client and a running hass, none
    of which this arithmetic touches.
    """


def _pacing_coordinator(requests_today: int) -> Any:
    return _PacingStub(
        _rate_limited=False,
        _original_update_interval=timedelta(seconds=60),
        _daily_request_budget=DEFAULT_DAILY_REQUEST_BUDGET,
        _api_client=SimpleNamespace(requests_today=requests_today),
        update_interval=timedelta(seconds=60),
    )


def test_coordinator_stretches_its_own_interval_for_a_large_install() -> None:
    """The coordinator applies the pacing rather than merely computing it.

    The clock is pinned to UTC midnight so a whole day's budget is in play;
    otherwise the answer depends on when the suite happens to run.
    """
    coordinator = _pacing_coordinator(requests_today=0)
    with patch("custom_components.govee.coordinator.time.time", return_value=0.0):
        GoveeCoordinator._apply_budget_pacing(coordinator, 19)
    assert coordinator.update_interval == timedelta(seconds=183)


def test_coordinator_leaves_a_small_install_at_its_configured_interval() -> None:
    """Three devices fit inside the budget at 60 s, so nothing changes."""
    coordinator = _pacing_coordinator(requests_today=0)
    with patch("custom_components.govee.coordinator.time.time", return_value=0.0):
        GoveeCoordinator._apply_budget_pacing(coordinator, 3)
    assert coordinator.update_interval == timedelta(seconds=60)


def test_coordinator_pacing_defers_to_an_active_rate_limit_backoff() -> None:
    """A 429 back-off owns update_interval; pacing must not overwrite it."""
    coordinator = _pacing_coordinator(requests_today=0)
    coordinator._rate_limited = True
    coordinator.update_interval = timedelta(seconds=120)
    with patch("custom_components.govee.coordinator.time.time", return_value=0.0):
        GoveeCoordinator._apply_budget_pacing(coordinator, 19)
    assert coordinator.update_interval == timedelta(seconds=120)
