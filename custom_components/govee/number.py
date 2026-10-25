"""Number platform for Govee integration.

Provides number entities for device controls that use numeric values,
such as music sensitivity.
"""

from __future__ import annotations

import logging
import math

from homeassistant.components.number import (
    NumberDeviceClass,
    NumberEntity,
    NumberMode,
    RestoreNumber,
)
from homeassistant.const import EntityCategory, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.util.unit_conversion import TemperatureConverter

from .api.probe_thermometer import probes_for_sku
from .const import CONF_ENABLE_H6104_BLE, SUFFIX_HEATER_TEMPERATURE, SUFFIX_MUSIC_SENSITIVITY
from .coordinator import GoveeConfigEntry, GoveeCoordinator
from .entity import GoveeEntity
from .h6104 import GoveeTVSensitivityNumber
from .models import GoveeDevice, MusicModeCommand, TemperatureSettingCommand

_LOGGER = logging.getLogger(__name__)

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: GoveeConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Govee number entities from a config entry."""
    coordinator: GoveeCoordinator = entry.runtime_data

    entities: list[NumberEntity] = []

    for device in coordinator.devices.values():
        if device.sku == "H6104" and not device.is_group and entry.options.get(CONF_ENABLE_H6104_BLE, False) is True:
            entities.append(GoveeTVSensitivityNumber(coordinator, device))
        # Probe thermometer alarm limits: four per probe.
        if device.is_probe_thermometer:
            for probe in probes_for_sku(device.sku):
                for limit in (
                    "core_max",
                    "core_min",
                    "ambient_max",
                    "ambient_min",
                ):
                    entities.append(GoveeProbeLimitNumber(coordinator, device, probe, limit))
            continue

        # Music sensitivity control for devices with STRUCT-based music mode
        # Note: This doesn't require MQTT - it uses REST API
        if device.has_struct_music_mode:
            music_options = device.get_music_mode_options()
            if music_options:
                sensitivity_range = device.get_music_sensitivity_range()
                entities.append(
                    GoveeMusicSensitivityNumber(
                        coordinator=coordinator,
                        device=device,
                        sensitivity_range=sensitivity_range,
                    )
                )
                _LOGGER.debug(
                    "Created music sensitivity number entity for %s (range=%s)",
                    device.name,
                    sensitivity_range,
                )

        # Heater temperature control
        if device.is_heater:
            temp_range = device.get_temperature_range()
            entities.append(
                GoveeHeaterTemperatureNumber(
                    coordinator=coordinator,
                    device=device,
                    temp_range=temp_range,
                )
            )
            _LOGGER.debug(
                "Created heater temperature number entity for %s (range=%s)",
                device.name,
                temp_range,
            )

    async_add_entities(entities)
    _LOGGER.debug("Set up %d Govee number entities", len(entities))


class GoveeProbeLimitNumber(GoveeEntity, NumberEntity):
    """One alarm limit of one probe on a probe thermometer (H5192).

    Four per probe: the core corridor and the ambient corridor, each with an
    upper and a lower bound. Register 0x12 has no partial update, so writing
    one value carries the other three over from state — see
    :meth:`GoveeCoordinator.async_set_probe_limits`, which refuses the write
    rather than guessing when one of them is still unknown.

    Availability deliberately ignores ``state.online``: this SKU reports it
    as false permanently, the same way the BFF thermo-hygrometers do.
    """

    _attr_device_class = NumberDeviceClass.TEMPERATURE
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_native_min_value = -20.0
    _attr_native_max_value = 300.0
    _attr_native_step = 1.0
    _attr_mode = NumberMode.BOX
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(
        self,
        coordinator: GoveeCoordinator,
        device: GoveeDevice,
        probe: int,
        limit: str,
    ) -> None:
        """Initialize the probe limit number."""
        super().__init__(coordinator, device)
        self._probe = probe
        self._limit = limit
        self._attr_unique_id = f"{device.device_id}_probe{probe}_{limit}"
        self._attr_translation_key = f"probe_{limit}"
        self._attr_translation_placeholders = {"probe": str(probe)}

    @property
    def available(self) -> bool:
        """Available as soon as the coordinator holds state for the device."""
        return self.coordinator.last_update_success and (self.device_state is not None)

    @property
    def native_value(self) -> float | None:
        """Return the limit currently stored in the device."""
        state = self.device_state
        if state is None:
            return None
        reading = state.probes.get(self._probe)
        if reading is None:
            return None
        value = getattr(reading, self._limit)
        return float(value) if value is not None else None

    async def async_set_native_value(self, value: float) -> None:
        """Write the limit to the device; raise if the frame did not go out."""
        if not await self.coordinator.async_set_probe_limits(self._device_id, self._probe, **{self._limit: value}):
            raise self._command_failed()


class GoveeMusicSensitivityNumber(GoveeEntity, RestoreEntity, NumberEntity):
    """Govee music sensitivity control entity.

    Controls the microphone sensitivity for music reactive modes (0-100).
    Higher values = more sensitive to sound.

    This entity uses the REST API with STRUCT-based music mode commands,
    NOT the legacy BLE passthrough.

    Uses RestoreEntity to persist sensitivity across Home Assistant restarts
    since the API doesn't return the current sensitivity value.
    """

    _attr_translation_key = "govee_music_sensitivity"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_mode = NumberMode.SLIDER

    def __init__(
        self,
        coordinator: GoveeCoordinator,
        device: GoveeDevice,
        sensitivity_range: tuple[int, int] | None = None,
    ) -> None:
        """Initialize the music sensitivity number entity.

        Args:
            coordinator: Govee data coordinator.
            device: Device this entity controls.
            sensitivity_range: Optional (min, max) sensitivity range.
        """
        super().__init__(coordinator, device)

        # Set sensitivity range (default 0-100)
        min_sens, max_sens = sensitivity_range or (0, 100)
        self._attr_native_min_value = float(min_sens)
        self._attr_native_max_value = float(max_sens)
        self._attr_native_step = 1
        self._attr_native_value: float | None = 50.0  # Default to mid-sensitivity

        # Unique ID
        self._attr_unique_id = f"{device.device_id}{SUFFIX_MUSIC_SENSITIVITY}"

    @property
    def native_value(self) -> float | None:
        """Prefer H6104 device-reported music sensitivity when available."""
        if self._device.sku == "H6104":
            state = self.coordinator.get_state(self._device_id)
            if state is not None and state.music_sensitivity is not None:
                return float(state.music_sensitivity)
        return self._attr_native_value

    async def async_added_to_hass(self) -> None:
        """Restore state when entity is added to Home Assistant."""
        await super().async_added_to_hass()

        # Restore previous state
        if (last_state := await self.async_get_last_state()) is not None:
            if last_state.state not in (None, "unknown", "unavailable"):
                try:
                    self._attr_native_value = float(last_state.state)
                    _LOGGER.debug(
                        "Restored music sensitivity for %s: %s",
                        self._device.name,
                        self._attr_native_value,
                    )
                except ValueError:
                    _LOGGER.warning(
                        "Could not restore music sensitivity for %s: invalid state '%s'",
                        self._device.name,
                        last_state.state,
                    )

    async def async_set_native_value(self, value: float) -> None:
        """Set the music sensitivity.

        This sends a music mode command with the new sensitivity value
        while preserving the current music mode.

        Args:
            value: Sensitivity value within the configured range (0-100).
        """
        sensitivity = int(value)

        # The sensitivity rides in the same STRUCT as the mode, so a mode the
        # device advertises must go with it. The old hard-coded default of 1
        # ("Rhythm" on most strips) is rejected by the H6022 (valid: 3/4/5/6)
        # with "Parameter value out of range" — the same defect the music-mode
        # switch was fixed for in v2026.9.2 (issue #186). A remembered mode is
        # only reused when the device actually advertises it.
        state = self.coordinator.get_state(self._device_id)
        valid_modes = [
            int(opt["value"]) for opt in self._device.get_music_mode_options() if isinstance(opt.get("value"), int)
        ]
        music_mode = valid_modes[0] if valid_modes else 1
        if state and state.music_mode_value is not None and (not valid_modes or state.music_mode_value in valid_modes):
            music_mode = state.music_mode_value

        await self._async_send_command(
            MusicModeCommand(
                music_mode=music_mode,
                sensitivity=sensitivity,
                auto_color=1,  # Use automatic colors
            )
        )
        self._attr_native_value = float(sensitivity)
        self.async_write_ha_state()
        _LOGGER.debug(
            "Set music sensitivity to %d (mode=%d) on %s",
            sensitivity,
            music_mode,
            self._device.name,
        )


class GoveeHeaterTemperatureNumber(GoveeEntity, RestoreNumber):
    """Govee heater temperature control entity.

    Controls the target temperature for heater devices (typically 16-35°C).
    The polled ``heater_temperature`` wins when the device reports it; the
    API may not, so the last native (°C) value is restored with
    ``RestoreNumber`` and seeded into the device state, where the auto-stop
    switch reads it for the shared STRUCT write.
    """

    _attr_translation_key = "govee_heater_temperature"
    _attr_device_class = NumberDeviceClass.TEMPERATURE
    _attr_mode = NumberMode.SLIDER
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS

    def __init__(
        self,
        coordinator: GoveeCoordinator,
        device: GoveeDevice,
        temp_range: tuple[int, int] | None = None,
    ) -> None:
        """Initialize the heater temperature number entity.

        Args:
            coordinator: Govee data coordinator.
            device: Device this entity controls.
            temp_range: Optional (min, max) temperature range in Celsius.
        """
        super().__init__(coordinator, device)

        # Set temperature range (default 16-35°C)
        min_temp, max_temp = temp_range or (16, 35)
        self._attr_native_min_value = float(min_temp)
        self._attr_native_max_value = float(max_temp)
        self._attr_native_step = 1
        self._attr_native_value: float | None = float((min_temp + max_temp) // 2)

        # Unique ID
        self._attr_unique_id = f"{device.device_id}{SUFFIX_HEATER_TEMPERATURE}"

    async def async_added_to_hass(self) -> None:
        """Restore the last native (°C) target when the device has not reported one.

        The restored value comes from ``RestoreNumber`` in the native unit;
        the saved entity state is in the user's display unit (°F on imperial
        installs) and must not be read back as Celsius.
        """
        await super().async_added_to_hass()

        last = await self.async_get_last_number_data()
        if last is not None and last.native_value is not None:
            native: float | None = float(last.native_value)
        else:
            native = await self._async_last_state_in_celsius()
        if native is None or not math.isfinite(native):
            return
        restored = min(max(native, self.native_min_value), self.native_max_value)
        self._attr_native_value = restored
        _LOGGER.debug("Restored heater temperature for %s: %s", self._device.name, restored)

        # Seed the shared state so the auto-stop switch sends this target,
        # not a guess, when the poll does not report one.
        state = self.coordinator.get_state(self._device_id)
        if state is not None and state.heater_temperature is None:
            state.heater_temperature = round(restored)

    async def _async_last_state_in_celsius(self) -> float | None:
        """Read a saved state from before native restore data existed, converted to °C.

        Older versions saved no native value, only the displayed state, which
        is in the unit named by its ``unit_of_measurement`` attribute.
        """
        last_state = await self.async_get_last_state()
        if last_state is None:
            return None
        unit = last_state.attributes.get("unit_of_measurement")
        if unit not in (UnitOfTemperature.CELSIUS, UnitOfTemperature.FAHRENHEIT):
            return None
        try:
            value = float(last_state.state)
        except ValueError:
            return None
        celsius = float(TemperatureConverter.convert(value, unit, UnitOfTemperature.CELSIUS))
        # Out of range can only come from the old restore bug (display value
        # read back as °C); discard it rather than clamp it into a real target.
        if not self.native_min_value <= celsius <= self.native_max_value:
            return None
        return celsius

    @property
    def native_value(self) -> float | None:
        """Return the device-reported target, else the last set or restored one."""
        state = self.device_state
        if state is not None and state.heater_temperature is not None:
            return float(state.heater_temperature)
        return self._attr_native_value

    async def async_set_native_value(self, value: float) -> None:
        """Set the heater target temperature.

        Args:
            value: Temperature value in Celsius.
        """
        temperature = int(value)

        # Preserve the current auto_stop setting so the API doesn't ignore the
        # command: polled, or the switch's restored/last set value (both live
        # in state); 0 only when nothing is known at all.
        auto_stop = 0
        state = self.coordinator.get_state(self._device_id)
        if state and state.heater_auto_stop is not None:
            auto_stop = state.heater_auto_stop

        await self._async_send_command(TemperatureSettingCommand(temperature=temperature, auto_stop=auto_stop))
        self._attr_native_value = float(temperature)
        self.async_write_ha_state()
        _LOGGER.debug(
            "Set heater temperature to %d°C on %s",
            temperature,
            self._device.name,
        )
