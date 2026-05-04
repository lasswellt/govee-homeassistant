"""Sensor platform for Govee integration.

Provides sensor entities for:
- Rate limit remaining (diagnostic)
- MQTT connection status (diagnostic)
- Temperature / humidity properties on stand-alone sensors (H5109, H5179)
"""

from __future__ import annotations

import logging

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    EntityCategory,
    PERCENTAGE,
    UnitOfTemperature,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    CONF_API_TEMPERATURE_UNIT,
    DEFAULT_API_TEMPERATURE_UNIT,
    DOMAIN,
)
from .coordinator import GoveeCoordinator
from .entity import GoveeEntity
from .models import GoveeDevice

_LOGGER = logging.getLogger(__name__)

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Govee sensors from a config entry."""
    coordinator: GoveeCoordinator = entry.runtime_data

    entities: list[SensorEntity] = [
        GoveeRateLimitSensor(coordinator, entry.entry_id),
    ]

    # Add MQTT status sensor if MQTT is configured
    if coordinator.mqtt_client is not None:
        entities.append(GoveeMqttStatusSensor(coordinator, entry.entry_id))

    # Per-device temperature / humidity sensors for stand-alone sensors
    # like H5109 and H5179 (issue #62). Anything that exposes the
    # corresponding `property` capability gets the entity, regardless of
    # device_type — the integration shouldn't have to know about every SKU.
    for device in coordinator.devices.values():
        if device.is_group:
            continue
        if device.supports_temperature_sensor:
            entities.append(GoveeTemperatureSensor(coordinator, device))
        if device.supports_humidity_sensor:
            entities.append(GoveeHumiditySensor(coordinator, device))

    async_add_entities(entities)
    _LOGGER.debug("Set up %d Govee sensor entities", len(entities))


class GoveeRateLimitSensor(CoordinatorEntity["GoveeCoordinator"], SensorEntity):
    """Sensor showing API rate limit remaining.

    Helps users monitor their API usage and avoid hitting limits.
    """

    _attr_has_entity_name = True
    _attr_translation_key = "rate_limit_remaining"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = "requests"
    _attr_icon = "mdi:speedometer"

    def __init__(
        self,
        coordinator: GoveeCoordinator,
        entry_id: str,
    ) -> None:
        """Initialize the rate limit sensor."""
        super().__init__(coordinator)

        self._attr_unique_id = f"{entry_id}_rate_limit"

    @property
    def device_info(self) -> DeviceInfo:
        """Return device info for the integration hub."""
        return DeviceInfo(
            identifiers={(DOMAIN, "hub")},
            name="Govee Integration",
            manufacturer="Govee",
            model="Cloud API",
        )

    @property
    def native_value(self) -> int:
        """Return the current rate limit remaining."""
        return self.coordinator.api_rate_limit_remaining

    @property
    def extra_state_attributes(self) -> dict[str, int]:
        """Return additional rate limit info."""
        return {
            "total_limit": self.coordinator.api_rate_limit_total,
            "reset_time": self.coordinator.api_rate_limit_reset,
        }


class GoveeMqttStatusSensor(CoordinatorEntity["GoveeCoordinator"], SensorEntity):
    """Sensor showing MQTT connection status.

    Indicates whether real-time push updates are working.
    """

    _attr_has_entity_name = True
    _attr_translation_key = "mqtt_status"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = ["connected", "disconnected", "unavailable"]
    _attr_icon = "mdi:cloud-sync"

    def __init__(
        self,
        coordinator: GoveeCoordinator,
        entry_id: str,
    ) -> None:
        """Initialize the MQTT status sensor."""
        super().__init__(coordinator)

        self._attr_unique_id = f"{entry_id}_mqtt_status"

    @property
    def device_info(self) -> DeviceInfo:
        """Return device info for the integration hub."""
        return DeviceInfo(
            identifiers={(DOMAIN, "hub")},
            name="Govee Integration",
            manufacturer="Govee",
            model="Cloud API",
        )

    @property
    def native_value(self) -> str:
        """Return the current MQTT status."""
        mqtt_client = self.coordinator.mqtt_client
        if mqtt_client is None:
            return "unavailable"
        return "connected" if mqtt_client.connected else "disconnected"


class GoveeTemperatureSensor(GoveeEntity, SensorEntity):
    """Read-only temperature reading from devices like H5109 and H5179.

    Backed by the ``devices.capabilities.property`` / ``sensorTemperature``
    capability. Values are pushed through the standard coordinator state
    flow so MQTT updates and API polls both feed it.
    """

    _attr_has_entity_name = True
    _attr_translation_key = "sensor_temperature"
    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS

    def __init__(
        self,
        coordinator: GoveeCoordinator,
        device: GoveeDevice,
    ) -> None:
        super().__init__(coordinator, device)
        self._attr_unique_id = f"{device.device_id}_temperature"

    @property
    def native_value(self) -> float | None:
        state = self.device_state
        if not state or state.sensor_temperature is None:
            return None

        value = float(state.sensor_temperature)

        # Some thermometer/hygrometer devices report temperature in Fahrenheit
        # via the Govee Cloud API, but without unit metadata. Normalize to
        # Celsius here so HA displays the correct value under a Celsius native
        # unit (and can still convert if the user's system is Fahrenheit).
        api_unit = self.coordinator.config_entry.options.get(
            CONF_API_TEMPERATURE_UNIT,
            DEFAULT_API_TEMPERATURE_UNIT,
        )
        if api_unit == "fahrenheit":
            # Avoid HA-version-specific helpers; keep conversion local.
            return (value - 32.0) * (5.0 / 9.0)

        return value


class GoveeHumiditySensor(GoveeEntity, SensorEntity):
    """Read-only humidity reading from devices like H5109 and H5179."""

    _attr_has_entity_name = True
    _attr_translation_key = "sensor_humidity"
    _attr_device_class = SensorDeviceClass.HUMIDITY
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = PERCENTAGE

    def __init__(
        self,
        coordinator: GoveeCoordinator,
        device: GoveeDevice,
    ) -> None:
        super().__init__(coordinator, device)
        self._attr_unique_id = f"{device.device_id}_humidity"

    @property
    def native_value(self) -> float | None:
        state = self.device_state
        return state.sensor_humidity if state else None
