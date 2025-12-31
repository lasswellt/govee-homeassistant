"""Button platform for Govee integration.

Provides action buttons for:
- Refreshing scene lists from API
- Identifying devices (flash/blink)
"""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DEVICE_TYPE_LIGHT
from .entities.button import GoveeIdentifyButton, GoveeRefreshScenesButton
from .entity_descriptions.button import BUTTON_DESCRIPTIONS
from .models import GoveeRuntimeData

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry[GoveeRuntimeData],
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Govee button entities from config entry.

    Creates action buttons for each supported device:
    - Refresh scenes button for light devices
    - Identify button for all devices

    Args:
        hass: Home Assistant instance
        entry: Config entry with runtime data
        async_add_entities: Callback to add entities
    """
    coordinator = entry.runtime_data.coordinator
    devices = entry.runtime_data.devices

    entities: list[GoveeRefreshScenesButton | GoveeIdentifyButton] = []

    for device in devices.values():
        # Identify button for all devices
        entities.append(
            GoveeIdentifyButton(
                coordinator,
                device,
                BUTTON_DESCRIPTIONS["identify"],
            )
        )

        # Refresh scenes button only for light devices
        if device.device_type == DEVICE_TYPE_LIGHT:
            entities.append(
                GoveeRefreshScenesButton(
                    coordinator,
                    device,
                    BUTTON_DESCRIPTIONS["refresh_scenes"],
                )
            )

    _LOGGER.debug("Setting up %d Govee button entities", len(entities))
    async_add_entities(entities)
