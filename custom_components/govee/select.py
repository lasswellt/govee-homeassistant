"""Govee select platform for scene selection."""

from __future__ import annotations

import logging

from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import GoveeConfigEntry
from .api.const import INSTANCE_DIY_SCENE, INSTANCE_LIGHT_SCENE
from .const import DEVICE_TYPE_LIGHT
from .entities import GoveeSceneSelect

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: GoveeConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Govee select entities from a config entry."""
    coordinator = entry.runtime_data.coordinator
    devices = entry.runtime_data.devices

    entities: list[GoveeSceneSelect] = []

    for device in devices.values():
        # Only create select entities for light devices with scene support
        if device.device_type != DEVICE_TYPE_LIGHT:
            continue

        # Add dynamic scene selector if supported
        if device.supports_scenes:
            entities.append(
                GoveeSceneSelect(
                    coordinator,
                    device,
                    scene_type="dynamic",
                    instance=INSTANCE_LIGHT_SCENE,
                )
            )

        # Add DIY scene selector if supported (disabled by default)
        if device.supports_diy_scenes:
            entities.append(
                GoveeSceneSelect(
                    coordinator,
                    device,
                    scene_type="diy",
                    instance=INSTANCE_DIY_SCENE,
                )
            )

    _LOGGER.debug("Adding %d select entities", len(entities))
    async_add_entities(entities)

    # Register select platform services
    from .services import async_setup_select_services

    await async_setup_select_services(hass)
