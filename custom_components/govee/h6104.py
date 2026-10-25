"""Desired H6104 TV-mode settings, restored locally rather than read from hardware."""

from __future__ import annotations

import math

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.components.select import SelectEntity
from homeassistant.const import EntityCategory
from homeassistant.helpers.restore_state import RestoreEntity

from .coordinator import GoveeCoordinator
from .entity import GoveeEntity
from .models import GoveeDevice

TV_AREAS = ("Part of screen", "Whole screen")


class GoveeTVSensitivityNumber(GoveeEntity, RestoreEntity, NumberEntity):
    """Camera response sensitivity; this is not a frame-rate control."""

    _attr_translation_key = "govee_tv_sensitivity"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_native_min_value = 0
    _attr_native_max_value = 100
    _attr_native_step = 1
    _attr_mode = NumberMode.SLIDER

    def __init__(self, coordinator: GoveeCoordinator, device: GoveeDevice) -> None:
        super().__init__(coordinator, device)
        self._attr_unique_id = f"{device.device_id}_tv_sensitivity"

    @property
    def native_value(self) -> float:
        return float(self.coordinator.h6104_tv_settings(self._device_id)[1])

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        if (last := await self.async_get_last_state()) is not None:
            try:
                value = float(last.state)
                if math.isfinite(value) and value.is_integer() and 0 <= value <= 100:
                    self.coordinator.restore_h6104_tv_settings(self._device_id, sensitivity=int(value))
            except (TypeError, ValueError):
                pass

    async def async_set_native_value(self, value: float) -> None:
        if not math.isfinite(value) or not float(value).is_integer() or not 0 <= value <= 100:
            raise ValueError("TV sensitivity must be a whole number from 0 to 100")
        if not await self.coordinator.async_update_h6104_tv_settings(self._device_id, sensitivity=int(value)):
            raise self._command_failed()
        self.async_write_ha_state()


class GoveeTVAreaSelect(GoveeEntity, RestoreEntity, SelectEntity):
    """Choose the part/all screen setting used by the H6104 camera."""

    _attr_translation_key = "govee_tv_area"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_options = list(TV_AREAS)

    def __init__(self, coordinator: GoveeCoordinator, device: GoveeDevice) -> None:
        super().__init__(coordinator, device)
        self._attr_unique_id = f"{device.device_id}_tv_area"

    @property
    def current_option(self) -> str:
        return TV_AREAS[self.coordinator.h6104_tv_settings(self._device_id)[0]]

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        if (last := await self.async_get_last_state()) is not None and last.state in TV_AREAS:
            self.coordinator.restore_h6104_tv_settings(self._device_id, area=TV_AREAS.index(last.state))

    async def async_select_option(self, option: str) -> None:
        if option not in TV_AREAS:
            raise ValueError("Unknown TV screen area")
        if not await self.coordinator.async_update_h6104_tv_settings(self._device_id, area=TV_AREAS.index(option)):
            raise self._command_failed()
        self.async_write_ha_state()
