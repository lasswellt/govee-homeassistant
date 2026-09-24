"""Budget pacing must not park the poll past the daily reset.

Found while tracing issue #214, and a separate, smaller problem from the one
that froze the devices in that report (see test_issue_214_push_keeps_poll.py).

When less than one poll cycle of the day's budget is left, the pacing backed
all the way off to ``MAX_BUDGET_PACED_INTERVAL`` (15 minutes). That is the
right answer at noon, but the counter it paces against resets at 00:00 UTC:
a few minutes before midnight the same answer schedules the next poll up to a
quarter of an hour into a day that has its whole budget back.

It is not a corner case. Pacing spreads the budget so it runs out at the end
of the day, and the small spend it does not plan for (rediscovery, commands,
retries) is enough to leave the last cycle of nearly every UTC day short, so
it costs one late poll a day. It is not what froze the devices in #214: there
the first poll after the reset paced a whole day's budget into an interval
longer than the MQTT status sweep, and the sweep's pushes kept re-arming it.
"""

from __future__ import annotations

from homeassistant.helpers.event import RANDOM_MICROSECOND_MIN

from custom_components.govee.const import (
    DEFAULT_DAILY_REQUEST_BUDGET,
    DEFAULT_POLL_INTERVAL,
    MAX_BUDGET_PACED_INTERVAL,
)
from custom_components.govee.request_budget import budget_paced_interval

DAY = 86400
# Rediscovery runs every 5 minutes: the ~288 requests/day the module docstring
# names, and spend the pacing does not plan for.
REDISCOVERY_PER_SECOND = 288 / DAY
# How long after the reset the first poll of the new day may land.
SLACK = 60


def _paced(*, requests_today: int, seconds_remaining_today: int, requests_per_cycle: int = 20) -> int:
    return budget_paced_interval(
        base_interval=DEFAULT_POLL_INTERVAL,
        requests_today=requests_today,
        requests_per_cycle=requests_per_cycle,
        seconds_remaining_today=seconds_remaining_today,
        daily_budget=DEFAULT_DAILY_REQUEST_BUDGET,
        max_interval=MAX_BUDGET_PACED_INTERVAL,
    )


def test_short_of_one_cycle_ten_minutes_before_the_reset_waits_for_the_reset() -> None:
    """23:50 UTC with five requests left and a 20-device cycle to pay for."""
    interval = _paced(requests_today=DEFAULT_DAILY_REQUEST_BUDGET - 5, seconds_remaining_today=600)

    assert 600 <= interval <= 600 + SLACK


def test_spent_budget_ten_minutes_before_the_reset_waits_for_the_reset() -> None:
    interval = _paced(requests_today=DEFAULT_DAILY_REQUEST_BUDGET, seconds_remaining_today=600)

    assert 600 <= interval <= 600 + SLACK


def test_the_poll_that_waits_for_the_reset_fires_after_it_at_worst() -> None:
    """The poll aimed at the reset must not fire on the old day.

    The coordinator passes the time left today rounded down to the whole
    second, and ``DataUpdateCoordinator._schedule_refresh`` arms the timer at
    ``int(loop.time()) + stagger + interval``, the stagger being at least
    ``RANDOM_MICROSECOND_MIN``. Worst case: both dropped fractions just under
    a second and the smallest stagger.
    """
    dropped = 0.999
    stagger = RANDOM_MICROSECOND_MIN / 10**6
    for whole_seconds_left in (61, 600, 890):
        interval = _paced(requests_today=DEFAULT_DAILY_REQUEST_BUDGET, seconds_remaining_today=whole_seconds_left)

        fires_in = interval - dropped + stagger
        assert fires_in > whole_seconds_left + dropped, whole_seconds_left


def test_spent_budget_seconds_before_the_reset_keeps_the_configured_floor() -> None:
    """Never faster than the user's interval, even when the reset is closer."""
    interval = _paced(requests_today=DEFAULT_DAILY_REQUEST_BUDGET, seconds_remaining_today=30)

    assert interval == DEFAULT_POLL_INTERVAL


def test_spent_budget_with_most_of_the_day_left_still_backs_all_the_way_off() -> None:
    """Unchanged: the ceiling still applies when the reset is far away."""
    interval = _paced(requests_today=DEFAULT_DAILY_REQUEST_BUDGET, seconds_remaining_today=12 * 3600)

    assert interval == MAX_BUDGET_PACED_INTERVAL


def _first_poll_of_the_next_day(requests_per_cycle: int) -> float:
    """Seconds after 00:00 UTC that a paced day schedules its next poll.

    Drives the real pacing through a whole UTC day, charging each cycle its
    device count plus rediscovery's share, and returns where the poll that
    crosses midnight lands.
    """
    now = 0
    spent = 0.0
    while True:
        interval = _paced(
            requests_today=int(spent),
            seconds_remaining_today=DAY - now,
            requests_per_cycle=requests_per_cycle,
        )
        if now + interval >= DAY:
            return now + interval - DAY
        now += interval
        spent += requests_per_cycle + REDISCOVERY_PER_SECOND * interval


def test_a_paced_day_resumes_polling_right_after_the_reset() -> None:
    """Every size of install whose full cadence the budget cannot afford."""
    for devices in (10, 20, 40, 60):
        assert 0 < _first_poll_of_the_next_day(devices) <= SLACK, devices
