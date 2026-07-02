"""Sensor platform for Govee integration.

Provides sensor entities for:
- Rate limit remaining (diagnostic)
- MQTT connection status (diagnostic)
- Temperature / humidity properties on stand-alone sensors (H5109, H5179)
- Leak sensor battery level (from BFF API polling)
- Leak sensor last wet event timestamp (from BFF API polling)
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    CONCENTRATION_PARTS_PER_MILLION,
    EntityCategory,
    PERCENTAGE,
    UnitOfTemperature,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    CONF_API_TEMPERATURE_UNIT,
    DEFAULT_API_TEMPERATURE_UNIT,
    DOMAIN,
    resolve_fahrenheit_conversion,
)
from .coordinator import GoveeCoordinator
from .entity import GoveeEntity
from .models import GoveeDevice
from .models.device import GoveeLeakSensor, leak_sensor_device_info

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

    # Add MQTT status sensors if MQTT is configured
    if coordinator.mqtt_client is not None:
        entities.append(GoveeMqttStatusSensor(coordinator, entry.entry_id))
        entities.append(GoveeMqttLastReceivedSensor(coordinator, entry.entry_id))

    # Per-device temperature / humidity sensors for stand-alone sensors
    # like H5109 and H5179 (issue #62). Anything that exposes the
    # corresponding `property` capability gets the entity, regardless of
    # device_type — the integration shouldn't have to know about every SKU.
    for device in coordinator.devices.values():
        if device.is_group:
            continue
        # Per-device connectivity diagnostics for every physical device:
        # last data received and last command sent (directional freshness).
        entities.append(GoveeAllDataLastUpdatedSensor(coordinator, device))
        entities.append(GoveeLastCommandSentSensor(coordinator, device))
        if device.supports_temperature_sensor:
            entities.append(GoveeTemperatureSensor(coordinator, device))
        if device.supports_humidity_sensor:
            entities.append(GoveeHumiditySensor(coordinator, device))
        # Air-quality index (H5106 monitor, H7124/H7126 purifiers) — read-only
        # property (#114). It is a coarse index, not a PM2.5 µg/m³ reading, but
        # it does vary (observed values 1 and 2), so it is a real numeric sensor
        # under the AQI device class — not the always-on presence flag a brief
        # mis-read had turned it into (issue #114).
        if device.supports_air_quality:
            entities.append(GoveeAirQualitySensor(coordinator, device))
        # CO₂ concentration in ppm (H5140 Smart CO₂ Monitor) — issue #117.
        if device.supports_co2:
            entities.append(GoveeCO2Sensor(coordinator, device))
        # Filter remaining-life (% on purifiers) — read-only property (#114).
        if device.supports_filter_life:
            entities.append(GoveeFilterLifeSensor(coordinator, device))
        if device.supports_temperature_sensor or device.supports_humidity_sensor:
            entities.append(GoveeSensorReadingTimestampSensor(coordinator, device))
        # Battery level from the BFF API, for either a BFF-synthesized
        # thermo-hygrometer (H5301, #86) OR a Developer-API BLE-bridged
        # thermometer whose battery the BFF carries but the Developer API does
        # not (e.g. H5110 via H5151, #83). Only create the entity when a battery
        # reading is actually present, so SKUs without one don't get a
        # permanently-unknown sensor.
        state = coordinator.get_state(device.device_id)
        if state is not None and state.battery is not None:
            entities.append(GoveeThermoBatterySensor(coordinator, device))

    # Register gateway hubs (leak + thermo) before async_add_entities so the
    # entities' `via_device` links resolve (must run after orphan-cleanup in
    # __init__.py, hence here, not in the coordinator's _async_setup).
    coordinator.register_thermo_hubs()
    coordinator.register_leak_hubs()
    seen_hubs: set[str] = set()
    for sensor in coordinator.leak_sensors.values():
        entities.append(GoveeLeakBatterySensor(coordinator, sensor))
        entities.append(GoveeLeakLastWetSensor(coordinator, sensor))
        entities.append(GoveeLeakAlertStatusSensor(coordinator, sensor))
        entities.append(GoveeLeakDeviceAddressSensor(sensor))
        if sensor.hub_device_id and sensor.hub_device_id not in seen_hubs:
            seen_hubs.add(sensor.hub_device_id)
            entities.append(GoveeLeakHubAddressSensor(sensor.hub_device_id))

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


class GoveeMqttLastReceivedSensor(CoordinatorEntity["GoveeCoordinator"], SensorEntity):
    """Timestamp of the last inbound MQTT state message (hub-level diagnostic).

    Shows when a real-time push update last arrived from AWS IoT. Renders as
    "X minutes ago" in HA. Reports unavailable until the first push arrives.
    """

    _attr_has_entity_name = True
    _attr_translation_key = "mqtt_last_received"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_icon = "mdi:cloud-sync-outline"

    def __init__(
        self,
        coordinator: GoveeCoordinator,
        entry_id: str,
    ) -> None:
        """Initialize the MQTT last-received timestamp sensor."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry_id}_mqtt_last_received"

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
    def native_value(self) -> datetime | None:
        """Return the UTC timestamp of the last MQTT push, or None."""
        return self.coordinator.mqtt_last_message_ts


