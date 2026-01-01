"""Govee segment light entities for RGBIC device control."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.light import (  # type: ignore[attr-defined]
    ATTR_BRIGHTNESS,
    ATTR_RGB_COLOR,
    ColorMode,
    LightEntity,
)
from homeassistant.helpers.restore_state import RestoreEntity

from ..coordinator import GoveeDataUpdateCoordinator
from ..entity_descriptions import SEGMENT_LIGHT_DESCRIPTION
from ..models import GoveeDevice
from .base import GoveeEntity

_LOGGER = logging.getLogger(__name__)


class GoveeSegmentLight(GoveeEntity, LightEntity, RestoreEntity):
    """Light entity for individual RGBIC device segment with optimistic state tracking."""

    _attr_color_mode = ColorMode.RGB
    _attr_supported_color_modes = {ColorMode.RGB}

    def __init__(
        self,
        coordinator: GoveeDataUpdateCoordinator,
        device: GoveeDevice,
        segment_index: int,
    ) -> None:
        """Initialize the segment light entity."""
        super().__init__(coordinator, device)

        self._segment_index = segment_index
        self._attr_unique_id = f"{device.device_id}_segment_{segment_index}"
        self.entity_description = SEGMENT_LIGHT_DESCRIPTION
        self._attr_translation_placeholders = {"segment_number": str(segment_index + 1)}

        self._optimistic_on: bool | None = None
        self._optimistic_brightness: int | None = None
        self._optimistic_rgb: tuple[int, int, int] | None = None

    async def async_added_to_hass(self) -> None:
        """Restore previous state when entity is added to Home Assistant."""
        await super().async_added_to_hass()

        if (last_state := await self.async_get_last_state()) is not None:
            self._optimistic_on = last_state.state == "on"

            if (brightness := last_state.attributes.get(ATTR_BRIGHTNESS)) is not None:
                self._optimistic_brightness = int(brightness)

            if (rgb := last_state.attributes.get(ATTR_RGB_COLOR)) is not None:
                rgb_list = list(rgb)
                self._optimistic_rgb = (rgb_list[0], rgb_list[1], rgb_list[2])

            _LOGGER.debug(
                "Restored segment %d state: on=%s, brightness=%s, rgb=%s",
                self._segment_index,
                self._optimistic_on,
                self._optimistic_brightness,
                self._optimistic_rgb,
            )

    @property
    def is_on(self) -> bool | None:
        """Return true if segment is on (has non-black color)."""
        if self._optimistic_on is not None:
            return self._optimistic_on
        # Default to unknown/off if no state tracked
        return None

    @property
    def brightness(self) -> int | None:
        """Return segment brightness (0-255 HA scale)."""
        return self._optimistic_brightness

    @property
    def rgb_color(self) -> tuple[int, int, int] | None:
        """Return segment RGB color."""
        return self._optimistic_rgb

    @property
    def assumed_state(self) -> bool:
        """Return True since segment state is always assumed (optimistic)."""
        return True

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the segment on with optional color/brightness."""
        rgb_color: tuple[int, int, int] | None = kwargs.get(ATTR_RGB_COLOR)
        brightness: int | None = kwargs.get(ATTR_BRIGHTNESS)

        # Determine target RGB color
        if rgb_color is not None:
            target_rgb = rgb_color
        elif self._optimistic_rgb is not None and self._optimistic_rgb != (0, 0, 0):
            # Use last known color if available and not black
            target_rgb = self._optimistic_rgb
        else:
            # Default to white when turning on without color
            target_rgb = (255, 255, 255)

        # Apply brightness scaling if specified
        if brightness is not None:
            # Scale RGB by brightness (brightness is 0-255)
            scale = brightness / 255.0
            target_rgb = (
                int(target_rgb[0] * scale),
                int(target_rgb[1] * scale),
                int(target_rgb[2] * scale),
            )

        # Send command to device
        await self.coordinator.async_set_segment_color(
            self._device.device_id,
            self._device.sku,
            self._segment_index,
            target_rgb,
        )

        # Update optimistic state
        self._optimistic_on = True
        self._optimistic_rgb = target_rgb
        if brightness is not None:
            self._optimistic_brightness = brightness

        # Also update device state tracking
        if self.device_state is not None:
            self.device_state.apply_segment_update(self._segment_index, target_rgb)

        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the segment off by setting it to black (RGB 0,0,0)."""
        # Set segment to black
        await self.coordinator.async_set_segment_color(
            self._device.device_id,
            self._device.sku,
            self._segment_index,
            (0, 0, 0),
        )

        # Update optimistic state
        self._optimistic_on = False
        self._optimistic_rgb = (0, 0, 0)

        # Also update device state tracking
        if self.device_state is not None:
            self.device_state.apply_segment_update(self._segment_index, (0, 0, 0))

        self.async_write_ha_state()

    def clear_segment_state(self) -> None:
        """Clear optimistic segment state when main light changes."""
        self._optimistic_on = None
        self._optimistic_brightness = None
        self._optimistic_rgb = None
        self.async_write_ha_state()
