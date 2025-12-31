"""Sensor entity descriptions for Govee integration.

Defines entity descriptions for rate limit monitoring sensors.
These sensors are disabled by default as they are primarily
useful for debugging API usage issues.
"""

from __future__ import annotations

from dataclasses import dataclass

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import EntityCategory


@dataclass(frozen=True, kw_only=True)
class GoveeSensorEntityDescription(SensorEntityDescription):
    """Describes a Govee sensor entity."""

    # No additional fields needed for now, but allows future extension


SENSOR_DESCRIPTIONS: dict[str, GoveeSensorEntityDescription] = {
    "rate_limit_remaining_minute": GoveeSensorEntityDescription(
        key="rate_limit_remaining_minute",
        translation_key="rate_limit_remaining_minute",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        native_unit_of_measurement="requests",
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:speedometer",
    ),
    "rate_limit_remaining_day": GoveeSensorEntityDescription(
        key="rate_limit_remaining_day",
        translation_key="rate_limit_remaining_day",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        native_unit_of_measurement="requests",
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:calendar-clock",
    ),
}
