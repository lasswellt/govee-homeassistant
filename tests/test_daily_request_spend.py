"""A simulated day of polling must not outspend the daily request budget.

This is the behavioural guard for the whole budget effort, and it is written
so it runs unchanged against the code *before* that effort as well: it imports
no new symbol that the old code lacks, it drives the real
``_async_update_data`` loop, and it counts calls through
``_fetch_device_state_bounded`` — the one seam every cloud read passes
through, in both versions.

The old behaviour it pins down: one request per device per cycle, every
cycle, regardless of what the day has already cost. At the 60 s default with
19 devices that is 86400/60 * 19 = 27,360 requests against Govee's documented
10,000/day cap. The assertion below fails on that number and passes only when
the poll paces itself.
"""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import MagicMock, patch

import pytest

from custom_components.govee.coordinator import GoveeCoordinator
from custom_components.govee.models import GoveeCapability, GoveeDevice, GoveeDeviceState
from custom_components.govee.models.device import (
    CAPABILITY_ON_OFF,
    INSTANCE_POWER,
)

# Imported defensively so this module still collects against the pre-budget
# code, where the constant does not exist. The point of the test is the
# request count, not the presence of a symbol.
try:  # pragma: no cover - exercised by whichever branch is being run
    from custom_components.govee.const import DEFAULT_DAILY_REQUEST_BUDGET as DAILY_BUDGET
except ImportError:  # pragma: no cover
    DAILY_BUDGET = 9000

DAY_SECONDS = 86400
DEVICE_COUNT = 19
BASE_POLL_INTERVAL = 60
# Enough cycles for a whole day at any interval the pacing can pick; a plain
# guard against looping forever if update_interval ever came back as zero.
MAX_CYCLES = 20000


def _devices(count: int) -> dict[str, GoveeDevice]:
    capabilities = (GoveeCapability(type=CAPABILITY_ON_OFF, instance=INSTANCE_POWER, parameters={}),)
    devices: dict[str, GoveeDevice] = {}
    for index in range(count):
        device_id = f"AA:BB:CC:DD:EE:FF:00:{index:02X}"
        devices[device_id] = GoveeDevice(
            device_id=device_id,
            sku="H6072",
            name=f"Light {index}",
            device_type="devices.types.light",
            capabilities=capabilities,
            is_group=False,
        )
    return devices


def _build_coordinator() -> tuple[GoveeCoordinator, MagicMock]:
    """A coordinator wired to a fake cloud client, with the poll's neighbours
    stubbed out so only the cloud-read path runs."""
    hass = MagicMock()
    config_entry = MagicMock()
    config_entry.entry_id = "test_entry"
    config_entry.options = {}

    api_client = MagicMock()
    # Headers absent: nothing to back off on, so the cloud read is only ever
    # shaped by the poll's own pacing.
    api_client.rate_limit_remaining = None
    api_client.rate_limit_reset_in = None
    api_client.requests_today = 0

    coordinator = GoveeCoordinator(
        hass=hass,
        config_entry=config_entry,
        api_client=api_client,
        iot_credentials=None,
        poll_interval=BASE_POLL_INTERVAL,
    )
    coordinator._devices = _devices(DEVICE_COUNT)

    async def _noop_async(*args: object, **kwargs: object) -> None:
        return None

    # Everything around the cloud fan-out: discovery, BLE enrolment, the
    # registry sweep, the transport-health and LAN refreshes. None of them
    # issue developer-API requests, and all of them need a real hass.
    coordinator._async_maybe_rediscover_devices = _noop_async  # type: ignore[method-assign]
    coordinator._async_maybe_rescan_lan = _noop_async  # type: ignore[method-assign]
    coordinator._refresh_lan_reads = _noop_async  # type: ignore[method-assign]
    coordinator._ble_handler = MagicMock()
    coordinator._devices_with_all_entities_disabled = lambda: set()  # type: ignore[method-assign]
    coordinator._refresh_mqtt_health = lambda: None  # type: ignore[method-assign]
    coordinator._refresh_ble_staleness = lambda: None  # type: ignore[method-assign]
    coordinator._refresh_lan_staleness = lambda: None  # type: ignore[method-assign]

    return coordinator, api_client


async def _simulate_one_day(coordinator: GoveeCoordinator, api_client: MagicMock) -> tuple[int, int]:
    """Run poll cycles until a virtual UTC day has elapsed.

    Returns (requests issued, cycles run). The clock starts at UTC midnight
    and advances by whatever interval the coordinator asks for next, so a
    coordinator that stretches its own cadence runs fewer cycles — which is
    the entire mechanism under test.
    """
    clock = {"now": 0.0}
    issued = 0

    async def _fake_fetch(device_id: str, device: GoveeDevice) -> GoveeDeviceState:
        nonlocal issued
        issued += 1
        api_client.requests_today += 1
        state = GoveeDeviceState.create_empty(device_id)
        state.online = True
        state.power_state = True
        return state

    coordinator._fetch_device_state_bounded = _fake_fetch  # type: ignore[method-assign]

    cycles = 0
    with patch("custom_components.govee.coordinator.time.time", side_effect=lambda: clock["now"]):
        while clock["now"] < DAY_SECONDS and cycles < MAX_CYCLES:
            await coordinator._async_update_data()
            cycles += 1
            interval = (coordinator.update_interval or timedelta(seconds=BASE_POLL_INTERVAL)).total_seconds()
            clock["now"] += max(interval, 1.0)

    return issued, cycles


@pytest.mark.asyncio
async def test_a_day_of_polling_19_devices_stays_within_the_daily_budget() -> None:
    """19 devices polled for a whole day must cost at most the daily budget.

    Old behaviour: the interval never moves off 60 s, so the day costs
    86400/60 * 19 = 27,360 requests. New behaviour: the poll paces itself
    against what the day has already spent and lands on the budget.
    """
    coordinator, api_client = _build_coordinator()
    issued, cycles = await _simulate_one_day(coordinator, api_client)

    assert cycles > 0, "the simulation never ran a poll cycle"
    assert issued > 0, "no cloud reads were issued, so nothing was measured"
    assert issued <= DAILY_BUDGET, (
        f"a day of polling {DEVICE_COUNT} devices issued {issued} requests "
        f"over {cycles} cycles, above the {DAILY_BUDGET}/day budget"
    )
