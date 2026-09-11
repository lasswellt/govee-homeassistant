"""Govee REST API client with automatic retry support.

Uses aiohttp-retry for exponential backoff on transient failures.
Implements IApiClient protocol.
"""

from __future__ import annotations

import logging
import time
import uuid
from collections import deque
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

import aiohttp
from aiohttp_retry import ExponentialRetry, RetryClient

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
    from homeassistant.core import HomeAssistant

    from ..models.commands import DeviceCommand

_LOGGER = logging.getLogger(__name__)

# How many hourly buckets of request history to keep. Govee documents a
# 10,000/day cap but returns no daily counter of its own, so the only way
# to know what an install actually spends is to count locally.
REQUEST_HISTORY_HOURS = 24

# Govee API v2.0 endpoints
API_BASE = "https://openapi.api.govee.com/router/api/v1"
ENDPOINT_DEVICES = f"{API_BASE}/user/devices"
ENDPOINT_STATE = f"{API_BASE}/device/state"
ENDPOINT_CONTROL = f"{API_BASE}/device/control"
ENDPOINT_SCENES = f"{API_BASE}/device/scenes"
ENDPOINT_DIY_SCENES = f"{API_BASE}/device/diy-scenes"

# Retry configuration
RETRY_ATTEMPTS = 3
RETRY_START_TIMEOUT = 1.0  # Initial retry delay in seconds
RETRY_MAX_TIMEOUT = 30.0  # Maximum retry delay
RETRY_FACTOR = 2.0  # Exponential factor

# Retryable server error status codes
RETRY_STATUSES = {500, 502, 503, 504}

