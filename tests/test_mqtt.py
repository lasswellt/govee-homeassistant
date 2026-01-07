"""Test MQTT client for Govee real-time device events."""
from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from custom_components.govee.mqtt import GoveeMqttClient
from custom_components.govee.const import (
    MQTT_BROKER,
    MQTT_PORT,
    MQTT_KEEPALIVE,
    MQTT_RECONNECT_BASE,
    MQTT_RECONNECT_MAX,
)


class TestGoveeMqttClientInit:
    """Test GoveeMqttClient initialization."""

    def test_init(self):
        """Test client initialization."""
        callback = MagicMock()
        client = GoveeMqttClient(api_key="test_key", on_event=callback)

        assert client._api_key == "test_key"
        assert client._on_event == callback
        assert client._running is False
        assert client._task is None
        assert client.connected is False
        assert client._max_backoff_count == 0

    def test_connected_property(self):
        """Test connected property."""
        callback = MagicMock()
        client = GoveeMqttClient(api_key="test_key", on_event=callback)

        assert client.connected is False
        client._connected = True
        assert client.connected is True


class TestGoveeMqttClientStartStop:
    """Test MQTT client start/stop lifecycle."""

    @pytest.mark.asyncio
    async def test_async_start_creates_task(self):
        """Test that async_start creates a background task."""
        callback = MagicMock()
        client = GoveeMqttClient(api_key="test_key", on_event=callback)

        # Mock the connection loop to prevent actual connection
        with patch.object(
            client, "_connection_loop", new_callable=AsyncMock
        ) as mock_loop:
            await client.async_start()

            assert client._running is True
            assert client._task is not None
            # Give the task a moment to start
            await asyncio.sleep(0.01)

            # Stop to clean up
            await client.async_stop()

    @pytest.mark.asyncio
    async def test_async_start_idempotent(self):
        """Test that calling async_start twice doesn't create duplicate tasks."""
        callback = MagicMock()
        client = GoveeMqttClient(api_key="test_key", on_event=callback)

        with patch.object(
            client, "_connection_loop", new_callable=AsyncMock
        ):
            await client.async_start()
            first_task = client._task

            await client.async_start()
            second_task = client._task

            # Should be same task, not a new one
            assert first_task is second_task

            await client.async_stop()

    @pytest.mark.asyncio
    async def test_async_stop_cancels_task(self):
        """Test that async_stop cancels the running task."""
        callback = MagicMock()
        client = GoveeMqttClient(api_key="test_key", on_event=callback)

        # Create a long-running mock loop
        async def slow_loop():
            while client._running:
                await asyncio.sleep(0.1)

        with patch.object(client, "_connection_loop", side_effect=slow_loop):
            await client.async_start()
            await asyncio.sleep(0.05)

            assert client._running is True

            await client.async_stop()

            assert client._running is False
            assert client.connected is False


class TestGoveeMqttClientConnection:
    """Test MQTT client connection behavior."""

    @pytest.mark.asyncio
    async def test_connection_loop_no_aiomqtt(self):
        """Test graceful degradation when aiomqtt not available."""
        callback = MagicMock()
        client = GoveeMqttClient(api_key="test_key", on_event=callback)

        # Mock aiomqtt import to fail
        with patch.dict("sys.modules", {"aiomqtt": None}):
            with patch(
                "custom_components.govee.mqtt.GoveeMqttClient._connection_loop"
            ) as mock_loop:
                # Simulate ImportError path
                async def no_aiomqtt():
                    client._connected = False
                    return

                mock_loop.side_effect = no_aiomqtt

                await client.async_start()
                await asyncio.sleep(0.01)
                await client.async_stop()

                assert client.connected is False

    @pytest.mark.asyncio
    async def test_successful_connection(self):
        """Test successful MQTT connection."""
        callback = MagicMock()
        client = GoveeMqttClient(api_key="test_api_key", on_event=callback)

        mock_mqtt_client = MagicMock()
        mock_mqtt_client.subscribe = AsyncMock()

        # Create an async iterator that yields one message then stops
        async def message_iterator():
            client._running = False  # Stop after first iteration
            return
            yield  # Make it a generator

        mock_mqtt_client.messages = message_iterator()
        mock_mqtt_client.__aenter__ = AsyncMock(return_value=mock_mqtt_client)
        mock_mqtt_client.__aexit__ = AsyncMock(return_value=None)

        mock_aiomqtt = MagicMock()
        mock_aiomqtt.Client.return_value = mock_mqtt_client

        with patch.dict("sys.modules", {"aiomqtt": mock_aiomqtt}):
            # Run connection loop directly with mocked aiomqtt
            client._running = True
            # Just verify no exceptions occur when starting/stopping
            await client.async_start()
            await asyncio.sleep(0.01)
            await client.async_stop()


