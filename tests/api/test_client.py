"""Test Govee API client."""
from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch
import pytest
import aiohttp
from aiohttp import ContentTypeError

from custom_components.govee.api.client import GoveeApiClient, RateLimiter
from custom_components.govee.api.const import (
    BASE_URL,
    CAPABILITY_COLOR_SETTING,
    CAPABILITY_DYNAMIC_SCENE,
    CAPABILITY_MUSIC_SETTING,
    CAPABILITY_ON_OFF,
    CAPABILITY_RANGE,
    CAPABILITY_SEGMENT_COLOR,
    CAPABILITY_TOGGLE,
    COLOR_TEMP_MAX,
    COLOR_TEMP_MIN,
    ENDPOINT_DEVICES,
    ENDPOINT_DEVICE_CONTROL,
    ENDPOINT_DEVICE_STATE,
    ENDPOINT_DIY_SCENES,
    ENDPOINT_DYNAMIC_SCENES,
    HEADER_API_RATE_LIMIT_REMAINING,
    HEADER_API_RATE_LIMIT_RESET,
    HEADER_RATE_LIMIT_REMAINING,
    HEADER_RATE_LIMIT_RESET,
    INSTANCE_BRIGHTNESS,
    INSTANCE_COLOR_RGB,
    INSTANCE_COLOR_TEMP,
    INSTANCE_DIY_SCENE,
    INSTANCE_LIGHT_SCENE,
    INSTANCE_MUSIC_MODE,
    INSTANCE_NIGHTLIGHT_TOGGLE,
    INSTANCE_POWER_SWITCH,
    INSTANCE_SEGMENTED_BRIGHTNESS,
    INSTANCE_SEGMENTED_COLOR,
    RATE_LIMIT_PER_DAY,
    RATE_LIMIT_PER_MINUTE,
)
from custom_components.govee.api.exceptions import (
    GoveeApiError,
    GoveeAuthError,
    GoveeConnectionError,
    GoveeRateLimitError,
)


# ==============================================================================
# RateLimiter Tests
# ==============================================================================


