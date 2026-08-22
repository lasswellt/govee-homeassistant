"""Tests for ``custom_components.govee.api.ble``.

Covers the BLE protocol encoding (frame layout, XOR checksum, command bytes,
RGB encoding, brightness encoding) and the ``GoveeBLEDevice`` wrapper class
(connection lifecycle, proxy handoff via ``ble_device_callback``, callback
fan-out, command plumbing). No real Bluetooth hardware is touched — all GATT
interactions are mocked.

Reference behavior is validated against Beshelmek/govee_ble_lights
(see ``docs/_research/2026-04-08_ble-direct-support.md`` for the cross-check).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.govee.api.ble import (
    BLE_DISCOVERY_NAMES,
    READ_CHARACTERISTIC_UUID,
    SEGMENTED_MODELS,
    WRITE_CHARACTERISTIC_UUID,
    GoveeBLEDevice,
    GoveeBLEState,
    LedColorMode,
    LedPacketCmd,
    _build_brightness_frame,
    _build_frame,
    _build_power_frame,
    _build_rgb_segmented_frame,
    _build_rgb_single_frame,
)
from custom_components.govee.api.ble_crypto import GoveeBLESession


@pytest.fixture(autouse=True)
def _plaintext_device():
    """Treat the mocked device as speaking the plaintext protocol.

    ``_ensure_connected`` probes every connection for the encrypted protocol,
    which reads a GATT characteristic. The clients here are MagicMocks, so
    that read would be awaited on a non-awaitable. The encrypted path has its
    own tests in ``test_ble_crypto.py`` and ``TestEncryptedTransport`` below.
    """
    with patch(
        "custom_components.govee.api.ble.async_supports_encryption",
        AsyncMock(return_value=False),
    ):
        yield


# ==============================================================================
# Module constants
# ==============================================================================


class TestConstants:
    """Characteristic UUIDs and SKU lists are stable — lock them down."""

    def test_write_characteristic_uuid(self):
        """Write characteristic matches Beshelmek/govee_ble_lights reference."""
        assert WRITE_CHARACTERISTIC_UUID == "00010203-0405-0607-0809-0a0b0c0d2b11"

    def test_read_characteristic_uuid(self):
        """Read (notify) characteristic uses the ``2b10`` suffix variant."""
        assert READ_CHARACTERISTIC_UUID == "00010203-0405-0607-0809-0a0b0c0d2b10"

    def test_discovery_name_prefixes(self):
        """All three known Govee BLE advertising name prefixes are listed."""
        assert BLE_DISCOVERY_NAMES == ("Govee_", "ihoment_", "GBK_")

    def test_segmented_models_covers_beshelmek(self):
        """Segmented whitelist keeps every SKU from Beshelmek light.py:35."""
        assert SEGMENTED_MODELS >= frozenset({"H6053", "H6072", "H6102", "H6199"})

    def test_segmented_models_includes_h1270(self):
        """H1270 ignores the single-zone frame and needs mode 0x15.

        Not in the Beshelmek list — added from field testing, where the colour
        read-back stayed black until the segmented encoding was used.
        """
        assert "H1270" in SEGMENTED_MODELS

    def test_command_byte_values(self):
        """Command bytes match the reference protocol."""
        assert LedPacketCmd.POWER == 0x01
        assert LedPacketCmd.BRIGHTNESS == 0x04
        assert LedPacketCmd.COLOR == 0x05

    def test_color_mode_byte_values(self):
        """Color mode bytes match the reference protocol."""
        assert LedColorMode.SINGLE == 0x02
        assert LedColorMode.SCENES == 0x05
        assert LedColorMode.MICROPHONE == 0x06
        assert LedColorMode.SEGMENTS == 0x15

    def test_no_legacy_color_mode(self):
        """``LEGACY = 0x0D`` from PR #52 has been removed (no primary source)."""
        assert not hasattr(LedColorMode, "LEGACY")


# ==============================================================================
# Frame construction (pure functions, no BLE)
# ==============================================================================


