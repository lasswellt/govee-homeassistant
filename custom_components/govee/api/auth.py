"""Govee authentication API for AWS IoT MQTT credentials.

Authenticates with Govee's account API to obtain certificates for AWS IoT MQTT
which provides real-time device state updates.

Reference: homebridge-govee, govee2mqtt implementations
"""

from __future__ import annotations

import base64
import json
import logging
import re
import time
import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

import aiohttp
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
    pkcs12,
)

from .exceptions import (
    Govee2FACodeInvalidError,
    Govee2FARequiredError,
    GoveeApiError,
    GoveeAuthError,
    GoveeLoginRejectedError,
)

from ..models.device import LEAK_HUB_SKUS, LEAK_SENSOR_SKUS, THERMO_HYGRO_BFF_SKUS

_LOGGER = logging.getLogger(__name__)

# Fields that should be redacted in debug logs (contain credentials/secrets)
_SENSITIVE_FIELDS = frozenset(
    {
        "token",
        "refreshToken",
        "password",
        "p12",
        "p12Pass",
        "p12_pass",
        "privateKey",
        "certificatePem",
        "caCertificate",
    }
)


def _safe_int(value: Any) -> int | None:
    """int(value) or None — Govee numeric fields occasionally arrive as strings.

    Guards the water-detector ``lastTime``/``battery`` parse so a stray string
    can't raise during the coordinator's ``last_time > prev_time`` comparison
    and silently kill polling (issue #62).
    """
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _sanitize_response_for_logging(data: Any) -> Any:
    """Mask sensitive fields in API response for safe logging.

    Args:
        data: API response (typically a dictionary).

    Returns:
        Copy of dict with sensitive values replaced by [REDACTED],
        or original value if not a dict.
    """
    if not isinstance(data, dict):
        return data

    sanitized: dict[str, Any] = {}
    for key, value in data.items():
        if key in _SENSITIVE_FIELDS:
            sanitized[key] = "[REDACTED]"
        elif isinstance(value, dict):
            sanitized[key] = _sanitize_response_for_logging(value)
        elif isinstance(value, str) and len(value) > 100:
            # Truncate long strings (likely base64 data)
            sanitized[key] = f"{value[:50]}...[truncated, {len(value)} chars]"
        else:
            sanitized[key] = value
    return sanitized


# 12+ hex chars (with or without separators) — catches MAC-derived ids used as
# dict keys, which must not appear in the PII-free skeleton.
_HEXKEY_RE = re.compile(r"^[0-9A-Fa-f]{2}([:_-]?[0-9A-Fa-f]{2}){5,}$")


def _shape_skeleton(obj: Any, _depth: int = 0) -> Any:
    """Summarize a JSON value as field-names + types + lengths (no scalar values).

    Recurses into dicts, the first element of lists, and JSON-encoded string
    fields. Drops MAC-shaped dict keys. Used for the BFF response skeleton (#87)
    so diagnostics reveal response *shape* without leaking any values.
    """
    if _depth > 12:
        return "..."
    if isinstance(obj, dict):
        out: dict[str, Any] = {}
        for key, value in obj.items():
            if isinstance(key, str) and _HEXKEY_RE.match(key):
                continue
            out[key] = _shape_skeleton(value, _depth + 1)
        return out
    if isinstance(obj, list):
        shape: list[Any] = [f"list[{len(obj)}]"]
        if obj:
            shape.append(_shape_skeleton(obj[0], _depth + 1))
        return shape
    if isinstance(obj, str):
        stripped = obj.strip()
        if stripped[:1] in ("{", "[") and len(stripped) < 100_000:
            try:
                return {"_json_str": _shape_skeleton(json.loads(stripped), _depth + 1)}
            except (json.JSONDecodeError, ValueError):
                pass
        return "str"
    if isinstance(obj, bool):
        return "bool"
    if obj is None:
        return "null"
    return type(obj).__name__


# Govee Account API endpoints
GOVEE_LOGIN_URL = "https://app2.govee.com/account/rest/account/v2/login"
GOVEE_VERIFICATION_URL = "https://app2.govee.com/account/rest/account/v1/verification"
GOVEE_IOT_KEY_URL = "https://app2.govee.com/app/v1/account/iot/key"
GOVEE_DEVICE_LIST_URL = "https://app2.govee.com/device/rest/devices/v1/list"
GOVEE_BFF_DEVICE_LIST_URL = "https://app2.govee.com/bff-app/v1/device/list"
# Standalone water-detector leak alerts (H5054 via H5040 gateway, issue #62).
# These RF-only sensors never reach the developer API / AWS IoT; their trip is
# only retrievable from the account "warning message" history, matching the
# homebridge-govee `http` path.
GOVEE_LEAK_WARN_URL = "https://app2.govee.com/leak/rest/device/v1/warnMessage"
GOVEE_CLIENT_TYPE = "1"
GOVEE_APP_VERSION = "7.4.10"
GOVEE_IOT_VERSION = "0"
GOVEE_USER_AGENT = (
    f"GoveeHome/{GOVEE_APP_VERSION} "
    "(com.ihoment.GoVeeSensor; build:2; iOS 18.4.0) Alamofire/5.10.2"
)

# Candidate keys a BFF ``lastDeviceData`` reading may hide behind, each tagged
# centi (True) or plain (False). Govee's gateway-bridged thermo-hygrometers
# (H5310 P2 via H5044, confirmed from #86 diagnostics) report ``tem``/``hum`` as
# centi-units (2350 == 23.50, including negatives: -500 == -5.0). The plain
# fallbacks cover any SKU that reports a already-scaled float. Same defensive
# spirit as models.state.
_BFF_TEMP_KEYS = (
    ("tem", True),
    ("temperature", False),
    ("sensorTemperature", False),
    ("currentTemperature", False),
)
_BFF_HUMIDITY_KEYS = (
    ("hum", True),
    ("humidity", False),
    ("sensorHumidity", False),
    ("currentHumidity", False),
)

