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

from ..api.const import (
    CAPABILITY_SEGMENT_COLOR,
    INSTANCE_SEGMENTED_BRIGHTNESS,
    INSTANCE_SEGMENTED_COLOR,
)
from ..const import CONF_INTER_COMMAND_DELAY, DEFAULT_INTER_COMMAND_DELAY
from ..coordinator import CommandBatch, GoveeDataUpdateCoordinator
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
        super().__init__(coordinator, device)

        self._segment_index = segment_index
        self._attr_unique_id = f"{device.device_id}_segment_{segment_index}"
        self.entity_description = SEGMENT_LIGHT_DESCRIPTION
        self._attr_translation_placeholders = {"segment_number": str(segment_index + 1)}

        self._optimistic_on: bool | None = None
        self._optimistic_brightness: int | None = None
        self._optimistic_rgb: tuple[int, int, int] | None = None

    async def async_added_to_hass(self) -> None:
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
        if self._optimistic_on is not None:
            return self._optimistic_on
        return None

    @property
    def brightness(self) -> int | None:
        return self._optimistic_brightness

    @property
    def rgb_color(self) -> tuple[int, int, int] | None:
        return self._optimistic_rgb

    @property
    def assumed_state(self) -> bool:
        return True

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn on the segment with batched command execution.

        Commands are batched and executed with delays to prevent flickering.
        Order: Color FIRST → Brightness LAST (validated best practice)
        """
        rgb_color: tuple[int, int, int] | None = kwargs.get(ATTR_RGB_COLOR)
        brightness: int | None = kwargs.get(ATTR_BRIGHTNESS)

        if rgb_color is not None:
            target_rgb = rgb_color
        elif self._optimistic_rgb is not None and self._optimistic_rgb != (0, 0, 0):
            target_rgb = self._optimistic_rgb
        else:
            target_rgb = (255, 255, 255)

        batch = CommandBatch(device_id=self._device.device_id)

        # VALIDATED ORDER: Color FIRST → Brightness LAST
        r, g, b = target_rgb
        batch.add(
            CAPABILITY_SEGMENT_COLOR,
            INSTANCE_SEGMENTED_COLOR,
            {"segment": [self._segment_index], "rgb": (r << 16) + (g << 8) + b},
        )

        if brightness is not None:
            brightness_percent = round((brightness / 255.0) * 100)
            batch.add(
                CAPABILITY_SEGMENT_COLOR,
                INSTANCE_SEGMENTED_BRIGHTNESS,
                {"segment": [self._segment_index], "brightness": brightness_percent},
            )
            self._optimistic_brightness = brightness

        # Update optimistic state before batch execution
        self._optimistic_on = True
        self._optimistic_rgb = target_rgb

        # Get configurable delay from options (default 500ms)
        delay_ms = self.coordinator.config_entry.options.get(
            CONF_INTER_COMMAND_DELAY, DEFAULT_INTER_COMMAND_DELAY
        )
        await self.coordinator.async_execute_batch(
            self._device.device_id, batch, delay_ms / 1000.0
        )

        if self.device_state is not None:
            self.device_state.apply_segment_update(self._segment_index, target_rgb)

        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.coordinator.async_set_segment_color(
            self._device.device_id,
            self._device.sku,
            self._segment_index,
            (0, 0, 0),
        )

        self._optimistic_on = False
        self._optimistic_rgb = (0, 0, 0)

        if self.device_state is not None:
            self.device_state.apply_segment_update(self._segment_index, (0, 0, 0))

        self.async_write_ha_state()

    def clear_segment_state(self) -> None:
        self._optimistic_on = None
        self._optimistic_brightness = None
        self._optimistic_rgb = None
        self.async_write_ha_state()
