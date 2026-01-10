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
    import aiomqtt  # type: ignore[import-not-found]

    AIOMQTT_AVAILABLE = True
except ImportError:
    AIOMQTT_AVAILABLE = False

if TYPE_CHECKING:
    from .auth import GoveeIotCredentials

_LOGGER = logging.getLogger(__name__)

# AWS IoT connection settings
AWS_IOT_PORT = 8883
AWS_IOT_KEEPALIVE = 120
RECONNECT_BASE = 5
RECONNECT_MAX = 300
CONNECTION_TIMEOUT = 60

# Amazon Root CA 1 - Required for AWS IoT server certificate verification
# Source: https://www.amazontrust.com/repository/AmazonRootCA1.pem
AMAZON_ROOT_CA1 = """-----BEGIN CERTIFICATE-----
MIIDQTCCAimgAwIBAgITBmyfz5m/jAo54vB4ikPmljZbyjANBgkqhkiG9w0BAQsF
ADA5MQswCQYDVQQGEwJVUzEPMA0GA1UEChMGQW1hem9uMRkwFwYDVQQDExBBbWF6
b24gUm9vdCBDQSAxMB4XDTE1MDUyNjAwMDAwMFoXDTM4MDExNzAwMDAwMFowOTEL
MAkGA1UEBhMCVVMxDzANBgNVBAoTBkFtYXpvbjEZMBcGA1UEAxMQQW1hem9uIFJv
b3QgQ0EgMTCCASIwDQYJKoZIhvcNAQEBBQADggEPADCCAQoCggEBALJ4gHHKeNXj
ca9HgFB0fW7Y14h29Jlo91ghYPl0hAEvrAIthtOgQ3pOsqTQNroBvo3bSMgHFzZM
9O6II8c+6zf1tRn4SWiw3te5djgdYZ6k/oI2peVKVuRF4fn9tBb6dNqcmzU5L/qw
IFAGbHrQgLKm+a/sRxmPUDgH3KKHOVj4utWp+UhnMJbulHheb4mjUcAwhmahRWa6
VOujw5H5SNz/0egwLX0tdHA114gk957EWW67c4cX8jJGKLhD+rcdqsq08p8kDi1L
93FcXmn/6pUCyziKrlA4b9v7LWIbxcceVOF34GfID5yHI9Y/QCB/IIDEgEw+OyQm
jgSubJrIqg0CAwEAAaNCMEAwDwYDVR0TAQH/BAUwAwEB/zAOBgNVHQ8BAf8EBAMC
AYYwHQYDVR0OBBYEFIQYzIU07LwMlJQuCFmcx7IQTgoIMA0GCSqGSIb3DQEBCwUA
A4IBAQCY8jdaQZChGsV2USggNiMOruYou6r4lK5IpDB/G/wkjUu0yKGX9rbxenDI
U5PMCCjjmCXPI6T53iHTfIUJrU6adTrCC2qJeHZERxhlbI1Bjjt/msv0tadQ1wUs
N+gDS63pYaACbvXy8MWy7Vu33PqUXHeeE6V/Uq2V8viTO96LXFvKWlJbYK8U90vv
o/ufQJVtMVT8QtPHRh8jrdkPSHCa2XV4cdFyQzR1bldZwgJcJmApzyMZFo6IQ6XU
5MsI+yMRQ+hDKXJioaldXgjUkK642M4UwtBV8ob2xJNDd2ZhwLnoQdeXeGADbkpy
rqXRfboQnoZsG4q5WTP468SQvvG5
-----END CERTIFICATE-----"""


# Type for state update callback
StateUpdateCallback = Callable[[str, dict[str, Any]], None]


