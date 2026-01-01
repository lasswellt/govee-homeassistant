"""Tests for Govee integration translatable exceptions."""
from __future__ import annotations

import pytest

from custom_components.govee.const import DOMAIN
from custom_components.govee.exceptions import (
    GoveeException,
    GoveeAuthenticationError,
    GoveeConnectionError,
    GoveeRateLimitError,
    GoveeDeviceError,
    GoveeCapabilityError,
)


class TestGoveeException:
    """Tests for base GoveeException."""

    def test_default_translation_key(self) -> None:
        """Test default translation key is set."""
        exc = GoveeException()
        assert exc.translation_key == "unknown_error"
        assert exc.translation_domain == DOMAIN

    def test_custom_translation_key(self) -> None:
        """Test custom translation key can be provided."""
        exc = GoveeException(translation_key="custom_error")
        assert exc.translation_key == "custom_error"

    def test_translation_placeholders(self) -> None:
        """Test translation placeholders are set."""
        exc = GoveeException(
            translation_key="error_with_value",
            translation_placeholders={"value": "42"},
        )
        assert exc.translation_placeholders == {"value": "42"}

    def test_empty_placeholders_default(self) -> None:
        """Test empty placeholders default."""
        exc = GoveeException()
        assert exc.translation_placeholders == {}

    def test_is_home_assistant_error(self) -> None:
        """Test exception inherits from HomeAssistantError."""
        from homeassistant.exceptions import HomeAssistantError

        exc = GoveeException()
        assert isinstance(exc, HomeAssistantError)


class TestGoveeAuthenticationError:
    """Tests for GoveeAuthenticationError."""

    def test_translation_key(self) -> None:
        """Test authentication error has correct translation key."""
        exc = GoveeAuthenticationError()
        assert exc.translation_key == "authentication_failed"
        assert exc.translation_domain == DOMAIN

    def test_is_govee_exception(self) -> None:
        """Test inherits from GoveeException."""
        exc = GoveeAuthenticationError()
        assert isinstance(exc, GoveeException)

    def test_can_be_raised(self) -> None:
        """Test exception can be raised and caught."""
        with pytest.raises(GoveeAuthenticationError):
            raise GoveeAuthenticationError()


class TestGoveeConnectionError:
    """Tests for GoveeConnectionError."""

    def test_translation_key(self) -> None:
        """Test connection error has correct translation key."""
        exc = GoveeConnectionError()
        assert exc.translation_key == "connection_failed"
        assert exc.translation_domain == DOMAIN

    def test_is_govee_exception(self) -> None:
        """Test inherits from GoveeException."""
        exc = GoveeConnectionError()
        assert isinstance(exc, GoveeException)


class TestGoveeRateLimitError:
    """Tests for GoveeRateLimitError."""

    def test_translation_key(self) -> None:
        """Test rate limit error has correct translation key."""
        exc = GoveeRateLimitError()
        assert exc.translation_key == "rate_limit_exceeded"
        assert exc.translation_domain == DOMAIN

    def test_retry_after_with_value(self) -> None:
        """Test retry_after is stored and in placeholders."""
        exc = GoveeRateLimitError(retry_after=60)
        assert exc.retry_after == 60
        assert exc.translation_placeholders["retry_after"] == "60"

    def test_retry_after_none(self) -> None:
        """Test retry_after is None when not provided."""
        exc = GoveeRateLimitError()
        assert exc.retry_after is None
        assert exc.translation_placeholders["retry_after"] == "unknown"

    def test_retry_after_zero(self) -> None:
        """Test retry_after can be zero."""
        exc = GoveeRateLimitError(retry_after=0)
        assert exc.retry_after == 0
        assert exc.translation_placeholders["retry_after"] == "0"

    def test_is_govee_exception(self) -> None:
        """Test inherits from GoveeException."""
        exc = GoveeRateLimitError()
        assert isinstance(exc, GoveeException)


