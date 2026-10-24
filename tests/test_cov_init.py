"""Entry setup paths that ``test_setup_entry*.py`` do not reach.

Covers the first-refresh exception handlers, the Bluetooth unsubscribe hooks
on unload, and the orphan-cleanup branches for disabled grouped segments,
scenes, and DIY scenes, for entities without a unique ID, and for unknown
devices after an incomplete discovery.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.govee import _async_cleanup_orphaned_entities
from custom_components.govee.api import GoveeConnectionError
from custom_components.govee.const import (
    CONF_API_KEY,
    CONF_ENABLE_DIY_SCENES,
    CONF_ENABLE_SCENES,
    CONF_SEGMENT_MODE_BY_DEVICE,
    DOMAIN,
    SEGMENT_MODE_INDIVIDUAL,
    SUFFIX_DIY_SCENE_SELECT,
    SUFFIX_GROUPED_SEGMENT,
    SUFFIX_SCENE_SELECT,
    SUFFIX_SEGMENT,
)

API_KEY = "12345678-1234-1234-1234-123456789abc"
GONE_ID = "DE:AD:BE:EF:00:00:00:01"
FIRST_REFRESH = "custom_components.govee.coordinator.GoveeCoordinator.async_config_entry_first_refresh"


@pytest.fixture(autouse=True)
def _custom_integrations(hass: HomeAssistant, enable_custom_integrations: None) -> None:
    """Let Home Assistant load the integration without starting Bluetooth."""
    hass.config.components.add("bluetooth_adapters")
    hass.config.components.add("network")


def _api_client(device, state) -> MagicMock:
    """A cloud client that knows one device and answers every read."""
    client = MagicMock(name="GoveeApiClient")
    client.get_devices = AsyncMock(return_value=[device])
    client.get_device_state = AsyncMock(return_value=state)
    client.get_dynamic_scenes = AsyncMock(return_value=[])
    client.get_diy_scenes = AsyncMock(return_value=[])
    client.close = AsyncMock()
    client.api_key = API_KEY
    client.rate_limit_remaining = 100
    client.rate_limit_total = 100
    client.rate_limit_reset = 0
    client.requests_last_24h = 0
    client.requests_today = 0
    client.requests_per_hour = 0.0
    client.last_raw_state = {}
    client.last_raw_devices = []
    client.recent_commands = []
    return client


def _events_client() -> MagicMock:
    events = MagicMock(name="GoveeOpenApiEventClient")
    events.async_start = AsyncMock()
    events.async_stop = AsyncMock()
    events.available = True
    events.connected = False
    events.recent_events = []
    return events


def _entry(hass: HomeAssistant, **options) -> MockConfigEntry:
    entry = MockConfigEntry(domain=DOMAIN, data={CONF_API_KEY: API_KEY}, options=options, version=2)
    entry.add_to_hass(hass)
    return entry


async def _setup(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    api_client: MagicMock,
    *,
    ble_unsubs: list[MagicMock] | None = None,
    first_refresh: AsyncMock | None = None,
) -> bool:
    """Set the entry up with every network boundary stubbed."""
    patches = [
        patch("custom_components.govee.GoveeApiClient", return_value=api_client),
        patch("custom_components.govee.coordinator.GoveeOpenApiEventClient", return_value=_events_client()),
        patch("custom_components.govee.coordinator.GoveeCoordinator._async_setup_lan", AsyncMock(return_value=None)),
        patch(
            "custom_components.govee.coordinator.GoveeCoordinator.setup_ble_subscriptions",
            return_value=list(ble_unsubs or []),
        ),
    ]
    if first_refresh is not None:
        patches.append(patch(FIRST_REFRESH, first_refresh))
    for p in patches:
        p.start()
    try:
        ok = await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    finally:
        for p in patches:
            p.stop()
    return ok


@pytest.mark.parametrize("error", [GoveeConnectionError("dns"), TimeoutError(), OSError("network down")])
async def test_transient_first_refresh_error_retries_setup(hass, mock_light_device, mock_device_state, error) -> None:
    """An API, timeout, or OS error escaping the first refresh becomes a retry, and the client is closed."""
    entry = _entry(hass)
    api = _api_client(mock_light_device, mock_device_state)

    assert not await _setup(hass, entry, api, first_refresh=AsyncMock(side_effect=error))

    assert entry.state is ConfigEntryState.SETUP_RETRY
    api.close.assert_awaited()


async def test_unexpected_first_refresh_error_is_a_setup_error(hass, mock_light_device, mock_device_state) -> None:
    """A bug in the first refresh surfaces as a setup error instead of being retried forever."""
    entry = _entry(hass)
    api = _api_client(mock_light_device, mock_device_state)

    assert not await _setup(hass, entry, api, first_refresh=AsyncMock(side_effect=RuntimeError("bug")))

    assert entry.state is ConfigEntryState.SETUP_ERROR
    api.close.assert_awaited()


async def test_ble_subscriptions_are_released_on_unload(hass, mock_light_device, mock_device_state) -> None:
    """Every Bluetooth unsubscribe callback is registered with the entry and runs on unload."""
    entry = _entry(hass)
    unsubs = [MagicMock(name="unsub_a"), MagicMock(name="unsub_b")]

    assert await _setup(hass, entry, _api_client(mock_light_device, mock_device_state), ble_unsubs=unsubs)
    for unsub in unsubs:
        unsub.assert_not_called()

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.NOT_LOADED
    for unsub in unsubs:
        unsub.assert_called_once_with()


def _seed(registry: er.EntityRegistry, entry: MockConfigEntry, *specs: tuple[str, str]) -> dict[str, str]:
    """Create registry entries for ``(platform, unique_id)`` pairs; return entity IDs."""
    return {
        unique_id: registry.async_get_or_create(platform, DOMAIN, unique_id, config_entry=entry).entity_id
        for platform, unique_id in specs
    }


def _coordinator(
    device_ids, *, incomplete: bool = False, segment_count: int = 15, segment_source: str = "api"
) -> SimpleNamespace:
    return SimpleNamespace(
        devices={
            did: SimpleNamespace(segment_count=segment_count, segment_count_resolution={"source": segment_source})
            for did in device_ids
        },
        leak_sensors={},
        hub_device_ids=set(),
        discovery_incomplete=incomplete,
    )


async def test_cleanup_removes_entities_of_disabled_features(hass, mock_light_device) -> None:
    """Individual-only segment mode drops the grouped entity; disabled scenes drop both selectors."""
    device_id = mock_light_device.device_id
    entry = _entry(
        hass,
        **{
            CONF_SEGMENT_MODE_BY_DEVICE: {device_id: SEGMENT_MODE_INDIVIDUAL},
            CONF_ENABLE_SCENES: False,
            CONF_ENABLE_DIY_SCENES: False,
        },
    )
    registry = er.async_get(hass)
    ids = _seed(
        registry,
        entry,
        ("light", device_id),
        ("light", f"{device_id}{SUFFIX_GROUPED_SEGMENT}"),
        ("light", f"{device_id}{SUFFIX_SEGMENT}0"),
        ("select", f"{device_id}{SUFFIX_SCENE_SELECT}"),
        ("select", f"{device_id}{SUFFIX_DIY_SCENE_SELECT}"),
    )

    await _async_cleanup_orphaned_entities(hass, entry, _coordinator([device_id]))

    survived = {uid for uid, eid in ids.items() if registry.async_get(eid) is not None}
    assert survived == {device_id, f"{device_id}{SUFFIX_SEGMENT}0"}


async def test_cleanup_ignores_entities_without_a_unique_id(hass, mock_light_device, monkeypatch) -> None:
    """A registry entry with an empty unique ID is skipped instead of matched against owners."""
    entry = _entry(hass)
    blank = SimpleNamespace(unique_id="", entity_id="light.blank", platform="light")
    monkeypatch.setattr(er, "async_entries_for_config_entry", lambda registry, entry_id: [blank])
    registry = er.async_get(hass)
    remove = MagicMock(name="async_remove")
    monkeypatch.setattr(registry, "async_remove", remove)

    await _async_cleanup_orphaned_entities(hass, entry, _coordinator([mock_light_device.device_id]))

    remove.assert_not_called()


async def test_cleanup_keeps_unknown_devices_after_incomplete_discovery(hass, mock_light_device) -> None:
    """A device the failed discovery did not report survives even with no entities left."""
    entry = _entry(hass)
    device_registry = dr.async_get(hass)
    gone = device_registry.async_get_or_create(
        config_entry_id=entry.entry_id, identifiers={(DOMAIN, GONE_ID)}, name="Gone"
    )

    await _async_cleanup_orphaned_entities(hass, entry, _coordinator([mock_light_device.device_id], incomplete=True))

    assert device_registry.async_get(gone.id) is not None