class TestRateLimiter:
    """Test RateLimiter class."""

    def test_rate_limiter_initialization(self):
        """Test rate limiter initializes with correct defaults."""
        limiter = RateLimiter()

        assert limiter._per_minute == RATE_LIMIT_PER_MINUTE
        assert limiter._per_day == RATE_LIMIT_PER_DAY
        assert limiter._minute_timestamps == []
        assert limiter._day_timestamps == []
        assert limiter._api_remaining_minute is None
        assert limiter._api_remaining_day is None

    def test_rate_limiter_custom_limits(self):
        """Test rate limiter with custom limits."""
        limiter = RateLimiter(requests_per_minute=50, requests_per_day=5000)

        assert limiter._per_minute == 50
        assert limiter._per_day == 5000

    @pytest.mark.asyncio
    async def test_acquire_allows_request_under_limit(self):
        """Test acquire() allows request when under rate limit."""
        limiter = RateLimiter(requests_per_minute=2, requests_per_day=10)

        # First request should be instant
        start = time.time()
        await limiter.acquire()
        elapsed = time.time() - start

        assert elapsed < 0.1  # Should not wait
        assert len(limiter._minute_timestamps) == 1
        assert len(limiter._day_timestamps) == 1

    @pytest.mark.asyncio
    async def test_acquire_waits_at_minute_limit(self):
        """Test acquire() waits when minute limit reached."""
        limiter = RateLimiter(requests_per_minute=2, requests_per_day=10)

        # Fill up minute limit
        await limiter.acquire()
        await limiter.acquire()

        # Third request should wait
        start = time.time()

        # Mock sleep to avoid actual waiting
        with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            await limiter.acquire()

            # Should have called sleep with some positive wait time
            mock_sleep.assert_called_once()
            wait_time = mock_sleep.call_args[0][0]
            assert wait_time > 0
            assert wait_time <= 60

    @pytest.mark.asyncio
    async def test_acquire_waits_at_day_limit(self):
        """Test acquire() waits when daily limit reached."""
        limiter = RateLimiter(requests_per_minute=100, requests_per_day=2)

        # Fill up daily limit
        await limiter.acquire()
        await limiter.acquire()

        # Third request should wait (capped at 1 hour)
        with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            await limiter.acquire()

            # Should have called sleep with capped wait time (max 1 hour)
            mock_sleep.assert_called_once()
            wait_time = mock_sleep.call_args[0][0]
            assert wait_time > 0
            assert wait_time <= 3600  # Capped at 1 hour

    @pytest.mark.asyncio
    async def test_acquire_cleans_old_timestamps(self):
        """Test acquire() removes old timestamps."""
        limiter = RateLimiter()

        # Add old timestamps (beyond 60s for minute, 24h for day)
        now = time.time()
        limiter._minute_timestamps = [now - 70, now - 65]  # Both > 60s old
        limiter._day_timestamps = [now - 90000]  # > 24h old

        await limiter.acquire()

        # Old timestamps should be cleaned
        assert len(limiter._minute_timestamps) == 1  # Only new one
        assert len(limiter._day_timestamps) == 1  # Only new one

    def test_update_from_headers_all_headers(self):
        """Test update_from_headers() with all rate limit headers."""
        limiter = RateLimiter()

        headers = {
            HEADER_RATE_LIMIT_REMAINING: "95",
            HEADER_RATE_LIMIT_RESET: "1234567890.5",
            HEADER_API_RATE_LIMIT_REMAINING: "9500",
            HEADER_API_RATE_LIMIT_RESET: "1234567900.0",
        }

        limiter.update_from_headers(headers)

        assert limiter._api_remaining_minute == 95
        assert limiter._api_reset_minute == 1234567890.5
        assert limiter._api_remaining_day == 9500
        assert limiter._api_reset_day == 1234567900.0

    def test_update_from_headers_partial(self):
        """Test update_from_headers() with partial headers."""
        limiter = RateLimiter()

        headers = {
            HEADER_RATE_LIMIT_REMAINING: "80",
        }

        limiter.update_from_headers(headers)

        assert limiter._api_remaining_minute == 80
        assert limiter._api_reset_minute is None
        assert limiter._api_remaining_day is None
        assert limiter._api_reset_day is None

    def test_remaining_minute_with_api_value(self):
        """Test remaining_minute property uses API value when available."""
        limiter = RateLimiter()
        limiter._api_remaining_minute = 42

        assert limiter.remaining_minute == 42

    def test_remaining_minute_calculated(self):
        """Test remaining_minute property calculates from timestamps."""
        limiter = RateLimiter(requests_per_minute=100)
        limiter._minute_timestamps = [time.time()] * 30  # 30 requests

        assert limiter.remaining_minute == 70  # 100 - 30

    def test_remaining_day_with_api_value(self):
        """Test remaining_day property uses API value when available."""
        limiter = RateLimiter()
        limiter._api_remaining_day = 8500

        assert limiter.remaining_day == 8500

    def test_remaining_day_calculated(self):
        """Test remaining_day property calculates from timestamps."""
        limiter = RateLimiter(requests_per_day=10000)
        limiter._day_timestamps = [time.time()] * 500  # 500 requests

        assert limiter.remaining_day == 9500  # 10000 - 500

    def test_reset_properties(self):
        """Test reset_minute and reset_day properties."""
        limiter = RateLimiter()
        limiter._api_reset_minute = 1234567890.0
        limiter._api_reset_day = 1234567900.0

        assert limiter.reset_minute == 1234567890.0
        assert limiter.reset_day == 1234567900.0


# ==============================================================================
# GoveeApiClient Initialization Tests
# ==============================================================================


class TestGoveeApiClientInit:
    """Test GoveeApiClient initialization."""

    def test_client_initialization_api_key_only(self):
        """Test client initializes with API key only."""
        client = GoveeApiClient("test_api_key")

        assert client._api_key == "test_api_key"
        assert client._session is None
        assert client._owns_session is True
        assert isinstance(client._rate_limiter, RateLimiter)

    def test_client_initialization_with_session(self):
        """Test client initializes with custom session."""
        mock_session = MagicMock(spec=aiohttp.ClientSession)
        client = GoveeApiClient("test_key", session=mock_session)

        assert client._api_key == "test_key"
        assert client._session is mock_session
        assert client._owns_session is False

    @pytest.mark.asyncio
    async def test_context_manager_enter(self):
        """Test async context manager creates session."""
        client = GoveeApiClient("test_key")

        assert client._session is None

        async with client as entered_client:
            assert entered_client is client
            assert client._session is not None
            assert isinstance(client._session, aiohttp.ClientSession)

    @pytest.mark.asyncio
    async def test_context_manager_exit_closes_owned_session(self):
        """Test context manager exit closes owned session."""
        client = GoveeApiClient("test_key")

        async with client:
            session = client._session
            assert session is not None

        # Session should be closed after exit
        assert client._session is None

    @pytest.mark.asyncio
    async def test_close_method_closes_owned_session(self):
        """Test close() method closes owned session."""
        client = GoveeApiClient("test_key")
        client._session = MagicMock(spec=aiohttp.ClientSession)
        client._session.close = AsyncMock()
        client._owns_session = True

        await client.close()

        client._session.close.assert_called_once()
        assert client._session is None

    @pytest.mark.asyncio
    async def test_close_method_preserves_unowned_session(self):
        """Test close() does not close session it doesn't own."""
        mock_session = MagicMock(spec=aiohttp.ClientSession)
        mock_session.close = AsyncMock()
        client = GoveeApiClient("test_key", session=mock_session)

        await client.close()

        # Should NOT close unowned session
        mock_session.close.assert_not_called()
        assert client._session is mock_session

    def test_rate_limiter_property(self):
        """Test rate_limiter property accessor."""
        client = GoveeApiClient("test_key")

        limiter = client.rate_limiter
        assert isinstance(limiter, RateLimiter)
        assert limiter is client._rate_limiter

    def test_headers_method(self):
        """Test _headers() returns correct headers."""
        client = GoveeApiClient("my_api_key_123")

        headers = client._headers()

        assert headers["Govee-API-Key"] == "my_api_key_123"
        assert headers["Content-Type"] == "application/json"


