"""Number platform for Govee integration.

Provides number entities for device controls that use numeric values,
such as music sensitivity.
"""

from __future__ import annotations

import logging

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import GoveeCoordinator
from .models import GoveeDevice, MusicModeCommand

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Govee number entities from a config entry."""
    coordinator: GoveeCoordinator = entry.runtime_data

    entities: list[NumberEntity] = []

    for device in coordinator.devices.values():
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

    async_add_entities(entities)
    _LOGGER.debug("Set up %d Govee number entities", len(entities))


class GoveeMusicSensitivityNumber(
    CoordinatorEntity["GoveeCoordinator"],
    RestoreEntity,
    NumberEntity,
):
    """Govee music sensitivity control entity.

    Controls the microphone sensitivity for music reactive modes (0-100).
    Higher values = more sensitive to sound.

    This entity uses the REST API with STRUCT-based music mode commands,
    NOT the legacy BLE passthrough.

    Uses RestoreEntity to persist sensitivity across Home Assistant restarts
    since the API doesn't return the current sensitivity value.
    """

    _attr_has_entity_name = True
    _attr_translation_key = "govee_music_sensitivity"
    _attr_icon = "mdi:microphone"
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
        super().__init__(coordinator)

        self._device = device
        self._device_id = device.device_id

        # Set sensitivity range (default 0-100)
        min_sens, max_sens = sensitivity_range or (0, 100)
        self._attr_native_min_value = float(min_sens)
        self._attr_native_max_value = float(max_sens)
        self._attr_native_step = 1
        self._attr_native_value: float | None = 50.0  # Default to mid-sensitivity

        # Unique ID
        self._attr_unique_id = f"{device.device_id}_music_sensitivity"

        # Entity name
        self._attr_name = "Music Sensitivity"

    @property
    def device_info(self) -> DeviceInfo:
        """Return device information."""
        return DeviceInfo(
            identifiers={(DOMAIN, self._device.device_id)},
            name=self._device.name,
            manufacturer="Govee",
            model=self._device.sku,
        )

    @property
    def available(self) -> bool:
        """Return True if entity is available."""
        state = self.coordinator.get_state(self._device_id)
        if state is None:
            return False
        return state.online or self._device.is_group

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

        # Get current music mode from state, default to 1 (Rhythm)
        state = self.coordinator.get_state(self._device_id)
        music_mode = 1
        if state and state.music_mode_value is not None:
            music_mode = state.music_mode_value

        command = MusicModeCommand(
            music_mode=music_mode,
            sensitivity=sensitivity,
            auto_color=1,  # Use automatic colors
        )

        success = await self.coordinator.async_control_device(
            self._device_id,
            command,
        )

        if success:
            self._attr_native_value = float(sensitivity)
            self.async_write_ha_state()
            _LOGGER.debug(
                "Set music sensitivity to %d (mode=%d) on %s",
                sensitivity,
                music_mode,
                self._device.name,
            )
        else:
            _LOGGER.warning(
                "Failed to set music sensitivity to %d on %s",
                sensitivity,
                self._device.name,
            )
