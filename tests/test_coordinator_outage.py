"""A total cloud outage must surface, a partial one must stay isolated.

``_async_update_data`` raises ``UpdateFailed`` only when every polled device
failed to reach Govee, so entities go unavailable and the coordinator logs the
outage (and the recovery) once. Per-device failures keep the last state.
"""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.helpers.update_coordinator import UpdateFailed

import custom_components.govee.coordinator as coordinator_module
from custom_components.govee.api.exceptions import GoveeApiError, GoveeConnectionError, GoveeRateLimitError
from custom_components.govee.const import DEFAULT_DAILY_REQUEST_BUDGET
from custom_components.govee.coordinator import GoveeCoordinator
from custom_components.govee.models import GoveeDevice, GoveeDeviceState
from custom_components.govee.transport_health import TransportHealthTracker

DEV_A = "AA:BB:CC:DD:EE:FF:00:01"
DEV_B = "AA:BB:CC:DD:EE:FF:00:02"


def _device(device_id: str, capabilities) -> GoveeDevice:
    return GoveeDevice(
        device_id=device_id,
        sku="H6159",
        name=f"Light {device_id[-2:]}",
        device_type="devices.types.light",
        capabilities=capabilities,
    )


@pytest.fixture
def coordinator(light_capabilities):
    coord = object.__new__(GoveeCoordinator)
    coord.hass = MagicMock()
    coord._config_entry = MagicMock()
    coord._devices = {
        DEV_A: _device(DEV_A, light_capabilities),
        DEV_B: _device(DEV_B, light_capabilities),
    }
    coord._states = {
        DEV_A: GoveeDeviceState.create_empty(DEV_A),
        DEV_B: GoveeDeviceState.create_empty(DEV_B),
    }
    coord._transport = TransportHealthTracker()
    for did in coord._devices:
        coord._transport.ensure(did)
    coord._api_client = MagicMock()
    coord._api_client.requests_today = 0
    coord._mqtt_client = None
    coord._ble_devices = {}
    coord._bff_thermometer_ids = set()
    coord._lan_client = None
    coord._lan_devices = {}
    coord.last_update_success = True
    coord._rate_limited = False
    # Budget pacing runs on every poll and reads these three. __init__ sets
    # them; this fixture builds the coordinator with object.__new__, so they
    # have to be supplied by hand like the rest. Two devices against the
    # default budget paces to the base interval, so the outage assertions
    # below are unaffected.
    coord.update_interval = timedelta(seconds=60)
    coord._original_update_interval = timedelta(seconds=60)
    coord._daily_request_budget = DEFAULT_DAILY_REQUEST_BUDGET
    # Local-freshness skipping keeps its own per-device counter.
    coord._local_fresh_skips = {}
    # Idle cadence tracks cycles seen and when a device last really changed.
    coord._poll_cycle_counts = {}
    coord._state_changed_at = {}
    coord._async_maybe_rediscover_devices = AsyncMock()
    coord._ble_handler = MagicMock()
    coord._devices_with_all_entities_disabled = MagicMock(return_value=set())
    coord._async_maybe_rescan_lan = AsyncMock()
    coord._refresh_lan_reads = AsyncMock()
    coord._refresh_lan_staleness = MagicMock()
    coord._refresh_mqtt_health = MagicMock()
    coord._refresh_ble_staleness = MagicMock()
    return coord


@pytest.mark.asyncio
async def test_total_outage_raises_update_failed(coordinator):
    coordinator._api_client.get_device_state = AsyncMock(
        side_effect=GoveeConnectionError("Connection error: DNS failed")
    )

    with pytest.raises(UpdateFailed, match="unreachable for all 2 device"):
        await coordinator._async_update_data()


@pytest.mark.asyncio
async def test_backend_5xx_on_every_device_is_an_outage(coordinator):
    coordinator._api_client.get_device_state = AsyncMock(side_effect=GoveeApiError("Service Unavailable", code=503))

    with pytest.raises(UpdateFailed):
        await coordinator._async_update_data()


@pytest.mark.asyncio
async def test_partial_failure_keeps_previous_state(coordinator):
    fresh = GoveeDeviceState.create_empty(DEV_B)
    fresh.power_state = True

    async def fetch(device_id, sku):
        if device_id == DEV_A:
            raise GoveeConnectionError("Connection error: reset")
        return fresh

    coordinator._api_client.get_device_state = AsyncMock(side_effect=fetch)

    states = await coordinator._async_update_data()

    assert states[DEV_B].power_state is True
    assert states[DEV_A] is coordinator._states[DEV_A]
    assert coordinator._transport.get(DEV_A, "cloud_api").is_available is False


