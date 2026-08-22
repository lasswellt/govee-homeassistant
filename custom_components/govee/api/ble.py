"""Direct BLE client for Govee BLE lights.

Implements the Govee BLE wire protocol for local control of Govee BLE-only
light devices (Govee_*, ihoment_*, GBK_* advertising name prefixes), bypassing
the cloud REST API. This is the foundation module for the hacs-govee BLE
direct-control feature.

Protocol reference
------------------
The wire protocol is validated against two independent sources:

* ``Beshelmek/govee_ble_lights`` — the de-facto reference HACS component with
  76 SKU profiles (GitHub 122 stars). This module matches Beshelmek's frame
  layout, XOR checksum algorithm, characteristic UUID, and command byte values
  exactly. Notably it sends brightness as ``0-255`` unchanged for all SKUs
  (including segmented models) and ends the segmented-color frame with the
  ``0xFF 0x7F`` tail — both differ from PR #52's original ``api/ble_direct.py``.

* The existing ``api/ble_packet.py`` module in this codebase, which already
  builds the same 20-byte framing + XOR checksum shape for cloud MQTT
  passthrough commands (Music Mode, DreamView, DIY scene). This module reuses
  ``build_packet()`` from there to avoid duplicating the packet primitive.

Characteristic UUIDs
--------------------
Both primary Govee BLE light variants (segmented and single-zone) use the same
vendor-specific characteristic for writes:

* Write: ``00010203-0405-0607-0809-0a0b0c0d2b11``
* Read (notifications): ``00010203-0405-0607-0809-0a0b0c0d2b10``

HA integration pattern
----------------------
``GoveeBLEDevice`` is structured as a self-contained device library so the HA
wiring code mirrors ``led_ble`` — the canonical HA core BLE light integration.
The class exposes the same API surface as ``led-ble``'s ``LEDBLE`` so that if
we later extract it to a ``govee-ble-lights`` PyPI package for an upstream HA
core contribution the refactor is mechanical.

See ``docs/_research/2026-04-08_ble-direct-support.md`` and
``docs/_research/2026-04-08_ha-ble-integration-patterns.md`` for full context.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Callable
from dataclasses import dataclass
from enum import IntEnum
from typing import TYPE_CHECKING

from bleak import BleakClient
from bleak_retry_connector import (  # type: ignore[attr-defined]
    BleakClientWithServiceCache,
    BleakError,
    close_stale_connections_by_address,
    establish_connection,
)

from .ble_crypto import (
    GoveeBLESession,
    async_establish_session,
    async_supports_encryption,
)
from .ble_packet import build_packet

if TYPE_CHECKING:
    from bleak.backends.device import BLEDevice
    from bleak.backends.scanner import AdvertisementData

_LOGGER = logging.getLogger(__name__)

# GATT characteristic UUIDs (same as Beshelmek/govee_ble_lights)
WRITE_CHARACTERISTIC_UUID = "00010203-0405-0607-0809-0a0b0c0d2b11"
READ_CHARACTERISTIC_UUID = "00010203-0405-0607-0809-0a0b0c0d2b10"

# Advertising name prefixes used for Bluetooth discovery (manifest.json matchers)
BLE_DISCOVERY_NAMES: tuple[str, ...] = ("Govee_", "ihoment_", "GBK_")

# SKUs that use the segmented color encoding. Lifted directly from
# Beshelmek/govee_ble_lights `SEGMENTED_MODELS` (light.py:35).
#
# H1270 is not in that reference list but requires the segmented encoding:
# field testing showed it silently discards the single-zone frame (the colour
# read-back stays black) and only acts on mode 0x15.
SEGMENTED_MODELS: frozenset[str] = frozenset(
    {"H6053", "H6072", "H6102", "H6199", "H1270"}
)

# SKUs with verified BLE command support. BLE command dispatch is only
# attempted for devices on this list — advertising over BLE is not
# sufficient proof that the device will accept our command frames.
# Issue #59: multiple RGBIC SKUs (H6072, H61E1, H60B0, H612A) advertise
# BLE but silently drop our writes, leaving commands unacknowledged and
# the UI flipflopping. Until we can programmatically probe a device for
# real BLE command capability, this allowlist is the correctness floor.
#
# How to extend: confirm via field testing that BLE commands (power,
# brightness, RGB) actually change device state, then add the SKU here
# and note the test result in the PR.
BLE_COMMAND_SUPPORTED_MODELS: frozenset[str] = frozenset(
    {
        "H6199",  # RGBIC — confirmed working per issue #59 (at-9 report, ~10s lag).
        # H1270 accepts commands only over the encrypted protocol; plaintext
        # frames are dropped without any error, which is why it read as
        # "advertises BLE but ignores writes" before ble_crypto existed.
        # Field-tested on hardware: power, brightness and colour all confirmed.
        "H1270",
    }
)

# Packet head byte used for all command frames.
_PACKET_HEAD_COMMAND = 0x33

# Connection management
_MAX_CONNECT_ATTEMPTS = 4


class LedPacketCmd(IntEnum):
    """Command byte values (Beshelmek reference, light.py:37-41)."""

    POWER = 0x01
    BRIGHTNESS = 0x04
    COLOR = 0x05


class LedColorMode(IntEnum):
    """Color-encoding mode byte for the COLOR command (Beshelmek, light.py:44-53).

    Notable omission from PR #52: ``LEGACY = 0x0D`` is absent here because no
    primary source (Beshelmek, Govee community captures, wez/govee2mqtt)
    references it. PR #52's speculative addition of a ``LEGACY`` duplicate
    frame has been dropped — if a specific old SKU turns out to need it, add
    it back with an attached device capture as evidence.
    """

    SINGLE = 0x02  # Beshelmek calls this "MANUAL"
    SCENES = 0x05
    MICROPHONE = 0x06
    SEGMENTS = 0x15


@dataclass
class GoveeBLEState:
    """Snapshot of the last-known state of a Govee BLE light.

    Govee BLE lights do not reliably push state updates over GATT notifications,
    so this snapshot is maintained optimistically from the commands we send.
    The coordinator's ``update()`` call is effectively a keep-alive — it does
    not round-trip state over GATT (see module docstring).
    """

    power: bool | None = None
    brightness: int | None = None  # 0-255 (HA native range, no rescale)
    rgb: tuple[int, int, int] | None = None


def _build_frame(cmd: LedPacketCmd, payload: list[int]) -> bytes:
    """Build a 20-byte command frame with XOR checksum.

    Delegates to ``build_packet`` from ``ble_packet.py`` which already handles
    the padding-to-19-bytes and XOR-checksum-in-byte-19 shape used by every
    Govee BLE command (and by the cloud MQTT passthrough commands).

    Args:
        cmd: The command byte (POWER/BRIGHTNESS/COLOR).
        payload: Command-specific payload bytes (0-17 bytes).

    Returns:
        A 20-byte frame ready to write to the control characteristic.
    """
    return build_packet([_PACKET_HEAD_COMMAND, cmd, *payload])


def _build_power_frame(on: bool) -> bytes:
    """Build a power on/off command frame."""
    return _build_frame(LedPacketCmd.POWER, [0x01 if on else 0x00])


def _build_brightness_frame(brightness: int) -> bytes:
    """Build a brightness command frame.

    Brightness is sent as ``0-255`` unchanged for all SKUs, matching
    Beshelmek's reference behavior. PR #52's rescale-to-0-100 for segmented
    devices was speculative and has been removed.
    """
    value = max(0, min(255, int(brightness)))
    return _build_frame(LedPacketCmd.BRIGHTNESS, [value])


def _build_rgb_single_frame(r: int, g: int, b: int) -> bytes:
    """Build an RGB color command frame for non-segmented devices."""
    return _build_frame(LedPacketCmd.COLOR, [LedColorMode.SINGLE, r, g, b])


def _build_rgb_segmented_frame(r: int, g: int, b: int) -> bytes:
    """Build an RGB color command frame for segmented RGBIC devices.

    Payload format matches Beshelmek exactly (light.py:271-273) including the
    ``0xFF 0x7F`` tail — NOT PR #52's ``0xFF 0xFF`` which had no primary source.
    """
    return _build_frame(
        LedPacketCmd.COLOR,
        [
            LedColorMode.SEGMENTS,
            0x01,
            r,
            g,
            b,
            0x00,
            0x00,
            0x00,
            0x00,
            0x00,
            0xFF,
            0x7F,
        ],
    )


class GoveeBLEDevice:
    """Owns the ``BleakClient`` for a single Govee BLE light.

    Lifecycle:
        * Instantiate with a ``BLEDevice`` from
          ``bluetooth.async_ble_device_from_address``.
        * Call ``set_ble_device_and_advertisement_data`` from a PASSIVE HA
          Bluetooth callback so the library always has a fresh ``BLEDevice``
          reference (important for ESPHome proxy handoff).
        * Call command methods (``turn_on``, ``set_rgb``, etc.) to send writes.
        * Call ``update()`` from the coordinator poll — currently a no-op
          because Govee lights don't reliably respond to state-request packets,
          but present for API compatibility with the led_ble device-library
          shape.
        * Call ``stop()`` in ``async_unload_entry`` to cleanly disconnect.

    Thread model:
        All command methods serialize via ``self._lock`` so concurrent HA
        service calls cannot interleave writes on the same device. The HA
        entity can safely set ``PARALLEL_UPDATES = 0`` because this class
        handles its own per-device serialization.
    """

    def __init__(
        self,
        ble_device: BLEDevice,
        refresh_ble_device: Callable[[], BLEDevice | None] | None = None,
        segmented: bool = False,
    ) -> None:
        """Initialize the device wrapper.

        Args:
            ble_device: The initial ``BLEDevice`` obtained from
                ``bluetooth.async_ble_device_from_address``.
            refresh_ble_device: Callable returning the current ``BLEDevice``
                for this address. Wired to ``async_ble_device_from_address``
                so retries pick up fresh references during proxy handoff.
                Optional for tests.
            segmented: True if the device uses the segmented color encoding
                (H6053, H6072, H6102, H6199-class RGBIC strips).
        """
        self._ble_device = ble_device
        self._refresh_ble_device = refresh_ble_device
        self._segmented = segmented
        self._client: BleakClient | None = None
        # Set only for devices speaking the encrypted protocol, and only for
        # as long as the connection that negotiated it lives.
        self._session: GoveeBLESession | None = None
        self._state = GoveeBLEState()
        self._lock = asyncio.Lock()
        self._callbacks: list[Callable[[GoveeBLEState], None]] = []

    # ------------------------------------------------------------------ #
    # Properties
    # ------------------------------------------------------------------ #

    @property
    def address(self) -> str:
        """Bluetooth MAC address (canonical form with colons)."""
        return self._ble_device.address

    @property
    def name(self) -> str:
        """Advertising name or a sensible default."""
        return self._ble_device.name or "Govee BLE Light"

    @property
    def state(self) -> GoveeBLEState:
        """Last-known optimistic state."""
        return self._state

    @property
    def segmented(self) -> bool:
        """Whether the device uses segmented color encoding."""
        return self._segmented

    # ------------------------------------------------------------------ #
    # Push update plumbing
    # ------------------------------------------------------------------ #

    def set_ble_device_and_advertisement_data(
        self,
        ble_device: BLEDevice,
        advertisement: AdvertisementData,  # noqa: ARG002 — kept for led_ble API parity
    ) -> None:
        """Refresh the cached ``BLEDevice`` reference from a new advertisement.

        Called from a PASSIVE ``bluetooth.async_register_callback`` in the HA
        integration's ``async_setup_entry``. Keeping this reference fresh is
        how the device library picks up proxy/adapter changes — for example
        when an ESPHome Bluetooth proxy takes over from the local adapter.
        """
        self._ble_device = ble_device

    def register_callback(
        self, callback: Callable[[GoveeBLEState], None]
    ) -> Callable[[], None]:
        """Register a state-change callback.

        Returns an unsubscribe callable (idempotent). The HA entity subscribes
        in ``async_added_to_hass`` so it receives push updates in addition to
        the coordinator's polling cycle (the led_ble dual-path pattern).
        """
        self._callbacks.append(callback)

        def _unsubscribe() -> None:
            if callback in self._callbacks:
                self._callbacks.remove(callback)

        return _unsubscribe

    def _emit(self) -> None:
        """Fire all registered state-change callbacks."""
        for cb in self._callbacks:
            try:
                cb(self._state)
            except Exception:  # pragma: no cover — defensive
                _LOGGER.exception("Error in GoveeBLEDevice state callback")

    # ------------------------------------------------------------------ #
    # Connection management
    # ------------------------------------------------------------------ #

    def _on_disconnected(self, _client: BleakClient) -> None:
        """Callback fired by bleak when the GATT link drops."""
        self._client = None
        # Frame counters restart at 1 on the next connection, so reusing this
        # session would encrypt with nonces the device has already seen.
        self._session = None

    async def _ensure_connected(self) -> BleakClient:
        """Open or return the cached ``BleakClient`` for this device.

        Critical defensive calls:

        1. ``close_stale_connections_by_address`` — frees any dangling GATT
           handle that a crashed HA process left behind on BlueZ. Without this,
           ``establish_connection`` can fail until the next reboot. This is
           the single highest-impact pattern learned from the yalexs_ble /
           switchbot integrations.
        2. ``ble_device_callback`` — called by bleak_retry_connector on every
           retry attempt. Wired to the caller-provided refresh callable so
           proxy handoff Just Works during retry storms.
        """
        if self._client is not None and self._client.is_connected:
            return self._client

        await close_stale_connections_by_address(self.address)

        def _ble_device_callback() -> BLEDevice:
            if self._refresh_ble_device is not None:
                fresh = self._refresh_ble_device()
                if fresh is not None:
                    self._ble_device = fresh
            return self._ble_device

        self._client = await establish_connection(
            BleakClientWithServiceCache,
            device=self._ble_device,
            name=self.name,
            disconnected_callback=self._on_disconnected,
            ble_device_callback=_ble_device_callback,
            max_attempts=_MAX_CONNECT_ATTEMPTS,
            use_services_cache=True,
        )

        try:
            if await async_supports_encryption(self._client):
                self._session = await async_establish_session(
                    self._client, WRITE_CHARACTERISTIC_UUID, READ_CHARACTERISTIC_UUID
                )
                _LOGGER.debug("Negotiated encrypted BLE session with %s", self.address)
        except Exception:
            # Drop the link rather than caching a connected-but-unnegotiated
            # client. Keeping it would send the next command as plaintext,
            # which an encrypted device discards without raising — so the
            # caller would record a success and skip the cloud fallback. A
            # dropped link just fails this one command and renegotiates next
            # time.
            client, self._client, self._session = self._client, None, None
            with contextlib.suppress(Exception):
                await client.disconnect()
            raise

        return self._client

    async def _write(self, frame: bytes) -> None:
        """Send a single 20-byte frame to the control characteristic.

        Devices on the encrypted protocol take the identical frame wrapped in
        the session cipher, so every command encoding above is shared.
        """
        async with self._lock:
            client = await self._ensure_connected()
            if self._session is not None:
                frame = self._session.wrap(frame)
            await client.write_gatt_char(
                WRITE_CHARACTERISTIC_UUID, frame, response=False
            )

    # ------------------------------------------------------------------ #
    # High-level command API
    # ------------------------------------------------------------------ #

    async def turn_on(self) -> None:
        """Power the device on."""
        await self._write(_build_power_frame(True))
        self._state.power = True
        self._emit()

    async def turn_off(self) -> None:
        """Power the device off."""
        await self._write(_build_power_frame(False))
        self._state.power = False
        self._emit()

    async def set_brightness(self, brightness: int) -> None:
        """Set brightness (0-255, HA native range).

        Sends the value unchanged — no rescale to 0-100 for segmented models.
        This matches Beshelmek's reference which uses 0-255 for every SKU it
        supports.
        """
        value = max(0, min(255, int(brightness)))
        await self._write(_build_brightness_frame(value))
        self._state.brightness = value
        self._emit()

    async def set_rgb(self, red: int, green: int, blue: int) -> None:
        """Set RGB color.

        Picks the segmented or single-zone encoding based on
        ``self._segmented``, which is set at setup time from the user's
        "segmented mode" config flow choice (defaulted per-SKU from the
        Beshelmek whitelist).
        """
        r, g, b = (max(0, min(255, int(v))) for v in (red, green, blue))
        if self._segmented:
            frame = _build_rgb_segmented_frame(r, g, b)
        else:
            frame = _build_rgb_single_frame(r, g, b)
        await self._write(frame)
        self._state.rgb = (r, g, b)
        self._emit()

    async def update(self) -> GoveeBLEState:
        """Poll hook for the coordinator.

        Govee BLE lights do not reliably respond to state-request packets (the
        Beshelmek reference never polls for state), so this method currently
        returns the cached optimistic state without a GATT round-trip. The
        coordinator polling cycle exists to surface connection failures
        through ``UpdateFailed`` / entity availability rather than to refresh
        state.

        Present for API parity with the ``led-ble`` device-library shape so
        future state-polling work is a drop-in replacement.
        """
        return self._state

    async def stop(self) -> None:
        """Cleanly disconnect the ``BleakClient`` (idempotent)."""
        async with self._lock:
            if self._client is not None and self._client.is_connected:
                try:
                    await self._client.disconnect()
                except BleakError:
                    _LOGGER.debug(
                        "Error disconnecting Govee BLE client %s (ignored)",
                        self.address,
                    )
            self._client = None
            self._session = None
