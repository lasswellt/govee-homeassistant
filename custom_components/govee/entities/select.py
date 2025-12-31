"""Govee select entity for scene selection."""

from __future__ import annotations

import logging

from homeassistant.components.select import SelectEntity

from ..api.const import CAPABILITY_DYNAMIC_SCENE
from ..coordinator import GoveeDataUpdateCoordinator
from ..models import GoveeDevice, SceneOption
from .base import GoveeEntity

_LOGGER = logging.getLogger(__name__)


class GoveeSceneSelect(GoveeEntity, SelectEntity):
    """Select entity for Govee scenes.

    Provides a dropdown selector for activating dynamic scenes or DIY scenes
    on compatible Govee devices. Scene options are fetched from the API when
    the entity is added to Home Assistant.

    Attributes:
        _scene_type: Type of scenes ("dynamic" or "diy")
        _instance: Capability instance name
        _options_map: Mapping of scene names to SceneOption objects
    """

    def __init__(
        self,
        coordinator: GoveeDataUpdateCoordinator,
        device: GoveeDevice,
        scene_type: str,
        instance: str,
    ) -> None:
        """Initialize the scene select entity.

        Args:
            coordinator: Data update coordinator
            device: Govee device
            scene_type: Type of scene selector ('dynamic' or 'diy')
            instance: API instance identifier (INSTANCE_LIGHT_SCENE or INSTANCE_DIY_SCENE)
        """
        super().__init__(coordinator, device)
        
        self._scene_type = scene_type
        self._instance = instance
        self._attr_unique_id = f"{device.device_id}_{scene_type}_scene"
        
        # Set entity description based on scene type
        from ..entity_descriptions import SELECT_DESCRIPTIONS
        
        self.entity_description = SELECT_DESCRIPTIONS[scene_type]

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
                scenes = await self.coordinator.async_get_dynamic_scenes(
                    self._device_id
                )

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
        """Select a scene.

        Args:
            option: Scene name to activate
        """
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
