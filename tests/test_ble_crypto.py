"""Tests for the encrypted Govee BLE transport.

The key derivation vector below was captured from a real H1270 during the
work that reverse-engineered this protocol, so these are known-answer tests
against hardware rather than against our own implementation.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from custom_components.govee.api.ble_crypto import (
    GoveeBLESession,
    async_establish_session,
    async_supports_encryption,
    build_handshake_request,
    derive_device_key,
    parse_handshake_response,
)

# Captured from a real H1270 (MAC C0:EB:32:C1:19:FC).
_REAL_DEVICE_INFO = bytes.fromhex("4831323730fc19c132ebc0")
_REAL_DEVICE_KEY = bytes.fromhex("6180f19566dedc80a3adef2f8b936184")

_HANDSHAKE_KEY = bytes.fromhex("fc03783c7c42cb83e202a1643648aff6")


def _session() -> GoveeBLESession:
    return GoveeBLESession(
        device_key=_REAL_DEVICE_KEY,
        tx_iv_key=bytes.fromhex("0011223344556677"),
        rx_iv_key=bytes.fromhex("8899aabbccddeeff"),
    )


def test_device_key_matches_real_device() -> None:
    """Key derivation reproduces the key a real H1270 session used."""
    assert derive_device_key(_REAL_DEVICE_INFO) == _REAL_DEVICE_KEY


def test_device_info_carries_sku_and_reversed_mac() -> None:
    """The identity check that proves the handshake decrypted correctly."""
    assert _REAL_DEVICE_INFO[:5] == b"H1270"
    mac = bytes.fromhex("C0EB32C119FC".lower())
    assert _REAL_DEVICE_INFO[5:] == bytes(reversed(mac))


def test_wrap_uses_a_sixteen_byte_tag() -> None:
    """A 12-byte tag is silently ignored by the device, so pin the length."""
    frame = bytes(20)
    wrapped = _session().wrap(frame)
    # 4-byte counter prefix + ciphertext the size of the frame + 16-byte tag.
    assert len(wrapped) == 4 + 20 + 16


def test_wrap_counter_starts_at_one_and_increments() -> None:
    """Counters start at 1 per connection and advance by one per frame."""
    session = _session()
    assert session.wrap(bytes(20))[:4] == (1).to_bytes(4, "big")
    assert session.wrap(bytes(20))[:4] == (2).to_bytes(4, "big")


def test_wrap_never_repeats_a_nonce() -> None:
    """Identical frames must still encrypt differently."""
    session = _session()
    frame = bytes(range(20))
    assert session.wrap(frame) != session.wrap(frame)


def test_unwrap_round_trips_a_wrapped_frame() -> None:
    """Decryption recovers the exact plaintext command frame."""
    # One session encrypts with tx; the peer decrypts with the same key, so
    # mirror the IV keys to model the far end.
    sender = _session()
    receiver = GoveeBLESession(
        device_key=_REAL_DEVICE_KEY,
        tx_iv_key=sender.rx_iv_key,
        rx_iv_key=sender.tx_iv_key,
    )
    frame = bytes.fromhex("330100000000000000000000000000000000000032")[:20]
    assert receiver.unwrap(sender.wrap(frame)) == frame


def test_unwrap_rejects_a_tampered_frame() -> None:
    """Authentication actually holds — a flipped bit must not decrypt."""
    session = _session()
    wrapped = bytearray(session.wrap(bytes(20)))
    wrapped[-1] ^= 0x01
    peer = GoveeBLESession(
        device_key=_REAL_DEVICE_KEY,
        tx_iv_key=session.rx_iv_key,
        rx_iv_key=session.tx_iv_key,
    )
    with pytest.raises(Exception):  # noqa: B017 — InvalidTag from cryptography
        peer.unwrap(bytes(wrapped))


def test_handshake_request_layout() -> None:
    """The request header and IV sit where the device expects them.

    The request's AAD is the full 16-byte header including the trailing tag
    length byte, one byte longer than the reply's — hence the two are parsed
    separately rather than sharing a helper.
    """
    iv = bytes(range(12))
    tx_iv_key = bytes.fromhex("0011223344556677")
    request = build_handshake_request(iv, tx_iv_key)

    assert request[:3] == bytes((0xE7, 0x11, 0x01))
    assert request[3:15] == iv
    assert request[15] == 16
    # 16-byte header + the 8-byte key sealed under a 16-byte tag.
    assert len(request) == 16 + 8 + 16


def test_parse_handshake_response_recovers_keys_and_identity() -> None:
    """The reply parser uses the 15-byte AAD and offsets the device uses."""
    rx_iv_key = bytes.fromhex("8a10ed17775ef1f4")
    iv = bytes(range(12))
    header = bytes((0xE7, 0x11, 0x00)) + iv
    body = AESGCM(_HANDSHAKE_KEY).encrypt(iv, rx_iv_key + _REAL_DEVICE_INFO, header)

    parsed_key, parsed_info = parse_handshake_response(header + body, _HANDSHAKE_KEY)

    assert parsed_key == rx_iv_key
    assert parsed_info == _REAL_DEVICE_INFO
    assert derive_device_key(parsed_info) == _REAL_DEVICE_KEY


class TestEncryptionProbe:
    """The probe must never answer "plaintext" just because a read failed.

    Writes are fire-and-forget, so a wrong "plaintext" answer makes an
    encrypted device discard every frame silently while the caller records a
    transport success and skips the cloud fallback.
    """

    @pytest.mark.asyncio
    async def test_absent_characteristic_means_plaintext(self):
        """A device without the characteristic is plaintext, decided with no I/O."""
        client = MagicMock()
        client.services.get_characteristic.return_value = None
        client.read_gatt_char = AsyncMock()

        assert await async_supports_encryption(client) is False
        client.read_gatt_char.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_read_failure_propagates_rather_than_downgrading(self):
        """A timed-out read must raise, not silently select the plaintext path."""
        client = MagicMock()
        client.services.get_characteristic.return_value = object()
        client.read_gatt_char = AsyncMock(side_effect=TimeoutError("proxy busy"))

        with pytest.raises(TimeoutError):
            await async_supports_encryption(client)

    @pytest.mark.asyncio
    async def test_version_two_is_detected_from_byte_one(self):
        """Byte 1 carries the version. Byte 0 reads 0x01 on a v2 device."""
        client = MagicMock()
        client.services.get_characteristic.return_value = object()
        client.read_gatt_char = AsyncMock(return_value=bytes([0x01, 0x02, 0x00]))

        assert await async_supports_encryption(client) is True


class TestHandshakeCleanup:
    """A failed handshake must not strand the single notify subscription."""

    @pytest.mark.asyncio
    async def test_timeout_releases_the_notify_subscription(self):
        client = MagicMock()
        client.address = "C0:EB:32:C1:19:FC"
        client.start_notify = AsyncMock()
        client.stop_notify = AsyncMock()
        client.write_gatt_char = AsyncMock()  # device never replies

        with pytest.raises(TimeoutError):
            await async_establish_session(client, "w", "n", timeout=0.05)

        client.stop_notify.assert_awaited_once_with("n")
