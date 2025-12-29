"""Govee light platform."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ATTR_COLOR_TEMP_KELVIN,
    ATTR_EFFECT,
    ATTR_RGB_COLOR,
    ColorMode,
    LightEntity,
    LightEntityFeature,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from . import GoveeConfigEntry
from .api.const import (
    CAPABILITY_COLOR_SETTING,
    CAPABILITY_DYNAMIC_SCENE,
    CAPABILITY_ON_OFF,
    CAPABILITY_RANGE,
    CAPABILITY_SEGMENT_COLOR,
    INSTANCE_BRIGHTNESS,
    INSTANCE_COLOR_RGB,
    INSTANCE_COLOR_TEMP,
    INSTANCE_LIGHT_SCENE,
    INSTANCE_POWER_SWITCH,
)
from .const import (
    COLOR_TEMP_KELVIN_MAX,
    COLOR_TEMP_KELVIN_MIN,
    CONF_OFFLINE_IS_OFF,
    CONF_USE_ASSUMED_STATE,
    DEVICE_TYPE_LIGHT,
)
from .coordinator import GoveeDataUpdateCoordinator
from .entity import GoveeEntity
from .models import GoveeDevice

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: GoveeConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Govee lights from a config entry."""
    coordinator = entry.runtime_data.coordinator
    devices = entry.runtime_data.devices

    entities: list[GoveeLightEntity] = []
    for device in devices.values():
        # Only create light entities for light devices
        if device.device_type == DEVICE_TYPE_LIGHT or device.supports_on_off:
            entities.append(GoveeLightEntity(coordinator, device, entry))

    _LOGGER.debug("Adding %d light entities", len(entities))
    async_add_entities(entities)

    # Register light platform services
    from .services import async_setup_services
    await async_setup_services(hass)


