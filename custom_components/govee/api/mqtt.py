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
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

from ..models.device import PROBE_THERMOMETER_BFF_SKUS
from .probe_thermometer import (
    concat_command_blocks,
    decode_limits,
    decode_probe_reading,
    decode_status_frame,
)

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
# 60 s like Home Assistant core's default: paho only notices a dead peer after
# 1-2 keepalive periods, so this bounds a silent half-open session to ~2 min.
AWS_IOT_KEEPALIVE = 60
RECONNECT_BASE = 5
RECONNECT_MAX = 300
# aiomqtt's default budget for every broker round-trip (CONNACK, SUBACK,
# DISCONNECT ack). Publishes use the much shorter ACK_TIMEOUT below.
CONNECTION_TIMEOUT = 60
# PUBACK budget for QoS-1 publishes (Home Assistant core's TIMEOUT_ACK). A
# half-open socket to AWS IoT otherwise "succeeds" for up to 2x keepalive
# while the command never arrives and the REST fallback is skipped.
ACK_TIMEOUT = 10
# Consecutive failed connection attempts (or sessions that died before
# STABLE_SESSION_SECONDS) after which on_give_up fires ONCE to surface a repair
# issue. The loop never stops retrying: a multi-hour outage must self-heal when
# the network returns, exactly as Home Assistant core's MQTT client and this
# integration's own OpenAPI events client behave. Name kept for history.
MAX_RECONNECT_ATTEMPTS = 50
# A session that lasted at least this long before dropping is a healthy
# connection that was lost, not a failed attempt: backoff resets. Shorter
# sessions count as failures so a flap (AWS IoT client-id takeover by another
# install on the same account, a policy revocation) backs off instead of
# hammering the endpoint every RECONNECT_BASE seconds forever.
STABLE_SESSION_SECONDS = 60
# MQTT 3.1.1 SUBACK return code for a refused subscription (AWS IoT sends it
# when the account policy does not allow the topic).
SUBACK_FAILURE = 0x80

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
#   T[°C] = (byte13 + 256 * (byte14 & 0x01) + THERMO_TEMP_OFFSET) / 10
#
# Established by @Araknus13 against 30 on-the-hour frames paired with the
# cloud reading they produced, spanning 24.4-29.5 °C over 31 hours: least
# squares gives T = 0.10010 * b13 + 11.1647, reproducing every point within
# 0.1 K. Two earlier candidates from the single-labelled-point capture in #157
# (°F + 113 and °C + 170) miss the same data by 19.3 K and 38.1 K — they had
# been indistinguishable only because they intersect at 31.25 °C, right where
# that lone reading sat.
#
# A later H5310 capture spanning the rollover showed byte 13 changing from
# 0xFF to 0x00 while byte 14 changed from 0x82 to 0x83. The low bit of byte 14
# is therefore the ninth (carry) bit of the temperature value; the other seven
# bits are device-specific and must be ignored. This also proves 0x00 and 0xFF
# are valid temperature bytes, not no-data sentinels.
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