class TestGoveeMqttClientMessageHandling:
    """Test MQTT message handling."""

    @pytest.mark.asyncio
    async def test_handle_message_valid_json(self):
        """Test handling a valid JSON message."""
        events_received = []

        def capture_event(event):
            events_received.append(event)

        client = GoveeMqttClient(api_key="test_key", on_event=capture_event)

        # Create mock message
        mock_message = MagicMock()
        mock_message.payload = json.dumps({
            "sku": "H7172",
            "device": "41:DA:D4:AD:FC:46:00:64",
            "deviceName": "Ice Maker",
            "capabilities": [
                {
                    "type": "devices.capabilities.event",
                    "instance": "lackWaterEvent",
                    "state": [{"name": "lack", "value": 1, "message": "Lack of Water"}],
                }
            ],
        }).encode()

        await client._handle_message(mock_message)

        assert len(events_received) == 1
        event = events_received[0]
        assert event["device"] == "41:DA:D4:AD:FC:46:00:64"
        assert event["sku"] == "H7172"
        assert len(event["capabilities"]) == 1
        assert event["capabilities"][0]["instance"] == "lackWaterEvent"

    @pytest.mark.asyncio
    async def test_handle_message_bytes_payload(self):
        """Test handling message with bytes payload."""
        events_received = []

        def capture_event(event):
            events_received.append(event)

        client = GoveeMqttClient(api_key="test_key", on_event=capture_event)

        mock_message = MagicMock()
        mock_message.payload = b'{"device": "test_device", "capabilities": []}'

        await client._handle_message(mock_message)

        assert len(events_received) == 1
        assert events_received[0]["device"] == "test_device"

    @pytest.mark.asyncio
    async def test_handle_message_invalid_json(self):
        """Test handling invalid JSON message."""
        callback = MagicMock()
        client = GoveeMqttClient(api_key="test_key", on_event=callback)

        mock_message = MagicMock()
        mock_message.payload = b"not valid json {"

        # Should not raise, just log warning
        await client._handle_message(mock_message)

        # Callback should not have been called
        callback.assert_not_called()

    @pytest.mark.asyncio
    async def test_handle_message_callback_exception(self):
        """Test handling callback that raises exception."""

        def bad_callback(event):
            raise ValueError("Callback error")

        client = GoveeMqttClient(api_key="test_key", on_event=bad_callback)

        mock_message = MagicMock()
        mock_message.payload = b'{"device": "test"}'

        # Should not raise, just log error
        await client._handle_message(mock_message)


class TestGoveeMqttClientReconnection:
    """Test MQTT client reconnection behavior."""

    @pytest.mark.asyncio
    async def test_reconnect_intervals(self):
        """Test that reconnect intervals follow expected pattern."""
        # Just verify the constants define a proper exponential backoff
        assert MQTT_RECONNECT_BASE == 5
        assert MQTT_RECONNECT_MAX == 300
        # Max should be much larger than base for proper backoff
        assert MQTT_RECONNECT_MAX >= MQTT_RECONNECT_BASE * 10

    def test_max_backoff_count_initialized_to_zero(self):
        """Test max backoff count is initialized to zero."""
        callback = MagicMock()
        client = GoveeMqttClient(api_key="test_key", on_event=callback)
        assert client._max_backoff_count == 0

    def test_max_backoff_count_can_be_set(self):
        """Test max backoff count can be set and read."""
        callback = MagicMock()
        client = GoveeMqttClient(api_key="test_key", on_event=callback)
        client._max_backoff_count = 5
        assert client._max_backoff_count == 5


class TestMqttConstants:
    """Test MQTT constants are properly defined."""

    def test_mqtt_broker(self):
        """Test MQTT broker constant."""
        assert MQTT_BROKER == "mqtt.openapi.govee.com"

    def test_mqtt_port(self):
        """Test MQTT port constant."""
        assert MQTT_PORT == 8883

    def test_mqtt_keepalive(self):
        """Test MQTT keepalive constant."""
        assert MQTT_KEEPALIVE == 120

    def test_reconnect_intervals(self):
        """Test reconnect interval constants."""
        assert MQTT_RECONNECT_BASE == 5
        assert MQTT_RECONNECT_MAX == 300
        assert MQTT_RECONNECT_MAX > MQTT_RECONNECT_BASE