class _BffThermometerAvailabilityMixin(GoveeEntity):
    """Availability that ignores ``state.online`` for BFF thermo-hygrometers.

    Battery/gateway-bridged sensors (e.g. H5310 via H5044) report ``online``
    as an unreliable liveness flag that flaps false between infrequent uploads,
    so the base ``GoveeEntity.available`` (which gates on ``online``) hides a
    valid, fresh reading. For these devices, gate only on coordinator success
    and a present reading; ``online`` remains exposed via the connectivity
    diagnostic entities (issue #97).
    """

    @property
    def available(self) -> bool:
        if self.coordinator.is_bff_thermometer(self._device_id):
            return self.coordinator.last_update_success and (
                self.device_state is not None
            )
        return super().available


class GoveeTemperatureSensor(_BffThermometerAvailabilityMixin, SensorEntity):
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
    _attr_suggested_display_precision = 1

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

        # Some thermometer/hygrometer SKUs (FAHRENHEIT_REPORTING_SKUS) return °F
        # via the Cloud API without unit metadata, while the native unit is
        # tagged °C — surfacing e.g. 101°F as 213.5°F (issues #72, #78, #96).
        # "auto" (default) converts those SKUs out-of-the-box; "fahrenheit"
        # forces conversion for any SKU; "celsius" trusts the API value as-is.
        # Heaters additionally report their own unit in the
        # temperature_setting STRUCT — in "auto" mode that explicit metadata
        # beats the SKU allowlist (H713B, issue #129).
        config_entry = self.coordinator.config_entry
        api_unit = (
            config_entry.options.get(
                CONF_API_TEMPERATURE_UNIT,
                DEFAULT_API_TEMPERATURE_UNIT,
            )
            if config_entry is not None
            else DEFAULT_API_TEMPERATURE_UNIT
        )
        if resolve_fahrenheit_conversion(
            self._device.sku,
            api_unit,
            getattr(state, "heater_temperature_unit", None),
        ):
            return (value - 32.0) * (5.0 / 9.0)

        return value


class GoveeAirQualitySensor(GoveeEntity, SensorEntity):
    """Read-only air-quality index (H5106 monitor, H7124/H7126) — issue #114.

    Backed by the ``devices.capabilities.property`` / ``airQuality`` capability.
    The Developer API returns a single index integer (no PM2.5 µg/m³ field), so
    this surfaces that index under the HA AQI device class.
    """

    _attr_has_entity_name = True
    _attr_translation_key = "sensor_air_quality"
    _attr_device_class = SensorDeviceClass.AQI
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(
        self,
        coordinator: GoveeCoordinator,
        device: GoveeDevice,
    ) -> None:
        super().__init__(coordinator, device)
        self._attr_unique_id = f"{device.device_id}_air_quality"

    @property
    def native_value(self) -> int | None:
        state = self.device_state
        return state.air_quality if state else None


class GoveeCO2Sensor(GoveeEntity, SensorEntity):
    """Read-only CO₂ concentration in ppm (H5140 Smart CO₂ Monitor) — #117.

    Backed by the ``devices.capabilities.property`` /
    ``carbonDioxideConcentration`` capability, reported in parts-per-million.
    """

    _attr_has_entity_name = True
    _attr_translation_key = "sensor_co2"
    _attr_device_class = SensorDeviceClass.CO2
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = CONCENTRATION_PARTS_PER_MILLION

    def __init__(
        self,
        coordinator: GoveeCoordinator,
        device: GoveeDevice,
    ) -> None:
        super().__init__(coordinator, device)
        self._attr_unique_id = f"{device.device_id}_co2"

    @property
    def native_value(self) -> int | None:
        state = self.device_state
        return state.carbon_dioxide if state else None


