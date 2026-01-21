"""Fan platform for Govee integration.

Provides fan entities with support for:
- On/Off control
- Speed control (Low/Medium/High)
- Oscillation
- Preset modes (Normal, Auto)
"""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.fan import FanEntity, FanEntityFeature
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util.percentage import (
    ordered_list_item_to_percentage,
    percentage_to_ordered_list_item,
)

from .coordinator import GoveeCoordinator
from .entity import GoveeEntity
from .models import GoveeDevice, OscillationCommand, PowerCommand, WorkModeCommand

_LOGGER = logging.getLogger(__name__)

# Fan speed names mapped to mode_value
ORDERED_NAMED_FAN_SPEEDS = ["low", "medium", "high"]

# Preset modes: Normal uses gearMode (manual speed), Auto uses auto mode
PRESET_MODE_NORMAL = "Normal"
PRESET_MODE_AUTO = "Auto"
FAN_PRESET_MODES = [PRESET_MODE_NORMAL, PRESET_MODE_AUTO]

# Work mode constants
WORK_MODE_GEAR = 1  # Manual speed control
WORK_MODE_AUTO = 3  # Automatic mode


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Govee fans from a config entry."""
    coordinator: GoveeCoordinator = entry.runtime_data

    entities: list[FanEntity] = []

    for device in coordinator.devices.values():
        if device.is_fan:
            _LOGGER.debug(
                "Creating fan entity for %s (%s): oscillation=%s, work_mode=%s",
                device.name,
                device.sku,
                device.supports_oscillation,
                device.supports_work_mode,
            )
            entities.append(GoveeFanEntity(coordinator, device))

    async_add_entities(entities)
    _LOGGER.debug("Set up %d Govee fan entities", len(entities))


class GoveeFanEntity(GoveeEntity, FanEntity):
    """Govee fan entity.

    Supports:
    - On/Off
    - Speed (Low/Medium/High as percentage)
    - Oscillation
    - Preset modes (Normal, Auto)
    """

    _attr_translation_key = "govee_fan"
    _attr_speed_count = len(ORDERED_NAMED_FAN_SPEEDS)

    def __init__(
        self,
        coordinator: GoveeCoordinator,
        device: GoveeDevice,
    ) -> None:
        """Initialize the fan entity."""
        super().__init__(coordinator, device)

        # Set name (uses has_entity_name = True)
        self._attr_name = None  # Use device name

        # Build supported features based on device capabilities
        features = FanEntityFeature.TURN_ON | FanEntityFeature.TURN_OFF

        if device.supports_work_mode:
            features |= FanEntityFeature.SET_SPEED
            features |= FanEntityFeature.PRESET_MODE
            self._attr_preset_modes = FAN_PRESET_MODES

        if device.supports_oscillation:
            features |= FanEntityFeature.OSCILLATE

        self._attr_supported_features = features

    @property
    def is_on(self) -> bool | None:
        """Return True if fan is on."""
        state = self.device_state
        return state.power_state if state else None

    @property
    def percentage(self) -> int | None:
        """Return the current speed as a percentage.

        Maps mode_value 1/2/3 to Low/Medium/High.
        Only applies when in gearMode (work_mode=1).
        """
        state = self.device_state
        if state is None:
            return None

        # Only return percentage when in manual gear mode
        if state.work_mode == WORK_MODE_GEAR and state.mode_value is not None:
            try:
                speed_name = ORDERED_NAMED_FAN_SPEEDS[state.mode_value - 1]
                return ordered_list_item_to_percentage(ORDERED_NAMED_FAN_SPEEDS, speed_name)
            except (IndexError, ValueError):
                _LOGGER.debug("Unknown mode_value: %s", state.mode_value)

        return None

    @property
    def preset_mode(self) -> str | None:
        """Return the current preset mode.

        Maps work_mode to preset:
        - 1 (gearMode) -> Normal
        - 3 (Auto) -> Auto
        """
        state = self.device_state
        if state is None or state.work_mode is None:
            return None

        if state.work_mode == WORK_MODE_AUTO:
            return PRESET_MODE_AUTO
        return PRESET_MODE_NORMAL

    @property
    def oscillating(self) -> bool | None:
        """Return the oscillation state."""
        state = self.device_state
        return state.oscillating if state else None

    async def async_turn_on(
        self,
        percentage: int | None = None,
        preset_mode: str | None = None,
        **kwargs: Any,
    ) -> None:
        """Turn the fan on."""
        # Handle preset mode if provided
        if preset_mode is not None:
            await self.async_set_preset_mode(preset_mode)
        elif percentage is not None:
            await self.async_set_percentage(percentage)

        # Send power on command
        await self.coordinator.async_control_device(
            self._device_id,
            PowerCommand(power_on=True),
        )

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the fan off."""
        await self.coordinator.async_control_device(
            self._device_id,
            PowerCommand(power_on=False),
        )

    async def async_set_percentage(self, percentage: int) -> None:
        """Set the speed percentage.

        0% turns off the fan.
        Other percentages map to Low/Medium/High.
        """
        if percentage == 0:
            await self.async_turn_off()
            return

        # Convert percentage to speed name (low/medium/high)
        speed_name = percentage_to_ordered_list_item(ORDERED_NAMED_FAN_SPEEDS, percentage)
        # Convert to mode_value (1/2/3)
        mode_value = ORDERED_NAMED_FAN_SPEEDS.index(speed_name) + 1

        _LOGGER.debug(
            "Setting fan speed: percentage=%d, speed_name=%s, mode_value=%d",
            percentage,
            speed_name,
            mode_value,
        )

        await self.coordinator.async_control_device(
            self._device_id,
            WorkModeCommand(work_mode=WORK_MODE_GEAR, mode_value=mode_value),
        )

    async def async_set_preset_mode(self, preset_mode: str) -> None:
        """Set the preset mode."""
        if preset_mode == PRESET_MODE_AUTO:
            work_mode = WORK_MODE_AUTO
            mode_value = 0  # Not used in auto mode
        else:
            # Normal mode - use current speed or default to medium
            work_mode = WORK_MODE_GEAR
            state = self.device_state
            mode_value = state.mode_value if state and state.mode_value else 2

        _LOGGER.debug(
            "Setting preset mode: preset=%s, work_mode=%d, mode_value=%d",
            preset_mode,
            work_mode,
            mode_value,
        )

        await self.coordinator.async_control_device(
            self._device_id,
            WorkModeCommand(work_mode=work_mode, mode_value=mode_value),
        )

    async def async_oscillate(self, oscillating: bool) -> None:
        """Oscillate the fan."""
        _LOGGER.debug("Setting oscillation: %s", oscillating)

        await self.coordinator.async_control_device(
            self._device_id,
            OscillationCommand(oscillating=oscillating),
        )
