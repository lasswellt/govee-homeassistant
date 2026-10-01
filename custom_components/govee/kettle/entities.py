"""H7175 kettle status entities, read from the kettle's AWS IoT frames.

Created for H7175 kettles only and added by the sensor and binary_sensor
platforms. Each stays unknown until the kettle has pushed the frame it reads
(account login is required for the push).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.const import EntityCategory, UnitOfTime

from ..const import (
    SUFFIX_KETTLE_DIY_SLOT,
    SUFFIX_KETTLE_HEATING_STATUS,
    SUFFIX_KETTLE_KEEP_WARM_MINUTES,
    SUFFIX_KETTLE_KEEP_WARM_REMAINING,
    SUFFIX_KETTLE_KEEP_WARM_STATUS,
)
from ..entity import GoveeEntity
from .frames import HEATING_STATUS, KETTLE_CUSTOM_WORK_MODE
from .modes import kettle_modes, mode_key

if TYPE_CHECKING:
    from ..coordinator import GoveeCoordinator
    from ..models import GoveeDevice


class _KettleEntity(GoveeEntity):
    """A status entity of one H7175 kettle."""

    _suffix: str

    def __init__(self, coordinator: GoveeCoordinator, device: GoveeDevice) -> None:
        """Initialize the entity."""
        super().__init__(coordinator, device)
        self._attr_unique_id = f"{device.device_id}{self._suffix}"


class GoveeKettleHeatingStatusSensor(_KettleEntity, SensorEntity):
    """What the kettle is doing: idle, heating, keeping warm, ..."""

    _attr_translation_key = "kettle_heating_status"
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = list(HEATING_STATUS.values())
    _suffix = SUFFIX_KETTLE_HEATING_STATUS

    @property
    def native_value(self) -> str | None:
        """The heating status; None until reported, or for an undocumented code."""
        state = self.device_state
        return state.kettle_heating_status if state else None


class GoveeKettleKeepWarmMinutesSensor(_KettleEntity, SensorEntity):
    """How long the kettle keeps warm, as set on it."""

    _attr_translation_key = "kettle_keep_warm_minutes"
    _attr_device_class = SensorDeviceClass.DURATION
    _attr_native_unit_of_measurement = UnitOfTime.MINUTES
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _suffix = SUFFIX_KETTLE_KEEP_WARM_MINUTES

    @property
    def native_value(self) -> int | None:
        """The keep-warm duration in minutes."""
        state = self.device_state
        return state.kettle_keep_warm_minutes if state else None


class GoveeKettleKeepWarmRemainingSensor(_KettleEntity, SensorEntity):
    """Minutes of keep warm left, as the kettle last reported (while keep warm is on)."""

    _attr_translation_key = "kettle_keep_warm_remaining"
    _attr_device_class = SensorDeviceClass.DURATION
    _attr_native_unit_of_measurement = UnitOfTime.MINUTES
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _suffix = SUFFIX_KETTLE_KEEP_WARM_REMAINING

    @property
    def native_value(self) -> int | None:
        """Minutes left; unknown while keep warm is off or not reported."""
        state = self.device_state
        if state is None or not state.kettle_keep_warm_enabled:
            return None
        return state.kettle_keep_warm_remaining


class GoveeKettleDiySlotSensor(_KettleEntity, SensorEntity):
    """The custom slot the Govee app marks "DIY" (its current custom slot).

    Not the selected mode: after the target is set directly the kettle is in
    manual and the DIY mark stays on the last custom slot.
    """

    _attr_translation_key = "kettle_diy_slot"
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _suffix = SUFFIX_KETTLE_DIY_SLOT

    def __init__(self, coordinator: GoveeCoordinator, device: GoveeDevice) -> None:
        """Initialize the DIY slot sensor."""
        super().__init__(coordinator, device)
        self._modes = kettle_modes(device)
        self._attr_options = [key for key, (wm, _) in self._modes.items() if wm == KETTLE_CUSTOM_WORK_MODE]

    @property
    def native_value(self) -> str | None:
        """The DIY slot's mode key (``custom_N``)."""
        state = self.device_state
        if state is None or state.kettle_diy_slot is None:
            return None
        return mode_key(self._modes, KETTLE_CUSTOM_WORK_MODE, state.kettle_diy_slot)


class GoveeKettleKeepWarmBinarySensor(_KettleEntity, BinarySensorEntity):
    """Whether keep warm is on, as set on the kettle."""

    _attr_translation_key = "kettle_keep_warm"
    _suffix = SUFFIX_KETTLE_KEEP_WARM_STATUS

    @property
    def is_on(self) -> bool | None:
        """Keep warm on or off."""
        state = self.device_state
        return state.kettle_keep_warm_enabled if state else None


def kettle_sensors(coordinator: GoveeCoordinator) -> list[SensorEntity]:
    """The status sensors of every H7175 kettle."""
    entities: list[SensorEntity] = []
    for device in coordinator.devices.values():
        if device.decodes_kettle_frames and not device.is_group:
            entities.append(GoveeKettleHeatingStatusSensor(coordinator, device))
            entities.append(GoveeKettleKeepWarmMinutesSensor(coordinator, device))
            entities.append(GoveeKettleKeepWarmRemainingSensor(coordinator, device))
            if any(opt["slotted"] for opt in device.get_kettle_mode_options()):
                entities.append(GoveeKettleDiySlotSensor(coordinator, device))
    return entities


def kettle_binary_sensors(coordinator: GoveeCoordinator) -> list[BinarySensorEntity]:
    """The keep-warm binary sensor of every H7175 kettle."""
    return [
        GoveeKettleKeepWarmBinarySensor(coordinator, device)
        for device in coordinator.devices.values()
        if device.decodes_kettle_frames and not device.is_group
    ]
