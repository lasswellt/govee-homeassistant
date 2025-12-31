"""Sensor entities for Govee integration.

Provides rate limit monitoring sensors for API usage tracking.
These sensors are disabled by default and useful for debugging.
"""

from __future__ import annotations

from homeassistant.components.sensor import SensorEntity

from ..coordinator import GoveeDataUpdateCoordinator
from ..entity_descriptions.sensor import (
    GoveeSensorEntityDescription,
    SENSOR_DESCRIPTIONS,
)
from .base import GoveeEntity


class GoveeRateLimitSensor(SensorEntity):
    """Sensor for monitoring API rate limits.

    This is an integration-level sensor (not device-specific) that
    monitors the overall API rate limit status.
    """

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: GoveeDataUpdateCoordinator,
        description: GoveeSensorEntityDescription,
    ) -> None:
        """Initialize rate limit sensor.

        Args:
            coordinator: The data update coordinator
            description: Entity description
        """
        self._coordinator = coordinator
        self.entity_description = description
        self._attr_unique_id = f"govee_{description.key}"
        # No device - this is an integration-level sensor
        self._attr_device_info = None

    @property
    def available(self) -> bool:
        """Return True if entity is available."""
        return self._coordinator.last_update_success

    @property
    def native_value(self) -> int | None:
        """Return the current rate limit remaining."""
        if self.entity_description.key == "rate_limit_remaining_minute":
            return self._coordinator.rate_limit_remaining_minute
        elif self.entity_description.key == "rate_limit_remaining_day":
            return self._coordinator.rate_limit_remaining_day
        return None

    async def async_added_to_hass(self) -> None:
        """Subscribe to coordinator updates."""
        self.async_on_remove(
            self._coordinator.async_add_listener(self.async_write_ha_state)
        )
