"""A pushed update must not postpone the cloud poll (issue #214).

Home Assistant's ``DataUpdateCoordinator.async_set_updated_data`` does more
than publish data: it cancels the pending poll and re-arms it a full
``update_interval`` later. Every MQTT push, gateway thermometer frame, LAN read
and OpenAPI event in this integration published through it, so each one moved
the next cloud read back.

At the old fixed 60 s that cost little. Budget pacing and the rate-limit
back-offs now stretch the interval, while the MQTT status sweep alone gets an
answer every 5 minutes from any device whose reading moved (an AQI monitor or
a dehumidifier, in the report). Once the interval is longer than the gap
between pushes, every push re-arms the poll before it can fire, and the pacing
that would have shortened the interval again only runs inside the poll.

The report hit this every day at the 00:00 UTC reset of Govee's request
counter: the first poll after it paces a whole budget over a whole day,
86,400 s x devices / budget, which at the default 9,000 budget is longer than
the 5-minute sweep from 32 pollable devices up (38 devices give about 365 s,
the interval the report's diagnostics show). Devices that depend on the cloud
poll, such as gateway-bridged thermometers, froze from that poll until the
entry was reloaded, while the pushing devices kept updating and nothing was
logged. After a restart or reload the counter starts again with less of the
day left, so the interval came out shorter and polls got through until the
next reset.

The loop below is a stand-in for the event loop that records what the
coordinator schedules, so the real ``DataUpdateCoordinator`` timer logic runs
unmodified.

A push still arms the poll when none is pending, on Home Assistant's own
conditions: a refresh that ends in ``ConfigEntryAuthFailed`` (one device
answering 401 is enough) leaves no poll armed, and the next push is what
brings it back.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.exceptions import ConfigEntryAuthFailed

from custom_components.govee.const import (
    DEFAULT_MQTT_STATUS_INTERVAL,
    DEFAULT_POLL_INTERVAL,
    MAX_BUDGET_PACED_INTERVAL,
)
from custom_components.govee.coordinator import GoveeCoordinator
from custom_components.govee.models import GoveeCapability, GoveeDevice, GoveeDeviceState
from custom_components.govee.models.device import CAPABILITY_PROPERTY, INSTANCE_SENSOR_TEMPERATURE

from .test_pm25_monitoring import DEVICE_ID as MONITOR
from .test_pm25_monitoring import FRAME_UNIT_1, FRAME_UNIT_2

# Two real H5106 frames with different readings, so each sweep answer is a
# change that gets published.
FRAME_A = FRAME_UNIT_1.hex()
FRAME_B = FRAME_UNIT_2.hex()

# The report's diagnostics show the poll frozen at 365 and 366 s, which at the
# default daily budget is 38 devices for the cloud poll to read. Here one of
# them is the monitor whose readings the status sweep keeps pushing.
POLLED_DEVICES = 38
# Any whole number of days after the epoch falls on 00:00 UTC, when Govee's
# daily request counter resets.
RESET = 20_000 * 86_400


class _Timer:
    def __init__(self, when: float) -> None:
        self._when = when
        self.cancelled = False
        self.fired = False

    def when(self) -> float:
        return self._when

    def cancel(self) -> None:
        self.cancelled = True


class _Loop:
    """Only the two loop methods the coordinator's scheduling uses."""

    def __init__(self) -> None:
        self.now = 0.0
        self.timers: list[_Timer] = []

    def time(self) -> float:
        return self.now

    def call_at(self, when: float, callback: Any, *args: Any) -> _Timer:
        timer = _Timer(when)
        self.timers.append(timer)
        return timer

    def live(self) -> list[_Timer]:
        return [timer for timer in self.timers if not timer.cancelled and not timer.fired]

    def pending(self) -> _Timer:
        live = self.live()
        assert len(live) == 1, live
        return live[0]

    def fire(self) -> None:
        """Advance to the pending poll and mark it fired; the test runs the refresh."""
        timer = self.pending()
        self.now = timer.when()
        timer.fired = True


