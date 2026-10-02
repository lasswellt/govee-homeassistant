"""Event platform for Govee integration.

Provides event entities for button presses on Govee leak sensors (H5058).
Button press events are received via MQTT multiSync messages (0xEE 0x32).
"""

from __future__ import annotations

import logging

from homeassistant.components.event import EventDeviceClass, EventEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import GoveeConfigEntry, GoveeCoordinator
from .kettle.entities import kettle_events
from .models.device import GoveeLeakSensor, leak_sensor_device_info

_LOGGER = logging.getLogger(__name__)

# Push-only entities: the coordinator's dispatcher signal drives updates.
PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: GoveeConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Govee event entities from a config entry."""
    coordinator: GoveeCoordinator = entry.runtime_data

    entities: list[EventEntity] = []

    # Register hub devices first so leak sensors' `via_device` link resolves
    # (must run after orphan-cleanup in __init__.py).
    coordinator.register_leak_hubs()
    for sensor in coordinator.leak_sensors.values():
        entities.append(GoveeLeakButtonEvent(coordinator, sensor))
    # H7175 kettles: the kettle's own button.
    entities.extend(kettle_events(coordinator))

    if entities:
        async_add_entities(entities)
        _LOGGER.debug("Set up %d Govee leak button event entities", len(entities))


class GoveeLeakButtonEvent(EventEntity):
    """Event entity for button presses on a Govee leak sensor.

    Subscribes to the leak-specific dispatcher signal rather than the
    coordinator's generic update to avoid churning unrelated entities.
    """

    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_device_class = EventDeviceClass.BUTTON
    _attr_event_types = ["press"]
    _attr_translation_key = "leak_button"

    def __init__(
        self,
        coordinator: GoveeCoordinator,
        sensor: GoveeLeakSensor,
    ) -> None:
        """Initialize the button event entity."""
        self._coordinator = coordinator
        self._sensor = sensor
        self._attr_unique_id = f"{sensor.device_id}_button"

    @property
    def device_info(self) -> DeviceInfo:
        """Return device information for device registry."""
        return leak_sensor_device_info(self._sensor, DOMAIN)

    async def async_added_to_hass(self) -> None:
        """Subscribe to leak-specific dispatcher signal."""
        self.async_on_remove(async_dispatcher_connect(self.hass, f"{DOMAIN}_leak_update", self._handle_leak_update))

    @callback
    def _handle_leak_update(self) -> None:
        """Handle leak-specific update signal."""
        if self._coordinator.consume_button_press(self._sensor.device_id):
            self._trigger_event("press")
            self.async_write_ha_state()
