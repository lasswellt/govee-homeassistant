"""Tests for Tower Fan swing-range tail capture in the AWS IoT MQTT client.

Tower Fan 2 units (H7105/H7107) report their configured sweep arc in an
inbound ``aa 1d`` BLE-format frame. The client keeps the last 4 range bytes
per device so an oscillation-ON frame can replay the fan's own arc
(homebridge-govee ``fan-H7107.js``).
"""

from __future__ import annotations

import base64
import json
from unittest.mock import MagicMock

import pytest

from custom_components.govee.api.auth import GoveeIotCredentials
from custom_components.govee.api.mqtt import GoveeAwsIotClient

FAN_ID = "AA:BB:CC:DD:EE:FF:00:44"


def _make_client() -> GoveeAwsIotClient:
    """Build a client with throwaway credentials and a no-op callback."""
    creds = GoveeIotCredentials(
        token="t",
        refresh_token="r",
        account_topic="GA/account",
        iot_cert="cert",
        iot_key="key",
        iot_ca=None,
        client_id="cid",
        endpoint="endpoint",
    )
    client = GoveeAwsIotClient(creds, on_state_update=MagicMock())
    # The multiSync branch is exercised elsewhere; keep this test on the tail.
    client._handle_multisync = MagicMock()
    return client


def _message(
    frames: list[str | bytes | int | None],
    *,
    cmd: str = "multiSync",
    state: dict | None = None,
) -> MagicMock:
    """Wrap raw frames in an inbound message from the fan.

    Defaults to the hub-style ``multiSync`` envelope; pass ``cmd="status"``
    and a ``state`` dict for the fan's own status push, which carries the
    ``op.command`` list alongside ``state``.
    """
    encoded = [
        base64.b64encode(f).decode() if isinstance(f, bytes) else f for f in frames
    ]
    payload: dict = {
        "device": FAN_ID,
        "sku": "H7107",
        "cmd": cmd,
        "op": {"command": encoded},
    }
    if state is not None:
        payload["state"] = state
    message = MagicMock()
    message.topic = "GA/account"
    message.payload = json.dumps(payload).encode()
    return message


def _aa1d(tail: list[int]) -> bytes:
    """Build a 20-byte aa 1d status frame carrying the given 4 range bytes."""
    body = [0xAA, 0x1D, 0x01, *tail]
    return bytes(body + [0] * (20 - len(body)))


class TestFanSwingTailCapture:
    """The client remembers the latest aa1d swing-range tail per device."""

    def test_none_before_any_frame(self):
        """No frame seen yet -> None (caller sends a bare ON)."""
        assert _make_client().fan_swing_tail(FAN_ID) is None

    @pytest.mark.asyncio
    async def test_captures_tail_from_aa1d_frame(self):
        """Bytes 3-6 of an aa 1d frame are retained for the device."""
        client = _make_client()

        await client._handle_message(_message([_aa1d([0x01, 0x06, 0x03, 0x50])]))

        assert client.fan_swing_tail(FAN_ID) == [0x01, 0x06, 0x03, 0x50]

    @pytest.mark.asyncio
    async def test_latest_frame_wins(self):
        """A newer aa 1d frame replaces the stored tail."""
        client = _make_client()

        await client._handle_message(_message([_aa1d([1, 2, 3, 4])]))
        await client._handle_message(_message([_aa1d([5, 6, 7, 8])]))

        assert client.fan_swing_tail(FAN_ID) == [5, 6, 7, 8]

    @pytest.mark.asyncio
    async def test_ignores_other_frames(self):
        """Frames that are not aa 1d (or are too short) are not harvested."""
        client = _make_client()
        other = bytes([0xAA, 0x05, 0x01, 0x06, 0x03, 0x50, 0x03]) + bytes(13)
        short = bytes([0xAA, 0x1D, 0x01, 0x02])

        await client._handle_message(_message([other, short]))

        assert client.fan_swing_tail(FAN_ID) is None

    @pytest.mark.asyncio
    async def test_status_push_captures_tail_and_still_reaches_state_callback(self):
        """The fan's own ``cmd: status`` push (state + op.command) feeds both paths."""
        client = _make_client()
        on_state_update = MagicMock()
        client._on_state_update = on_state_update

        await client._handle_message(
            _message([_aa1d([3, 0x32, 3, 0xE8])], cmd="status", state={"result": 1})
        )

        assert client.fan_swing_tail(FAN_ID) == [3, 0x32, 3, 0xE8]
        on_state_update.assert_called_once()
        assert on_state_update.call_args[0][0] == FAN_ID
        assert on_state_update.call_args[0][1]["result"] == 1

    @pytest.mark.asyncio
    async def test_only_on_off_reports_are_harvested(self):
        """An unknown ``aa 1d 02`` sub-report must not become the replayed arc."""
        client = _make_client()
        other = bytes([0xAA, 0x1D, 0x02, 1, 2, 3, 4]) + bytes(13)

        await client._handle_message(_message([other]))

        assert client.fan_swing_tail(FAN_ID) is None

    @pytest.mark.asyncio
    async def test_non_string_entry_does_not_drop_the_message(self):
        """A non-string op.command entry is skipped; the state still lands."""
        client = _make_client()
        on_state_update = MagicMock()
        client._on_state_update = on_state_update

        await client._handle_message(
            _message([None, 42, _aa1d([9, 9, 9, 9])], cmd="status", state={"onOff": 1})
        )

        assert client.fan_swing_tail(FAN_ID) == [9, 9, 9, 9]
        on_state_update.assert_called_once()

    @pytest.mark.asyncio
    async def test_bad_base64_is_skipped(self):
        """Undecodable entries are skipped without breaking the message."""
        client = _make_client()

        await client._handle_message(_message(["not-base64!!", _aa1d([9, 9, 9, 9])]))

        assert client.fan_swing_tail(FAN_ID) == [9, 9, 9, 9]
        client._handle_multisync.assert_called_once()

    @pytest.mark.asyncio
    async def test_tail_is_per_device(self):
        """Another device's frame does not leak into this fan's tail."""
        client = _make_client()
        message = _message([_aa1d([1, 1, 1, 1])])
        payload = json.loads(message.payload)
        payload["device"] = "11:22:33:44:55:66:77:88"
        message.payload = json.dumps(payload).encode()

        await client._handle_message(message)

        assert client.fan_swing_tail(FAN_ID) is None
        assert client.fan_swing_tail("11:22:33:44:55:66:77:88") == [1, 1, 1, 1]