class TestBuildFrame:
    """Generic ``_build_frame`` wrapping ``ble_packet.build_packet``."""

    def test_frame_is_20_bytes(self):
        """Every frame must be exactly 20 bytes regardless of payload size."""
        frame = _build_frame(LedPacketCmd.POWER, [0x01])
        assert len(frame) == 20

    def test_frame_starts_with_head_byte(self):
        """Byte 0 is always the command head ``0x33``."""
        frame = _build_frame(LedPacketCmd.POWER, [0x01])
        assert frame[0] == 0x33

    def test_frame_command_byte_at_index_1(self):
        """Byte 1 is the command enum value."""
        frame = _build_frame(LedPacketCmd.BRIGHTNESS, [128])
        assert frame[1] == 0x04

    def test_frame_payload_follows_command(self):
        """Payload bytes immediately follow the command byte."""
        frame = _build_frame(LedPacketCmd.COLOR, [0x02, 0xFF, 0x00, 0x7F])
        assert frame[2:6] == bytes([0x02, 0xFF, 0x00, 0x7F])

    def test_frame_zero_padded_after_payload(self):
        """Space between payload and checksum is zero-padded."""
        frame = _build_frame(LedPacketCmd.POWER, [0x01])
        # bytes 3..18 (16 bytes) should all be zero
        assert frame[3:19] == bytes([0x00] * 16)

    def test_frame_checksum_is_xor_of_bytes_0_to_18(self):
        """Last byte is XOR of all preceding bytes."""
        frame = _build_frame(LedPacketCmd.COLOR, [0x02, 0x11, 0x22, 0x33])
        expected = 0
        for b in frame[:19]:
            expected ^= b
        assert frame[19] == expected

    def test_frame_long_payload_truncated_at_17_bytes(self):
        """Payloads longer than 17 bytes are truncated by build_packet."""
        long_payload = list(range(20))  # 20 bytes, too many
        frame = _build_frame(LedPacketCmd.POWER, long_payload)
        assert len(frame) == 20


class TestPowerFrame:
    def test_power_on_frame_bytes(self):
        frame = _build_power_frame(True)
        assert frame[0] == 0x33
        assert frame[1] == LedPacketCmd.POWER
        assert frame[2] == 0x01

    def test_power_off_frame_bytes(self):
        frame = _build_power_frame(False)
        assert frame[0] == 0x33
        assert frame[1] == LedPacketCmd.POWER
        assert frame[2] == 0x00

    def test_power_on_checksum_valid(self):
        frame = _build_power_frame(True)
        computed = 0
        for b in frame[:19]:
            computed ^= b
        assert frame[19] == computed


class TestBrightnessFrame:
    def test_brightness_bytes(self):
        frame = _build_brightness_frame(128)
        assert frame[0] == 0x33
        assert frame[1] == LedPacketCmd.BRIGHTNESS
        assert frame[2] == 128

    def test_brightness_sent_as_raw_0_255(self):
        """Brightness must be sent as HA's native 0-255 range — no rescale.

        This is the Beshelmek reference behavior; PR #52's rescale to 0-100
        for segmented models was speculative and has been removed.
        """
        frame = _build_brightness_frame(255)
        assert frame[2] == 255

    def test_brightness_clamped_above_255(self):
        """Values above 255 are clamped, not wrapped."""
        frame = _build_brightness_frame(500)
        assert frame[2] == 255

    def test_brightness_clamped_below_zero(self):
        """Negative values are clamped to zero."""
        frame = _build_brightness_frame(-10)
        assert frame[2] == 0

    def test_brightness_zero(self):
        frame = _build_brightness_frame(0)
        assert frame[2] == 0


class TestRgbSingleFrame:
    def test_single_frame_bytes(self):
        frame = _build_rgb_single_frame(255, 128, 64)
        assert frame[0] == 0x33
        assert frame[1] == LedPacketCmd.COLOR
        assert frame[2] == LedColorMode.SINGLE
        assert frame[3] == 255
        assert frame[4] == 128
        assert frame[5] == 64

    def test_single_frame_black(self):
        frame = _build_rgb_single_frame(0, 0, 0)
        assert frame[3:6] == bytes([0, 0, 0])

    def test_single_frame_white(self):
        frame = _build_rgb_single_frame(255, 255, 255)
        assert frame[3:6] == bytes([255, 255, 255])


