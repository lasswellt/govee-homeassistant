"""Set up and tear down the config entry through Home Assistant itself.

These tests use a real ``MockConfigEntry`` with the real entity and device
registries, so they cover ``async_setup``, ``async_setup_entry``,
``async_unload_entry``, the orphan cleanup, and
``async_remove_config_entry_device`` end to end. The cloud, LAN, Bluetooth,
and MQTT transports are stubbed at their boundaries.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.govee import (
    _async_cleanup_orphaned_entities,
    _async_migrate_hub_device,
    async_remove_config_entry_device,
)
from custom_components.govee.const import (
    CONF_API_KEY,
    CONF_SEGMENT_MODE_BY_DEVICE,
    DOMAIN,
    LEGACY_HUB_DEVICE_IDENTIFIER,
    SEGMENT_MODE_BOTH,
    SEGMENT_MODE_GROUPED,
    SUFFIX_DIY_STYLE_SELECT,
    SUFFIX_GROUPED_SEGMENT,
    SUFFIX_SEGMENT,
    hub_device_identifier,
)

API_KEY = "12345678-1234-1234-1234-123456789abc"
LEAK_ID = "11:22:33:44:55:66:77:88"
HUB_ID = "99:88:77:66:55:44:33:22"
GONE_ID = "DE:AD:BE:EF:00:00:00:01"


@pytest.fixture(autouse=True)
def _custom_integrations(hass: HomeAssistant, enable_custom_integrations: None) -> None:
    """Let Home Assistant load ``custom_components.govee``.

    The integration depends on ``bluetooth_adapters`` and ``network``; mark
    them set up so Home Assistant does not start the Bluetooth stack here.
    """
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


def _stub_transports(hass: HomeAssistant, api_client: MagicMock):
    """Patch every network boundary the entry setup would otherwise touch."""
    return (
        patch("custom_components.govee.GoveeApiClient", return_value=api_client),
        patch(
            "custom_components.govee.coordinator.GoveeOpenApiEventClient",
            return_value=_events_client(),
        ),
        patch(
            "custom_components.govee.coordinator.GoveeCoordinator._async_setup_lan",
            AsyncMock(return_value=None),
        ),
        patch(
            "custom_components.govee.coordinator.GoveeCoordinator.setup_ble_subscriptions",
            return_value=[],
        ),
    )


async def _setup_entry(hass: HomeAssistant, device, state) -> MockConfigEntry:
    entry = MockConfigEntry(domain=DOMAIN, data={CONF_API_KEY: API_KEY}, options={}, version=2)
    entry.add_to_hass(hass)
    patches = _stub_transports(hass, _api_client(device, state))
    for p in patches:
        p.start()
    try:
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    finally:
        for p in patches:
            p.stop()
    return entry


async def test_setup_registers_services_and_creates_entities(
    hass: HomeAssistant, mock_light_device, mock_device_state
) -> None:
    """A loaded entry creates the light and hub sensors and registers the actions."""
    entry = await _setup_entry(hass, mock_light_device, mock_device_state)

    assert entry.state is ConfigEntryState.LOADED
    assert hass.services.has_service(DOMAIN, "refresh_scenes")
    assert hass.services.has_service(DOMAIN, "set_segment_color")

    registry = er.async_get(hass)
    unique_ids = {e.unique_id for e in er.async_entries_for_config_entry(registry, entry.entry_id)}
    assert mock_light_device.device_id in unique_ids
    assert f"{entry.entry_id}_rate_limit" in unique_ids

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.NOT_LOADED
    # Actions stay registered: they validate a loaded entry per call.
    assert hass.services.has_service(DOMAIN, "refresh_scenes")


async def test_services_registered_without_an_entry(hass: HomeAssistant) -> None:
    """Actions exist as soon as the component is set up (rule action-setup)."""
    assert await async_setup_component(hass, DOMAIN, {})
    assert hass.services.has_service(DOMAIN, "set_segment_color")


async def test_reload_keeps_registry_entries(hass: HomeAssistant, mock_light_device, mock_device_state) -> None:
    """Hub-level diagnostics survive a reload with their registry entries intact."""
    entry = await _setup_entry(hass, mock_light_device, mock_device_state)
    registry = er.async_get(hass)
    before = {e.unique_id: e.id for e in er.async_entries_for_config_entry(registry, entry.entry_id)}

    patches = _stub_transports(hass, _api_client(mock_light_device, mock_device_state))
    for p in patches:
        p.start()
    try:
        assert await hass.config_entries.async_reload(entry.entry_id)
        await hass.async_block_till_done()
    finally:
        for p in patches:
            p.stop()

    after = {e.unique_id: e.id for e in er.async_entries_for_config_entry(registry, entry.entry_id)}
    assert after == before


def _seed(registry: er.EntityRegistry, entry: MockConfigEntry, *specs: tuple[str, str]) -> dict[str, str]:
    """Create registry entries for ``(platform, unique_id)`` pairs; return entity IDs."""
    return {
        unique_id: registry.async_get_or_create(platform, DOMAIN, unique_id, config_entry=entry).entity_id
        for platform, unique_id in specs
    }


def _coordinator(
    device_ids, *, leak_ids=(), hub_ids=(), incomplete=False, segment_count=15, segment_source="override"
) -> SimpleNamespace:
    return SimpleNamespace(
        devices={
            did: SimpleNamespace(segment_count=segment_count, segment_count_resolution={"source": segment_source})
            for did in device_ids
        },
        leak_sensors={lid: object() for lid in leak_ids},
        hub_device_ids=set(hub_ids),
        discovery_incomplete=incomplete,
    )


async def test_cleanup_keeps_leak_hub_and_diagnostic_entities(hass: HomeAssistant, mock_light_device) -> None:
    """Entities not keyed by ``coordinator.devices`` are not orphans."""
    entry = MockConfigEntry(domain=DOMAIN, data={CONF_API_KEY: API_KEY}, options={})
    entry.add_to_hass(hass)
    registry = er.async_get(hass)
    device_id = mock_light_device.device_id
    ids = _seed(
        registry,
        entry,
        ("light", device_id),
        ("sensor", f"{entry.entry_id}_rate_limit"),
        ("sensor", f"{entry.entry_id}_mqtt_status"),
        ("binary_sensor", f"{LEAK_ID}_leak"),
        ("sensor", f"{LEAK_ID}_battery"),
        ("binary_sensor", f"{HUB_ID}_hub_online"),
        ("light", f"{GONE_ID}"),
    )

    await _async_cleanup_orphaned_entities(
        hass, entry, _coordinator([device_id], leak_ids=[LEAK_ID], hub_ids=[HUB_ID])
    )

    survived = {uid for uid, eid in ids.items() if registry.async_get(eid) is not None}
    assert survived == set(ids) - {GONE_ID}


async def test_cleanup_skips_unknown_devices_after_failed_discovery(hass: HomeAssistant, mock_light_device) -> None:
    """A BFF timeout must not delete the leak sensors it failed to discover."""
    entry = MockConfigEntry(domain=DOMAIN, data={CONF_API_KEY: API_KEY}, options={})
    entry.add_to_hass(hass)
    registry = er.async_get(hass)
    ids = _seed(registry, entry, ("binary_sensor", f"{LEAK_ID}_leak"))

    await _async_cleanup_orphaned_entities(hass, entry, _coordinator([mock_light_device.device_id], incomplete=True))

    assert registry.async_get(ids[f"{LEAK_ID}_leak"]) is not None


async def test_cleanup_skips_everything_when_discovery_is_empty(
    hass: HomeAssistant,
) -> None:
    """An empty device list is treated as an API glitch, not as removal of everything."""
    entry = MockConfigEntry(domain=DOMAIN, data={CONF_API_KEY: API_KEY}, options={})
    entry.add_to_hass(hass)
    registry = er.async_get(hass)
    ids = _seed(registry, entry, ("light", GONE_ID))

    await _async_cleanup_orphaned_entities(hass, entry, _coordinator([]))

    assert registry.async_get(ids[GONE_ID]) is not None


async def test_cleanup_applies_feature_toggles(hass: HomeAssistant, mock_light_device) -> None:
    """Segment mode 'both' keeps grouped and individual; the DIY style selector goes."""
    device_id = mock_light_device.device_id
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_API_KEY: API_KEY},
        options={CONF_SEGMENT_MODE_BY_DEVICE: {device_id: SEGMENT_MODE_BOTH}},
    )
    entry.add_to_hass(hass)
    registry = er.async_get(hass)
    ids = _seed(
        registry,
        entry,
        ("light", f"{device_id}{SUFFIX_GROUPED_SEGMENT}"),
        ("light", f"{device_id}{SUFFIX_SEGMENT}0"),
        ("select", f"{device_id}{SUFFIX_DIY_STYLE_SELECT}"),
    )

    await _async_cleanup_orphaned_entities(hass, entry, _coordinator([device_id]))

    assert registry.async_get(ids[f"{device_id}{SUFFIX_GROUPED_SEGMENT}"]) is not None
    assert registry.async_get(ids[f"{device_id}{SUFFIX_SEGMENT}0"]) is not None
    assert registry.async_get(ids[f"{device_id}{SUFFIX_DIY_STYLE_SELECT}"]) is None

    # Grouped-only mode removes the individual segment entity.
    hass.config_entries.async_update_entry(
        entry, options={CONF_SEGMENT_MODE_BY_DEVICE: {device_id: SEGMENT_MODE_GROUPED}}
    )
    await _async_cleanup_orphaned_entities(hass, entry, _coordinator([device_id]))
    assert registry.async_get(ids[f"{device_id}{SUFFIX_SEGMENT}0"]) is None
    assert registry.async_get(ids[f"{device_id}{SUFFIX_GROUPED_SEGMENT}"]) is not None


@pytest.mark.parametrize("segment_source", ["override", "size_max"])
async def test_cleanup_removes_segments_beyond_the_segment_count(
    hass: HomeAssistant, mock_light_device, segment_source
) -> None:
    """Segment entities left over from a higher count (before a SKU override) are removed."""
    device_id = mock_light_device.device_id
    entry = MockConfigEntry(domain=DOMAIN, data={CONF_API_KEY: API_KEY}, options={})
    entry.add_to_hass(hass)
    registry = er.async_get(hass)
    ids = _seed(
        registry,
        entry,
        *(("light", f"{device_id}{SUFFIX_SEGMENT}{index}") for index in range(5)),
        ("binary_sensor", f"{LEAK_ID}_leak"),
        ("binary_sensor", f"{HUB_ID}_hub_online"),
    )

    await _async_cleanup_orphaned_entities(
        hass,
        entry,
        _coordinator(
            [device_id], leak_ids=[LEAK_ID], hub_ids=[HUB_ID], segment_count=3, segment_source=segment_source
        ),
    )

    survived = {uid for uid, eid in ids.items() if registry.async_get(eid) is not None}
    assert survived == {
        f"{device_id}{SUFFIX_SEGMENT}0",
        f"{device_id}{SUFFIX_SEGMENT}1",
        f"{device_id}{SUFFIX_SEGMENT}2",
        f"{LEAK_ID}_leak",
        f"{HUB_ID}_hub_online",
    }


@pytest.mark.parametrize(
    ("segment_count", "segment_source", "incomplete"),
    [
        (0, "override", False),  # a zero count is never trusted
        (3, "api", False),  # a parser-only count may be a bad API response
        (3, None, False),  # no resolution recorded at all
        (3, "override", True),  # a startup discovery step failed
    ],
)
async def test_cleanup_keeps_segments_when_the_count_is_not_trusted(
    hass: HomeAssistant, mock_light_device, segment_count, segment_source, incomplete
) -> None:
    """High-index segment entities stay unless the lower count came from an override or clamp."""
    device_id = mock_light_device.device_id
    entry = MockConfigEntry(domain=DOMAIN, data={CONF_API_KEY: API_KEY}, options={})
    entry.add_to_hass(hass)
    registry = er.async_get(hass)
    ids = _seed(registry, entry, *(("light", f"{device_id}{SUFFIX_SEGMENT}{index}") for index in range(5)))
    coordinator = _coordinator(
        [device_id], segment_count=segment_count, segment_source=segment_source, incomplete=incomplete
    )
    if segment_source is None:
        coordinator.devices[device_id].segment_count_resolution = None

    await _async_cleanup_orphaned_entities(hass, entry, coordinator)

    assert all(registry.async_get(eid) is not None for eid in ids.values())


async def test_cleanup_keeps_owned_devices_without_entities(hass: HomeAssistant, mock_light_device) -> None:
    """A hub registered before its first entity exists is not removed."""
    entry = MockConfigEntry(domain=DOMAIN, data={CONF_API_KEY: API_KEY}, options={})
    entry.add_to_hass(hass)
    device_registry = dr.async_get(hass)
    hub = device_registry.async_get_or_create(
        config_entry_id=entry.entry_id, identifiers={(DOMAIN, HUB_ID)}, name="Hub"
    )
    gone = device_registry.async_get_or_create(
        config_entry_id=entry.entry_id, identifiers={(DOMAIN, GONE_ID)}, name="Gone"
    )
    integration = device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, hub_device_identifier(entry.entry_id))},
        name="Govee Integration",
    )

    await _async_cleanup_orphaned_entities(hass, entry, _coordinator([mock_light_device.device_id], hub_ids=[HUB_ID]))

    assert device_registry.async_get(hub.id) is not None
    assert device_registry.async_get(integration.id) is not None
    assert device_registry.async_get(gone.id) is None


async def test_remove_config_entry_device(hass: HomeAssistant, mock_light_device) -> None:
    """Users may delete devices the account no longer reports, not live ones."""
    entry = MockConfigEntry(domain=DOMAIN, data={CONF_API_KEY: API_KEY}, options={})
    entry.add_to_hass(hass)
    entry.runtime_data = _coordinator([mock_light_device.device_id], hub_ids=[HUB_ID])
    device_registry = dr.async_get(hass)
    live = device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, mock_light_device.device_id)},
    )
    hub = device_registry.async_get_or_create(config_entry_id=entry.entry_id, identifiers={(DOMAIN, HUB_ID)})
    stale = device_registry.async_get_or_create(config_entry_id=entry.entry_id, identifiers={(DOMAIN, GONE_ID)})

    assert await async_remove_config_entry_device(hass, entry, live) is False
    assert await async_remove_config_entry_device(hass, entry, hub) is False
    assert await async_remove_config_entry_device(hass, entry, stale) is True


# --------------------------------------------------------------------------- #
# Per-entry diagnostics device (migration off the shared "hub" identifier)
# --------------------------------------------------------------------------- #


def _legacy_hub(hass: HomeAssistant, *entries: MockConfigEntry) -> tuple[dr.DeviceEntry, dict[str, str]]:
    """The old shared diagnostics device with each entry's rate-limit sensor on it."""
    device_registry = dr.async_get(hass)
    entity_registry = er.async_get(hass)
    device = None
    entity_ids = {}
    for entry in entries:
        device = device_registry.async_get_or_create(
            config_entry_id=entry.entry_id,
            identifiers={(DOMAIN, LEGACY_HUB_DEVICE_IDENTIFIER)},
            name="Govee Integration",
            manufacturer="Govee",
            model="Cloud API",
        )
        entity_ids[entry.entry_id] = entity_registry.async_get_or_create(
            "sensor", DOMAIN, f"{entry.entry_id}_rate_limit", config_entry=entry, device_id=device.id
        ).entity_id
    assert device is not None
    return device, entity_ids


