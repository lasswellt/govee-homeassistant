"""Fan platform for Govee integration.

Provides fan entities with support for:
- On/Off control
- Speed control (dynamic speed count from device capabilities)
- Oscillation
- Preset modes (Normal, Auto)
"""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.fan import (
    DIRECTION_FORWARD,
    DIRECTION_REVERSE,
    FanEntity,
    FanEntityFeature,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.util.percentage import (
    ordered_list_item_to_percentage,
    percentage_to_ordered_list_item,
)

from .coordinator import GoveeCoordinator
from .entity import GoveeEntity
from .models import (
    GoveeDevice,
    ModeCommand,
    OscillationCommand,
    PowerCommand,
    ToggleCommand,
    WorkModeCommand,
)
from .models.device import (
    INSTANCE_FAN_SPEED_MODE,
    INSTANCE_FAN_TOGGLE,
    INSTANCE_REVERSE_AIRFLOW,
)

_LOGGER = logging.getLogger(__name__)

PARALLEL_UPDATES = 0

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

        # Ceiling-fan-with-light combos (e.g. H1310) report as
        # devices.types.light, so they get a light entity from the light
        # platform AND a fan entity here for the integrated fan (issue #74).
        elif device.supports_ceiling_fan:
            _LOGGER.debug(
                "Creating ceiling fan entity for %s (%s): reverse=%s, speeds=%d",
                device.name,
                device.sku,
                device.supports_reverse_airflow,
                len(device.get_ceiling_fan_speed_options()),
            )
            entities.append(GoveeCeilingFanEntity(coordinator, device))

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

    def __init__(
        self,
        coordinator: GoveeCoordinator,
        device: GoveeDevice,
    ) -> None:
        """Initialize the fan entity."""
        super().__init__(coordinator, device)

        # Set name (uses has_entity_name = True)
        self._attr_name = None  # Use device name

        # Detect speed count from device capabilities
        gear_speeds = [
            opt
            for opt in device.get_fan_speed_options()
            if opt["work_mode"] == WORK_MODE_GEAR
        ]
        self._fan_speeds = (
            [opt["mode_value"] for opt in gear_speeds] if gear_speeds else [1, 2, 3]
        )
        self._attr_speed_count = len(self._fan_speeds)

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

        Maps mode_value to percentage using the device's speed list.
        Only applies when in gearMode (work_mode=1).
        """
        state = self.device_state
        if state is None:
            return None

        # Only return percentage when in manual gear mode
        if state.work_mode == WORK_MODE_GEAR and state.mode_value is not None:
            try:
                return ordered_list_item_to_percentage(
                    self._fan_speeds, state.mode_value
                )
            except ValueError:
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
        Other percentages map to the device's speed levels.
        """
        if percentage == 0:
            await self.async_turn_off()
            return

        mode_value = percentage_to_ordered_list_item(self._fan_speeds, percentage)

        _LOGGER.debug(
            "Setting fan speed: percentage=%d, mode_value=%d",
            percentage,
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


class GoveeCeilingFanEntity(GoveeEntity, FanEntity, RestoreEntity):
    """Fan entity for ceiling-fan-with-light combos (e.g. H1310).

    Controls the integrated fan via the ``fanToggle`` / ``fanSpeedMode`` /
    ``reverseAirflowToggle`` capabilities — separate from the device's light
    entity (the H1310 reports as devices.types.light). Govee's state poll
    does not return these fan values, so state is optimistic and restored
    across restarts via RestoreEntity (issue #74).
    """

    _attr_icon = "mdi:ceiling-fan-light"

    def __init__(
        self,
        coordinator: GoveeCoordinator,
        device: GoveeDevice,
    ) -> None:
        """Initialize the ceiling fan entity."""
        super().__init__(coordinator, device)

        # Distinct unique_id — the device_id alone backs the light entity.
        self._attr_unique_id = f"{device.device_id}_fan"
        self._attr_name = "Fan"

        # Speed values from fanSpeedMode options (e.g. [1, 2, 3, 4, 5, 6]).
        options = device.get_ceiling_fan_speed_options()
        self._speed_values: list[int] = (
            [int(o["value"]) for o in options if "value" in o] if options else [1, 2, 3]
        )
        self._attr_speed_count = len(self._speed_values)

        features = (
            FanEntityFeature.TURN_ON
            | FanEntityFeature.TURN_OFF
            | FanEntityFeature.SET_SPEED
        )
        if device.supports_reverse_airflow:
            features |= FanEntityFeature.DIRECTION
        self._attr_supported_features = features

        # Optimistic state — Govee does not report fan state on poll.
        self._is_on = False
        self._speed_value: int | None = None
        self._direction = DIRECTION_FORWARD

    async def async_added_to_hass(self) -> None:
        """Restore optimistic state on startup."""
        await super().async_added_to_hass()
        last_state = await self.async_get_last_state()
        if last_state is None:
            return
        self._is_on = last_state.state == "on"
        pct = last_state.attributes.get("percentage")
        if pct is not None:
            try:
                self._speed_value = percentage_to_ordered_list_item(
                    self._speed_values, int(pct)
                )
            except (ValueError, TypeError):
                self._speed_value = None
        direction = last_state.attributes.get("direction")
        if direction in (DIRECTION_FORWARD, DIRECTION_REVERSE):
            self._direction = direction

    @property
    def is_on(self) -> bool:
        """Return True if the fan is on (optimistic)."""
        return self._is_on

    @property
    def percentage(self) -> int | None:
        """Return current speed as a percentage (optimistic)."""
        if not self._is_on or self._speed_value is None:
            return 0 if not self._is_on else None
        try:
            return ordered_list_item_to_percentage(
                self._speed_values, self._speed_value
            )
        except ValueError:
            return None

    @property
    def current_direction(self) -> str | None:
        """Return the current airflow direction (optimistic)."""
        if not self._device.supports_reverse_airflow:
            return None
        return self._direction

    async def async_turn_on(
        self,
        percentage: int | None = None,
        preset_mode: str | None = None,
        **kwargs: Any,
    ) -> None:
        """Turn the fan on, optionally at a given speed."""
        success = await self.coordinator.async_control_device(
            self._device_id,
            ToggleCommand(toggle_instance=INSTANCE_FAN_TOGGLE, enabled=True),
        )
        if success:
            self._is_on = True
            self.async_write_ha_state()
        if percentage is not None:
            await self.async_set_percentage(percentage)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the fan off."""
        success = await self.coordinator.async_control_device(
            self._device_id,
            ToggleCommand(toggle_instance=INSTANCE_FAN_TOGGLE, enabled=False),
        )
        if success:
            self._is_on = False
            self.async_write_ha_state()

    async def async_set_percentage(self, percentage: int) -> None:
        """Set the fan speed from a percentage. 0% turns off."""
        if percentage == 0:
            await self.async_turn_off()
            return

        speed_value = percentage_to_ordered_list_item(self._speed_values, percentage)
        _LOGGER.debug(
            "Setting ceiling fan speed: percentage=%d, fanSpeedMode=%d",
            percentage,
            speed_value,
        )
        success = await self.coordinator.async_control_device(
            self._device_id,
            ModeCommand(mode_instance=INSTANCE_FAN_SPEED_MODE, value=speed_value),
        )
        if success:
            self._speed_value = speed_value
            # Setting a speed implies the fan is running.
            self._is_on = True
            self.async_write_ha_state()

    async def async_set_direction(self, direction: str) -> None:
        """Set the airflow direction (reverse airflow toggle)."""
        reverse = direction == DIRECTION_REVERSE
        _LOGGER.debug("Setting ceiling fan direction: %s", direction)
        success = await self.coordinator.async_control_device(
            self._device_id,
            ToggleCommand(toggle_instance=INSTANCE_REVERSE_AIRFLOW, enabled=reverse),
        )
        if success:
            self._direction = DIRECTION_REVERSE if reverse else DIRECTION_FORWARD
            self.async_write_ha_state()
