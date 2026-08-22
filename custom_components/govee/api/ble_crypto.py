"""Encrypted BLE transport (protocol version 2) for newer Govee devices.

Newer Govee SKUs silently discard the plaintext 20-byte frames that
``ble.py`` builds. They accept the very same frames wrapped in AES-GCM after
a session handshake. This module is only that wrapper: it establishes the
session and encrypts/decrypts frames. Every command encoding stays in
``ble.py`` untouched.

Reverse-engineered from the Govee Home Android app and confirmed against a
real H1270 (power, brightness and colour all verified on hardware). The
device proves the scheme itself: its encrypted handshake reply decrypts to
its own SKU and MAC, which we check below.

Keys are fixed constants compiled into the vendor app and shipped in every
device. They are not user secrets and grant nothing beyond talking to a
light you are already next to.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
from collections.abc import Callable
from dataclasses import dataclass, field

from bleak import BleakClient
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

_LOGGER = logging.getLogger(__name__)

# Reports the protocol version this device speaks (a "BgcInfo" record).
VERSION_CHARACTERISTIC_UUID = "00010203-0405-0607-0809-0a0b0c0d2b12"

# Static keys extracted from the vendor app.
_KEY_HANDSHAKE = bytes.fromhex("fc03783c7c42cb83e202a1643648aff6")
_KEY_DEVICE = bytes.fromhex("ae028b630bae6ecc4bff1b249e22f955")

# Byte 1 of the version characteristic carries the protocol version. Byte 0
# is something else and reads 0x01 on a version-2 device, so keying off it
# selects the wrong protocol and the device then never answers at all.
_VERSION_BYTE_INDEX = 1
_PROTOCOL_V2 = 2

_HANDSHAKE_REQUEST_HEADER = bytes((0xE7, 0x11, 0x01))
_HANDSHAKE_RESPONSE_HEADER = bytes((0xE7, 0x11, 0x00))

_IV_LEN = 12
_IV_KEY_LEN = 8
_COUNTER_LEN = 4
_DEVICE_INFO_LEN = 11
_SKU_LEN = 5

# The GCM tag is 16 bytes. Public write-ups of this protocol say 12; with a
# 12-byte tag the device accepts the write at GATT level and then ignores it
# completely — no reply, no state change, no error anywhere.
_TAG_LEN = 16


def _counter_bytes(counter: int) -> bytes:
    """Encode a frame counter as the 4-byte big-endian prefix/AAD."""
    return counter.to_bytes(_COUNTER_LEN, "big")


def derive_device_key(device_info: bytes) -> bytes:
    """Derive the per-device data key from the identity the device returned.

    ``device_info`` is the 11-byte blob from the handshake reply, which is
    zero-padded to one AES block and encrypted with the static device key.
    """
    padded = device_info.ljust(16, b"\x00")
    # ECB is the vendor's choice, not ours. It encrypts exactly one block of
    # device identity to derive a key, so the usual ECB pattern leak does not
    # arise here.
    encryptor = Cipher(algorithms.AES(_KEY_DEVICE), modes.ECB()).encryptor()
    return bytes(encryptor.update(padded) + encryptor.finalize())


@dataclass
class GoveeBLESession:
    """Live encryption session for one GATT connection.

    Counters restart at 1 for each direction on every new connection, so a
    session must not outlive the connection that created it.
    """

    device_key: bytes
    tx_iv_key: bytes
    rx_iv_key: bytes
    _tx_counter: int = field(default=0, repr=False)

    def wrap(self, frame: bytes) -> bytes:
        """Encrypt one plaintext command frame for transmission."""
        self._tx_counter += 1
        counter = _counter_bytes(self._tx_counter)
        sealed = AESGCM(self.device_key).encrypt(
            self.tx_iv_key + counter, frame, counter
        )
        return counter + sealed

    def unwrap(self, packet: bytes) -> bytes:
        """Decrypt one notification packet from the device.

        The device's counter is carried in the packet, so replies can be
        decrypted without tracking receive state.
        """
        counter = packet[:_COUNTER_LEN]
        return bytes(
            AESGCM(self.device_key).decrypt(
                self.rx_iv_key + counter, packet[_COUNTER_LEN:], counter
            )
        )


def parse_handshake_response(response: bytes, handshake_key: bytes) -> tuple[bytes, bytes]:
    """Decrypt the handshake reply into ``(rx_iv_key, device_info)``."""
    aad = response[: 3 + _IV_LEN]
    iv = response[3 : 3 + _IV_LEN]
    plaintext = AESGCM(handshake_key).decrypt(iv, response[3 + _IV_LEN :], aad)
    return plaintext[:_IV_KEY_LEN], plaintext[_IV_KEY_LEN:]


def build_handshake_request(iv: bytes, tx_iv_key: bytes) -> bytes:
    """Build the handshake request carrying our half of the session keys."""
    header = _HANDSHAKE_REQUEST_HEADER + iv + bytes((16,))
    return header + AESGCM(_KEY_HANDSHAKE).encrypt(iv, tx_iv_key, header)


def _log_identity(address: str, device_info: bytes) -> None:
    """Log whether the decrypted reply matches the device we dialled.

    The reply carries the device's own SKU and its MAC reversed, so a match
    turns "the write was accepted" into proof we hold the right key. This is
    advisory only and must never raise: on macOS ``address`` is a CoreBluetooth
    UUID rather than a MAC, so there is nothing to compare against.
    """
    if len(device_info) < _DEVICE_INFO_LEN:
        _LOGGER.debug("Govee BLE handshake reply is short: %s", device_info.hex())
        return
    try:
        expected_mac = bytes(reversed(bytes.fromhex(address.replace(":", ""))))
    except ValueError:
        return
    if device_info[_SKU_LEN:] != expected_mac:
        _LOGGER.debug(
            "Govee BLE handshake identity mismatch for %s: %s",
            address,
            device_info.hex(),
        )


async def async_supports_encryption(client: BleakClient) -> bool:
    """Return True if this device speaks the encrypted protocol.

    Probing the device beats maintaining an allowlist of SKUs. A device that
    does not expose the characteristic at all is plaintext, and that is decided
    from the service cache without any I/O.

    A read that fails deliberately propagates rather than answering "plaintext".
    Answering plaintext on a device that only accepts encrypted frames is the
    worst outcome available: writes use ``response=False``, so the frames the
    device discards raise nothing, the caller records a transport success, and
    the working cloud fallback is skipped. A raised error costs one failed
    command and falls through to the next transport.
    """
    if client.services.get_characteristic(VERSION_CHARACTERISTIC_UUID) is None:
        _LOGGER.debug("No BLE version characteristic on %s", client.address)
        return False
    info = await client.read_gatt_char(VERSION_CHARACTERISTIC_UUID)
    return (
        len(info) > _VERSION_BYTE_INDEX and info[_VERSION_BYTE_INDEX] == _PROTOCOL_V2
    )


async def async_establish_session(
    client: BleakClient,
    write_uuid: str,
    notify_uuid: str,
    *,
    timeout: float = 5.0,
    on_frame: Callable[[bytes], None] | None = None,
) -> GoveeBLESession:
    """Perform the handshake and return the session for this connection.

    Notifications are left enabled afterwards. The device is only known to
    honour encrypted writes with notifications subscribed, and the reply is
    also the only acknowledgement a command ever gets.

    Once the session is up, every further notification is decrypted and passed
    to ``on_frame`` as a plaintext 20-byte frame. Only one subscription to the
    notify characteristic is possible, so this callback is the sole way for a
    caller to see the device's replies.
    """
    loop = asyncio.get_running_loop()
    reply: asyncio.Future[bytes] = loop.create_future()
    established: dict[str, GoveeBLESession] = {}

    def _on_notify(_sender: object, data: bytearray) -> None:
        payload = bytes(data)
        if not reply.done() and payload.startswith(_HANDSHAKE_RESPONSE_HEADER):
            reply.set_result(payload)
            return
        session = established.get("session")
        if session is None or on_frame is None:
            return
        try:
            on_frame(session.unwrap(payload))
        except (InvalidTag, ValueError, IndexError):
            # The device also emits frames we have no counter context for.
            _LOGGER.debug("Undecryptable Govee BLE notification: %s", payload.hex())

    await client.start_notify(notify_uuid, _on_notify)

    try:
        iv = os.urandom(_IV_LEN)
        tx_iv_key = os.urandom(_IV_KEY_LEN)
        await client.write_gatt_char(
            write_uuid, build_handshake_request(iv, tx_iv_key), response=False
        )

        async with asyncio.timeout(timeout):
            response = await reply

        rx_iv_key, device_info = parse_handshake_response(response, _KEY_HANDSHAKE)
    except BaseException:
        # Only one subscription to the notify characteristic is possible, so a
        # handler left behind here would hold the only slot for this connection
        # and block every later attempt.
        with contextlib.suppress(Exception):
            await client.stop_notify(notify_uuid)
        raise

    _log_identity(client.address, device_info)

    session = GoveeBLESession(
        device_key=derive_device_key(device_info),
        tx_iv_key=tx_iv_key,
        rx_iv_key=rx_iv_key,
    )
    established["session"] = session
    return session
