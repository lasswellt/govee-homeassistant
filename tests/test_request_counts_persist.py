"""The daily request count survives a reload, so budget pacing stays honest.

Budget pacing spreads the day's polls over what is left of Govee's daily
allowance. If the count restarts at zero on every reload (an options change,
the automatic new-leak-sensor reload, a restart), pacing believes the whole
budget is unspent and can push the install past Govee's daily cap.
"""

from __future__ import annotations

import time
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.govee.api.client import GoveeApiClient
from custom_components.govee.const import CONF_API_KEY, DOMAIN

API_KEY = "12345678-1234-1234-1234-123456789abc"


@pytest.fixture(autouse=True)
def _custom_integrations(hass: HomeAssistant, enable_custom_integrations: None) -> None:
    hass.config.components.add("bluetooth_adapters")
    hass.config.components.add("network")


def _counting_client_factory(device, state):
    """A real client whose network calls are stubbed but still counted."""

    class _CountingClient(GoveeApiClient):
        def __init__(self, api_key: str, hass: Any = None, session: Any = None) -> None:
            super().__init__(api_key, session=MagicMock())

        async def get_devices(self):  # type: ignore[override]
            self._note_request()
            return [device]

        async def get_device_state(self, device_id, sku):  # type: ignore[override]
            self._note_request()
            return state

        async def get_dynamic_scenes(self, *args, **kwargs):  # type: ignore[override]
            return []

        async def get_diy_scenes(self, *args, **kwargs):  # type: ignore[override]
            return []

        async def close(self) -> None:
            return None

    return _CountingClient


def _events_client() -> MagicMock:
    events = MagicMock(name="GoveeOpenApiEventClient")
    events.async_start = AsyncMock()
    events.async_stop = AsyncMock()
    events.available = True
    events.connected = False
    events.recent_events = []
    return events


def _patches(device, state):
    return (
        patch("custom_components.govee.GoveeApiClient", _counting_client_factory(device, state)),
        patch("custom_components.govee.coordinator.GoveeOpenApiEventClient", return_value=_events_client()),
        patch(
            "custom_components.govee.coordinator.GoveeCoordinator._async_setup_lan",
            AsyncMock(return_value=None),
        ),
        patch(
            "custom_components.govee.coordinator.GoveeCoordinator.setup_ble_subscriptions",
            return_value=[],
        ),
    )


async def test_request_count_survives_a_reload(
    hass: HomeAssistant, hass_storage: dict[str, Any], mock_light_device, mock_device_state
) -> None:
    entry = MockConfigEntry(domain=DOMAIN, data={CONF_API_KEY: API_KEY}, options={}, version=2)
    entry.add_to_hass(hass)
    patches = _patches(mock_light_device, mock_device_state)
    for p in patches:
        p.start()
    try:
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        first = entry.runtime_data._api_client.requests_today
        first_24h = entry.runtime_data._api_client.requests_last_24h
        assert first > 0
        old_client = entry.runtime_data._api_client

        assert await hass.config_entries.async_reload(entry.entry_id)
        await hass.async_block_till_done()
        # The unloaded client no longer schedules saves: a late request on it
        # (an in-flight service call) must not overwrite the new client's file.
        assert old_client._request_listener is None
        # The second setup spends the same requests again, on top of the first.
        assert entry.runtime_data._api_client.requests_today == 2 * first
        assert entry.runtime_data._api_client.requests_last_24h == 2 * first_24h

        assert await hass.config_entries.async_remove(entry.entry_id)
        await hass.async_block_till_done()
    finally:
        for p in patches:
            p.stop()

    assert not [key for key in hass_storage if key.startswith(f"{DOMAIN}.") and entry.entry_id in key]


# --- The counters themselves ---------------------------------------------------


def test_export_import_round_trip() -> None:
    old = GoveeApiClient(API_KEY, session=MagicMock())
    for _ in range(5):
        old._note_request()

    new = GoveeApiClient(API_KEY, session=MagicMock())
    new.import_request_counts(old.export_request_counts())

    assert new.requests_today == 5
    assert new.requests_last_24h == 5


def test_a_saved_count_from_another_day_is_not_today() -> None:
    client = GoveeApiClient(API_KEY, session=MagicMock())
    yesterday = int(time.time() // 86400) - 1
    hour = int(time.time() // 3600)
    client.import_request_counts({"day": yesterday, "today": 400, "buckets": [[hour - 30, 400], [hour, 7]]})

    assert client.requests_today == 0
    # Only the hour still inside the 24 h window is kept.
    assert client.requests_last_24h == 7


@pytest.mark.parametrize(
    "saved",
    [None, "junk", {"day": "today", "today": 3}, {"buckets": "x"}, {"buckets": [[1], ["a", 2], [True, 3]]}],
)
def test_malformed_saved_counts_are_ignored(saved: Any) -> None:
    client = GoveeApiClient(API_KEY, session=MagicMock())
    client.import_request_counts(saved)

    assert client.requests_today == 0
    assert client.requests_last_24h == 0


def test_imported_counts_merge_with_requests_already_made() -> None:
    client = GoveeApiClient(API_KEY, session=MagicMock())
    client._note_request()
    hour = int(time.time() // 3600)
    client.import_request_counts({"day": int(time.time() // 86400), "today": 4, "buckets": [[hour, 4]]})

    assert client.requests_today == 5
    assert client.requests_last_24h == 5


def test_request_listener_is_told_of_each_request() -> None:
    client = GoveeApiClient(API_KEY, session=MagicMock())
    listener = MagicMock()
    client.set_request_listener(listener)

    client._note_request()
    client._note_request()

    assert listener.call_count == 2