class GoveeAwsIotClient:
    """AWS IoT MQTT client for real-time Govee device state updates.

    Receives push notifications for device state changes including:
    - Power state (onOff)
    - Brightness
    - Color (RGB)
    - Color temperature

    Uses certificate-based authentication obtained from Govee login API.

    Usage:
        client = GoveeAwsIotClient(credentials, on_state_update)
        await client.async_start()
        # ... receives updates via callback ...
        await client.async_stop()
    """

    def __init__(
        self,
        credentials: GoveeIotCredentials,
        on_state_update: StateUpdateCallback,
    ) -> None:
        """Initialize the AWS IoT MQTT client.

        Args:
            credentials: IoT credentials from Govee login API.
            on_state_update: Callback(device_id, state_dict) for state changes.
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

    @property
    def available(self) -> bool:
        """Return True if MQTT library is available."""
        return AIOMQTT_AVAILABLE

    async def async_start(self) -> None:
        """Start the AWS IoT MQTT connection loop.

        Spawns a background task that maintains the connection with
        automatic reconnection on failure.
        """
        if not AIOMQTT_AVAILABLE:
            _LOGGER.warning(
                "aiomqtt library not available - AWS IoT MQTT disabled. "
                "Install with: pip install aiomqtt"
            )
            return

        if self._running:
            return

        self._running = True
        self._task = asyncio.create_task(self._connection_loop())
        _LOGGER.debug("AWS IoT MQTT client started")

    async def async_stop(self) -> None:
        """Stop the AWS IoT MQTT connection.

        Cancels the connection loop and cleans up temporary certificate files.
        Cleanup is run in executor to avoid blocking the event loop.
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

        # Clean up temp certificate files in executor to avoid blocking
        if self._temp_dir:
            temp_dir = self._temp_dir
            self._temp_dir = None
            try:
                loop = asyncio.get_event_loop()
                await loop.run_in_executor(None, temp_dir.cleanup)
            except Exception:
                pass

        self._connected = False
        _LOGGER.info("AWS IoT MQTT client stopped")

    def _create_ssl_context_sync(self) -> ssl.SSLContext:
        """Create SSL context with certificate files (synchronous).

        Configures mutual TLS authentication for AWS IoT:
        - Loads Amazon Root CA for server verification
        - Loads client certificate and key for client authentication
        - Enforces TLS 1.2+ as required by AWS IoT

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

            # Write certificate files with restricted permissions
            cert_path.write_text(self._credentials.iot_cert)
            cert_path.chmod(0o600)
            key_path.write_text(self._credentials.iot_key)
            key_path.chmod(0o600)

            # Create SSL context for mutual TLS with AWS IoT
            ssl_context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            ssl_context.minimum_version = ssl.TLSVersion.TLSv1_2
            ssl_context.verify_mode = ssl.CERT_REQUIRED
            ssl_context.check_hostname = True

            # Load Amazon Root CA for server certificate verification
            ssl_context.load_verify_locations(cadata=AMAZON_ROOT_CA1)

            # Load client certificate and private key for mutual TLS
            ssl_context.load_cert_chain(str(cert_path), str(key_path))

            _LOGGER.debug("SSL context created for AWS IoT MQTT")

            # Store reference after successful creation
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

        Runs blocking SSL context creation in an executor.
        """
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._create_ssl_context_sync)

    async def _connection_loop(self) -> None:
        """Maintain AWS IoT MQTT connection with exponential backoff."""
        reconnect_interval = RECONNECT_BASE

        while self._running:
            try:
                ssl_context = await self._create_ssl_context()

                _LOGGER.debug(
                    "Connecting to AWS IoT: %s:%d",
                    self._credentials.endpoint,
                    AWS_IOT_PORT,
                )

                async with aiomqtt.Client(
                    hostname=self._credentials.endpoint,
                    port=AWS_IOT_PORT,
                    identifier=self._credentials.client_id,
                    tls_context=ssl_context,
                    keepalive=AWS_IOT_KEEPALIVE,
                    timeout=CONNECTION_TIMEOUT,
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
                    _LOGGER.debug("Subscribed to topic: %s", topic[:30] + "...")

                    async for message in client.messages:
                        if not self._running:
                            break  # type: ignore[unreachable]
                        await self._handle_message(message)

            except asyncio.CancelledError:
                _LOGGER.debug("AWS IoT connection loop cancelled")
                raise

            except Exception as err:
                self._connected = False

                if self._running:
                    _LOGGER.warning(
                        "AWS IoT connection error (%s): %s. Reconnecting in %ds",
                        type(err).__name__,
                        err,
                        reconnect_interval,
                    )

                    await asyncio.sleep(reconnect_interval)
                    reconnect_interval = min(reconnect_interval * 2, RECONNECT_MAX)

                    if reconnect_interval >= RECONNECT_MAX:
                        self._max_backoff_count += 1
                        if self._max_backoff_count >= 3:
                            _LOGGER.error(
                                "AWS IoT connection failed %d times at max backoff",
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
                _LOGGER.debug("AWS IoT message missing device ID")
                return

            _LOGGER.debug(
                "MQTT state update for %s: power=%s, brightness=%s",
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