@pytest.mark.asyncio
async def test_rejected_requests_are_not_an_outage(coordinator):
    """A 400 on every device means bad requests, not an unreachable cloud."""
    coordinator._api_client.get_device_state = AsyncMock(side_effect=GoveeApiError("Bad request", code=400))

    states = await coordinator._async_update_data()

    assert set(states) == {DEV_A, DEV_B}


# --- A 429 back-off must hold until a real read succeeds ------------------


@pytest.fixture
def rate_limit_issues(monkeypatch):
    """Capture the repair-issue calls the coordinator makes."""
    created = MagicMock()
    deleted = MagicMock()
    monkeypatch.setattr(coordinator_module, "async_create_rate_limit_issue", created)
    monkeypatch.setattr(coordinator_module, "async_delete_rate_limit_issue", deleted)
    return created, deleted


@pytest.mark.asyncio
@pytest.mark.parametrize("headers", [None, (0, 600)], ids=["no_headers", "remaining_reset_headers"])
async def test_rate_limit_backoff_holds_until_a_real_read(coordinator, rate_limit_issues, headers):
    """A cycle where every device answers 429 keeps the back-off and the issue.

    The 429 branch hands back the held state; that must not count as a
    successful read, or the back-off and the repair issue are undone in the
    same poll that set them.
    """
    created, deleted = rate_limit_issues
    coordinator._api_client.rate_limit_remaining = 100
    coordinator._api_client.rate_limit_reset_in = 0

    async def refuse(device_id, sku):
        # The headers, when Govee sends them, arrive on the 429 itself.
        if headers is not None:
            coordinator._api_client.rate_limit_remaining, coordinator._api_client.rate_limit_reset_in = headers
        raise GoveeRateLimitError("429", retry_after=600)

    coordinator._api_client.get_device_state = AsyncMock(side_effect=refuse)

    await coordinator._async_update_data()

    assert coordinator._rate_limited is True
    assert coordinator.update_interval == timedelta(seconds=600)
    assert created.call_count == 1
    deleted.assert_not_called()

    # Once the back-off window has passed the next cycle polls again (no extra
    # header deferral on top of the 429 wait) and, still limited, keeps waiting.
    calls_before = coordinator._api_client.get_device_state.await_count
    await coordinator._async_update_data()
    assert coordinator._api_client.get_device_state.await_count - calls_before == 2
    assert coordinator._rate_limited is True
    assert coordinator.update_interval == timedelta(seconds=600)
    assert created.call_count == 1
    deleted.assert_not_called()

    # A real read clears it, restores the interval and removes the issue.
    coordinator._api_client.rate_limit_remaining = 100
    coordinator._api_client.rate_limit_reset_in = 0
    coordinator._api_client.get_device_state = AsyncMock(
        side_effect=lambda device_id, sku: GoveeDeviceState.create_empty(device_id)
    )
    await coordinator._async_update_data()
    assert coordinator._rate_limited is False
    assert coordinator.update_interval == timedelta(seconds=60)
    deleted.assert_called_once()


@pytest.mark.asyncio
async def test_rate_limit_on_one_device_is_not_cleared_by_another(coordinator, rate_limit_issues):
    """One device's 429 is not undone by another device's success in the same cycle."""
    created, deleted = rate_limit_issues
    coordinator._api_client.rate_limit_remaining = 100
    coordinator._api_client.rate_limit_reset_in = 0

    async def fetch(device_id, sku):
        if device_id == DEV_A:
            raise GoveeRateLimitError("429", retry_after=300)
        return GoveeDeviceState.create_empty(device_id)

    coordinator._api_client.get_device_state = AsyncMock(side_effect=fetch)
    await coordinator._async_update_data()

    assert coordinator._rate_limited is True
    assert coordinator.update_interval == timedelta(seconds=300)
    created.assert_called_once()
    deleted.assert_not_called()


@pytest.mark.asyncio
async def test_rate_limit_backoff_never_speeds_up_a_paced_poll(coordinator, rate_limit_issues):
    """A 429 without Retry-After must not shorten an interval budget pacing already stretched."""
    coordinator._api_client.rate_limit_remaining = 100
    coordinator._api_client.rate_limit_reset_in = 0
    coordinator.update_interval = timedelta(seconds=600)  # paced well past the 120 s default
    coordinator._api_client.get_device_state = AsyncMock(side_effect=GoveeRateLimitError("429"))

    await coordinator._async_update_data()

    assert coordinator._rate_limited is True
    assert coordinator.update_interval >= timedelta(seconds=600)


# --- Devices that make no cloud request must not hide a total outage --------

