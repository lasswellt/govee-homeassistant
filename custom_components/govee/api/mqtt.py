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
import base64
import binascii
import json
import logging
import ssl
import tempfile
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

# Import aiomqtt at module level to avoid blocking in event loop
try:
    import aiomqtt

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
MAX_RECONNECT_ATTEMPTS = 50

# --- multiSync 0xEE 0x34 sub-device frames ------------------------------------
#
# A leak hub (H5043/H5044) wraps its BLE sub-devices' reports in ``multiSync``
# 0xEE 0x34 frames. Byte 3 identifies the sub-device class, and it is the ONLY
# byte separating a leak report from a thermometer report — both share the
# 0xEE 0x34 header and the byte-5 battery slot:
#
#   leak (H5054/H5058/H5059):  ee 34 <slot> 02 00 64 ...   (issue #87)
#   thermo (H5310 via H5044):  ee 34 <slot> 08 00 64 ...   (issues #151, #157)
#
# Only the exact thermo signature is diverted; anything else keeps the leak
# path, so an unknown leak SKU can never be silenced by this discrimination.
MULTISYNC_SUBTYPE_THERMO = 0x08

# Thermo frame temperature encoding (issue #151).
#
#   T[°C] = (byte13 + THERMO_TEMP_OFFSET) / 10
#
# Established by @Araknus13 against 30 on-the-hour frames paired with the
# cloud reading they produced, spanning 24.4-29.5 °C over 31 hours: least
# squares gives T = 0.10010 * b13 + 11.1647, reproducing every point within
# 0.1 K. Two earlier candidates from the single-labelled-point capture in #157
# (°F + 113 and °C + 170) miss the same data by 19.3 K and 38.1 K — they had
# been indistinguishable only because they intersect at 31.25 °C, right where
# that lone reading sat.
#
# The byte is unsigned, so the representable span is 11.2-36.7 °C, and the
# capture only spans 24-30 °C. Both endpoints are treated as sentinels rather
# than readings (see _decode_thermo_frame) since the rest of the frame uses
# 0x00/0xFF the same way.
THERMO_TEMP_OFFSET = 112
THERMO_TEMP_SCALE = 10.0

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
GiveUpCallback = Callable[[int, str], None]
"""Invoked when the reconnect loop exhausts MAX_RECONNECT_ATTEMPTS.
Args: (attempts_made, last_error_message)."""


