"""Status bursts: repeated AWS IoT status queries on request (govee.request_status)."""

from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
import voluptuous as vol
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.govee.coordinator import GoveeCoordinator
from custom_components.govee.models import GoveeDevice, GoveeDeviceState, PowerCommand
from custom_components.govee.const import DOMAIN
from custom_components.govee.services import (
    SERVICE_REQUEST_STATUS_SCHEMA,
    async_request_status_handler,
    async_setup_services,
)

from .kettle_samples import DEVICE_ID, H7175_DEVICE, H7175_STATE

LOOKUP = "custom_components.govee.services._get_coordinator_for_device"


def _coordinator(hass: HomeAssistant, sku: str = "H7175") -> GoveeCoordinator:
    entry = MagicMock(entry_id="entry", options={})
    coordinator = GoveeCoordinator(
        hass=hass, config_entry=entry, api_client=MagicMock(), iot_credentials=None, poll_interval=60
    )
    coordinator._devices[DEVICE_ID] = GoveeDevice.from_api_response({**H7175_DEVICE, "sku": sku})
    state = GoveeDeviceState.create_empty(DEVICE_ID)
    state.update_from_api(H7175_STATE)
    coordinator._states[DEVICE_ID] = state
    coordinator._api_client.control_device = AsyncMock(return_value=True)
    coordinator._mqtt_client = MagicMock(connected=True, async_publish_status_query=AsyncMock(return_value=True))
    coordinator._device_topics[DEVICE_ID] = "GD/kettle"
    coordinator.async_set_updated_data = MagicMock()
    coordinator.async_update_listeners = MagicMock()
    return coordinator


@pytest.fixture
async def coordinator(hass: HomeAssistant):
    coordinator = _coordinator(hass)
    yield coordinator
    coordinator.status_burst.async_shutdown()
    coordinator.kettles.async_shutdown()


def _queries(coordinator: GoveeCoordinator) -> int:
    return coordinator._mqtt_client.async_publish_status_query.await_count


async def _advance(hass: HomeAssistant, freezer, seconds: float) -> None:
    freezer.tick(timedelta(seconds=seconds))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()


class TestBurst:
    async def test_queries_every_interval_for_the_duration(self, hass, freezer, coordinator):
        coordinator.status_burst.start(DEVICE_ID, 20, 5)
        await _advance(hass, freezer, 0)
        assert _queries(coordinator) == 1
        for _ in range(6):
            await _advance(hass, freezer, 5)
        assert _queries(coordinator) == 4  # at 0, 5, 10 and 15 s; 20 s is the end
        assert coordinator.status_burst.running(DEVICE_ID) is False

    async def test_limits_are_clamped(self, hass, freezer, coordinator):
        coordinator.status_burst.start(DEVICE_ID, 600, 1)
        await _advance(hass, freezer, 0)
        for _ in range(3):
            await _advance(hass, freezer, 1)
        assert _queries(coordinator) == 2  # the interval is at least 3 s
        for _ in range(50):
            await _advance(hass, freezer, 3)
        assert coordinator.status_burst.running(DEVICE_ID) is False  # at most 120 s

    async def test_a_new_burst_replaces_one_and_stop_ends_it(self, hass, freezer, coordinator):
        burst = coordinator.status_burst
        burst.start(DEVICE_ID, 60, 10)
        await _advance(hass, freezer, 0)
        burst.start(DEVICE_ID, 60, 30)
        await _advance(hass, freezer, 0)
        await _advance(hass, freezer, 10)
        assert _queries(coordinator) == 2  # the first burst's 10 s query is gone
        burst.stop(DEVICE_ID)
        await _advance(hass, freezer, 30)
        assert _queries(coordinator) == 2

    async def test_stopped_while_a_query_is_awaited(self, hass, freezer, coordinator):
        async def _query(topic):
            coordinator.status_burst.stop(DEVICE_ID)
            return True

        coordinator._mqtt_client.async_publish_status_query = AsyncMock(side_effect=_query)
        coordinator.status_burst.start(DEVICE_ID, 60, 5)
        await _advance(hass, freezer, 0)
        await _advance(hass, freezer, 10)
        assert _queries(coordinator) == 1

    async def test_unload_stops_and_refuses_new_bursts(self, hass, freezer, coordinator):
        coordinator.status_burst.start(DEVICE_ID, 60, 5)
        coordinator._api_client.close = AsyncMock()
        client = coordinator._mqtt_client
        client.async_stop = AsyncMock()
        await coordinator.async_shutdown()
        coordinator.status_burst.start(DEVICE_ID, 60, 5)
        await _advance(hass, freezer, 30)
        client.async_publish_status_query.assert_not_awaited()


