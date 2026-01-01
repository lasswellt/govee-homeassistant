"""Govee API v2.0 client package."""
from __future__ import annotations

from .client import GoveeApiClient, RateLimiter
from .const import (
    BASE_URL,
    BRIGHTNESS_MAX,
    BRIGHTNESS_MIN,
    CAPABILITY_COLOR_SETTING,
    CAPABILITY_DYNAMIC_SCENE,
    CAPABILITY_MUSIC_SETTING,
    CAPABILITY_ON_OFF,
    CAPABILITY_RANGE,
    CAPABILITY_SEGMENT_COLOR,
    COLOR_TEMP_MAX,
    COLOR_TEMP_MIN,
    INSTANCE_BRIGHTNESS,
    INSTANCE_COLOR_RGB,
    INSTANCE_COLOR_TEMP,
    INSTANCE_DIY_SCENE,
    INSTANCE_LIGHT_SCENE,
    INSTANCE_MUSIC_MODE,
    INSTANCE_POWER_SWITCH,
    INSTANCE_SEGMENTED_BRIGHTNESS,
    INSTANCE_SEGMENTED_COLOR,
)
from .exceptions import (
    GoveeApiError,
    GoveeAuthError,
    GoveeCapabilityError,
    GoveeConnectionError,
    GoveeDeviceError,
    GoveeRateLimitError,
)

__all__ = [
    # Client
    "GoveeApiClient",
    "RateLimiter",
    # Exceptions
    "GoveeApiError",
    "GoveeAuthError",
    "GoveeCapabilityError",
    "GoveeConnectionError",
    "GoveeDeviceError",
    "GoveeRateLimitError",
    # Constants - API
    "BASE_URL",
    # Constants - Capabilities
    "CAPABILITY_COLOR_SETTING",
    "CAPABILITY_DYNAMIC_SCENE",
    "CAPABILITY_MUSIC_SETTING",
    "CAPABILITY_ON_OFF",
    "CAPABILITY_RANGE",
    "CAPABILITY_SEGMENT_COLOR",
    # Constants - Instances
    "INSTANCE_BRIGHTNESS",
    "INSTANCE_COLOR_RGB",
    "INSTANCE_COLOR_TEMP",
    "INSTANCE_DIY_SCENE",
    "INSTANCE_LIGHT_SCENE",
    "INSTANCE_MUSIC_MODE",
    "INSTANCE_POWER_SWITCH",
    "INSTANCE_SEGMENTED_BRIGHTNESS",
    "INSTANCE_SEGMENTED_COLOR",
    # Constants - Ranges
    "BRIGHTNESS_MAX",
    "BRIGHTNESS_MIN",
    "COLOR_TEMP_MAX",
    "COLOR_TEMP_MIN",
]
