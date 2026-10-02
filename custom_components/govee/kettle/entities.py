"""H7175 kettle entities shared by the platforms.

Status entities (added by the sensor and binary_sensor platforms) read the
kettle's AWS IoT frames and stay unknown until the kettle has pushed (account
login is required for the push). The Brew mode select (select platform)
changes the mode without switching the kettle on, as the Govee app does.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.components.select import SelectEntity
from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.const import EntityCategory, UnitOfTime
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.restore_state import RestoredExtraData, RestoreEntity

from ..const import (
    DOMAIN,
    SUFFIX_KETTLE_BREW_MODE,
    SUFFIX_KETTLE_DIY_SLOT,
    SUFFIX_KETTLE_HEATING_STATUS,
    SUFFIX_KETTLE_KEEP_WARM_MINUTES,
    SUFFIX_KETTLE_KEEP_WARM_REMAINING,
    SUFFIX_KETTLE_KEEP_WARM_STATUS,
)
from ..entity import GoveeEntity
from .frames import HEATING_STATUS, KETTLE_CUSTOM_WORK_MODE
from ..models import WorkModeCommand
from .labels import slot_labels
from .modes import (
    KETTLE_MANUAL_MODE,
    kettle_modes,
    manual_command,
    mode_key,
    restore_slot,
    selected_mode,
    slot_restore_data,
)

if TYPE_CHECKING:
    from ..coordinator import GoveeCoordinator
    from ..models import GoveeDevice

ATTR_LABELS = "labels"


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


class KettleModeMixin(GoveeEntity, RestoreEntity):
    """Shared by the entities that show the brew mode.

    Restores the selected custom slot, which the poll does not report: after
    a restart a kettle in a custom slot is reported as ``{"workMode": 1}``
    until it pushes. The slot is stored by value; a restore that arrives
    before the kettle's first state is kept until that state is in. Also
    exposes the user's slot labels (see :mod:`.labels`).
    """

    _modes: dict[str, tuple[int, int]]
    _pending_restore: dict[str, Any] | None = None

    async def async_added_to_hass(self) -> None:
        """Restore the stored slot."""
        await super().async_added_to_hass()
        extra = await self.async_get_last_extra_data()
        if extra is not None:
            self._pending_restore = extra.as_dict()
            self._apply_pending_restore()

    def _apply_pending_restore(self) -> None:
        state = self.device_state
        if self._pending_restore is not None and state is not None:
            restore_slot(self._modes, state, self._pending_restore)
            self._pending_restore = None

    def _handle_coordinator_update(self) -> None:
        """Apply a pending restore once the kettle's first state is in."""
        self._apply_pending_restore()
        super()._handle_coordinator_update()

    @property
    def extra_restore_state_data(self) -> RestoredExtraData | None:
        """Keep the selected custom slot by value."""
        data = slot_restore_data(self._modes, self.device_state)
        return RestoredExtraData(data) if data is not None else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """The custom-slot labels set in the options, ``{"custom_1": label}``."""
        attrs = dict(super().extra_state_attributes)
        entry = self.coordinator.config_entry
        labels = slot_labels(entry.options, self._device) if entry is not None else {}
        if labels:
            attrs[ATTR_LABELS] = labels
        return attrs


class GoveeKettleBrewModeSelect(KettleModeMixin, SelectEntity):
    """The kettle's brew mode, selected without switching it on (as the Govee app does)."""

    _attr_translation_key = "kettle_brew_mode"

    def __init__(self, coordinator: GoveeCoordinator, device: GoveeDevice) -> None:
        """Initialize the Brew mode select."""
        super().__init__(coordinator, device)
        self._attr_unique_id = f"{device.device_id}{SUFFIX_KETTLE_BREW_MODE}"
        self._modes = kettle_modes(device)
        self._attr_options = list(self._modes)

    @property
    def current_option(self) -> str | None:
        """The mode the kettle has selected, also while it is off."""
        return selected_mode(self._modes, self.device_state)

    def _error(self, key: str, **placeholders: str) -> ServiceValidationError:
        return ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key=key,
            translation_placeholders={"device": self._device.name, **placeholders},
        )

    async def async_select_option(self, option: str) -> None:
        """Select a mode; the kettle stays on or off. ``manual`` re-sends the target."""
        target = self._modes.get(option)
        if target is None:
            raise self._error("unknown_option", option=option)
        if option != KETTLE_MANUAL_MODE:
            await self._async_send_command(WorkModeCommand(work_mode=target[0], mode_value=target[1]))
            return
        state = self.device_state
        current = state.kettle_target_temperature if state is not None else None
        if current is None:
            raise self._error("kettle_manual_no_target")
        await self._async_send_command(
            manual_command(current, self.coordinator.kettles.reports_fahrenheit(self._device_id))
        )


def kettle_selects(coordinator: GoveeCoordinator) -> list[SelectEntity]:
    """The Brew mode select of every H7175 kettle with modes."""
    return [
        GoveeKettleBrewModeSelect(coordinator, device)
        for device in coordinator.devices.values()
        if device.decodes_kettle_frames and not device.is_group and device.get_kettle_mode_options()
    ]


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