def _coordinator(
    *, listen: bool = True, polling: bool = True, interval: int = MAX_BUDGET_PACED_INTERVAL
) -> tuple[GoveeCoordinator, _Loop, list[int]]:
    loop = _Loop()
    hass = MagicMock()
    hass.loop = loop
    hass.is_stopping = False
    config_entry = MagicMock()
    config_entry.entry_id = "entry"
    config_entry.options = {}
    config_entry.pref_disable_polling = not polling
    coordinator = GoveeCoordinator(
        hass=hass,
        config_entry=config_entry,
        api_client=MagicMock(),
        iot_credentials=None,
        poll_interval=60,
    )
    coordinator._devices[MONITOR] = GoveeDevice(
        device_id=MONITOR,
        sku="H5106",
        name="AQI Monitor",
        device_type="devices.types.sensor",
        capabilities=(GoveeCapability(type=CAPABILITY_PROPERTY, instance=INSTANCE_SENSOR_TEMPERATURE, parameters={}),),
    )
    coordinator._states[MONITOR] = GoveeDeviceState.create_empty(MONITOR)
    # By default budget pacing or a back-off has stretched the poll to its
    # ceiling.
    coordinator.update_interval = timedelta(seconds=interval)
    notified: list[int] = []
    if listen:
        # The first listener arms the poll, as an entity being added does.
        coordinator.async_add_listener(lambda: notified.append(1))
    return coordinator, loop, notified


def test_mqtt_sweep_answers_do_not_postpone_a_stretched_poll() -> None:
    coordinator, loop, notified = _coordinator()
    due = loop.pending().when()

    # Two status sweeps land before the 15-minute poll is due, each carrying a
    # changed reading from the monitor.
    for sweep, frame in enumerate((FRAME_A, FRAME_B), start=1):
        loop.now = sweep * DEFAULT_MQTT_STATUS_INTERVAL
        coordinator._on_mqtt_state_update(MONITOR, {"onOff": 1, "_op_frames": [frame]})

    assert len(notified) == 2, "both pushes are still published"
    # Still due 15 minutes after it was armed, not 15 minutes after the last
    # push: at a sweep every 5 minutes that is the difference between a poll
    # and none at all.
    assert loop.pending().when() == due


def test_publishing_pushed_state_keeps_the_rest_of_its_contract() -> None:
    """Data published, listeners told, and a push still counts as success."""
    coordinator, loop, notified = _coordinator()
    timer = loop.pending()
    coordinator.last_update_success = False
    loop.now = 120

    coordinator.async_set_updated_data(coordinator._states)

    assert timer.cancelled is False
    assert loop.pending() is timer
    assert coordinator.data is coordinator._states
    assert coordinator.last_update_success is True
    assert notified == [1]


async def test_a_push_rearms_the_poll_after_a_refresh_that_left_none() -> None:
    """After an auth failure Home Assistant does not re-arm the poll; a push must."""
    coordinator, loop, _ = _coordinator()
    coordinator._async_update_data = AsyncMock(side_effect=ConfigEntryAuthFailed("Invalid API key"))

    loop.fire()
    await coordinator._handle_refresh_interval()
    assert loop.live() == [], "the failed refresh left no poll armed"

    # The next status sweep answers five minutes later.
    loop.now = float(MAX_BUDGET_PACED_INTERVAL + DEFAULT_MQTT_STATUS_INTERVAL)
    coordinator._on_mqtt_state_update(MONITOR, {"onOff": 1, "_op_frames": [FRAME_A]})

    rearmed = loop.pending()
    assert loop.now + MAX_BUDGET_PACED_INTERVAL < rearmed.when() < loop.now + MAX_BUDGET_PACED_INTERVAL + 1


@pytest.mark.parametrize(("listen", "polling"), [(False, True), (True, False)], ids=["no listener", "polling off"])
def test_a_push_arms_no_poll_where_home_assistant_would_not(listen: bool, polling: bool) -> None:
    coordinator, loop, _ = _coordinator(listen=listen, polling=polling)

    coordinator.async_set_updated_data(coordinator._states)

    assert loop.live() == []


