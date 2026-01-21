"""Light platform for Govee integration.

Provides light entities with support for:
- On/Off control
- Brightness control
- RGB color
- Color temperature
"""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.light import (  # type: ignore[attr-defined]
    ATTR_BRIGHTNESS,
    ATTR_COLOR_TEMP_KELVIN,
    ATTR_RGB_COLOR,
    ColorMode,
    LightEntity,
    LightEntityFeature,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from .const import CONF_ENABLE_SEGMENTS, DEFAULT_ENABLE_SEGMENTS
from .coordinator import GoveeCoordinator
from .entity import GoveeEntity
from .models import (
    BrightnessCommand,
    ColorCommand,
    ColorTempCommand,
    GoveeDevice,
    PowerCommand,
    RGBColor,
)
from .platforms.segment import GoveeSegmentEntity

_LOGGER = logging.getLogger(__name__)

# Home Assistant brightness range
HA_BRIGHTNESS_MAX = 255


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Govee lights from a config entry."""
    coordinator: GoveeCoordinator = entry.runtime_data

    entities: list[LightEntity] = []

    # Check if segments are enabled
    enable_segments = entry.options.get(CONF_ENABLE_SEGMENTS, DEFAULT_ENABLE_SEGMENTS)

    for device in coordinator.devices.values():
        # Only create light entities for devices with power control (not fans)
        if device.supports_power and not device.is_fan:
            entities.append(GoveeLightEntity(coordinator, device))

        # Create segment entities for RGBIC devices
        _LOGGER.debug(
            "Segment check for %s: enable_segments=%s, supports_segments=%s, segment_count=%d",
            device.name,
            enable_segments,
            device.supports_segments,
            device.segment_count,
        )
        if enable_segments and device.supports_segments and device.segment_count > 0:
            _LOGGER.debug(
                "Creating %d segment entities for %s",
                device.segment_count,
                device.name,
            )
            for segment_index in range(device.segment_count):
                entities.append(
                    GoveeSegmentEntity(
                        coordinator=coordinator,
                        device=device,
                        segment_index=segment_index,
                    )
                )

    async_add_entities(entities)
    _LOGGER.debug("Set up %d Govee light entities", len(entities))


class GoveeLightEntity(GoveeEntity, LightEntity, RestoreEntity):
    """Govee light entity.

    Supports:
    - On/Off
    - Brightness (scaled to device range)
    - RGB color
    - Color temperature
    - State restoration for group devices
    """

    _attr_translation_key = "govee_light"

    def __init__(
        self,
        coordinator: GoveeCoordinator,
        device: GoveeDevice,
    ) -> None:
        """Initialize the light entity."""
        super().__init__(coordinator, device)

        # Set name (uses has_entity_name = True)
        self._attr_name = None  # Use device name

        # Determine supported color modes
        self._attr_supported_color_modes = self._determine_color_modes()
        self._attr_color_mode = self._get_current_color_mode()

        # Get device brightness range
        self._brightness_min, self._brightness_max = device.brightness_range

        # Add effect support if device has scenes
        if device.supports_scenes:
            self._attr_supported_features = LightEntityFeature.EFFECT

    def _determine_color_modes(self) -> set[ColorMode]:
        """Determine supported color modes from device capabilities."""
        modes: set[ColorMode] = set()

        if self._device.supports_rgb:
            modes.add(ColorMode.RGB)

        if self._device.supports_color_temp:
            modes.add(ColorMode.COLOR_TEMP)

        if not modes and self._device.supports_brightness:
            modes.add(ColorMode.BRIGHTNESS)

        if not modes:
            modes.add(ColorMode.ONOFF)

        return modes

    def _get_current_color_mode(self) -> ColorMode:
        """Get current color mode based on state."""
        state = self.device_state
        modes = self._attr_supported_color_modes or set()

        if state and state.color_temp_kelvin is not None:
            if ColorMode.COLOR_TEMP in modes:
                return ColorMode.COLOR_TEMP

        if state and state.color is not None:
            if ColorMode.RGB in modes:
                return ColorMode.RGB

        if ColorMode.BRIGHTNESS in modes:
            return ColorMode.BRIGHTNESS

        return ColorMode.ONOFF

    @property
    def is_on(self) -> bool | None:
        """Return True if light is on."""
        state = self.device_state
        return state.power_state if state else None

    @property
    def brightness(self) -> int | None:
        """Return brightness (0-255)."""
        state = self.device_state
        if state is None:
            return None

        # Convert device brightness to HA scale
        return self._device_to_ha_brightness(state.brightness)

    @property
    def rgb_color(self) -> tuple[int, int, int] | None:
        """Return RGB color as (r, g, b) tuple."""
        state = self.device_state
        if state and state.color:
            return state.color.as_tuple
        return None

    @property
    def color_temp_kelvin(self) -> int | None:
        """Return color temperature in Kelvin."""
        state = self.device_state
        return state.color_temp_kelvin if state else None

    @property
    def min_color_temp_kelvin(self) -> int:
        """Return minimum color temperature in Kelvin."""
        temp_range = self._device.color_temp_range
        return temp_range.min_kelvin if temp_range else 2000

    @property
    def max_color_temp_kelvin(self) -> int:
        """Return maximum color temperature in Kelvin."""
        temp_range = self._device.color_temp_range
        return temp_range.max_kelvin if temp_range else 9000

    def _ha_to_device_brightness(self, ha_brightness: int) -> int:
        """Convert HA brightness (0-255) to device range."""
        return int(ha_brightness / HA_BRIGHTNESS_MAX * self._brightness_max)

    def _device_to_ha_brightness(self, device_brightness: int) -> int:
        """Convert device brightness to HA range (0-255)."""
        return int(device_brightness / self._brightness_max * HA_BRIGHTNESS_MAX)

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the light on with optional parameters."""
        # Handle brightness
        if ATTR_BRIGHTNESS in kwargs:
            ha_brightness = kwargs[ATTR_BRIGHTNESS]
            device_brightness = self._ha_to_device_brightness(ha_brightness)
            await self.coordinator.async_control_device(
                self._device_id,
                BrightnessCommand(brightness=device_brightness),
            )

        # Handle RGB color
        if ATTR_RGB_COLOR in kwargs:
            r, g, b = kwargs[ATTR_RGB_COLOR]
            color = RGBColor(r=r, g=g, b=b)
            await self.coordinator.async_control_device(
                self._device_id,
                ColorCommand(color=color),
            )
            self._attr_color_mode = ColorMode.RGB

        # Handle color temperature
        if ATTR_COLOR_TEMP_KELVIN in kwargs:
            kelvin = kwargs[ATTR_COLOR_TEMP_KELVIN]
            await self.coordinator.async_control_device(
                self._device_id,
                ColorTempCommand(kelvin=kelvin),
            )
            self._attr_color_mode = ColorMode.COLOR_TEMP

        # Always send power on
        await self.coordinator.async_control_device(
            self._device_id,
            PowerCommand(power_on=True),
        )

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the light off."""
        await self.coordinator.async_control_device(
            self._device_id,
            PowerCommand(power_on=False),
        )

    async def async_added_to_hass(self) -> None:
        """Restore state for group devices."""
        await super().async_added_to_hass()

        if self._device.is_group:
            last_state = await self.async_get_last_state()
            if last_state:
                # Restore power state
                state = self.device_state
                if state:
                    state.power_state = last_state.state == "on"

                    # Restore brightness
                    if last_state.attributes.get("brightness"):
                        device_brightness = self._ha_to_device_brightness(
                            last_state.attributes["brightness"]
                        )
                        state.brightness = device_brightness