# ==============================================================================
# Request Method Tests
# ==============================================================================


class TestGoveeApiClientRequest:
    """Test GoveeApiClient._request() method."""

    @pytest.mark.asyncio
    async def test_request_successful_get(self):
        """Test _request() successful GET request."""
        client = GoveeApiClient("test_key")

        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.headers = {}
        mock_response.json = AsyncMock(return_value={"code": 200, "data": "success"})

        mock_session = MagicMock(spec=aiohttp.ClientSession)
        mock_session.request = MagicMock(
            return_value=MagicMock(__aenter__=AsyncMock(return_value=mock_response))
        )
        client._session = mock_session

        with patch.object(client._rate_limiter, "acquire", new_callable=AsyncMock):
            result = await client._request("GET", "test/endpoint")

        assert result == {"code": 200, "data": "success"}

    @pytest.mark.asyncio
    async def test_request_successful_post_with_json(self):
        """Test _request() successful POST with JSON data."""
        client = GoveeApiClient("test_key")

        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.headers = {}
        mock_response.json = AsyncMock(return_value={"code": 200, "message": "OK"})

        mock_session = MagicMock(spec=aiohttp.ClientSession)
        mock_session.request = MagicMock(
            return_value=MagicMock(__aenter__=AsyncMock(return_value=mock_response))
        )
        client._session = mock_session

        payload = {"key": "value"}

        with patch.object(client._rate_limiter, "acquire", new_callable=AsyncMock):
            result = await client._request("POST", "test/endpoint", json_data=payload)

        assert result == {"code": 200, "message": "OK"}
        # Verify JSON payload was passed
        call_kwargs = mock_session.request.call_args[1]
        assert call_kwargs["json"] == payload

    @pytest.mark.asyncio
    async def test_request_creates_session_if_none(self):
        """Test _request() creates session if none exists."""
        client = GoveeApiClient("test_key")
        assert client._session is None

        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.headers = {}
        mock_response.json = AsyncMock(return_value={"code": 200})

        with patch.object(
            aiohttp, "ClientSession", return_value=MagicMock(spec=aiohttp.ClientSession)
        ) as mock_session_class:
            mock_session = mock_session_class.return_value
            mock_session.request = MagicMock(
                return_value=MagicMock(__aenter__=AsyncMock(return_value=mock_response))
            )

            with patch.object(client._rate_limiter, "acquire", new_callable=AsyncMock):
                await client._request("GET", "test/endpoint")

            # Should create session and mark as owned
            assert client._owns_session is True
            assert client._session is mock_session

    @pytest.mark.asyncio
    async def test_request_updates_rate_limiter_from_headers(self):
        """Test _request() updates rate limiter from response headers."""
        client = GoveeApiClient("test_key")

        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.headers = {
            HEADER_RATE_LIMIT_REMAINING: "95",
            HEADER_API_RATE_LIMIT_REMAINING: "9500",
        }
        mock_response.json = AsyncMock(return_value={"code": 200})

        mock_session = MagicMock(spec=aiohttp.ClientSession)
        mock_session.request = MagicMock(
            return_value=MagicMock(__aenter__=AsyncMock(return_value=mock_response))
        )
        client._session = mock_session

        with patch.object(client._rate_limiter, "acquire", new_callable=AsyncMock):
            with patch.object(
                client._rate_limiter, "update_from_headers"
            ) as mock_update:
                await client._request("GET", "test/endpoint")

                # Should update rate limiter with headers
                mock_update.assert_called_once()

    @pytest.mark.asyncio
    async def test_request_raises_auth_error_on_401(self):
        """Test _request() raises GoveeAuthError on 401."""
        client = GoveeApiClient("invalid_key")

        mock_response = MagicMock()
        mock_response.status = 401
        mock_response.headers = {}
        mock_response.json = AsyncMock(return_value={"message": "Unauthorized"})

        mock_session = MagicMock(spec=aiohttp.ClientSession)
        mock_session.request = MagicMock(
            return_value=MagicMock(__aenter__=AsyncMock(return_value=mock_response))
        )
        client._session = mock_session

        with patch.object(client._rate_limiter, "acquire", new_callable=AsyncMock):
            with pytest.raises(GoveeAuthError):
                await client._request("GET", "test/endpoint")

    @pytest.mark.asyncio
    async def test_request_raises_rate_limit_error_on_429(self):
        """Test _request() raises GoveeRateLimitError on 429."""
        client = GoveeApiClient("test_key")

        mock_response = MagicMock()
        mock_response.status = 429
        mock_response.headers = {"Retry-After": "60"}
        mock_response.json = AsyncMock(return_value={"message": "Too many requests"})

        mock_session = MagicMock(spec=aiohttp.ClientSession)
        mock_session.request = MagicMock(
            return_value=MagicMock(__aenter__=AsyncMock(return_value=mock_response))
        )
        client._session = mock_session

        with patch.object(client._rate_limiter, "acquire", new_callable=AsyncMock):
            with pytest.raises(GoveeRateLimitError) as exc_info:
                await client._request("GET", "test/endpoint")

            # Should include retry_after from header
            assert exc_info.value.retry_after == 60.0

    @pytest.mark.asyncio
    async def test_request_raises_api_error_on_4xx_5xx(self):
        """Test _request() raises GoveeApiError on 4xx/5xx errors."""
        client = GoveeApiClient("test_key")

        mock_response = MagicMock()
        mock_response.status = 500
        mock_response.headers = {}
        mock_response.json = AsyncMock(return_value={"message": "Internal server error"})

        mock_session = MagicMock(spec=aiohttp.ClientSession)
        mock_session.request = MagicMock(
            return_value=MagicMock(__aenter__=AsyncMock(return_value=mock_response))
        )
        client._session = mock_session

        with patch.object(client._rate_limiter, "acquire", new_callable=AsyncMock):
            with pytest.raises(GoveeApiError) as exc_info:
                await client._request("GET", "test/endpoint")

            assert exc_info.value.code == 500
            assert "Internal server error" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_request_raises_api_error_on_api_level_error(self):
        """Test _request() raises GoveeApiError on API-level error in body."""
        client = GoveeApiClient("test_key")

        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.headers = {}
        mock_response.json = AsyncMock(
            return_value={"code": 400, "message": "Invalid device ID"}
        )

        mock_session = MagicMock(spec=aiohttp.ClientSession)
        mock_session.request = MagicMock(
            return_value=MagicMock(__aenter__=AsyncMock(return_value=mock_response))
        )
        client._session = mock_session

        with patch.object(client._rate_limiter, "acquire", new_callable=AsyncMock):
            with pytest.raises(GoveeApiError) as exc_info:
                await client._request("GET", "test/endpoint")

            assert exc_info.value.code == 400
            assert "Invalid device ID" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_request_raises_connection_error_on_timeout(self):
        """Test _request() raises GoveeConnectionError on timeout."""
        client = GoveeApiClient("test_key")

        mock_session = MagicMock(spec=aiohttp.ClientSession)
        mock_session.request = MagicMock(
            side_effect=asyncio.TimeoutError("Request timed out")
        )
        client._session = mock_session

        with patch.object(client._rate_limiter, "acquire", new_callable=AsyncMock):
            with pytest.raises(GoveeConnectionError) as exc_info:
                await client._request("GET", "test/endpoint")

            assert "timed out" in str(exc_info.value).lower()

    @pytest.mark.asyncio
    async def test_request_raises_connection_error_on_client_error(self):
        """Test _request() raises GoveeConnectionError on aiohttp.ClientError."""
        client = GoveeApiClient("test_key")

        mock_session = MagicMock(spec=aiohttp.ClientSession)
        mock_session.request = MagicMock(
            side_effect=aiohttp.ClientError("Network error")
        )
        client._session = mock_session

        with patch.object(client._rate_limiter, "acquire", new_callable=AsyncMock):
            with pytest.raises(GoveeConnectionError) as exc_info:
                await client._request("GET", "test/endpoint")

            assert "Network error" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_request_handles_non_json_response(self):
        """Test _request() handles non-JSON response gracefully."""
        client = GoveeApiClient("test_key")

        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.headers = {}
        mock_response.json = AsyncMock(side_effect=ContentTypeError(None, None))
        mock_response.text = AsyncMock(return_value="Plain text response")

        mock_session = MagicMock(spec=aiohttp.ClientSession)
        mock_session.request = MagicMock(
            return_value=MagicMock(__aenter__=AsyncMock(return_value=mock_response))
        )
        client._session = mock_session

        with patch.object(client._rate_limiter, "acquire", new_callable=AsyncMock):
            result = await client._request("GET", "test/endpoint")

        # Should return text in message field
        assert result == {"message": "Plain text response"}


