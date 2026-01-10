"""Govee data update coordinator.

Manages device state with two update strategies:
1. AWS IoT MQTT (preferred): Real-time state push when email/password provided
2. Polling fallback: REST API polling when only API key provided

State management approach:
- Main devices: Source-based (poll after command, no optimistic to avoid flipflop)
- Segments/Groups/Scenes: Optimistic + RestoreEntity (API never returns this state)
"""
from __future__ import annotations

import asyncio
import logging
from datetime import timedelta
from typing import TYPE_CHECKING, Any

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import GoveeApiClient, GoveeApiError, GoveeAuthError, GoveeRateLimitError
from .models import GoveeDevice, GoveeDeviceState

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry

    from .api.auth import GoveeIotCredentials

# Import at module level to avoid blocking I/O in event loop
# (importlib_metadata does file operations when aiomqtt is imported)
from .mqtt_iot import GoveeAwsIotClient

_LOGGER = logging.getLogger(__name__)


class GoveeCoordinator(DataUpdateCoordinator[dict[str, GoveeDeviceState]]):
    """Coordinator for Govee device state updates.

    Supports two modes:
    - Real-time: AWS IoT MQTT push (requires Govee email/password)
    - Polling: REST API polling (API key only, fallback mode)
    """

    config_entry: ConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        config_entry: ConfigEntry,
        client: GoveeApiClient,
        update_interval: timedelta,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=config_entry,
            name="Govee",
            update_interval=update_interval,
            always_update=False,  # Only notify when data changes
        )
        self.client = client
        self.devices: dict[str, GoveeDevice] = {}
        self._iot_client: GoveeAwsIotClient | None = None
        self._iot_credentials: GoveeIotCredentials | None = None

    async def _async_setup(self) -> None:
        """Fetch device list on first refresh."""
        _LOGGER.debug("Fetching device list from Govee API")

        try:
            raw_devices = await self.client.get_devices()
        except GoveeAuthError as err:
            raise ConfigEntryAuthFailed("Invalid API key") from err
        except GoveeApiError as err:
            raise UpdateFailed(f"Failed to fetch devices: {err}") from err

        for raw in raw_devices:
            device = GoveeDevice.from_api(raw)
            if device.is_supported:
                self.devices[device.device_id] = device
                _LOGGER.debug(
                    "Discovered device: %s (%s) - %s",
                    device.device_name,
                    device.sku,
                    device.device_type,
                )
            else:
                _LOGGER.debug(
                    "Skipping unsupported device: %s (%s)",
                    device.device_name,
                    device.sku,
                )

        _LOGGER.info("Discovered %d supported Govee devices", len(self.devices))

    async def _async_update_data(self) -> dict[str, GoveeDeviceState]:
        """Poll device states from API.

        This is the fallback when AWS IoT MQTT is not available.
        When IoT is connected, polling frequency can be reduced.
        """
        if not self.devices:
            await self._async_setup()

        states: dict[str, GoveeDeviceState] = {}

        for device_id, device in self.devices.items():
            # Skip group devices - they return 400 on state query
            if device.is_group:
                if self.data and device_id in self.data:
                    states[device_id] = self.data[device_id]
                else:
                    states[device_id] = GoveeDeviceState(device_id=device_id, online=False)
                continue

            try:
                raw = await self.client.get_device_state(device_id, device.sku)
                new_state = GoveeDeviceState.from_api(device_id, raw)

                # Preserve optimistic state (segments, scenes) from previous
                if self.data and device_id in self.data:
                    old_state = self.data[device_id]
                    new_state.segment_colors = old_state.segment_colors
                    new_state.segment_brightness = old_state.segment_brightness
                    new_state.active_scene = old_state.active_scene
                    new_state.active_scene_name = old_state.active_scene_name

                states[device_id] = new_state

            except GoveeRateLimitError as err:
                _LOGGER.warning("Rate limited fetching %s: %s", device_id, err)
                raise UpdateFailed(f"Rate limited: {err}") from err

            except GoveeApiError as err:
                _LOGGER.warning("Failed to get state for %s: %s", device_id, err)
                # Preserve previous state on error
                if self.data and device_id in self.data:
                    states[device_id] = self.data[device_id]
                else:
                    states[device_id] = GoveeDeviceState(device_id=device_id, online=False)

        return states

    def get_state(self, device_id: str) -> GoveeDeviceState | None:
        """Get current state for a device."""
        return self.data.get(device_id) if self.data else None

    async def async_setup_iot(self, credentials: GoveeIotCredentials) -> None:
        """Start AWS IoT MQTT client for real-time state updates.

        Args:
            credentials: IoT credentials from Govee login API
        """
        def on_state_update(device_id: str, state: dict[str, Any]) -> None:
            """Handle real-time state update from AWS IoT.

            Schedules update on HA event loop to avoid race conditions
            with concurrent coordinator updates.
            """
            # Schedule the update on the event loop to avoid race conditions
            self.hass.loop.call_soon_threadsafe(
                self._apply_iot_update, device_id, state
            )

        self._iot_credentials = credentials
        self._iot_client = GoveeAwsIotClient(credentials, on_state_update)
        await self._iot_client.async_start()

        _LOGGER.info("AWS IoT MQTT enabled for real-time state updates")

    def _apply_iot_update(self, device_id: str, state: dict[str, Any]) -> None:
        """Apply IoT state update (called from event loop).

        Creates a new data dict to avoid mutating shared state directly.
        """
        if not self.data or device_id not in self.data:
            return

        # Create shallow copy to avoid mutating shared state
        new_data = dict(self.data)
        device_state = new_data[device_id]
        device_state.apply_iot_state(state)

        # Push update to all listeners
        self.async_set_updated_data(new_data)
        _LOGGER.debug("Applied IoT state update for %s", device_id)

    async def async_stop_iot(self) -> None:
        """Stop AWS IoT MQTT client."""
        if self._iot_client:
            await self._iot_client.async_stop()
            self._iot_client = None
            _LOGGER.debug("AWS IoT MQTT client stopped")

    @property
    def iot_connected(self) -> bool:
        """Return True if AWS IoT MQTT is connected."""
        return self._iot_client is not None and self._iot_client.connected

    async def async_send_command(
        self,
        device_id: str,
        capability_type: str,
        instance: str,
        value: Any,
    ) -> None:
        """Send command to device and refresh state.

        Source-based approach: No optimistic update for main device state.
        Polls API after command to get actual state.

        Args:
            device_id: Target device ID
            capability_type: Govee capability type
            instance: Capability instance
            value: Command value
        """
        device = self.devices.get(device_id)
        if not device:
            _LOGGER.error("Device not found: %s", device_id)
            return

        try:
            await self.client.control_device(
                device_id,
                device.sku,
                capability_type,
                instance,
                value,
            )

            # For IoT-connected devices, state push will come via MQTT
            # For polling-only, request immediate refresh
            if not self.iot_connected:
                await self.async_request_refresh()

        except GoveeApiError as err:
            _LOGGER.error(
                "Failed to control device %s (%s.%s = %s): %s",
                device_id,
                capability_type,
                instance,
                value,
                err,
            )
            raise

    async def async_set_segment_color(
        self,
        device_id: str,
        segment: int,
        rgb: tuple[int, int, int],
    ) -> None:
        """Set segment color with optimistic update.

        Segments use optimistic state because API never returns segment state.
        """
        device = self.devices.get(device_id)
        if not device:
            return

        try:
            await self.client.set_segment_color(device_id, device.sku, segment, rgb)

            # Optimistic update - create new dict to avoid mutating shared state
            if self.data and device_id in self.data:
                new_data = dict(self.data)
                new_data[device_id].set_segment_color(segment, rgb)
                self.async_set_updated_data(new_data)

        except GoveeApiError as err:
            _LOGGER.error("Failed to set segment %d color: %s", segment, err)
            raise

    async def async_set_segment_brightness(
        self,
        device_id: str,
        segment: int,
        brightness: int,
    ) -> None:
        """Set segment brightness with optimistic update."""
        device = self.devices.get(device_id)
        if not device:
            return

        try:
            await self.client.set_segment_brightness(device_id, device.sku, segment, brightness)

            # Optimistic update - create new dict to avoid mutating shared state
            if self.data and device_id in self.data:
                new_data = dict(self.data)
                new_data[device_id].set_segment_brightness(segment, brightness)
                self.async_set_updated_data(new_data)

        except GoveeApiError as err:
            _LOGGER.error("Failed to set segment %d brightness: %s", segment, err)
            raise

    async def async_set_scene(
        self,
        device_id: str,
        scene_value: dict[str, Any],
        scene_name: str | None = None,
    ) -> None:
        """Set scene with optimistic update.

        Scenes use optimistic state because API never returns active scene.
        """
        device = self.devices.get(device_id)
        if not device:
            return

        try:
            await self.client.set_scene(device_id, device.sku, scene_value)

            # Optimistic update - create new dict to avoid mutating shared state
            scene_id = str(scene_value.get("id", scene_value.get("paramId", "")))
            if self.data and device_id in self.data:
                new_data = dict(self.data)
                new_data[device_id].set_scene(scene_id, scene_name)
                self.async_set_updated_data(new_data)

        except GoveeApiError as err:
            _LOGGER.error("Failed to set scene: %s", err)
            raise

    async def async_get_dynamic_scenes(self, device_id: str) -> list[dict[str, Any]]:
        """Get dynamic scenes for a device."""
        device = self.devices.get(device_id)
        if not device:
            return []

        try:
            return await self.client.get_dynamic_scenes(device_id, device.sku)
        except GoveeApiError as err:
            _LOGGER.warning("Failed to get scenes for %s: %s", device_id, err)
            return []
