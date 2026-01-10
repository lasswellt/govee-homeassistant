"""Segment light entities for RGBIC devices.

Each segment of an RGBIC LED strip is exposed as a separate light entity,
following the WLED pattern for segment control.
"""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.light import (  # type: ignore[attr-defined]
    ATTR_BRIGHTNESS,
    ATTR_RGB_COLOR,
    ColorMode,
    LightEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo  # type: ignore[attr-defined]
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from ..const import CONF_ENABLE_SEGMENTS, DEFAULT_ENABLE_SEGMENTS, DOMAIN
from ..coordinator import GoveeCoordinator
from ..models import GoveeDevice, RGBColor, SegmentColorCommand

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Govee segment lights from a config entry."""
    coordinator: GoveeCoordinator = entry.runtime_data

    # Check if segments are enabled
    if not entry.options.get(CONF_ENABLE_SEGMENTS, DEFAULT_ENABLE_SEGMENTS):
        _LOGGER.debug("Segment entities disabled")
        return

    entities: list[LightEntity] = []

    for device in coordinator.devices.values():
        if device.supports_segments and device.segment_count > 0:
            # Create entity for each segment
            for segment_index in range(device.segment_count):
                entities.append(
                    GoveeSegmentEntity(
                        coordinator=coordinator,
                        device=device,
                        segment_index=segment_index,
                    )
                )

    async_add_entities(entities)
    _LOGGER.debug("Set up %d Govee segment entities", len(entities))


class GoveeSegmentEntity(LightEntity, RestoreEntity):
    """Govee segment light entity.

    Represents a single segment of an RGBIC LED strip.

    API Limitation: Govee API returns empty strings for segment colors.
    We use purely optimistic/local state that persists via RestoreEntity.
    This entity intentionally does NOT subscribe to coordinator updates
    to prevent API responses from overwriting local state.
    """

    _attr_has_entity_name = True
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
        self._coordinator = coordinator
        self._device = device
        self._device_id = device.device_id
        self._segment_index = segment_index

        # Unique ID combines device and segment
        self._attr_unique_id = f"{device.device_id}_segment_{segment_index}"

        # Segment name with 1-based index for user display
        self._attr_name = f"Segment {segment_index + 1}"

        # Translation placeholders
        self._attr_translation_placeholders = {
            "device_name": device.name,
            "segment_index": str(segment_index + 1),
        }

        # Optimistic state (API doesn't return per-segment state)
        self._is_on = True
        self._brightness = 255
        self._rgb_color: tuple[int, int, int] = (255, 255, 255)

    @property
    def device_info(self) -> DeviceInfo:
        """Return device information."""
        return DeviceInfo(
            identifiers={(DOMAIN, self._device.device_id)},
            name=self._device.name,
            manufacturer="Govee",
            model=self._device.sku,
        )

    @property
    def available(self) -> bool:
        """Return True if entity is available."""
        # Check parent device availability
        state = self._coordinator.get_state(self._device_id)
        if state is None:
            return False
        return state.online or self._device.is_group

    @property
    def is_on(self) -> bool:
        """Return True if segment is on."""
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
        """Turn the segment on with optional parameters."""
        # Update brightness if provided
        if ATTR_BRIGHTNESS in kwargs:
            self._brightness = kwargs[ATTR_BRIGHTNESS]

        # Update color if provided
        if ATTR_RGB_COLOR in kwargs:
            self._rgb_color = kwargs[ATTR_RGB_COLOR]

        # Create segment color command
        r, g, b = self._rgb_color
        color = RGBColor(r=r, g=g, b=b)

        command = SegmentColorCommand(
            segment_indices=(self._segment_index,),
            color=color,
        )

        await self._coordinator.async_control_device(
            self._device_id,
            command,
        )

        self._is_on = True
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the segment off (set to black)."""
        # Set segment to black
        command = SegmentColorCommand(
            segment_indices=(self._segment_index,),
            color=RGBColor(r=0, g=0, b=0),
        )

        await self._coordinator.async_control_device(
            self._device_id,
            command,
        )

        self._is_on = False
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
