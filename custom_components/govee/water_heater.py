"""Water heater platform for the Govee H7175 kettle.

One ``water_heater`` entity per H7175, with the target temperature, the brew
modes and power. Other kettles (H717A, H7170) keep their power switch and
temperature sensor only.

Units. The entity works in the unit the kettle reports (see
:meth:`KettleManager.reports_fahrenheit`) and Home Assistant converts for
display. The capability's 40-100 range is °C and is converted for a kettle
that reports °F.

Modes (see :mod:`.kettle.modes`). Following Home Assistant's convention a
powered-off kettle reports ``off``, choosing another mode while it is off
switches it on in that mode (the mode is sent first, so it never heats in the
previous one), and ``off`` powers it off. Mode states are stable keys
(``custom_1``, ``green_tea``, ``manual``, ...) translated for display.
"""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.water_heater import (
    ATTR_OPERATION_MODE,
    WaterHeaterEntity,
    WaterHeaterEntityFeature,
)
from homeassistant.const import ATTR_TEMPERATURE, PRECISION_WHOLE, STATE_OFF, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, SUFFIX_KETTLE
from .coordinator import GoveeConfigEntry, GoveeCoordinator
from .kettle.entities import KettleModeMixin
from .kettle.modes import KETTLE_MANUAL_MODE, kettle_modes, manual_command, selected_mode, to_kettle_unit
from .models import GoveeDevice, PowerCommand, WorkModeCommand

_LOGGER = logging.getLogger(__name__)

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: GoveeConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the H7175 kettle water heaters."""
    coordinator: GoveeCoordinator = entry.runtime_data
    async_add_entities(
        GoveeKettleWaterHeater(coordinator, device)
        for device in coordinator.devices.values()
        if device.decodes_kettle_frames and not device.is_group
    )


class GoveeKettleWaterHeater(KettleModeMixin, WaterHeaterEntity):
    """H7175 kettle as a water heater: target, brew modes, power."""

    _attr_translation_key = "kettle"
    _attr_name = None  # the main feature: the device name
    _attr_precision = PRECISION_WHOLE
    _attr_target_temperature_step = 1

    def __init__(self, coordinator: GoveeCoordinator, device: GoveeDevice) -> None:
        """Initialize the kettle entity."""
        super().__init__(coordinator, device)
        self._attr_unique_id = f"{device.device_id}{SUFFIX_KETTLE}"
        self._range_celsius = device.get_kettle_temperature_range()
        self._has_power = device.supports_power
        self._modes = kettle_modes(device)
        features = WaterHeaterEntityFeature(0)
        if device.supports_kettle_temperature:
            features |= WaterHeaterEntityFeature.TARGET_TEMPERATURE
        if self._modes:
            features |= WaterHeaterEntityFeature.OPERATION_MODE
            self._attr_operation_list = ([STATE_OFF] if self._has_power else []) + list(self._modes)
        if self._has_power:
            features |= WaterHeaterEntityFeature.ON_OFF
        self._attr_supported_features = features

    # ------------------------------------------------------------------ #
    # Units and range
    # ------------------------------------------------------------------ #

    @property
    def _fahrenheit(self) -> bool:
        return self.coordinator.kettles.reports_fahrenheit(self._device_id)

    @property
    def temperature_unit(self) -> str:
        """The kettle's own unit."""
        return UnitOfTemperature.FAHRENHEIT if self._fahrenheit else UnitOfTemperature.CELSIUS

    @property
    def min_temp(self) -> float:
        """Lowest settable target, in the entity's unit."""
        return round(to_kettle_unit(self._range_celsius[0], UnitOfTemperature.CELSIUS, self._fahrenheit))

    @property
    def max_temp(self) -> float:
        """Highest settable target, in the entity's unit."""
        return round(to_kettle_unit(self._range_celsius[1], UnitOfTemperature.CELSIUS, self._fahrenheit))

    # ------------------------------------------------------------------ #
    # State
    # ------------------------------------------------------------------ #

    @property
    def current_operation(self) -> str | None:
        """``off`` while powered off, otherwise the selected mode."""
        state = self.device_state
        if state is None:
            return None
        if self._has_power and not state.power_state:
            return STATE_OFF
        return selected_mode(self._modes, state)

    @property
    def current_temperature(self) -> float | None:
        """Water temperature, in the entity's unit."""
        state = self.device_state
        return state.sensor_temperature if state else None

    @property
    def target_temperature(self) -> float | None:
        """Target temperature, in the entity's unit."""
        state = self.device_state
        return state.kettle_target_temperature if state else None

    # ------------------------------------------------------------------ #
    # Commands
    # ------------------------------------------------------------------ #

    def _validation_error(self, key: str, **placeholders: str) -> ServiceValidationError:
        return ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key=key,
            translation_placeholders={"device": self._device.name, **placeholders},
        )

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the kettle on (start heating)."""
        await self._async_send_command(PowerCommand(power_on=True))

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the kettle off."""
        await self._async_send_command(PowerCommand(power_on=False))

    async def async_set_operation_mode(self, operation_mode: str) -> None:
        """Run the kettle in a mode (switching it on), or turn it off for ``off``."""
        if operation_mode == STATE_OFF and self._has_power:
            await self.async_turn_off()
            return
        target = self._modes.get(operation_mode)
        if target is None:
            raise self._validation_error("unsupported_mode", mode=operation_mode)
        state = self.device_state
        if operation_mode == KETTLE_MANUAL_MODE:
            current = state.kettle_target_temperature if state is not None else None
            if current is None:
                raise self._validation_error("kettle_manual_no_target")
            await self._async_send_command(manual_command(current, self._fahrenheit))
        else:
            await self._async_send_command(WorkModeCommand(work_mode=target[0], mode_value=target[1]))
        if self._has_power and state is not None and not state.power_state:
            await self._async_send_command(PowerCommand(power_on=True))

    async def async_set_temperature(self, **kwargs: Any) -> None:
        """Set the target, in the entity's unit; this selects manual.

        Setting the target puts the kettle in manual, so a target together
        with any other mode is refused.
        """
        operation_mode = kwargs.get(ATTR_OPERATION_MODE)
        temperature = kwargs.get(ATTR_TEMPERATURE)
        if temperature is None:
            if operation_mode is not None:
                await self.async_set_operation_mode(operation_mode)
            return
        if operation_mode not in (None, KETTLE_MANUAL_MODE):
            raise self._validation_error("kettle_target_with_mode", mode=str(operation_mode))
        clamped = max(self.min_temp, min(self.max_temp, float(temperature)))
        await self._async_send_command(manual_command(clamped, self._fahrenheit))
        state = self.device_state
        if operation_mode is not None and self._has_power and state is not None and not state.power_state:
            await self._async_send_command(PowerCommand(power_on=True))