# AWS recommends trusting every Amazon Trust Services root, not just Root CA 1:
# the -ats endpoints serve an RSA chain (Root CA 1) today, but the ECC chains
# (Root CA 3/4) and Root CA 2 are equally valid and a pin on one root would
# fail verification the day AWS rotates. Source: https://www.amazontrust.com/repository/
AMAZON_ROOT_CAS = (
    AMAZON_ROOT_CA1
    + "\n"
    + """-----BEGIN CERTIFICATE-----
MIIFQTCCAymgAwIBAgITBmyf0pY1hp8KD+WGePhbJruKNzANBgkqhkiG9w0BAQwF
ADA5MQswCQYDVQQGEwJVUzEPMA0GA1UEChMGQW1hem9uMRkwFwYDVQQDExBBbWF6
b24gUm9vdCBDQSAyMB4XDTE1MDUyNjAwMDAwMFoXDTQwMDUyNjAwMDAwMFowOTEL
MAkGA1UEBhMCVVMxDzANBgNVBAoTBkFtYXpvbjEZMBcGA1UEAxMQQW1hem9uIFJv
b3QgQ0EgMjCCAiIwDQYJKoZIhvcNAQEBBQADggIPADCCAgoCggIBAK2Wny2cSkxK
gXlRmeyKy2tgURO8TW0G/LAIjd0ZEGrHJgw12MBvIITplLGbhQPDW9tK6Mj4kHbZ
W0/jTOgGNk3Mmqw9DJArktQGGWCsN0R5hYGCrVo34A3MnaZMUnbqQ523BNFQ9lXg
1dKmSYXpN+nKfq5clU1Imj+uIFptiJXZNLhSGkOQsL9sBbm2eLfq0OQ6PBJTYv9K
8nu+NQWpEjTj82R0Yiw9AElaKP4yRLuH3WUnAnE72kr3H9rN9yFVkE8P7K6C4Z9r
2UXTu/Bfh+08LDmG2j/e7HJV63mjrdvdfLC6HM783k81ds8P+HgfajZRRidhW+me
z/CiVX18JYpvL7TFz4QuK/0NURBs+18bvBt+xa47mAExkv8LV/SasrlX6avvDXbR
8O70zoan4G7ptGmh32n2M8ZpLpcTnqWHsFcQgTfJU7O7f/aS0ZzQGPSSbtqDT6Zj
mUyl+17vIWR6IF9sZIUVyzfpYgwLKhbcAS4y2j5L9Z469hdAlO+ekQiG+r5jqFoz
7Mt0Q5X5bGlSNscpb/xVA1wf+5+9R+vnSUeVC06JIglJ4PVhHvG/LopyboBZ/1c6
+XUyo05f7O0oYtlNc/LMgRdg7c3r3NunysV+Ar3yVAhU/bQtCSwXVEqY0VThUWcI
0u1ufm8/0i2BWSlmy5A5lREedCf+3euvAgMBAAGjQjBAMA8GA1UdEwEB/wQFMAMB
Af8wDgYDVR0PAQH/BAQDAgGGMB0GA1UdDgQWBBSwDPBMMPQFWAJI/TPlUq9LhONm
UjANBgkqhkiG9w0BAQwFAAOCAgEAqqiAjw54o+Ci1M3m9Zh6O+oAA7CXDpO8Wqj2
LIxyh6mx/H9z/WNxeKWHWc8w4Q0QshNabYL1auaAn6AFC2jkR2vHat+2/XcycuUY
+gn0oJMsXdKMdYV2ZZAMA3m3MSNjrXiDCYZohMr/+c8mmpJ5581LxedhpxfL86kS
k5Nrp+gvU5LEYFiwzAJRGFuFjWJZY7attN6a+yb3ACfAXVU3dJnJUH/jWS5E4ywl
7uxMMne0nxrpS10gxdr9HIcWxkPo1LsmmkVwXqkLN1PiRnsn/eBG8om3zEK2yygm
btmlyTrIQRNg91CMFa6ybRoVGld45pIq2WWQgj9sAq+uEjonljYE1x2igGOpm/Hl
urR8FLBOybEfdF849lHqm/osohHUqS0nGkWxr7JOcQ3AWEbWaQbLU8uz/mtBzUF+
fUwPfHJ5elnNXkoOrJupmHN5fLT0zLm4BwyydFy4x2+IoZCn9Kr5v2c69BoVYh63
n749sSmvZ6ES8lgQGVMDMBu4Gon2nL2XA46jCfMdiyHxtN/kHNGfZQIG6lzWE7OE
76KlXIx3KadowGuuQNKotOrN8I1LOJwZmhsoVLiJkO/KdYE+HvJkJMcYr07/R54H
9jVlpNMKVv/1F2Rs76giJUmTtt8AF9pYfl3uxRuw0dFfIRDH+fO6AgonB8Xx1sfT
4PsJYGw=
-----END CERTIFICATE-----
-----BEGIN CERTIFICATE-----
MIIBtjCCAVugAwIBAgITBmyf1XSXNmY/Owua2eiedgPySjAKBggqhkjOPQQDAjA5
MQswCQYDVQQGEwJVUzEPMA0GA1UEChMGQW1hem9uMRkwFwYDVQQDExBBbWF6b24g
Um9vdCBDQSAzMB4XDTE1MDUyNjAwMDAwMFoXDTQwMDUyNjAwMDAwMFowOTELMAkG
A1UEBhMCVVMxDzANBgNVBAoTBkFtYXpvbjEZMBcGA1UEAxMQQW1hem9uIFJvb3Qg
Q0EgMzBZMBMGByqGSM49AgEGCCqGSM49AwEHA0IABCmXp8ZBf8ANm+gBG1bG8lKl
ui2yEujSLtf6ycXYqm0fc4E7O5hrOXwzpcVOho6AF2hiRVd9RFgdszflZwjrZt6j
QjBAMA8GA1UdEwEB/wQFMAMBAf8wDgYDVR0PAQH/BAQDAgGGMB0GA1UdDgQWBBSr
ttvXBp43rDCGB5Fwx5zEGbF4wDAKBggqhkjOPQQDAgNJADBGAiEA4IWSoxe3jfkr
BqWTrBqYaGFy+uGh0PsceGCmQ5nFuMQCIQCcAu/xlJyzlvnrxir4tiz+OpAUFteM
YyRIHN8wfdVoOw==
-----END CERTIFICATE-----
-----BEGIN CERTIFICATE-----
MIIB8jCCAXigAwIBAgITBmyf18G7EEwpQ+Vxe3ssyBrBDjAKBggqhkjOPQQDAzA5
MQswCQYDVQQGEwJVUzEPMA0GA1UEChMGQW1hem9uMRkwFwYDVQQDExBBbWF6b24g
Um9vdCBDQSA0MB4XDTE1MDUyNjAwMDAwMFoXDTQwMDUyNjAwMDAwMFowOTELMAkG
A1UEBhMCVVMxDzANBgNVBAoTBkFtYXpvbjEZMBcGA1UEAxMQQW1hem9uIFJvb3Qg
Q0EgNDB2MBAGByqGSM49AgEGBSuBBAAiA2IABNKrijdPo1MN/sGKe0uoe0ZLY7Bi
9i0b2whxIdIA6GO9mif78DluXeo9pcmBqqNbIJhFXRbb/egQbeOc4OO9X4Ri83Bk
M6DLJC9wuoihKqB1+IGuYgbEgds5bimwHvouXKNCMEAwDwYDVR0TAQH/BAUwAwEB
/zAOBgNVHQ8BAf8EBAMCAYYwHQYDVR0OBBYEFNPsxzplbszh2naaVvuc84ZtV+WB
MAoGCCqGSM49BAMDA2gAMGUCMDqLIfG9fhGt0O9Yli/W651+kI0rz2ZVwyzjKKlw
CkcO8DdZEv8tmZQoTipPNU0zWgIxAOp1AE47xDqUEpHJWEadIRNyp4iciuRMStuW
1KyLa2tJElMzrdfkviT8tQp21KW8EA==
-----END CERTIFICATE-----
"""
)


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
        the frame is too short.
    """
    if len(raw) < 15:
        return None

    temp_byte = raw[13]
    temp_carry = raw[14] & 0x01

    battery = raw[5] if raw[5] <= 100 else None
    frame_ts = int.from_bytes(raw[9:13], "big") if len(raw) >= 13 else None

    return {
        "sensor_slot": raw[2],
        "temperature_c": (
            temp_byte + (temp_carry << 8) + THERMO_TEMP_OFFSET
        ) / THERMO_TEMP_SCALE,
        "battery": battery,
        "frame_ts": frame_ts,
    }


def _decode_op_frames(op: Any) -> list[bytes]:
    """Base64-decode the BLE-format frames in a message's ``op.command`` list.

    Tolerates a missing/odd ``op`` block, non-string entries and bad base64:
    a malformed entry must never cost the message its ``state``.
    """
    if not isinstance(op, dict):
        return []
    commands = op.get("command")
    if not isinstance(commands, list):
        return []
    frames: list[bytes] = []
    for entry in commands:
        if not isinstance(entry, str):
            continue
        try:
            frames.append(base64.b64decode(entry))
        except (binascii.Error, ValueError):
            continue
    return frames


def _subscription_refused(granted: Any) -> bool:
    """True if a SUBACK reports the subscription was refused.

    paho hands aiomqtt a ``tuple[int]`` of granted QoS values for MQTT 3.1.1
    (0x80 = failure) or a list of ``ReasonCodes`` for MQTT 5.
    """
    try:
        for code in granted or ():
            if isinstance(code, int):
                if code >= SUBACK_FAILURE:
                    return True
            elif getattr(code, "is_failure", False):
                return True
    except TypeError:  # pragma: no cover - defensive
        return False
    return False


def _same_iot_credentials(a: GoveeIotCredentials, b: GoveeIotCredentials) -> bool:
    """Whether two credential sets connect identically (token differences don't matter)."""
    return (
        a.iot_cert == b.iot_cert
        and a.iot_key == b.iot_key
        and a.client_id == b.client_id
        and a.endpoint == b.endpoint
        and a.account_topic == b.account_topic
    )


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
        on_connected: Callable[[], None] | None = None,
        on_disconnected: Callable[[], None] | None = None,
    ) -> None:
        """Initialize the AWS IoT MQTT client.

        Args:
            credentials: IoT credentials from Govee login API.
            on_state_update: Callback(device_id, state_dict) for state changes.
            on_give_up: Optional callback fired ONCE when MAX_RECONNECT_ATTEMPTS
                consecutive attempts have failed. Use to surface a repair
                issue; the loop keeps retrying regardless.
            on_connected: Optional callback fired after every successful
                CONNACK + SUBACK, so the caller can clear that repair issue.
            on_disconnected: Optional callback fired when a live session
                drops, so status entities reflect it immediately instead of
                on the next poll.
        """
        self._credentials = credentials
        self._on_state_update = on_state_update
        self._on_give_up = on_give_up
        self._on_connected = on_connected
        self._on_disconnected = on_disconnected
        self._running = False
        self._connected = False
        self._task: asyncio.Task[None] | None = None
        self._temp_dir: tempfile.TemporaryDirectory[str] | None = None
        # TLS context built once per credential set and reused across
        # reconnects (an SSLContext is reusable; HA core builds its once too).
        # Invalidated by async_restart() when the credentials change.
        self._ssl_context: ssl.SSLContext | None = None
        self._unhealthy_reported = False
        self._consecutive_failures = 0
        self._last_error: str | None = None
        self._connected_since: float | None = None
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
        # Same idea for probe-thermometer frames: keeping the raw hex means
        # the next probe SKU can be decoded from a diagnostics download
        # alone, without asking the owner to run verbose logging while
        # cooking.
        self._recent_probe_frames: deque[dict[str, Any]] = deque(maxlen=64)
        # Latest Tower-Fan swing-range tail bytes per device_id, harvested from
        # inbound aa1d status frames. Used to rebuild the oscillation-ON packet
        # so the fan resumes its own physically-configured sweep arc.
        self._fan_swing_tail: dict[str, list[int]] = {}

    def fan_swing_tail(self, device_id: str) -> list[int] | None:
        """Return the last-seen Tower-Fan swing-range tail (4 bytes) or None."""
        return self._fan_swing_tail.get(device_id)

    @property
    def last_messages(self) -> dict[str, dict[str, Any]]:
        """Most recent raw MQTT state payload per device_id (diagnostics)."""
        return self._last_messages

    @property
    def recent_multisync(self) -> list[dict[str, Any]]:
        """Recent multiSync packets (hex + decode) for leak-sensor diagnostics."""
        return list(self._recent_multisync)

    @property
    def recent_probe_frames(self) -> list[dict[str, Any]]:
        """Recent probe-thermometer frames (hex) for diagnostics."""
        return list(self._recent_probe_frames)

    @property
    def last_message_ts(self) -> datetime | None:
        """UTC timestamp of the most recent inbound MQTT state message."""
        return self._last_message_ts

    def last_message_ts_for(self, device_id: str) -> datetime | None:
        """UTC timestamp of the most recent inbound MQTT message for a device."""
        return self._last_message_per_device.get(device_id)

    @property
    def connected(self) -> bool:
        """Return True if connected AND subscribed to the account topic."""
        return self._connected

    @property
    def consecutive_failures(self) -> int:
        """Failed connection attempts since the last stable session (diagnostics)."""
        return self._consecutive_failures

    @property
    def last_error(self) -> str | None:
        """Last connection error, or None (diagnostics)."""
        return self._last_error

    @property
    def connected_since(self) -> datetime | None:
        """UTC time the current session became connected, or None (diagnostics)."""
        if self._connected_since is None or not self._connected:
            return None
        age = time.monotonic() - self._connected_since
        return datetime.now(timezone.utc) - timedelta(seconds=age)

    async def async_restart(self, credentials: GoveeIotCredentials) -> bool:
        """Swap in new credentials and reconnect if anything relevant changed.

        A re-login can rotate the account's certificate, key or topic; the
        session that authenticated with the old set keeps running until it
        drops, after which the old material would be retried for good.
        Returns True if a restart happened.
        """
        if _same_iot_credentials(self._credentials, credentials):
            self._credentials = credentials
            return False
        was_running = self._running
        await self.async_stop()
        self._credentials = credentials
        self._ssl_context = None
        self._consecutive_failures = 0
        self._unhealthy_reported = False
        if was_running:
            await self.async_start()
        _LOGGER.info("AWS IoT MQTT client restarted with refreshed credentials")
        return True

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
        self._connected_since = None
        _LOGGER.info("AWS IoT MQTT client stopped")

    def _create_ssl_context_sync(self) -> ssl.SSLContext:
        """Create SSL context with certificate files (synchronous).

        Configures mutual TLS authentication for AWS IoT:
        - Loads Amazon Root CA for server verification
        - Loads client certificate and key for client authentication
        - Enforces TLS 1.2+ as required by AWS IoT

        ``load_cert_chain`` needs real files, so the certificate and key are
        written to a private temp directory that is removed as soon as the
        context has loaded them — the key material lives in the context, not
        on disk, for the rest of the session.

        This method is blocking and should be run in an executor.
        """
        # Clean up any leftover temp directory first
        if self._temp_dir:
            try:
                self._temp_dir.cleanup()
            except Exception:
                pass
            self._temp_dir = None

        with tempfile.TemporaryDirectory() as temp_name:
            temp_path = Path(temp_name)
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
            ssl_context.load_verify_locations(cadata=AMAZON_ROOT_CAS)

            # Load client certificate and private key for mutual TLS
            ssl_context.load_cert_chain(str(cert_path), str(key_path))

        _LOGGER.debug("SSL context created for AWS IoT MQTT")
        return ssl_context

    async def _create_ssl_context(self) -> ssl.SSLContext:
        """Return the cached SSL context, building it in an executor on first use."""
        if self._ssl_context is None:
            loop = asyncio.get_running_loop()
            self._ssl_context = await loop.run_in_executor(
                None, self._create_ssl_context_sync
            )
        return self._ssl_context

    async def _connection_loop(self) -> None:
        """Maintain the AWS IoT MQTT connection; never stops retrying.

        Exponential backoff RECONNECT_BASE..RECONNECT_MAX between failed
        attempts. A session that lived at least STABLE_SESSION_SECONDS resets
        the backoff when it drops; a shorter one counts as a failure so a flap
        backs off too. After MAX_RECONNECT_ATTEMPTS consecutive failures
        ``on_give_up`` fires once (a repair issue) and the loop carries on at
        RECONNECT_MAX cadence until the network comes back or the client is
        stopped.
        """
        reconnect_interval = RECONNECT_BASE

        while self._running:
            session_started: float | None = None
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

                    # Subscribe to the account topic for all device updates,
                    # at QoS 1 like the OpenAPI events client. AWS IoT answers
                    # a policy-refused subscription with SUBACK 0x80 and keeps
                    # the session up — without this check the client would
                    # sit "connected" and deaf forever.
                    topic = self._credentials.account_topic
                    granted = await client.subscribe(topic, qos=1)
                    if _subscription_refused(granted):
                        raise aiomqtt.MqttError(
                            f"account topic subscription refused (SUBACK {granted!r})"
                        )

                    self._connected = True
                    session_started = time.monotonic()
                    self._connected_since = session_started
                    if self._consecutive_failures:
                        _LOGGER.info(
                            "Connected to AWS IoT MQTT at %s after %d failed attempt(s)",
                            self._credentials.endpoint,
                            self._consecutive_failures,
                        )
                    else:
                        _LOGGER.info(
                            "Connected to AWS IoT MQTT at %s",
                            self._credentials.endpoint,
                        )
                    # The failure streak and backoff are NOT reset here: a
                    # session that dies within STABLE_SESSION_SECONDS is a
                    # flap and must keep backing off. They reset once the
                    # session has proven itself (below, or when it drops late).
                    self._last_error = None
                    _LOGGER.debug("Subscribed to topic: %s", topic[:30] + "...")
                    self._notify_connected()

                    async for message in client.messages:
                        if not self._running:
                            break  # type: ignore[unreachable]
                        if (
                            self._consecutive_failures
                            and time.monotonic() - session_started >= STABLE_SESSION_SECONDS
                        ):
                            self._consecutive_failures = 0
                            self._unhealthy_reported = False
                            reconnect_interval = RECONNECT_BASE
                        await self._handle_message(message)

                    self._client = None

            except asyncio.CancelledError:
                _LOGGER.debug("AWS IoT connection loop cancelled")
                raise

            except Exception as err:
                self._client = None
                was_connected = self._connected
                self._connected = False
                self._connected_since = None
                self._last_error = f"{type(err).__name__}: {err}"
                if was_connected:
                    self._notify_disconnected()

                if not self._running:
                    break  # type: ignore[unreachable]

                if (
                    session_started is not None
                    and time.monotonic() - session_started >= STABLE_SESSION_SECONDS
                ):
                    # A healthy session was lost (keepalive timeout, broker
                    # restart, network blip): not a failure streak.
                    self._consecutive_failures = 0
                    self._unhealthy_reported = False
                    reconnect_interval = RECONNECT_BASE
                    _LOGGER.warning(
                        "AWS IoT connection lost after %ds (%s); reconnecting in %ds",
                        int(time.monotonic() - session_started),
                        self._last_error,
                        reconnect_interval,
                    )
                else:
                    self._consecutive_failures += 1
                    # First failure is worth a WARNING; the rest of a streak is
                    # noise at that level (HA core logs retries at DEBUG).
                    _LOGGER.log(
                        logging.WARNING if self._consecutive_failures == 1 else logging.DEBUG,
                        "AWS IoT connection %s (%s); reconnecting in %ds (attempt %d)",
                        "dropped early" if session_started is not None else "failed",
                        self._last_error,
                        reconnect_interval,
                        self._consecutive_failures,
                    )
                    if (
                        self._consecutive_failures >= MAX_RECONNECT_ATTEMPTS
                        and not self._unhealthy_reported
                    ):
                        self._unhealthy_reported = True
                        _LOGGER.error(
                            "AWS IoT connection has failed %d times in a row (last: %s); "
                            "surfacing a repair and continuing to retry every %ds",
                            self._consecutive_failures,
                            self._last_error,
                            RECONNECT_MAX,
                        )
                        if self._on_give_up is not None:
                            try:
                                self._on_give_up(self._consecutive_failures, str(err))
                            except Exception as cb_err:  # pragma: no cover
                                _LOGGER.warning("give-up callback raised: %s", cb_err)

                await asyncio.sleep(reconnect_interval)
                reconnect_interval = min(reconnect_interval * 2, RECONNECT_MAX)

        self._connected = False
        self._connected_since = None

    def _notify_disconnected(self) -> None:
        """Tell the owner a live session dropped."""
        if self._on_disconnected is None:
            return
        try:
            self._on_disconnected()
        except Exception as err:  # pragma: no cover - defensive
            _LOGGER.warning("disconnected callback raised: %s", err)

    def _notify_connected(self) -> None:
        """Tell the owner a session is up (clears the disconnect repair)."""
        if self._on_connected is None:
            return
        try:
            self._on_connected()
        except Exception as err:  # pragma: no cover - defensive
            _LOGGER.warning("connected callback raised: %s", err)

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
            if not isinstance(data, dict):
                _LOGGER.debug("Ignoring non-object AWS IoT payload")
                return

            # Older devices wrap the whole status in a JSON string under
            # "msg" (homebridge-govee unwraps it too); our own publishes are
            # {"msg": {"cmd", "data", ...}} objects. Unwrap the former, drop
            # the latter.
            if "msg" in data:
                wrapped = data["msg"]
                if isinstance(wrapped, str):
                    try:
                        wrapped = json.loads(wrapped)
                    except json.JSONDecodeError:
                        wrapped = None
                if isinstance(wrapped, dict) and "device" in wrapped and "state" in wrapped:
                    data = wrapped
                else:
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

            # BLE-format status frames riding in op.command[] (base64),
            # decoded once here. The Tower-Fan tail harvest below reads them,
            # they are attached to the state dict as hex so the coordinator
            # can decode SKU-specific reports (ceiling-fan combos, #181), and
            # the diagnostics download shows them verbatim. This runs before
            # the multiSync/state branches so it sees every envelope.
            frames = _decode_op_frames(data.get("op"))

            # Harvest Tower-Fan swing-range tail bytes from an inbound aa 1d
            # frame. Bytes 3-6 are the swing range (homebridge fan-H7107.js),
            # replayed on oscillation ON. Only the fan's own on/off reports
            # (aa 1d 00 / aa 1d 01) carry the arc — homebridge caches nothing
            # else — so an unknown aa 1d sub-report can't be replayed as a
            # bogus range.
            for _fb in frames:
                if (
                    len(_fb) >= 7
                    and _fb[0] == 0xAA
                    and _fb[1] == 0x1D
                    and _fb[2] in (0x00, 0x01)
                ):
                    self._fan_swing_tail[device_id] = list(_fb[3:7])

            cmd = data.get("cmd")

            # Probe thermometers (H5192) answer reads over ptReal and
            # volunteer a status frame on power-on. Gated on the SKU because
            # light strips push their own 0xAA 0x05/0x13/0xA5 packets
            # through op.command, which must never reach this decoder.
            if data.get("sku", "") in PROBE_THERMOMETER_BFF_SKUS and cmd in (
                "status",
                "ptReal",
            ):
                self._handle_probe_frame(device_id, data)
                return

            # Handle multiSync messages (leak sensor events)
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

            if frames and isinstance(state, dict):
                state["_op_frames"] = [frame.hex() for frame in frames]

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

    def _handle_probe_frame(self, device_id: str, data: dict[str, Any]) -> None:
        """Handle a probe-thermometer frame (H5192) from status or ptReal.

        Three frame kinds arrive on this path, all identified by byte 1 of the
        reassembled 20-byte packet (byte 0 varies between 0x40 and 0x47 and
        carries no meaning here):

        - 0x0F: the full status frame the device volunteers on power-on, both
          probes with readings and limits in one packet
        - 0x24: the answer to a live read, one probe's core and ambient
        - 0x12: the answer to a limits read, one probe's alarm corridor

        Anything else is left to the diagnostics ring buffer, which is written
        before decoding so an unknown frame from the next probe SKU survives
        into a download.
        """
        raw = concat_command_blocks(data.get("op", {}).get("command", []))
        if raw is None:
            return

        self._recent_probe_frames.append(
            {
                "ts": datetime.now(timezone.utc).isoformat(),
                "device_id": device_id,
                "sku": data.get("sku", ""),
                "cmd": data.get("cmd", ""),
                "header": raw[:2].hex(),
                "length": len(raw),
                "hex": raw.hex(),
            }
        )

        probes: dict[int, dict[str, float | None]] = {}

        status = decode_status_frame(raw)
        if status is not None:
            probes = status
        else:
            reading = decode_probe_reading(raw)
            if reading is not None:
                probe, core, ambient = reading
                probes = {probe: {"core": core, "ambient": ambient}}
            else:
                decoded_limits = decode_limits(raw)
                if decoded_limits is not None:
                    probe, limits = decoded_limits
                    probes = {
                        probe: {
                            "core_max": limits.core_max,
                            "core_min": limits.core_min,
                            "ambient_max": limits.ambient_max,
                            "ambient_min": limits.ambient_min,
                        }
                    }

        if not probes:
            _LOGGER.debug(
                "Unhandled probe frame from %s: %s", device_id, raw[:2].hex()
            )
            return

        try:
            self._on_state_update(device_id, {"_probe_frame": True, "probes": probes})
        except Exception as err:
            _LOGGER.error("probe callback failed for %s: %s", device_id, err)

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
            # QoS 1: success means the broker PUBACKed within ACK_TIMEOUT, not
            # that the bytes reached the kernel. On a half-open socket this
            # fails fast so the caller's REST fallback runs instead of an
            # optimistic update masking a lost command.
            await self._client.publish(
                device_topic, json.dumps(payload), qos=1, timeout=ACK_TIMEOUT
            )
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
