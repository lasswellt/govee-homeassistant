"""Select platform for Govee integration.

Provides select entities for scene control - one dropdown per device.
This replaces individual scene entities with a more manageable interface.
"""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .api.ble_packet import DIY_STYLE_NAMES
from .const import (
    CONF_ENABLE_DIY_SCENES,
    CONF_ENABLE_SCENES,
    DEFAULT_ENABLE_DIY_SCENES,
    DEFAULT_ENABLE_SCENES,
    SUFFIX_DIY_SCENE_SELECT,
    SUFFIX_DIY_STYLE_SELECT,
    SUFFIX_HDMI_SOURCE_SELECT,
    SUFFIX_HEATER_FAN_SPEED,
    SUFFIX_MUSIC_MODE_SELECT,
    SUFFIX_PURIFIER_MODE_SELECT,
    SUFFIX_SCENE_SELECT,
)
from .coordinator import GoveeCoordinator
from .entity import GoveeEntity
from .models import (
    GoveeDevice,
    ModeCommand,
    MusicModeCommand,
    SceneCommand,
    WorkModeCommand,
)
from .models.device import INSTANCE_HDMI_SOURCE, INSTANCE_PURIFIER_MODE

# DIY Style options for select entity
DIY_STYLE_OPTIONS = list(DIY_STYLE_NAMES.keys())

_LOGGER = logging.getLogger(__name__)

