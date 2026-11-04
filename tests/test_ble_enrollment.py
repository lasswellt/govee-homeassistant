"""Tests for BLE enrolment from Home Assistant's advertisement cache.

Bluetooth proxies register their scanners after this integration sets up, so
the advertisement callbacks are refused at setup time. Without the cache
sweep a BLE-capable device stays cloud-only until a manual reload — which is
exactly what happened on real hardware before ``enroll_from_cache`` existed.

``homeassistant.components.bluetooth`` is not installed in the test
environment, so ``HAS_BLUETOOTH`` is False and the module never binds its
Bluetooth symbols. The helper below injects them.
"""

from __future__ import annotations

from contextlib import contextmanager
from unittest.mock import MagicMock, patch

import pytest

from custom_components.govee.ble_advertisement import (
    BleAdvertisementHandler,
    ble_address_from_device_id,
)
from custom_components.govee.const import CONF_ENABLE_H6104_BLE

_MODULE = "custom_components.govee.ble_advertisement"


class TestBleAddressFromDeviceId:
    """Cloud device IDs carry the BLE MAC with two extra leading octets."""

    def test_strips_the_two_leading_octets(self):
        assert ble_address_from_device_id("11:66:C0:EB:32:C1:19:FC") == "C0:EB:32:C1:19:FC"

    def test_uppercases_the_address(self):
        assert ble_address_from_device_id("11:66:c0:eb:32:c1:19:fc") == "C0:EB:32:C1:19:FC"

    def test_plain_mac_is_returned_unchanged(self):
        assert ble_address_from_device_id("C0:EB:32:C1:19:FC") == "C0:EB:32:C1:19:FC"

    def test_too_short_returns_none(self):
        assert ble_address_from_device_id("C0:EB:32") is None


def _coordinator(sku: str = "H1270", device_id: str = "11:66:C0:EB:32:C1:19:FC"):
    device = MagicMock()
    device.sku = sku
    device.is_group = False
    coord = MagicMock()
    coord._devices = {device_id: device}
    coord._ble_devices = {}
    coord.hass = MagicMock()
    return coord


@contextmanager
def _bluetooth(handler, *, found=None, side_effect=None):
    """Make the module behave as if HA's Bluetooth component were present."""
    bt = MagicMock()
    if side_effect is not None:
        bt.async_last_service_info.side_effect = side_effect
    else:
        bt.async_last_service_info.return_value = found
    with (
        patch(f"{_MODULE}.HAS_BLUETOOTH", True),
        patch(f"{_MODULE}.bt_component", bt, create=True),
        patch(f"{_MODULE}.BLE_COMMAND_SUPPORTED_MODELS", frozenset({"H1270"}), create=True),
        patch.object(handler, "handle_advertisement") as handle,
    ):
        yield bt, handle


