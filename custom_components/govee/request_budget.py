"""Pure request-budget policy for the Govee cloud poll.

Govee's developer API allows 10,000 requests/day. The state poll asks once
per device per tick, so spend scales with device count x poll frequency and
the cap is easy to blow through without noticing:

    requests/day = 86400 / interval * devices  (+ ~288 for rediscovery)

At the 60 s default, 19 devices costs 86400/60 * 19 = 27,360 state reads,
plus 288 rediscovery calls -> 27,648/day against a 10,000 cap. Put the other
way, the cap allows 10000/1440 ~= 6.9 devices at 60 s, or 19 devices at
~170 s.

Everything here is a pure function of numbers and timestamps: no Home
Assistant, no coordinator, no clock of its own. The coordinator supplies the
readings and applies the answers, which keeps the arithmetic testable on its
own.
"""

from __future__ import annotations

import math

__all__ = ["budget_paced_interval", "local_reading_is_fresh"]


def budget_paced_interval(
    *,
    base_interval: int,
    requests_today: int,
    requests_per_cycle: int,
    seconds_remaining_today: int,
    daily_budget: int,
    max_interval: int,
) -> int:
    """Poll interval (seconds) that spends the rest of today's budget evenly.

    The pacing arithmetic, in full:

        remaining         = daily_budget - requests_today
        affordable_cycles = remaining / requests_per_cycle
        required_interval = seconds_remaining_today / affordable_cycles

    Worked through for the case this exists for — 19 devices, a 9,000/day
    budget, at the start of a UTC day::

        remaining         = 9000 - 0            = 9000
        affordable_cycles = 9000 / 19           = 473 cycles
        required_interval = 86400 / 473         = 182.7 -> 183 s

    which is 9,000 requests/day rather than 27,648, and 183 s is well inside
    the "does not feel stale" range for devices that also have a local
    transport pushing changes in between.

    The result never goes *below* ``base_interval``: this only ever slows the
    poll down. A small install stays at whatever the user configured, because
    its arithmetic asks for less than the base interval and the floor wins.

    Args:
        base_interval: The configured poll interval; the floor of the result.
        requests_today: Requests already spent since UTC midnight.
        requests_per_cycle: Cloud requests one poll cycle costs (devices that
            will actually be asked).
        seconds_remaining_today: Seconds left before the daily counter resets.
        daily_budget: Requests this install is willing to spend per day.
        max_interval: Hard ceiling, so pacing can never stall the poll
            outright.

    Returns:
        The interval to use, clamped to ``[base_interval, max_interval]``.
    """
    if requests_per_cycle <= 0 or base_interval <= 0:
        return base_interval

    remaining = daily_budget - requests_today
    if remaining <= 0:
        # Budget already spent. Back all the way off rather than stopping:
        # the counter resets at UTC midnight, and a poll that never runs
        # again would leave state frozen if the reading were ever wrong.
        return max_interval

    affordable_cycles = remaining / requests_per_cycle
    if affordable_cycles < 1:
        return max_interval

    required = math.ceil(max(seconds_remaining_today, 0) / affordable_cycles)
    return max(base_interval, min(required, max_interval))


def local_reading_is_fresh(
    *,
    seconds_since_local_reading: float | None,
    freshness_window: float,
    consecutive_skips: int,
    max_consecutive_skips: int,
) -> bool:
    """Whether a local reading is recent enough to stand in for a cloud read.

    LAN, MQTT and BLE all deliver the same power/brightness/colour fields the
    /device/state poll returns, and they cost nothing against Govee's quota.
    Until now they were applied as an overlay *after* the cloud call had
    already been spent, so a device with a healthy local transport paid for a
    cloud read it did not need — on a 19-device install that is the bulk of
    the ~27,600 requests/day.

    Two guards keep this from turning into silent staleness:

    * the reading has to be newer than one poll interval, so a transport that
      has gone quiet stops qualifying immediately; and
    * a device is never skipped more than ``max_consecutive_skips`` times in
      a row, so even a local transport that keeps reporting confidently
      wrong values is reconciled against the cloud regularly.

    Args:
        seconds_since_local_reading: Age of the newest LAN/MQTT/BLE reading,
            or None when no local transport has ever delivered one.
        freshness_window: How old a local reading may be and still count,
            normally the current poll interval.
        consecutive_skips: Cloud reads already skipped in a row for this
            device.
        max_consecutive_skips: Cap on that run.

    Returns:
        True when the cloud read can be skipped this cycle.
    """
    if seconds_since_local_reading is None:
        return False
    if consecutive_skips >= max_consecutive_skips:
        return False
    return 0 <= seconds_since_local_reading < freshness_window
