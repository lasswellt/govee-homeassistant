"""Reset and reboot tracking for a power-monitoring outlet's energy counter.

The H5086 counts the energy used today, like "today's usage" in the Govee
app: the counter resets at local midnight (issue #200). It also resets when
the plug reboots, and is then restored from a saved copy about a minute after
the plug is back online. That copy can be somewhat behind. With
``TOTAL_INCREASING``, Home Assistant took the drop after a reboot as a reset
and counted the restored value a second time.

So the energy sensor uses ``TOTAL`` with a ``last_reset``, which Home
Assistant only starts a new cycle for when it changes, and books other changes
as plain differences. :class:`EnergyCounter` decides when that is:

- Reset: energy drops by more than 1 Wh, or on-time by more than 5 minutes,
  while the plug didn't reboot. ``last_reset`` moves to that reading.
- Reboot: the plug's uptime grew less than the time that passed. Readings
  aren't tracked until the counter is restored, which shows in on-time being
  well above the uptime or growing faster than it. If no restore comes within
  10 minutes, e.g. because the plug was without power over midnight, the
  reboot becomes the reset.

No I/O; the energy sensor owns one and persists it across restarts, since a
``last_reset`` that moved on a restart would count the day's energy again.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

# Drops of up to 1 Wh or 5 minutes of on-time are noise, not resets. A reset
# sets both back to zero, and on-time catches resets after days with little
# energy use, as long as the outlet was on.
ENERGY_NOISE_DECIWATT_HOURS = 10
ON_TIME_NOISE_SECONDS = 300

# Plugs restore their counters within about a minute of coming online.
RESTORE_TIMEOUT = timedelta(minutes=10)


def _stc_numbers(sta: Any) -> list[str]:
    """The numbers in ``sta.stc``, which reads like ``19_0_77_40_1``.

    Their meaning is inferred from captures; Govee doesn't document it.
    """
    stc = sta.get("stc") if isinstance(sta, dict) else None
    return stc.split("_") if isinstance(stc, str) else []


def uptime_from_sta(sta: Any) -> int | None:
    """The plug's uptime in seconds, from the ``sta`` field of its status.

    The fourth number in ``sta.stc`` restarts on a reboot and runs about 6%
    slow, in steps of 10 seconds.
    """
    try:
        return int(_stc_numbers(sta)[3])
    except (IndexError, ValueError):
        return None


def answers_other_client(sta: Any) -> bool:
    """Whether a status push answers another client's status query.

    The first number in ``sta.stc`` is 19 in replies to our status queries,
    but 0 in replies to the Govee app's, and 0 or 1 right after a reboot.
    Once a day, a reply to the app carries a small energy counter that
    isn't today's, which would look like a reset.
    """
    numbers = _stc_numbers(sta)
    return bool(numbers) and numbers[0] != "19"


@dataclass
class EnergyCounter:
    """The tracked state of one outlet's energy counter."""

    last_reset: datetime
    # When the last reading was tracked
    updated: datetime
    energy_deciwatt_hours: int
    on_time_seconds: int
    uptime_seconds: int
    # When we first saw the counter drop after a reboot, while we're waiting
    # for the plug to restore it. The readings until then aren't tracked, so
    # nothing is reported with the wrong last_reset.
    reboot_seen: datetime | None = None

    @classmethod
    def new(
        cls, now: datetime, energy_deciwatt_hours: int, on_time_seconds: int, uptime_seconds: int
    ) -> EnergyCounter:
        return cls(now, now, energy_deciwatt_hours, on_time_seconds, uptime_seconds)

    def rebooted(self, now: datetime, uptime_seconds: int) -> bool:
        """Whether the plug rebooted since the last reading.

        If the uptime grew clearly less than the time that passed, the plug
        must have rebooted in between, even if it has been up for longer than
        it was at the last reading.
        """
        elapsed = max(int((now - self.updated).total_seconds()), 0)
        return uptime_seconds + 30 < self.uptime_seconds + elapsed * 9 // 10

    def restored(self, on_time_seconds: int, uptime_seconds: int) -> bool:
        """Whether the plug has restored its counters after a reboot.

        On-time only counts while the plug is up, so without a restore it
        can't be well above the uptime, nor grow faster than it. Both come
        from the plug's clock, so this holds even if readings reach us delayed.
        """
        uptime_growth = max(uptime_seconds - self.uptime_seconds, 0)
        return (
            on_time_seconds > uptime_seconds * 3 // 2 + 300
            or on_time_seconds > self.on_time_seconds + uptime_growth * 6 // 5 + 30
        )

    def update(self, now: datetime, energy_deciwatt_hours: int, on_time_seconds: int, uptime_seconds: int) -> None:
        """Track a new reading; the same reading again is ignored."""
        if (on_time_seconds, uptime_seconds) == (self.on_time_seconds, self.uptime_seconds):
            return
        dropped = (
            energy_deciwatt_hours + ENERGY_NOISE_DECIWATT_HOURS < self.energy_deciwatt_hours
            or on_time_seconds + ON_TIME_NOISE_SECONDS < self.on_time_seconds
        )
        if self.reboot_seen is not None:
            if self.restored(on_time_seconds, uptime_seconds):
                self.reboot_seen = None
            elif now - self.reboot_seen >= RESTORE_TIMEOUT:
                # No restore came, so the reboot was a real reset
                self.last_reset = self.reboot_seen
                self.reboot_seen = None
        elif dropped:
            if not self.rebooted(now, uptime_seconds):
                self.last_reset = now
            elif not self.restored(on_time_seconds, uptime_seconds):
                self.reboot_seen = now
        # While waiting for a restore, keep the energy from before the reboot
        if self.reboot_seen is None:
            self.energy_deciwatt_hours = energy_deciwatt_hours
        self.updated = now
        self.on_time_seconds = on_time_seconds
        self.uptime_seconds = uptime_seconds

    def as_dict(self) -> dict[str, Any]:
        """JSON-serializable form, for Home Assistant's restore state."""
        return {
            "last_reset": self.last_reset.isoformat(),
            "updated": self.updated.isoformat(),
            "energy_deciwatt_hours": self.energy_deciwatt_hours,
            "on_time_seconds": self.on_time_seconds,
            "uptime_seconds": self.uptime_seconds,
            "reboot_seen": self.reboot_seen.isoformat() if self.reboot_seen else None,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> EnergyCounter | None:
        """Inverse of :meth:`as_dict`; None if the data doesn't fit."""
        try:
            reboot_seen = data.get("reboot_seen")
            return cls(
                last_reset=datetime.fromisoformat(data["last_reset"]),
                updated=datetime.fromisoformat(data["updated"]),
                energy_deciwatt_hours=int(data["energy_deciwatt_hours"]),
                on_time_seconds=int(data["on_time_seconds"]),
                uptime_seconds=int(data["uptime_seconds"]),
                reboot_seen=datetime.fromisoformat(reboot_seen) if reboot_seen else None,
            )
        except (KeyError, TypeError, ValueError):
            return None
