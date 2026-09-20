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

from custom_components.govee.api.exceptions import GoveeApiError, GoveeConnectionError
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