def _add_entry(hass: HomeAssistant, key: str = API_KEY) -> MockConfigEntry:
    entry = MockConfigEntry(domain=DOMAIN, data={CONF_API_KEY: key}, options={}, version=2)
    entry.add_to_hass(hass)
    return entry


async def test_hub_device_migrates_in_place_for_a_single_entry(hass: HomeAssistant) -> None:
    """One account: the same device is re-identified, keeping its id, area, name and entities."""
    entry = _add_entry(hass)
    legacy, entity_ids = _legacy_hub(hass, entry)
    device_registry = dr.async_get(hass)
    device_registry.async_update_device(legacy.id, area_id="living_room", name_by_user="Govee Cloud")

    _async_migrate_hub_device(hass, entry)

    device = device_registry.async_get(legacy.id)
    assert device is not None
    assert device.identifiers == {(DOMAIN, hub_device_identifier(entry.entry_id))}
    assert device.area_id == "living_room"
    assert device.name_by_user == "Govee Cloud"
    entity = er.async_get(hass).async_get(entity_ids[entry.entry_id])
    assert entity is not None
    assert entity.device_id == legacy.id
    assert device_registry.async_get_device(identifiers={(DOMAIN, LEGACY_HUB_DEVICE_IDENTIFIER)}) is None


async def test_hub_device_splits_when_two_entries_share_it(hass: HomeAssistant) -> None:
    """Two accounts: each ends with its own device and keeps its entity id."""
    first = _add_entry(hass)
    second = _add_entry(hass, "abcdefab-abcd-abcd-abcd-abcdefabcdef")
    legacy, entity_ids = _legacy_hub(hass, first, second)
    device_registry = dr.async_get(hass)
    entity_registry = er.async_get(hass)
    device_registry.async_update_device(legacy.id, area_id="living_room", name_by_user="Govee Cloud")

    _async_migrate_hub_device(hass, first)

    first_device = device_registry.async_get_device(identifiers={(DOMAIN, hub_device_identifier(first.entry_id))})
    assert first_device is not None
    assert first_device.id != legacy.id
    assert first_device.config_entries == {first.entry_id}
    # The user's area and rename follow the entry onto its own device.
    assert first_device.area_id == "living_room"
    assert first_device.name_by_user == "Govee Cloud"
    first_entity = entity_registry.async_get(entity_ids[first.entry_id])
    assert first_entity is not None
    assert first_entity.device_id == first_device.id
    shared = device_registry.async_get(legacy.id)
    assert shared is not None
    assert shared.config_entries == {second.entry_id}
    second_entity = entity_registry.async_get(entity_ids[second.entry_id])
    assert second_entity is not None
    assert second_entity.device_id == legacy.id

    # The last owner then takes the original device over in place.
    _async_migrate_hub_device(hass, second)

    second_device = device_registry.async_get(legacy.id)
    assert second_device is not None
    assert second_device.identifiers == {(DOMAIN, hub_device_identifier(second.entry_id))}
    assert entity_registry.async_get(entity_ids[second.entry_id]) is not None


