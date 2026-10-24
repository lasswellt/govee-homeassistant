"""The BLE advertisement handler must not wake entities on every advert.

Advertisements arrive unthrottled, often every second per device. Calling
``async_set_updated_data`` for each one re-armed the coordinator's poll timer
indefinitely and made every entity of every device write state per frame.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

import custom_components.govee.ble_advertisement as ble_mod
from custom_components.govee.ble_advertisement import BleAdvertisementHandler
from custom_components.govee.coordinator import GoveeCoordinator
from custom_components.govee.models import GoveeDeviceState
from custom_components.govee.transport_health import TransportHealthTracker

DEVICE_ID = "11:66:AA:BB:CC:DD:EE:FF"


@pytest.fixture
def coordinator(monkeypatch, mock_light_device):
    """A coordinator shell with one BLE-capable H6072 and mocked notifiers."""
    device = mock_light_device
    object.__setattr__(device, "device_id", DEVICE_ID)
    # These module globals only exist when Home Assistant's Bluetooth stack
    # imported cleanly, so set them regardless of the environment.
    # The enrolled device reports the address of the radio it was built from.
    monkeypatch.setattr(
        ble_mod,
        "GoveeBLEDevice",
        MagicMock(side_effect=lambda ble_device, **_kw: MagicMock(address=ble_device.address)),
        raising=False,
    )
    monkeypatch.setattr(ble_mod, "SEGMENTED_MODELS", frozenset(), raising=False)
    monkeypatch.setattr(ble_mod, "BLE_COMMAND_SUPPORTED_MODELS", frozenset({device.sku}), raising=False)
    bt = MagicMock()
    bt.async_scanner_count = MagicMock(return_value=1)
    monkeypatch.setattr(ble_mod, "bt_component", bt, raising=False)

    coord = object.__new__(GoveeCoordinator)
    coord.hass = MagicMock()
    coord._devices = {DEVICE_ID: device}
    coord._ble_devices = {}
    coord._transport = TransportHealthTracker()
    coord._transport.ensure(DEVICE_ID)
    coord._states = {DEVICE_ID: GoveeDeviceState.create_empty(DEVICE_ID)}
    coord._states[DEVICE_ID].online = True
    coord.data = coord._states
    coord._ble_ignored_skus_logged = set()
    coord._ble_handler = BleAdvertisementHandler(coord)
    coord.async_update_listeners = MagicMock()
    coord.async_set_updated_data = MagicMock()
    return coord


def _advert(sku: str = "H6072", address: str = "AA:BB:CC:DD:EE:FF") -> MagicMock:
    info = MagicMock()
    info.name = f"Govee_{sku}_754B"
    info.address = address
    info.device = MagicMock()
    info.device.address = address
    info.advertisement = MagicMock()
    return info


def test_first_enrolment_notifies_once_without_rescheduling_the_poll(coordinator, mock_light_device):
    coordinator._ble_handler.handle_advertisement(_advert(mock_light_device.sku))

    assert DEVICE_ID in coordinator._ble_devices
    coordinator.async_update_listeners.assert_called_once()
    coordinator.async_set_updated_data.assert_not_called()


def test_repeated_advertisement_is_silent(coordinator, mock_light_device):
    advert = _advert(mock_light_device.sku)
    coordinator._ble_handler.handle_advertisement(advert)
    coordinator.async_update_listeners.reset_mock()

    for _ in range(5):
        coordinator._ble_handler.handle_advertisement(advert)

    coordinator.async_update_listeners.assert_not_called()
    coordinator.async_set_updated_data.assert_not_called()
    # The transport stamp still advances silently.
    assert coordinator._transport.get(DEVICE_ID, "ble").last_success_ts is not None


def test_online_flip_notifies(coordinator, mock_light_device):
    advert = _advert(mock_light_device.sku)
    coordinator._ble_handler.handle_advertisement(advert)
    coordinator.async_update_listeners.reset_mock()

    coordinator._states[DEVICE_ID].online = False
    coordinator._ble_handler.handle_advertisement(advert)

    assert coordinator._states[DEVICE_ID].online is True
    coordinator.async_update_listeners.assert_called_once()


NEIGHBOUR = "11:22:33:44:55:66"


def test_same_model_advert_from_another_address_is_not_enrolled(coordinator, mock_light_device):
    """A neighbour's light of the same model must not be controlled as ours."""
    coordinator._ble_handler.handle_advertisement(_advert(mock_light_device.sku, address=NEIGHBOUR))

    assert DEVICE_ID not in coordinator._ble_devices
    coordinator.async_update_listeners.assert_not_called()


def test_mismatched_advert_is_logged_once_per_device(coordinator, mock_light_device, caplog):
    import logging

    caplog.set_level(logging.DEBUG, logger="custom_components.govee.ble_advertisement")
    for _ in range(3):
        coordinator._ble_handler.handle_advertisement(_advert(mock_light_device.sku, address=NEIGHBOUR))

    mismatches = [r for r in caplog.records if "expects" in r.getMessage()]
    assert len(mismatches) == 1
    message = mismatches[0].getMessage()
    assert mock_light_device.sku in message and NEIGHBOUR in message and "AA:BB:CC:DD:EE:FF" in message


def test_advert_address_is_compared_case_insensitively(coordinator, mock_light_device):
    coordinator._ble_handler.handle_advertisement(_advert(mock_light_device.sku, address="aa:bb:cc:dd:ee:ff"))

    assert DEVICE_ID in coordinator._ble_devices


def test_enrolled_device_is_not_repointed_by_another_address(coordinator, mock_light_device):
    coordinator._ble_handler.handle_advertisement(_advert(mock_light_device.sku))
    enrolled = coordinator._ble_devices[DEVICE_ID]

    coordinator._ble_handler.handle_advertisement(_advert(mock_light_device.sku, address=NEIGHBOUR))

    assert coordinator._ble_devices[DEVICE_ID] is enrolled
    enrolled.set_ble_device_and_advertisement_data.assert_not_called()


def test_id_without_a_mac_still_enrolls_on_sku_but_is_never_repointed(coordinator, mock_light_device):
    """IDs that carry no MAC keep the SKU match, pinned to the first radio seen."""
    device = coordinator._devices.pop(DEVICE_ID)
    coordinator._devices["AB:CD"] = device
    coordinator._states["AB:CD"] = coordinator._states.pop(DEVICE_ID)
    coordinator._transport.ensure("AB:CD")

    coordinator._ble_handler.handle_advertisement(_advert(mock_light_device.sku))
    enrolled = coordinator._ble_devices["AB:CD"]
    coordinator._ble_handler.handle_advertisement(_advert(mock_light_device.sku))
    coordinator._ble_handler.handle_advertisement(_advert(mock_light_device.sku, address=NEIGHBOUR))

    assert enrolled.address == "AA:BB:CC:DD:EE:FF"
    enrolled.set_ble_device_and_advertisement_data.assert_called_once()
