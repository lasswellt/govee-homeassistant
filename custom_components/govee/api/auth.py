"""Govee authentication API for AWS IoT MQTT credentials.

Authenticates with Govee's account API to obtain certificates for AWS IoT MQTT
which provides real-time device state updates.

Reference: homebridge-govee, govee2mqtt implementations
"""
from __future__ import annotations

import base64
import logging
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

from .exceptions import GoveeApiError, GoveeAuthError

_LOGGER = logging.getLogger(__name__)

# Govee Account API endpoints
GOVEE_LOGIN_URL = "https://app2.govee.com/account/rest/account/v1/login"
GOVEE_IOT_KEY_URL = "https://app2.govee.com/app/v1/account/iot/key"
GOVEE_CLIENT_TYPE = "1"  # Android client type


def _extract_p12_credentials(
    p12_base64: str, password: str | None = None
) -> tuple[str, str]:
    """Extract certificate and private key from P12/PFX container.

    Govee API returns AWS IoT credentials as a PKCS#12 (P12/PFX) container
    in base64 encoding. This function extracts the certificate and private
    key and converts them to PEM format for use with SSL/TLS.

    Args:
        p12_base64: Base64-encoded P12/PFX container from Govee API
        password: Optional password for the P12 container

    Returns:
        Tuple of (certificate_pem, private_key_pem)

    Raises:
        GoveeApiError: If P12 extraction fails
    """
    if not p12_base64:
        raise GoveeApiError("Empty P12 data received from Govee API")

    try:
        # Clean base64 string: strip whitespace, newlines
        cleaned = p12_base64.strip().replace("\n", "").replace("\r", "").replace(" ", "")

        # Handle URL-safe base64 (convert - to + and _ to /)
        cleaned = cleaned.replace("-", "+").replace("_", "/")

        # Fix base64 padding if needed (Govee may omit trailing = characters)
        # Base64 strings must have length divisible by 4
        padding_needed = len(cleaned) % 4
        if padding_needed:
            cleaned += "=" * (4 - padding_needed)

        _LOGGER.debug(
            "P12 base64 input: original_length=%d, cleaned_length=%d, padding_added=%d, has_password=%s",
            len(p12_base64),
            len(cleaned),
            4 - padding_needed if padding_needed else 0,
            password is not None,
        )

        # Log first/last few chars to help identify encoding issues (safe - just structure)
        if len(cleaned) > 20:
            _LOGGER.debug(
                "P12 base64 preview: starts_with='%s...', ends_with='...%s'",
                cleaned[:10],
                cleaned[-10:],
            )

        # Decode base64 to get raw P12 bytes
        try:
            p12_data = base64.b64decode(cleaned)
            _LOGGER.debug("P12 decoded successfully: %d bytes", len(p12_data))
        except Exception as b64_err:
            _LOGGER.error(
                "Base64 decode failed: %s (cleaned_length=%d, first_chars='%s')",
                b64_err,
                len(cleaned),
                cleaned[:50] if len(cleaned) > 50 else cleaned,
            )
            raise GoveeApiError(f"Base64 decode failed: {b64_err}") from b64_err

        # Parse PKCS#12 container with optional password
        pwd_bytes = password.encode("utf-8") if password else None
        try:
            private_key, certificate, additional_certs = pkcs12.load_key_and_certificates(
                p12_data, pwd_bytes
            )
            _LOGGER.debug(
                "P12 parsed: has_private_key=%s, has_certificate=%s, additional_certs=%d",
                private_key is not None,
                certificate is not None,
                len(additional_certs) if additional_certs else 0,
            )
        except Exception as p12_err:
            _LOGGER.error(
                "P12 parse failed: %s (data_length=%d, has_password=%s)",
                p12_err,
                len(p12_data),
                password is not None,
            )
            raise GoveeApiError(f"P12 container parse failed: {p12_err}") from p12_err

        if private_key is None:
            _LOGGER.error("P12 container has no private key")
            raise GoveeApiError("No private key found in P12 container")
        if certificate is None:
            _LOGGER.error("P12 container has no certificate")
            raise GoveeApiError("No certificate found in P12 container")

        # Convert private key to PEM format (PKCS8)
        key_pem = private_key.private_bytes(
            encoding=Encoding.PEM,
            format=PrivateFormat.PKCS8,
            encryption_algorithm=NoEncryption(),
        ).decode("utf-8")

        # Convert certificate to PEM format
        cert_pem = certificate.public_bytes(Encoding.PEM).decode("utf-8")

        # Log certificate details for debugging
        _LOGGER.debug(
            "Certificate extracted: subject=%s, issuer=%s, not_valid_after=%s",
            certificate.subject.rfc4514_string() if certificate.subject else "unknown",
            certificate.issuer.rfc4514_string() if certificate.issuer else "unknown",
            certificate.not_valid_after_utc,
        )
        _LOGGER.debug(
            "Key extracted: key_type=%s, key_size=%d bits",
            type(private_key).__name__,
            private_key.key_size if hasattr(private_key, "key_size") else 0,
        )

        _LOGGER.info("Successfully extracted certificate and key from P12 container")
        return cert_pem, key_pem

    except GoveeApiError:
        # Re-raise our own errors without wrapping
        raise
    except Exception as err:
        _LOGGER.error(
            "Unexpected error extracting P12 credentials: %s (%s)",
            err,
            type(err).__name__,
        )
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
        return bool(self.token and self.iot_cert and self.iot_key and self.account_topic)


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
        self._session = session
        self._owns_session = session is None

    async def __aenter__(self) -> GoveeAuthClient:
        if self._session is None:
            self._session = aiohttp.ClientSession()
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self.close()

    async def close(self) -> None:
        """Close the session if we own it."""
        if self._owns_session and self._session:
            await self._session.close()
            self._session = None

    async def get_iot_key(self, token: str) -> dict:
        """Fetch IoT credentials from Govee API.

        This is a separate endpoint from login that returns the P12 certificate
        and password needed for AWS IoT MQTT authentication.

        Args:
            token: Authentication token from login response

        Returns:
            Dict with keys: p12, p12_pass, endpoint, log

        Raises:
            GoveeApiError: If the request fails
        """
        if self._session is None:
            self._session = aiohttp.ClientSession()
            self._owns_session = True

        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

        try:
            async with self._session.get(
                GOVEE_IOT_KEY_URL,
                headers=headers,
            ) as response:
                data = await response.json()

                if response.status != 200:
                    message = data.get("message", f"HTTP {response.status}")
                    raise GoveeApiError(f"Failed to get IoT key: {message}", code=response.status)

                _LOGGER.debug(
                    "IoT key response keys: %s",
                    list(data.keys()) if isinstance(data, dict) else type(data).__name__,
                )

                # The IoT key response wraps the actual data in a "data" field
                iot_data = data.get("data", {}) if isinstance(data, dict) else {}

                _LOGGER.debug(
                    "IoT key data keys: %s",
                    list(iot_data.keys()) if isinstance(iot_data, dict) else type(iot_data).__name__,
                )

                # Log details about IoT key response (without sensitive data)
                if isinstance(iot_data, dict):
                    p12_len = len(iot_data.get("p12", "")) if iot_data.get("p12") else 0
                    has_pass = bool(iot_data.get("p12Pass") or iot_data.get("p12_pass"))
                    endpoint = iot_data.get("endpoint", "not provided")
                    _LOGGER.debug(
                        "IoT key details: p12_length=%d, has_password=%s, endpoint=%s",
                        p12_len,
                        has_pass,
                        endpoint,
                    )

                return iot_data

        except aiohttp.ClientError as err:
            raise GoveeApiError(f"Connection error getting IoT key: {err}") from err

    async def login(
        self,
        email: str,
        password: str,
        client_id: str | None = None,
    ) -> GoveeIotCredentials:
        """Login to Govee account to obtain AWS IoT credentials.

        Args:
            email: Govee account email
            password: Govee account password
            client_id: Optional client ID (32-char UUID). Generated if not provided.

        Returns:
            GoveeIotCredentials with AWS IoT connection details

        Raises:
            GoveeAuthError: Invalid credentials or login failed
            GoveeApiError: API communication error
        """
        if self._session is None:
            self._session = aiohttp.ClientSession()
            self._owns_session = True

        if client_id is None:
            client_id = uuid.uuid4().hex

        payload = {
            "email": email,
            "password": password,
            "client": client_id,
            "clientType": GOVEE_CLIENT_TYPE,
        }

        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

        try:
            async with self._session.post(
                GOVEE_LOGIN_URL,
                json=payload,
                headers=headers,
            ) as response:
                data = await response.json()

                if response.status == 401:
                    raise GoveeAuthError("Invalid email or password")

                if response.status != 200:
                    message = data.get("message", f"HTTP {response.status}")
                    raise GoveeApiError(f"Login failed: {message}", code=response.status)

                # Check response status code within JSON
                status = data.get("status")
                if status != 200:
                    message = data.get("message", "Login failed")
                    if status == 401 or "password" in message.lower():
                        raise GoveeAuthError(message)
                    raise GoveeApiError(f"Login failed: {message}", code=status)

                client_data = data.get("client", {})

                # Debug: log available keys in response
                _LOGGER.debug(
                    "Login response keys: data=%s, client=%s",
                    list(data.keys()),
                    list(client_data.keys()) if client_data else "empty",
                )

                # Get token from login response
                token = client_data.get("token", "")
                if not token:
                    _LOGGER.error("Login response missing token field")
                    raise GoveeApiError("No token in login response")

                _LOGGER.debug("Login successful, fetching IoT credentials...")

                # Fetch IoT credentials from separate endpoint
                # This returns the P12 certificate and password needed for AWS IoT
                iot_data = await self.get_iot_key(token)

                # Extract AWS IoT credentials
                # The API can return credentials in two formats:
                # 1. Direct PEM: certificatePem + privateKey fields
                # 2. P12 container: p12 + p12Pass fields
                iot_endpoint = iot_data.get("endpoint", "aqm3wd1qlc3dy-ats.iot.us-east-1.amazonaws.com")

                # Check for direct PEM format first (certificatePem + privateKey)
                cert_pem = iot_data.get("certificatePem", "")
                key_pem = iot_data.get("privateKey", "")

                if cert_pem and key_pem:
                    _LOGGER.debug(
                        "IoT credentials received (PEM format): cert_length=%d, key_length=%d, endpoint=%s",
                        len(cert_pem),
                        len(key_pem),
                        iot_endpoint,
                    )
                else:
                    # Fall back to P12 container format
                    p12_base64 = iot_data.get("p12", "")
                    p12_password = iot_data.get("p12Pass") or iot_data.get("p12_pass", "")

                    if not p12_base64:
                        _LOGGER.error(
                            "IoT key response missing credentials. Available keys: %s",
                            list(iot_data.keys()) if isinstance(iot_data, dict) else "none",
                        )
                        raise GoveeApiError("No certificate data in IoT key response")

                    _LOGGER.debug(
                        "IoT credentials received (P12 format): p12_length=%d, has_password=%s, endpoint=%s",
                        len(p12_base64),
                        bool(p12_password),
                        iot_endpoint,
                    )

                    cert_pem, key_pem = _extract_p12_credentials(p12_base64, p12_password)

                credentials = GoveeIotCredentials(
                    token=token,
                    refresh_token=client_data.get("refreshToken", ""),
                    account_topic=client_data.get("topic", ""),
                    iot_cert=cert_pem,
                    iot_key=key_pem,
                    iot_ca=client_data.get("caCertificate"),
                    client_id=client_id,
                    endpoint=iot_endpoint,
                )

                if not credentials.is_valid:
                    _LOGGER.warning(
                        "Login succeeded but missing IoT credentials. "
                        "Account may not have IoT access enabled."
                    )
                    raise GoveeApiError("Missing IoT credentials in response")

                _LOGGER.info(
                    "Successfully authenticated with Govee (topic: %s)",
                    credentials.account_topic[:20] + "..." if credentials.account_topic else "none",
                )

                return credentials

        except aiohttp.ClientError as err:
            raise GoveeApiError(f"Connection error during login: {err}") from err


async def validate_govee_credentials(
    email: str,
    password: str,
    session: aiohttp.ClientSession | None = None,
) -> GoveeIotCredentials:
    """Validate Govee account credentials and return IoT credentials.

    Convenience function for config flow validation.

    Args:
        email: Govee account email
        password: Govee account password
        session: Optional aiohttp session

    Returns:
        GoveeIotCredentials if valid

    Raises:
        GoveeAuthError: Invalid credentials
        GoveeApiError: API communication error
    """
    async with GoveeAuthClient(session=session) as client:
        return await client.login(email, password)
