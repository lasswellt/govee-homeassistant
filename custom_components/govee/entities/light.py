"""Govee light entity with v2.0 API features."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.light import (  # type: ignore[attr-defined]
    ATTR_BRIGHTNESS,
    ATTR_COLOR_TEMP_KELVIN,
    ATTR_EFFECT,
    ATTR_RGB_COLOR,
    ColorMode,
    LightEntity,
    LightEntityFeature,
)
from homeassistant.helpers.restore_state import RestoreEntity

from ..api.const import (
    CAPABILITY_COLOR_SETTING,
    CAPABILITY_DYNAMIC_SCENE,
    CAPABILITY_MUSIC_SETTING,
    CAPABILITY_ON_OFF,
    CAPABILITY_RANGE,
    CAPABILITY_SEGMENT_COLOR,
    INSTANCE_BRIGHTNESS,
    INSTANCE_COLOR_RGB,
    INSTANCE_COLOR_TEMP,
    INSTANCE_LIGHT_SCENE,
    INSTANCE_MUSIC_MODE,
    INSTANCE_POWER_SWITCH,
    INSTANCE_SEGMENTED_BRIGHTNESS,
    INSTANCE_SEGMENTED_COLOR,
)
from ..api.exceptions import GoveeApiError
from ..const import (
    API_BRIGHTNESS_MAX,
    CONF_INTER_COMMAND_DELAY,
    CONF_OFFLINE_IS_OFF,
    CONF_USE_ASSUMED_STATE,
    DEFAULT_INTER_COMMAND_DELAY,
    HA_BRIGHTNESS_MAX,
)
from ..coordinator import CommandBatch, GoveeDataUpdateCoordinator
from ..entity_descriptions import LIGHT_DESCRIPTIONS
from ..models import GoveeDevice
from ..models.config import GoveeConfigEntry
from .base import GoveeEntity

_LOGGER = logging.getLogger(__name__)


class GoveeLightEntity(GoveeEntity, LightEntity, RestoreEntity):
    """Govee light entity with full color control, scenes, and state restoration."""

    _attr_name = None  # Use device name from DeviceInfo

    def __init__(
        self,
        coordinator: GoveeDataUpdateCoordinator,
        device: GoveeDevice,
        entry: GoveeConfigEntry,
    ) -> None:
        super().__init__(coordinator, device)

        self._entry = entry
        self._attr_unique_id = f"govee_{entry.title}_{device.device_id}"

        self.entity_description = LIGHT_DESCRIPTIONS["main"]

        self._attr_supported_color_modes = self._determine_color_modes()
        self._attr_supported_features = self._determine_features()

        self._effect_map: dict[str, Any] = {}
        self._build_effect_list()

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

        if last_state.state in ("on", "off"):
            restored_power = last_state.state == "on"
            if state:
                state.power_state = restored_power
                state.online = False

                if last_state.attributes.get(ATTR_BRIGHTNESS):
                    brightness = last_state.attributes[ATTR_BRIGHTNESS]
                    state.brightness = round(
                        brightness * API_BRIGHTNESS_MAX / HA_BRIGHTNESS_MAX
                    )

                if last_state.attributes.get(ATTR_RGB_COLOR):
                    state.color_rgb = tuple(last_state.attributes[ATTR_RGB_COLOR])

                if last_state.attributes.get(ATTR_COLOR_TEMP_KELVIN):
                    state.color_temp_kelvin = last_state.attributes[
                        ATTR_COLOR_TEMP_KELVIN
                    ]

                _LOGGER.info(
                    "Restored state for group device %s: power=%s, brightness=%s",
                    self._device.device_name,
                    restored_power,
                    state.brightness,
                )
                self.async_write_ha_state()

    def _determine_color_modes(self) -> set[ColorMode]:
        modes: set[ColorMode] = set()

        if self._device.supports_color:
            modes.add(ColorMode.RGB)
        if self._device.supports_color_temp:
            modes.add(ColorMode.COLOR_TEMP)

        if not modes:
            if self._device.supports_brightness:
                modes.add(ColorMode.BRIGHTNESS)
            else:
                modes.add(ColorMode.ONOFF)

        return modes

    def _determine_features(self) -> LightEntityFeature:
        features = LightEntityFeature(0)

        if self._device.supports_scenes:
            features |= LightEntityFeature.EFFECT

        return features

    def _build_effect_list(self) -> None:
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
                effects[:5],
            )

    @property
    def is_on(self) -> bool | None:
        state = self.device_state
        if state is None:
            return None

        if not state.online:
            if self._entry.options.get(CONF_OFFLINE_IS_OFF, False):
                return False
            return None

        return state.power_state

    @property
    def brightness(self) -> int | None:
        state = self.device_state
        if state is None or state.brightness is None:
            return None

        return round(state.brightness * HA_BRIGHTNESS_MAX / API_BRIGHTNESS_MAX)

    @property
    def rgb_color(self) -> tuple[int, int, int] | None:
        state = self.device_state
        if state is None:
            return None
        return state.color_rgb

    @property
    def color_temp_kelvin(self) -> int | None:
        state = self.device_state
        if state is None:
            return None
        return state.color_temp_kelvin

    @property
    def color_mode(self) -> ColorMode | None:
        state = self.device_state
        if state is None:
            return None

        supported = self._attr_supported_color_modes
        if supported is None:
            return ColorMode.ONOFF

        if (
            state.color_temp_kelvin is not None
            and ColorMode.COLOR_TEMP in supported
        ):
            return ColorMode.COLOR_TEMP
        if (
            state.color_rgb is not None
            and ColorMode.RGB in supported
        ):
            return ColorMode.RGB
        if ColorMode.BRIGHTNESS in supported:
            return ColorMode.BRIGHTNESS
        return ColorMode.ONOFF

    @property
    def effect(self) -> str | None:
        state = self.device_state
        if state is None:
            return None
        return state.current_scene_name

    @property
    def assumed_state(self) -> bool:
        """Return true if we're using assumed state.

        This shows two buttons (on/off) in the UI instead of a toggle.
        """
        result: bool = self._entry.options.get(CONF_USE_ASSUMED_STATE, True)
        return result

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn on the light with batched command execution.

        Commands are batched and executed with delays to prevent flickering.
        Order: Color/ColorTemp FIRST → Brightness LAST (validated best practice)
        """
        _LOGGER.debug(
            "async_turn_on for %s, kwargs: %s",
            self._device.device_name,
            kwargs,
        )

        # Effects are mutually exclusive with other attributes - bypass batching
        if ATTR_EFFECT in kwargs:
            await self._async_set_effect(kwargs[ATTR_EFFECT])
            return

        batch = CommandBatch(device_id=self._device_id)

        # VALIDATED ORDER: Color/ColorTemp FIRST → Brightness LAST
        # Reason: Brightness before color causes revert issues (govee2mqtt #258)
        # No explicit power-on needed - Govee auto-turns-on with color/brightness

        if ATTR_RGB_COLOR in kwargs:
            r, g, b = kwargs[ATTR_RGB_COLOR]
            color_int = (r << 16) + (g << 8) + b
            batch.add(CAPABILITY_COLOR_SETTING, INSTANCE_COLOR_RGB, color_int)

        if ATTR_COLOR_TEMP_KELVIN in kwargs:
            temp_kelvin = kwargs[ATTR_COLOR_TEMP_KELVIN]
            min_kelvin = self._attr_min_color_temp_kelvin or 2000
            max_kelvin = self._attr_max_color_temp_kelvin or 9000
            temp_kelvin = max(min_kelvin, min(max_kelvin, temp_kelvin))
            batch.add(CAPABILITY_COLOR_SETTING, INSTANCE_COLOR_TEMP, temp_kelvin)

        if ATTR_BRIGHTNESS in kwargs:
            api_brightness = round(
                kwargs[ATTR_BRIGHTNESS] * API_BRIGHTNESS_MAX / HA_BRIGHTNESS_MAX
            )
            min_val, max_val = self._device.get_brightness_range()
            api_brightness = max(min_val, min(max_val, api_brightness))
            batch.add(CAPABILITY_RANGE, INSTANCE_BRIGHTNESS, api_brightness)

        # If we have batched commands, execute them together
        if batch.commands:
            # Get configurable delay from options (default 500ms)
            delay_ms = self._entry.options.get(
                CONF_INTER_COMMAND_DELAY, DEFAULT_INTER_COMMAND_DELAY
            )
            await self.coordinator.async_execute_batch(
                self._device_id, batch, delay_ms / 1000.0
            )
            return

        # No color/brightness attributes - just turn on
        await self._async_turn_on_off(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        _LOGGER.debug("async_turn_off for %s", self._device.device_name)
        await self._async_turn_on_off(False)

    async def _async_control(
        self, capability: str, instance: str, value: Any, operation: str
    ) -> None:
        try:
            await self.coordinator.async_control_device(
                self._device_id, capability, instance, value
            )
        except GoveeApiError as err:
            _LOGGER.error(
                "Failed to %s for %s: %s", operation, self._device.device_name, err
            )

    async def _async_turn_on_off(self, on: bool) -> None:
        await self._async_control(
            CAPABILITY_ON_OFF,
            INSTANCE_POWER_SWITCH,
            1 if on else 0,
            f"turn {'on' if on else 'off'}",
        )

    async def _async_set_brightness(self, brightness: int) -> None:
        api_brightness = round(brightness * API_BRIGHTNESS_MAX / HA_BRIGHTNESS_MAX)
        min_val, max_val = self._device.get_brightness_range()
        api_brightness = max(min_val, min(max_val, api_brightness))
        await self._async_control(
            CAPABILITY_RANGE, INSTANCE_BRIGHTNESS, api_brightness, "set brightness"
        )

    async def _async_set_color_rgb(self, rgb: tuple[int, int, int]) -> None:
        r, g, b = rgb
        color_int = (r << 16) + (g << 8) + b
        await self._async_control(
            CAPABILITY_COLOR_SETTING, INSTANCE_COLOR_RGB, color_int, "set color"
        )

    async def _async_set_color_temp(self, temp_kelvin: int) -> None:
        min_kelvin = self._attr_min_color_temp_kelvin or 2000
        max_kelvin = self._attr_max_color_temp_kelvin or 9000
        temp_kelvin = max(min_kelvin, min(max_kelvin, temp_kelvin))
        await self._async_control(
            CAPABILITY_COLOR_SETTING,
            INSTANCE_COLOR_TEMP,
            temp_kelvin,
            "set color temperature",
        )

    async def _async_set_effect(self, effect_name: str) -> None:
        if effect_name not in self._effect_map:
            _LOGGER.warning(
                "Unknown effect '%s' for %s", effect_name, self._device.device_name
            )
            return

        scene_value = self._effect_map[effect_name].get("value")
        await self._async_control(
            CAPABILITY_DYNAMIC_SCENE,
            INSTANCE_LIGHT_SCENE,
            scene_value,
            f"set effect '{effect_name}'",
        )

    async def async_set_segment_color(
        self, segments: list[int], rgb: tuple[int, int, int]
    ) -> None:
        if not self._device.supports_segments:
            _LOGGER.warning(
                "Device %s does not support segment control", self._device.device_name
            )
            return

        r, g, b = rgb
        value = {"segment": segments, "rgb": (r << 16) + (g << 8) + b}
        await self._async_control(
            CAPABILITY_SEGMENT_COLOR, INSTANCE_SEGMENTED_COLOR, value, "set segment color"
        )

    async def async_set_segment_brightness(
        self, segments: list[int], brightness: int
    ) -> None:
        if not self._device.supports_segments:
            _LOGGER.warning(
                "Device %s does not support segment control", self._device.device_name
            )
            return

        value = {"segment": segments, "brightness": brightness}
        await self._async_control(
            CAPABILITY_SEGMENT_COLOR,
            INSTANCE_SEGMENTED_BRIGHTNESS,
            value,
            "set segment brightness",
        )

    async def async_set_music_mode(
        self,
        mode: str,
        sensitivity: int = 50,
        auto_color: bool = True,
        rgb: tuple[int, int, int] | None = None,
    ) -> None:
        if not self._device.supports_music_mode:
            _LOGGER.warning(
                "Device %s does not support music mode", self._device.device_name
            )
            return

        value: dict[str, Any] = {
            "musicMode": mode,
            "sensitivity": sensitivity,
            "autoColor": 1 if auto_color else 0,
        }
        if not auto_color and rgb:
            r, g, b = rgb
            value["color"] = (r << 16) + (g << 8) + b

        await self._async_control(
            CAPABILITY_MUSIC_SETTING, INSTANCE_MUSIC_MODE, value, "set music mode"
        )
