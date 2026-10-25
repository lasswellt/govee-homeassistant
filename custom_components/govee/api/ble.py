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
from collections import deque
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
from .ble_status import H6104Report, decode_h6104_status

if TYPE_CHECKING:
    from bleak.backends.device import BLEDevice
    from bleak.backends.scanner import AdvertisementData

_LOGGER = logging.getLogger(__name__)

# GATT characteristic UUIDs (same as Beshelmek/govee_ble_lights)
WRITE_CHARACTERISTIC_UUID = "00010203-0405-0607-0809-0a0b0c0d2b11"
READ_CHARACTERISTIC_UUID = "00010203-0405-0607-0809-0a0b0c0d2b10"
# App-derived H6104/H6101 UART endpoint, also present in the H6104 field GATT dump.
# https://github.com/Brady-Woods/govee-ble-local/blob/535104e8e107b35757e6f90d8bbb2cadf599985c/spec/docs/GOVEE_BLE_GATT_PROTOCOL.md#23-alternate-uart-service-a-few-devices
LEGACY_UART_CHARACTERISTIC_UUID = "0000ffe1-0000-1000-8000-00805f9b34fb"

# Advertising name prefixes used for Bluetooth discovery (manifest.json matchers)
BLE_DISCOVERY_NAMES: tuple[str, ...] = ("Govee_", "ihoment_", "GBK_")