# ==============================================================================
# Device Discovery Tests
# ==============================================================================


class TestDeviceDiscovery:
    """Test device discovery methods."""

    @pytest.mark.asyncio
    async def test_get_devices_success(self):
        """Test get_devices() returns device list."""
        client = GoveeApiClient("test_key")

        devices_data = [
            {"device": "AA:BB:CC:DD:EE:FF", "sku": "H6160"},
            {"device": "11:22:33:44:55:66", "sku": "H6159"},
        ]

        with patch.object(
            client, "_request", new_callable=AsyncMock
        ) as mock_request:
            mock_request.return_value = {"code": 200, "data": devices_data}

            devices = await client.get_devices()

            assert devices == devices_data
            mock_request.assert_called_once_with("GET", ENDPOINT_DEVICES)

    @pytest.mark.asyncio
    async def test_get_devices_with_auth_error(self):
        """Test get_devices() raises on auth error."""
        client = GoveeApiClient("invalid_key")

        with patch.object(
            client, "_request", new_callable=AsyncMock
        ) as mock_request:
            mock_request.side_effect = GoveeAuthError()

            with pytest.raises(GoveeAuthError):
                await client.get_devices()

    @pytest.mark.asyncio
    async def test_get_devices_empty_response(self):
        """Test get_devices() handles empty data gracefully."""
        client = GoveeApiClient("test_key")

        with patch.object(
            client, "_request", new_callable=AsyncMock
        ) as mock_request:
            mock_request.return_value = {"code": 200}  # No 'data' field

            devices = await client.get_devices()

            assert devices == []


