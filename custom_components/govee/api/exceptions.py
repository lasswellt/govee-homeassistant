"""Exceptions for Govee API v2.0 client.

These exceptions are used at the API layer and are intentionally
kept lightweight without Home Assistant dependencies. The coordinator
layer wraps these in translatable exceptions from govee.exceptions.
"""
from __future__ import annotations


class GoveeApiError(Exception):
    """Base exception for Govee API errors.

    All API-level exceptions inherit from this class.
    These are wrapped by the coordinator in translatable
    GoveeException subclasses for user-facing messages.
    """

    def __init__(self, message: str, code: int | None = None) -> None:
        """Initialize the exception."""
        super().__init__(message)
        self.code = code


class GoveeAuthError(GoveeApiError):
    """Authentication failure - invalid or expired API key."""

    def __init__(self, message: str = "Invalid API key") -> None:
        """Initialize auth error."""
        super().__init__(message, code=401)


class GoveeRateLimitError(GoveeApiError):
    """Rate limit exceeded."""

    def __init__(
        self,
        message: str = "Rate limit exceeded",
        retry_after: float | None = None,
    ) -> None:
        """Initialize rate limit error."""
        super().__init__(message, code=429)
        self.retry_after = retry_after


class GoveeDeviceError(GoveeApiError):
    """Device-specific error."""

    def __init__(self, device_id: str, message: str) -> None:
        """Initialize device error."""
        super().__init__(f"Device {device_id}: {message}")
        self.device_id = device_id


class GoveeCapabilityError(GoveeApiError):
    """Capability not supported error."""

    def __init__(self, device_id: str, capability: str) -> None:
        """Initialize capability error."""
        super().__init__(f"Device {device_id} does not support capability: {capability}")
        self.device_id = device_id
        self.capability = capability


class GoveeConnectionError(GoveeApiError):
    """Connection error - network issues."""

    def __init__(self, message: str = "Failed to connect to Govee API") -> None:
        """Initialize connection error."""
        super().__init__(message)