# SKUs that use the segmented color encoding. Lifted directly from
# Beshelmek/govee_ble_lights `SEGMENTED_MODELS` (light.py:35).
#
# H1270 is not in that reference list but requires the segmented encoding:
# field testing showed it silently discards the single-zone frame (the colour
# read-back stays black) and only acts on mode 0x15.
SEGMENTED_MODELS: frozenset[str] = frozenset({"H6053", "H6072", "H6102", "H6199", "H1270"})

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
_LEGACY_IDLE_DISCONNECT_SECONDS = 5.0
_STATUS_REPLY_TIMEOUT = 0.5


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

    This compatibility snapshot is maintained optimistically from commands.
    H6104 device-reported values travel separately through H6104Report callbacks
    and async_read_status(), so a cached desired value cannot masquerade as a
    received status report. Modern models retain the existing optimistic path.
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
        legacy_uart: bool = False,
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
            legacy_uart: Use H6104's plaintext FFE1 write/notify endpoint.
        """
        self._ble_device = ble_device
        self._refresh_ble_device = refresh_ble_device
        self._segmented = segmented
        self._legacy_uart = legacy_uart
        self._write_uuid = LEGACY_UART_CHARACTERISTIC_UUID if legacy_uart else WRITE_CHARACTERISTIC_UUID
        self._client: BleakClient | None = None
        # Set only for devices speaking the encrypted protocol, and only for
        # as long as the connection that negotiated it lives.
        self._session: GoveeBLESession | None = None
        self._state = GoveeBLEState()
        self._lock = asyncio.Lock()
        self._idle_disconnect_handle: asyncio.TimerHandle | None = None
        self._connection_generation = 0
        self._idle_disconnected = False
        self._report_callbacks: list[Callable[[H6104Report], None]] = []
        self._pending_status: tuple[bytes, asyncio.Future[H6104Report]] | None = None
        self._power_read_validated = False
        self._status_frames: deque[str] = deque(maxlen=12)
        self._status_candidates: deque[str] = deque(maxlen=12)
        self._status_results: dict[str, str] = {}
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
    def is_connected(self) -> bool:
        """Whether the current GATT connection is still live."""
        return self._client is not None and bool(self._client.is_connected)

    @property
    def idle_disconnected(self) -> bool:
        """The last link was deliberately released, rather than lost or failed."""
        return self._idle_disconnected

    @property
    def supports_readback(self) -> bool:
        return self._legacy_uart

    @property
    def readback_diagnostics(self) -> dict[str, object]:
        """Known status frames only; never device-info or pairing payloads."""
        return {
            "power_read_validated": self._power_read_validated,
            "last_queries": dict(self._status_results),
            "recent_status_frames": list(self._status_frames),
            "recent_status_candidates": list(self._status_candidates),
        }

    def register_report_callback(self, callback: Callable[[H6104Report], None]) -> None:
        """Receive device reports separately from optimistic state callbacks."""
        self._report_callbacks.append(callback)

    def _emit_report(self, report: H6104Report) -> None:
        for callback in self._report_callbacks:
            try:
                callback(report)
            except Exception:
                _LOGGER.exception("Error applying H6104 status report")

    def _on_uart_notification(self, client: BleakClient, data: bytearray) -> None:
        """Ignore late connections, echoes, bad checksums and unrelated frames."""
        if client is not self._client:
            return
        frame = bytes(data)
        if len(frame) != 20:
            return
        if (frame[0] == 0xAA and frame[1] in (1, 4, 5)) or frame[:2] == bytes([0xEE, 0x20]):
            # Keep bounded evidence even for echoes/unknown layouts, while
            # excluding device-info, Wi-Fi/pairing and arbitrary notifications.
            # A candidate is diagnostic evidence, not decoded/accepted state.
            self._status_candidates.append(frame.hex())
        pending = self._pending_status
        if frame[0] == 0xAA:
            if pending is None or pending[1].done() or frame[1] != pending[0][1]:
                return
            query = pending[0]
        elif frame[:2] == bytes([0xEE, 0x20]):
            query = None
        else:
            return
        report = decode_h6104_status(frame, query=query, power_read_validated=self._power_read_validated)
        if report is None:
            return
        self._status_frames.append(frame.hex())
        if report.power is True:
            self._power_read_validated = True
        if frame[0] == 0xAA and pending is not None:
            pending[1].set_result(report)
        else:
            self._emit_report(report)

    async def async_read_status(self) -> list[H6104Report]:
        """Read power, raw brightness and mode without changing the light.

        A timeout is missing data, never a negative state or a failed control
        command. Each reply is bounded; unsupported reads do not hold a slot.
        """
        if not self._legacy_uart:
            return []
        reports: list[H6104Report] = []
        async with self._lock:
            self._cancel_idle_disconnect()
            self._idle_disconnected = False
            try:
                client = await self._ensure_connected()
                for command, payload in ((1, []), (4, []), (5, [1])):
                    query = build_packet([0xAA, command, *payload])
                    future: asyncio.Future[H6104Report] = asyncio.get_running_loop().create_future()
                    self._pending_status = (query, future)
                    try:
                        await client.write_gatt_char(self._write_uuid, query, response=False)
                        report = await asyncio.wait_for(future, _STATUS_REPLY_TIMEOUT)
                        reports.append(report)
                        self._status_results[f"aa{command:02x}"] = "reply"
                        self._emit_report(report)
                    except TimeoutError:
                        self._status_results[f"aa{command:02x}"] = "timeout_or_echo"
                    finally:
                        if not future.done():
                            future.cancel()
                        elif not future.cancelled():
                            future.exception()  # retrieve if disconnect and write both failed
                        self._pending_status = None
            finally:
                self._schedule_idle_disconnect()
        return reports

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

    def register_callback(self, callback: Callable[[GoveeBLEState], None]) -> Callable[[], None]:
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

    def _on_disconnected(self, client: BleakClient) -> None:
        """Callback fired by bleak when the GATT link drops.

        A late callback from a superseded client must not wipe the connection
        that replaced it, so only the current client's drop is honoured.
        """
        if self._client is not None and client is not self._client:
            return
        self._cancel_idle_disconnect()
        self._idle_disconnected = False
        if self._pending_status is not None and not self._pending_status[1].done():
            self._pending_status[1].set_exception(BleakError("Disconnected during status read"))
        self._client = None
        # Frame counters restart at 1 on the next connection, so reusing this
        # session would encrypt with nonces the device has already seen.
        self._session = None

    def _cancel_idle_disconnect(self) -> None:
        """Invalidate queued idle work before a command, disconnect or shutdown."""
        self._connection_generation += 1
        if self._idle_disconnect_handle is not None:
            self._idle_disconnect_handle.cancel()
            self._idle_disconnect_handle = None

    def _schedule_idle_disconnect(self) -> None:
        """Release the H6104 proxy slot after a short burst of commands."""
        if not self._legacy_uart or self._client is None:
            return
        self._idle_disconnect_handle = asyncio.get_running_loop().call_later(
            _LEGACY_IDLE_DISCONNECT_SECONDS, self._begin_idle_disconnect, self._connection_generation
        )

    def _begin_idle_disconnect(self, generation: int) -> None:
        if generation != self._connection_generation:
            return
        self._idle_disconnect_handle = None
        asyncio.create_task(self._disconnect_if_idle(generation))

    async def _disconnect_if_idle(self, generation: int) -> None:
        async with self._lock:
            # A command can arrive after the timer fired but before this task
            # got the lock. It owns the connection now; don't disconnect it.
            if generation != self._connection_generation:
                return
            if self._client is None:
                return
            try:
                await self._disconnect_client()
                self._idle_disconnected = True
            except Exception:
                _LOGGER.debug("Idle BLE disconnect failed for %s", self.address, exc_info=True)

    async def _disconnect_client(self) -> None:
        """Disconnect under the caller's lock without changing light power/mode."""
        client = self._client
        try:
            if client is not None and client.is_connected:
                await client.disconnect()
        except BleakError:
            _LOGGER.debug("Error disconnecting Govee BLE client %s (ignored)", self.address)
        finally:
            self._client = None
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
            if self._legacy_uart:
                # On H6104 the 2b12 characteristic belongs to OTA service 1912,
                # not control service 1910: it is not encryption-version data.
                # Subscribe on the UART as the app does. Only validated status
                # replies/known notifications reach the separate report path.
                if self._client.services.get_characteristic(self._write_uuid) is not None:
                    client = self._client
                    await client.start_notify(
                        self._write_uuid, lambda _sender, data: self._on_uart_notification(client, data)
                    )
            elif await async_supports_encryption(self._client):
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
            self._cancel_idle_disconnect()
            self._idle_disconnected = False
            try:
                client = await self._ensure_connected()
                if client.services.get_characteristic(self._write_uuid) is None:
                    # Expose structure only, never characteristic values. Older
                    # H6104 firmware can connect without exposing our control UUID;
                    # its actual GATT layout is needed before selecting a protocol.
                    services = [
                        f"{service.uuid}: "
                        + ", ".join(f"{char.uuid} ({'/'.join(char.properties)})" for char in service.characteristics)
                        for service in client.services
                    ]
                    raise BleakError(
                        f"Control characteristic {self._write_uuid} was not found. "
                        f"Discovered GATT services: {'; '.join(services) or 'none'}. "
                        "No BLE command was sent; no alternate characteristic was tried."
                    )
                if self._session is not None:
                    frame = self._session.wrap(frame)
                await client.write_gatt_char(self._write_uuid, frame, response=False)
            finally:
                self._schedule_idle_disconnect()

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

    async def set_tv_mode(self, area: int = 1, sensitivity: int = 50) -> None:
        """Select H6104 video mode with screen area and sensitivity.

        App-derived SubModeVideo layout: 33 05 00 00 area sensitivity.
        https://github.com/Brady-Woods/govee-ble-local/blob/535104e8e107b35757e6f90d8bbb2cadf599985c/spec/docs/OPCODE_COVERAGE.md
        """
        if not self._legacy_uart:
            raise BleakError("TV mode is only implemented for the H6104 UART profile")
        if area not in (0, 1) or not 0 <= sensitivity <= 100:
            raise ValueError("TV area must be 0/1 and sensitivity must be 0-100")
        await self._write(_build_frame(LedPacketCmd.COLOR, [0x00, 0x00, area, sensitivity]))

    async def set_music_mode(
        self, mode: int, sensitivity: int = 50, rgb: tuple[int, int, int] = (255, 255, 255)
    ) -> None:
        """Select a legacy BLE music code, not a Developer API effect ID.

        H6104 SubModeMusic: 33 05 01 code sensitivity. Codes 1/2 additionally
        require RGB. Legacy codes are Energic=0, Spectrum=1, Rolling=2,
        Rhythm=3 (softgrass/govee-api-ble; still needs H6104 field validation).
        https://github.com/Brady-Woods/govee-ble-local/blob/535104e8e107b35757e6f90d8bbb2cadf599985c/spec/docs/OPCODE_COVERAGE.md
        """
        if not self._legacy_uart:
            raise BleakError("Music mode is only implemented for the H6104 UART profile")
        if mode not in (0, 1, 2, 3) or not 0 <= sensitivity <= 100:
            raise ValueError("Unsupported H6104 music mode or sensitivity")
        payload = [0x01, mode, sensitivity]
        if mode in (1, 2):
            payload.extend(max(0, min(255, int(value))) for value in rgb)
        await self._write(_build_frame(LedPacketCmd.COLOR, payload))

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
            self._cancel_idle_disconnect()
            self._idle_disconnected = False
            await self._disconnect_client()
            self._report_callbacks.clear()