# ==============================================================================
# State Query Tests
# ==============================================================================


class TestStateQueries:
    """Test device state query methods."""

    @pytest.mark.asyncio
    async def test_get_device_state_success(self):
        """Test get_device_state() returns device state."""
        client = GoveeApiClient("test_key")

        state_data = {
            "online": True,
            "powerState": 1,
            "brightness": 80,
        }

        with patch.object(
            client, "_request", new_callable=AsyncMock
        ) as mock_request:
            mock_request.return_value = {"code": 200, "payload": state_data}

            state = await client.get_device_state("AA:BB:CC:DD:EE:FF", "H6160")

            assert state == state_data
            # Verify request was made correctly
            mock_request.assert_called_once()
            call_args = mock_request.call_args
            assert call_args[0][0] == "POST"
            assert call_args[0][1] == ENDPOINT_DEVICE_STATE

    @pytest.mark.asyncio
    async def test_get_device_state_includes_uuid(self):
        """Test get_device_state() generates UUID in request."""
        client = GoveeApiClient("test_key")

        with patch.object(
            client, "_request", new_callable=AsyncMock
        ) as mock_request:
            mock_request.return_value = {"code": 200, "payload": {}}

            await client.get_device_state("device_id", "sku_model")

            # Verify payload includes requestId
            call_args = mock_request.call_args
            payload = call_args[0][2]  # json_data argument
            assert "requestId" in payload
            # Should be valid UUID format
            import uuid
            uuid.UUID(payload["requestId"])  # Raises if invalid

    @pytest.mark.asyncio
    async def test_get_device_state_with_error(self):
        """Test get_device_state() handles API errors."""
        client = GoveeApiClient("test_key")

        with patch.object(
            client, "_request", new_callable=AsyncMock
        ) as mock_request:
            mock_request.side_effect = GoveeApiError("Device not found", code=404)

            with pytest.raises(GoveeApiError) as exc_info:
                await client.get_device_state("invalid_device", "H6160")

            assert exc_info.value.code == 404


# ==============================================================================
# Control Command Tests
# ==============================================================================


