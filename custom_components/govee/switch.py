"""Switch platform for Govee integration.

Provides switch entities for:
- Smart plugs (on/off control)
- Night light toggle (for lights with night light mode)
"""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.switch import SwitchDeviceClass, SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import GoveeCoordinator
from .entity import GoveeEntity
from .models import GoveeDevice, MusicModeCommand, PowerCommand, create_dreamview_command, create_night_light_command

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Govee switches from a config entry."""
    coordinator: GoveeCoordinator = entry.runtime_data

    entities: list[SwitchEntity] = []

    for device in coordinator.devices.values():
        # Create switch for smart plugs (power on/off)
        if device.is_plug and device.supports_power:
            entities.append(GoveePlugSwitchEntity(coordinator, device))

        # Create switch for night light toggle (lights with night light mode)
        if device.supports_night_light:
            entities.append(GoveeNightLightSwitchEntity(coordinator, device))

        # Create switch for music mode toggle
        # STRUCT-based devices use REST API (no MQTT required)
        # Legacy devices use BLE passthrough via MQTT
        if device.has_struct_music_mode:
            # STRUCT-based music mode - uses REST API, no MQTT required
            entities.append(GoveeMusicModeSwitchEntity(coordinator, device, use_rest_api=True))
            _LOGGER.debug("Created STRUCT music mode switch entity for %s", device.name)
        elif device.supports_music_mode and coordinator.mqtt_connected:
            # Legacy BLE-based music mode - requires MQTT
            entities.append(GoveeMusicModeSwitchEntity(coordinator, device, use_rest_api=False))
            _LOGGER.debug("Created BLE music mode switch entity for %s", device.name)

        # Create switch for DreamView (Movie Mode) toggle
        if device.supports_dreamview:
            entities.append(GoveeDreamViewSwitchEntity(coordinator, device))
            _LOGGER.debug("Created DreamView switch entity for %s", device.name)

    async_add_entities(entities)
    _LOGGER.debug("Set up %d Govee switch entities", len(entities))


class GoveePlugSwitchEntity(GoveeEntity, SwitchEntity):
    """Govee smart plug switch entity.

    Controls power state for Govee smart plugs.
    """

    _attr_device_class = SwitchDeviceClass.OUTLET
    _attr_translation_key = "govee_plug"

    def __init__(
        self,
        coordinator: GoveeCoordinator,
        device: GoveeDevice,
    ) -> None:
        """Initialize the plug switch entity."""
        super().__init__(coordinator, device)

        # Use device name as entity name
        self._attr_name = None

    @property
    def is_on(self) -> bool | None:
        """Return True if plug is on."""
        state = self.device_state
        return state.power_state if state else None

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the plug on."""
        await self.coordinator.async_control_device(
            self._device_id,
            PowerCommand(power_on=True),
        )

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the plug off."""
        await self.coordinator.async_control_device(
            self._device_id,
            PowerCommand(power_on=False),
        )


class GoveeNightLightSwitchEntity(GoveeEntity, SwitchEntity):
    """Govee night light toggle switch entity.

    Controls night light mode for devices that support it.
    Uses optimistic state since API may not return night light status.
    """

    _attr_translation_key = "govee_night_light"

    def __init__(
        self,
        coordinator: GoveeCoordinator,
        device: GoveeDevice,
    ) -> None:
        """Initialize the night light switch entity."""
        super().__init__(coordinator, device)

        # Unique ID for night light switch
        self._attr_unique_id = f"{device.device_id}_night_light"

        # Name as "Night Light"
        self._attr_name = "Night Light"

        # Optimistic state
        self._is_on = False

    @property
    def is_on(self) -> bool:
        """Return True if night light is on."""
        return self._is_on

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn night light on."""
        success = await self.coordinator.async_control_device(
            self._device_id,
            create_night_light_command(enabled=True),
        )
        if success:
            self._is_on = True
            self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn night light off."""
        success = await self.coordinator.async_control_device(
            self._device_id,
            create_night_light_command(enabled=False),
        )
        if success:
            self._is_on = False
            self.async_write_ha_state()


class GoveeMusicModeSwitchEntity(GoveeEntity, SwitchEntity):
    """Govee music mode toggle switch entity.

    Controls music reactive mode for devices that support it.

    For STRUCT-based devices (use_rest_api=True):
    - Uses REST API with structured payload
    - No MQTT required
    - Sends musicMode command with mode and sensitivity

    For legacy devices (use_rest_api=False):
    - Uses BLE passthrough via MQTT
    - Requires MQTT connection
    - Sends simple on/off toggle

    Uses optimistic state since API may not return music mode status.
    """

    _attr_translation_key = "govee_music_mode"
    _attr_icon = "mdi:music"

    def __init__(
        self,
        coordinator: GoveeCoordinator,
        device: GoveeDevice,
        use_rest_api: bool = False,
    ) -> None:
        """Initialize the music mode switch entity.

        Args:
            coordinator: Govee data coordinator.
            device: Device this switch controls.
            use_rest_api: True to use REST API (STRUCT), False for BLE passthrough.
        """
        super().__init__(coordinator, device)

        self._use_rest_api = use_rest_api

        # Unique ID for music mode switch
        self._attr_unique_id = f"{device.device_id}_music_mode"

        # Name as "Music Mode"
        self._attr_name = "Music Mode"

        # Optimistic state
        self._is_on = False

    @property
    def available(self) -> bool:
        """Return True if entity is available.

        For BLE passthrough, requires MQTT connection.
        For REST API, only requires device to be online.
        """
        if not self._use_rest_api and not self.coordinator.mqtt_connected:
            return False
        return super().available

    @property
    def is_on(self) -> bool:
        """Return True if music mode is on."""
        state = self.device_state
        if state and state.music_mode_enabled is not None:
            return state.music_mode_enabled
        return self._is_on

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn music mode on."""
        if self._use_rest_api:
            # Use REST API with STRUCT payload
            # Get current sensitivity and mode from state, or use defaults
            state = self.device_state
            sensitivity = 50
            music_mode = 1  # Default to Rhythm mode
            if state:
                if state.music_sensitivity is not None:
                    sensitivity = state.music_sensitivity
                if state.music_mode_value is not None:
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
        else:
            # Use BLE passthrough via MQTT
            success = await self.coordinator.async_send_music_mode(
                self._device_id,
                enabled=True,
            )

        if success:
            self._is_on = True
            self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn music mode off.

        For STRUCT devices, turning off music mode typically requires
        sending a different command (like switching to a scene or
        solid color). For now, we just clear the state and let the
        user switch to another mode.

        For BLE devices, we send the explicit off command.
        """
        if self._use_rest_api:
            # STRUCT-based devices: Clear optimistic state
            # Note: There's no explicit "off" for STRUCT music mode
            # The user should switch to a scene or color to exit music mode
            state = self.device_state
            if state:
                state.music_mode_enabled = False
                state.source = "optimistic"
            self._is_on = False
            self.async_write_ha_state()
            _LOGGER.debug(
                "Cleared music mode state for %s (switch to scene/color to fully exit)",
                self._device.name,
            )
        else:
            # Use BLE passthrough via MQTT
            success = await self.coordinator.async_send_music_mode(
                self._device_id,
                enabled=False,
            )
            if success:
                self._is_on = False
                self.async_write_ha_state()


class GoveeDreamViewSwitchEntity(GoveeEntity, SwitchEntity):
    """Govee DreamView (Movie Mode) toggle switch entity.

    Controls DreamView mode for devices that support it (e.g., Immersion TV backlights).
    Uses optimistic state since API may not return DreamView status.
    """

    _attr_translation_key = "govee_dreamview"
    _attr_icon = "mdi:movie-open"

    def __init__(
        self,
        coordinator: GoveeCoordinator,
        device: GoveeDevice,
    ) -> None:
        """Initialize the DreamView switch entity."""
        super().__init__(coordinator, device)

        # Unique ID for DreamView switch
        self._attr_unique_id = f"{device.device_id}_dreamview"

        # Name as "DreamView"
        self._attr_name = "DreamView"

        # Optimistic state
        self._is_on = False

    @property
    def is_on(self) -> bool:
        """Return True if DreamView is on."""
        return self._is_on

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn DreamView on."""
        success = await self.coordinator.async_control_device(
            self._device_id,
            create_dreamview_command(enabled=True),
        )
        if success:
            self._is_on = True
            self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn DreamView off."""
        success = await self.coordinator.async_control_device(
            self._device_id,
            create_dreamview_command(enabled=False),
        )
        if success:
            self._is_on = False
            self.async_write_ha_state()
