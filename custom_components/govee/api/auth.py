"""Govee authentication API for AWS IoT MQTT credentials.

Authenticates with Govee's account API to obtain certificates for AWS IoT MQTT
which provides real-time device state updates.

Reference: homebridge-govee, govee2mqtt implementations
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from typing import Any

import aiohttp

from .exceptions import GoveeApiError, GoveeAuthError

_LOGGER = logging.getLogger(__name__)

# Govee Account API endpoints
GOVEE_LOGIN_URL = "https://app2.govee.com/account/rest/account/v1/login"
GOVEE_CLIENT_TYPE = "1"  # Android client type


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

                # Extract AWS IoT credentials
                # Note: Govee uses "A" for certificate and "B" for private key
                credentials = GoveeIotCredentials(
                    token=client_data.get("token", ""),
                    refresh_token=client_data.get("refreshToken", ""),
                    account_topic=client_data.get("topic", ""),
                    iot_cert=client_data.get("A", ""),  # Certificate is in field "A"
                    iot_key=client_data.get("B", ""),   # Private key is in field "B"
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
