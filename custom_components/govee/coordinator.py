"""DataUpdateCoordinator for Govee integration.

Manages device discovery, state polling, and MQTT integration.
Implements IStateProvider protocol for clean architecture.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import timedelta
from typing import TYPE_CHECKING, Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import (
    GoveeApiClient,
    GoveeApiError,
    GoveeAuthError,
    GoveeAwsIotClient,
    GoveeDeviceNotFoundError,
    GoveeIotCredentials,
    GoveeRateLimitError,
)
from .const import DOMAIN
from .models import GoveeDevice, GoveeDeviceState
from .protocols import IStateObserver
from .repairs import (
    async_create_auth_issue,
    async_create_mqtt_issue,
    async_create_rate_limit_issue,
    async_delete_auth_issue,
    async_delete_mqtt_issue,
    async_delete_rate_limit_issue,
)

if TYPE_CHECKING:
    from .models.commands import DeviceCommand

_LOGGER = logging.getLogger(__name__)

# State fetch timeout per device
STATE_FETCH_TIMEOUT = 30


class GoveeCoordinator(DataUpdateCoordinator[dict[str, GoveeDeviceState]]):
    """Coordinator for Govee device state management.

    Features:
    - Parallel state fetching for all devices
    - MQTT integration for real-time updates
    - Scene caching
    - Optimistic state updates
    - Group device handling

    Implements IStateProvider protocol for entities.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        config_entry: ConfigEntry,
        api_client: GoveeApiClient,
        iot_credentials: GoveeIotCredentials | None,
        poll_interval: int,
        enable_groups: bool = False,
    ) -> None:
        """Initialize the coordinator.

        Args:
            hass: Home Assistant instance.
            config_entry: Config entry for this integration.
            api_client: Govee REST API client.
            iot_credentials: Optional IoT credentials for MQTT.
            poll_interval: Polling interval in seconds.
            enable_groups: Whether to include group devices.
        """
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=poll_interval),
        )

        self._config_entry = config_entry
        self._api_client = api_client
        self._iot_credentials = iot_credentials
        self._enable_groups = enable_groups

        # Device registry
        self._devices: dict[str, GoveeDevice] = {}

        # State cache
        self._states: dict[str, GoveeDeviceState] = {}

        # Scene cache {device_id: [scenes]}
        self._scene_cache: dict[str, list[dict[str, Any]]] = {}

        # DIY scene cache {device_id: [scenes]}
        self._diy_scene_cache: dict[str, list[dict[str, Any]]] = {}

        # Observers for state changes
        self._observers: list[IStateObserver] = []

        # MQTT client for real-time updates
        self._mqtt_client: GoveeAwsIotClient | None = None

        # Track rate limit state to avoid spamming repair issues
        self._rate_limited: bool = False

    @property
    def devices(self) -> dict[str, GoveeDevice]:
        """Get all discovered devices."""
        return self._devices

    @property
    def states(self) -> dict[str, GoveeDeviceState]:
        """Get current states for all devices."""
        return self._states

    def get_device(self, device_id: str) -> GoveeDevice | None:
        """Get device by ID."""
        return self._devices.get(device_id)

    def get_state(self, device_id: str) -> GoveeDeviceState | None:
        """Get current state for a device."""
        return self._states.get(device_id)

    def register_observer(self, observer: IStateObserver) -> None:
        """Register a state change observer."""
        if observer not in self._observers:
            self._observers.append(observer)

    def unregister_observer(self, observer: IStateObserver) -> None:
        """Unregister a state change observer."""
        if observer in self._observers:
            self._observers.remove(observer)

    def _notify_observers(self, device_id: str, state: GoveeDeviceState) -> None:
        """Notify all observers of state change."""
        for observer in self._observers:
            try:
                observer.on_state_changed(device_id, state)
            except Exception as err:
                _LOGGER.warning("Observer notification failed: %s", err)

    async def async_setup(self) -> None:
        """Set up the coordinator - discover devices and start MQTT.

        Should be called once during integration setup.
        """
        # Discover devices
        await self._discover_devices()

        # Start MQTT client if credentials available
        if self._iot_credentials:
            await self._start_mqtt()

    async def _discover_devices(self) -> None:
        """Discover all devices from Govee API."""
        try:
            devices = await self._api_client.get_devices()

            for device in devices:
                _LOGGER.debug(
                    "Device: %s (%s) type=%s is_group=%s",
                    device.name,
                    device.device_id,
                    device.device_type,
                    device.is_group,
                )
                # Log capabilities for debugging segment issues
                for cap in device.capabilities:
                    _LOGGER.debug(
                        "  Capability: type=%s instance=%s params=%s",
                        cap.type,
                        cap.instance,
                        cap.parameters,
                    )

                # Filter group devices unless enabled
                if device.is_group and not self._enable_groups:
                    _LOGGER.debug("Skipping group device: %s", device.name)
                    continue

                self._devices[device.device_id] = device
                # Create empty state for each device
                self._states[device.device_id] = GoveeDeviceState.create_empty(
                    device.device_id
                )

            _LOGGER.info(
                "Discovered %d Govee devices (enable_groups=%s)",
                len(self._devices),
                self._enable_groups,
            )

            # Clear any auth issues on success
            await async_delete_auth_issue(self.hass, self._config_entry)

        except GoveeAuthError as err:
            # Create repair issue for auth failure
            await async_create_auth_issue(self.hass, self._config_entry)
            raise ConfigEntryAuthFailed("Invalid API key") from err
        except GoveeApiError as err:
            raise UpdateFailed(f"Failed to discover devices: {err}") from err

    async def _start_mqtt(self) -> None:
        """Start MQTT client for real-time updates."""
        if not self._iot_credentials:
            return

        self._mqtt_client = GoveeAwsIotClient(
            credentials=self._iot_credentials,
            on_state_update=self._on_mqtt_state_update,
        )

        if self._mqtt_client.available:
            try:
                await self._mqtt_client.async_start()
                _LOGGER.info("MQTT client started for real-time updates")
                # Clear any MQTT issues on success
                await async_delete_mqtt_issue(self.hass, self._config_entry)
            except Exception as err:
                _LOGGER.warning("MQTT client failed to start: %s", err)
                await async_create_mqtt_issue(
                    self.hass,
                    self._config_entry,
                    str(err),
                )
        else:
            _LOGGER.warning("MQTT library not available")

    def _on_mqtt_state_update(self, device_id: str, state_data: dict[str, Any]) -> None:
        """Handle state update from MQTT.

        This is called from the MQTT client when a state message is received.
        Updates internal state and notifies observers.
        """
        if device_id not in self._devices:
            _LOGGER.debug("MQTT update for unknown device: %s", device_id)
            return

        state = self._states.get(device_id)
        if state is None:
            state = GoveeDeviceState.create_empty(device_id)
            self._states[device_id] = state

        # Update state from MQTT data
        state.update_from_mqtt(state_data)

        # Update coordinator data and notify HA
        self.async_set_updated_data(self._states)

        # Notify observers
        self._notify_observers(device_id, state)

        _LOGGER.debug(
            "MQTT state applied for %s: power=%s",
            device_id,
            state.power_state,
        )

    async def _async_update_data(self) -> dict[str, GoveeDeviceState]:
        """Fetch state for all devices (parallel).

        Called by DataUpdateCoordinator on poll interval.
        """
        if not self._devices:
            return self._states

        # Create tasks for parallel fetching
        tasks = [
            self._fetch_device_state(device_id, device)
            for device_id, device in self._devices.items()
        ]

        # Wait for all with timeout
        try:
            async with asyncio.timeout(STATE_FETCH_TIMEOUT):
                results = await asyncio.gather(*tasks, return_exceptions=True)
        except TimeoutError:
            _LOGGER.warning("State fetch timed out after %ds", STATE_FETCH_TIMEOUT)
            return self._states

        # Process results
        successful_updates = 0
        for device_id, result in zip(self._devices.keys(), results):
            if isinstance(result, GoveeDeviceState):
                self._states[device_id] = result
                successful_updates += 1
            elif isinstance(result, GoveeAuthError):
                await async_create_auth_issue(self.hass, self._config_entry)
                raise ConfigEntryAuthFailed("Invalid API key") from result
            elif isinstance(result, Exception):
                _LOGGER.debug(
                    "Failed to fetch state for %s: %s",
                    device_id,
                    result,
                )
                # Keep previous state on error

        # Clear rate limit issue if we got successful updates
        if successful_updates > 0 and self._rate_limited:
            self._rate_limited = False
            await async_delete_rate_limit_issue(self.hass, self._config_entry)

        return self._states

    async def _fetch_device_state(
        self,
        device_id: str,
        device: GoveeDevice,
    ) -> GoveeDeviceState | Exception:
        """Fetch state for a single device.

        Args:
            device_id: Device identifier.
            device: Device instance.

        Returns:
            GoveeDeviceState or Exception on error.
        """
        # Skip API call for group devices - state fetch always fails with 400
        if device.is_group:
            existing = self._states.get(device_id)
            if existing:
                existing.online = True  # Group devices are always "available"
                return existing
            return GoveeDeviceState.create_empty(device_id)

        try:
            state = await self._api_client.get_device_state(device_id, device.sku)

            # Preserve optimistic state for scenes (API doesn't return active scene)
            # But clear scene if device was turned off
            existing_state = self._states.get(device_id)
            if existing_state and existing_state.active_scene:
                if state.power_state:
                    # Device is still on, preserve the scene
                    state.active_scene = existing_state.active_scene
                else:
                    # Device is off, clear the scene
                    _LOGGER.debug("Clearing scene for %s (device turned off)", device_id)

            return state

        except GoveeDeviceNotFoundError:
            # Expected for group devices - use existing/optimistic state
            _LOGGER.debug("State query failed for group device %s [expected]", device_id)
            existing = self._states.get(device_id)
            if existing:
                existing.online = True  # Group devices are always "available"
                return existing
            return GoveeDeviceState.create_empty(device_id)

        except GoveeRateLimitError as err:
            _LOGGER.warning("Rate limit hit, keeping previous state")
            # Create rate limit repair issue (only once)
            if not self._rate_limited:
                self._rate_limited = True
                reset_time = "unknown"
                if err.retry_after:
                    reset_time = f"{int(err.retry_after)} seconds"
                self.hass.async_create_task(
                    async_create_rate_limit_issue(
                        self.hass,
                        self._config_entry,
                        reset_time,
                    )
                )
            existing = self._states.get(device_id)
            return existing if existing else GoveeDeviceState.create_empty(device_id)

        except Exception as err:
            return err

    async def async_control_device(
        self,
        device_id: str,
        command: DeviceCommand,
    ) -> bool:
        """Send control command to device with optimistic update.

        Args:
            device_id: Device identifier.
            command: Command to execute.

        Returns:
            True if command succeeded.
        """
        device = self._devices.get(device_id)
        if not device:
            _LOGGER.error("Unknown device: %s", device_id)
            return False

        try:
            success = await self._api_client.control_device(
                device_id,
                device.sku,
                command,
            )

            if success:
                # Apply optimistic update
                self._apply_optimistic_update(device_id, command)
                self.async_set_updated_data(self._states)

            return success

        except GoveeAuthError as err:
            raise ConfigEntryAuthFailed("Invalid API key") from err
        except GoveeApiError as err:
            _LOGGER.error("Control command failed: %s", err)
            return False

    def _apply_optimistic_update(
        self,
        device_id: str,
        command: DeviceCommand,
    ) -> None:
        """Apply optimistic state update based on command."""
        state = self._states.get(device_id)
        if not state:
            return

        # Import here to avoid circular dependency
        from .models.commands import (
            BrightnessCommand,
            ColorCommand,
            ColorTempCommand,
            PowerCommand,
            SceneCommand,
        )

        if isinstance(command, PowerCommand):
            state.apply_optimistic_power(command.power_on)
        elif isinstance(command, BrightnessCommand):
            state.apply_optimistic_brightness(command.brightness)
        elif isinstance(command, ColorCommand):
            state.apply_optimistic_color(command.color)
        elif isinstance(command, ColorTempCommand):
            state.apply_optimistic_color_temp(command.kelvin)
        elif isinstance(command, SceneCommand):
            state.apply_optimistic_scene(str(command.scene_id))

    async def async_get_scenes(
        self,
        device_id: str,
        refresh: bool = False,
    ) -> list[dict[str, Any]]:
        """Get available scenes for a device.

        Args:
            device_id: Device identifier.
            refresh: Force refresh from API.

        Returns:
            List of scene definitions.
        """
        if not refresh and device_id in self._scene_cache:
            return self._scene_cache[device_id]

        device = self._devices.get(device_id)
        if not device:
            return []

        try:
            scenes = await self._api_client.get_dynamic_scenes(device_id, device.sku)
            self._scene_cache[device_id] = scenes
            return scenes
        except GoveeApiError as err:
            _LOGGER.warning("Failed to fetch scenes for %s: %s", device_id, err)
            return self._scene_cache.get(device_id, [])

    async def async_get_diy_scenes(
        self,
        device_id: str,
        refresh: bool = False,
    ) -> list[dict[str, Any]]:
        """Get available DIY scenes for a device.

        Args:
            device_id: Device identifier.
            refresh: Force refresh from API.

        Returns:
            List of DIY scene definitions.
        """
        if not refresh and device_id in self._diy_scene_cache:
            return self._diy_scene_cache[device_id]

        device = self._devices.get(device_id)
        if not device:
            return []

        try:
            scenes = await self._api_client.get_diy_scenes(device_id, device.sku)
            self._diy_scene_cache[device_id] = scenes
            return scenes
        except GoveeApiError as err:
            _LOGGER.warning("Failed to fetch DIY scenes for %s: %s", device_id, err)
            return self._diy_scene_cache.get(device_id, [])

    async def async_shutdown(self) -> None:
        """Shutdown coordinator and cleanup resources."""
        if self._mqtt_client:
            await self._mqtt_client.async_stop()
            self._mqtt_client = None

        await self._api_client.close()
