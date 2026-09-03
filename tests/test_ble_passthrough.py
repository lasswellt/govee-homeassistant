"""Tests for the BLE passthrough manager's Tower Fan oscillation path.

The Tower Fan 2 family (H7105/H7107) ignores the Developer-API
``oscillationToggle``; the sweep motor only obeys raw ptReal / multiSync
frames on the AWS IoT session (homebridge-govee ``fan-H7107.js``).
"""

from __future__ import annotations

import base64
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.govee.ble_passthrough import BlePassthroughManager

DEVICE_ID = "AA:BB:CC:DD:EE:FF:00:44"
SKU = "H7107"
TOPIC = "GD/device-topic"
# Hardware-confirmed OFF frame from homebridge-govee lib/device/fan-H7107.js.
HOMEBRIDGE_OFF_B64 = "Mx0AAAAAAAAAAAAAAAAAAAAAAC4="


def _make_client() -> MagicMock:
    """Build a fake AWS IoT client with async publish methods."""
    client = MagicMock()
    client.connected = True
    client.async_publish_ptreal = AsyncMock(return_value=True)
    client.async_publish_command = AsyncMock(return_value=True)
    return client


def _make_manager(client: MagicMock | None) -> BlePassthroughManager:
    """Build a manager wired to the given (possibly absent) client."""
    return BlePassthroughManager(
        get_mqtt_client=lambda: client,
        device_topics={DEVICE_ID: TOPIC},
        ensure_device_topic=AsyncMock(return_value=TOPIC),
    )


class TestSendFanOscillation:
    """async_send_fan_oscillation publishes ptReal + multiSync twin."""

    @pytest.mark.asyncio
    async def test_off_sends_homebridge_frame_and_multisync_twin(self):
        """OFF publishes the byte-exact homebridge frame plus its 0x3a twin."""
        client = _make_client()
        manager = _make_manager(client)

        result = await manager.async_send_fan_oscillation(DEVICE_ID, SKU, False)

        assert result is True
        client.async_publish_ptreal.assert_awaited_once_with(
            DEVICE_ID, SKU, HOMEBRIDGE_OFF_B64, TOPIC
        )
        client.async_publish_command.assert_awaited_once()
        topic, cmd, payload = client.async_publish_command.call_args[0]
        assert topic == TOPIC
        assert cmd == "multiSync"
        assert payload["device"] == DEVICE_ID
        assert payload["sku"] == SKU
        twin = base64.b64decode(payload["command"][0])
        assert twin[:3] == bytes([0x3A, 0x1D, 0x00])

    @pytest.mark.asyncio
    async def test_on_carries_swing_tail_in_both_frames(self):
        """ON replays the harvested swing-range tail on ptReal and multiSync."""
        client = _make_client()
        manager = _make_manager(client)
        tail = [0x01, 0x06, 0x03, 0x50]

        result = await manager.async_send_fan_oscillation(DEVICE_ID, SKU, True, tail)

        assert result is True
        ptreal_b64 = client.async_publish_ptreal.call_args[0][2]
        ptreal = base64.b64decode(ptreal_b64)
        assert ptreal[:7] == bytes([0x33, 0x1D, 0x01, 0x01, 0x06, 0x03, 0x50])
        payload = client.async_publish_command.call_args[0][2]
        twin = base64.b64decode(payload["command"][0])
        assert twin[:7] == bytes([0x3A, 0x1D, 0x01, 0x01, 0x06, 0x03, 0x50])

    @pytest.mark.asyncio
    async def test_no_client_returns_false(self):
        """Without an MQTT client nothing is sent and False lets REST run."""
        manager = _make_manager(None)

        assert await manager.async_send_fan_oscillation(DEVICE_ID, SKU, False) is False

    @pytest.mark.asyncio
    async def test_ptreal_result_propagates(self):
        """The ptReal publish result is the return value."""
        client = _make_client()
        client.async_publish_ptreal.return_value = False
        manager = _make_manager(client)

        assert await manager.async_send_fan_oscillation(DEVICE_ID, SKU, True) is False

    @pytest.mark.asyncio
    async def test_multisync_failure_is_non_fatal(self):
        """A failing multiSync twin does not mask a successful ptReal send."""
        client = _make_client()
        client.async_publish_command.side_effect = RuntimeError("twin failed")
        manager = _make_manager(client)

        result = await manager.async_send_fan_oscillation(DEVICE_ID, SKU, False)

        assert result is True
        client.async_publish_ptreal.assert_awaited_once()
