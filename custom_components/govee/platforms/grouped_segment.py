"""Grouped segment light entity for RGBIC devices.

Controls all segments of an RGBIC LED strip as a single grouped light entity.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

# mypy --strict: HA's `light` module re-exports without __all__, so
# `--no-implicit-reexport` raises attr-defined for each member. The
# suppression is upstream-stub-bound, not a real type error here.
from homeassistant.components.light import (  # type: ignore[attr-defined]
    ATTR_BRIGHTNESS,
    ATTR_RGB_COLOR,
    ColorMode,
    LightEntity,
)
from homeassistant.helpers.restore_state import RestoreEntity

from ..const import SUFFIX_GROUPED_SEGMENT
from ..coordinator import GoveeCoordinator
from ..entity import GoveeEntity
from ..models import GoveeDevice, RGBColor, SegmentColorCommand

_LOGGER = logging.getLogger(__name__)

PARALLEL_UPDATES = 0


class GoveeGroupedSegmentEntity(GoveeEntity, LightEntity, RestoreEntity):
    """Govee grouped segment light entity.

    Controls all segments of an RGBIC LED strip as a single grouped light.
    Sends a command to all segments at once for synchronized control.

    API Limitation: Govee API returns empty strings for segment colors.
    We use purely optimistic/local state that persists via RestoreEntity.
    This entity intentionally does NOT subscribe to coordinator updates
    to prevent API responses from overwriting local state.
    """

    _attr_translation_key = "govee_grouped_segment"
    _attr_supported_color_modes = {ColorMode.RGB}
    _attr_color_mode = ColorMode.RGB

    def __init__(
        self,
        coordinator: GoveeCoordinator,
        device: GoveeDevice,
    ) -> None:
        """Initialize the grouped segment entity.

        Args:
            coordinator: Govee data coordinator.
            device: Device with segments to control.
        """
        super().__init__(coordinator, device)
        self._device = device

        # Segment indices for all segments (0 to segment_count-1)
        self._segment_indices = tuple(range(device.segment_count))

        # Unique ID for grouped segments
        self._attr_unique_id = f"{device.device_id}{SUFFIX_GROUPED_SEGMENT}"

        # Name for grouped segment control
        self._attr_name = "Segments"

        # Translation placeholders
        self._attr_translation_placeholders = {
            "device_name": device.name,
        }

        # Optimistic state (API doesn't return per-segment state)
        self._is_on = True
        self._brightness = 255
        self._rgb_color: tuple[int, int, int] = (255, 255, 255)

    @property
    def available(self) -> bool:
        """Return True if entity is available.

        Grouped segments don't depend on coordinator state updates.
        Just check the coordinator is healthy.
        """
        return self.coordinator.last_update_success

    @property
    def is_on(self) -> bool:
        """Return True if segments are on."""
        return self._is_on

    @property
    def brightness(self) -> int:
        """Return brightness (0-255)."""
        return self._brightness

    @property
    def rgb_color(self) -> tuple[int, int, int]:
        """Return RGB color."""
        return self._rgb_color

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the segments on with optional parameters."""
        # Update brightness if provided
        if ATTR_BRIGHTNESS in kwargs:
            self._brightness = kwargs[ATTR_BRIGHTNESS]

        # Update color if provided
        if ATTR_RGB_COLOR in kwargs:
            self._rgb_color = kwargs[ATTR_RGB_COLOR]

        # Create segment color command with all segment indices
        r, g, b = self._rgb_color
        color = RGBColor(r=r, g=g, b=b)

        command = SegmentColorCommand(
            segment_indices=self._segment_indices,
            color=color,
        )

        await self.coordinator.async_control_device(
            self._device_id,
            command,
        )

        self._is_on = True
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the segments off (set to black).

        Skips the API call if a power-off is already in flight or the device
        is already off — prevents race conditions in area-targeted turn_off
        that cause firmware glitches on RGBIC devices (issue #16).
        """
        # Yield to the event loop so that a concurrent PowerCommand (from the
        # main light entity in an area-targeted turn_off) gets a chance to set
        # the _pending_power_off flag before we check it.
        await asyncio.sleep(0)

        device_state = self.coordinator.get_state(self._device_id)
        device_already_off = device_state is not None and not device_state.power_state
        power_off_pending = self.coordinator.is_power_off_pending(self._device_id)

        if not device_already_off and not power_off_pending:
            command = SegmentColorCommand(
                segment_indices=self._segment_indices,
                color=RGBColor(r=0, g=0, b=0),
            )
            await self.coordinator.async_control_device(self._device_id, command)
        else:
            _LOGGER.debug(
                "Skipping grouped segments turn_off for %s (power_off_pending=%s, device_already_off=%s)",
                self._device_id,
                power_off_pending,
                device_already_off,
            )

        self._is_on = False

        # Record black even when the write was skipped above — see the same
        # block in segment.py for why (issue #131).
        for segment_index in self._segment_indices:
            self.coordinator.record_segment_color(
                self._device_id, segment_index, (0, 0, 0)
            )
        self.async_write_ha_state()

    async def async_added_to_hass(self) -> None:
        """Restore previous state."""
        await super().async_added_to_hass()

        last_state = await self.async_get_last_state()
        if last_state:
            self._is_on = last_state.state == "on"

            if last_state.attributes.get("brightness"):
                self._brightness = last_state.attributes["brightness"]

            if last_state.attributes.get("rgb_color"):
                self._rgb_color = tuple(last_state.attributes["rgb_color"])

        # Seed the coordinator's segment tracking from the restored state — see
        # the same block in segment.py for why (issue #131).
        rgb = self._rgb_color if self._is_on else (0, 0, 0)
        for segment_index in self._segment_indices:
            self.coordinator.record_segment_color(self._device_id, segment_index, rgb)