def _decode_thermo_frame(raw: bytes) -> dict[str, Any] | None:
    """Decode a gateway-bridged thermometer report (issue #151).

    Frame layout, from 64 frames in the #157 diagnostics and 44 in #151::

        ee 34 00 08 00 64 29 15 c2 6a 7e ca 3c 89 ba cc ff 80 00 2a
        └──┬──┘ │  │  │  └──┬──┘ └────┬────┘ │  └────┬────┘ └─┬─┘ │
         header │  │  │   per-dev   epoch    │    per-dev    ?  cksum
                │  │  battery      seconds   temperature
                │  sub-device class (0x08)
                slot (sno on the hub)

    Bytes 9-12 are a big-endian Unix timestamp — on the #151 capture it tracks
    receive time to within 3 seconds across all 44 frames, which is what makes
    it usable as a staleness check rather than just a decoded curiosity.

    Args:
        raw: The decoded multiSync packet.

    Returns:
        ``{"sensor_slot", "temperature_c", "battery", "frame_ts"}``, or None if
        the frame is too short or byte 13 reads as a sentinel rather than a
        temperature.
    """
    if len(raw) < 14:
        return None

    temp_byte = raw[13]
    # 0x00 / 0xFF are the frame's own no-data markers (byte 16 sits at 0xFF
    # permanently on the temperature-only H5310, and BFF reports its absent
    # humidity as 0xFFFF). Decoding them would surface a confident 11.2 °C or
    # 36.7 °C, which is worse than reporting nothing.
    if temp_byte in (0x00, 0xFF):
        return None

    battery = raw[5] if raw[5] <= 100 else None
    frame_ts = int.from_bytes(raw[9:13], "big") if len(raw) >= 13 else None

    return {
        "sensor_slot": raw[2],
        "temperature_c": (temp_byte + THERMO_TEMP_OFFSET) / THERMO_TEMP_SCALE,
        "battery": battery,
        "frame_ts": frame_ts,
    }


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
        on_give_up: GiveUpCallback | None = None,
    ) -> None:
        """Initialize the AWS IoT MQTT client.

        Args:
            credentials: IoT credentials from Govee login API.
            on_state_update: Callback(device_id, state_dict) for state changes.
            on_give_up: Optional callback fired when the reconnect loop
                exhausts MAX_RECONNECT_ATTEMPTS. Use to surface a repair
                issue so the user can intervene (e.g., reload integration).
        """
        self._credentials = credentials
        self._on_state_update = on_state_update
        self._on_give_up = on_give_up
        self._running = False
        self._connected = False
        self._task: asyncio.Task[None] | None = None
        self._temp_dir: tempfile.TemporaryDirectory[str] | None = None
        self._max_backoff_count = 0
        self._client: Any | None = None  # aiomqtt.Client when connected
        # Last raw state payload seen per device, retained for diagnostics so a
        # dump shows exactly what AWS IoT pushed (redacted at dump time).
        self._last_messages: dict[str, dict[str, Any]] = {}
        # UTC timestamp of the most recent inbound MQTT state message.
        self._last_message_ts: datetime | None = None
        # UTC timestamp of the most recent inbound MQTT message per device_id.
        self._last_message_per_device: dict[str, datetime] = {}
        # Ring buffer of recent decoded multiSync packets (leak/button/unknown),
        # retained for diagnostics so a download alone is enough to crack
        # undecoded hub packets (e.g. the H5059's 0xEE 0x35 wet alarm in #87)
        # without asking end users to enable verbose logging.
        self._recent_multisync: deque[dict[str, Any]] = deque(maxlen=64)

    @property
    def last_messages(self) -> dict[str, dict[str, Any]]:
        """Most recent raw MQTT state payload per device_id (diagnostics)."""
        return self._last_messages

    @property
    def recent_multisync(self) -> list[dict[str, Any]]:
        """Recent multiSync packets (hex + decode) for leak-sensor diagnostics."""
        return list(self._recent_multisync)

    @property
    def last_message_ts(self) -> datetime | None:
        """UTC timestamp of the most recent inbound MQTT state message."""
        return self._last_message_ts

    def last_message_ts_for(self, device_id: str) -> datetime | None:
        """UTC timestamp of the most recent inbound MQTT message for a device."""
        return self._last_message_per_device.get(device_id)

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
                loop = asyncio.get_running_loop()
                await loop.run_in_executor(None, temp_dir.cleanup)
            except Exception as err:
                _LOGGER.debug("Temp dir cleanup: %s", err)

        self._client = None
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
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self._create_ssl_context_sync)

    async def _connection_loop(self) -> None:
        """Maintain AWS IoT MQTT connection with exponential backoff."""
        reconnect_interval = RECONNECT_BASE
        reconnect_attempts = 0

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
                    self._client = client
                    self._connected = True
                    self._max_backoff_count = 0
                    reconnect_interval = RECONNECT_BASE
                    reconnect_attempts = 0

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

                    self._client = None

            except asyncio.CancelledError:
                _LOGGER.debug("AWS IoT connection loop cancelled")
                raise

            except Exception as err:
                self._client = None
                self._connected = False

                if self._running:
                    reconnect_attempts += 1

                    if reconnect_attempts >= MAX_RECONNECT_ATTEMPTS:
                        _LOGGER.error(
                            "AWS IoT connection failed after %d attempts, giving up",
                            reconnect_attempts,
                        )
                        if self._on_give_up is not None:
                            try:
                                self._on_give_up(reconnect_attempts, str(err))
                            except Exception as cb_err:  # pragma: no cover
                                _LOGGER.warning("give-up callback raised: %s", cb_err)
                        self._running = False
                        break

                    _LOGGER.warning(
                        "AWS IoT connection error (%s): %s. "
                        "Reconnecting in %ds (attempt %d/%d)",
                        type(err).__name__,
                        err,
                        reconnect_interval,
                        reconnect_attempts,
                        MAX_RECONNECT_ATTEMPTS,
                    )

                    await asyncio.sleep(reconnect_interval)
                    reconnect_interval = min(reconnect_interval * 2, RECONNECT_MAX)

        self._connected = False

    async def _handle_message(self, message: Any) -> None:
        """Handle incoming AWS IoT MQTT message.

        Message format from PCAP analysis (state updates):
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

        multiSync format (leak sensor events from H5043 hub):
        {
            "sku": "H5043",
            "device": "XX:XX:XX:XX:XX:XX:XX:XX",
            "cmd": "multiSync",
            "op": {"command": ["base64_encoded_20_byte_packet"]}
        }

        Command responses and other messages are silently ignored.
        """
        try:
            raw_payload = message.payload
            # Older strips (e.g. H6117/H6163) push payloads with non-UTF-8 bytes
            # — accented characters in scene/DIY/device names (0xb0 '°', 0xfc
            # 'ü' in latin-1). Strict decode raised UnicodeDecodeError, dropping
            # the ENTIRE state message and breaking on/off feedback (#98).
            # errors="replace" keeps the JSON parseable; a replaced char only
            # ever lands inside a string value (a name), never structural JSON.
            payload_str = (
                raw_payload.decode("utf-8", errors="replace")
                if isinstance(raw_payload, bytes)
                else str(raw_payload)
            )

            # Log every inbound account-topic message before any filtering so a
            # debug capture shows exactly what arrives — used to determine
            # whether standalone water detectors (H5054, issue #62) ever push a
            # trip on the account topic, since they have no per-device topic.
            topic = getattr(message, "topic", "?")
            _LOGGER.debug(
                "AWS IoT inbound topic=%s payload=%s",
                topic,
                payload_str[:500],
            )

            data = json.loads(payload_str)

            # Ignore command messages (our own publishes or responses)
            if "msg" in data:
                _LOGGER.debug("Ignoring command/response message")
                return

            device_id = data.get("device")

            # Only process messages with device ID
            if not device_id:
                _LOGGER.debug("AWS IoT message missing device ID, ignoring")
                return

            # Any accepted inbound device message proves the MQTT transport is
            # live — stamp freshness before branching on message type so leak
            # events (multiSync) count as activity, not just state updates.
            self._last_message_ts = datetime.now(timezone.utc)
            self._last_message_per_device[device_id] = self._last_message_ts

            # Handle multiSync messages (leak sensor events)
            cmd = data.get("cmd")
            if cmd == "multiSync":
                self._handle_multisync(device_id, data)
                return

            state = data.get("state", {})

            if not state:
                _LOGGER.debug(
                    "AWS IoT message missing state for %s, ignoring", device_id
                )
                return

            _LOGGER.debug(
                "MQTT state update for %s: power=%s, brightness=%s",
                device_id,
                state.get("onOff"),
                state.get("brightness"),
            )

            self._last_messages[device_id] = state

            # Invoke callback with device ID and state dict
            try:
                self._on_state_update(device_id, state)
            except Exception as err:
                _LOGGER.error("State update callback failed for %s: %s", device_id, err)

        except json.JSONDecodeError as err:
            _LOGGER.warning("Failed to parse AWS IoT message: %s", err)
        except Exception as err:
            _LOGGER.error("Error handling AWS IoT message: %s", err)

    def _record_multisync(self, hub_device_id: str, raw: bytes) -> None:
        """Append a decoded multiSync packet to the diagnostics ring buffer.

        Stores the packet hex so undecoded hub subtypes can be reverse-
        engineered from a diagnostics download. Button-press packets (0xEE
        0x32) embed the sensor MAC in bytes 2-9; those bytes are masked so the
        retained hex stays PII-free. The hub id is stored under the
        ``hub_device_id`` key so the diagnostics redactor scrubs it.
        """
        header = raw[:2].hex() if len(raw) >= 2 else raw.hex()
        safe = bytearray(raw)
        # Mask the MAC embedded in button-press payloads (bytes 2-9).
        if len(raw) >= 10 and raw[0] == 0xEE and raw[1] == 0x32:
            for i in range(2, 10):
                safe[i] = 0
        self._recent_multisync.append(
            {
                "ts": datetime.now(timezone.utc).isoformat(),
                "hub_device_id": hub_device_id,
                "header": header,
                "length": len(raw),
                "hex": safe.hex(),
            }
        )

    def _handle_multisync(self, hub_device_id: str, data: dict[str, Any]) -> None:
        """Handle multiSync messages from hub devices (e.g., H5043 leak hub).

        Decodes BLE-format packets in op.command[] to extract leak sensor events.
        Packet format (20 bytes):
        - byte 0: 0xEE (sensor report header)
        - byte 1: 0x34 = leak/dry event, 0x32 = button press
        - byte 2: sensor slot (sno) on hub
        - byte 5: battery % (e.g. 0x64=100) — NOT wet; legacy decoders misread it
        - bytes 14, 16: probe state (0x01 = wet, 0x00 = dry); byte 15 = 0x03 sep

        H5059 (issue #87) reports wet in bytes 14/16. Earlier SKUs were decoded
        off byte 5; the wet check ORs both so no model loses detection (a leak
        sensor must never under-report). See
        docs/_research/2026-06-04_h5059-h5044-leak-sensor-support.md.
        """
        op = data.get("op", {})
        commands = op.get("command", [])

        for cmd_b64 in commands:
            try:
                raw = base64.b64decode(cmd_b64)
            except (binascii.Error, ValueError):
                _LOGGER.debug("Failed to decode multiSync command base64")
                continue

            # Record every decoded packet (incl. short/unknown) for diagnostics
            # before any type filtering, so a downloaded dump can crack packet
            # subtypes the current decoder doesn't handle yet.
            self._record_multisync(hub_device_id, raw)

            if len(raw) < 6:
                continue

            # mmWave presence report (H5127, issue #124): 0xAA 0x01 frames.
            # Byte layout (ultimate-govee presence.state.ts + real captures in
            # homebridge-govee #840): byte 2 = mmWave detected, bytes 3-4 =
            # distance cm, byte 5 = biological detected, bytes 8-11 = duration,
            # byte 16 = overall occupancy flag — tracks the ``status`` push's
            # ``triSta`` exactly. Absence arrives ONLY as this multiSync frame
            # (with the flags cleared); it never gets a triSta status push.
            if raw[0] == 0xAA:
                if raw[1] == 0x01:
                    if len(raw) >= 17:
                        presence_val = 1 if raw[16] == 0x01 else 0
                    else:
                        presence_val = 1 if raw[2] == 0x01 else 0
                    _LOGGER.debug(
                        "Presence report from %s: presence=%d (frame=%s)",
                        hub_device_id,
                        presence_val,
                        raw[:4].hex(),
                    )
                    # Route through the normal state-update path so
                    # update_from_mqtt's triSta parse applies it (same field
                    # the status push carries).
                    try:
                        self._on_state_update(hub_device_id, {"triSta": presence_val})
                    except Exception as err:
                        _LOGGER.error(
                            "presence callback failed for %s: %s",
                            hub_device_id,
                            err,
                        )
                # Other 0xAA opcodes (0x1F enable flags, 0x05 detection
                # settings, 0x1A sensitivity, ...) are config reports — already
                # captured in the diagnostics ring buffer above.
                continue

            if raw[0] != 0xEE:
                continue

            sensor_slot = raw[2]

            if raw[1] == 0x34 and raw[3] == MULTISYNC_SUBTYPE_THERMO:
                # Gateway-bridged thermometer report (H5310 via H5044, #151).
                # Diverted before the leak branch: these frames carry battery
                # in the same byte 5 a leak report uses, so letting them fall
                # through would emit a spurious dry-event for whatever leak
                # sensor happens to share this slot on the hub.
                reading = _decode_thermo_frame(raw)
                if reading is None:
                    _LOGGER.debug(
                        "Thermo frame from %s not decodable: %s",
                        hub_device_id,
                        raw.hex(),
                    )
                    continue

                _LOGGER.debug(
                    "Thermo report from hub %s: slot=%d temp=%.1f°C battery=%s",
                    hub_device_id,
                    reading["sensor_slot"],
                    reading["temperature_c"],
                    reading["battery"],
                )

                event_data = {
                    "_thermo_frame": True,
                    "hub_device_id": hub_device_id,
                    **reading,
                }

            elif raw[1] == 0x34:
                # Leak/dry event. Probe-state bytes 14/16 carry the H5059 wet
                # flag (issue #87); byte 5 is battery. OR both so older SKUs
                # decoded off byte 5 keep working and H5059 is added.
                is_wet = raw[5] == 0x01 or (
                    len(raw) >= 17 and (raw[14] == 0x01 or raw[16] == 0x01)
                )

                _LOGGER.debug(
                    "Leak event from hub %s: slot=%d wet=%s",
                    hub_device_id,
                    sensor_slot,
                    is_wet,
                )

                event_data = {
                    "_leak_event": True,
                    "hub_device_id": hub_device_id,
                    "sensor_slot": sensor_slot,
                    "is_wet": is_wet,
                }

            elif raw[1] == 0x32 and len(raw) >= 10:
                # Button press event
                # Unlike leak packets, button press encodes the sensor MAC
                # in bytes 2-9 in reverse byte order (not a slot number)
                mac_bytes = raw[2:10][::-1]
                sensor_mac = ":".join(f"{b:02X}" for b in mac_bytes)

                _LOGGER.debug(
                    "Button press from hub %s: sensor=%s",
                    hub_device_id,
                    sensor_mac,
                )

                event_data = {
                    "_button_press": True,
                    "hub_device_id": hub_device_id,
                    "device_id": sensor_mac,
                }

            else:
                _LOGGER.debug(
                    "multiSync unknown packet from %s: header=%02x%02x",
                    hub_device_id,
                    raw[0],
                    raw[1],
                )
                continue

            try:
                self._on_state_update(hub_device_id, event_data)
            except Exception as err:
                _LOGGER.error(
                    "multiSync callback failed for hub %s: %s",
                    hub_device_id,
                    err,
                )

    async def async_publish_command(
        self,
        device_topic: str | None,
        cmd: str,
        data: dict[str, Any],
        *,
        cmd_version: int = 0,
    ) -> bool:
        """Publish a generic command to a device's MQTT topic.

        Builds the standard Govee MQTT command envelope and publishes it.
        Used for both native control commands (turn/brightness/colorwc) and
        BLE passthrough (ptReal).

        Args:
            device_topic: Device-specific MQTT topic. Required for AWS IoT -
                          obtained from undocumented API.
            cmd: Command name (e.g. "turn", "brightness", "colorwc", "ptReal").
            data: Command-specific data payload.
            cmd_version: Command version (0 standard, 1 legacy color).

        Returns:
            True if publish succeeded, False otherwise.
        """
        if not self._connected or self._client is None:
            _LOGGER.warning("Cannot publish %s: MQTT not connected", cmd)
            return False

        if not device_topic:
            _LOGGER.warning(
                "Cannot publish %s: No device topic available. "
                "Device topics must be fetched from Govee undocumented API.",
                cmd,
            )
            return False

        payload = {
            "msg": {
                "cmd": cmd,
                "data": data,
                "cmdVersion": cmd_version,
                "transaction": f"v_{int(time.time() * 1000)}",
                "type": 1,
            }
        }

        try:
            await self._client.publish(device_topic, json.dumps(payload))
            _LOGGER.debug(
                "Published %s to %s...",
                cmd,
                device_topic[:30],
            )
            return True
        except Exception as err:
            _LOGGER.error("Failed to publish %s: %s", cmd, err)
            return False

    async def async_publish_ptreal(
        self,
        device_id: str,
        sku: str,
        ble_packet_base64: str | list[str],
        device_topic: str | None = None,
    ) -> bool:
        """Publish BLE passthrough command via MQTT.

        Sends a ptReal command to the device to execute BLE packet(s).
        This allows controlling device features not exposed via REST API.

        Args:
            device_id: Target device identifier.
            sku: Device SKU/model.
            ble_packet_base64: Base64-encoded BLE packet or list of packets.
                               For multi-packet sequences (e.g., scene speed),
                               pass a list of base64-encoded packets.
            device_topic: Device-specific MQTT topic for publishing commands.
                          Required for AWS IoT - obtained from undocumented API.

        Returns:
            True if publish succeeded, False otherwise.
        """
        # Normalize to list for consistent handling
        if isinstance(ble_packet_base64, str):
            packets = [ble_packet_base64]
        else:
            packets = ble_packet_base64

        # ptReal data carries device targeting inside the data block.
        data: dict[str, Any] = {
            "command": packets,
            "device": device_id,
            "sku": sku,
        }
        return await self.async_publish_command(device_topic, "ptReal", data)

    async def async_publish_gateway_ptreal(
        self,
        gateway_route: dict[str, str],
        ble_packet_base64: str | list[str],
    ) -> bool:
        """Publish a BLE passthrough packet through a device's gateway (#135).

        Gateway-attached BLE devices (e.g. an H5901 Smart Water Timer behind an
        H5044) do not act on anything published to their own ``GD/`` topic — the
        gateway ignores it, verified on live hardware. The packet has to go to
        the *gateway's* topic, addressed to the gateway itself, which then
        relays it over BLE.

        Args:
            gateway_route: The gateway's ``{device, sku, topic}``, as returned by
                ``GoveeAuthClient.gateway_routes()``.
            ble_packet_base64: Base64-encoded BLE packet, or a list of them.

        Returns:
            True if the publish succeeded, False otherwise.
        """
        topic = gateway_route.get("topic")
        gateway_device = gateway_route.get("device")
        if not topic or not gateway_device:
            _LOGGER.debug("No gateway topic/device to publish ptReal to")
            return False

        return await self.async_publish_ptreal(
            gateway_device,
            gateway_route.get("sku", ""),
            ble_packet_base64,
            device_topic=topic,
        )
