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
    PERCENTAGE,
    EntityCategory,
    UnitOfTemperature,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api.probe_thermometer import PROBES
from .const import (
    CONF_API_TEMPERATURE_UNIT,
    DEFAULT_API_TEMPERATURE_UNIT,
    DOMAIN,
    resolve_fahrenheit_conversion,
)
from .coordinator import GoveeCoordinator
from .entity import GoveeEntity
from .models import GoveeDevice, TransportHealth, TransportKind
from .models.device import GoveeLeakSensor, leak_sensor_device_info

try:  # HA >= 2026.7 (CONCENTRATION_PARTS_PER_MILLION is deprecated there)
    from homeassistant.const import UnitOfRatio

    PARTS_PER_MILLION: str = UnitOfRatio.PARTS_PER_MILLION
except ImportError:  # hacs.json still declares 2024.11.0 as the minimum
    from homeassistant.const import CONCENTRATION_PARTS_PER_MILLION

    PARTS_PER_MILLION = CONCENTRATION_PARTS_PER_MILLION

_LOGGER = logging.getLogger(__name__)

PARALLEL_UPDATES = 0

_PRIORITY_ORDER: tuple[TransportKind, ...] = ("ble", "lan", "mqtt", "cloud_api")
_ICON_BY_VALUE: dict[str, str] = {
    "lan": "mdi:lan",
    "mqtt": "mdi:cloud-sync",
    "cloud_api": "mdi:cloud",
    "ble": "mdi:bluetooth",
    "unavailable": "mdi:lan-pending",
}


def _is_delivering(health: TransportHealth | None) -> bool:
    """True only when ``is_available`` AND ``last_success_ts`` is set.

    MQTT, for example, marks itself available on broker connect even for
    devices that never push state — without the timestamp gate the sensor
    would surface a transport the device is not actually using.
    """
    return health is not None and health.is_available and health.last_success_ts is not None


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
        entities.append(GoveeConnectionModeSensor(coordinator, device))
        if device.is_group:
            continue
        # Per-device connectivity diagnostics for every physical device:
        # last data received and last command sent (directional freshness).
        entities.append(GoveeAllDataLastUpdatedSensor(coordinator, device))
        entities.append(GoveeLastCommandSentSensor(coordinator, device))
        # Probe thermometers get dedicated per-probe entities instead of the
        # generic temperature sensor: one reading per probe and channel
        # cannot be expressed by a single sensorTemperature value.
        if device.is_probe_thermometer:
            for probe in PROBES:
                for channel in ("core", "ambient"):
                    entities.append(
                        GoveeProbeTemperatureSensor(
                            coordinator, device, probe, channel
                        )
                    )
            continue

        if device.supports_temperature_sensor:
            entities.append(GoveeTemperatureSensor(coordinator, device))
        # Second probe on dual-probe SKUs (#150). Gated on a reading actually
        # being present rather than on the SKU: the same model ships with one
        # or two probes connected, and a device with nothing on probe 2 must
        # not gain an entity that can only ever read unknown.
        probe_state = coordinator.get_state(device.device_id)
        if probe_state is not None and probe_state.sensor_temperature_2 is not None:
            entities.append(GoveeSecondProbeTemperatureSensor(coordinator, device))
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
        #
        # Skipped only for devices the hub/BFF path ALSO creates a battery
        # entity for below, since both use the same `<device_id>_battery`
        # unique_id: HA keeps one and drops the other, leaving the dropped one's
        # registry entry behind as an Unavailable row (an H5058 behind an H5043,
        # issue #145). The test is membership of that path, not the SKU — a
        # standalone H5054 shares the leak SKUs but is never in it, and gets its
        # battery from the water-detector poll through this entity.
        state = coordinator.get_state(device.device_id)
        if (
            state is not None
            and state.battery is not None
            and not coordinator.is_bff_leak_sensor(device.device_id)
        ):
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
        # Water detectors (H5054) are sleepy gateway-bridged devices that report
        # online: false at poll time, same as the BFF thermometers — gate their
        # battery sensor on coordinator success, not online, or it would show
        # permanently unavailable (issues #97, #145).
        if self.coordinator.is_bff_thermometer(
            self._device_id
        ) or self.coordinator.is_water_detector(self._device_id):
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
    def _raw_reading(self) -> float | None:
        """The stored reading this entity converts. Overridden per probe."""
        state = self.device_state
        return state.sensor_temperature if state else None

    @property
    def native_value(self) -> float | None:
        raw = self._raw_reading
        if raw is None:
            return None

        state = self.device_state
        value = float(raw)

        # BFF-sourced readings (lastDeviceData) are already canonical °C from
        # _bff_reading's centi-scaling, so the SKU-based °F conversion below —
        # which targets the Developer-API path — must NOT apply. The H5179 lives
        # in both worlds: it's in FAHRENHEIT_REPORTING_SKUS for its Developer
        # path, but its value here comes via BFF (issue #141).
        if self.coordinator.is_bff_thermometer(self._device_id):
            return value

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
        # Heaters carry their unit in the temperature_setting STRUCT; for
        # everything else the account's own fahOpen preference is the next best
        # ground truth, because the Developer API mirrors it (issue #157).
        unit_hint = getattr(state, "device_temperature_unit", None)
        if unit_hint is None:
            unit_hint = self.coordinator.account_temperature_unit(self._device_id)

        if resolve_fahrenheit_conversion(self._device.sku, api_unit, unit_hint):
            return (value - 32.0) * (5.0 / 9.0)

        return value


