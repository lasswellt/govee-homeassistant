"""Button entities for Govee integration.

Provides action buttons for:
- Refreshing scene lists from API
- Identifying devices (flash/blink)
"""

from __future__ import annotations

import logging

from homeassistant.components.button import ButtonEntity

from ..coordinator import GoveeDataUpdateCoordinator
from ..entity_descriptions.button import (
    GoveeButtonEntityDescription,
    BUTTON_DESCRIPTIONS,
)
from ..models import GoveeDevice
from .base import GoveeEntity

_LOGGER = logging.getLogger(__name__)


class GoveeRefreshScenesButton(GoveeEntity, ButtonEntity):
    """Button to refresh scene lists from API.

    Triggers a reload of dynamic and DIY scenes for the device,
    useful when new scenes have been created in the Govee app.
    """

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: GoveeDataUpdateCoordinator,
        device: GoveeDevice,
        description: GoveeButtonEntityDescription,
    ) -> None:
        """Initialize refresh scenes button.

        Args:
            coordinator: The data update coordinator
            device: The Govee device
            description: Entity description
        """
        super().__init__(coordinator, device)
        self.entity_description = description
        self._attr_unique_id = f"{device.device_id}_{description.key}"

    async def async_press(self) -> None:
        """Handle button press - refresh scenes."""
        _LOGGER.debug(
            "Refreshing scenes for device %s (%s)",
            self._device.device_name,
            self._device.device_id,
        )
        try:
            # Trigger scene refresh through coordinator
            await self.coordinator.async_refresh_device_scenes(self._device.device_id)
        except Exception as err:
            _LOGGER.error(
                "Failed to refresh scenes for %s: %s",
                self._device.device_name,
                err,
            )
            raise


class GoveeIdentifyButton(GoveeEntity, ButtonEntity):
    """Button to identify a device by flashing it.

    Sends a brief flash command to help users identify which
    physical device corresponds to this entity.
    """

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: GoveeDataUpdateCoordinator,
        device: GoveeDevice,
        description: GoveeButtonEntityDescription,
    ) -> None:
        """Initialize identify button.

        Args:
            coordinator: The data update coordinator
            device: The Govee device
            description: Entity description
        """
        super().__init__(coordinator, device)
        self.entity_description = description
        self._attr_unique_id = f"{device.device_id}_{description.key}"

    async def async_press(self) -> None:
        """Handle button press - identify device."""
        _LOGGER.debug(
            "Identifying device %s (%s)",
            self._device.device_name,
            self._device.device_id,
        )
        try:
            # Flash the device on/off briefly
            await self.coordinator.async_identify_device(self._device.device_id)
        except Exception as err:
            _LOGGER.error(
                "Failed to identify device %s: %s",
                self._device.device_name,
                err,
            )
            raise