GROUP_ID = "11825917"
EXTRA_ID = "AA:BB:CC:DD:EE:FF:00:03"


def _add_no_request_device(coordinator, kind: str, light_capabilities) -> None:
    """Give the account one device whose poll never reaches the cloud."""
    from custom_components.govee.models import GoveeCapability
    from custom_components.govee.models.device import CAPABILITY_EVENT, INSTANCE_BODY_APPEARED_EVENT

    if kind == "group":
        device = GoveeDevice(
            device_id=GROUP_ID,
            sku="SameModeGroup",
            name="Group",
            device_type="devices.types.light",
            capabilities=light_capabilities,
            is_group=True,
        )
    elif kind == "water_detector":
        device = GoveeDevice(
            device_id=EXTRA_ID,
            sku="H5054",
            name="Leak Detector",
            device_type="devices.types.sensor",
            capabilities=(
                GoveeCapability(type=CAPABILITY_EVENT, instance=INSTANCE_BODY_APPEARED_EVENT, parameters={}),
            ),
        )
    else:  # bff_thermometer
        device = _device(EXTRA_ID, light_capabilities)
        coordinator._bff_thermometer_ids = {EXTRA_ID}
    coordinator._devices[device.device_id] = device
    coordinator._states[device.device_id] = GoveeDeviceState.create_empty(device.device_id)
    coordinator._transport.ensure(device.device_id)


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["group", "water_detector", "bff_thermometer"])
async def test_no_request_devices_do_not_mask_a_total_outage(coordinator, light_capabilities, kind):
    _add_no_request_device(coordinator, kind, light_capabilities)
    coordinator._api_client.get_device_state = AsyncMock(
        side_effect=GoveeConnectionError("Connection error: DNS failed")
    )

    with pytest.raises(UpdateFailed, match="unreachable for all 2 device"):
        await coordinator._async_update_data()


@pytest.mark.asyncio
async def test_device_not_found_does_not_mask_a_total_outage(coordinator, light_capabilities):
    from custom_components.govee.api.exceptions import GoveeDeviceNotFoundError

    coordinator._devices[EXTRA_ID] = _device(EXTRA_ID, light_capabilities)
    coordinator._states[EXTRA_ID] = GoveeDeviceState.create_empty(EXTRA_ID)
    coordinator._transport.ensure(EXTRA_ID)

    async def fetch(device_id, sku):
        if device_id == EXTRA_ID:
            raise GoveeDeviceNotFoundError("devices not exist")
        raise GoveeConnectionError("Connection error: DNS failed")

    coordinator._api_client.get_device_state = AsyncMock(side_effect=fetch)

    with pytest.raises(UpdateFailed, match="unreachable for all 2 device"):
        await coordinator._async_update_data()


@pytest.mark.asyncio
async def test_account_of_only_no_request_devices_is_never_an_outage(coordinator, light_capabilities):
    coordinator._devices = {}
    coordinator._states = {}
    _add_no_request_device(coordinator, "group", light_capabilities)
    coordinator._api_client.get_device_state = AsyncMock(side_effect=AssertionError("groups are never polled"))

    states = await coordinator._async_update_data()

    assert states[GROUP_ID].online is True


@pytest.mark.asyncio
async def test_rate_limit_alongside_failures_is_not_an_outage(coordinator, rate_limit_issues):
    """A 429 proves the cloud answered: no UpdateFailed even if the rest failed."""
    coordinator._api_client.rate_limit_remaining = 100
    coordinator._api_client.rate_limit_reset_in = 0

    async def fetch(device_id, sku):
        if device_id == DEV_A:
            raise GoveeRateLimitError("429", retry_after=120)
        raise GoveeConnectionError("Connection error: reset")

    coordinator._api_client.get_device_state = AsyncMock(side_effect=fetch)

    states = await coordinator._async_update_data()

    assert set(states) == {DEV_A, DEV_B}


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["group", "water_detector", "bff_thermometer"])
async def test_rate_limit_clears_when_no_cloud_read_device_is_left(
    coordinator, light_capabilities, rate_limit_issues, kind
):
    """The device that drew the 429 is gone; only held devices remain, so nothing could clear it."""
    _created, deleted = rate_limit_issues
    coordinator._devices = {}
    coordinator._states = {}
    _add_no_request_device(coordinator, kind, light_capabilities)
    coordinator._rate_limited = True
    coordinator.update_interval = timedelta(seconds=600)
    coordinator._api_client.get_device_state = AsyncMock(side_effect=AssertionError("held devices are never polled"))

    await coordinator._async_update_data()

    assert coordinator._rate_limited is False
    assert coordinator.update_interval == timedelta(seconds=60)
    deleted.assert_called_once()
