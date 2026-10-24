"""Segment light entities for RGBIC devices.

Each segment of an RGBIC LED strip is exposed as a separate light entity,
following the WLED pattern for segment control.
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
from homeassistant.core import callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.restore_state import RestoreEntity

from ..brightness import ha_to_device_brightness
from ..const import SUFFIX_SEGMENT
from ..coordinator import GoveeCoordinator
from ..entity import GoveeEntity
from ..models import GoveeDevice, RGBColor, SegmentBrightnessCommand, SegmentColorCommand
from .grouped_segment import segments_optimistic_signal

_LOGGER = logging.getLogger(__name__)

PARALLEL_UPDATES = 0


class GoveeSegmentEntity(GoveeEntity, LightEntity, RestoreEntity):
    """Govee segment light entity.

    Represents a single segment of an RGBIC LED strip.

    API Limitation: Govee API returns empty strings for segment colors.
    We use purely optimistic/local state that persists via RestoreEntity.
    This entity intentionally does NOT subscribe to coordinator updates
    to prevent API responses from overwriting local state.
    """

    _attr_translation_key = "govee_segment"
    _attr_supported_color_modes = {ColorMode.RGB}
    _attr_color_mode = ColorMode.RGB

    def __init__(
        self,
        coordinator: GoveeCoordinator,
        device: GoveeDevice,
        segment_index: int,
    ) -> None:
        """Initialize the segment entity.

        Args:
            coordinator: Govee data coordinator.
            device: Device this segment belongs to.
            segment_index: Zero-based segment index.
        """
        super().__init__(coordinator, device)
        self._segment_index = segment_index

        # Unique ID combines device and segment
        self._attr_unique_id = f"{device.device_id}{SUFFIX_SEGMENT}{segment_index}"

        # Name comes from the ``govee_segment`` translation; the placeholder
        # carries the 1-based index users see on the strip.
        self._attr_translation_placeholders = {
            "segment_index": str(segment_index + 1),
        }

        # Optimistic state (API doesn't return per-segment state)
        self._is_on = True
        self._brightness = 255
        self._rgb_color: tuple[int, int, int] = (255, 255, 255)

        # Segment brightness is only real when the device advertises
        # segmentedBrightness; otherwise nothing could ever send it.
        self._brightness_supported = device.supports_segment_brightness
        self._brightness_range = device.segment_brightness_range

    @property
    def is_on(self) -> bool:
        """Return True if segment is on."""
        return self._is_on

    @property
    def brightness(self) -> int | None:
        """Return brightness (0-255), or None when the device cannot set it per segment."""
        return self._brightness if self._brightness_supported else None

    @property
    def rgb_color(self) -> tuple[int, int, int]:
        """Return RGB color."""
        return self._rgb_color

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the segment on with optional parameters.

        The optimistic values are only stored once the write is accepted, so
        a rejected command never leaves an unsent colour behind for the next
        state write or the restore cache.
        """
        send_brightness = self._brightness_supported and ATTR_BRIGHTNESS in kwargs
        brightness = kwargs[ATTR_BRIGHTNESS] if send_brightness else self._brightness
        rgb_color = tuple(kwargs.get(ATTR_RGB_COLOR, self._rgb_color))

        r, g, b = rgb_color
        color = RGBColor(r=r, g=g, b=b)
        indices = (self._segment_index,)

        # A lit segment changing only its brightness needs no colour write; an
        # off segment is black and has to be relit with its colour first.
        if ATTR_RGB_COLOR in kwargs or not send_brightness or not self._is_on:
            await self._async_send_command(SegmentColorCommand(segment_indices=indices, color=color))
            self._rgb_color = (r, g, b)
            self._is_on = True
        if send_brightness:
            device_brightness = ha_to_device_brightness(self._brightness_range, brightness)
            await self._async_send_command(
                SegmentBrightnessCommand(segment_indices=indices, brightness=device_brightness)
            )
            self._brightness = brightness

        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the segment off (set to black).

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
            await self._async_send_command(
                SegmentColorCommand(
                    segment_indices=(self._segment_index,),
                    color=RGBColor(r=0, g=0, b=0),
                )
            )
        else:
            _LOGGER.debug(
                "Skipping segment %d turn_off for %s (power_off_pending=%s, device_already_off=%s)",
                self._segment_index,
                self._device_id,
                power_off_pending,
                device_already_off,
            )

        self._is_on = False

        # Record black even when the write was skipped above. The segment is
        # off either way — the skip only means something else is already
        # taking the device dark — and leaving the previous colour in the
        # coordinator's tracking would make a later whole-device write replay
        # it, relighting a ring the user had switched off (issue #131).
        self.coordinator.record_segment_color(self._device_id, self._segment_index, (0, 0, 0))
        self.async_write_ha_state()

    async def async_added_to_hass(self) -> None:
        """Restore previous state, then listen for grouped-entity writes.

        SEGMENT_MODE_BOTH runs this entity alongside GoveeGroupedSegmentEntity
        for the same segment. Govee never reports real per-segment state, so
        without this a `light.turn_off` on the grouped "all segments" entity
        would leave this entity still optimistically showing "on".
        """
        await super().async_added_to_hass()

        last_state = await self.async_get_last_state()
        if last_state:
            self._is_on = last_state.state == "on"

            if last_state.attributes.get("brightness"):
                self._brightness = last_state.attributes["brightness"]

            if last_state.attributes.get("rgb_color"):
                self._rgb_color = tuple(last_state.attributes["rgb_color"])

        # Seed the coordinator's segment tracking from the restored state. On
        # fixtures where a whole-device write clobbers the segment overlay, the
        # coordinator replays these colours afterwards; that tracking is
        # in-memory, so without this the first such write after a restart would
        # have nothing to replay and would leave the ring wiped (issue #131).
        # An off segment is black, matching what async_turn_off actually sends.
        self.coordinator.record_segment_color(
            self._device_id,
            self._segment_index,
            self._rgb_color if self._is_on else (0, 0, 0),
        )

        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                segments_optimistic_signal(self._device_id),
                self._handle_group_update,
            )
        )

    @callback
    def _handle_group_update(
        self,
        is_on: bool,
        brightness: int,
        rgb_color: tuple[int, int, int],
    ) -> None:
        """Mirror a write made through the grouped "all segments" entity.

        The coordinator's own segment tracking (#131) is fed by the command
        path rather than from here — the grouped entity dispatches a real
        SegmentColorCommand, so async_control_device records those colours
        whichever entity issued them. This handler only keeps the individual
        entity's optimistic view in step with the group's.
        """
        self._is_on = is_on
        self._brightness = brightness
        self._rgb_color = rgb_color
        self.async_write_ha_state()