async def test_hub_device_migration_reuses_an_existing_per_entry_device(hass: HomeAssistant) -> None:
    """If the per-entry device already exists, the entities move to it and the entry leaves the old one."""
    entry = _add_entry(hass)
    legacy, entity_ids = _legacy_hub(hass, entry)
    device_registry = dr.async_get(hass)
    existing = device_registry.async_get_or_create(
        config_entry_id=entry.entry_id, identifiers={(DOMAIN, hub_device_identifier(entry.entry_id))}
    )

    _async_migrate_hub_device(hass, entry)

    entity = er.async_get(hass).async_get(entity_ids[entry.entry_id])
    assert entity is not None
    assert entity.device_id == existing.id
    assert device_registry.async_get(legacy.id) is None


async def test_hub_device_migration_is_a_no_op_without_the_legacy_device(hass: HomeAssistant) -> None:
    entry = _add_entry(hass)
    other = _add_entry(hass, "abcdefab-abcd-abcd-abcd-abcdefabcdef")
    legacy, _ = _legacy_hub(hass, other)

    _async_migrate_hub_device(hass, entry)
    _async_migrate_hub_device(hass, _add_entry(hass, "00000000-0000-0000-0000-000000000000"))

    device = dr.async_get(hass).async_get(legacy.id)
    assert device is not None
    assert device.identifiers == {(DOMAIN, LEGACY_HUB_DEVICE_IDENTIFIER)}


async def test_setup_attaches_hub_sensors_to_a_per_entry_device(
    hass: HomeAssistant, mock_light_device, mock_device_state
) -> None:
    """A real setup puts the rate-limit sensor on this entry's own diagnostics device."""
    entry = await _setup_entry(hass, mock_light_device, mock_device_state)

    entity_registry = er.async_get(hass)
    entity_id = entity_registry.async_get_entity_id("sensor", DOMAIN, f"{entry.entry_id}_rate_limit")
    assert entity_id is not None
    entity = entity_registry.async_get(entity_id)
    assert entity is not None
    device = dr.async_get(hass).async_get(entity.device_id)
    assert device is not None
    assert device.identifiers == {(DOMAIN, hub_device_identifier(entry.entry_id))}

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
