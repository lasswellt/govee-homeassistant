"""AWS IoT MQTT client for Govee real-time device state updates.

Connects to Govee's AWS IoT endpoint to receive push notifications of device
state changes (power, brightness, color). This provides instant state updates
without polling, eliminating the "flipflop" bug from optimistic updates.

PCAP validated endpoint: aqm3wd1qlc3dy-ats.iot.us-east-1.amazonaws.com:8883

Key differences from official Govee MQTT (mqtt.openapi.govee.com):
- AWS IoT provides full state updates (power, brightness, color, temp)
- Official MQTT only provides EVENT capabilities (sensors, alerts)
- AWS IoT requires certificate auth (from login API), not API key
"""
from __future__ import annotations

import asyncio
import json
import logging
import ssl
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

# Import aiomqtt at module level to avoid blocking in event loop
try:
    import aiomqtt

    AIOMQTT_AVAILABLE = True
except ImportError:
    AIOMQTT_AVAILABLE = False

if TYPE_CHECKING:
    from .api.auth import GoveeIotCredentials

_LOGGER = logging.getLogger(__name__)

# AWS IoT connection settings
AWS_IOT_PORT = 8883
AWS_IOT_KEEPALIVE = 60
RECONNECT_BASE = 5
RECONNECT_MAX = 300