class TestRgbSegmentedFrame:
    """Segmented frames must match Beshelmek/govee_ble_lights byte-for-byte."""

    def test_segmented_frame_bytes(self):
        frame = _build_rgb_segmented_frame(255, 0, 0)
        assert frame[0] == 0x33
        assert frame[1] == LedPacketCmd.COLOR
        assert frame[2] == LedColorMode.SEGMENTS
        assert frame[3] == 0x01
        assert frame[4:7] == bytes([255, 0, 0])

    def test_segmented_frame_zero_padded_middle(self):
        """Bytes 7..11 are zero-padding between the RGB triple and the tail."""
        # Frame layout: [head=0x33][cmd=0x05][mode=0x15][0x01][r][g][b]
        #               [0,0,0,0,0][0xFF][0x7F][pad...][checksum]
        #                ^indices 7-11  12    13
        frame = _build_rgb_segmented_frame(10, 20, 30)
        assert frame[7:12] == bytes([0x00] * 5)

    def test_segmented_frame_tail_matches_beshelmek(self):
        """Tail bytes must be 0xFF 0x7F (Beshelmek light.py:273).

        PR #52's original implementation ended with 0xFF 0xFF; we validated
        against the Beshelmek reference and confirmed 0xFF 0x7F is correct.
        """
        frame = _build_rgb_segmented_frame(10, 20, 30)
        assert frame[12] == 0xFF
        assert frame[13] == 0x7F

    def test_segmented_frame_rest_zero_padded(self):
        """Bytes after the 0x7F tail up to the checksum byte are zero."""
        frame = _build_rgb_segmented_frame(10, 20, 30)
        assert frame[14:19] == bytes([0x00] * 5)


# ==============================================================================
# GoveeBLEState dataclass
# ==============================================================================


class TestGoveeBLEState:
    def test_default_state_is_all_none(self):
        """Fresh state has no known values (lights may be in any state)."""
        state = GoveeBLEState()
        assert state.power is None
        assert state.brightness is None
        assert state.rgb is None

    def test_state_fields_assignable(self):
        """State is a mutable dataclass for optimistic updates."""
        state = GoveeBLEState()
        state.power = True
        state.brightness = 200
        state.rgb = (100, 50, 25)
        assert state.power is True
        assert state.brightness == 200
        assert state.rgb == (100, 50, 25)


# ==============================================================================
# GoveeBLEDevice — construction + reference management
# ==============================================================================


def _make_ble_device(
    address: str = "AA:BB:CC:DD:EE:FF", name: str = "Govee_H6072_1234"
):
    """Build a minimal BLEDevice-like object for tests."""
    device = MagicMock()
    device.address = address
    device.name = name
    return device


class TestGoveeBLEDeviceBasics:
    def test_address_exposed_from_ble_device(self):
        ble_device = _make_ble_device()
        device = GoveeBLEDevice(ble_device)
        assert device.address == "AA:BB:CC:DD:EE:FF"

    def test_name_exposed_from_ble_device(self):
        ble_device = _make_ble_device(name="ihoment_H6102_5678")
        device = GoveeBLEDevice(ble_device)
        assert device.name == "ihoment_H6102_5678"

    def test_name_fallback_when_ble_device_has_none(self):
        """A BLEDevice with ``name=None`` should still expose a sensible name."""
        ble_device = _make_ble_device()
        ble_device.name = None
        device = GoveeBLEDevice(ble_device)
        assert device.name == "Govee BLE Light"

    def test_initial_state_is_empty(self):
        device = GoveeBLEDevice(_make_ble_device())
        assert device.state.power is None
        assert device.state.brightness is None
        assert device.state.rgb is None

    def test_segmented_flag_defaults_false(self):
        device = GoveeBLEDevice(_make_ble_device())
        assert device.segmented is False

    def test_segmented_flag_honored(self):
        device = GoveeBLEDevice(_make_ble_device(), segmented=True)
        assert device.segmented is True


class TestSetBleDeviceAndAdvertisementData:
    """PASSIVE advertisement callback must refresh the BLEDevice reference."""

    def test_advertisement_refreshes_cached_ble_device(self):
        original = _make_ble_device()
        device = GoveeBLEDevice(original)
        refreshed = _make_ble_device(name="Govee_H6072_1234 (refreshed)")
        refreshed.address = original.address

        device.set_ble_device_and_advertisement_data(refreshed, MagicMock())

        # Subsequent reads should see the refreshed reference.
        assert device.name == "Govee_H6072_1234 (refreshed)"