class GoveeLightEntity(GoveeEntity, LightEntity, RestoreEntity):
    """Govee light entity with v2.0 API features and state restoration."""

    _attr_name = None  # Use device name

    def __init__(
        self,
        coordinator: GoveeDataUpdateCoordinator,
        device: GoveeDevice,
        entry: GoveeConfigEntry,
    ) -> None:
        """Initialize the light entity."""
        super().__init__(coordinator, device)

        self._entry = entry
        self._attr_unique_id = f"govee_{entry.title}_{device.device_id}"

        # Build supported color modes from capabilities
        self._attr_supported_color_modes = self._determine_color_modes()

        # Build supported features
        self._attr_supported_features = self._determine_features()

        # Build effect list from scene capabilities
        self._effect_map: dict[str, dict[str, Any]] = {}
        self._build_effect_list()

        # Color temp range from device capabilities
        temp_range = device.get_color_temp_range()
        self._attr_min_color_temp_kelvin = temp_range[0]
        self._attr_max_color_temp_kelvin = temp_range[1]

    async def async_added_to_hass(self) -> None:
        """Run when entity is added to Home Assistant.

        Restore previous state for group devices that can't be queried.
        """
        await super().async_added_to_hass()

        # Only restore state for group devices
        if not self._is_group_device:
            return

        # Check if we already have state from coordinator
        state = self.device_state
        if state and state.power_state is not None:
            return  # Already have state, don't overwrite

        # Restore from previous state
        last_state = await self.async_get_last_state()
        if last_state is None:
            _LOGGER.debug(
                "No previous state to restore for group device %s",
                self._device.device_name,
            )
            return

        # Restore power state
        if last_state.state in ("on", "off"):
            restored_power = last_state.state == "on"

            # Apply to coordinator state
            if state:
                state.power_state = restored_power
                state.online = False  # Still can't query

                # Restore brightness if available
                if last_state.attributes.get(ATTR_BRIGHTNESS):
                    # Convert HA range (0-255) to API range (0-100)
                    brightness = last_state.attributes[ATTR_BRIGHTNESS]
                    state.brightness = round(brightness * 100 / 255)

                # Restore color if available
                if last_state.attributes.get(ATTR_RGB_COLOR):
                    state.color_rgb = tuple(last_state.attributes[ATTR_RGB_COLOR])

                # Restore color temp if available
                if last_state.attributes.get(ATTR_COLOR_TEMP_KELVIN):
                    state.color_temp_kelvin = last_state.attributes[ATTR_COLOR_TEMP_KELVIN]

                _LOGGER.info(
                    "Restored state for group device %s: power=%s, brightness=%s",
                    self._device.device_name,
                    restored_power,
                    state.brightness,
                )

                # Trigger update
                self.async_write_ha_state()

    def _determine_color_modes(self) -> set[ColorMode]:
        """Determine supported color modes from device capabilities."""
        modes: set[ColorMode] = set()

        if self._device.supports_color:
            modes.add(ColorMode.RGB)
        if self._device.supports_color_temp:
            modes.add(ColorMode.COLOR_TEMP)

        # If no color modes, check for brightness-only or on/off
        if not modes:
            if self._device.supports_brightness:
                modes.add(ColorMode.BRIGHTNESS)
            else:
                modes.add(ColorMode.ONOFF)

        return modes

    def _determine_features(self) -> LightEntityFeature:
        """Determine supported features from device capabilities."""
        features = LightEntityFeature(0)

        if self._device.supports_scenes:
            features |= LightEntityFeature.EFFECT

        return features

    def _build_effect_list(self) -> None:
        """Build effect list from device scene capabilities."""
        scene_options = self._device.get_scene_options()

        effects: list[str] = []
        for option in scene_options:
            name = option.get("name", "")
            if name:
                effects.append(name)
                self._effect_map[name] = option

        if effects:
            self._attr_effect_list = sorted(effects)
            _LOGGER.debug(
                "Device %s has %d effects: %s",
                self._device.device_name,
                len(effects),
                effects[:5],  # Log first 5
            )

    # === State Properties ===

    @property
    def is_on(self) -> bool | None:
        """Return true if light is on."""
        state = self.device_state
        if state is None:
            return None

        # Handle offline_is_off option
        if not state.online:
            if self._entry.options.get(CONF_OFFLINE_IS_OFF, False):
                return False
            return None

        return state.power_state

    @property
    def brightness(self) -> int | None:
        """Return the brightness (0-255)."""
        state = self.device_state
        if state is None or state.brightness is None:
            return None

        # Convert from API range (0-100) to HA range (0-255)
        return round(state.brightness * 255 / 100)

    @property
    def rgb_color(self) -> tuple[int, int, int] | None:
        """Return the RGB color."""
        state = self.device_state
        if state is None:
            return None
        return state.color_rgb

    @property
    def color_temp_kelvin(self) -> int | None:
        """Return the color temperature in Kelvin."""
        state = self.device_state
        if state is None:
            return None
        return state.color_temp_kelvin

    @property
    def color_mode(self) -> ColorMode | None:
        """Return the current color mode."""
        state = self.device_state
        if state is None:
            return None

        # Determine current mode based on state
        if state.color_temp_kelvin is not None and ColorMode.COLOR_TEMP in self._attr_supported_color_modes:
            return ColorMode.COLOR_TEMP
        if state.color_rgb is not None and ColorMode.RGB in self._attr_supported_color_modes:
            return ColorMode.RGB
        if ColorMode.BRIGHTNESS in self._attr_supported_color_modes:
            return ColorMode.BRIGHTNESS
        return ColorMode.ONOFF

    @property
    def effect(self) -> str | None:
        """Return the current effect."""
        state = self.device_state
        if state is None:
            return None
        return state.current_scene_name

    @property
    def assumed_state(self) -> bool:
        """Return true if we're using assumed state.

        This shows two buttons (on/off) in the UI instead of a toggle.
        """
        return self._entry.options.get(CONF_USE_ASSUMED_STATE, True)

    # === Control Methods ===

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the light on."""
        _LOGGER.debug(
            "async_turn_on for %s, kwargs: %s",
            self._device.device_name,
            kwargs,
        )

        # Handle effect (scene) first - it's exclusive
        if ATTR_EFFECT in kwargs:
            await self._async_set_effect(kwargs[ATTR_EFFECT])
            return

        # Handle color attributes
        if ATTR_RGB_COLOR in kwargs:
            rgb = kwargs[ATTR_RGB_COLOR]
            await self._async_set_color_rgb(rgb)

        if ATTR_COLOR_TEMP_KELVIN in kwargs:
            temp = kwargs[ATTR_COLOR_TEMP_KELVIN]
            await self._async_set_color_temp(temp)

        if ATTR_BRIGHTNESS in kwargs:
            brightness = kwargs[ATTR_BRIGHTNESS]
            await self._async_set_brightness(brightness)

        # If no specific attribute, just turn on
        if not any(attr in kwargs for attr in [ATTR_RGB_COLOR, ATTR_COLOR_TEMP_KELVIN, ATTR_BRIGHTNESS, ATTR_EFFECT]):
            await self._async_turn_on_off(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the light off."""
        _LOGGER.debug("async_turn_off for %s", self._device.device_name)
        await self._async_turn_on_off(False)

    async def _async_turn_on_off(self, on: bool) -> None:
        """Turn the light on or off."""
        try:
            await self.coordinator.async_control_device(
                self._device_id,
                CAPABILITY_ON_OFF,
                INSTANCE_POWER_SWITCH,
                1 if on else 0,
            )
        except Exception as err:
            _LOGGER.error(
                "Failed to turn %s %s: %s",
                "on" if on else "off",
                self._device.device_name,
                err,
            )

    async def _async_set_brightness(self, brightness: int) -> None:
        """Set brightness (HA range 0-255)."""
        # Convert from HA range (0-255) to API range (0-100)
        api_brightness = round(brightness * 100 / 255)

        # Clamp to device range
        min_val, max_val = self._device.get_brightness_range()
        api_brightness = max(min_val, min(max_val, api_brightness))

        try:
            await self.coordinator.async_control_device(
                self._device_id,
                CAPABILITY_RANGE,
                INSTANCE_BRIGHTNESS,
                api_brightness,
            )
        except Exception as err:
            _LOGGER.error(
                "Failed to set brightness for %s: %s",
                self._device.device_name,
                err,
            )

    async def _async_set_color_rgb(self, rgb: tuple[int, int, int]) -> None:
        """Set RGB color."""
        r, g, b = rgb
        color_int = (r << 16) + (g << 8) + b

        try:
            await self.coordinator.async_control_device(
                self._device_id,
                CAPABILITY_COLOR_SETTING,
                INSTANCE_COLOR_RGB,
                color_int,
            )
        except Exception as err:
            _LOGGER.error(
                "Failed to set color for %s: %s",
                self._device.device_name,
                err,
            )

    async def _async_set_color_temp(self, temp_kelvin: int) -> None:
        """Set color temperature in Kelvin."""
        # Clamp to device range
        temp_kelvin = max(
            self._attr_min_color_temp_kelvin,
            min(self._attr_max_color_temp_kelvin, temp_kelvin),
        )

        try:
            await self.coordinator.async_control_device(
                self._device_id,
                CAPABILITY_COLOR_SETTING,
                INSTANCE_COLOR_TEMP,
                temp_kelvin,
            )
        except Exception as err:
            _LOGGER.error(
                "Failed to set color temperature for %s: %s",
                self._device.device_name,
                err,
            )

    async def _async_set_effect(self, effect_name: str) -> None:
        """Set an effect (scene)."""
        if effect_name not in self._effect_map:
            _LOGGER.warning(
                "Unknown effect '%s' for %s",
                effect_name,
                self._device.device_name,
            )
            return

        scene_option = self._effect_map[effect_name]
        scene_value = {
            "value": scene_option.get("value"),
            "name": effect_name,
        }

        try:
            await self.coordinator.async_control_device(
                self._device_id,
                CAPABILITY_DYNAMIC_SCENE,
                INSTANCE_LIGHT_SCENE,
                scene_value,
            )
        except Exception as err:
            _LOGGER.error(
                "Failed to set effect '%s' for %s: %s",
                effect_name,
                self._device.device_name,
                err,
            )

    # === Segment Control (for services) ===

    async def async_set_segment_color(
        self,
        segments: list[int],
        rgb: tuple[int, int, int],
    ) -> None:
        """Set color for specific segments.

        This method is called by the govee.set_segment_color service.
        """
        if not self._device.supports_segments:
            _LOGGER.warning(
                "Device %s does not support segment control",
                self._device.device_name,
            )
            return

        r, g, b = rgb
        color_int = (r << 16) + (g << 8) + b

        from .api.const import INSTANCE_SEGMENTED_COLOR

        value = {
            "segment": segments,
            "rgb": color_int,
        }

        try:
            await self.coordinator.async_control_device(
                self._device_id,
                CAPABILITY_SEGMENT_COLOR,
                INSTANCE_SEGMENTED_COLOR,
                value,
            )
        except Exception as err:
            _LOGGER.error(
                "Failed to set segment color for %s: %s",
                self._device.device_name,
                err,
            )

    async def async_set_segment_brightness(
        self,
        segments: list[int],
        brightness: int,
    ) -> None:
        """Set brightness for specific segments.

        This method is called by the govee.set_segment_brightness service.
        """
        if not self._device.supports_segments:
            _LOGGER.warning(
                "Device %s does not support segment control",
                self._device.device_name,
            )
            return

        from .api.const import INSTANCE_SEGMENTED_BRIGHTNESS

        value = {
            "segment": segments,
            "brightness": brightness,
        }

        try:
            await self.coordinator.async_control_device(
                self._device_id,
                CAPABILITY_SEGMENT_COLOR,
                INSTANCE_SEGMENTED_BRIGHTNESS,
                value,
            )
        except Exception as err:
            _LOGGER.error(
                "Failed to set segment brightness for %s: %s",
                self._device.device_name,
                err,
            )

    async def async_set_music_mode(
        self,
        mode: str,
        sensitivity: int = 50,
        auto_color: bool = True,
        rgb: tuple[int, int, int] | None = None,
    ) -> None:
        """Activate music reactive mode.

        This method is called by the govee.set_music_mode service.
        """
        if not self._device.supports_music_mode:
            _LOGGER.warning(
                "Device %s does not support music mode",
                self._device.device_name,
            )
            return

        from .api.const import CAPABILITY_MUSIC_SETTING, INSTANCE_MUSIC_MODE

        value: dict[str, Any] = {
            "musicMode": mode,
            "sensitivity": sensitivity,
            "autoColor": 1 if auto_color else 0,
        }

        if not auto_color and rgb:
            r, g, b = rgb
            value["color"] = (r << 16) + (g << 8) + b

        try:
            await self.coordinator.async_control_device(
                self._device_id,
                CAPABILITY_MUSIC_SETTING,
                INSTANCE_MUSIC_MODE,
                value,
            )
        except Exception as err:
            _LOGGER.error(
                "Failed to set music mode for %s: %s",
                self._device.device_name,
                err,
            )