# u16 "no reading / no sensor" sentinels Govee reports for a missing centi value
# (e.g. the H5310 pool thermometer has no hygrometer and reports hum == 0xFFFF,
# which would otherwise de-scale to 655.35 — issue #97). 0x7FFF covers a signed
# variant for the temperature key.
_BFF_NO_VALUE_SENTINELS = frozenset({65535, 32767, -1})


def _bff_reading(
    last_device_data: dict[str, Any], keys: tuple[tuple[str, bool], ...]
) -> float | None:
    """Extract a temperature/humidity reading from BFF ``lastDeviceData``.

    Tries each ``(key, is_centi)`` candidate in order; returns the first numeric
    value found. Centi-tagged keys are always divided by 100 when integer (so
    2350 -> 23.5, -500 -> -5.0, 50 -> 0.5 — no magnitude guess, which would mis-
    handle near-0 readings). Plain keys pass through unscaled. Returns None when
    no candidate key holds a usable number.
    """
    for key, is_centi in keys:
        if key not in last_device_data:
            continue
        raw = last_device_data[key]
        if raw is None or raw == "":
            continue
        try:
            value = float(raw)
        except (TypeError, ValueError):
            continue
        if is_centi and isinstance(raw, int):
            # 0xFFFF (and signed variants) mean "no reading / no sensor", not a
            # real centi value — treat as absent so we don't surface 655.35 (#97).
            if raw in _BFF_NO_VALUE_SENTINELS:
                continue
            value /= 100.0
        return value
    return None


def _derive_client_id(email: str) -> str:
    """Derive a stable client_id from the account email.

    Govee's account API caches (email, client_id) pairs after first login.
    Sending a different client_id on subsequent calls or across HA restarts
    looks like a new device to Govee and triggers 2FA hardening or outright
    rejection on newer/stricter accounts.

    All reference implementations derive client_id deterministically from
    the username/email:
    - homebridge-govee: uuid.generate(username)
    - wez/govee2mqtt: Uuid::new_v5(NAMESPACE_DNS, email)
    - TheOneOgre/govee-cloud: uuid.uuid5(NAMESPACE_DNS, email).hex

    We match that pattern, prefixed with "hacs-govee:" to namespace our IDs
    and avoid collisions with other clients using the same derivation scheme.
    """
    normalized = (email or "").strip().lower()
    return uuid.uuid5(uuid.NAMESPACE_DNS, f"hacs-govee:{normalized}").hex


def _extract_p12_credentials(
    p12_base64: str, password: str | None = None
) -> tuple[str, str]:
    """Extract certificate and private key from P12/PFX container.

    Govee API returns AWS IoT credentials as a PKCS#12 (P12/PFX) container
    in base64 encoding. This function extracts the certificate and private
    key and converts them to PEM format for use with SSL/TLS.

    Args:
        p12_base64: Base64-encoded P12/PFX container from Govee API.
        password: Optional password for the P12 container.

    Returns:
        Tuple of (certificate_pem, private_key_pem).

    Raises:
        GoveeApiError: If P12 extraction fails.
    """
    if not p12_base64:
        raise GoveeApiError("Empty P12 data received from Govee API")

    try:
        # Clean base64 string: strip whitespace, newlines
        cleaned = (
            p12_base64.strip().replace("\n", "").replace("\r", "").replace(" ", "")
        )

        # Handle URL-safe base64 (convert - to + and _ to /)
        cleaned = cleaned.replace("-", "+").replace("_", "/")

        # Fix base64 padding if needed
        padding_needed = len(cleaned) % 4
        if padding_needed:
            cleaned += "=" * (4 - padding_needed)

        # Decode base64 to get raw P12 bytes
        try:
            p12_data = base64.b64decode(cleaned)
        except Exception as b64_err:
            raise GoveeApiError(f"Base64 decode failed: {b64_err}") from b64_err

        # Parse PKCS#12 container with optional password
        pwd_bytes = password.encode("utf-8") if password else None
        try:
            private_key, certificate, _ = pkcs12.load_key_and_certificates(
                p12_data, pwd_bytes
            )
        except Exception as p12_err:
            raise GoveeApiError(f"P12 container parse failed: {p12_err}") from p12_err

        if private_key is None:
            raise GoveeApiError("No private key found in P12 container")
        if certificate is None:
            raise GoveeApiError("No certificate found in P12 container")

        # Convert private key to PEM format (PKCS8)
        key_pem = private_key.private_bytes(
            encoding=Encoding.PEM,
            format=PrivateFormat.PKCS8,
            encryption_algorithm=NoEncryption(),
        ).decode("utf-8")

        # Convert certificate to PEM format
        cert_pem = certificate.public_bytes(Encoding.PEM).decode("utf-8")

        _LOGGER.debug("Successfully extracted certificate and key from P12 container")
        return cert_pem, key_pem

    except GoveeApiError:
        raise
    except Exception as err:
        raise GoveeApiError(f"Failed to parse P12 certificate: {err}") from err


@dataclass
class GoveeIotCredentials:
    """Credentials for AWS IoT MQTT connection."""

    token: str
    refresh_token: str
    account_topic: str
    iot_cert: str
    iot_key: str
    iot_ca: str | None
    client_id: str
    endpoint: str

    @property
    def is_valid(self) -> bool:
        """Check if credentials appear valid."""
        return bool(
            self.token and self.iot_cert and self.iot_key and self.account_topic
        )


