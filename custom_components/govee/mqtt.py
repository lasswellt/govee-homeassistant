"""MQTT client for Govee real-time device events.

Govee provides a cloud MQTT broker for push notifications of device events.
This reduces the need for constant API polling and provides real-time updates.

Key features:
- TLS-secured connection to Govee cloud MQTT broker
- Automatic reconnection with exponential backoff
- Event-driven updates pushed to coordinator

Note: MQTT events only cover EVENT-type capabilities (sensors, alerts).
Power state and brightness changes require polling for full state sync.
"""
from __future__ import annotations

import asyncio
import json
import logging
import ssl
from typing import TYPE_CHECKING, Any, Callable

from .const import (
    MQTT_BROKER,
    MQTT_KEEPALIVE,
    MQTT_PORT,
    MQTT_RECONNECT_BASE,
    MQTT_RECONNECT_MAX,
)

if TYPE_CHECKING:
    import aiomqtt

_LOGGER = logging.getLogger(__name__)


class GoveeMqttClient:
    """MQTT client for Govee device events with auto-reconnection.

    Connects to Govee's cloud MQTT broker to receive real-time device events.
    Uses the API key for authentication (both username and password).

    Attributes:
        connected: Whether currently connected to the MQTT broker.
    """

    def __init__(
        self,
        api_key: str,
        on_event: Callable[[dict[str, Any]], None],
    ) -> None:
        """Initialize the MQTT client.

        Args:
            api_key: Govee API key used for authentication.
            on_event: Callback function invoked for each received event.
        """
        self._api_key = api_key
        self._on_event = on_event
        self._running = False
        self._task: asyncio.Task[None] | None = None
        self._connected = False
        self._max_backoff_count = 0

    @property
    def connected(self) -> bool:
        """Return True if connected to MQTT broker."""
        return self._connected

    async def async_start(self) -> None:
        """Start the MQTT connection loop.

        Spawns a background task that maintains the connection with
        automatic reconnection on failure.
        """
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._connection_loop())
        _LOGGER.debug("MQTT client started")

    async def async_stop(self) -> None:
        """Stop the MQTT connection.

        Cancels the connection loop and waits for clean shutdown.
        """
        _LOGGER.debug("Stopping MQTT client")
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        self._connected = False
        _LOGGER.info("MQTT client stopped")

    async def _connection_loop(self) -> None:
        """Maintain MQTT connection with exponential backoff.

        Continuously attempts to maintain a connection to the Govee MQTT broker.
        On connection failure, waits with exponential backoff before retrying.
        """
        try:
            import aiomqtt
        except ImportError:
            _LOGGER.warning(
                "aiomqtt library not available - MQTT events disabled. "
                "Install with: pip install aiomqtt"
            )
            self._connected = False
            return

        reconnect_interval = MQTT_RECONNECT_BASE

        while self._running:
            try:
                tls_context = ssl.create_default_context()

                async with aiomqtt.Client(
                    hostname=MQTT_BROKER,
                    port=MQTT_PORT,
                    username=self._api_key,
                    password=self._api_key,
                    tls_context=tls_context,
                    keepalive=MQTT_KEEPALIVE,
                ) as client:
                    self._connected = True
                    self._max_backoff_count = 0
                    reconnect_interval = MQTT_RECONNECT_BASE
                    _LOGGER.info("Connected to Govee MQTT broker")

                    topic = f"GA/{self._api_key}"
                    await client.subscribe(topic)
                    _LOGGER.debug("Subscribed to Govee events topic")

                    async for message in client.messages:
                        if not self._running:
                            break  # type: ignore[unreachable]
                        await self._handle_message(message)

            except asyncio.CancelledError:
                _LOGGER.debug("MQTT connection loop cancelled")
                raise

            except Exception as err:
                self._connected = False
                # Check if aiomqtt is available for specific error handling
                try:
                    import aiomqtt

                    if isinstance(err, aiomqtt.MqttError):
                        if self._running:
                            _LOGGER.warning(
                                "MQTT connection error: %s. Reconnecting in %ds",
                                err,
                                reconnect_interval,
                            )
                    else:
                        _LOGGER.error("Unexpected MQTT error: %s", err)
                except ImportError:
                    _LOGGER.error("MQTT error: %s", err)

                if self._running:
                    await asyncio.sleep(reconnect_interval)
                    reconnect_interval = min(
                        reconnect_interval * 2, MQTT_RECONNECT_MAX
                    )

                    # Track consecutive max backoff occurrences for diagnostics
                    if reconnect_interval >= MQTT_RECONNECT_MAX:
                        self._max_backoff_count += 1
                        if self._max_backoff_count >= 3:
                            _LOGGER.warning(
                                "MQTT connection failed %d times at max backoff interval. "
                                "Check: 1) Network connectivity to %s:%d, "
                                "2) API key validity, 3) Firewall rules",
                                self._max_backoff_count,
                                MQTT_BROKER,
                                MQTT_PORT,
                            )

        self._connected = False

    async def _handle_message(self, message: aiomqtt.Message) -> None:
        """Handle incoming MQTT message.

        Parses the JSON payload and invokes the event callback.

        Args:
            message: aiomqtt Message object with payload.
        """
        try:
            raw_payload = message.payload
            # aiomqtt.Message.payload is always bytes, decode to string
            payload_str: str = raw_payload.decode() if isinstance(
                raw_payload, bytes
            ) else str(raw_payload)

            event: dict[str, Any] = json.loads(payload_str)
            _LOGGER.debug(
                "MQTT event received for device %s: %s",
                event.get("device"),
                event.get("capabilities"),
            )
            self._on_event(event)

        except json.JSONDecodeError as err:
            _LOGGER.warning("Failed to parse MQTT message: %s", err)
        except Exception as err:
            _LOGGER.error("Error handling MQTT message: %s", err)
