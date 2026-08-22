"""BLE advertisement subscription + correlation handler.

Extracted from coordinator.py for cohesion (audit H1). Holds the BLE
subscription bookkeeping and the correlation logic that maps an
incoming advertisement to a known cloud device, then enrolls the device
for BLE command dispatch when the SKU is on the verified allowlist.

The handler holds a reference to the coordinator and operates on its
mutable state (``_devices``, ``_ble_devices``, ``_states``, transport
tracker). This is code organization, not coupling reduction —
extracting LOC from the 1697-line monolith without redesigning the
data flow.
"""

from __future__ import annotations

import dataclasses
import logging
from typing import TYPE_CHECKING, Any

try:
    from homeassistant.components import bluetooth
    from homeassistant.components import bluetooth as bt_component
    from homeassistant.components.bluetooth import (
        BluetoothCallbackMatcher,
        BluetoothScanningMode,
    )
    from .api.ble import BLE_COMMAND_SUPPORTED_MODELS, GoveeBLEDevice, SEGMENTED_MODELS

    HAS_BLUETOOTH = True
except ImportError:  # pragma: no cover — HA installs without Bluetooth
    HAS_BLUETOOTH = False

from homeassistant.core import callback

from .const import GOVEE_BLE_MANUFACTURER_IDS

if TYPE_CHECKING:
    from .coordinator import GoveeCoordinator

_LOGGER = logging.getLogger(__name__)

# BLE advertising name prefixes used by Govee devices.
_BLE_NAME_PREFIXES = ("Govee_*", "ihoment_*", "GBK_*")


def sku_from_ble_name(name: str | None) -> str | None:
    """Extract SKU from a BLE advertising name like ``Govee_H6072_754B``.

    Govee BLE lights advertise with names following the pattern
    ``<Prefix>_<SKU>_<Suffix>`` where the SKU starts with ``H`` followed
    by 3+ alphanumeric characters. Returns ``None`` if no SKU can be
    parsed.
    """
    if not name:
        return None
    for part in name.split("_"):
        if part.startswith("H") and len(part) >= 4 and part[1:].isalnum():
            return part
    return None


def ble_address_from_device_id(device_id: str) -> str | None:
    """Derive the BLE MAC from a cloud device ID.

    Cloud IDs carry the BLE MAC with two extra leading octets, e.g.
    ``11:66:C0:EB:32:C1:19:FC`` for MAC ``C0:EB:32:C1:19:FC``.
    """
    parts = device_id.split(":")
    if len(parts) < 6:
        return None
    return ":".join(parts[-6:]).upper()