class TestGoveeDeviceError:
    """Tests for GoveeDeviceError."""

    def test_translation_key(self) -> None:
        """Test device error has correct translation key."""
        exc = GoveeDeviceError(device_id="AA:BB:CC:DD")
        assert exc.translation_key == "device_error"
        assert exc.translation_domain == DOMAIN

    def test_device_id_stored(self) -> None:
        """Test device_id is stored on exception."""
        exc = GoveeDeviceError(device_id="AA:BB:CC:DD")
        assert exc.device_id == "AA:BB:CC:DD"

    def test_placeholders_with_device_name(self) -> None:
        """Test placeholders include device name when provided."""
        exc = GoveeDeviceError(device_id="AA:BB:CC:DD", device_name="Living Room Light")
        assert exc.translation_placeholders["device_id"] == "AA:BB:CC:DD"
        assert exc.translation_placeholders["device_name"] == "Living Room Light"

    def test_placeholders_without_device_name(self) -> None:
        """Test device_name falls back to device_id when not provided."""
        exc = GoveeDeviceError(device_id="AA:BB:CC:DD")
        assert exc.translation_placeholders["device_id"] == "AA:BB:CC:DD"
        assert exc.translation_placeholders["device_name"] == "AA:BB:CC:DD"

    def test_is_govee_exception(self) -> None:
        """Test inherits from GoveeException."""
        exc = GoveeDeviceError(device_id="test")
        assert isinstance(exc, GoveeException)


class TestGoveeCapabilityError:
    """Tests for GoveeCapabilityError."""

    def test_translation_key(self) -> None:
        """Test capability error has correct translation key."""
        exc = GoveeCapabilityError(device_id="AA:BB:CC:DD", capability="colorRgb")
        assert exc.translation_key == "capability_not_supported"
        assert exc.translation_domain == DOMAIN

    def test_attributes_stored(self) -> None:
        """Test device_id and capability are stored."""
        exc = GoveeCapabilityError(device_id="AA:BB:CC:DD", capability="musicMode")
        assert exc.device_id == "AA:BB:CC:DD"
        assert exc.capability == "musicMode"

    def test_placeholders(self) -> None:
        """Test placeholders include device and capability info."""
        exc = GoveeCapabilityError(device_id="AA:BB:CC:DD", capability="segmentedColorRgb")
        assert exc.translation_placeholders["device_id"] == "AA:BB:CC:DD"
        assert exc.translation_placeholders["capability"] == "segmentedColorRgb"

    def test_is_govee_exception(self) -> None:
        """Test inherits from GoveeException."""
        exc = GoveeCapabilityError(device_id="test", capability="test")
        assert isinstance(exc, GoveeException)


class TestExceptionHierarchy:
    """Tests for exception class hierarchy."""

    def test_all_exceptions_inherit_from_base(self) -> None:
        """Test all Govee exceptions inherit from GoveeException."""
        exceptions = [
            GoveeAuthenticationError(),
            GoveeConnectionError(),
            GoveeRateLimitError(),
            GoveeDeviceError(device_id="test"),
            GoveeCapabilityError(device_id="test", capability="test"),
        ]
        for exc in exceptions:
            assert isinstance(exc, GoveeException)

    def test_all_exceptions_have_translation_domain(self) -> None:
        """Test all exceptions have the Govee translation domain."""
        exceptions = [
            GoveeException(),
            GoveeAuthenticationError(),
            GoveeConnectionError(),
            GoveeRateLimitError(),
            GoveeDeviceError(device_id="test"),
            GoveeCapabilityError(device_id="test", capability="test"),
        ]
        for exc in exceptions:
            assert exc.translation_domain == DOMAIN

    def test_exceptions_can_be_caught_by_base(self) -> None:
        """Test all specific exceptions can be caught by base class."""
        exceptions_to_raise = [
            GoveeAuthenticationError(),
            GoveeConnectionError(),
            GoveeRateLimitError(retry_after=30),
            GoveeDeviceError(device_id="test"),
            GoveeCapabilityError(device_id="test", capability="test"),
        ]

        for exc in exceptions_to_raise:
            with pytest.raises(GoveeException):
                raise exc

    def test_specific_exceptions_distinguishable(self) -> None:
        """Test specific exception types can be distinguished."""
        try:
            raise GoveeAuthenticationError()
        except GoveeAuthenticationError:
            pass
        except GoveeException:
            pytest.fail("Should have caught specific AuthenticationError first")

        try:
            raise GoveeRateLimitError(retry_after=10)
        except GoveeRateLimitError as exc:
            assert exc.retry_after == 10
        except GoveeException:
            pytest.fail("Should have caught specific RateLimitError first")