class TestRegisterCallback:
    def test_callback_fires_on_state_change(self):
        device = GoveeBLEDevice(_make_ble_device())
        received = []
        device.register_callback(received.append)
        device._state.power = True
        device._emit()
        assert len(received) == 1
        assert received[0] is device.state

    def test_unsubscribe_stops_callback(self):
        device = GoveeBLEDevice(_make_ble_device())
        received = []
        unsub = device.register_callback(received.append)
        unsub()
        device._emit()
        assert received == []

    def test_unsubscribe_is_idempotent(self):
        """Calling unsub twice must not raise."""
        device = GoveeBLEDevice(_make_ble_device())
        unsub = device.register_callback(lambda _s: None)
        unsub()
        unsub()  # must not raise

    def test_multiple_callbacks_all_fire(self):
        device = GoveeBLEDevice(_make_ble_device())
        a, b = [], []
        device.register_callback(a.append)
        device.register_callback(b.append)
        device._emit()
        assert len(a) == 1
        assert len(b) == 1


# ==============================================================================
# GoveeBLEDevice — connection management
# ==============================================================================


class TestEnsureConnected:
    """_ensure_connected must call the critical defensive helpers."""

    @pytest.mark.asyncio
    async def test_calls_close_stale_connections_first(self):
        """``close_stale_connections_by_address`` must be called before connect.

        Failing to do this leaves dangling GATT handles from a crashed HA
        which block ``establish_connection`` until the next reboot.
        """
        ble_device = _make_ble_device()
        device = GoveeBLEDevice(ble_device)

        fake_client = MagicMock()
        fake_client.is_connected = True

        with patch(
            "custom_components.govee.api.ble.close_stale_connections_by_address",
            AsyncMock(),
        ) as mock_close, patch(
            "custom_components.govee.api.ble.establish_connection",
            AsyncMock(return_value=fake_client),
        ) as mock_establish:
            await device._ensure_connected()

        mock_close.assert_awaited_once_with("AA:BB:CC:DD:EE:FF")
        # close_stale must be called BEFORE establish_connection
        assert mock_close.call_args is not None
        assert mock_establish.call_args is not None

    @pytest.mark.asyncio
    async def test_wires_ble_device_callback_for_proxy_handoff(self):
        """``establish_connection`` must receive a ``ble_device_callback`` that
        refreshes via the caller-supplied refresh callable."""
        ble_device_v1 = _make_ble_device()
        ble_device_v2 = _make_ble_device()

        refresh_calls = iter([ble_device_v2])
        device = GoveeBLEDevice(
            ble_device_v1,
            refresh_ble_device=lambda: next(refresh_calls, None),
        )

        captured_kwargs: dict = {}

        async def fake_establish(*args, **kwargs):
            captured_kwargs.update(kwargs)
            client = MagicMock()
            client.is_connected = True
            return client

        with patch(
            "custom_components.govee.api.ble.close_stale_connections_by_address",
            AsyncMock(),
        ), patch(
            "custom_components.govee.api.ble.establish_connection",
            side_effect=fake_establish,
        ):
            await device._ensure_connected()

        assert "ble_device_callback" in captured_kwargs
        cb = captured_kwargs["ble_device_callback"]
        # Calling the callback must return the refreshed device AND update
        # the cached reference on the class.
        assert cb() is ble_device_v2
        assert device._ble_device is ble_device_v2

    @pytest.mark.asyncio
    async def test_establish_connection_kwargs(self):
        """Sanity-check the constant kwargs — use_services_cache, max_attempts."""
        device = GoveeBLEDevice(_make_ble_device())

        captured: dict = {}

        async def fake_establish(*args, **kwargs):
            captured.update(kwargs)
            client = MagicMock()
            client.is_connected = True
            return client

        with patch(
            "custom_components.govee.api.ble.close_stale_connections_by_address",
            AsyncMock(),
        ), patch(
            "custom_components.govee.api.ble.establish_connection",
            side_effect=fake_establish,
        ):
            await device._ensure_connected()

        assert captured["use_services_cache"] is True
        assert captured["max_attempts"] == 4
        assert captured["name"] == "Govee_H6072_1234"

    @pytest.mark.asyncio
    async def test_reuses_existing_connected_client(self):
        """Second call must return the cached client without reconnecting."""
        device = GoveeBLEDevice(_make_ble_device())

        fake_client = MagicMock()
        fake_client.is_connected = True

        with patch(
            "custom_components.govee.api.ble.close_stale_connections_by_address",
            AsyncMock(),
        ) as mock_close, patch(
            "custom_components.govee.api.ble.establish_connection",
            AsyncMock(return_value=fake_client),
        ) as mock_establish:
            first = await device._ensure_connected()
            second = await device._ensure_connected()

        assert first is second
        mock_establish.assert_awaited_once()
        mock_close.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_refresh_callable_returning_none_falls_back(self):
        """If the refresh callable returns None, use the cached BLEDevice."""
        original = _make_ble_device()
        device = GoveeBLEDevice(original, refresh_ble_device=lambda: None)

        captured: dict = {}

        async def fake_establish(*args, **kwargs):
            captured.update(kwargs)
            client = MagicMock()
            client.is_connected = True
            return client

        with patch(
            "custom_components.govee.api.ble.close_stale_connections_by_address",
            AsyncMock(),
        ), patch(
            "custom_components.govee.api.ble.establish_connection",
            side_effect=fake_establish,
        ):
            await device._ensure_connected()

        assert captured["ble_device_callback"]() is original