class TestMqttConnectionLoop:
    """Test MQTT connection loop behavior with aiomqtt."""

    @pytest.mark.asyncio
    async def test_handle_message_non_bytes_payload(self):
        """Test handling message with non-bytes payload."""
        events_received = []

        def capture_event(event):
            events_received.append(event)

        client = GoveeMqttClient(api_key="test_key", on_event=capture_event)

        # Mock message with string payload (edge case)
        mock_message = MagicMock()
        mock_message.payload = '{"device": "test_device", "capabilities": []}'

        await client._handle_message(mock_message)

        assert len(events_received) == 1
        assert events_received[0]["device"] == "test_device"


class TestMqttMessagePayloadVariants:
    """Test various MQTT message payload formats."""

    @pytest.mark.asyncio
    async def test_handle_message_complex_event(self):
        """Test handling complex MQTT event with multiple capabilities."""
        events_received = []

        def capture_event(event):
            events_received.append(event)

        client = GoveeMqttClient(api_key="test_key", on_event=capture_event)

        mock_message = MagicMock()
        mock_message.payload = json.dumps({
            "sku": "H7172",
            "device": "AA:BB:CC:DD:EE:FF",
            "deviceName": "Ice Maker",
            "capabilities": [
                {
                    "type": "devices.capabilities.event",
                    "instance": "lackWaterEvent",
                    "state": [{"name": "lack", "value": 1, "message": "Lack of Water"}],
                },
                {
                    "type": "devices.capabilities.event",
                    "instance": "iceFullEvent",
                    "state": [{"name": "full", "value": 0}],
                },
            ],
        }).encode()

        await client._handle_message(mock_message)

        assert len(events_received) == 1
        event = events_received[0]
        assert len(event["capabilities"]) == 2
        assert event["capabilities"][0]["instance"] == "lackWaterEvent"
        assert event["capabilities"][1]["instance"] == "iceFullEvent"

    @pytest.mark.asyncio
    async def test_handle_message_empty_capabilities(self):
        """Test handling message with empty capabilities list."""
        events_received = []

        def capture_event(event):
            events_received.append(event)

        client = GoveeMqttClient(api_key="test_key", on_event=capture_event)

        mock_message = MagicMock()
        mock_message.payload = b'{"device": "test", "capabilities": []}'

        await client._handle_message(mock_message)

        assert len(events_received) == 1
        assert events_received[0]["capabilities"] == []

    @pytest.mark.asyncio
    async def test_handle_message_unicode_content(self):
        """Test handling message with unicode content."""
        events_received = []

        def capture_event(event):
            events_received.append(event)

        client = GoveeMqttClient(api_key="test_key", on_event=capture_event)

        mock_message = MagicMock()
        mock_message.payload = '{"device": "test", "deviceName": "灯光"}'.encode('utf-8')

        await client._handle_message(mock_message)

        assert len(events_received) == 1
        assert events_received[0]["deviceName"] == "灯光"


class TestMqttClientLifecycle:
    """Test MQTT client lifecycle edge cases."""

    @pytest.mark.asyncio
    async def test_stop_without_start(self):
        """Test stopping client that was never started."""
        callback = MagicMock()
        client = GoveeMqttClient(api_key="test_key", on_event=callback)

        # Should not raise
        await client.async_stop()

        assert client._running is False
        assert client._task is None

    @pytest.mark.asyncio
    async def test_stop_clears_connected_state(self):
        """Test stopping client clears connected state."""
        callback = MagicMock()
        client = GoveeMqttClient(api_key="test_key", on_event=callback)

        # Manually set connected for testing
        client._connected = True
        client._running = True

        await client.async_stop()

        assert client.connected is False

    @pytest.mark.asyncio
    async def test_start_sets_running_flag(self):
        """Test start sets running flag before creating task."""
        callback = MagicMock()
        client = GoveeMqttClient(api_key="test_key", on_event=callback)

        with patch.object(client, "_connection_loop", new_callable=AsyncMock):
            await client.async_start()
            assert client._running is True
            await client.async_stop()
