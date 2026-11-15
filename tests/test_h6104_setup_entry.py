"""H6104 opt-in Bluetooth through a real config entry (#267).

A real ``MockConfigEntry`` drives setup, reload and unload with the real
coordinator, advertisement handler and entity platforms. Only the transport
boundaries are replaced: the cloud and event clients, LAN discovery, the
Home Assistant Bluetooth scanner lookups, and the bleak connection.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Iterator
from contextlib import ExitStack
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from bleak.backends.device import BLEDevice
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.govee.api.ble import (
    BLE_COMMAND_SUPPORTED_MODELS,
    SEGMENTED_MODELS,
    LEGACY_UART_CHARACTERISTIC_UUID,
    WRITE_CHARACTERISTIC_UUID,
    GoveeBLEDevice,
    _build_power_frame,
)
from custom_components.govee.const import CONF_API_KEY, CONF_ENABLE_H6104_BLE, DOMAIN
from custom_components.govee.coordinator import GoveeCoordinator
from custom_components.govee.models import GoveeDevice, PowerCommand
from tests.test_setup_entry import API_KEY, _api_client, _events_client

DEVICE_ID = "AA:BB:CC:DD:EE:FF:00:11"
# The cloud ID carries the BLE MAC after two leading octets.
BLE_MAC = "CC:DD:EE:FF:00:11"
_ADV = "custom_components.govee.ble_advertisement"


@pytest.fixture(autouse=True)
def _custom_integrations(hass: HomeAssistant, enable_custom_integrations: None) -> None:
    """Let Home Assistant load the integration without starting the Bluetooth stack."""
    hass.config.components.add("bluetooth_adapters")
    hass.config.components.add("network")


@pytest.fixture
def transports() -> Iterator[ExitStack]:
    """Keep the transport patches alive for the whole test, including reloads."""
    with ExitStack() as stack:
        yield stack


def _fake_bleak_client() -> MagicMock:
    """A connected bleak client exposing only the control characteristic."""
    client = MagicMock(name="BleakClient", address=BLE_MAC)
    client.is_connected = True
    client.services.get_characteristic = MagicMock(
        side_effect=lambda uuid: (
            MagicMock() if uuid in (WRITE_CHARACTERISTIC_UUID, LEGACY_UART_CHARACTERISTIC_UUID) else None
        )
    )
    client.write_gatt_char = AsyncMock()
    client.start_notify = AsyncMock()
    client.disconnect = AsyncMock()
    return client


def _advert(sku: str) -> SimpleNamespace:
    """The Bluetooth service info HA would hand the integration."""
    return SimpleNamespace(
        name=f"Govee_{sku}_0011",
        address=BLE_MAC,
        device=BLEDevice(BLE_MAC, f"Govee_{sku}_0011", None),
        advertisement=MagicMock(),
    )


def _boot(
    hass: HomeAssistant,
    stack: ExitStack,
    light_capabilities,
    mock_device_state,
    sku: str,
    options: dict,
) -> tuple[MockConfigEntry, MagicMock, MagicMock]:
    """Add an entry for one ``sku`` device whose BLE advert is already cached."""
    device = GoveeDevice(
        device_id=DEVICE_ID,
        sku=sku,
        name=f"Test {sku}",
        device_type="devices.types.light",
        capabilities=light_capabilities,
        is_group=False,
    )
    api_client = _api_client(device, mock_device_state)
    api_client.control_device = AsyncMock(return_value=True)
    bleak_client = _fake_bleak_client()
    # Home Assistant's Bluetooth component is not importable in every test
    # environment, so the advertisement module's symbols are bound here.
    bluetooth = MagicMock(name="bluetooth")
    bluetooth.async_scanner_count.return_value = 1
    bluetooth.async_last_service_info.return_value = _advert(sku)
    for p in (
        patch("custom_components.govee.GoveeApiClient", return_value=api_client),
        patch("custom_components.govee.coordinator.GoveeOpenApiEventClient", return_value=_events_client()),
        patch("custom_components.govee.coordinator.GoveeCoordinator._async_setup_lan", AsyncMock(return_value=None)),
        patch("custom_components.govee.coordinator.GoveeCoordinator.setup_ble_subscriptions", return_value=[]),
        patch(f"{_ADV}.HAS_BLUETOOTH", True),
        patch(f"{_ADV}.bt_component", bluetooth, create=True),
        patch(f"{_ADV}.GoveeBLEDevice", GoveeBLEDevice, create=True),
        patch(f"{_ADV}.SEGMENTED_MODELS", SEGMENTED_MODELS, create=True),
        patch(f"{_ADV}.BLE_COMMAND_SUPPORTED_MODELS", BLE_COMMAND_SUPPORTED_MODELS, create=True),
        patch("custom_components.govee.api.ble.close_stale_connections_by_address", AsyncMock()),
        patch("custom_components.govee.api.ble.establish_connection", AsyncMock(return_value=bleak_client)),
    ):
        stack.enter_context(p)
    entry = MockConfigEntry(domain=DOMAIN, data={CONF_API_KEY: API_KEY}, options=options, version=2)
    entry.add_to_hass(hass)
    return entry, api_client, bleak_client


async def _setup(hass: HomeAssistant, entry: MockConfigEntry) -> GoveeCoordinator:
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED
    return entry.runtime_data


def _tv_unique_ids(hass: HomeAssistant, entry: MockConfigEntry) -> set[str]:
    registry = er.async_get(hass)
    return {
        e.unique_id
        for e in er.async_entries_for_config_entry(registry, entry.entry_id)
        if e.unique_id in (f"{DEVICE_ID}_tv_sensitivity", f"{DEVICE_ID}_tv_area")
    }


async def test_option_on_enrols_creates_entities_and_stops_ble_on_reload_and_unload(
    hass: HomeAssistant, transports: ExitStack, light_capabilities, mock_device_state
) -> None:
    """Opted-in H6104 enrols from its advert, gets the TV entities, and is stopped on reload and unload."""
    entry, api_client, bleak_client = _boot(
        hass, transports, light_capabilities, mock_device_state, "H6104", {CONF_ENABLE_H6104_BLE: True}
    )
    stop_spy = transports.enter_context(
        patch.object(GoveeBLEDevice, "stop", autospec=True, side_effect=GoveeBLEDevice.stop)
    )
    # Let the idle disconnect fire during the test instead of after 120 s.
    transports.enter_context(patch("custom_components.govee.api.ble.DISCONNECT_DELAY", 0.01))

    coordinator = await _setup(hass, entry)

    ble = coordinator._ble_devices[DEVICE_ID]
    assert ble.supports_readback is True
    assert ble._report_callbacks, "H6104 reports must reach the coordinator"
    assert _tv_unique_ids(hass, entry) == {f"{DEVICE_ID}_tv_sensitivity", f"{DEVICE_ID}_tv_area"}

    # A command over BLE arms the idle timer; wait for the idle disconnect task.
    assert await coordinator.async_control_device(DEVICE_ID, PowerCommand(power_on=True))
    bleak_client.write_gatt_char.assert_awaited()
    assert bleak_client.write_gatt_char.await_args.args[0] == LEGACY_UART_CHARACTERISTIC_UUID
    api_client.control_device.assert_not_awaited()
    await asyncio.sleep(0.1)
    assert ble._idle_disconnect_task is not None
    bleak_client.disconnect.assert_awaited()

    # Reload: the old coordinator stops its BLE device, the new one re-enrols.
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED
    stop_spy.assert_awaited_once()
    assert stop_spy.await_args.args[0] is ble
    assert ble._idle_disconnect_task is None
    assert ble._report_callbacks == []
    assert ble._disconnect_timer is None
    new_ble = entry.runtime_data._ble_devices[DEVICE_ID]
    assert new_ble is not ble and new_ble.supports_readback is True
    assert _tv_unique_ids(hass, entry) == {f"{DEVICE_ID}_tv_sensitivity", f"{DEVICE_ID}_tv_area"}

    # Unload stops the re-enrolled device too and leaves its link released.
    assert await hass.config_entries.async_unload(entry.entry_id)
    assert entry.state is ConfigEntryState.NOT_LOADED
    assert stop_spy.await_count == 2
    assert stop_spy.await_args.args[0] is new_ble
    assert new_ble._report_callbacks == []
    assert new_ble._disconnect_timer is None


async def test_option_off_creates_no_ble_entities(
    hass: HomeAssistant, transports: ExitStack, light_capabilities, mock_device_state, caplog
) -> None:
    """H6104 without the option stays cloud-only: no BLE device, no TV entities, no errors."""
    entry, api_client, bleak_client = _boot(hass, transports, light_capabilities, mock_device_state, "H6104", {})

    with caplog.at_level(logging.WARNING):
        coordinator = await _setup(hass, entry)
        assert not coordinator.h6104_ble_enabled
        assert coordinator._ble_devices == {}
        assert _tv_unique_ids(hass, entry) == set()

        # Commands use the cloud, and the Bluetooth-only DreamView error never applies.
        assert await coordinator.async_control_device(DEVICE_ID, PowerCommand(power_on=True))
        api_client.control_device.assert_awaited_once()
        bleak_client.write_gatt_char.assert_not_awaited()

        assert await hass.config_entries.async_unload(entry.entry_id)

    assert [r for r in caplog.records if r.levelno >= logging.WARNING and "govee" in r.name] == []


async def test_h6199_staleness_unchanged(
    hass: HomeAssistant, transports: ExitStack, light_capabilities, mock_device_state
) -> None:
    """H6199 keeps advert-staleness gating and its plain control-characteristic write, option on or off."""
    entry, api_client, bleak_client = _boot(
        hass, transports, light_capabilities, mock_device_state, "H6199", {CONF_ENABLE_H6104_BLE: True}
    )
    coordinator = await _setup(hass, entry)

    ble = coordinator._ble_devices[DEVICE_ID]
    assert ble.supports_readback is False
    assert ble._report_callbacks == []
    assert _tv_unique_ids(hass, entry) == set()

    # Fresh advert: write goes to the plain control characteristic, as before #267.
    assert coordinator._ble_write_eligible(DEVICE_ID)
    assert await coordinator.async_control_device(DEVICE_ID, PowerCommand(power_on=True))
    bleak_client.write_gatt_char.assert_awaited_once_with(
        WRITE_CHARACTERISTIC_UUID, _build_power_frame(True), response=False
    )
    api_client.control_device.assert_not_awaited()
    assert ble.idle_disconnected is False
    assert not [t for t in entry._background_tasks if t.get_name().startswith("govee_h6104_readback")]

    # Stale advert: no H6104-style idle or readback exemption, the command falls through to the cloud.
    health = coordinator._transport.get(DEVICE_ID, "ble")
    health.last_success_ts = dt_util.utcnow() - timedelta(hours=1)
    coordinator._transport.refresh_ble_staleness([DEVICE_ID], {DEVICE_ID})
    assert health.last_failure_reason == "stale_advertisement"
    assert not coordinator._ble_write_eligible(DEVICE_ID)
    bleak_client.write_gatt_char.reset_mock()
    assert await coordinator.async_control_device(DEVICE_ID, PowerCommand(power_on=False))
    bleak_client.write_gatt_char.assert_not_awaited()
    api_client.control_device.assert_awaited_once()

    assert await hass.config_entries.async_unload(entry.entry_id)
