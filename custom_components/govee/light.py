"""Govee light platform."""

from __future__ import annotations

import logging

from homeassistant.components.light import LightEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import GoveeConfigEntry
from .const import DEVICE_TYPE_LIGHT
from .entities import GoveeLightEntity
from .entities.segment import GoveeSegmentLight

_LOGGER = logging.getLogger(__name__)

# Maximum segment count before logging a warning
MAX_SEGMENTS_WARNING = 20


async def async_setup_entry(
    hass: HomeAssistant,
    entry: GoveeConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Govee lights from a config entry.

    Platform Setup:
    1. Retrieve coordinator and devices from runtime data
    2. Create light entities for devices that support lighting
    3. Create segment entities for RGBIC devices with segment control
    4. Register Govee-specific services (segment control, music mode)

    Entity Creation:
    - Light entities created for devices with DEVICE_TYPE_LIGHT
    - Also created for any device with on/off capability (smart plugs with lights)
    - Segment entities created for RGBIC devices (e.g., H6199, H6160)
    - Each entity gets full capability detection and color mode setup
    """
    coordinator = entry.runtime_data.coordinator
    devices = entry.runtime_data.devices

    entities: list[LightEntity] = []
    segment_count_total = 0

    for device in devices.values():
        # Only create light entities for light devices
        # Some smart plugs also have light control capabilities
        if device.device_type == DEVICE_TYPE_LIGHT or device.supports_on_off:
            entities.append(GoveeLightEntity(coordinator, device, entry))

            # Create segment entities for RGBIC devices
            if device.supports_segments:
                segment_count = device.get_segment_count()

                if segment_count > MAX_SEGMENTS_WARNING:
                    _LOGGER.warning(
                        "Device %s (%s) reports %d segments, which is unusually high. "
                        "Creating all segment entities anyway.",
                        device.device_name,
                        device.sku,
                        segment_count,
                    )

                for segment_index in range(segment_count):
                    entities.append(
                        GoveeSegmentLight(
                            coordinator,
                            device,
                            segment_index,
                        )
                    )

                segment_count_total += segment_count
                _LOGGER.debug(
                    "Created %d segment entities for device %s (%s)",
                    segment_count,
                    device.device_name,
                    device.sku,
                )

    _LOGGER.debug(
        "Adding %d light entities (%d main lights, %d segments)",
        len(entities),
        len(entities) - segment_count_total,
        segment_count_total,
    )
    async_add_entities(entities)

    # Register light platform services (segment control, music mode, etc.)
    from .services import async_setup_services

    await async_setup_services(hass)
