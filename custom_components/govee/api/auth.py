"""Govee authentication API for AWS IoT MQTT credentials.

Authenticates with Govee's account API to obtain certificates for AWS IoT MQTT
which provides real-time device state updates.

Reference: homebridge-govee, govee2mqtt implementations
"""

from __future__ import annotations

import base64
import json
import logging
import time
import uuid
from dataclasses import dataclass
from typing import Any

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


# Govee Account API endpoints
GOVEE_LOGIN_URL = "https://app2.govee.com/account/rest/account/v2/login"
GOVEE_VERIFICATION_URL = (
    "https://app2.govee.com/account/rest/account/v1/verification"
)
GOVEE_IOT_KEY_URL = "https://app2.govee.com/app/v1/account/iot/key"
GOVEE_DEVICE_LIST_URL = "https://app2.govee.com/device/rest/devices/v1/list"
GOVEE_CLIENT_TYPE = "1"
GOVEE_APP_VERSION = "6.5.02"
GOVEE_IOT_VERSION = "0"
GOVEE_USER_AGENT = (
    f"GoveeHome/{GOVEE_APP_VERSION} "
    "(com.ihoment.GoVeeSensor; build:2; iOS 18.4.0) Alamofire/5.10.2"
)


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
    ) -> None:
        """Initialize the auth client.

        Args:
            session: Optional shared aiohttp session.
        """
        self._session = session
        self._owns_session = session is None
        # Stored after login() so subsequent calls (get_iot_key,
        # fetch_device_topics) reuse the same client_id. Govee rejects
        # inconsistent client_ids within a single auth session.
        self._client_id: str | None = None

    async def __aenter__(self) -> GoveeAuthClient:
        """Async context manager entry."""
        if self._session is None:
            self._session = aiohttp.ClientSession()
        return self

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
        if self._session is None:
            self._session = aiohttp.ClientSession()
            self._owns_session = True

        cid = client_id or self._client_id
        headers = self._build_govee_headers(cid)
        headers["Authorization"] = f"Bearer {token}"

        _LOGGER.debug("Fetching IoT credentials from Govee API")

        try:
            async with self._session.get(
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
        if self._session is None:
            self._session = aiohttp.ClientSession()
            self._owns_session = True

        cid = client_id or self._client_id
        headers = self._build_govee_headers(cid)
        headers["Authorization"] = f"Bearer {token}"

        try:
            async with self._session.post(
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
        if self._session is None:
            self._session = aiohttp.ClientSession()
            self._owns_session = True

        headers = self._build_govee_headers(client_id)
        payload = {"type": 8, "email": email}

        _LOGGER.debug("Requesting Govee verification code for %s", email)

        try:
            async with self._session.post(
                GOVEE_VERIFICATION_URL,
                json=payload,
                headers=headers,
            ) as response:
                if response.status != 200:
                    raise GoveeApiError(
                        f"Failed to request verification code: HTTP {response.status}"
                    )
                _LOGGER.debug(
                    "Verification code requested for %s", email
                )
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
        if self._session is None:
            self._session = aiohttp.ClientSession()
            self._owns_session = True

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
            async with self._session.post(
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
) -> GoveeIotCredentials:
    """Validate Govee account credentials and return IoT credentials.

    Convenience function for config flow validation.

    Args:
        email: Govee account email.
        password: Govee account password.
        code: Optional 2FA verification code.
        client_id: Optional client ID (reuse from code request).
        session: Optional aiohttp session.

    Returns:
        GoveeIotCredentials if valid.

    Raises:
        Govee2FARequiredError: 2FA verification code needed.
        Govee2FACodeInvalidError: Provided code was invalid.
        GoveeAuthError: Invalid credentials.
        GoveeApiError: API communication error.
    """
    async with GoveeAuthClient(session=session) as client:
        return await client.login(email, password, client_id=client_id, code=code)