class GoveeSecondProbeTemperatureSensor(GoveeTemperatureSensor):
    """The second temperature probe on a dual-probe SKU (H5112, issue #150).

    These fridge/freezer thermometers carry two independent probes and report
    them separately — ``tem`` and ``tem2`` in the BFF payload, with a matching
    second set of ``probeName2`` / ``temMin2`` / ``temMax2`` settings. The
    Developer API exposes a single ``sensorTemperature`` and has no concept of
    the second one, so this reading exists only on the BFF path.

    The probes are genuinely independent: reporter diagnostics on #150 showed
    two of three units with probe 1 unplugged (reporting the ``-1`` sentinel)
    while probe 2 read normally, which is why those devices surfaced no
    temperature at all. Created only when a probe-2 reading is actually
    present, so single-probe devices don't gain a permanently-unknown entity.

    Everything else — the °F/°C normalization, availability, device class —
    is inherited; only which stored field is read differs.
    """

    _attr_translation_key = "sensor_temperature_2"

    def __init__(
        self,
        coordinator: GoveeCoordinator,
        device: GoveeDevice,
    ) -> None:
        super().__init__(coordinator, device)
        self._attr_unique_id = f"{device.device_id}_temperature_2"

    @property
    def _raw_reading(self) -> float | None:
        state = self.device_state
        return state.sensor_temperature_2 if state else None


class GoveeProbeTemperatureSensor(_BffThermometerAvailabilityMixin, SensorEntity):
    """One channel of one probe on a probe thermometer (H5192).

    Four per device — core and ambient for each of the two probes — created
    unconditionally. The H5112 gates its second-probe entity on a reading being
    present at setup, but a pull device has nothing at setup by definition, so
    that gate would produce zero entities here. An unplugged probe reports the
    0xFFFF sentinel, which decodes to None and shows as unknown.
    """

    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(
        self,
        coordinator: GoveeCoordinator,
        device: GoveeDevice,
        probe: int,
        channel: str,
    ) -> None:
        """Initialize the probe temperature sensor."""
        super().__init__(coordinator, device)
        self._probe = probe
        self._channel = channel
        self._attr_unique_id = f"{device.device_id}_probe{probe}_{channel}"
        self._attr_translation_key = f"probe_{channel}"
        self._attr_translation_placeholders = {"probe": str(probe)}

    @property
    def native_value(self) -> float | None:
        """Return the current reading in degrees Celsius."""
        state = self.device_state
        if state is None:
            return None
        reading = state.probes.get(self._probe)
        if reading is None:
            return None
        return getattr(reading, self._channel)


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
    _attr_native_unit_of_measurement = PARTS_PER_MILLION

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


class GoveeConnectionModeSensor(GoveeEntity, SensorEntity):
    """Show the best currently reachable transport for a device."""

    _attr_has_entity_name = True
    _attr_translation_key = "connection_mode"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = ["lan", "mqtt", "cloud_api", "ble", "unavailable"]

    def __init__(self, coordinator: GoveeCoordinator, device: GoveeDevice) -> None:
        """Initialize the connection-mode sensor."""
        super().__init__(coordinator, device)
        self._attr_unique_id = f"{device.device_id}_connection_mode"

    def _active_transport(self) -> TransportKind | None:
        """The highest-priority transport currently delivering state, if any.

        ``is_available`` alone is insufficient — MQTT marks itself available
        on broker connect even for devices that never push state. A
        ``last_success_ts`` stamp is required so the surfaced transport has
        actually delivered state for this device.

        Returns the ``TransportKind`` rather than a plain string so callers
        that need to look the transport back up keep the narrow type.
        """
        if self._device.is_group:
            health = self.coordinator.get_transport_health(
                self._device_id, "cloud_api"
            )
            return "cloud_api" if _is_delivering(health) else None

        for kind in _PRIORITY_ORDER:
            if _is_delivering(self.coordinator.get_transport_health(self._device_id, kind)):
                return kind
        return None

    @property
    def native_value(self) -> str:
        """Return the current connection mode, or ``unavailable``."""
        return self._active_transport() or "unavailable"

    @property
    def icon(self) -> str:
        """Return the icon for the current connection mode."""
        return _ICON_BY_VALUE[self.native_value]

    @property
    def available(self) -> bool:
        """Return availability based only on coordinator health."""
        return self.coordinator.last_update_success

    @property
    def extra_state_attributes(self) -> dict[str, str]:
        """Return bridge identity and when the reported transport last delivered.

        ``last_delivered_at`` is the chosen transport's own
        ``last_success_ts``, not the time this property happened to be read.
        That distinction matters more than it looks: Home Assistant evaluates
        attributes on every state write and treats any change as a new state,
        so a value of "now" would record a fresh row for every device on every
        poll — forever, and carrying no information. Reading the transport's
        stamp means the attribute changes only when data actually arrives,
        which is also the thing a user wants to know.
        """
        attrs: dict[str, str] = {}

        mode = self._active_transport()
        if mode is not None:
            health = self.coordinator.get_transport_health(self._device_id, mode)
            if health is not None and health.last_success_ts is not None:
                attrs["last_delivered_at"] = health.last_success_ts.isoformat()

        if self._device.hub_device_id:
            attrs["via_gateway"] = self._device.hub_device_id
            return attrs

        route = self.coordinator.gateway_route(self._device_id)
        if route:
            gateway_device = route.get("device")
            if gateway_device:
                attrs["via_gateway"] = gateway_device
        return attrs


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
