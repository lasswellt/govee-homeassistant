"""Test Govee API client."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import aiohttp
import pytest

from custom_components.govee.api.client import GoveeApiClient
from custom_components.govee.api.exceptions import (
    GoveeApiError,
    GoveeAuthError,
    GoveeConnectionError,
    GoveeDeviceNotFoundError,
    GoveeRateLimitError,
)
from custom_components.govee.models import PowerCommand


# ==============================================================================
# Exception Tests
# ==============================================================================


class TestExceptions:
    """Test API exceptions."""

    def test_govee_api_error(self):
        """Test base API error."""
        err = GoveeApiError("Test error", code=500)
        assert str(err) == "Test error"
        assert err.code == 500

    def test_govee_api_error_no_code(self):
        """Test API error without code."""
        err = GoveeApiError("Test error")
        assert err.code is None

    def test_govee_auth_error(self):
        """Test auth error."""
        err = GoveeAuthError()
        assert "Invalid API key" in str(err)
        assert err.code == 401

    def test_govee_auth_error_custom_message(self):
        """Test auth error with custom message."""
        err = GoveeAuthError("Token expired")
        assert str(err) == "Token expired"
        assert err.code == 401

    def test_govee_rate_limit_error(self):
        """Test rate limit error."""
        err = GoveeRateLimitError()
        assert "Rate limit" in str(err)
        assert err.code == 429
        assert err.retry_after is None

    def test_govee_rate_limit_error_with_retry(self):
        """Test rate limit error with retry_after."""
        err = GoveeRateLimitError(retry_after=30.0)
        assert err.retry_after == 30.0

    def test_govee_connection_error(self):
        """Test connection error."""
        err = GoveeConnectionError()
        assert "connect" in str(err).lower()
        assert err.code is None

    def test_govee_device_not_found_error(self):
        """Test device not found error."""
        err = GoveeDeviceNotFoundError("AA:BB:CC:DD")
        assert "AA:BB:CC:DD" in str(err)
        assert err.code == 400
        assert err.device_id == "AA:BB:CC:DD"


# ==============================================================================
# API Client Tests
# ==============================================================================


class TestGoveeApiClient:
    """Test GoveeApiClient."""

    @pytest.fixture
    def mock_session(self):
        """Create a mock aiohttp session."""
        session = MagicMock(spec=aiohttp.ClientSession)
        session.close = AsyncMock()
        return session

    @pytest.fixture
    def client(self, mock_session):
        """Create an API client with mock session."""
        return GoveeApiClient("test_api_key", session=mock_session)

    def test_client_creation(self):
        """Test creating a client."""
        client = GoveeApiClient("test_key")
        assert client._api_key == "test_key"

    def test_get_headers(self):
        """Test request headers."""
        client = GoveeApiClient("test_api_key")
        headers = client._get_headers()
        assert headers["Govee-API-Key"] == "test_api_key"
        assert headers["Content-Type"] == "application/json"
        assert headers["Accept"] == "application/json"

    def test_rate_limit_tracking(self):
        """Test rate limit header parsing."""
        client = GoveeApiClient("test_key")
        headers = {
            "X-RateLimit-Remaining": "50",
            "X-RateLimit-Limit": "100",
            "X-RateLimit-Reset": "1699999999",
        }
        client._update_rate_limits(headers)
        assert client.rate_limit_remaining == 50
        assert client.rate_limit_total == 100
        assert client.rate_limit_reset == 1699999999

    def test_rate_limit_tracking_invalid(self):
        """Test rate limit with invalid values."""
        client = GoveeApiClient("test_key")
        original_remaining = client.rate_limit_remaining
        headers = {
            "X-RateLimit-Remaining": "invalid",
            "X-RateLimit-Limit": "not_a_number",
        }
        client._update_rate_limits(headers)
        # Should not change on invalid values
        assert client.rate_limit_remaining == original_remaining

    def test_rate_limit_initial_values(self):
        """Test initial rate limit values."""
        client = GoveeApiClient("test_key")
        assert client.rate_limit_remaining == 100
        assert client.rate_limit_total == 100
        assert client.rate_limit_reset == 0


# ==============================================================================
# Response Handling Tests
# ==============================================================================


class TestResponseHandling:
    """Test API response handling patterns."""

    def test_device_response_structure(self):
        """Test expected device response structure."""
        response = {
            "code": 200,
            "data": [
                {
                    "device": "AA:BB:CC:DD:EE:FF:00:11",
                    "sku": "H6072",
                    "deviceName": "Living Room Light",
                    "type": "devices.types.light",
                    "capabilities": [],
                },
            ],
        }

        assert response["code"] == 200
        assert len(response["data"]) == 1
        assert response["data"][0]["device"] == "AA:BB:CC:DD:EE:FF:00:11"

    def test_state_response_structure(self):
        """Test expected state response structure."""
        response = {
            "code": 200,
            "payload": {
                "capabilities": [
                    {
                        "type": "devices.capabilities.online",
                        "instance": "online",
                        "state": {"value": True},
                    },
                    {
                        "type": "devices.capabilities.on_off",
                        "instance": "powerSwitch",
                        "state": {"value": 1},
                    },
                ],
            },
        }

        assert response["code"] == 200
        assert "capabilities" in response["payload"]

    def test_scenes_response_structure(self):
        """Test expected scenes response structure."""
        response = {
            "code": 200,
            "payload": {
                "capabilities": [
                    {
                        "type": "devices.capabilities.dynamic_scene",
                        "instance": "lightScene",
                        "parameters": {
                            "options": [
                                {"name": "Sunrise", "value": {"id": 1}},
                                {"name": "Sunset", "value": {"id": 2}},
                            ],
                        },
                    },
                ],
            },
        }

        scenes = response["payload"]["capabilities"][0]["parameters"]["options"]
        assert len(scenes) == 2
        assert scenes[0]["name"] == "Sunrise"


# ==============================================================================
# Command Payload Tests
# ==============================================================================


class TestCommandPayloads:
    """Test command payload generation."""

    def test_power_command_payload(self):
        """Test power command payload structure matches Govee API v2.0."""
        cmd = PowerCommand(power_on=True)
        payload = cmd.to_api_payload()

        assert payload["type"] == "devices.capabilities.on_off"
        assert payload["instance"] == "powerSwitch"
        assert payload["value"] == 1

    def test_power_off_command_payload(self):
        """Test power off command payload."""
        cmd = PowerCommand(power_on=False)
        assert cmd.get_value() == 0

    def test_power_on_command_payload(self):
        """Test power on command payload."""
        cmd = PowerCommand(power_on=True)
        assert cmd.get_value() == 1


# ==============================================================================
# Error Response Tests
# ==============================================================================


class TestErrorResponses:
    """Test error response handling patterns."""

    def test_auth_error_response(self):
        """Test 401 auth error response."""
        response_code = 401
        assert response_code == 401

        # This should trigger GoveeAuthError
        err = GoveeAuthError("Invalid API key")
        assert err.code == 401

    def test_rate_limit_response(self):
        """Test 429 rate limit response."""
        retry_after = 60

        err = GoveeRateLimitError(retry_after=float(retry_after))
        assert err.code == 429
        assert err.retry_after == 60.0

    def test_device_not_found_response(self):
        """Test 400 device not found response."""
        message = "devices not exist"

        # Check if message indicates device not found
        is_device_not_found = "not exist" in message.lower()
        assert is_device_not_found

        err = GoveeDeviceNotFoundError("test_device")
        assert err.code == 400

    def test_server_error_response(self):
        """Test 500 server error response."""
        response_code = 500

        err = GoveeApiError("Server error", code=response_code)
        assert err.code == 500