class GoveeAuthClient:
    """Client for Govee account authentication.

    Handles login to obtain AWS IoT MQTT certificates for real-time state updates.

    Note: Login is rate-limited to 30 attempts per 24 hours by Govee.
    Credentials should be cached and reused.
    """

    def __init__(
        self,
        session: aiohttp.ClientSession | None = None,
        hass: HomeAssistant | None = None,
    ) -> None:
        """Initialize the auth client.

        Args:
            session: Optional shared aiohttp session. Takes precedence over hass.
            hass: Home Assistant instance — when provided (and no `session`),
                the HA-managed clientsession is used so the client participates
                in HA shutdown/DNS lifecycle (Platinum rule `inject-websession`).
        """
        self._session = session
        self._owns_session = session is None
        # Stored after login() so subsequent calls (get_iot_key,
        # fetch_device_topics) reuse the same client_id. Govee rejects
        # inconsistent client_ids within a single auth session.
        self._client_id: str | None = None
        # Raw device list from the most recent fetch_bff_leak_sensors() call,
        # retained so a PII-free census can be built for diagnostics (#87) to
        # reveal whether the BFF API returns a given leak SKU at all.
        # Untyped (raw JSON); items are validated in bff_device_census().
        self._last_bff_raw_devices: list[Any] = []
        # Full raw BFF response, for the PII-free structural skeleton (#87) —
        # so a diagnostics download reveals whether leak sensors are absent vs.
        # merely under a different path/SKU than discovery expects.
        self._last_bff_raw_response: Any = None

        if session is None and hass is not None:
            from homeassistant.helpers.aiohttp_client import async_get_clientsession

            self._session = async_get_clientsession(hass)
            self._owns_session = False

    async def __aenter__(self) -> GoveeAuthClient:
        """Async context manager entry."""
        self._require_session()
        return self

    def _require_session(self) -> aiohttp.ClientSession:
        """Return the underlying session; raise if not configured."""
        if self._session is None:
            raise RuntimeError(
                "GoveeAuthClient requires either a `session` or `hass` parameter "
                "at construction. Pass `hass=hass` so the HA-managed "
                "clientsession is used (Platinum rule `inject-websession`)."
            )
        return self._session

    async def __aexit__(self, *args: Any) -> None:
        """Async context manager exit."""
        await self.close()

    async def close(self) -> None:
        """Close the session if we own it."""
        if self._owns_session and self._session:
            await self._session.close()
            self._session = None

    @staticmethod
    def _build_govee_headers(client_id: str | None = None) -> dict[str, str]:
        """Build standard Govee app headers for API requests."""
        if client_id is None:
            client_id = uuid.uuid4().hex
        return {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "appVersion": GOVEE_APP_VERSION,
            "clientId": client_id,
            "clientType": GOVEE_CLIENT_TYPE,
            "iotVersion": GOVEE_IOT_VERSION,
            "timestamp": str(int(time.time() * 1000)),
            "User-Agent": GOVEE_USER_AGENT,
        }

    async def get_iot_key(
        self,
        token: str,
        client_id: str | None = None,
    ) -> dict[str, Any]:
        """Fetch IoT credentials from Govee API.

        Args:
            token: Authentication token from login response.
            client_id: Client ID to use in headers. Defaults to the one
                stored during login() (which is what Govee expects —
                the Bearer token is bound to the login's client_id).

        Returns:
            Dict with keys: p12, p12_pass, endpoint, etc.

        Raises:
            GoveeApiError: If the request fails.
        """
        cid = client_id or self._client_id
        headers = self._build_govee_headers(cid)
        headers["Authorization"] = f"Bearer {token}"

        _LOGGER.debug("Fetching IoT credentials from Govee API")

        try:
            async with self._require_session().get(
                GOVEE_IOT_KEY_URL,
                headers=headers,
            ) as response:
                data = await response.json()
                _LOGGER.debug("Govee IoT key HTTP response: status=%d", response.status)

                if response.status != 200:
                    message = data.get("message", f"HTTP {response.status}")
                    _LOGGER.warning(
                        "Govee IoT key request failed: status=%d message='%s' response=%s",
                        response.status,
                        message,
                        (
                            _sanitize_response_for_logging(data)
                            if isinstance(data, dict)
                            else data
                        ),
                    )
                    raise GoveeApiError(
                        f"Failed to get IoT key: {message}", code=response.status
                    )

                # IoT key response wraps data in a "data" field
                return data.get("data", {}) if isinstance(data, dict) else {}

        except aiohttp.ClientError as err:
            _LOGGER.warning(
                "Connection error fetching IoT key: %s (%s)",
                type(err).__name__,
                str(err),
            )
            raise GoveeApiError(f"Connection error getting IoT key: {err}") from err

    async def fetch_device_topics(
        self,
        token: str,
        client_id: str | None = None,
    ) -> dict[str, str]:
        """Fetch device-specific MQTT topics from undocumented Govee API.

        This API returns device_ext.device_settings.topic for each device,
        which is required for publishing MQTT commands (ptReal, etc).

        Args:
            token: Authentication token from login response.
            client_id: Client ID to use in headers. Defaults to the one
                stored during login() — must match the login's client_id
                or Govee will reject the Bearer token.

        Returns:
            Dict mapping device_id to MQTT topic.

        Raises:
            GoveeApiError: If the request fails.
        """
        cid = client_id or self._client_id
        headers = self._build_govee_headers(cid)
        headers["Authorization"] = f"Bearer {token}"

        try:
            async with self._require_session().post(
                GOVEE_DEVICE_LIST_URL,
                headers=headers,
                json={},  # Empty body required for POST
            ) as response:
                data = await response.json()

                if response.status != 200:
                    message = data.get("message", f"HTTP {response.status}")
                    raise GoveeApiError(
                        f"Failed to get device list: {message}", code=response.status
                    )

                # Extract device topics from response
                # Structure: devices[].device_ext.device_settings.topic
                device_topics: dict[str, str] = {}
                devices = data.get("devices", [])

                for device in devices:
                    device_id = device.get("device")
                    if not device_id:
                        continue

                    # device_ext may be a JSON string that needs parsing
                    device_ext = device.get("deviceExt", {})
                    if isinstance(device_ext, str):
                        try:
                            device_ext = json.loads(device_ext)
                        except (json.JSONDecodeError, TypeError):
                            device_ext = {}

                    # device_settings may also be a JSON string
                    device_settings = device_ext.get("deviceSettings", {})
                    if isinstance(device_settings, str):
                        try:
                            device_settings = json.loads(device_settings)
                        except (json.JSONDecodeError, TypeError):
                            device_settings = {}

                    topic = device_settings.get("topic")
                    if topic:
                        device_topics[device_id] = topic
                        _LOGGER.debug(
                            "Device %s has MQTT topic: %s...", device_id, topic[:30]
                        )
                    else:
                        # Log missing topics - group devices (numeric IDs) never have topics
                        # because they're virtual aggregation entities, not physical devices
                        is_likely_group = device_id.isdigit() if device_id else False
                        if is_likely_group:
                            _LOGGER.debug(
                                "Group device %s has no MQTT topic (expected - groups are virtual)",
                                device_id,
                            )
                        else:
                            _LOGGER.debug(
                                "Device %s has no MQTT topic in response",
                                device_id,
                            )

                _LOGGER.info("Fetched MQTT topics for %d devices", len(device_topics))
                return device_topics

        except aiohttp.ClientError as err:
            raise GoveeApiError(
                f"Connection error fetching device topics: {err}"
            ) from err

    async def fetch_bff_thermo_hygrometers(
        self,
        token: str,
    ) -> list[dict[str, Any]]:
        """Fetch thermo-hygrometer devices (e.g. H5301) from the BFF device list.

        These battery WiFi sensors are absent from the Developer API
        ``/user/devices`` list (issue #86), but appear here. Identity comes from
        the top-level fields + ``deviceExt.deviceSettings``; the last temperature
        / humidity reading from ``deviceExt.lastDeviceData``.

        Args:
            token: Authentication token (from app2 login).

        Returns:
            List of dicts, each with keys: device_id, name, sku, sw_version,
            hw_version, battery, online, temperature (°C or None), humidity
            (%RH or None).
        """
        if self._session is None:
            self._session = aiohttp.ClientSession()
            self._owns_session = True

        headers = {
            "Authorization": f"Bearer {token}",
            "appVersion": GOVEE_APP_VERSION,
            "clientType": GOVEE_CLIENT_TYPE,
            "iotVersion": GOVEE_IOT_VERSION,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

        try:
            async with self._session.get(
                GOVEE_BFF_DEVICE_LIST_URL,
                headers=headers,
            ) as response:
                data = await response.json()

                if response.status == 401:
                    message = data.get("message", "Unauthorized")
                    raise GoveeAuthError(f"BFF API auth failed (401): {message}")

                if response.status != 200:
                    message = data.get("message", f"HTTP {response.status}")
                    raise GoveeApiError(
                        f"BFF device list failed: {message}",
                        code=response.status,
                    )

                devices = data.get("data", {}).get("devices", [])
                # Retain for the diagnostics census (#87 / #86 triage).
                self._last_bff_raw_devices = (
                    devices if isinstance(devices, list) else []
                )
                self._last_bff_raw_response = data

                sensors: list[dict[str, Any]] = []
                for device in devices:
                    sku = device.get("sku", "")
                    if sku not in THERMO_HYGRO_BFF_SKUS:
                        continue

                    device_id = device.get("device", "")
                    name = device.get("deviceName", sku)

                    device_ext = device.get("deviceExt", {})
                    if isinstance(device_ext, str):
                        try:
                            device_ext = json.loads(device_ext)
                        except (json.JSONDecodeError, TypeError):
                            device_ext = {}

                    settings = device_ext.get("deviceSettings", {})
                    if isinstance(settings, str):
                        try:
                            settings = json.loads(settings)
                        except (json.JSONDecodeError, TypeError):
                            settings = {}
                    settings = settings if isinstance(settings, dict) else {}

                    ld = device_ext.get("lastDeviceData", {})
                    if isinstance(ld, str):
                        try:
                            ld = json.loads(ld)
                        except (json.JSONDecodeError, TypeError):
                            ld = {}
                    ld = ld if isinstance(ld, dict) else {}

                    gateway_info = settings.get("gatewayInfo", {})
                    gateway_info = (
                        gateway_info if isinstance(gateway_info, dict) else {}
                    )

                    # fahOpen / temCali / humCali change the *displayed* reading
                    # in the Govee app, but we have no ground-truth confirming
                    # whether BFF `tem`/`hum` are pre- or post-transform. Capture
                    # them (debug + returned dict) WITHOUT altering the value so a
                    # real diagnostic can settle the encoding before we act (#86).
                    fah_open = settings.get("fahOpen")
                    tem_cali = settings.get("temCali")
                    hum_cali = settings.get("humCali")

                    # The exact reading keys/scaling for H5301 are unverified —
                    # log the raw lastDeviceData so a diagnostics download / debug
                    # log from issue #86 reveals the true shape, then refine
                    # _BFF_TEMP_KEYS / _BFF_HUMIDITY_KEYS if needed.
                    _LOGGER.debug(
                        "BFF thermo-hygrometer %s (%s) lastDeviceData keys=%s "
                        "fahOpen=%s temCali=%s humCali=%s gateway=%s",
                        name,
                        sku,
                        sorted(ld.keys()),
                        fah_open,
                        tem_cali,
                        hum_cali,
                        gateway_info.get("sku"),
                    )

                    sensors.append(
                        {
                            "device_id": device_id,
                            "name": name,
                            "sku": sku,
                            "sw_version": settings.get("versionSoft", ""),
                            "hw_version": settings.get("versionHard", ""),
                            "battery": settings.get("battery"),
                            "online": ld.get("online", True),
                            "temperature": _bff_reading(ld, _BFF_TEMP_KEYS),
                            "humidity": _bff_reading(ld, _BFF_HUMIDITY_KEYS),
                            "hub_device_id": gateway_info.get("device", ""),
                            "hub_sku": gateway_info.get("sku", ""),
                            # Instrumentation only — not applied to readings (#86).
                            "fah_open": fah_open,
                            "tem_cali": tem_cali,
                            "hum_cali": hum_cali,
                        }
                    )

                _LOGGER.info(
                    "Discovered %d thermo-hygrometers from BFF API", len(sensors)
                )
                return sensors

        except aiohttp.ClientError as err:
            raise GoveeApiError(
                f"Connection error fetching BFF device list: {err}"
            ) from err

    async def fetch_bff_leak_sensors(
        self,
        token: str,
    ) -> tuple[
        list[dict[str, Any]],
        dict[str, dict[str, Any]],
        dict[str, dict[str, Any]],
    ]:
        """Fetch leak sensor sub-devices and their hubs from the BFF device list API.

        The BFF API returns rich device data including sub-devices like
        H5058 leak sensors with their slot number (sno), battery level,
        and gateway information.

        Args:
            token: Authentication token (from app2 login).

        Returns:
            Tuple of (sensors, hubs):
            - sensors: list of dicts, each with keys:
              device_id, name, sku, hub_device_id, sno, battery,
              hw_version, sw_version, online, gateway_online,
              last_wet_time, read
            - hubs: dict mapping hub_device_id -> {sku, name}

        Raises:
            GoveeAuthError: If the server returns 401 (token expired).
            GoveeApiError: If the request fails for other reasons.
        """
        if self._session is None:
            self._session = aiohttp.ClientSession()
            self._owns_session = True

        headers = {
            "Authorization": f"Bearer {token}",
            "appVersion": GOVEE_APP_VERSION,
            "clientType": GOVEE_CLIENT_TYPE,
            "iotVersion": GOVEE_IOT_VERSION,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

        try:
            async with self._session.get(
                GOVEE_BFF_DEVICE_LIST_URL,
                headers=headers,
            ) as response:
                data = await response.json()

                if response.status == 401:
                    message = data.get("message", "Unauthorized")
                    raise GoveeAuthError(f"BFF API auth failed (401): {message}")

                if response.status != 200:
                    message = data.get("message", f"HTTP {response.status}")
                    raise GoveeApiError(
                        f"BFF device list failed: {message}",
                        code=response.status,
                    )

                sensors: list[dict[str, Any]] = []
                devices = data.get("data", {}).get("devices", [])
                # Retain for the diagnostics census + skeleton (PII-free; #87).
                self._last_bff_raw_devices = (
                    devices if isinstance(devices, list) else []
                )
                self._last_bff_raw_response = data
                for device in devices:
                    sku = device.get("sku", "")
                    if sku not in LEAK_SENSOR_SKUS:
                        continue

                    device_id = device.get("device", "")
                    name = device.get("deviceName", sku)

                    # Extract settings for sno and gateway info
                    # deviceExt fields may be JSON strings that need parsing
                    device_ext = device.get("deviceExt", {})
                    if isinstance(device_ext, str):
                        try:
                            device_ext = json.loads(device_ext)
                        except (json.JSONDecodeError, TypeError):
                            device_ext = {}

                    device_settings = device_ext.get("deviceSettings", {})
                    if isinstance(device_settings, str):
                        try:
                            device_settings = json.loads(device_settings)
                        except (json.JSONDecodeError, TypeError):
                            device_settings = {}

                    sno = device_settings.get("sno")
                    if sno is None:
                        _LOGGER.debug(
                            "Leak sensor %s (%s) has no sno, skipping",
                            name,
                            device_id,
                        )
                        continue

                    # Get gateway (hub) device ID
                    gateway_info = device_settings.get("gatewayInfo", {})
                    hub_device_id = gateway_info.get("device", "")

                    sensors.append(
                        {
                            "device_id": device_id,
                            "name": name,
                            "sku": sku,
                            "hub_device_id": hub_device_id,
                            "sno": int(sno),
                            "battery": device_settings.get("battery"),
                            "hw_version": device_settings.get("versionHard", ""),
                            "sw_version": device_settings.get("versionSoft", ""),
                        }
                    )

                # Second pass: attach lastDeviceData for state
                device_state_map: dict[str, dict[str, Any]] = {}
                for device in devices:
                    sku = device.get("sku", "")
                    if sku not in LEAK_SENSOR_SKUS:
                        continue
                    device_id = device.get("device", "")
                    device_ext = device.get("deviceExt", {})
                    if isinstance(device_ext, str):
                        try:
                            device_ext = json.loads(device_ext)
                        except (json.JSONDecodeError, TypeError):
                            device_ext = {}
                    ld = device_ext.get("lastDeviceData", {})
                    if isinstance(ld, str):
                        try:
                            ld = json.loads(ld)
                        except (json.JSONDecodeError, TypeError):
                            ld = {}
                    device_state_map[device_id] = ld

                # Attach state to sensor dicts
                for sensor in sensors:
                    ld = device_state_map.get(sensor["device_id"], {})
                    sensor["online"] = ld.get("online", True)
                    sensor["gateway_online"] = ld.get("gwonline", True)
                    sensor["last_wet_time"] = ld.get("lastTime")
                    sensor["read"] = ld.get("read", True)

                # Collect hub metadata (SKU, name) for known leak hubs.
                # Note: in practice the BFF /device/list endpoint does not
                # include the hub itself as a top-level device, only its
                # child leak sensors. We keep this loop for completeness in
                # case Govee changes that behavior.
                hubs: dict[str, dict[str, Any]] = {}
                for device in devices:
                    sku = device.get("sku", "")
                    if sku not in LEAK_HUB_SKUS:
                        continue
                    hub_id = device.get("device", "")
                    if not hub_id:
                        continue
                    hubs[hub_id] = {
                        "sku": sku,
                        "name": device.get("deviceName", sku),
                    }

                # Note which non-leak, Developer-API thermometers (e.g. H5110/
                # H5075 via an H5151/H5044 gateway, H5179) the BFF list carries a
                # reading for. The Developer /device/state endpoint returns a
                # stale cached reading for these BLE-bridged sensors and only
                # refreshes lazily — but calling THIS BFF endpoint tickles Govee's
                # cloud into refreshing it (issue #83, confirmed by @davcamer).
                # We return the raw tem/hum so the coordinator knows which devices
                # to keep the tickle running for, but the values are NOT applied:
                # the BFF scale varies by gateway/firmware (e.g. hum is hundredths
                # for H5110-via-H5044/H5151 and H5179, not the tenths once assumed),
                # and a fixed divisor mis-scaled humidity 10x (issue #102). The
                # correctly scaled, unit-handled value comes from the Developer
                # poll. H5301/H5310 are skipped; the dedicated thermo path owns them.
                thermo_readings: dict[str, dict[str, Any]] = {}
                for device in devices:
                    sku = device.get("sku", "")
                    if (
                        sku in LEAK_SENSOR_SKUS
                        or sku in LEAK_HUB_SKUS
                        or sku in THERMO_HYGRO_BFF_SKUS
                    ):
                        continue
                    device_id = device.get("device", "")
                    if not device_id:
                        continue
                    device_ext = device.get("deviceExt", {})
                    if isinstance(device_ext, str):
                        try:
                            device_ext = json.loads(device_ext)
                        except (json.JSONDecodeError, TypeError):
                            device_ext = {}
                    ld = device_ext.get("lastDeviceData", {})
                    if isinstance(ld, str):
                        try:
                            ld = json.loads(ld)
                        except (json.JSONDecodeError, TypeError):
                            ld = {}
                    ld = ld if isinstance(ld, dict) else {}
                    tem = ld.get("tem")
                    hum = ld.get("hum")
                    if tem is not None or hum is not None:
                        thermo_readings[device_id] = {"tem": tem, "hum": hum}

                _LOGGER.info(
                    "Discovered %d leak sensors, %d hubs, %d thermo readings "
                    "from BFF API",
                    len(sensors),
                    len(hubs),
                    len(thermo_readings),
                )
                return sensors, hubs, thermo_readings

        except aiohttp.ClientError as err:
            raise GoveeApiError(
                f"Connection error fetching BFF device list: {err}"
            ) from err

    async def fetch_water_detector_states(
        self,
        token: str,
        device_ids: set[str],
    ) -> dict[str, dict[str, Any]]:
        """Fetch online/battery/last-report state for standalone water detectors.

        Standalone H5054 detectors (issue #62) are RF-only leaves bridged to the
        cloud by an H5040 WiFi gateway. They appear in the BFF device list with
        ``lastDeviceData`` carrying ``online``/``gwonline``/``lastTime`` and
        ``deviceSettings`` carrying ``battery`` — but their leak *trip* lives in
        the separate warnMessage history (see ``fetch_leak_warning``).

        Args:
            token: Account token (from app2 login).
            device_ids: Device IDs to return state for (developer-API format).

        Returns:
            ``{device_id: {"online", "gateway_online", "battery", "last_time"}}``
            for each requested device found in the BFF list.
        """
        if self._session is None:
            self._session = aiohttp.ClientSession()
            self._owns_session = True

        # Same minimal header set as fetch_bff_leak_sensors (proven to return
        # 200). Deliberately NO clientId: this client is created fresh per poll
        # and never logged in, so its random client_id would NOT match the one
        # the Bearer token is bound to and Govee would reject it (see
        # fetch_device_topics). The BFF endpoint accepts the token without it.
        headers = {
            "Authorization": f"Bearer {token}",
            "appVersion": GOVEE_APP_VERSION,
            "clientType": GOVEE_CLIENT_TYPE,
            "iotVersion": GOVEE_IOT_VERSION,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

        # The BFF list uses colon-stripped device IDs; map both ways so callers
        # can pass the developer-API (colon) form and get it back.
        wanted = {d.replace(":", "").upper(): d for d in device_ids}
        result: dict[str, dict[str, Any]] = {}

        try:
            async with self._session.get(
                GOVEE_BFF_DEVICE_LIST_URL,
                headers=headers,
            ) as response:
                data = await response.json()
                if response.status == 401:
                    raise GoveeAuthError("BFF API auth failed (401)")
                if response.status != 200:
                    message = data.get("message", f"HTTP {response.status}")
                    raise GoveeApiError(
                        f"BFF device list failed: {message}", code=response.status
                    )

                for device in data.get("data", {}).get("devices", []):
                    raw_id = device.get("device", "")
                    key = raw_id.replace(":", "").upper()
                    if key not in wanted:
                        continue
                    device_ext = device.get("deviceExt", {})
                    if isinstance(device_ext, str):
                        try:
                            device_ext = json.loads(device_ext)
                        except (json.JSONDecodeError, TypeError):
                            device_ext = {}
                    settings = device_ext.get("deviceSettings", {})
                    if isinstance(settings, str):
                        try:
                            settings = json.loads(settings)
                        except (json.JSONDecodeError, TypeError):
                            settings = {}
                    ld = device_ext.get("lastDeviceData", {})
                    if isinstance(ld, str):
                        try:
                            ld = json.loads(ld)
                        except (json.JSONDecodeError, TypeError):
                            ld = {}
                    result[wanted[key]] = {
                        "online": bool(ld.get("online", True)),
                        "gateway_online": bool(ld.get("gwonline", True)),
                        "battery": _safe_int(settings.get("battery")),
                        "last_time": _safe_int(ld.get("lastTime")),
                    }
                return result

        except aiohttp.ClientError as err:
            raise GoveeApiError(
                f"Connection error fetching water-detector states: {err}"
            ) from err

    async def fetch_leak_warning(
        self,
        token: str,
        device_id: str,
        sku: str,
    ) -> bool:
        """Return True if a standalone water detector has an unread leak alert.

        Mirrors homebridge-govee's H5054 path: POST the device's warnMessage
        history and treat an unread ``LeakageAlert`` entry as wet. The trip
        latches until the user reads the alert in the Govee app (the only
        signal Govee exposes for these gateway-bridged RF sensors, issue #62).

        Args:
            token: Account token (from app2 login).
            device_id: Device ID (developer-API or colon form; stripped here).
            sku: Device SKU (e.g. ``H5054``).

        Returns:
            True if an unread leak alert exists, False otherwise.
        """
        if self._session is None:
            self._session = aiohttp.ClientSession()
            self._owns_session = True

        # Minimal proven header set, no clientId — see fetch_water_detector_states.
        headers = {
            "Authorization": f"Bearer {token}",
            "appVersion": GOVEE_APP_VERSION,
            "clientType": GOVEE_CLIENT_TYPE,
            "iotVersion": GOVEE_IOT_VERSION,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        body = {"device": device_id.replace(":", ""), "limit": 50, "sku": sku}

        try:
            async with self._session.post(
                GOVEE_LEAK_WARN_URL,
                headers=headers,
                json=body,
            ) as response:
                data = await response.json()
                if response.status == 401:
                    raise GoveeAuthError("warnMessage auth failed (401)")
                if response.status != 200:
                    message = data.get("message", f"HTTP {response.status}")
                    raise GoveeApiError(
                        f"warnMessage failed: {message}", code=response.status
                    )

                messages = data.get("data", [])
                if not isinstance(messages, list):
                    return False
                # Log the raw shape once so the reverse-engineered field names
                # can be confirmed against real accounts (issue #62).
                _LOGGER.debug(
                    "warnMessage for %s (%s): %d entries raw=%s",
                    device_id,
                    sku,
                    len(messages),
                    messages[:3],
                )
                return any(
                    isinstance(m, dict)
                    and not m.get("read", True)
                    and str(m.get("message", ""))
                    .lower()
                    .replace(" ", "")
                    .startswith("leakagealert")
                    for m in messages
                )

        except aiohttp.ClientError as err:
            raise GoveeApiError(
                f"Connection error fetching leak warning: {err}"
            ) from err

    def bff_device_census(self) -> list[dict[str, Any]]:
        """PII-free summary of the last BFF device list, for diagnostics (#87).

        Returns one entry per device the BFF API returned — SKU plus whether it
        carries the fields leak-sensor discovery requires (`sno`, `gatewayInfo`)
        — with no MACs or names. Lets a diagnostics download answer "does the
        BFF return this leak SKU, and does our SKU allowlist / parser match it?"
        without exposing identities or needing verbose logging.
        """
        census: list[dict[str, Any]] = []
        for device in self._last_bff_raw_devices:
            if not isinstance(device, dict):
                continue
            sku = device.get("sku", "")
            device_ext = device.get("deviceExt", {})
            if isinstance(device_ext, str):
                try:
                    device_ext = json.loads(device_ext)
                except (json.JSONDecodeError, TypeError):
                    device_ext = {}
            settings = (
                device_ext.get("deviceSettings", {})
                if isinstance(device_ext, dict)
                else {}
            )
            if isinstance(settings, str):
                try:
                    settings = json.loads(settings)
                except (json.JSONDecodeError, TypeError):
                    settings = {}
            settings = settings if isinstance(settings, dict) else {}
            gateway = settings.get("gatewayInfo", {})
            sno = settings.get("sno")
            census.append(
                {
                    "sku": sku,
                    "in_leak_sensor_skus": sku in LEAK_SENSOR_SKUS,
                    "in_leak_hub_skus": sku in LEAK_HUB_SKUS,
                    "in_thermo_hygro_skus": sku in THERMO_HYGRO_BFF_SKUS,
                    "has_sno": sno is not None,
                    # The slot number itself is a small int (0-7), not PII —
                    # surfacing it lets us confirm slot<->sno alignment against
                    # the recent_multisync events in one capture.
                    "sno": sno,
                    "has_gateway_info": bool(gateway),
                    "gateway_sku": (
                        gateway.get("sku") if isinstance(gateway, dict) else None
                    ),
                }
            )
        return census

    def bff_response_skeleton(self) -> Any:
        """PII-free structural skeleton of the last raw BFF response (#87).

        Emits only field names + value *types* + container lengths — never
        scalar values — recursing into JSON-encoded string fields (Govee nests
        JSON as text). MAC-shaped dict keys are dropped. This reveals whether
        leak sensors are returned under an unexpected path/shape (so discovery
        finds 0) versus genuinely absent from the BFF response — the question
        an empty census alone cannot answer.
        """
        if self._last_bff_raw_response is None:
            return None
        return _shape_skeleton(self._last_bff_raw_response)

    async def request_verification_code(
        self,
        email: str,
        client_id: str,
    ) -> None:
        """Request Govee to send a 2FA verification code to the user's email.

        Args:
            email: Govee account email.
            client_id: Client ID to use in headers (must match login request).

        Raises:
            GoveeApiError: If the request fails.
        """
        headers = self._build_govee_headers(client_id)
        payload = {"type": 8, "email": email}

        _LOGGER.debug("Requesting Govee verification code for %s", email)

        try:
            async with self._require_session().post(
                GOVEE_VERIFICATION_URL,
                json=payload,
                headers=headers,
            ) as response:
                if response.status != 200:
                    raise GoveeApiError(
                        f"Failed to request verification code: HTTP {response.status}"
                    )
                _LOGGER.debug("Verification code requested for %s", email)
        except aiohttp.ClientError as err:
            raise GoveeApiError(
                f"Connection error requesting verification code: {err}"
            ) from err

    async def login(
        self,
        email: str,
        password: str,
        client_id: str | None = None,
        code: str | None = None,
    ) -> GoveeIotCredentials:
        """Login to Govee account to obtain AWS IoT credentials.

        Args:
            email: Govee account email.
            password: Govee account password.
            client_id: Optional client ID (32-char UUID). Generated if not provided.
            code: Optional 2FA verification code from email.

        Returns:
            GoveeIotCredentials with AWS IoT connection details.

        Raises:
            Govee2FARequiredError: 2FA code needed (status 454, no code provided).
            Govee2FACodeInvalidError: Provided code was invalid (status 454, code provided).
            GoveeAuthError: Invalid credentials or login failed.
            GoveeApiError: API communication error.
        """
        if client_id is None:
            # Derive a stable client_id from the email so every login for
            # the same account uses the same ID. Govee caches
            # (email, client_id) and rejects new IDs on hardened accounts.
            client_id = _derive_client_id(email)

        # Store on instance so get_iot_key() and fetch_device_topics()
        # can reuse the same client_id without a fresh random UUID
        self._client_id = client_id

        payload: dict[str, Any] = {
            "email": email,
            "password": password,
            "client": client_id,
            "clientType": GOVEE_CLIENT_TYPE,
        }
        if code:
            payload["code"] = code

        headers = self._build_govee_headers(client_id)

        _LOGGER.debug("Attempting Govee account login")

        try:
            async with self._require_session().post(
                GOVEE_LOGIN_URL,
                json=payload,
                headers=headers,
            ) as response:
                data = await response.json()
                _LOGGER.debug("Govee login HTTP response: status=%d", response.status)

                if response.status == 401:
                    _LOGGER.debug(
                        "Govee login failed with HTTP 401. Response: %s",
                        (
                            _sanitize_response_for_logging(data)
                            if isinstance(data, dict)
                            else data
                        ),
                    )
                    raise GoveeAuthError("Invalid email or password", code=401)

                if response.status != 200:
                    message = data.get("message", f"HTTP {response.status}")
                    _LOGGER.warning(
                        "Govee login failed with HTTP %d: %s. Response: %s",
                        response.status,
                        message,
                        (
                            _sanitize_response_for_logging(data)
                            if isinstance(data, dict)
                            else data
                        ),
                    )
                    raise GoveeLoginRejectedError(
                        f"Login rejected (HTTP {response.status}): {message}"
                    )

                # Check response status code within JSON
                status = data.get("status")
                if status != 200:
                    message = data.get("message", "Login failed")
                    _LOGGER.warning(
                        "Govee login error: status=%s message='%s' response=%s",
                        status,
                        message,
                        (
                            _sanitize_response_for_logging(data)
                            if isinstance(data, dict)
                            else data
                        ),
                    )
                    if status == 454:
                        if code:
                            raise Govee2FACodeInvalidError()
                        raise Govee2FARequiredError()
                    if status == 401 or "password" in message.lower():
                        raise GoveeAuthError(message, code=status)
                    raise GoveeLoginRejectedError(
                        f"Login rejected (status {status}): {message}"
                    )

                client_data = data.get("client", {})

                # Get token from login response
                token = client_data.get("token", "")
                if not token:
                    raise GoveeApiError("No token in login response")

                # Fetch IoT credentials from separate endpoint
                iot_data = await self.get_iot_key(token)

                # Extract AWS IoT credentials (PEM or P12 format)
                iot_endpoint = iot_data.get(
                    "endpoint", "aqm3wd1qlc3dy-ats.iot.us-east-1.amazonaws.com"
                )

                # Check for direct PEM format first
                cert_pem = iot_data.get("certificatePem", "")
                key_pem = iot_data.get("privateKey", "")

                if not (cert_pem and key_pem):
                    # Fall back to P12 container format
                    p12_base64 = iot_data.get("p12", "")
                    p12_password = iot_data.get("p12Pass") or iot_data.get(
                        "p12_pass", ""
                    )

                    if not p12_base64:
                        raise GoveeApiError("No certificate data in IoT key response")

                    cert_pem, key_pem = _extract_p12_credentials(
                        p12_base64, p12_password
                    )

                # Build MQTT client ID: AP/{accountId}/{uuid}
                account_id = str(client_data.get("accountId", ""))
                mqtt_client_id = (
                    f"AP/{account_id}/{client_id}" if account_id else client_id
                )

                credentials = GoveeIotCredentials(
                    token=token,
                    refresh_token=client_data.get("refreshToken", ""),
                    account_topic=client_data.get("topic", ""),
                    iot_cert=cert_pem,
                    iot_key=key_pem,
                    iot_ca=client_data.get("caCertificate"),
                    client_id=mqtt_client_id,
                    endpoint=iot_endpoint,
                )

                if not credentials.is_valid:
                    raise GoveeApiError("Missing IoT credentials in response")

                _LOGGER.info("Successfully authenticated with Govee")
                return credentials

        except aiohttp.ClientError as err:
            _LOGGER.warning(
                "Connection error during Govee login: %s (%s)",
                type(err).__name__,
                str(err),
            )
            raise GoveeApiError(f"Connection error during login: {err}") from err


async def validate_govee_credentials(
    email: str,
    password: str,
    code: str | None = None,
    client_id: str | None = None,
    session: aiohttp.ClientSession | None = None,
    hass: HomeAssistant | None = None,
) -> GoveeIotCredentials:
    """Validate Govee account credentials and return IoT credentials.

    Convenience function for config flow validation.

    Args:
        email: Govee account email.
        password: Govee account password.
        code: Optional 2FA verification code.
        client_id: Optional client ID (reuse from code request).
        session: Optional aiohttp session. Takes precedence over hass.
        hass: HA instance — when provided (and no session), the HA-managed
            clientsession is used.

    Returns:
        GoveeIotCredentials if valid.

    Raises:
        Govee2FARequiredError: 2FA verification code needed.
        Govee2FACodeInvalidError: Provided code was invalid.
        GoveeAuthError: Invalid credentials.
        GoveeApiError: API communication error.
    """
    async with GoveeAuthClient(session=session, hass=hass) as client:
        return await client.login(email, password, client_id=client_id, code=code)
