"""Govee switch entities for smart plugs and feature toggles."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.switch import SwitchDeviceClass, SwitchEntity

from ..api.const import (
    CAPABILITY_ON_OFF,
    CAPABILITY_TOGGLE,
    INSTANCE_NIGHTLIGHT_TOGGLE,
    INSTANCE_POWER_SWITCH,
)
from ..coordinator import GoveeDataUpdateCoordinator
from ..models import GoveeDevice
from .base import GoveeEntity

_LOGGER = logging.getLogger(__name__)


class GoveeSwitchEntity(GoveeEntity, SwitchEntity):
    """Representation of a Govee switch (smart plug/socket)."""

    def __init__(
        self,
        coordinator: GoveeDataUpdateCoordinator,
        device: GoveeDevice,
    ) -> None:
        """Initialize the switch entity.

        Args:
            coordinator: Data update coordinator
            device: Govee device
        """
        super().__init__(coordinator, device)
        
        self._attr_unique_id = f"{device.device_id}_switch"
        
        # Set entity description for outlet/plug device
        from ..entity_descriptions import SWITCH_DESCRIPTIONS
        
        self.entity_description = SWITCH_DESCRIPTIONS["outlet"]

    @property
    def is_on(self) -> bool | None:
        """Return true if switch is on."""
        state = self.device_state
        if state is None:
            return None
        return state.power_state

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the switch on."""
        await self.coordinator.async_set_power_state(self._device.device_id, True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the switch off."""
        await self.coordinator.async_set_power_state(self._device.device_id, False)


class GoveeNightLightSwitch(GoveeEntity, SwitchEntity):
    """Govee night light toggle switch entity.

    Provides a switch for controlling the nightlight mode on compatible
    Govee light devices. The nightlight mode is a dimmed, warm light mode.
    """

    _attr_device_class = SwitchDeviceClass.SWITCH
    _attr_translation_key = "nightlight"

    def __init__(
        self,
        coordinator: GoveeDataUpdateCoordinator,
        device: GoveeDevice,
    ) -> None:
        """Initialize the nightlight switch entity.

        Args:
            coordinator: Data update coordinator
            device: Govee device with nightlight support
        """
        super().__init__(coordinator, device)
        
        self._attr_unique_id = f"{device.device_id}_nightlight"
        
        # Set entity description for nightlight switch
        from ..entity_descriptions import SWITCH_DESCRIPTIONS
        
        self.entity_description = SWITCH_DESCRIPTIONS["nightlight"]

    @property
    def name(self) -> str:
        """Return the name of the entity."""
        return f"{self._device.device_name} Night Light"

    @property
    def is_on(self) -> bool | None:
        """Return true if nightlight is on."""
        state = self.device_state
        if state is None:
            return None
        return state.nightlight_on

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the nightlight on."""
        _LOGGER.debug("Turning on nightlight for %s", self._device.device_name)
        try:
            await self.coordinator.async_control_device(
                self._device_id,
                CAPABILITY_TOGGLE,
                INSTANCE_NIGHTLIGHT_TOGGLE,
                1,
            )
        except Exception as err:
            _LOGGER.error(
                "Failed to turn on nightlight for %s: %s",
                self._device.device_name,
                err,
            )

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the nightlight off."""
        _LOGGER.debug("Turning off nightlight for %s", self._device.device_name)
        try:
            await self.coordinator.async_control_device(
                self._device_id,
                CAPABILITY_TOGGLE,
                INSTANCE_NIGHTLIGHT_TOGGLE,
                0,
            )
        except Exception as err:
            _LOGGER.error(
                "Failed to turn off nightlight for %s: %s",
                self._device.device_name,
                err,
            )
