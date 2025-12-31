"""Translatable exceptions for Govee integration.

This module provides Home Assistant-compatible translatable exceptions
that display user-friendly error messages in the configured language.

Exception Hierarchy:
    GoveeException (HomeAssistantError)
    ├── GoveeAuthenticationError - Authentication/API key issues
    ├── GoveeConnectionError - Network connectivity issues
    ├── GoveeRateLimitError - API rate limit exceeded
    ├── GoveeDeviceError - Device-specific errors
    └── GoveeCapabilityError - Unsupported capability errors
"""

from __future__ import annotations

from homeassistant.exceptions import HomeAssistantError

from .const import DOMAIN


class GoveeException(HomeAssistantError):
    """Base exception for Govee integration with translation support.

    All Govee exceptions inherit from this class to provide:
    - Automatic translation via Home Assistant's translation system
    - Consistent error handling across the integration
    - User-friendly error messages in the configured language

    Attributes:
        translation_domain: Always set to DOMAIN ("govee")
        translation_key: Key to look up in strings.json exceptions section
        translation_placeholders: Dynamic values to substitute in message
    """

    translation_domain: str = DOMAIN
    translation_key: str = "unknown_error"

    def __init__(
        self,
        translation_key: str | None = None,
        translation_placeholders: dict[str, str] | None = None,
    ) -> None:
        """Initialize translatable exception.

        Args:
            translation_key: Key for exception message in strings.json
            translation_placeholders: Dynamic values for message substitution
        """
        super().__init__()
        if translation_key:
            self.translation_key = translation_key
        self.translation_placeholders = translation_placeholders or {}


class GoveeAuthenticationError(GoveeException):
    """Authentication failure - invalid or expired API key.

    Raised when:
    - API key is invalid
    - API key has been revoked
    - API returns 401 Unauthorized

    This exception should trigger ConfigEntryAuthFailed for reauth flow.
    """

    translation_key = "authentication_failed"


class GoveeConnectionError(GoveeException):
    """Connection error - network or API unavailability.

    Raised when:
    - Network is unreachable
    - Govee API is down
    - DNS resolution fails
    - Timeout occurs

    This exception should trigger ConfigEntryNotReady for retry.
    """

    translation_key = "connection_failed"


class GoveeRateLimitError(GoveeException):
    """Rate limit exceeded.

    Raised when the Govee API rate limit is exceeded:
    - Per-minute limit: 100 requests/minute
    - Per-day limit: 10,000 requests/day

    Attributes:
        retry_after: Seconds until rate limit resets (if known)
    """

    translation_key = "rate_limit_exceeded"

    def __init__(self, retry_after: int | None = None) -> None:
        """Initialize with retry information.

        Args:
            retry_after: Seconds until rate limit resets
        """
        placeholders = {}
        if retry_after is not None:
            placeholders["retry_after"] = str(retry_after)
        else:
            placeholders["retry_after"] = "unknown"

        super().__init__(
            translation_key=self.translation_key,
            translation_placeholders=placeholders,
        )
        self.retry_after = retry_after


class GoveeDeviceError(GoveeException):
    """Device-specific error.

    Raised when an operation fails for a specific device:
    - Device not found
    - Device offline
    - Device returned error
    - State query failed
    """

    translation_key = "device_error"

    def __init__(self, device_id: str, device_name: str | None = None) -> None:
        """Initialize with device information.

        Args:
            device_id: Device identifier
            device_name: Human-readable device name
        """
        super().__init__(
            translation_key=self.translation_key,
            translation_placeholders={
                "device_id": device_id,
                "device_name": device_name or device_id,
            },
        )
        self.device_id = device_id


class GoveeCapabilityError(GoveeException):
    """Capability not supported error.

    Raised when attempting to use a capability the device doesn't support:
    - RGB color on non-RGB device
    - Segments on non-RGBIC device
    - Music mode on unsupported device
    """

    translation_key = "capability_not_supported"

    def __init__(self, device_id: str, capability: str) -> None:
        """Initialize with capability information.

        Args:
            device_id: Device identifier
            capability: Name of the unsupported capability
        """
        super().__init__(
            translation_key=self.translation_key,
            translation_placeholders={
                "device_id": device_id,
                "capability": capability,
            },
        )
        self.device_id = device_id
        self.capability = capability


class GoveeSceneError(GoveeException):
    """Scene-related error.

    Raised when scene operations fail:
    - Scene not found
    - Scene fetch failed
    - Invalid scene ID
    """

    translation_key = "scene_error"

    def __init__(self, scene_name: str | None = None) -> None:
        """Initialize with scene information.

        Args:
            scene_name: Name of the problematic scene
        """
        super().__init__(
            translation_key=self.translation_key,
            translation_placeholders={
                "scene_name": scene_name or "unknown",
            },
        )