class GoveeFilterLifeSensor(GoveeEntity, SensorEntity):
    """Read-only remaining filter life % on air purifiers (H7124/H7126, #114).

    Backed by the ``devices.capabilities.property`` / ``filterLifeTime``
    capability, reported as a 0-100 percentage.
    """

    _attr_has_entity_name = True
    _attr_translation_key = "sensor_filter_life"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_icon = "mdi:air-filter"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(
        self,
        coordinator: GoveeCoordinator,
        device: GoveeDevice,
    ) -> None:
        super().__init__(coordinator, device)
        self._attr_unique_id = f"{device.device_id}_filter_life"

    @property
    def native_value(self) -> int | None:
        state = self.device_state
        return state.filter_life if state else None


class GoveeHumiditySensor(_BffThermometerAvailabilityMixin, SensorEntity):
    """Read-only humidity reading from devices like H5109 and H5179."""

    _attr_has_entity_name = True
    _attr_translation_key = "sensor_humidity"
    _attr_device_class = SensorDeviceClass.HUMIDITY
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_suggested_display_precision = 1

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


class GoveeThermoBatterySensor(_BffThermometerAvailabilityMixin, SensorEntity):
    """Battery level for a BFF-discovered thermo-hygrometer (issue #86).

    Govee returns ``battery`` in the BFF ``deviceSettings`` payload but the
    Developer API never exposes these devices, so this is the only battery
    source. Availability follows the BFF mixin (ignore flapping ``online``).
    """

    _attr_has_entity_name = True
    _attr_translation_key = "sensor_battery"
    _attr_device_class = SensorDeviceClass.BATTERY
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(
        self,
        coordinator: GoveeCoordinator,
        device: GoveeDevice,
    ) -> None:
        super().__init__(coordinator, device)
        self._attr_unique_id = f"{device.device_id}_battery"

    @property
    def native_value(self) -> int | None:
        state = self.device_state
        return state.battery if state else None


class GoveeSensorReadingTimestampSensor(GoveeEntity, SensorEntity):
    """When this device's temperature/humidity reading last changed.

    Govee batches BLE-bridged thermometers (H5075/H5110 via an H5151 gateway)
    to the cloud every 15-60 min, so a reading can look "frozen" while polling
    is healthy. This diagnostic timestamp makes the reading's age visible —
    "updated 22 min ago" — instead of leaving users guessing (#83). Semantic
    is last *change* (the Cloud API does not expose the device reading time).
    """

    _attr_has_entity_name = True
    _attr_translation_key = "sensor_reading_changed"
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(
        self,
        coordinator: GoveeCoordinator,
        device: GoveeDevice,
    ) -> None:
        super().__init__(coordinator, device)
        self._attr_unique_id = f"{device.device_id}_reading_changed"

    @property
    def native_value(self) -> datetime | None:
        return self.coordinator.sensor_reading_changed_at(self._device.device_id)


class GoveeAllDataLastUpdatedSensor(GoveeEntity, SensorEntity):
    """When this device last received data over any transport.

    Max of the per-transport last-success timestamps (Cloud API / MQTT /
    BLE). Renders as a relative "X ago" so users can see overall data
    freshness per device at a glance ("All Data Last Updated").
    """

    _attr_has_entity_name = True
    _attr_translation_key = "all_data_last_updated"
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:database-clock"

    def __init__(
        self,
        coordinator: GoveeCoordinator,
        device: GoveeDevice,
    ) -> None:
        super().__init__(coordinator, device)
        self._attr_unique_id = f"{device.device_id}_all_data_last_updated"

    @property
    def native_value(self) -> datetime | None:
        return self.coordinator.device_data_last_updated(self._device.device_id)


class GoveeLastCommandSentSensor(GoveeEntity, SensorEntity):
    """When this device was last sent a command over any transport.

    Max of the per-transport last-send timestamps (Cloud API / MQTT / BLE).
    The outbound counterpart to "Last Update Received" — renders as a
    relative "X ago" so users can see command activity per device.
    """

    _attr_has_entity_name = True
    _attr_translation_key = "last_command_sent"
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:send-clock"

    def __init__(
        self,
        coordinator: GoveeCoordinator,
        device: GoveeDevice,
    ) -> None:
        super().__init__(coordinator, device)
        self._attr_unique_id = f"{device.device_id}_last_command_sent"

    @property
    def native_value(self) -> datetime | None:
        return self.coordinator.device_last_command_sent(self._device.device_id)


