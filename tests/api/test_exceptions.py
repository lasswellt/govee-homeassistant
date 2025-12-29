"""Test Govee API exceptions."""
from __future__ import annotations

import pytest

from custom_components.govee.api.exceptions import (
    GoveeError,
    GoveeAuthError,
    GoveeRateLimitError,
    GoveeConnectionError,
    GoveeApiError,
    GoveeDeviceError,
    GoveeCapabilityError,
)


class TestGoveeError:
    """Test base GoveeError exception."""

    def test_create_base_error(self):
        """Test creating base error with message."""
        error = GoveeError("Test error message")
        assert str(error) == "Test error message"
        assert isinstance(error, Exception)

    def test_base_error_inheritance(self):
        """Test that all Govee errors inherit from GoveeError."""
        assert issubclass(GoveeAuthError, GoveeError)
        assert issubclass(GoveeRateLimitError, GoveeError)
        assert issubclass(GoveeConnectionError, GoveeError)
        assert issubclass(GoveeApiError, GoveeError)
        assert issubclass(GoveeDeviceError, GoveeError)
        assert issubclass(GoveeCapabilityError, GoveeError)


class TestGoveeAuthError:
    """Test GoveeAuthError exception."""

    def test_create_auth_error(self):
        """Test creating authentication error."""
        error = GoveeAuthError("Invalid API key")
        assert str(error) == "Invalid API key"
        assert isinstance(error, GoveeError)

    def test_raise_auth_error(self):
        """Test raising authentication error."""
        with pytest.raises(GoveeAuthError) as exc_info:
            raise GoveeAuthError("401 Unauthorized")

        assert "401 Unauthorized" in str(exc_info.value)

    def test_catch_as_govee_error(self):
        """Test catching auth error as base GoveeError."""
        try:
            raise GoveeAuthError("Invalid credentials")
        except GoveeError as e:
            assert isinstance(e, GoveeAuthError)
            assert "Invalid credentials" in str(e)


class TestGoveeRateLimitError:
    """Test GoveeRateLimitError exception."""

    def test_create_rate_limit_error(self):
        """Test creating rate limit error."""
        error = GoveeRateLimitError("Rate limit exceeded")
        assert str(error) == "Rate limit exceeded"
        assert isinstance(error, GoveeError)

    def test_raise_rate_limit_error(self):
        """Test raising rate limit error."""
        with pytest.raises(GoveeRateLimitError) as exc_info:
            raise GoveeRateLimitError("429 Too Many Requests")

        assert "429 Too Many Requests" in str(exc_info.value)

    def test_rate_limit_with_details(self):
        """Test rate limit error with details."""
        error = GoveeRateLimitError(
            "Rate limit: 100 requests per minute exceeded. Retry after 60s"
        )
        assert "100 requests per minute" in str(error)
        assert "Retry after 60s" in str(error)


class TestGoveeConnectionError:
    """Test GoveeConnectionError exception."""

    def test_create_connection_error(self):
        """Test creating connection error."""
        error = GoveeConnectionError("Failed to connect to Govee API")
        assert str(error) == "Failed to connect to Govee API"
        assert isinstance(error, GoveeError)

    def test_raise_connection_error(self):
        """Test raising connection error."""
        with pytest.raises(GoveeConnectionError) as exc_info:
            raise GoveeConnectionError("Network timeout")

        assert "Network timeout" in str(exc_info.value)

    def test_connection_error_with_cause(self):
        """Test connection error with underlying cause."""
        original_error = ConnectionError("Connection refused")
        error = GoveeConnectionError(f"API connection failed: {original_error}")

        assert "API connection failed" in str(error)
        assert "Connection refused" in str(error)


class TestGoveeApiError:
    """Test GoveeApiError exception."""

    def test_create_api_error(self):
        """Test creating API error."""
        error = GoveeApiError("API returned error code 500")
        assert str(error) == "API returned error code 500"
        assert isinstance(error, GoveeError)

    def test_raise_api_error(self):
        """Test raising API error."""
        with pytest.raises(GoveeApiError) as exc_info:
            raise GoveeApiError("Internal server error")

        assert "Internal server error" in str(exc_info.value)

    def test_api_error_with_response_data(self):
        """Test API error with response data."""
        error = GoveeApiError("Error code 1234: Device not found")
        assert "Error code 1234" in str(error)
        assert "Device not found" in str(error)


class TestGoveeDeviceError:
    """Test GoveeDeviceError exception."""

    def test_create_device_error(self):
        """Test creating device error."""
        error = GoveeDeviceError("Device is offline")
        assert str(error) == "Device is offline"
        assert isinstance(error, GoveeError)

    def test_raise_device_error(self):
        """Test raising device error."""
        with pytest.raises(GoveeDeviceError) as exc_info:
            raise GoveeDeviceError("Device not responding")

        assert "Device not responding" in str(exc_info.value)

    def test_device_error_with_device_id(self):
        """Test device error with device ID."""
        error = GoveeDeviceError(
            "Device AA:BB:CC:DD:EE:FF is not available"
        )
        assert "AA:BB:CC:DD:EE:FF" in str(error)


class TestGoveeCapabilityError:
    """Test GoveeCapabilityError exception."""

    def test_create_capability_error(self):
        """Test creating capability error."""
        error = GoveeCapabilityError("Device does not support color temperature")
        assert str(error) == "Device does not support color temperature"
        assert isinstance(error, GoveeError)

    def test_raise_capability_error(self):
        """Test raising capability error."""
        with pytest.raises(GoveeCapabilityError) as exc_info:
            raise GoveeCapabilityError("Capability not supported")

        assert "Capability not supported" in str(exc_info.value)

    def test_capability_error_specific_feature(self):
        """Test capability error for specific feature."""
        error = GoveeCapabilityError(
            "Device H6001 does not support RGB color control"
        )
        assert "RGB color control" in str(error)
        assert "H6001" in str(error)


class TestExceptionHierarchy:
    """Test exception hierarchy and catching."""

    def test_catch_all_govee_errors(self):
        """Test catching any Govee error with base exception."""
        errors_to_test = [
            GoveeAuthError("Auth"),
            GoveeRateLimitError("Rate limit"),
            GoveeConnectionError("Connection"),
            GoveeApiError("API"),
            GoveeDeviceError("Device"),
            GoveeCapabilityError("Capability"),
        ]

        for error in errors_to_test:
            with pytest.raises(GoveeError):
                raise error

    def test_specific_error_catching(self):
        """Test catching specific error types."""
        # Auth error
        with pytest.raises(GoveeAuthError):
            raise GoveeAuthError("Invalid key")

        # Rate limit error
        with pytest.raises(GoveeRateLimitError):
            raise GoveeRateLimitError("Too many requests")

        # Connection error
        with pytest.raises(GoveeConnectionError):
            raise GoveeConnectionError("Timeout")

    def test_error_not_caught_by_wrong_type(self):
        """Test that errors are not caught by wrong exception type."""
        with pytest.raises(GoveeAuthError):
            try:
                raise GoveeAuthError("Auth failed")
            except GoveeRateLimitError:
                # Should not catch auth error
                pass