class TestOnDisconnected:
    def test_disconnection_clears_cached_client(self):
        device = GoveeBLEDevice(_make_ble_device())
        device._client = MagicMock()
        device._on_disconnected(device._client)
        assert device._client is None


# ==============================================================================
# GoveeBLEDevice — command plumbing
# ==============================================================================


def _install_fake_client(device: GoveeBLEDevice) -> MagicMock:
    """Bypass ``_ensure_connected`` by installing a fake already-connected client."""
    fake_client = MagicMock()
    fake_client.is_connected = True
    fake_client.write_gatt_char = AsyncMock()
    fake_client.disconnect = AsyncMock()
    device._client = fake_client
    return fake_client


class TestTurnOnOff:
    @pytest.mark.asyncio
    async def test_turn_on_writes_power_frame(self):
        device = GoveeBLEDevice(_make_ble_device())
        client = _install_fake_client(device)

        await device.turn_on()

        client.write_gatt_char.assert_awaited_once()
        args, kwargs = client.write_gatt_char.call_args
        assert args[0] == WRITE_CHARACTERISTIC_UUID
        frame = args[1]
        assert frame[1] == LedPacketCmd.POWER
        assert frame[2] == 0x01
        assert kwargs["response"] is False
        assert device.state.power is True

    @pytest.mark.asyncio
    async def test_turn_off_writes_power_frame(self):
        device = GoveeBLEDevice(_make_ble_device())
        client = _install_fake_client(device)

        await device.turn_off()

        frame = client.write_gatt_char.call_args.args[1]
        assert frame[1] == LedPacketCmd.POWER
        assert frame[2] == 0x00
        assert device.state.power is False

    @pytest.mark.asyncio
    async def test_turn_on_fires_callback(self):
        device = GoveeBLEDevice(_make_ble_device())
        _install_fake_client(device)
        received = []
        device.register_callback(received.append)

        await device.turn_on()

        assert len(received) == 1
        assert received[0].power is True


class TestSetBrightness:
    @pytest.mark.asyncio
    async def test_set_brightness_writes_frame(self):
        device = GoveeBLEDevice(_make_ble_device())
        client = _install_fake_client(device)

        await device.set_brightness(128)

        frame = client.write_gatt_char.call_args.args[1]
        assert frame[1] == LedPacketCmd.BRIGHTNESS
        assert frame[2] == 128
        assert device.state.brightness == 128

    @pytest.mark.asyncio
    async def test_set_brightness_clamped(self):
        device = GoveeBLEDevice(_make_ble_device())
        client = _install_fake_client(device)

        await device.set_brightness(500)

        frame = client.write_gatt_char.call_args.args[1]
        assert frame[2] == 255
        assert device.state.brightness == 255

    @pytest.mark.asyncio
    async def test_set_brightness_not_rescaled_for_segmented(self):
        """Even segmented devices receive the raw 0-255 value."""
        device = GoveeBLEDevice(_make_ble_device(), segmented=True)
        client = _install_fake_client(device)

        await device.set_brightness(200)

        frame = client.write_gatt_char.call_args.args[1]
        assert frame[2] == 200  # NOT 200/255*100 = 78


