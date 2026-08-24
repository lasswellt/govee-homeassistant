"""The account poll must not overwrite a fresher gateway frame (issue #151).

The gateway's MQTT frame reaches Home Assistant before the cloud's copy of the
same reading does — measured at roughly 8 minutes on an H5310 behind an H5044.
Inside that window the 5-minute account poll still returns the PREVIOUS hour's
value, so applying it replaced a current reading with a stale one for about
five minutes of every hour.

@Araknus13 measured it directly on v2026.8.5 (all times UTC)::

    frame  08:56:35   19.9 °C   MQTT, current
    poll   09:00:42   19.1 °C   account API, still the 07:56 reading
    poll   09:05:43   19.8 °C   account API, caught up

The step is one hour of pool temperature change (-0.8 K that morning), and it
showed up as write volume too: 37 state changes in ten hours against 20-24 per
full day before. Harmless for their use, but it reads as a sensor fault on a
graph, and a faster-moving device would step further.

Both sides timestamp the reading device-side — the frame in bytes 9-12, the
BFF as ``lastTime`` — so they are directly comparable.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from custom_components.govee.coordinator import GoveeCoordinator
from custom_components.govee.models import GoveeDeviceState

DEV = "03:55:01:25:00:00:00:0B:FF:FF:00:41:FF:FF:00:33"

# 2026-08-23 08:56:35 UTC, the frame in the report.
FRAME_TS = 1787907395
# The reading the frame carried.
FRAME_TEMP = 19.9
# The stale cloud copy: produced at 07:56, an hour earlier.
STALE_BFF_MS = (FRAME_TS - 3600) * 1000
STALE_BFF_TEMP = 19.1
# The cloud catching up, produced after the frame.
FRESH_BFF_MS = (FRAME_TS + 480) * 1000
FRESH_BFF_TEMP = 19.8


def _coordinator(*, frame_ts=FRAME_TS, current=FRAME_TEMP):
    coordinator = GoveeCoordinator.__new__(GoveeCoordinator)
    coordinator._config_entry = SimpleNamespace(options={}, data={}, entry_id="e1")
    coordinator.hass = MagicMock()
    state = GoveeDeviceState.create_empty(DEV)
    state.sensor_temperature = current
    coordinator._states = {DEV: state}
    coordinator._thermo_frame_ts = {DEV: frame_ts} if frame_ts else {}
    return coordinator


def _reading(last_time_ms, temperature):
    return {
        "device_id": DEV,
        "name": "Pool",
        "temperature": temperature,
        "last_time": last_time_ms,
    }


class TestStalenessPredicate:
    def test_previous_hours_reading_is_stale(self):
        """The exact case measured: cloud copy older than the applied frame."""
        coordinator = _coordinator()
        sensor = _reading(STALE_BFF_MS, STALE_BFF_TEMP)
        assert coordinator._bff_reading_is_stale(DEV, sensor) is True

    def test_caught_up_reading_is_not_stale(self):
        """Once the cloud catches up it is newer, and must be applied."""
        coordinator = _coordinator()
        sensor = _reading(FRESH_BFF_MS, FRESH_BFF_TEMP)
        assert coordinator._bff_reading_is_stale(DEV, sensor) is False

    def test_same_instant_counts_as_stale(self):
        """The cloud copy OF the frame we already applied is not new data."""
        coordinator = _coordinator()
        sensor = _reading(FRAME_TS * 1000, FRAME_TEMP)
        assert coordinator._bff_reading_is_stale(DEV, sensor) is True

    def test_no_frame_seen_means_never_stale(self):
        """A device with no gateway frame keeps the old behaviour exactly."""
        coordinator = _coordinator(frame_ts=None)
        sensor = _reading(STALE_BFF_MS, STALE_BFF_TEMP)
        assert coordinator._bff_reading_is_stale(DEV, sensor) is False

    @pytest.mark.parametrize("last_time", [None, 0, -1, "", "1787907395000"])
    def test_unusable_timestamp_means_never_stale(self, last_time):
        """Can't prove it's stale -> apply it, as before.

        Accounts whose BFF omits lastTime, or returns it as a string, must not
        silently stop receiving readings.
        """
        coordinator = _coordinator()
        sensor = _reading(last_time, STALE_BFF_TEMP)
        assert coordinator._bff_reading_is_stale(DEV, sensor) is False

    def test_unknown_device_is_not_stale(self):
        coordinator = _coordinator()
        sensor = _reading(STALE_BFF_MS, STALE_BFF_TEMP)
        assert coordinator._bff_reading_is_stale("other", sensor) is False
