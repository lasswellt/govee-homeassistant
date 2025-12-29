"""Govee select platform for scene selection."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.select import SelectEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import GoveeConfigEntry
from .api.const import (
    CAPABILITY_DYNAMIC_SCENE,
    INSTANCE_DIY_SCENE,
    INSTANCE_LIGHT_SCENE,
)
from .const import DEVICE_TYPE_LIGHT
from .coordinator import GoveeDataUpdateCoordinator
from .entity import GoveeEntity
from .models import GoveeDevice, SceneOption

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


class GoveeSceneSelect(GoveeEntity, SelectEntity):
    """Select entity for Govee scenes."""

    def __init__(
        self,
        coordinator: GoveeDataUpdateCoordinator,
        device: GoveeDevice,
        scene_type: str,
        instance: str,
    ) -> None:
        """Initialize the select entity."""
        super().__init__(coordinator, device)

        self._scene_type = scene_type
        self._instance = instance
        self._options_map: dict[str, SceneOption] = {}

        # Set unique ID and name
        self._attr_unique_id = f"govee_{device.device_id}_{scene_type}_scene"

        if scene_type == "dynamic":
            self._attr_translation_key = "scene"
            self._attr_name = "Scene"
        else:
            self._attr_translation_key = "diy_scene"
            self._attr_name = "DIY Scene"
            # DIY scenes disabled by default
            self._attr_entity_registry_enabled_default = False

        # Initialize with empty options (will be populated async)
        self._attr_options = []
        self._attr_current_option = None

    async def async_added_to_hass(self) -> None:
        """Load scene options when entity is added."""
        await super().async_added_to_hass()
        await self._async_refresh_options()

    async def _async_refresh_options(self) -> None:
        """Fetch scene options from API."""
        try:
            if self._scene_type == "diy":
                scenes = await self.coordinator.async_get_diy_scenes(self._device_id)
            else:
                scenes = await self.coordinator.async_get_dynamic_scenes(self._device_id)

            self._options_map = {scene.name: scene for scene in scenes}
            self._attr_options = sorted(self._options_map.keys())

            _LOGGER.debug(
                "Loaded %d %s scenes for %s",
                len(scenes),
                self._scene_type,
                self._device.device_name,
            )

        except Exception as err:
            _LOGGER.warning(
                "Failed to load %s scenes for %s: %s",
                self._scene_type,
                self._device.device_name,
                err,
            )

    @property
    def current_option(self) -> str | None:
        """Return the current selected scene."""
        state = self.device_state
        if state is None or not state.current_scene:
            return None

        for name, scene in self._options_map.items():
            if self._scene_type == "diy":
                if f"diy_{scene.value}" == state.current_scene:
                    return name
            else:
                if isinstance(scene.value, dict):
                    scene_id = scene.value.get("id") or scene.value.get("paramId")
                    if str(scene_id) == state.current_scene:
                        return name
                elif str(scene.value) == state.current_scene:
                    return name

        return None

    async def async_select_option(self, option: str) -> None:
        """Select a scene."""
        if option not in self._options_map:
            _LOGGER.warning(
                "Unknown scene '%s' for %s",
                option,
                self._device.device_name,
            )
            return

        scene = self._options_map[option]
        scene_value = scene.to_command_value()

        try:
            await self.coordinator.async_control_device(
                self._device_id,
                CAPABILITY_DYNAMIC_SCENE,
                self._instance,
                scene_value,
            )
            _LOGGER.debug(
                "Set %s scene to '%s' for %s",
                self._scene_type,
                option,
                self._device.device_name,
            )
        except Exception as err:
            _LOGGER.error(
                "Failed to set %s scene '%s' for %s: %s",
                self._scene_type,
                option,
                self._device.device_name,
                err,
            )

    async def async_refresh_scenes(self) -> None:
        """Refresh scene list from API.

        This method is called by the govee.refresh_scenes service.
        """
        await self._async_refresh_options()
        self.async_write_ha_state()