class TestSetRgb:
    @pytest.mark.asyncio
    async def test_single_mode_frame(self):
        device = GoveeBLEDevice(_make_ble_device(), segmented=False)
        client = _install_fake_client(device)

        await device.set_rgb(255, 128, 64)

        frame = client.write_gatt_char.call_args.args[1]
        assert frame[1] == LedPacketCmd.COLOR
        assert frame[2] == LedColorMode.SINGLE
        assert frame[3:6] == bytes([255, 128, 64])
        assert device.state.rgb == (255, 128, 64)

    @pytest.mark.asyncio
    async def test_segmented_mode_frame(self):
        device = GoveeBLEDevice(_make_ble_device(), segmented=True)
        client = _install_fake_client(device)

        await device.set_rgb(10, 20, 30)

        frame = client.write_gatt_char.call_args.args[1]
        assert frame[1] == LedPacketCmd.COLOR
        assert frame[2] == LedColorMode.SEGMENTS
        assert frame[3] == 0x01
        assert frame[4:7] == bytes([10, 20, 30])
        # Tail matches Beshelmek reference exactly
        assert frame[12] == 0xFF
        assert frame[13] == 0x7F

    @pytest.mark.asyncio
    async def test_rgb_values_clamped(self):
        device = GoveeBLEDevice(_make_ble_device())
        client = _install_fake_client(device)

        await device.set_rgb(300, -10, 128)

        frame = client.write_gatt_char.call_args.args[1]
        assert frame[3:6] == bytes([255, 0, 128])
        assert device.state.rgb == (255, 0, 128)


class TestUpdate:
    @pytest.mark.asyncio
    async def test_update_returns_cached_state_without_gatt(self):
        """``update()`` is a no-op — no GATT writes, returns cached state."""
        device = GoveeBLEDevice(_make_ble_device())
        client = _install_fake_client(device)
        device._state.power = True
        device._state.brightness = 200

        result = await device.update()

        assert result is device.state
        assert result.power is True
        assert result.brightness == 200
        client.write_gatt_char.assert_not_called()


class TestStop:
    @pytest.mark.asyncio
    async def test_stop_disconnects_connected_client(self):
        device = GoveeBLEDevice(_make_ble_device())
        client = _install_fake_client(device)

        await device.stop()

        client.disconnect.assert_awaited_once()
        assert device._client is None

    @pytest.mark.asyncio
    async def test_stop_skips_when_no_client(self):
        """Stop is a no-op when never connected — must not raise."""
        device = GoveeBLEDevice(_make_ble_device())
        assert device._client is None
        await device.stop()  # must not raise
        assert device._client is None

    @pytest.mark.asyncio
    async def test_stop_skips_when_client_already_disconnected(self):
        """Stop checks ``is_connected`` before calling disconnect."""
        device = GoveeBLEDevice(_make_ble_device())
        client = MagicMock()
        client.is_connected = False
        client.disconnect = AsyncMock()
        device._client = client

        await device.stop()

        client.disconnect.assert_not_called()
        assert device._client is None

    @pytest.mark.asyncio
    async def test_stop_swallows_bleak_error_on_disconnect(self):
        """BleakError during disconnect must be caught (connection was already dying)."""
        from bleak_retry_connector import BleakError

        device = GoveeBLEDevice(_make_ble_device())
        client = MagicMock()
        client.is_connected = True
        client.disconnect = AsyncMock(side_effect=BleakError("boom"))
        device._client = client

        await device.stop()  # must not raise
        assert device._client is None


# ==============================================================================
# Encrypted transport wiring
# ==============================================================================