class TestControlCommands:
    """Test device control command methods."""

    @pytest.mark.asyncio
    async def test_control_device_generic(self):
        """Test control_device() sends control command."""
        client = GoveeApiClient("test_key")

        with patch.object(
            client, "_request", new_callable=AsyncMock
        ) as mock_request:
            mock_request.return_value = {"code": 200, "message": "Success"}

            result = await client.control_device(
                "device_id",
                "H6160",
                CAPABILITY_ON_OFF,
                INSTANCE_POWER_SWITCH,
                1,
            )

            assert result == {"code": 200, "message": "Success"}
            # Verify request structure
            call_args = mock_request.call_args
            assert call_args[0][0] == "POST"
            assert call_args[0][1] == ENDPOINT_DEVICE_CONTROL
            payload = call_args[0][2]
            assert payload["payload"]["capability"]["type"] == CAPABILITY_ON_OFF
            assert payload["payload"]["capability"]["instance"] == INSTANCE_POWER_SWITCH
            assert payload["payload"]["capability"]["value"] == 1

    @pytest.mark.asyncio
    async def test_turn_on(self):
        """Test turn_on() sends power on command."""
        client = GoveeApiClient("test_key")

        with patch.object(
            client, "control_device", new_callable=AsyncMock
        ) as mock_control:
            mock_control.return_value = {"code": 200}

            result = await client.turn_on("device_id", "H6160")

            assert result == {"code": 200}
            mock_control.assert_called_once_with(
                "device_id", "H6160", CAPABILITY_ON_OFF, INSTANCE_POWER_SWITCH, 1
            )

    @pytest.mark.asyncio
    async def test_turn_off(self):
        """Test turn_off() sends power off command."""
        client = GoveeApiClient("test_key")

        with patch.object(
            client, "control_device", new_callable=AsyncMock
        ) as mock_control:
            mock_control.return_value = {"code": 200}

            result = await client.turn_off("device_id", "H6160")

            assert result == {"code": 200}
            mock_control.assert_called_once_with(
                "device_id", "H6160", CAPABILITY_ON_OFF, INSTANCE_POWER_SWITCH, 0
            )

    @pytest.mark.asyncio
    async def test_set_nightlight_on(self):
        """Test set_nightlight() enables nightlight."""
        client = GoveeApiClient("test_key")

        with patch.object(
            client, "control_device", new_callable=AsyncMock
        ) as mock_control:
            mock_control.return_value = {"code": 200}

            await client.set_nightlight("device_id", "H6160", on=True)

            mock_control.assert_called_once_with(
                "device_id", "H6160", CAPABILITY_TOGGLE, INSTANCE_NIGHTLIGHT_TOGGLE, 1
            )

    @pytest.mark.asyncio
    async def test_set_nightlight_off(self):
        """Test set_nightlight() disables nightlight."""
        client = GoveeApiClient("test_key")

        with patch.object(
            client, "control_device", new_callable=AsyncMock
        ) as mock_control:
            mock_control.return_value = {"code": 200}

            await client.set_nightlight("device_id", "H6160", on=False)

            mock_control.assert_called_once_with(
                "device_id", "H6160", CAPABILITY_TOGGLE, INSTANCE_NIGHTLIGHT_TOGGLE, 0
            )

    @pytest.mark.asyncio
    async def test_set_brightness(self):
        """Test set_brightness() sets device brightness."""
        client = GoveeApiClient("test_key")

        with patch.object(
            client, "control_device", new_callable=AsyncMock
        ) as mock_control:
            mock_control.return_value = {"code": 200}

            await client.set_brightness("device_id", "H6160", 75)

            mock_control.assert_called_once_with(
                "device_id", "H6160", CAPABILITY_RANGE, INSTANCE_BRIGHTNESS, 75
            )

    @pytest.mark.asyncio
    async def test_set_color_rgb(self):
        """Test set_color_rgb() converts RGB tuple to integer."""
        client = GoveeApiClient("test_key")

        with patch.object(
            client, "control_device", new_callable=AsyncMock
        ) as mock_control:
            mock_control.return_value = {"code": 200}

            rgb = (255, 128, 64)
            await client.set_color_rgb("device_id", "H6160", rgb)

            # Calculate expected integer: (255 << 16) + (128 << 8) + 64
            expected_int = 16744512

            mock_control.assert_called_once_with(
                "device_id",
                "H6160",
                CAPABILITY_COLOR_SETTING,
                INSTANCE_COLOR_RGB,
                expected_int,
            )

    @pytest.mark.asyncio
    async def test_set_color_temp_within_range(self):
        """Test set_color_temp() with temperature in valid range."""
        client = GoveeApiClient("test_key")

        with patch.object(
            client, "control_device", new_callable=AsyncMock
        ) as mock_control:
            mock_control.return_value = {"code": 200}

            await client.set_color_temp("device_id", "H6160", 5000)

            mock_control.assert_called_once_with(
                "device_id",
                "H6160",
                CAPABILITY_COLOR_SETTING,
                INSTANCE_COLOR_TEMP,
                5000,
            )

    @pytest.mark.asyncio
    async def test_set_color_temp_clamps_minimum(self):
        """Test set_color_temp() clamps temperature to minimum."""
        client = GoveeApiClient("test_key")

        with patch.object(
            client, "control_device", new_callable=AsyncMock
        ) as mock_control:
            mock_control.return_value = {"code": 200}

            # Try to set below minimum (2000K)
            await client.set_color_temp("device_id", "H6160", 1000)

            # Should clamp to COLOR_TEMP_MIN
            mock_control.assert_called_once()
            call_value = mock_control.call_args[0][4]
            assert call_value == COLOR_TEMP_MIN

    @pytest.mark.asyncio
    async def test_set_color_temp_clamps_maximum(self):
        """Test set_color_temp() clamps temperature to maximum."""
        client = GoveeApiClient("test_key")

        with patch.object(
            client, "control_device", new_callable=AsyncMock
        ) as mock_control:
            mock_control.return_value = {"code": 200}

            # Try to set above maximum (9000K)
            await client.set_color_temp("device_id", "H6160", 15000)

            # Should clamp to COLOR_TEMP_MAX
            mock_control.assert_called_once()
            call_value = mock_control.call_args[0][4]
            assert call_value == COLOR_TEMP_MAX

    @pytest.mark.asyncio
    async def test_set_scene(self):
        """Test set_scene() sends dynamic scene command."""
        client = GoveeApiClient("test_key")

        with patch.object(
            client, "control_device", new_callable=AsyncMock
        ) as mock_control:
            mock_control.return_value = {"code": 200}

            scene_value = {"id": 5, "name": "Sunset"}
            await client.set_scene("device_id", "H6160", scene_value)

            mock_control.assert_called_once_with(
                "device_id",
                "H6160",
                CAPABILITY_DYNAMIC_SCENE,
                INSTANCE_LIGHT_SCENE,
                scene_value,
            )

    @pytest.mark.asyncio
    async def test_set_diy_scene(self):
        """Test set_diy_scene() sends DIY scene command."""
        client = GoveeApiClient("test_key")

        with patch.object(
            client, "control_device", new_callable=AsyncMock
        ) as mock_control:
            mock_control.return_value = {"code": 200}

            diy_value = {"id": 101, "name": "My Custom"}
            await client.set_diy_scene("device_id", "H6160", diy_value)

            mock_control.assert_called_once_with(
                "device_id",
                "H6160",
                CAPABILITY_DYNAMIC_SCENE,
                INSTANCE_DIY_SCENE,
                diy_value,
            )

    @pytest.mark.asyncio
    async def test_set_segment_color(self):
        """Test set_segment_color() sends segment color command."""
        client = GoveeApiClient("test_key")

        with patch.object(
            client, "control_device", new_callable=AsyncMock
        ) as mock_control:
            mock_control.return_value = {"code": 200}

            rgb = (255, 0, 128)
            await client.set_segment_color("device_id", "H6160", segment=2, rgb=rgb)

            # Verify command structure
            mock_control.assert_called_once()
            call_args = mock_control.call_args[0]
            assert call_args[2] == CAPABILITY_SEGMENT_COLOR
            assert call_args[3] == INSTANCE_SEGMENTED_COLOR

            # Verify value structure
            value = call_args[4]
            assert value["segment"] == [2]
            # RGB should be converted to integer
            expected_rgb_int = (255 << 16) + (0 << 8) + 128
            assert value["rgb"] == expected_rgb_int

    @pytest.mark.asyncio
    async def test_set_segment_brightness(self):
        """Test set_segment_brightness() sends segment brightness command."""
        client = GoveeApiClient("test_key")

        with patch.object(
            client, "control_device", new_callable=AsyncMock
        ) as mock_control:
            mock_control.return_value = {"code": 200}

            await client.set_segment_brightness("device_id", "H6160", segment=3, brightness=80)

            # Verify command structure
            mock_control.assert_called_once()
            call_args = mock_control.call_args[0]
            assert call_args[2] == CAPABILITY_SEGMENT_COLOR
            assert call_args[3] == INSTANCE_SEGMENTED_BRIGHTNESS

            # Verify value structure
            value = call_args[4]
            assert value["segment"] == [3]
            assert value["brightness"] == 80

    @pytest.mark.asyncio
    async def test_set_music_mode_auto_color(self):
        """Test set_music_mode() with auto color enabled."""
        client = GoveeApiClient("test_key")

        with patch.object(
            client, "control_device", new_callable=AsyncMock
        ) as mock_control:
            mock_control.return_value = {"code": 200}

            await client.set_music_mode(
                "device_id", "H6160", mode="Energic", sensitivity=75, auto_color=True
            )

            # Verify command structure
            mock_control.assert_called_once()
            call_args = mock_control.call_args[0]
            assert call_args[2] == CAPABILITY_MUSIC_SETTING
            assert call_args[3] == INSTANCE_MUSIC_MODE

            # Verify value structure
            value = call_args[4]
            assert value["musicMode"] == "Energic"
            assert value["sensitivity"] == 75
            assert value["autoColor"] == 1
            assert "color" not in value  # Should not include color when auto

    @pytest.mark.asyncio
    async def test_set_music_mode_manual_color(self):
        """Test set_music_mode() with manual color."""
        client = GoveeApiClient("test_key")

        with patch.object(
            client, "control_device", new_callable=AsyncMock
        ) as mock_control:
            mock_control.return_value = {"code": 200}

            rgb = (128, 64, 200)
            await client.set_music_mode(
                "device_id",
                "H6160",
                mode="Rhythm",
                sensitivity=50,
                auto_color=False,
                rgb=rgb,
            )

            # Verify command structure
            mock_control.assert_called_once()
            value = mock_control.call_args[0][4]
            assert value["musicMode"] == "Rhythm"
            assert value["sensitivity"] == 50
            assert value["autoColor"] == 0
            # Should include RGB converted to integer
            expected_color_int = (128 << 16) + (64 << 8) + 200
            assert value["color"] == expected_color_int


