from __future__ import annotations

import logging

from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import GoveeConfigEntry
from .api.const import INSTANCE_DIY_SCENE, INSTANCE_LIGHT_SCENE, INSTANCE_SNAPSHOT
from .const import DEVICE_TYPE_LIGHT
from .entities import GoveeSceneSelect

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: GoveeConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data.coordinator
    devices = entry.runtime_data.devices

    entities: list[GoveeSceneSelect] = []

    for device in devices.values():
        if device.device_type != DEVICE_TYPE_LIGHT:
            continue

        if device.supports_scenes:
            entities.append(
                GoveeSceneSelect(
                    coordinator,
                    device,
                    scene_type="dynamic",
                    instance=INSTANCE_LIGHT_SCENE,
                )
            )

        if device.supports_diy_scenes:
            entities.append(
                GoveeSceneSelect(
                    coordinator,
                    device,
                    scene_type="diy",
                    instance=INSTANCE_DIY_SCENE,
                )
            )

        if device.supports_snapshots:
            entities.append(
                GoveeSceneSelect(
                    coordinator,
                    device,
                    scene_type="snapshot",
                    instance=INSTANCE_SNAPSHOT,
                )
            )

    _LOGGER.debug("Adding %d select entities", len(entities))
    async_add_entities(entities)

    from .services import async_setup_select_services

    await async_setup_select_services(hass)