class GoveeLeakBatterySensor(SensorEntity):
    """Sensor showing leak sensor battery level (from BFF API polling).

    Uses dispatcher signal for updates to avoid churning unrelated entities.
    """

    _attr_has_entity_name = True
    _attr_device_class = SensorDeviceClass.BATTERY
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_translation_key = "leak_battery"

    def __init__(self, coordinator: GoveeCoordinator, sensor: GoveeLeakSensor) -> None:
        self._coordinator = coordinator
        self._sensor = sensor
        self._attr_unique_id = f"{sensor.device_id}_battery"

    @property
    def device_info(self) -> DeviceInfo:
        return leak_sensor_device_info(self._sensor, DOMAIN)

    @property
    def native_value(self) -> int | None:
        state = self._coordinator.leak_states.get(self._sensor.device_id)
        return state.battery if state else None

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass, f"{DOMAIN}_leak_update", self._handle_leak_update
            )
        )

    @callback
    def _handle_leak_update(self) -> None:
        self.async_write_ha_state()


class GoveeLeakLastWetSensor(SensorEntity):
    """Sensor showing when the last leak was detected."""

    _attr_has_entity_name = True
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_translation_key = "leak_last_wet"

    def __init__(self, coordinator: GoveeCoordinator, sensor: GoveeLeakSensor) -> None:
        self._coordinator = coordinator
        self._sensor = sensor
        self._attr_unique_id = f"{sensor.device_id}_last_wet"

    @property
    def device_info(self) -> DeviceInfo:
        return leak_sensor_device_info(self._sensor, DOMAIN)

    @property
    def native_value(self) -> datetime | None:
        state = self._coordinator.leak_states.get(self._sensor.device_id)
        if state and state.last_wet_time:
            return datetime.fromtimestamp(state.last_wet_time / 1000, tz=timezone.utc)
        return None

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass, f"{DOMAIN}_leak_update", self._handle_leak_update
            )
        )

    @callback
    def _handle_leak_update(self) -> None:
        self.async_write_ha_state()


class GoveeLeakAlertStatusSensor(SensorEntity):
    """Sensor showing leak alert acknowledgment status."""

    _attr_has_entity_name = True
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = ["Pending", "Acknowledged"]
    _attr_icon = "mdi:bell-alert"
    _attr_translation_key = "leak_alert_status"

    def __init__(self, coordinator: GoveeCoordinator, sensor: GoveeLeakSensor) -> None:
        self._coordinator = coordinator
        self._sensor = sensor
        self._attr_unique_id = f"{sensor.device_id}_alert_status"

    @property
    def device_info(self) -> DeviceInfo:
        return leak_sensor_device_info(self._sensor, DOMAIN)

    @property
    def native_value(self) -> str | None:
        state = self._coordinator.leak_states.get(self._sensor.device_id)
        if state is None:
            return None
        return "Acknowledged" if state.read else "Pending"

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass, f"{DOMAIN}_leak_update", self._handle_leak_update
            )
        )

    @callback
    def _handle_leak_update(self) -> None:
        self.async_write_ha_state()


class GoveeLeakDeviceAddressSensor(SensorEntity):
    """Diagnostic sensor exposing the leak sensor's IEEE EUI-64 address.

    HA's `serial_number` device field expects a manufacturer serial; the
    Govee cloud only knows the wireless address, which is not the same
    thing. Surfacing it here as a diagnostic entity keeps it visible
    without mislabeling it on the device card.
    """

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_translation_key = "ieee_address"
    _attr_icon = "mdi:identifier"

    def __init__(self, sensor: GoveeLeakSensor) -> None:
        self._sensor = sensor
        self._attr_unique_id = f"{sensor.device_id}_address"
        self._attr_native_value = sensor.device_id

    @property
    def device_info(self) -> DeviceInfo:
        return leak_sensor_device_info(self._sensor, DOMAIN)


class GoveeLeakHubAddressSensor(SensorEntity):
    """Diagnostic sensor exposing the hub's IEEE EUI-64 address."""

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_translation_key = "ieee_address"
    _attr_icon = "mdi:identifier"

    def __init__(self, hub_device_id: str) -> None:
        self._hub_device_id = hub_device_id
        self._attr_unique_id = f"{hub_device_id}_address"
        self._attr_native_value = hub_device_id

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(identifiers={(DOMAIN, self._hub_device_id)})