class GoveeAwsIotClient:
    """AWS IoT MQTT client for real-time Govee device state updates.

    Receives push notifications for device state changes including:
    - Power state (onOff)
    - Brightness
    - Color (RGB)
    - Color temperature

    Uses certificate-based authentication obtained from Govee login API.
    """

    def __init__(
        self,
        credentials: GoveeIotCredentials,
        on_state_update: Callable[[str, dict[str, Any]], None],
    ) -> None:
        """Initialize the AWS IoT MQTT client.

        Args:
            credentials: IoT credentials from Govee login API
            on_state_update: Callback(device_id, state_dict) for state changes
        """
        self._credentials = credentials
        self._on_state_update = on_state_update
        self._running = False
        self._connected = False
        self._task: asyncio.Task[None] | None = None
        self._temp_dir: tempfile.TemporaryDirectory[str] | None = None
        self._max_backoff_count = 0

    @property
    def connected(self) -> bool:
        """Return True if connected to AWS IoT."""
        return self._connected

    async def async_start(self) -> None:
        """Start the AWS IoT MQTT connection loop.

        Spawns a background task that maintains the connection with
        automatic reconnection on failure.
        """
        if self._running:
            return

        self._running = True
        self._task = asyncio.create_task(self._connection_loop())
        _LOGGER.debug("AWS IoT MQTT client started")

    async def async_stop(self) -> None:
        """Stop the AWS IoT MQTT connection.

        Cancels the connection loop and cleans up temporary certificate files.
        """
        _LOGGER.debug("Stopping AWS IoT MQTT client")
        self._running = False

        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

        # Clean up temp certificate files
        if self._temp_dir:
            try:
                self._temp_dir.cleanup()
            except Exception:
                pass
            self._temp_dir = None

        self._connected = False
        _LOGGER.info("AWS IoT MQTT client stopped")

    def _create_ssl_context_sync(self) -> ssl.SSLContext:
        """Create SSL context with certificate files (synchronous).

        Writes certificate/key to temporary files for SSL context loading.
        aiomqtt requires file paths, not in-memory certificates.

        Note: Cleans up temp directory on failure to prevent resource leaks.
        This method is blocking and should be run in an executor.
        """
        # Clean up any existing temp directory first
        if self._temp_dir:
            try:
                self._temp_dir.cleanup()
            except Exception:
                pass
            self._temp_dir = None

        temp_dir = None
        try:
            # Create temp directory for certificate files
            temp_dir = tempfile.TemporaryDirectory()
            temp_path = Path(temp_dir.name)

            cert_path = temp_path / "cert.pem"
            key_path = temp_path / "key.pem"

            # Write certificate files with restricted permissions (0600)
            cert_path.write_text(self._credentials.iot_cert)
            cert_path.chmod(0o600)
            key_path.write_text(self._credentials.iot_key)
            key_path.chmod(0o600)

            ssl_context = ssl.create_default_context()
            ssl_context.load_cert_chain(str(cert_path), str(key_path))

            # Load CA certificate if provided
            if self._credentials.iot_ca:
                ca_path = temp_path / "ca.pem"
                ca_path.write_text(self._credentials.iot_ca)
                ca_path.chmod(0o600)
                ssl_context.load_verify_locations(str(ca_path))

            # Only store reference after successful creation
            self._temp_dir = temp_dir
            return ssl_context

        except Exception:
            # Clean up temp directory on failure
            if temp_dir:
                try:
                    temp_dir.cleanup()
                except Exception:
                    pass
            raise

    async def _create_ssl_context(self) -> ssl.SSLContext:
        """Create SSL context with certificate files (async wrapper).

        Runs blocking SSL context creation in an executor to avoid
        blocking the event loop.
        """
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._create_ssl_context_sync)

    async def _connection_loop(self) -> None:
        """Maintain AWS IoT MQTT connection with exponential backoff."""
        if not AIOMQTT_AVAILABLE:
            _LOGGER.warning(
                "aiomqtt library not available - AWS IoT MQTT disabled. "
                "Install with: pip install aiomqtt"
            )
            self._connected = False
            return

        reconnect_interval = RECONNECT_BASE

        while self._running:
            try:
                ssl_context = await self._create_ssl_context()

                # aiomqtt 2.0+ uses 'identifier' instead of 'client_id'
                async with aiomqtt.Client(
                    hostname=self._credentials.endpoint,
                    port=AWS_IOT_PORT,
                    identifier=self._credentials.client_id,
                    tls_context=ssl_context,
                    keepalive=AWS_IOT_KEEPALIVE,
                ) as client:
                    self._connected = True
                    self._max_backoff_count = 0
                    reconnect_interval = RECONNECT_BASE
                    _LOGGER.info(
                        "Connected to AWS IoT MQTT at %s",
                        self._credentials.endpoint,
                    )

                    # Subscribe to account topic for all device updates
                    topic = self._credentials.account_topic
                    await client.subscribe(topic)
                    _LOGGER.debug("Subscribed to AWS IoT topic: %s", topic[:30] + "...")

                    async for message in client.messages:
                        if not self._running:
                            break
                        await self._handle_message(message)

            except asyncio.CancelledError:
                _LOGGER.debug("AWS IoT connection loop cancelled")
                raise

            except Exception as err:
                self._connected = False

                if self._running:
                    _LOGGER.warning(
                        "AWS IoT connection error: %s. Reconnecting in %ds",
                        err,
                        reconnect_interval,
                    )
                    await asyncio.sleep(reconnect_interval)
                    reconnect_interval = min(reconnect_interval * 2, RECONNECT_MAX)

                    if reconnect_interval >= RECONNECT_MAX:
                        self._max_backoff_count += 1
                        if self._max_backoff_count >= 3:
                            _LOGGER.error(
                                "AWS IoT connection failed %d times at max backoff. "
                                "Check: 1) Credentials validity, 2) Network connectivity, "
                                "3) Certificate expiration",
                                self._max_backoff_count,
                            )

        self._connected = False

    async def _handle_message(self, message: Any) -> None:
        """Handle incoming AWS IoT MQTT message.

        Message format from PCAP analysis:
        {
            "device": "XX:XX:XX:XX:XX:XX:XX:XX",
            "sku": "H6072",
            "state": {
                "onOff": 1,
                "brightness": 50,
                "color": {"r": 255, "g": 0, "b": 0},
                "colorTemInKelvin": 0
            }
        }
        """
        try:
            raw_payload = message.payload
            payload_str = raw_payload.decode() if isinstance(raw_payload, bytes) else str(raw_payload)

            data = json.loads(payload_str)

            device_id = data.get("device")
            state = data.get("state", {})

            if not device_id:
                _LOGGER.debug("AWS IoT message missing device ID: %s", data)
                return

            _LOGGER.debug(
                "AWS IoT state update for %s: power=%s, brightness=%s",
                device_id,
                state.get("onOff"),
                state.get("brightness"),
            )

            # Invoke callback with device ID and state dict
            self._on_state_update(device_id, state)

        except json.JSONDecodeError as err:
            _LOGGER.warning("Failed to parse AWS IoT message: %s", err)
        except Exception as err:
            _LOGGER.error("Error handling AWS IoT message: %s", err)