class BleAdvertisementHandler:
    """Subscribe to and correlate Govee BLE advertisements.

    Lifecycle:
        handler = BleAdvertisementHandler(coordinator)
        for unsub in handler.setup_subscriptions():
            entry.async_on_unload(unsub)
    """

    def __init__(self, coordinator: GoveeCoordinator) -> None:
        self._coord = coordinator

    def setup_subscriptions(self) -> list[Any]:
        """Register name-prefix and manufacturer-ID callbacks.

        No-op when HAS_BLUETOOTH is False. Returns the list of
        unsubscribe callables.
        """
        if not HAS_BLUETOOTH:
            return []

        unsubs: list[Any] = []

        @callback
        def _on_ble_advertisement(service_info: Any, change: Any) -> None:
            self.handle_advertisement(service_info)

        for prefix in _BLE_NAME_PREFIXES:
            unsubs.append(
                bluetooth.async_register_callback(
                    self._coord.hass,
                    _on_ble_advertisement,
                    BluetoothCallbackMatcher(local_name=prefix, connectable=True),
                    BluetoothScanningMode.ACTIVE,
                )
            )

        # Some Govee SKUs (H6053 / H6076 / H6126, issue #58) don't deliver
        # reliably via name-prefix matchers in all HA Bluetooth setups.
        # Manufacturer-ID callback is a complementary path.
        for mfg_id in GOVEE_BLE_MANUFACTURER_IDS:
            unsubs.append(
                bluetooth.async_register_callback(
                    self._coord.hass,
                    _on_ble_advertisement,
                    BluetoothCallbackMatcher(manufacturer_id=mfg_id, connectable=True),
                    BluetoothScanningMode.ACTIVE,
                )
            )

        _LOGGER.debug(
            "BLE advertisement subscription active for names=%s manufacturer_ids=%s",
            _BLE_NAME_PREFIXES,
            GOVEE_BLE_MANUFACTURER_IDS,
        )
        return unsubs

    @callback
    def enroll_from_cache(self) -> None:
        """Enrol eligible devices from Home Assistant's advertisement cache.

        The advertisement callbacks only deliver while a connectable scanner
        is running. Bluetooth proxies are themselves integrations, and they
        usually register their scanners after this one has set up, so at setup
        time the scanner count is zero and every advertisement is refused. The
        device then stays cloud-only until somebody reloads the entry by hand.

        Reading the cache on each refresh closes that gap without depending on
        a fresh advertisement arriving at the right moment.
        """
        if not HAS_BLUETOOTH:
            return

        coord = self._coord
        for device_id, device in list(coord._devices.items()):
            if device_id in coord._ble_devices or device.is_group:
                continue
            if device.sku not in BLE_COMMAND_SUPPORTED_MODELS:
                continue
            address = ble_address_from_device_id(device_id)
            if address is None:
                continue
            try:
                info = bt_component.async_last_service_info(
                    coord.hass, address, connectable=True
                )
            except Exception as err:  # noqa: BLE001 — runs inside the poll
                # Never let a Bluetooth hiccup fail the whole state refresh.
                _LOGGER.debug("BLE cache lookup failed for %s: %s", address, err)
                continue
            if info is not None:
                _LOGGER.debug(
                    "Enrolling %s (%s) from the BLE advertisement cache",
                    device_id,
                    address,
                )
                self.handle_advertisement(info)

    @callback
    def handle_advertisement(self, service_info: Any) -> None:
        """Correlate one BLE advertisement with a known cloud device.

        Matching strategy (see
        ``docs/_research/2026-04-09_multi-transport-single-entity.md``):
          1. Extract SKU from the advertising name.
          2. Find cloud devices with that SKU (ignoring group devices).
          3. If exactly one match → unambiguous correlation.
          4. If multiple same-SKU → MAC-prefix tiebreaker.
          5. If no match or ambiguous → skip.
        """
        coord = self._coord
        from .models.state import GoveeDeviceState  # noqa — avoid module cycle

        ble_sku = sku_from_ble_name(service_info.name)
        if not ble_sku:
            return

        candidates = [
            (did, dev)
            for did, dev in coord._devices.items()
            if dev.sku == ble_sku and not dev.is_group
        ]

        matched_id: str | None = None
        if len(candidates) == 1:
            matched_id = candidates[0][0]
        elif len(candidates) > 1:
            ble_mac = service_info.address.upper()
            for did, _dev in candidates:
                if did.upper().startswith(ble_mac):
                    matched_id = did
                    break

        if matched_id is None:
            return

        # BLE advertisement visibility != BLE command capability (issue #59).
        # Only enroll SKUs whose BLE command set is verified.
        if ble_sku not in BLE_COMMAND_SUPPORTED_MODELS:
            if ble_sku not in coord._ble_ignored_skus_logged:
                coord._ble_ignored_skus_logged.add(ble_sku)
                _LOGGER.info(
                    "%s (SKU=%s) is advertising BLE but is not on the BLE "
                    "command allowlist. Staying cloud-only. If BLE commands "
                    "are known to work for this SKU, please open a GitHub "
                    "issue referencing #59 so it can be added.",
                    coord._devices[matched_id].name,
                    ble_sku,
                )
            return

        # Don't enroll BLE without a connectable adapter (issue #59 follow-up).
        if matched_id not in coord._ble_devices:
            try:
                if bt_component.async_scanner_count(coord.hass, connectable=True) == 0:
                    if ble_sku not in coord._ble_ignored_skus_logged:
                        coord._ble_ignored_skus_logged.add(ble_sku)
                        _LOGGER.info(
                            "%s (SKU=%s) advertising BLE but no connectable "
                            "adapter is available — staying cloud-only. Reload "
                            "the integration after attaching a Bluetooth adapter.",
                            coord._devices[matched_id].name,
                            ble_sku,
                        )
                    return
            except Exception as err:  # pragma: no cover — defensive
                _LOGGER.debug("BLE adapter probe failed: %s", err)

            coord._ble_devices[matched_id] = GoveeBLEDevice(
                service_info.device,
                segmented=ble_sku in SEGMENTED_MODELS,
            )
            _LOGGER.info(
                "BLE transport available for %s (SKU=%s, BLE=%s)",
                coord._devices[matched_id].name,
                ble_sku,
                service_info.address,
            )
        else:
            coord._ble_devices[matched_id].set_ble_device_and_advertisement_data(
                service_info.device,
                service_info.advertisement,
            )

        # Stamp last-seen for the BLE connectivity sensor and notify entities.
        coord._record_transport_success(matched_id, "ble")

        # BLE advertisement is direct proof of life — flip ``online`` back True
        # if a stale ``online: false`` from the cloud is masking a recovered
        # device (issue #68). Use dataclasses.replace so listeners detect the
        # change (audit H2).
        existing_state = coord._states.get(matched_id)
        if existing_state is not None and not existing_state.online:
            _LOGGER.info(
                "BLE advertisement restored online status for %s (was offline per cloud)",
                coord._devices[matched_id].name,
            )
            coord._states[matched_id] = dataclasses.replace(existing_state, online=True)

        # Guard for tests that instantiate the coordinator via object.__new__().
        try:
            if coord.data is not None:
                coord.async_set_updated_data(coord._states)
        except AttributeError:
            pass
