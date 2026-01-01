from __future__ import annotations

import logging

from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import GoveeConfigEntry
from .const import DEVICE_TYPE_LIGHT, DEVICE_TYPE_SOCKET
from .entities import (
    GoveeAirDeflectorSwitch,
    GoveeGradientSwitch,
    GoveeNightLightSwitch,
    GoveeOscillationSwitch,
    GoveeSwitchEntity,
    GoveeThermostatSwitch,
    GoveeWarmMistSwitch,
)

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: GoveeConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data.coordinator
    devices = entry.runtime_data.devices

    entities: list[SwitchEntity] = []

    for device in devices.values():
        if device.device_type == DEVICE_TYPE_SOCKET:
            entities.append(GoveeSwitchEntity(coordinator, device))

        if device.device_type == DEVICE_TYPE_LIGHT and device.supports_nightlight:
            entities.append(GoveeNightLightSwitch(coordinator, device))

        if device.supports_oscillation_toggle:
            entities.append(GoveeOscillationSwitch(coordinator, device))

        if device.supports_thermostat_toggle:
            entities.append(GoveeThermostatSwitch(coordinator, device))

        if device.supports_gradient_toggle:
            entities.append(GoveeGradientSwitch(coordinator, device))

        if device.supports_warm_mist_toggle:
            entities.append(GoveeWarmMistSwitch(coordinator, device))

        if device.supports_air_deflector_toggle:
            entities.append(GoveeAirDeflectorSwitch(coordinator, device))

    _LOGGER.debug("Adding %d switch entities", len(entities))
    async_add_entities(entities)
