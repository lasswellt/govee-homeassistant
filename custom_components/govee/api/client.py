"""Govee REST API client with automatic retry support.

Uses aiohttp-retry for exponential backoff on transient failures.
Implements IApiClient protocol.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

import aiohttp
from aiohttp_retry import ExponentialRetry, RetryClient  # type: ignore[import-not-found]

from ..models.device import GoveeDevice
from ..models.state import GoveeDeviceState
from .exceptions import (
    GoveeApiError,
    GoveeAuthError,
    GoveeConnectionError,
    GoveeDeviceNotFoundError,
    GoveeRateLimitError,
)

if TYPE_CHECKING:
    from ..models.commands import DeviceCommand

_LOGGER = logging.getLogger(__name__)

# Govee API v2.0 endpoints
API_BASE = "https://openapi.api.govee.com/router/api/v1"
ENDPOINT_DEVICES = f"{API_BASE}/user/devices"
ENDPOINT_STATE = f"{API_BASE}/device/state"
ENDPOINT_CONTROL = f"{API_BASE}/device/control"
ENDPOINT_SCENES = f"{API_BASE}/device/scenes"

# Retry configuration
RETRY_ATTEMPTS = 3
RETRY_START_TIMEOUT = 1.0  # Initial retry delay in seconds
RETRY_MAX_TIMEOUT = 30.0  # Maximum retry delay
RETRY_FACTOR = 2.0  # Exponential factor

# Non-retryable status codes
NO_RETRY_STATUSES = {400, 401, 403, 404}


class GoveeApiClient:
    """Async HTTP client for Govee Cloud API v2.0.

    Features:
    - Automatic retry with exponential backoff (via aiohttp-retry)
    - Rate limit tracking from response headers
    - Proper exception mapping
    - Device and state parsing

    Usage:
        async with GoveeApiClient(api_key) as client:
            devices = await client.get_devices()
    """

    def __init__(
        self,
        api_key: str,
        session: aiohttp.ClientSession | None = None,
    ) -> None:
        """Initialize the API client.

        Args:
            api_key: Govee API key from developer portal.
            session: Optional shared aiohttp session.
        """
        self._api_key = api_key
        self._session = session
        self._owns_session = session is None
        self._retry_client: RetryClient | None = None

        # Rate limit tracking (updated from response headers)
        self.rate_limit_remaining: int = 100
        self.rate_limit_total: int = 100
        self.rate_limit_reset: int = 0

    async def __aenter__(self) -> GoveeApiClient:
        """Async context manager entry."""
        await self._ensure_client()
        return self

    async def __aexit__(self, *args: Any) -> None:
        """Async context manager exit."""
        await self.close()

    async def _ensure_client(self) -> RetryClient:
        """Ensure retry client is initialized."""
        if self._retry_client is None:
            if self._session is None:
                self._session = aiohttp.ClientSession()
                self._owns_session = True

            retry_options = ExponentialRetry(
                attempts=RETRY_ATTEMPTS,
                start_timeout=RETRY_START_TIMEOUT,
                max_timeout=RETRY_MAX_TIMEOUT,
                factor=RETRY_FACTOR,
                statuses=set(),  # Retry all except NO_RETRY_STATUSES
                exceptions={aiohttp.ClientError, TimeoutError},
            )

            self._retry_client = RetryClient(
                client_session=self._session,
                retry_options=retry_options,
            )

        return self._retry_client

    async def close(self) -> None:
        """Close the client and release resources."""
        if self._retry_client is not None:
            await self._retry_client.close()
            self._retry_client = None

        if self._owns_session and self._session is not None:
            await self._session.close()
            self._session = None

    def _get_headers(self) -> dict[str, str]:
        """Get request headers with API key."""
        return {
            "Govee-API-Key": self._api_key,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    def _update_rate_limits(self, headers: Any) -> None:
        """Update rate limit tracking from response headers."""
        if "X-RateLimit-Remaining" in headers:
            try:
                self.rate_limit_remaining = int(headers["X-RateLimit-Remaining"])
            except (ValueError, TypeError):
                pass

        if "X-RateLimit-Limit" in headers:
            try:
                self.rate_limit_total = int(headers["X-RateLimit-Limit"])
            except (ValueError, TypeError):
                pass

        if "X-RateLimit-Reset" in headers:
            try:
                self.rate_limit_reset = int(headers["X-RateLimit-Reset"])
            except (ValueError, TypeError):
                pass

    async def _handle_response(
        self,
        response: aiohttp.ClientResponse,
    ) -> dict[str, Any]:
        """Handle API response and raise appropriate exceptions.

        Args:
            response: aiohttp response object.

        Returns:
            Parsed JSON response data.

        Raises:
            GoveeAuthError: 401 Unauthorized.
            GoveeRateLimitError: 429 Too Many Requests.
            GoveeDeviceNotFoundError: 400 for missing device.
            GoveeApiError: Other API errors.
        """
        self._update_rate_limits(response.headers)

        try:
            data: dict[str, Any] = await response.json()
        except aiohttp.ContentTypeError:
            text = await response.text()
            raise GoveeApiError(f"Invalid JSON response: {text[:200]}")

        # Check HTTP status
        if response.status == 401:
            raise GoveeAuthError("Invalid API key")

        if response.status == 429:
            retry_after = response.headers.get("Retry-After")
            raise GoveeRateLimitError(
                "Rate limit exceeded",
                retry_after=float(retry_after) if retry_after else None,
            )

        if response.status == 400:
            message = data.get("message", "Bad request")
            # Check for "devices not exist" error (expected for groups)
            if "not exist" in message.lower():
                raise GoveeDeviceNotFoundError(message)
            raise GoveeApiError(message, code=400)

        if response.status >= 400:
            message = data.get("message", f"HTTP {response.status}")
            raise GoveeApiError(message, code=response.status)

        # Check response code within JSON
        code = data.get("code")
        if code is not None and code != 200:
            message = data.get("message", f"API error code {code}")
            if code == 401:
                raise GoveeAuthError(message)
            raise GoveeApiError(message, code=code)

        return data

    async def get_devices(self) -> list[GoveeDevice]:
        """Fetch all devices from Govee API.

        Returns:
            List of GoveeDevice instances with capabilities.

        Raises:
            GoveeAuthError: Invalid API key.
            GoveeConnectionError: Network error.
            GoveeApiError: Other API errors.
        """
        client = await self._ensure_client()

        try:
            async with client.get(
                ENDPOINT_DEVICES,
                headers=self._get_headers(),
            ) as response:
                data = await self._handle_response(response)

                devices = []
                for device_data in data.get("data", []):
                    try:
                        device = GoveeDevice.from_api_response(device_data)
                        devices.append(device)
                    except Exception as err:
                        _LOGGER.warning(
                            "Failed to parse device %s: %s",
                            device_data.get("device", "unknown"),
                            err,
                        )

                _LOGGER.debug("Fetched %d devices from Govee API", len(devices))
                return devices

        except aiohttp.ClientError as err:
            raise GoveeConnectionError(f"Connection error: {err}") from err

    async def get_device_state(
        self,
        device_id: str,
        sku: str,
    ) -> GoveeDeviceState:
        """Fetch current state for a device.

        Args:
            device_id: Device identifier (MAC address format).
            sku: Device SKU/model number.

        Returns:
            GoveeDeviceState with current values.

        Raises:
            GoveeDeviceNotFoundError: Device not found (expected for groups).
            GoveeApiError: Other API errors.
        """
        client = await self._ensure_client()

        payload = {
            "requestId": "uuid",
            "payload": {
                "sku": sku,
                "device": device_id,
            },
        }

        try:
            async with client.post(
                ENDPOINT_STATE,
                headers=self._get_headers(),
                json=payload,
            ) as response:
                data = await self._handle_response(response)

                state = GoveeDeviceState.create_empty(device_id)
                payload_data = data.get("payload", {})
                state.update_from_api(payload_data)

                return state

        except aiohttp.ClientError as err:
            raise GoveeConnectionError(f"Connection error: {err}") from err

    async def control_device(
        self,
        device_id: str,
        sku: str,
        command: DeviceCommand,
    ) -> bool:
        """Send control command to device.

        Args:
            device_id: Device identifier.
            sku: Device SKU.
            command: Command to execute.

        Returns:
            True if command was accepted by API.

        Raises:
            GoveeApiError: If command fails.
        """
        client = await self._ensure_client()

        # Build request payload
        cmd_payload = command.to_api_payload()
        payload = {
            "requestId": "uuid",
            "payload": {
                "sku": sku,
                "device": device_id,
                "capability": cmd_payload,
            },
        }

        try:
            async with client.post(
                ENDPOINT_CONTROL,
                headers=self._get_headers(),
                json=payload,
            ) as response:
                await self._handle_response(response)
                return True

        except aiohttp.ClientError as err:
            raise GoveeConnectionError(f"Connection error: {err}") from err

    async def get_dynamic_scenes(
        self,
        device_id: str,
        sku: str,
    ) -> list[dict[str, Any]]:
        """Fetch available scenes for a device.

        Args:
            device_id: Device identifier.
            sku: Device SKU.

        Returns:
            List of scene definitions with id, name, etc.
        """
        client = await self._ensure_client()

        payload = {
            "requestId": "uuid",
            "payload": {
                "sku": sku,
                "device": device_id,
            },
        }

        try:
            async with client.post(
                ENDPOINT_SCENES,
                headers=self._get_headers(),
                json=payload,
            ) as response:
                data = await self._handle_response(response)

                scenes = []
                capabilities = data.get("payload", {}).get("capabilities", [])
                for cap in capabilities:
                    if cap.get("type") == "devices.capabilities.dynamic_scene":
                        params = cap.get("parameters", {})
                        options = params.get("options", [])
                        scenes.extend(options)

                _LOGGER.debug(
                    "Fetched %d scenes for device %s",
                    len(scenes),
                    device_id,
                )
                return scenes

        except GoveeDeviceNotFoundError:
            _LOGGER.debug("No scenes available for device %s", device_id)
            return []
        except aiohttp.ClientError as err:
            raise GoveeConnectionError(f"Connection error: {err}") from err


async def validate_api_key(api_key: str) -> bool:
    """Validate a Govee API key by making a test request.

    Args:
        api_key: API key to validate.

    Returns:
        True if valid.

    Raises:
        GoveeAuthError: Invalid API key.
        GoveeApiError: Other errors.
    """
    async with GoveeApiClient(api_key) as client:
        # get_devices will raise GoveeAuthError if key is invalid
        await client.get_devices()
        return True