class TestEnrollFromCache:
    """The sweep must enrol exactly the devices that are eligible."""

    def test_enrolls_a_supported_device_found_in_cache(self):
        handler = BleAdvertisementHandler(_coordinator())
        info = MagicMock()

        with _bluetooth(handler, found=info) as (bt, handle):
            handler.enroll_from_cache()

        handle.assert_called_once_with(info)
        # Commands can only ride a connectable scanner.
        assert bt.async_last_service_info.call_args.kwargs["connectable"] is True
        # And it must look up the BLE MAC, not the cloud device ID.
        assert bt.async_last_service_info.call_args[0][1] == "C0:EB:32:C1:19:FC"

    def test_skips_a_device_already_enrolled(self):
        coord = _coordinator()
        coord._ble_devices = {"11:66:C0:EB:32:C1:19:FC": MagicMock()}
        handler = BleAdvertisementHandler(coord)

        with _bluetooth(handler, found=MagicMock()) as (bt, handle):
            handler.enroll_from_cache()

        handle.assert_not_called()
        bt.async_last_service_info.assert_not_called()

    def test_skips_a_sku_not_on_the_allowlist(self):
        handler = BleAdvertisementHandler(_coordinator(sku="H9999"))

        with _bluetooth(handler, found=MagicMock()) as (bt, handle):
            handler.enroll_from_cache()

        handle.assert_not_called()
        bt.async_last_service_info.assert_not_called()

    def test_skips_group_devices(self):
        coord = _coordinator()
        next(iter(coord._devices.values())).is_group = True
        handler = BleAdvertisementHandler(coord)

        with _bluetooth(handler, found=MagicMock()) as (_bt, handle):
            handler.enroll_from_cache()

        handle.assert_not_called()

    def test_does_nothing_when_the_device_is_not_in_cache(self):
        handler = BleAdvertisementHandler(_coordinator())

        with _bluetooth(handler, found=None) as (_bt, handle):
            handler.enroll_from_cache()

        handle.assert_not_called()

    def test_a_cache_lookup_error_never_breaks_the_refresh(self):
        """This runs inside the coordinator poll, so it must not raise."""
        handler = BleAdvertisementHandler(_coordinator())

        with _bluetooth(handler, side_effect=RuntimeError("bluetooth is down")) as (
            _bt,
            handle,
        ):
            handler.enroll_from_cache()  # must not raise

        handle.assert_not_called()

    def test_is_a_noop_without_the_bluetooth_component(self):
        """Installs without HA Bluetooth must be unaffected."""
        handler = BleAdvertisementHandler(_coordinator())

        with patch(f"{_MODULE}.HAS_BLUETOOTH", False), patch.object(handler, "handle_advertisement") as handle:
            handler.enroll_from_cache()  # must not raise

        handle.assert_not_called()


class TestH6104OptIn:
    """Exercise the same gate for fresh and cached H6104 advertisements."""

    @pytest.mark.parametrize("enabled", [False, True])
    def test_cached_h6104_requires_opt_in(self, enabled):
        coord = _coordinator(sku="H6104")
        coord._config_entry.options = {CONF_ENABLE_H6104_BLE: enabled}
        handler = BleAdvertisementHandler(coord)
        info = MagicMock()
        with _bluetooth(handler, found=info) as (bt, handle):
            handler.enroll_from_cache()
        if enabled:
            handle.assert_called_once_with(info)
        else:
            bt.async_last_service_info.assert_not_called()
            handle.assert_not_called()

    @pytest.mark.parametrize("options", [{}, {CONF_ENABLE_H6104_BLE: False}, {CONF_ENABLE_H6104_BLE: True}])
    def test_fresh_h6104_requires_opt_in(self, options):
        coord = _coordinator(sku="H6104")
        coord._config_entry.options = options
        coord._states = {}
        coord._ble_ignored_skus_logged = set()
        info = MagicMock(name="service_info")
        info.name = "ihoment_H6104_B99E"
        info.address = "C0:EB:32:C1:19:FC"
        bt = MagicMock()
        bt.async_scanner_count.return_value = 1
        with (
            patch(f"{_MODULE}.bt_component", bt, create=True),
            patch(f"{_MODULE}.BLE_COMMAND_SUPPORTED_MODELS", frozenset({"H1270"}), create=True),
            patch(f"{_MODULE}.SEGMENTED_MODELS", frozenset(), create=True),
            patch(f"{_MODULE}.GoveeBLEDevice", create=True) as client,
        ):
            BleAdvertisementHandler(coord).handle_advertisement(info)
        if options.get(CONF_ENABLE_H6104_BLE):
            client.assert_called_once_with(info.device, segmented=False, legacy_uart=True)
            assert list(coord._ble_devices.values()) == [client.return_value]
        else:
            client.assert_not_called()
            assert coord._ble_devices == {}

    def test_opt_in_does_not_enable_another_unverified_model(self):
        coord = _coordinator(sku="H6072")
        coord._config_entry.options = {CONF_ENABLE_H6104_BLE: True}
        handler = BleAdvertisementHandler(coord)
        with _bluetooth(handler, found=MagicMock()) as (bt, handle):
            handler.enroll_from_cache()
        bt.async_last_service_info.assert_not_called()
        handle.assert_not_called()
