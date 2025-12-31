"""Govee switch platform for smart plugs, sockets, and feature toggles."""

from __future__ import annotations

import logging

from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import GoveeConfigEntry
from .const import DEVICE_TYPE_LIGHT, DEVICE_TYPE_SOCKET
from .entities import GoveeNightLightSwitch, GoveeSwitchEntity

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: GoveeConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Govee switches from a config entry."""
    coordinator = entry.runtime_data.coordinator
    devices = entry.runtime_data.devices

    entities: list[SwitchEntity] = []

    for device in devices.values():
        # Create switch entities for socket/plug devices
        if device.device_type == DEVICE_TYPE_SOCKET:
            entities.append(GoveeSwitchEntity(coordinator, device))

        # Create nightlight switch for light devices that support it
        if device.device_type == DEVICE_TYPE_LIGHT and device.supports_nightlight:
            entities.append(GoveeNightLightSwitch(coordinator, device))

    _LOGGER.debug("Adding %d switch entities", len(entities))
    async_add_entities(entities)