# ==============================================================================
# Scene Query Tests
# ==============================================================================


class TestSceneQueries:
    """Test scene query methods."""

    @pytest.mark.asyncio
    async def test_get_dynamic_scenes_success(self):
        """Test get_dynamic_scenes() returns scene list."""
        client = GoveeApiClient("test_key")

        response_data = {
            "code": 200,
            "payload": {
                "capabilities": [
                    {
                        "instance": "lightScene",
                        "parameters": {
                            "options": [
                                {"name": "Sunrise", "value": 1},
                                {"name": "Sunset", "value": 2},
                            ]
                        },
                    }
                ]
            },
        }

        with patch.object(
            client, "_request", new_callable=AsyncMock
        ) as mock_request:
            mock_request.return_value = response_data

            scenes = await client.get_dynamic_scenes("device_id", "H6160")

            assert len(scenes) == 2
            assert scenes[0]["name"] == "Sunrise"
            assert scenes[1]["name"] == "Sunset"
            # Verify request was made
            mock_request.assert_called_once_with("POST", ENDPOINT_DYNAMIC_SCENES, payload=mock_request.call_args[1]["payload"])

    @pytest.mark.asyncio
    async def test_get_dynamic_scenes_empty(self):
        """Test get_dynamic_scenes() handles empty response."""
        client = GoveeApiClient("test_key")

        response_data = {"code": 200, "payload": {"capabilities": []}}

        with patch.object(
            client, "_request", new_callable=AsyncMock
        ) as mock_request:
            mock_request.return_value = response_data

            scenes = await client.get_dynamic_scenes("device_id", "H6160")

            assert scenes == []

    @pytest.mark.asyncio
    async def test_get_diy_scenes_success(self):
        """Test get_diy_scenes() returns DIY scene list."""
        client = GoveeApiClient("test_key")

        response_data = {
            "code": 200,
            "payload": {
                "capabilities": [
                    {
                        "instance": "diyScene",
                        "parameters": {
                            "options": [
                                {"name": "My Custom 1", "value": 101},
                                {"name": "My Custom 2", "value": 102},
                            ]
                        },
                    }
                ]
            },
        }

        with patch.object(
            client, "_request", new_callable=AsyncMock
        ) as mock_request:
            mock_request.return_value = response_data

            scenes = await client.get_diy_scenes("device_id", "H6160")

            assert len(scenes) == 2
            assert scenes[0]["name"] == "My Custom 1"
            assert scenes[1]["name"] == "My Custom 2"

    @pytest.mark.asyncio
    async def test_get_diy_scenes_with_error(self):
        """Test get_diy_scenes() handles API errors."""
        client = GoveeApiClient("test_key")

        with patch.object(
            client, "_request", new_callable=AsyncMock
        ) as mock_request:
            mock_request.side_effect = GoveeApiError("Device does not support DIY scenes")

            with pytest.raises(GoveeApiError):
                await client.get_diy_scenes("device_id", "H6160")


# ==============================================================================
# Connection Test Tests
# ==============================================================================


class TestConnectionTest:
    """Test connection test method."""

    @pytest.mark.asyncio
    async def test_test_connection_success(self):
        """Test test_connection() returns True on success."""
        client = GoveeApiClient("test_key")

        with patch.object(
            client, "get_devices", new_callable=AsyncMock
        ) as mock_get_devices:
            mock_get_devices.return_value = [{"device": "test", "sku": "H6160"}]

            result = await client.test_connection()

            assert result is True
            mock_get_devices.assert_called_once()

    @pytest.mark.asyncio
    async def test_test_connection_auth_failure(self):
        """Test test_connection() raises on auth error."""
        client = GoveeApiClient("invalid_key")

        with patch.object(
            client, "get_devices", new_callable=AsyncMock
        ) as mock_get_devices:
            mock_get_devices.side_effect = GoveeAuthError()

            with pytest.raises(GoveeAuthError):
                await client.test_connection()
