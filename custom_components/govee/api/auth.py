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
GOVEE_CLIENT_TYPE = "1"  # Android client type


def _extract_p12_credentials(p12_base64: str) -> tuple[str, str]:
    """Extract certificate and private key from P12/PFX container.

    Govee API returns AWS IoT credentials as a PKCS#12 (P12/PFX) container
    in base64 encoding. This function extracts the certificate and private
    key and converts them to PEM format for use with SSL/TLS.

    Args:
        p12_base64: Base64-encoded P12/PFX container from Govee API

    Returns:
        Tuple of (certificate_pem, private_key_pem)

    Raises:
        GoveeApiError: If P12 extraction fails
    """
    if not p12_base64:
        raise GoveeApiError("Empty P12 data received from Govee API")

    try:
        # Fix base64 padding if needed (Govee may omit trailing = characters)
        # Base64 strings must have length divisible by 4
        padded = p12_base64
        padding_needed = len(padded) % 4
        if padding_needed:
            padded += "=" * (4 - padding_needed)

        # Decode base64 to get raw P12 bytes
        p12_data = base64.b64decode(padded)

        # Parse PKCS#12 container (Govee uses no password)
        private_key, certificate, _ = pkcs12.load_key_and_certificates(p12_data, None)

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

    except Exception as err:
        _LOGGER.error("Failed to extract P12 credentials: %s", err)
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

                # Extract AWS IoT credentials from P12/PFX container
                # Govee API returns a PKCS#12 container in field "A" containing
                # both the certificate and private key bundled together
                raw_p12 = client_data.get("A", "")
                cert_pem, key_pem = _extract_p12_credentials(raw_p12)

                credentials = GoveeIotCredentials(
                    token=client_data.get("token", ""),
                    refresh_token=client_data.get("refreshToken", ""),
                    account_topic=client_data.get("topic", ""),
                    iot_cert=cert_pem,
                    iot_key=key_pem,
                    iot_ca=client_data.get("caCertificate"),
                    client_id=client_id,
                    endpoint=client_data.get("endpoint", "aqm3wd1qlc3dy-ats.iot.us-east-1.amazonaws.com"),
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