def _paced_install() -> tuple[GoveeCoordinator, _Loop]:
    """The monitor plus cloud-only thermometers, polling at the configured 60 s.

    Each poll runs the real ``_async_update_data``, and with it the real
    budget pacing, with only its neighbours stubbed as in
    test_daily_request_spend.py. Every cloud read counts against the day, and
    no rate-limit headers arrive (none reached the reporter's install either).
    """
    coordinator, loop, _ = _coordinator(interval=DEFAULT_POLL_INTERVAL)
    capabilities = (GoveeCapability(type=CAPABILITY_PROPERTY, instance=INSTANCE_SENSOR_TEMPERATURE, parameters={}),)
    for index in range(POLLED_DEVICES - 1):
        device_id = f"AA:BB:CC:DD:EE:FF:01:{index:02X}"
        coordinator._devices[device_id] = GoveeDevice(
            device_id=device_id,
            sku="H5179",
            name=f"Thermometer {index}",
            device_type="devices.types.thermometer",
            capabilities=capabilities,
        )
    api_client = coordinator._api_client
    api_client.rate_limit_remaining = None
    api_client.rate_limit_reset_in = None
    api_client.requests_today = 0

    async def _cloud_read(device_id: str, device: GoveeDevice) -> GoveeDeviceState:
        api_client.requests_today += 1
        state = coordinator._states.get(device_id)
        if state is None:
            state = GoveeDeviceState.create_empty(device_id)
        state.online = True
        return state

    async def _nothing() -> None:
        return None

    coordinator._fetch_device_state_bounded = _cloud_read  # type: ignore[method-assign]
    coordinator._async_maybe_rediscover_devices = _nothing  # type: ignore[method-assign]
    coordinator._async_maybe_rescan_lan = _nothing  # type: ignore[method-assign]
    coordinator._refresh_lan_reads = _nothing  # type: ignore[method-assign]
    coordinator._ble_handler = MagicMock()
    coordinator._devices_with_all_entities_disabled = lambda: set()  # type: ignore[method-assign]
    coordinator._refresh_mqtt_health = lambda: None  # type: ignore[method-assign]
    coordinator._refresh_ble_staleness = lambda: None  # type: ignore[method-assign]
    coordinator._refresh_lan_staleness = lambda: None  # type: ignore[method-assign]
    return coordinator, loop


async def test_the_first_poll_after_the_daily_reset_still_runs_between_sweeps() -> None:
    """The daily trigger in the report, end to end.

    Govee's request counter resets at 00:00 UTC, and the first poll after it
    paces a whole budget over a whole day: 86,400 s x 38 devices / 9,000 is
    about 365 s, the interval the report's diagnostics show frozen, and longer
    than the 5-minute status sweep. Before this fix each sweep answer re-armed
    that poll a full interval out, so it never ran again, and the pacing that
    would have shortened the interval only runs inside the poll.
    """
    coordinator, loop = _paced_install()
    polls: list[float] = []

    async def _poll() -> None:
        loop.fire()
        await coordinator._handle_refresh_interval()
        polls.append(loop.now)

    with patch("custom_components.govee.coordinator.time.time", side_effect=lambda: RESET + loop.now):
        # The first poll of the day, a minute after the reset, with the whole
        # budget back.
        await _poll()
        assert coordinator.update_interval is not None
        paced = coordinator.update_interval.total_seconds()
        assert DEFAULT_MQTT_STATUS_INTERVAL < paced < 370

        # Two hours of status sweeps, each answered with a changed reading. A
        # poll that falls due before the next answer runs first.
        for sweep in range(1, 25):
            answer_at = sweep * DEFAULT_MQTT_STATUS_INTERVAL
            while loop.pending().when() <= answer_at:
                await _poll()
            loop.now = float(answer_at)
            coordinator._on_mqtt_state_update(MONITOR, {"onOff": 1, "_op_frames": [(FRAME_A, FRAME_B)[sweep % 2]]})

    assert len(polls) > 1, "no cloud poll ran after the first one of the day"
    # And each on its paced time, never pushed back by a sweep.
    gaps = [later - earlier for earlier, later in zip(polls, polls[1:])]
    assert max(gaps) <= paced + 1