# Ring-buffer size for the control-command history kept for diagnostics.
COMMAND_BUFFER_SIZE = 30


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
        hass: HomeAssistant | None = None,
    ) -> None:
        """Initialize the API client.

        Args:
            api_key: Govee API key from developer portal.
            session: Optional shared aiohttp session. Takes precedence over hass.
            hass: Home Assistant instance — when provided (and no `session`),
                the HA-managed clientsession is used so the client participates
                in HA shutdown/DNS lifecycle (Platinum rule `inject-websession`).
        """
        self._api_key = api_key
        self._session = session
        self._owns_session = session is None
        self._retry_client: RetryClient | None = None

        if session is None and hass is not None:
            from homeassistant.helpers.aiohttp_client import async_get_clientsession

            self._session = async_get_clientsession(hass)
            self._owns_session = False

        # Rate limit tracking (updated from response headers)
        self.rate_limit_remaining: int = 100
        self.rate_limit_total: int = 100
        self.rate_limit_reset: int = 0

        # Local request accounting. The per-minute allowance comes back in
        # response headers, but the daily one does not, so it is counted here.
        # Hourly buckets rather than per-request timestamps: 24 small entries
        # instead of tens of thousands, which is ample to answer "are we over
        # the daily cap, and by how much".
        self._request_buckets: deque[list[int]] = deque()
        self._request_day: int = 0
        self._requests_today: int = 0

        # Last raw API responses, retained for diagnostics (redacted at dump
        # time). Lets a diagnostics download include exactly what the device
        # list and /device/state endpoints returned — essential for debugging
        # state-shape issues the parsed model hides (e.g. thermometers, #83).
        self._last_raw_devices: list[dict[str, Any]] | None = None
        self._last_raw_state: dict[str, dict[str, Any]] = {}

        # Recent /device/control sends, retained for diagnostics (redacted at
        # dump time). Each record carries the exact capability payload sent
        # plus Govee's HTTP status and response body — the data debug logging
        # would show, available from a diagnostics download alone (#127).
        self._recent_commands: deque[dict[str, Any]] = deque(maxlen=COMMAND_BUFFER_SIZE)

    @property
    def api_key(self) -> str:
        """The Developer API key (also used by the OpenAPI event channel)."""
        return self._api_key

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
                raise RuntimeError(
                    "GoveeApiClient requires either a `session` or `hass` parameter "
                    "at construction. Pass `hass=hass` so the HA-managed "
                    "clientsession is used (Platinum rule `inject-websession`)."
                )

            retry_options = ExponentialRetry(
                attempts=RETRY_ATTEMPTS,
                start_timeout=RETRY_START_TIMEOUT,
                max_timeout=RETRY_MAX_TIMEOUT,
                factor=RETRY_FACTOR,
                statuses=RETRY_STATUSES,
                exceptions={aiohttp.ClientError, TimeoutError},
            )

            self._retry_client = RetryClient(
                client_session=self._session,
                retry_options=retry_options,
            )

        return self._retry_client

    async def close(self) -> None:
        """Close the client and release resources.

        Only closes the underlying aiohttp session when this client owns it.
        When `hass` was passed at construction the session belongs to Home
        Assistant — closing it would tear down the shared client session
        and trigger HA's frame-helper warning. RetryClient.close() forwards
        unconditionally to the wrapped session, so we drop the reference
        instead of calling its close() in that case.
        """
        if self._retry_client is not None:
            if self._owns_session:
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

    def _note_request(self) -> None:
        """Record that one request was spent against the Govee quota.

        Counted for every response, including 429s and errors: a rejected
        request still consumed the allowance.
        """
        now = time.time()
        hour = int(now // 3600)
        day = int(now // 86400)

        if day != self._request_day:
            self._request_day = day
            self._requests_today = 0
        self._requests_today += 1

        if self._request_buckets and self._request_buckets[-1][0] == hour:
            self._request_buckets[-1][1] += 1
        else:
            self._request_buckets.append([hour, 1])

        cutoff = hour - (REQUEST_HISTORY_HOURS - 1)
        while self._request_buckets and self._request_buckets[0][0] < cutoff:
            self._request_buckets.popleft()

    @property
    def requests_last_24h(self) -> int:
        """Requests spent in the trailing 24 hours, to hourly resolution."""
        cutoff = int(time.time() // 3600) - (REQUEST_HISTORY_HOURS - 1)
        return sum(count for hour, count in self._request_buckets if hour >= cutoff)

    @property
    def requests_today(self) -> int:
        """Requests spent since UTC midnight.

        Tracked alongside the rolling figure because a daily cap resets on a
        clock boundary, and the two answer different questions: this one says
        how much of today's allowance is gone, the rolling one says what a
        steady state actually costs.
        """
        if int(time.time() // 86400) != self._request_day:
            return 0
        return self._requests_today

    @property
    def requests_per_hour(self) -> float:
        """Mean requests/hour over the buckets held, 0.0 before the first hour.

        Only complete history is averaged, so this is a description of what has
        happened, not a projection.
        """
        cutoff = int(time.time() // 3600) - (REQUEST_HISTORY_HOURS - 1)
        buckets = [b for b in self._request_buckets if b[0] >= cutoff]
        if not buckets:
            return 0.0
        return round(sum(count for _, count in buckets) / len(buckets), 1)

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
        self._note_request()
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
            # Govee API uses both "message" and "msg" for errors
            message = data.get("message") or data.get("msg", "Bad request")
            _LOGGER.debug("API 400 error response: %s", data)
            # Check for "devices not exist" error (expected for groups)
            if "not exist" in message.lower():
                raise GoveeDeviceNotFoundError(message)
            raise GoveeApiError(message, code=400)

        if response.status >= 400:
            message = data.get("message") or data.get("msg", f"HTTP {response.status}")
            raise GoveeApiError(message, code=response.status)

        # Check response code within JSON
        code = data.get("code")
        if code is not None and code != 200:
            message = data.get("message") or data.get("msg", f"API error code {code}")
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

                self._last_raw_devices = data.get("data", [])
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
            "requestId": str(uuid.uuid4()),
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

                # Diagnostic (issue #62): log the raw poll response for standalone
                # event sensors (e.g. H5054 water detectors) so a debug capture
                # shows whether the developer device-state endpoint ever returns
                # the bodyAppearedEvent trip — earlier dumps showed only `online`.
                if any(
                    cap.get("type") == "devices.capabilities.event"
                    for cap in payload_data.get("capabilities", [])
                ):
                    _LOGGER.debug(
                        "Event-sensor poll for %s (%s) raw=%s",
                        device_id,
                        sku,
                        payload_data,
                    )

                self._last_raw_state[device_id] = payload_data
                return state

        except aiohttp.ClientError as err:
            raise GoveeConnectionError(f"Connection error: {err}") from err

    @property
    def last_raw_devices(self) -> list[dict[str, Any]] | None:
        """Raw device-list payload from the most recent get_devices() call."""
        return self._last_raw_devices

    @property
    def recent_commands(self) -> list[dict[str, Any]]:
        """Recent control-command sends for the diagnostics download (oldest first)."""
        return list(self._recent_commands)

    def record_local_command(
        self,
        device_id: str,
        sku: str,
        transport: str,
        capability: dict[str, Any],
        *,
        delivered: bool,
        detail: str | None = None,
    ) -> None:
        """Add a non-REST send to the same command history diagnostics reads.

        Only cloud commands used to be recorded, so a diagnostics download from
        someone whose light ignores a colour change showed nothing at all when
        that write had gone out over LAN, MQTT or BLE. Since those transports
        are exactly where "accepted but nothing happened" reports come from
        (issues #127, #143, #149, #158), they belong in the same buffer.

        Args:
            device_id: The device the command was addressed to.
            sku: The device model.
            transport: ``"lan"`` / ``"mqtt"`` / ``"ble"``.
            capability: The command payload, in the REST record's shape.
            delivered: Whether the transport reported the write as delivered —
                for LAN that means confirmed by readback.
            detail: Optional note, e.g. why the write wasn't confirmed.
        """
        self._recent_commands.append(
            {
                "sent_at": datetime.now(timezone.utc).isoformat(),
                "device": device_id,
                "sku": sku,
                "transport": transport,
                "capability": capability,
                "http_status": None,
                "response": {"delivered": delivered, "detail": detail},
                "error": None if delivered else (detail or "not confirmed"),
            }
        )

    def peek_last_command_record(self) -> dict[str, Any] | None:
        """Return the most recently appended command record (same object, not a copy).

        Lets a caller attach follow-up data (e.g. a verification poll result)
        to the record that a diagnostics download will surface, without
        re-plumbing control_device's return value.
        """
        return self._recent_commands[-1] if self._recent_commands else None

    @property
    def last_raw_state(self) -> dict[str, dict[str, Any]]:
        """Raw /device/state payloads keyed by device_id (latest per device)."""
        return self._last_raw_state

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
            "requestId": str(uuid.uuid4()),
            "payload": {
                "sku": sku,
                "device": device_id,
                "capability": cmd_payload,
            },
        }

        _LOGGER.debug(
            "Control send: device=%s sku=%s type=%s instance=%s value=%s",
            device_id,
            sku,
            cmd_payload["type"],
            cmd_payload["instance"],
            cmd_payload["value"],
        )

        # Command-history record for diagnostics: what was sent and exactly how
        # Govee answered (aiohttp caches the body, so reading it here does not
        # consume it from _handle_response).
        record: dict[str, Any] = {
            "sent_at": datetime.now(timezone.utc).isoformat(),
            "device": device_id,
            "sku": sku,
            "capability": cmd_payload,
            "http_status": None,
            "response": None,
            "error": None,
        }

        try:
            async with client.post(
                ENDPOINT_CONTROL,
                headers=self._get_headers(),
                json=payload,
            ) as response:
                record["http_status"] = response.status
                try:
                    record["response"] = await response.json(content_type=None)
                except Exception:
                    record["response"] = (await response.text())[:500]
                _LOGGER.debug(
                    "Control response: device=%s instance=%s HTTP %s body=%s",
                    device_id,
                    cmd_payload["instance"],
                    response.status,
                    str(record["response"])[:300],
                )
                await self._handle_response(response)
                return True

        except aiohttp.ClientError as err:
            record["error"] = f"connection: {err}"
            raise GoveeConnectionError(f"Connection error: {err}") from err
        except GoveeApiError as err:
            record["error"] = str(err)
            _LOGGER.warning(
                "Control rejected by Govee: device=%s sku=%s instance=%s value=%s HTTP %s body=%s (%s)",
                device_id,
                sku,
                cmd_payload["instance"],
                cmd_payload["value"],
                record["http_status"],
                str(record["response"])[:300],
                err,
            )
            raise
        finally:
            self._recent_commands.append(record)

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
            "requestId": str(uuid.uuid4()),
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

    async def get_diy_scenes(
        self,
        device_id: str,
        sku: str,
    ) -> list[dict[str, Any]]:
        """Fetch available DIY scenes for a device.

        Args:
            device_id: Device identifier.
            sku: Device SKU.

        Returns:
            List of DIY scene definitions with id, name, etc.
        """
        client = await self._ensure_client()

        payload = {
            "requestId": str(uuid.uuid4()),
            "payload": {
                "sku": sku,
                "device": device_id,
            },
        }

        try:
            async with client.post(
                ENDPOINT_DIY_SCENES,
                headers=self._get_headers(),
                json=payload,
            ) as response:
                data = await self._handle_response(response)

                scenes = []
                capabilities = data.get("payload", {}).get("capabilities", [])
                for cap in capabilities:
                    # DIY scenes endpoint returns dynamic_scene type with diyScene instance
                    if cap.get("type") == "devices.capabilities.dynamic_scene":
                        params = cap.get("parameters", {})
                        options = params.get("options", [])
                        scenes.extend(options)

                _LOGGER.debug(
                    "Fetched %d DIY scenes for device %s",
                    len(scenes),
                    device_id,
                )
                return scenes

        except GoveeDeviceNotFoundError:
            _LOGGER.debug("No DIY scenes available for device %s", device_id)
            return []
        except aiohttp.ClientError as err:
            raise GoveeConnectionError(f"Connection error: {err}") from err


async def validate_api_key(api_key: str, hass: HomeAssistant | None = None) -> bool:
    """Validate a Govee API key by making a test request.

    Args:
        api_key: API key to validate.
        hass: Home Assistant instance for shared aiohttp session (required since v2026.5.4).

    Returns:
        True if valid.

    Raises:
        GoveeAuthError: Invalid API key.
        GoveeApiError: Other errors.
    """
    async with GoveeApiClient(api_key, hass=hass) as client:
        # get_devices will raise GoveeAuthError if key is invalid
        await client.get_devices()
        return True