class TestEndToEnd:
    async def test_a_burst_follows_the_heating(self, hass, freezer, coordinator):
        """Each query is answered by the kettle's own push (no Govee app involved), and applied.

        The query goes to the device's topic over the integration's AWS IoT
        session; the reply arrives as a push on the account topic.
        """
        readings = iter(range(9000, 20000, 500))
        topics: list[str] = []

        async def _query(topic):
            topics.append(topic)
            coordinator._on_mqtt_state_update(DEVICE_ID, {"onOff": 1, "sta": {"curTem": next(readings)}})
            return True

        coordinator._mqtt_client.async_publish_status_query = AsyncMock(side_effect=_query)
        coordinator._api_client.get_device_state = AsyncMock(side_effect=AssertionError("no cloud read expected"))
        coordinator._rate_limited = True  # follow-up cloud reads stay out of the way
        await coordinator.async_control_device(DEVICE_ID, PowerCommand(power_on=True))
        coordinator.status_burst.start(DEVICE_ID, 90, 5)
        await _advance(hass, freezer, 0)
        temperatures = []
        for _ in range(4):
            await _advance(hass, freezer, 5)
            temperatures.append(coordinator._states[DEVICE_ID].sensor_temperature)
        assert set(topics) == {"GD/kettle"}
        assert temperatures == sorted(temperatures) and len(set(temperatures)) == 4
        assert coordinator._states[DEVICE_ID].power_state is True


class TestStatusQuery:
    async def test_a_drop_during_the_query_is_charged_to_the_device(self, coordinator):
        seen: list[str | None] = []

        async def _query(topic):
            seen.append(coordinator._status_query_in_flight)
            return True

        coordinator._mqtt_client.async_publish_status_query = AsyncMock(side_effect=_query)
        await coordinator.async_request_status(DEVICE_ID)
        assert (seen, coordinator._status_query_in_flight) == ([DEVICE_ID], None)
        coordinator._status_query_in_flight = "sweep-device"  # a sweep's query is not taken over
        await coordinator.async_request_status(DEVICE_ID)
        assert (seen[-1], coordinator._status_query_in_flight) == ("sweep-device", "sweep-device")

    @pytest.mark.parametrize("case", ["no_client", "down", "no_topic", "quarantined"])
    async def test_not_possible(self, coordinator, case):
        if case == "no_client":
            coordinator._mqtt_client = None
        elif case == "down":
            coordinator._mqtt_client.connected = False
        elif case == "no_topic":
            coordinator._device_topics.clear()
        else:
            coordinator._status_query_quarantine.add(DEVICE_ID)
        assert coordinator.status_query_possible(DEVICE_ID) is False
        await coordinator.async_request_status(DEVICE_ID)
        if case != "no_client":
            coordinator._mqtt_client.async_publish_status_query.assert_not_awaited()


class TestService:
    def _call(self, **data):
        return SimpleNamespace(data=SERVICE_REQUEST_STATUS_SCHEMA({"device_id": DEVICE_ID, **data}))

    async def test_once_or_a_burst(self, monkeypatch):
        coordinator = MagicMock()
        coordinator.status_query_possible = MagicMock(return_value=True)
        coordinator.async_request_status = AsyncMock()
        monkeypatch.setattr(LOOKUP, lambda hass, raw: (coordinator, DEVICE_ID))
        await async_request_status_handler(MagicMock(), self._call())
        coordinator.async_request_status.assert_awaited_once_with(DEVICE_ID)
        await async_request_status_handler(MagicMock(), self._call(duration=60, interval=4))
        coordinator.status_burst.start.assert_called_once_with(DEVICE_ID, 60.0, 4.0)

    async def test_errors(self, monkeypatch):
        monkeypatch.setattr(LOOKUP, lambda hass, raw: None)
        with pytest.raises(ServiceValidationError):
            await async_request_status_handler(MagicMock(), self._call())
        coordinator = MagicMock(devices={})
        coordinator.status_query_possible = MagicMock(return_value=False)
        monkeypatch.setattr(LOOKUP, lambda hass, raw: (coordinator, DEVICE_ID))
        with pytest.raises(HomeAssistantError) as err:
            await async_request_status_handler(MagicMock(), self._call())
        assert err.value.translation_key == "status_query_unavailable"

    async def test_registered(self, hass: HomeAssistant, monkeypatch):
        coordinator = MagicMock()
        coordinator.status_query_possible = MagicMock(return_value=True)
        coordinator.async_request_status = AsyncMock()
        monkeypatch.setattr(LOOKUP, lambda hass, raw: (coordinator, DEVICE_ID))
        async_setup_services(hass)
        await hass.services.async_call(DOMAIN, "request_status", {"device_id": DEVICE_ID}, blocking=True)
        coordinator.async_request_status.assert_awaited_once_with(DEVICE_ID)

    @pytest.mark.parametrize("data", [{"duration": 121}, {"interval": 2}, {"duration": -1}])
    def test_schema_limits(self, data):
        with pytest.raises(vol.Invalid):
            SERVICE_REQUEST_STATUS_SCHEMA({"device_id": DEVICE_ID, **data})