# Option for "no scene" / off state
SCENE_NONE = "None"


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Govee scene selects from a config entry."""
    coordinator: GoveeCoordinator = entry.runtime_data

    entities: list[SelectEntity] = []

    # Check if scenes are enabled
    enable_scenes = entry.options.get(CONF_ENABLE_SCENES, DEFAULT_ENABLE_SCENES)
    enable_diy_scenes = entry.options.get(
        CONF_ENABLE_DIY_SCENES, DEFAULT_ENABLE_DIY_SCENES
    )

    _LOGGER.debug(
        "Scene entity setup: enable_scenes=%s enable_diy_scenes=%s",
        enable_scenes,
        enable_diy_scenes,
    )

    for device in coordinator.devices.values():
        _LOGGER.debug(
            "Device %s: supports_scenes=%s supports_diy_scenes=%s is_group=%s",
            device.name,
            device.supports_scenes,
            device.supports_diy_scenes,
            device.is_group,
        )

        # Skip scene/DIY/music mode entities for group devices
        # Groups are virtual aggregation entities that don't support these features
        # via the API - they only support basic power/brightness/color control
        if device.is_group:
            _LOGGER.debug(
                "Skipping scene/DIY/music entities for group device %s "
                "(groups don't support these features)",
                device.name,
            )
            continue

        # Dynamic scenes
        if enable_scenes and device.supports_scenes:
            scenes = await coordinator.async_get_scenes(device.device_id)
            _LOGGER.debug("Fetched %d scenes for %s", len(scenes), device.name)
            if scenes:
                entities.append(
                    GoveeSceneSelectEntity(
                        coordinator=coordinator,
                        device=device,
                        scenes=scenes,
                    )
                )
                _LOGGER.debug("Created scene select entity for %s", device.name)

        # DIY scenes and DIY style selector
        # Availability gated on MQTT at runtime (no REST endpoint for DIY)
        if enable_diy_scenes and device.supports_diy_scenes:
            diy_scenes = await coordinator.async_get_diy_scenes(device.device_id)
            _LOGGER.debug("Fetched %d DIY scenes for %s", len(diy_scenes), device.name)
            if diy_scenes:
                entities.append(
                    GoveeDIYSceneSelectEntity(
                        coordinator=coordinator,
                        device=device,
                        scenes=diy_scenes,
                    )
                )
                _LOGGER.debug("Created DIY scene select entity for %s", device.name)

            # DIY style selector - availability gated on MQTT at runtime
            entities.append(
                GoveeDIYStyleSelectEntity(
                    coordinator=coordinator,
                    device=device,
                )
            )
            _LOGGER.debug("Created DIY style select entity for %s", device.name)

        # HDMI source selector (for devices like AI Sync Box H6604)
        if device.supports_hdmi_source:
            hdmi_options = device.get_hdmi_source_options()
            if hdmi_options:
                entities.append(
                    GoveeHdmiSourceSelectEntity(
                        coordinator=coordinator,
                        device=device,
                        options=hdmi_options,
                    )
                )
                _LOGGER.debug("Created HDMI source select entity for %s", device.name)

        # Music mode selector (for devices with STRUCT-based music mode)
        if device.has_struct_music_mode:
            music_options = device.get_music_mode_options()
            if music_options:
                entities.append(
                    GoveeMusicModeSelectEntity(
                        coordinator=coordinator,
                        device=device,
                        options=music_options,
                    )
                )
                _LOGGER.debug(
                    "Created music mode select entity for %s with %d modes",
                    device.name,
                    len(music_options),
                )

        # Heater fan speed selector
        if device.is_heater:
            fan_options = device.get_fan_speed_options()
            if fan_options:
                entities.append(
                    GoveeFanSpeedSelectEntity(
                        coordinator=coordinator,
                        device=device,
                        options=fan_options,
                    )
                )
                _LOGGER.debug(
                    "Created fan speed select entity for %s with %d speeds",
                    device.name,
                    len(fan_options),
                )

        # Purifier mode selector
        if device.is_purifier:
            purifier_options = device.get_purifier_mode_options()
            if purifier_options:
                entities.append(
                    GoveePurifierModeSelectEntity(
                        coordinator=coordinator,
                        device=device,
                        options=purifier_options,
                    )
                )
                _LOGGER.debug(
                    "Created purifier mode select entity for %s with %d modes",
                    device.name,
                    len(purifier_options),
                )

    async_add_entities(entities)
    _LOGGER.debug("Set up %d Govee scene select entities", len(entities))


class GoveeSceneSelectEntity(GoveeEntity, SelectEntity):
    """Govee scene select entity.

    Provides a dropdown to select and activate scenes on a device.
    Much more manageable than individual scene entities.

    Scene, Music Mode, and DreamView are mutually exclusive.
    When Music Mode or DreamView is activated, the scene selection shows "None".
    """

    _attr_translation_key = "govee_scene_select"
    _attr_icon = "mdi:palette"

    def __init__(
        self,
        coordinator: GoveeCoordinator,
        device: GoveeDevice,
        scenes: list[dict[str, Any]],
    ) -> None:
        """Initialize the scene select entity.

        Args:
            coordinator: Govee data coordinator.
            device: Device this select belongs to.
            scenes: List of scene data from API.
        """
        super().__init__(coordinator, device)

        # Build scene mapping: name -> (id, name)
        self._scene_map: dict[str, tuple[int, str]] = {}
        # Reverse mapping: scene_id (as string) -> option name
        self._scene_id_to_option: dict[str, str] = {}
        options = [SCENE_NONE]

        for scene_data in scenes:
            scene_id = scene_data.get("value", {}).get("id", 0)
            scene_name = scene_data.get("name", f"Scene {scene_id}")

            # Handle duplicate names by appending ID
            unique_name = scene_name
            counter = 1
            while unique_name in self._scene_map:
                unique_name = f"{scene_name} ({counter})"
                counter += 1

            self._scene_map[unique_name] = (scene_id, scene_name)
            self._scene_id_to_option[str(scene_id)] = unique_name
            options.append(unique_name)

        self._attr_options = options

        # Unique ID
        self._attr_unique_id = f"{device.device_id}{SUFFIX_SCENE_SELECT}"

    @property
    def current_option(self) -> str | None:
        """Return current selected option from state.

        Reads from coordinator state to reflect mutual exclusion.
        When DreamView or Music Mode is active, scene is cleared.
        """
        state = self.coordinator.get_state(self._device_id)
        if state and state.active_scene:
            # Look up option name from scene ID
            option = self._scene_id_to_option.get(state.active_scene)
            if option:
                return option
        return SCENE_NONE

    async def async_select_option(self, option: str) -> None:
        """Handle scene selection.

        Selecting a scene clears Music Mode and DreamView states.
        """
        if option == SCENE_NONE:
            # Send a color/color_temp command to exit the scene on the device
            await self.coordinator.async_clear_scene(self._device_id)
            self.async_write_ha_state()
            return

        scene_info = self._scene_map.get(option)
        if not scene_info:
            _LOGGER.warning("Unknown scene option: %s", option)
            return

        scene_id, scene_name = scene_info

        command = SceneCommand(
            scene_id=scene_id,
            scene_name=scene_name,
        )

        success = await self.coordinator.async_control_device(
            self._device_id,
            command,
        )

        if success:
            # State update with mutual exclusion is handled in coordinator
            self.async_write_ha_state()
            _LOGGER.debug(
                "Activated scene '%s' on %s",
                scene_name,
                self._device.name,
            )
        else:
            _LOGGER.warning(
                "Failed to activate scene '%s' on %s",
                scene_name,
                self._device.name,
            )


class GoveeDIYSceneSelectEntity(GoveeEntity, SelectEntity):
    """Govee DIY scene select entity.

    Provides a dropdown to select and activate DIY scenes on a device.
    DIY scenes are user-created custom effects stored on the device.

    DIY Scene, Music Mode, and DreamView are mutually exclusive.
    When Music Mode or DreamView is activated, the scene selection shows "None".
    """

    _attr_translation_key = "govee_diy_scene_select"
    _attr_icon = "mdi:palette-advanced"

    def __init__(
        self,
        coordinator: GoveeCoordinator,
        device: GoveeDevice,
        scenes: list[dict[str, Any]],
    ) -> None:
        """Initialize the DIY scene select entity.

        Args:
            coordinator: Govee data coordinator.
            device: Device this select belongs to.
            scenes: List of DIY scene data from API.
        """
        super().__init__(coordinator, device)

        # Build scene mapping: name -> (id, name)
        self._scene_map: dict[str, tuple[int, str]] = {}
        # Reverse mapping: scene_id (as string) -> option name
        self._scene_id_to_option: dict[str, str] = {}
        options = [SCENE_NONE]

        for scene_data in scenes:
            # DIY scenes: value is an int (scene ID), not a dict like regular scenes
            scene_id = scene_data.get("value", 0)
            scene_name = scene_data.get("name", f"DIY {scene_id}")

            # Handle duplicate names by appending ID
            unique_name = scene_name
            counter = 1
            while unique_name in self._scene_map:
                unique_name = f"{scene_name} ({counter})"
                counter += 1

            self._scene_map[unique_name] = (scene_id, scene_name)
            self._scene_id_to_option[str(scene_id)] = unique_name
            options.append(unique_name)

        self._attr_options = options

        # Unique ID
        self._attr_unique_id = f"{device.device_id}{SUFFIX_DIY_SCENE_SELECT}"

    @property
    def available(self) -> bool:
        """Return True if entity is available.

        Requires MQTT connection for DIY scene control (no REST endpoint).
        """
        if not self.coordinator.mqtt_connected:
            return False
        return super().available

    @property
    def current_option(self) -> str | None:
        """Return current selected option from state.

        Reads from coordinator state to reflect mutual exclusion.
        When DreamView or Music Mode is active, DIY scene is cleared.
        """
        state = self.coordinator.get_state(self._device_id)
        if state and state.active_diy_scene:
            # Look up option name from scene ID
            option = self._scene_id_to_option.get(state.active_diy_scene)
            if option:
                return option
        return SCENE_NONE

    async def async_select_option(self, option: str) -> None:
        """Handle DIY scene selection.

        Selecting a DIY scene clears Music Mode and DreamView states.
        """
        if option == SCENE_NONE:
            # Send a color/color_temp command to exit the scene on the device
            await self.coordinator.async_clear_scene(self._device_id)
            self.async_write_ha_state()
            return

        scene_info = self._scene_map.get(option)
        if not scene_info:
            _LOGGER.warning("Unknown DIY scene option: %s", option)
            return

        scene_id, scene_name = scene_info

        success = await self.coordinator.async_send_diy_scene(
            self._device_id,
            scene_id=scene_id,
            scene_name=scene_name,
        )

        if success:
            self.async_write_ha_state()
            _LOGGER.debug(
                "Activated DIY scene '%s' on %s",
                scene_name,
                self._device.name,
            )
        else:
            _LOGGER.warning(
                "Failed to activate DIY scene '%s' on %s",
                scene_name,
                self._device.name,
            )


class GoveeDIYStyleSelectEntity(GoveeEntity, SelectEntity):
    """Govee DIY style select entity.

    Provides a dropdown to select the animation style for DIY scenes.
    Requires MQTT connection for BLE passthrough commands.

    This entity is critical for the DIY speed slider to work correctly:
    the speed command must include the correct style byte, which is
    tracked when this selector is used.
    """

    _attr_translation_key = "govee_diy_style_select"
    _attr_icon = "mdi:animation-play"
    _attr_entity_registry_enabled_default = False

    def __init__(
        self,
        coordinator: GoveeCoordinator,
        device: GoveeDevice,
    ) -> None:
        """Initialize the DIY style select entity.

        Args:
            coordinator: Govee data coordinator.
            device: Device this select belongs to.
        """
        super().__init__(coordinator, device)

        # Available style options
        self._attr_options = DIY_STYLE_OPTIONS
        self._attr_current_option = DIY_STYLE_OPTIONS[0]  # Default to Fade

        # Unique ID
        self._attr_unique_id = f"{device.device_id}{SUFFIX_DIY_STYLE_SELECT}"

    async def async_added_to_hass(self) -> None:
        """Initialize default DIY style in coordinator state when added to HA."""
        await super().async_added_to_hass()

        # Initialize the style value in state if not already set
        # This ensures speed commands work even before user interacts with style selector
        state = self.coordinator.get_state(self._device_id)
        if state and state.diy_style_value is None:
            # Set default style value (Fade = 0)
            state.diy_style = DIY_STYLE_OPTIONS[0]
            state.diy_style_value = DIY_STYLE_NAMES[DIY_STYLE_OPTIONS[0]]
            _LOGGER.debug(
                "Initialized DIY style for %s: %s (value=%d)",
                self._device.name,
                state.diy_style,
                state.diy_style_value,
            )

    @property
    def available(self) -> bool:
        """Return True if entity is available.

        Requires MQTT connection for BLE passthrough.
        """
        if not self.coordinator.mqtt_connected:
            return False
        return super().available

    @property
    def current_option(self) -> str | None:
        """Return current selected option from state."""
        state = self.coordinator.get_state(self._device_id)
        if state and state.diy_style:
            return state.diy_style
        return self._attr_current_option

    async def async_select_option(self, option: str) -> None:
        """Handle style selection."""
        if option not in DIY_STYLE_OPTIONS:
            _LOGGER.warning("Unknown DIY style option: %s", option)
            return

        # Use default speed of 50 for DIY style animations
        speed = 50

        success = await self.coordinator.async_send_diy_style(
            self._device_id,
            option,
            speed,
        )

        if success:
            self._attr_current_option = option
            self.async_write_ha_state()
            _LOGGER.debug(
                "Set DIY style '%s' on %s",
                option,
                self._device.name,
            )
        else:
            _LOGGER.warning(
                "Failed to set DIY style '%s' on %s",
                option,
                self._device.name,
            )


class GoveeHdmiSourceSelectEntity(GoveeEntity, SelectEntity):
    """Govee HDMI source select entity.

    Provides a dropdown to select HDMI input source on devices like
    the Govee AI Sync Box (H6604).
    """

    _attr_translation_key = "govee_hdmi_source_select"
    _attr_icon = "mdi:hdmi-port"

    def __init__(
        self,
        coordinator: GoveeCoordinator,
        device: GoveeDevice,
        options: list[dict[str, Any]],
    ) -> None:
        """Initialize the HDMI source select entity.

        Args:
            coordinator: Govee data coordinator.
            device: Device this select belongs to.
            options: List of HDMI source options from capability parameters.
        """
        super().__init__(coordinator, device)

        # Build option mapping: display name -> value
        self._option_map: dict[str, int] = {}
        option_names: list[str] = []

        for opt in options:
            name = opt.get("name", "")
            value = opt.get("value")
            if name and value is not None:
                self._option_map[name] = value
                option_names.append(name)

        self._attr_options = option_names

        # Unique ID
        self._attr_unique_id = f"{device.device_id}{SUFFIX_HDMI_SOURCE_SELECT}"

    @property
    def current_option(self) -> str | None:
        """Return current selected option from state."""
        state = self.coordinator.get_state(self._device_id)
        if state and state.hdmi_source is not None:
            # Find option name matching the current value
            for name, value in self._option_map.items():
                if value == state.hdmi_source:
                    return name
        # Return first option as default if available
        return self._attr_options[0] if self._attr_options else None

    async def async_select_option(self, option: str) -> None:
        """Handle HDMI source selection."""
        value = self._option_map.get(option)
        if value is None:
            _LOGGER.warning("Unknown HDMI source option: %s", option)
            return

        command = ModeCommand(
            mode_instance=INSTANCE_HDMI_SOURCE,
            value=value,
        )

        success = await self.coordinator.async_control_device(
            self._device_id,
            command,
        )

        if success:
            self.async_write_ha_state()
            _LOGGER.debug(
                "Set HDMI source '%s' (value=%d) on %s",
                option,
                value,
                self._device.name,
            )
        else:
            _LOGGER.warning(
                "Failed to set HDMI source '%s' on %s",
                option,
                self._device.name,
            )


class GoveeMusicModeSelectEntity(GoveeEntity, SelectEntity):
    """Govee music mode select entity.

    Provides a dropdown to select music reactive mode on devices with
    STRUCT-based music mode capability. This sends the mode via REST API
    with a structured payload containing musicMode and sensitivity.

    Music mode options vary by device but typically include:
    - Rhythm (1)
    - Spectrum (2)
    - Rolling (3)
    - Separation (4)
    - Hopping (5)
    - PianoKeys (6)
    - Fountain (7)
    - DayAndNight (8)
    - Sprouting (9)
    - Shiny (10)
    - Energic (11)
    """

    _attr_translation_key = "govee_music_mode_select"
    _attr_icon = "mdi:music"

    def __init__(
        self,
        coordinator: GoveeCoordinator,
        device: GoveeDevice,
        options: list[dict[str, Any]],
    ) -> None:
        """Initialize the music mode select entity.

        Args:
            coordinator: Govee data coordinator.
            device: Device this select belongs to.
            options: List of music mode options from capability parameters.
        """
        super().__init__(coordinator, device)

        # Build option mapping: display name -> value
        self._option_map: dict[str, int] = {}
        option_names: list[str] = []

        for opt in options:
            name = opt.get("name", "")
            value = opt.get("value")
            if name and value is not None:
                self._option_map[name] = value
                option_names.append(name)

        self._attr_options = option_names

        # Unique ID
        self._attr_unique_id = f"{device.device_id}{SUFFIX_MUSIC_MODE_SELECT}"

    @property
    def current_option(self) -> str | None:
        """Return current selected option from state."""
        state = self.coordinator.get_state(self._device_id)
        if state and state.music_mode_name is not None:
            # Check if the name is in our options
            if state.music_mode_name in self._option_map:
                return state.music_mode_name
        # Return first option as default if available
        return self._attr_options[0] if self._attr_options else None

    async def async_select_option(self, option: str) -> None:
        """Handle music mode selection."""
        value = self._option_map.get(option)
        if value is None:
            _LOGGER.warning("Unknown music mode option: %s", option)
            return

        # Get current sensitivity from state, default to 50
        state = self.coordinator.get_state(self._device_id)
        sensitivity = 50
        if state and state.music_sensitivity is not None:
            sensitivity = state.music_sensitivity

        command = MusicModeCommand(
            music_mode=value,
            sensitivity=sensitivity,
            auto_color=1,  # Use automatic colors
        )

        success = await self.coordinator.async_control_device(
            self._device_id,
            command,
        )

        if success:
            self.async_write_ha_state()
            _LOGGER.debug(
                "Set music mode '%s' (value=%d, sensitivity=%d) on %s",
                option,
                value,
                sensitivity,
                self._device.name,
            )
        else:
            _LOGGER.warning(
                "Failed to set music mode '%s' on %s",
                option,
                self._device.name,
            )


class GoveeFanSpeedSelectEntity(GoveeEntity, SelectEntity):
    """Govee heater fan speed select entity.

    Provides a dropdown to select fan speed mode on heater devices
    (typically Low, Medium, High).
    """

    _attr_translation_key = "govee_fan_speed_select"
    _attr_icon = "mdi:fan"

    def __init__(
        self,
        coordinator: GoveeCoordinator,
        device: GoveeDevice,
        options: list[dict[str, Any]],
    ) -> None:
        """Initialize the fan speed select entity.

        Args:
            coordinator: Govee data coordinator.
            device: Device this select belongs to.
            options: List of fan speed options from capability parameters.
        """
        super().__init__(coordinator, device)

        # Build option mapping: display name -> (work_mode, mode_value)
        self._option_map: dict[str, tuple[int, int]] = {}
        option_names: list[str] = []

        for opt in options:
            name = opt.get("name", "")
            work_mode = opt.get("work_mode")
            mode_value = opt.get("mode_value")
            if name and work_mode is not None and mode_value is not None:
                self._option_map[name] = (work_mode, mode_value)
                option_names.append(name)

        self._attr_options = option_names

        # Unique ID
        self._attr_unique_id = f"{device.device_id}{SUFFIX_HEATER_FAN_SPEED}"

    @property
    def current_option(self) -> str | None:
        """Return current selected option from state."""
        state = self.coordinator.get_state(self._device_id)
        if state and state.work_mode is not None:
            # Try matching both work_mode and mode_value
            if state.mode_value is not None:
                for name, (wm, mv) in self._option_map.items():
                    if wm == state.work_mode and mv == state.mode_value:
                        return name
            # Fallback: match on work_mode only
            for name, (wm, _mv) in self._option_map.items():
                if wm == state.work_mode:
                    return name
        # Return first option as default if available
        return self._attr_options[0] if self._attr_options else None

    async def async_select_option(self, option: str) -> None:
        """Handle fan speed selection."""
        values = self._option_map.get(option)
        if values is None:
            _LOGGER.warning("Unknown fan speed option: %s", option)
            return

        work_mode, mode_value = values
        command = WorkModeCommand(
            work_mode=work_mode,
            mode_value=mode_value,
        )

        success = await self.coordinator.async_control_device(
            self._device_id,
            command,
        )

        if success:
            self.async_write_ha_state()
            _LOGGER.debug(
                "Set fan speed '%s' (work_mode=%d, mode_value=%d) on %s",
                option,
                work_mode,
                mode_value,
                self._device.name,
            )
        else:
            _LOGGER.warning(
                "Failed to set fan speed '%s' on %s",
                option,
                self._device.name,
            )


class GoveePurifierModeSelectEntity(GoveeEntity, SelectEntity):
    """Govee air purifier mode select entity.

    Provides a dropdown to select purifier mode on air purifier devices
    (typically Sleep, Low, High, Custom).
    """

    _attr_translation_key = "govee_purifier_mode_select"
    _attr_icon = "mdi:air-purifier"

    def __init__(
        self,
        coordinator: GoveeCoordinator,
        device: GoveeDevice,
        options: list[dict[str, Any]],
    ) -> None:
        """Initialize the purifier mode select entity.

        Args:
            coordinator: Govee data coordinator.
            device: Device this select belongs to.
            options: List of purifier mode options from capability parameters.
        """
        super().__init__(coordinator, device)

        # Build option mapping: display name -> value
        self._option_map: dict[str, int] = {}
        option_names: list[str] = []

        for opt in options:
            name = opt.get("name", "")
            value = opt.get("value")
            if name and value is not None:
                self._option_map[name] = value
                option_names.append(name)

        self._attr_options = option_names

        # Unique ID
        self._attr_unique_id = f"{device.device_id}{SUFFIX_PURIFIER_MODE_SELECT}"

    @property
    def current_option(self) -> str | None:
        """Return current selected option from state."""
        state = self.coordinator.get_state(self._device_id)
        if state and state.purifier_mode is not None:
            # Find option name matching the current value
            for name, value in self._option_map.items():
                if value == state.purifier_mode:
                    return name
        # Return first option as default if available
        return self._attr_options[0] if self._attr_options else None

    async def async_select_option(self, option: str) -> None:
        """Handle purifier mode selection."""
        value = self._option_map.get(option)
        if value is None:
            _LOGGER.warning("Unknown purifier mode option: %s", option)
            return

        command = ModeCommand(
            mode_instance=INSTANCE_PURIFIER_MODE,
            value=value,
        )

        success = await self.coordinator.async_control_device(
            self._device_id,
            command,
        )

        if success:
            self.async_write_ha_state()
            _LOGGER.debug(
                "Set purifier mode '%s' (value=%d) on %s",
                option,
                value,
                self._device.name,
            )
        else:
            _LOGGER.warning(
                "Failed to set purifier mode '%s' on %s",
                option,
                self._device.name,
            )