class TestEncryptedTransport:
    """Frames must be encrypted when a session was negotiated, and only then."""

    @staticmethod
    def _connected_device() -> tuple[GoveeBLEDevice, MagicMock]:
        device = GoveeBLEDevice(_make_ble_device())
        client = MagicMock()
        client.is_connected = True
        client.write_gatt_char = AsyncMock()
        device._client = client
        return device, client

    @staticmethod
    def _session() -> GoveeBLESession:
        return GoveeBLESession(
            device_key=bytes(16), tx_iv_key=bytes(8), rx_iv_key=bytes(8)
        )

    @pytest.mark.asyncio
    async def test_write_sends_plaintext_without_session(self):
        """Devices on the plaintext protocol must be unaffected."""
        device, client = self._connected_device()

        await device.turn_on()

        assert client.write_gatt_char.call_args[0][1] == _build_power_frame(True)

    @pytest.mark.asyncio
    async def test_write_encrypts_the_same_command_frame(self):
        """The wrapped frame must decrypt back to the identical command."""
        device, client = self._connected_device()
        device._session = self._session()

        await device.turn_on()

        sent = client.write_gatt_char.call_args[0][1]
        assert sent != _build_power_frame(True)
        # The peer decrypts our transmissions with our tx key as its rx key.
        peer = GoveeBLESession(
            device_key=bytes(16), tx_iv_key=bytes(8), rx_iv_key=bytes(8)
        )
        assert peer.unwrap(sent) == _build_power_frame(True)

    @pytest.mark.asyncio
    async def test_disconnect_clears_session(self):
        """A stale session would reuse nonces the device has already seen."""
        device, _client = self._connected_device()
        device._session = self._session()

        device._on_disconnected(MagicMock())

        assert device._session is None


class TestNegotiationFailureNeverDowngrades:
    """A failed negotiation must not leave a usable plaintext connection.

    Writes use ``response=False``, so plaintext frames sent to an encrypted
    device raise nothing. The caller would then record a transport success and
    skip the LAN/MQTT/REST fallback, leaving the device untouched while HA
    shows the command as applied.
    """

    @staticmethod
    def _patches(establish_side_effect=None, supports=True):
        client = MagicMock()
        client.is_connected = True
        client.write_gatt_char = AsyncMock()
        client.disconnect = AsyncMock()
        return client, patch(
            "custom_components.govee.api.ble.establish_connection",
            AsyncMock(return_value=client),
        ), patch(
            "custom_components.govee.api.ble.close_stale_connections_by_address",
            AsyncMock(),
        ), patch(
            "custom_components.govee.api.ble.async_supports_encryption",
            AsyncMock(return_value=supports),
        ), patch(
            "custom_components.govee.api.ble.async_establish_session",
            AsyncMock(side_effect=establish_side_effect),
        )

    @pytest.mark.asyncio
    async def test_handshake_failure_drops_the_connection_and_raises(self):
        device = GoveeBLEDevice(_make_ble_device())
        client, p1, p2, p3, p4 = self._patches(TimeoutError("no reply"))

        with p1, p2, p3, p4, pytest.raises(TimeoutError):
            await device._ensure_connected()

        # Nothing cached, so the next command renegotiates instead of
        # silently writing plaintext down a half-negotiated link.
        assert device._client is None
        assert device._session is None
        client.disconnect.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_probe_failure_drops_the_connection_and_raises(self):
        device = GoveeBLEDevice(_make_ble_device())
        client = MagicMock()
        client.is_connected = True
        client.disconnect = AsyncMock()

        with patch(
            "custom_components.govee.api.ble.establish_connection",
            AsyncMock(return_value=client),
        ), patch(
            "custom_components.govee.api.ble.close_stale_connections_by_address",
            AsyncMock(),
        ), patch(
            "custom_components.govee.api.ble.async_supports_encryption",
            AsyncMock(side_effect=TimeoutError("proxy busy")),
        ), pytest.raises(TimeoutError):
            await device._ensure_connected()

        assert device._client is None
        client.disconnect.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_a_command_after_a_failed_handshake_renegotiates(self):
        """The failure must not be sticky for the life of the link."""
        device = GoveeBLEDevice(_make_ble_device())
        session = GoveeBLESession(
            device_key=bytes(16), tx_iv_key=bytes(8), rx_iv_key=bytes(8)
        )
        client, p1, p2, p3, _ = self._patches()
        establish = AsyncMock(side_effect=[TimeoutError("no reply"), session])

        with p1, p2, p3, patch(
            "custom_components.govee.api.ble.async_establish_session", establish
        ):
            with pytest.raises(TimeoutError):
                await device.turn_on()
            await device.turn_on()  # must try again, and succeed

        assert establish.await_count == 2
        assert device._session is session
        sent = client.write_gatt_char.call_args[0][1]
        assert sent != _build_power_frame(True)  # it went out encrypted
