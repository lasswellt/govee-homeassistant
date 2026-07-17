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
    CAPABILITY_WORK_MODE,
    INSTANCE_FAN_OSCILLATE,
    INSTANCE_FAN_SPEED_MODE,
    INSTANCE_FAN_TOGGLE,
    INSTANCE_REVERSE_AIRFLOW,
    INSTANCE_WORK_MODE,
)

_LOGGER = logging.getLogger(__name__)

PARALLEL_UPDATES = 0

# Stable user-facing preset labels.
PRESET_MODE_NORMAL = "Normal"
PRESET_MODE_AUTO = "Auto"

# Capability fallback defaults when workMode metadata is unavailable.
DEFAULT_WORK_MODE_MANUAL = 1
DEFAULT_WORK_MODE_AUTO = 3
# Backward-compatible aliases used by tests and existing imports.
WORK_MODE_GEAR = DEFAULT_WORK_MODE_MANUAL
WORK_MODE_AUTO = DEFAULT_WORK_MODE_AUTO
MANUAL_MODE_NAMES = {"manual", "gearmode", "fanspeed"}


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
        self._manual_preset_name = PRESET_MODE_NORMAL
        self._manual_work_mode = DEFAULT_WORK_MODE_MANUAL
        self._auto_work_mode = DEFAULT_WORK_MODE_AUTO
        self._preset_work_modes: dict[str, int] = {}
        self._preset_commands: dict[str, tuple[int, int]] = {}
        self._last_mode_values: dict[int, int] = {}
        self._speed_work_modes: set[int] = set()
        self._speedless_work_modes: set[int] = set()
        self._work_mode_speed_values: dict[int, list[int]] = {}
        self._work_mode_speed_sets: dict[int, set[int]] = {}

        self._init_work_mode_mappings(device)

        self._fan_speed_set = set(self._fan_speeds)
        self._attr_speed_count = len(self._fan_speeds)
        self._attr_percentage_step = 100 / len(self._fan_speeds)

        # Build supported features based on device capabilities
        features = FanEntityFeature.TURN_ON | FanEntityFeature.TURN_OFF

        if device.supports_work_mode:
            features |= FanEntityFeature.SET_SPEED
            features |= FanEntityFeature.PRESET_MODE
            self._attr_preset_modes = list(self._preset_commands)

        # Reverse lookup work_mode value -> preset name (issue #114).
        self._work_mode_to_preset: dict[int, str] = {
            wm: name for name, wm in self._preset_work_modes.items()
        }

        if device.supports_oscillation:
            features |= FanEntityFeature.OSCILLATE

        self._attr_supported_features = features

    def _init_work_mode_mappings(self, device: GoveeDevice) -> None:
        """Build work mode mappings and discrete speed levels from capabilities."""
        work_mode_options: list[dict[str, Any]] = []
        mode_value_options: list[dict[str, Any]] = []
        for cap in device.capabilities:
            if cap.type != CAPABILITY_WORK_MODE or cap.instance != INSTANCE_WORK_MODE:
                continue
            for field in cap.parameters.get("fields", []):
                if field.get("fieldName") == "workMode":
                    work_mode_options = field.get("options", [])
                elif field.get("fieldName") == "modeValue":
                    mode_value_options = field.get("options", [])
            break

        mode_values_by_name = {
            str(opt.get("name", "")).strip().lower(): opt
            for opt in mode_value_options
            if opt.get("name")
        }
        mode_value_speeds_by_name: dict[str, list[int]] = {}
        for mode_name, mode_opt in mode_values_by_name.items():
            speeds: list[int] = []
            for opt in mode_opt.get("options", []):
                raw_value = opt.get("value")
                if raw_value is None:
                    continue
                try:
                    speed_value = int(raw_value)
                except (TypeError, ValueError):
                    continue
                if speed_value > 0:
                    speeds.append(speed_value)
            if speeds:
                mode_value_speeds_by_name[mode_name] = sorted(set(speeds))

        # Discover manual mode and its display name from workMode options.
        manual_name = ""
        for opt in work_mode_options:
            opt_name = str(opt.get("name", "")).strip()
            opt_value = opt.get("value")
            if opt_value is None:
                continue
            if opt_name.lower() in MANUAL_MODE_NAMES:
                self._manual_work_mode = int(opt_value)
                manual_name = opt_name
                break

        # The manual preset is always surfaced as "Normal" regardless of the
        # device's workMode option name (gearMode/FanSpeed/manual/...), so
        # automations and scenes referencing "Normal" stay stable across SKUs.
        self._speed_work_modes = {self._manual_work_mode}
        self._speedless_work_modes = set()

        # Discover auto mode ID from workMode options.
        for opt in work_mode_options:
            if (
                str(opt.get("name", "")).strip().lower() == PRESET_MODE_AUTO.lower()
                and opt.get("value") is not None
            ):
                self._auto_work_mode = int(opt["value"])
                break

        # Build authoritative manual speeds from modeValue nested options.
        manual_sub_options = (
            mode_values_by_name.get(manual_name.lower(), {}).get("options", [])
            if manual_name
            else []
        )
        manual_speeds: list[int] = []
        for opt in manual_sub_options:
            raw_value = opt.get("value")
            if raw_value is None:
                continue
            try:
                speed_value = int(raw_value)
            except (TypeError, ValueError):
                continue
            if speed_value > 0:
                manual_speeds.append(speed_value)
        if not manual_speeds:
            for opt in device.get_fan_speed_options():
                if opt.get("work_mode") != self._manual_work_mode:
                    continue
                raw_value = opt.get("mode_value")
                if raw_value is None:
                    continue
                try:
                    speed_value = int(raw_value)
                except (TypeError, ValueError):
                    continue
                if speed_value > 0:
                    manual_speeds.append(speed_value)
        self._fan_speeds = sorted(set(manual_speeds)) if manual_speeds else [1, 2, 3]
        self._work_mode_speed_values[self._manual_work_mode] = self._fan_speeds
        self._work_mode_speed_sets[self._manual_work_mode] = set(self._fan_speeds)
        middle_speed_index = (len(self._fan_speeds) - 1) // 2
        default_manual_mode_value = self._fan_speeds[middle_speed_index]
        self._last_manual_mode_value = default_manual_mode_value

        # Build ordered preset map from workMode options with de-duplication.
        seen: set[str] = set()
        self._preset_work_modes[self._manual_preset_name] = self._manual_work_mode
        # Default to a typical manual speed for safer transitions from non-manual modes.
        self._preset_commands[self._manual_preset_name] = (
            self._manual_work_mode,
            default_manual_mode_value,
        )
        seen.add(self._manual_preset_name.lower())

        auto_name = ""
        auto_mode_value_opt: dict[str, Any] = {}
        for opt in work_mode_options:
            if str(opt.get("name", "")).strip().lower() != PRESET_MODE_AUTO.lower():
                continue
            auto_name = str(opt.get("name", "")).strip() or PRESET_MODE_AUTO
            auto_mode_value_opt = mode_values_by_name.get(auto_name.lower(), {})
            break
        if auto_name and auto_name.lower() not in seen:
            auto_mode_value = self._extract_mode_value(auto_mode_value_opt)
            auto_mode_value = max(auto_mode_value, 0)
            self._preset_work_modes[auto_name] = self._auto_work_mode
            self._preset_commands[auto_name] = (self._auto_work_mode, int(auto_mode_value))
            if mode_value_speeds_by_name.get(auto_name.lower()):
                self._speed_work_modes.add(self._auto_work_mode)
                self._work_mode_speed_values[self._auto_work_mode] = mode_value_speeds_by_name[auto_name.lower()]
                self._work_mode_speed_sets[self._auto_work_mode] = set(mode_value_speeds_by_name[auto_name.lower()])
            else:
                self._speedless_work_modes.add(self._auto_work_mode)
            seen.add(auto_name.lower())

        for opt in work_mode_options:
            preset_name = str(opt.get("name", "")).strip()
            work_mode = opt.get("value")
            if not preset_name or work_mode is None:
                continue
            if preset_name.lower() in MANUAL_MODE_NAMES:
                continue
            if preset_name.lower() == PRESET_MODE_AUTO.lower():
                continue
            if preset_name.lower() in seen:
                continue

            preset_name_lower = preset_name.lower()
            mode_value_opt = mode_values_by_name.get(preset_name_lower, {})
            mode_value = self._extract_mode_value(mode_value_opt)
            mode_speeds = mode_value_speeds_by_name.get(preset_name_lower, [])
            work_mode = int(work_mode)
            if mode_speeds:
                mode_value = max(mode_value, min(mode_speeds))
                self._speed_work_modes.add(work_mode)
                self._work_mode_speed_values[work_mode] = mode_speeds
                self._work_mode_speed_sets[work_mode] = set(mode_speeds)
            else:
                mode_value = max(mode_value, 0)
                self._speedless_work_modes.add(work_mode)

            self._preset_work_modes[preset_name] = work_mode
            self._preset_commands[preset_name] = (work_mode, int(mode_value))
            seen.add(preset_name.lower())

        if PRESET_MODE_AUTO.lower() not in seen:
            self._preset_work_modes[PRESET_MODE_AUTO] = self._auto_work_mode
            self._preset_commands[PRESET_MODE_AUTO] = (
                self._auto_work_mode,
                0,
            )
            self._speedless_work_modes.add(self._auto_work_mode)

        self._last_mode_values.clear()
        for work_mode, mode_value in self._preset_commands.values():
            self._last_mode_values.setdefault(work_mode, mode_value)

    @staticmethod
    def _extract_mode_value(mode_value_opt: dict[str, Any]) -> int:
        """Extract a usable modeValue from a modeValue option definition."""
        mode_value = mode_value_opt.get("defaultValue")
        if mode_value is None and mode_value_opt.get("options"):
            first = mode_value_opt["options"][0]
            mode_value = first.get("value")
        if mode_value is None:
            mode_value = mode_value_opt.get("value", 0)
        try:
            return int(mode_value)
        except (TypeError, ValueError):
            return 0

    def _manual_mode_value_from_state(self) -> int | None:
        """Return modeValue when state is in manual mode and value is a valid speed."""
        state = self.device_state
        manual_speed_set = self._work_mode_speed_sets[self._manual_work_mode]
        if (
            state
            and state.work_mode == self._manual_work_mode
            and state.mode_value is not None
            and state.mode_value in manual_speed_set
        ):
            return int(state.mode_value)
        return None

    @property
    def is_on(self) -> bool | None:
        """Return True if fan is on."""
        state = self.device_state
        return state.power_state if state else None

    @property
    def percentage(self) -> int | None:
        """Return the current speed as a percentage.

        Maps mode_value to percentage using the device's speed list.
        Applies to manual mode and other speed-bearing work modes discovered from presets.
        """
        state = self.device_state
        if state is None:
            return None

        # Return percentage for speed-bearing modes (manual + presets that expose speed).
        if state.work_mode in self._speed_work_modes:
            work_mode = int(state.work_mode)
            speed_values = self._work_mode_speed_values[work_mode]
            speed_set = self._work_mode_speed_sets[work_mode]
            mode_value: int | None
            if state.mode_value is None:
                mode_value = None
            else:
                try:
                    mode_value = int(state.mode_value)
                except (TypeError, ValueError):
                    mode_value = None

            if mode_value not in speed_set:
                mode_value = self._last_mode_values.get(work_mode)

            if mode_value in speed_set:
                return ordered_list_item_to_percentage(speed_values, mode_value)

        return None

    @property
    def preset_mode(self) -> str | None:
        """Return the current preset mode.

        Maps the current work_mode to capability-discovered preset IDs/names.
        Manual/Auto work_mode values are device-specific and not hardcoded.
        """
        state = self.device_state
        if state is None or state.work_mode is None:
            return None

        if state.work_mode == self._manual_work_mode:
            return self._manual_preset_name
        if state.work_mode in self._work_mode_to_preset:
            return self._work_mode_to_preset[state.work_mode]
        return self._manual_preset_name

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

        work_mode = self._manual_work_mode
        state = self.device_state
        if (
            state
            and state.work_mode is not None
            and int(state.work_mode) in self._speed_work_modes
        ):
            work_mode = int(state.work_mode)
        speed_values = self._work_mode_speed_values[work_mode]
        mode_value = percentage_to_ordered_list_item(speed_values, percentage)
        _LOGGER.debug(
            "Setting fan speed: percentage=%d, work_mode=%d, mode_value=%d",
            percentage,
            work_mode,
            mode_value,
        )

        await self.coordinator.async_control_device(
            self._device_id,
            WorkModeCommand(work_mode=work_mode, mode_value=mode_value),
        )
        if work_mode == self._manual_work_mode:
            self._last_manual_mode_value = mode_value
        self._last_mode_values[work_mode] = mode_value

    async def async_set_preset_mode(self, preset_mode: str) -> None:
        """Set the preset mode."""
        manual_mode_value = self._manual_mode_value_from_state()
        if manual_mode_value is not None:
            self._last_manual_mode_value = manual_mode_value
            self._last_mode_values[self._manual_work_mode] = manual_mode_value

        state = self.device_state
        if (
            state
            and state.work_mode is not None
            and state.mode_value is not None
            and int(state.work_mode) in self._preset_work_modes.values()
        ):
            # Capture the current mode's value before switching away so
            # returning to this preset restores the user's last selection.
            state_work_mode = int(state.work_mode)
            state_mode_value = int(state.mode_value)
            if state_work_mode in self._speed_work_modes:
                mode_speeds = self._work_mode_speed_values.get(state_work_mode, self._fan_speeds)
                mode_speed_set = self._work_mode_speed_sets.get(state_work_mode, set(mode_speeds))
                if state_mode_value in mode_speed_set:
                    self._last_mode_values[state_work_mode] = state_mode_value
            elif state_work_mode in self._speedless_work_modes:
                self._last_mode_values[state_work_mode] = 0
            else:
                self._last_mode_values[state_work_mode] = state_mode_value

        if preset_mode in self._preset_commands:
            work_mode, mode_value = self._preset_commands[preset_mode]
            if preset_mode == self._manual_preset_name:
                mode_value = self._last_manual_mode_value
                if manual_mode_value is not None:
                    mode_value = manual_mode_value
                # Optimistically persist the selected manual speed.
                self._last_manual_mode_value = mode_value
            elif work_mode in self._speedless_work_modes:
                mode_value = 0
            elif work_mode in self._speed_work_modes:
                mode_value = self._last_mode_values.get(work_mode, mode_value)
                mode_value = max(
                    mode_value,
                    min(self._work_mode_speed_values.get(work_mode, self._fan_speeds)),
                )
            else:
                mode_value = self._last_mode_values.get(work_mode, mode_value)
        else:
            # Manual mode fallback - use current speed or typical available speed
            work_mode = self._manual_work_mode
            mode_value = self._last_manual_mode_value
            if manual_mode_value is not None:
                mode_value = manual_mode_value
            self._last_manual_mode_value = mode_value

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
        if work_mode == self._manual_work_mode:
            self._last_manual_mode_value = mode_value
        self._last_mode_values[work_mode] = mode_value

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
        if device.supports_fan_oscillation:
            features |= FanEntityFeature.OSCILLATE
        self._attr_supported_features = features

        # Optimistic state — Govee does not report fan state on poll.
        self._is_on = False
        self._speed_value: int | None = None
        self._direction = DIRECTION_FORWARD
        self._oscillating = False

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
        oscillating = last_state.attributes.get("oscillating")
        if oscillating is not None:
            self._oscillating = bool(oscillating)

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

    @property
    def oscillating(self) -> bool | None:
        """Return whether the fan is oscillating (optimistic)."""
        if not self._device.supports_fan_oscillation:
            return None
        return self._oscillating

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

    async def async_oscillate(self, oscillating: bool) -> None:
        """Start or stop oscillation (fanOscillateToggle)."""
        _LOGGER.debug("Setting ceiling fan oscillation: %s", oscillating)
        success = await self.coordinator.async_control_device(
            self._device_id,
            ToggleCommand(toggle_instance=INSTANCE_FAN_OSCILLATE, enabled=oscillating),
        )
        if success:
            self._oscillating = oscillating
            self.async_write_ha_state()
