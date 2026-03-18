"""DataUpdateCoordinator for Govee integration.

Manages device discovery, state polling, and MQTT integration.
Implements IStateProvider protocol for clean architecture.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
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
from .api.auth import GoveeAuthClient
from .api.ble_packet import DIY_STYLE_NAMES
from .ble_passthrough import BlePassthroughManager
from .const import DOMAIN
from .models import GoveeDevice, GoveeDeviceState, RGBColor
from .models.commands import (
    BrightnessCommand,
    ColorCommand,
    ColorTempCommand,
    DeviceCommand,
    DIYSceneCommand,
    ModeCommand,
    MusicModeCommand,
    PowerCommand,
    SceneCommand,
    TemperatureSettingCommand,
    ToggleCommand,
    WorkModeCommand,
    create_dreamview_command,
)
from .models.device import (
    INSTANCE_DREAMVIEW,
    INSTANCE_HDMI_SOURCE,
    INSTANCE_THERMOSTAT_TOGGLE,
)
from .protocols import IStateObserver
from .scene_cache import SceneCacheManager
from .repairs import (
    async_create_auth_issue,
    async_create_mqtt_issue,
    async_create_rate_limit_issue,
    async_delete_auth_issue,
    async_delete_mqtt_issue,
    async_delete_rate_limit_issue,
)

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
            config_entry=config_entry,
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

        # Scene cache manager
        self._scene_cache = SceneCacheManager(api_client)

        # Observers for state changes
        self._observers: list[IStateObserver] = []

        # MQTT client for real-time updates
        self._mqtt_client: GoveeAwsIotClient | None = None

        # Device-specific MQTT topics from undocumented API
        # Maps device_id -> MQTT topic for publishing commands
        self._device_topics: dict[str, str] = {}

        # BLE passthrough manager for MQTT-based commands
        self._ble_manager = BlePassthroughManager(
            get_mqtt_client=lambda: self._mqtt_client,
            device_topics=self._device_topics,
            ensure_device_topic=self._ensure_device_topic,
        )

        # Track in-flight power-off commands so segment entities can
        # avoid racing with a concurrent device power-off (issue #16).
        self._pending_power_off: set[str] = set()

        # Track rate limit state to avoid spamming repair issues
        self._rate_limited: bool = False

        # Store original poll interval for restoring after rate limit backoff
        self._original_update_interval = timedelta(seconds=poll_interval)

    @property
    def devices(self) -> dict[str, GoveeDevice]:
        """Get all discovered devices."""
        return self._devices

    @property
    def api_rate_limit_remaining(self) -> int:
        """Return API rate limit remaining."""
        return self._api_client.rate_limit_remaining

    @property
    def api_rate_limit_total(self) -> int:
        """Return API rate limit total."""
        return self._api_client.rate_limit_total

    @property
    def api_rate_limit_reset(self) -> int:
        """Return API rate limit reset time."""
        return self._api_client.rate_limit_reset

    @property
    def mqtt_client(self) -> GoveeAwsIotClient | None:
        """Return MQTT client instance."""
        return self._mqtt_client

    @property
    def scene_cache_count(self) -> int:
        """Return number of devices with cached scenes."""
        return self._scene_cache.scene_cache_count

    @property
    def diy_scene_cache_count(self) -> int:
        """Return number of devices with cached DIY scenes."""
        return self._scene_cache.diy_scene_cache_count

    @property
    def mqtt_connected(self) -> bool:
        """Return True if MQTT client is connected."""
        return self._mqtt_client is not None and self._mqtt_client.connected

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

    def is_power_off_pending(self, device_id: str) -> bool:
        """Return True if a power-off command is in flight for this device.

        Segment entities use this to avoid racing with a concurrent power-off.
        """
        return device_id in self._pending_power_off

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

    async def _async_setup(self) -> None:
        """Set up the coordinator - discover devices and start MQTT.

        Called automatically by async_config_entry_first_refresh().
        """
        # Discover devices
        await self._discover_devices()

        # Start MQTT client if credentials available
        if self._iot_credentials:
            await self._start_mqtt()
            # Fetch device-specific MQTT topics for publishing commands
            await self._fetch_device_topics()

    async def _discover_devices(self) -> None:
        """Discover all devices from Govee API."""
        try:
            devices = await self._api_client.get_devices()

            _LOGGER.info(
                "API returned %d devices (enable_groups=%s)",
                len(devices),
                self._enable_groups,
            )

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
                    _LOGGER.info(
                        "Skipping group device: %s (device_id=%s) because enable_groups=False",
                        device.name,
                        device.device_id,
                    )
                    continue

                _LOGGER.debug("Adding device to coordinator: %s", device.device_id)
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

            # Clean up scene caches for devices no longer discovered
            self._scene_cache.cleanup_stale(set(self._devices))

            # Scene cache is populated lazily via async_get_scenes() / async_get_diy_scenes()
            # during entity setup, avoiding rate limit pressure at startup

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

    async def _fetch_device_topics(self) -> None:
        """Fetch device-specific MQTT topics from undocumented Govee API.

        These topics are required for publishing commands (ptReal, etc).
        Device targeting via payload alone doesn't work - AWS IoT requires
        publishing to the device's specific topic.
        """
        if not self._iot_credentials:
            return

        try:
            async with GoveeAuthClient() as auth_client:
                self._device_topics = await auth_client.fetch_device_topics(
                    self._iot_credentials.token
                )
                _LOGGER.info(
                    "Fetched MQTT topics for %d devices",
                    len(self._device_topics),
                )
        except GoveeApiError as err:
            _LOGGER.warning("Failed to fetch device topics: %s", err)
            # Continue without device topics - ptReal commands won't work
            # but the integration can still function with polling
        except Exception as err:
            _LOGGER.warning("Unexpected error fetching device topics: %s", err)

    @callback
    def _on_mqtt_state_update(self, device_id: str, state_data: dict[str, Any]) -> None:
        """Handle state update from MQTT.

        Called from aiomqtt's async message loop, which runs on the HA event
        loop. Safe to call async_set_updated_data() directly. The @callback
        decorator documents this event-loop-only contract.
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

        # Scale timeout based on device count (2s per device, min 30s, max 120s)
        timeout = min(max(STATE_FETCH_TIMEOUT, len(self._devices) * 2), 120)

        # Wait for all with timeout
        try:
            async with asyncio.timeout(timeout):
                results = await asyncio.gather(*tasks, return_exceptions=True)
        except TimeoutError:
            _LOGGER.warning("State fetch timed out after %ds", timeout)
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

        # Clear rate limit issue and restore poll interval if we got successful updates
        if successful_updates > 0 and self._rate_limited:
            self._rate_limited = False
            self.update_interval = self._original_update_interval
            _LOGGER.info(
                "Rate limit cleared, restoring poll interval to %s",
                self._original_update_interval,
            )
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

            # Preserve optimistic state fields that API doesn't reliably return.
            # Clear them when device is turned off (no longer active).
            existing_state = self._states.get(device_id)
            if existing_state:
                # Scenes persist on device across power cycles — always preserve
                if existing_state.active_scene:
                    state.active_scene = existing_state.active_scene
                if existing_state.active_scene_name:
                    state.active_scene_name = existing_state.active_scene_name
                # DIY scenes also persist across power cycles
                if existing_state.active_diy_scene:
                    state.active_diy_scene = existing_state.active_diy_scene
                # Preserve restore-target fields across API polls.
                # These are "memory" fields — always preserved regardless of power state.
                if existing_state.last_color is not None:
                    state.last_color = existing_state.last_color
                if existing_state.last_color_temp_kelvin is not None:
                    state.last_color_temp_kelvin = existing_state.last_color_temp_kelvin
                if existing_state.last_scene_id is not None:
                    state.last_scene_id = existing_state.last_scene_id
                if existing_state.last_scene_name is not None:
                    state.last_scene_name = existing_state.last_scene_name

                # Heater state: preserve across polls (API doesn't reliably return these)
                if existing_state.heater_temperature is not None:
                    state.heater_temperature = existing_state.heater_temperature
                if existing_state.heater_auto_stop is not None:
                    state.heater_auto_stop = existing_state.heater_auto_stop

                self._preserve_optimistic_field(
                    existing_state, state, device_id, "dreamview_enabled", "DreamView"
                )
                # Music mode has extra fields to preserve alongside the flag
                if existing_state.music_mode_enabled:
                    if state.power_state:
                        state.music_mode_enabled = existing_state.music_mode_enabled
                        state.music_mode_value = existing_state.music_mode_value
                        state.music_mode_name = existing_state.music_mode_name
                        state.music_sensitivity = existing_state.music_sensitivity
                    else:
                        _LOGGER.debug(
                            "Clearing music mode for %s (device turned off)",
                            device_id,
                        )

            return state

        except GoveeDeviceNotFoundError:
            # Expected for group devices - use existing/optimistic state
            _LOGGER.debug(
                "State query failed for group device %s [expected]", device_id
            )
            existing = self._states.get(device_id)
            if existing:
                existing.online = True  # Group devices are always "available"
                return existing
            return GoveeDeviceState.create_empty(device_id)

        except GoveeRateLimitError as err:
            _LOGGER.warning("Rate limit hit, keeping previous state")
            # Create rate limit repair issue and back off (only once)
            if not self._rate_limited:
                self._rate_limited = True
                reset_time = "unknown"
                # Back off: increase poll interval to retry_after or 120s
                backoff_seconds = int(err.retry_after) if err.retry_after else 120
                self.update_interval = timedelta(seconds=backoff_seconds)
                _LOGGER.warning(
                    "Rate limited, increasing poll interval to %ds",
                    backoff_seconds,
                )
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

        # Track power-off commands so segment entities can detect them
        # before the first await, ensuring concurrent coroutines see the flag.
        is_power_off = isinstance(command, PowerCommand) and not command.power_on
        if is_power_off:
            self._pending_power_off.add(device_id)

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
        finally:
            if is_power_off:
                self._pending_power_off.discard(device_id)

    async def _ensure_device_topic(self, device_id: str) -> str | None:
        """Get device MQTT topic, refreshing if needed.

        If the topic is missing for this device but we have credentials,
        attempt a single refresh from the API.
        """
        topic = self._device_topics.get(device_id)
        if topic is not None:
            return topic

        # Topic missing - try one refresh
        if self._iot_credentials:
            _LOGGER.debug("Device topic missing for %s, refreshing from API", device_id)
            await self._fetch_device_topics()
            topic = self._device_topics.get(device_id)
            if topic:
                _LOGGER.debug("Got device topic for %s after refresh", device_id)

        return topic

    async def async_send_music_mode(
        self,
        device_id: str,
        enabled: bool,
        sensitivity: int = 50,
        music_mode: int = 1,
        last_scene_id: str | None = None,
        last_scene_name: str | None = None,
    ) -> bool:
        """Send music mode command via REST API first, with BLE fallback.

        Tries REST API for devices with STRUCT music mode capability,
        then falls back to BLE passthrough via MQTT.

        Args:
            device_id: Device identifier.
            enabled: True to enable music mode, False to disable.
            sensitivity: Microphone sensitivity 0-100 (default 50).
            music_mode: Music mode value (default 1 = Rhythm).
            last_scene_id: Last active scene ID (for restoring on disable).
            last_scene_name: Last active scene name (for restoring on disable).

        Returns:
            True if command was sent successfully.
        """
        device = self._devices.get(device_id)
        if not device:
            _LOGGER.error("Unknown device for music mode: %s", device_id)
            return False

        # Try REST API first for devices with STRUCT music mode capability
        if device.has_struct_music_mode:
            if enabled:
                try:
                    command = MusicModeCommand(
                        music_mode=music_mode,
                        sensitivity=sensitivity,
                        auto_color=1,
                    )
                    success = await self.async_control_device(device_id, command)
                    if success:
                        _LOGGER.debug(
                            "Sent music mode ON to %s via REST API", device.name
                        )
                        return True
                except ConfigEntryAuthFailed:
                    raise
                except Exception as err:
                    _LOGGER.debug(
                        "REST music mode ON failed for %s: %s, trying BLE",
                        device.name,
                        err,
                    )
            else:
                # Disable music mode via REST: restore last scene or send brightness
                success = await self._rest_disable_music_mode(
                    device_id, last_scene_id, last_scene_name
                )
                if success:
                    return True

        # Fall back to BLE passthrough via MQTT
        if not self._ble_manager.available:
            _LOGGER.warning(
                "Cannot send music mode for %s: MQTT not connected",
                device_id,
            )
            return False

        success = await self._ble_manager.async_send_music_mode(
            device_id, device.sku, enabled, sensitivity
        )

        if success:
            # Apply optimistic update to state
            state = self._states.get(device_id)
            if state:
                state.apply_optimistic_music_mode(enabled)
            _LOGGER.debug(
                "Sent music mode %s (sensitivity=%d) to %s via BLE",
                "ON" if enabled else "OFF",
                sensitivity,
                device.name,
            )

        return success

    async def _rest_disable_music_mode(
        self,
        device_id: str,
        last_scene_id: str | None = None,
        last_scene_name: str | None = None,
    ) -> bool:
        """Disable music mode via REST API.

        Tries to restore the last active scene, then falls back to a
        brightness command to cleanly exit music mode.

        Args:
            device_id: Device identifier.
            last_scene_id: Scene ID to restore.
            last_scene_name: Scene name to restore.

        Returns:
            True if successfully disabled via REST.
        """
        device = self._devices.get(device_id)
        success = False

        # Try restoring last active scene
        if last_scene_id and last_scene_name:
            command = SceneCommand(
                scene_id=int(last_scene_id),
                scene_name=last_scene_name,
            )
            success = await self.async_control_device(device_id, command)
            if success:
                _LOGGER.debug(
                    "Restored scene '%s' on %s after music mode off",
                    last_scene_name,
                    device.name if device else device_id,
                )

        if not success:
            # No last scene or scene restore failed - send brightness command
            # to cleanly exit music mode via REST (avoids visible power cycle)
            state = self._states.get(device_id)
            brightness = state.brightness if state and state.brightness else 100
            success = await self.async_control_device(
                device_id, BrightnessCommand(brightness=brightness)
            )
            _LOGGER.debug(
                "Sent brightness command to %s to exit music mode (brightness=%d)",
                device.name if device else device_id,
                brightness,
            )

        if success:
            self.clear_music_mode(device_id)

        return success

    async def async_send_dreamview(self, device_id: str, enabled: bool) -> bool:
        """Send DreamView command via REST API, with BLE fallback.

        Args:
            device_id: Device identifier.
            enabled: True to enable DreamView, False to disable.

        Returns:
            True if command was sent successfully.
        """
        device = self._devices.get(device_id)
        if not device:
            _LOGGER.error("Unknown device for DreamView: %s", device_id)
            return False

        # Try REST API first (works for HTTP-capable devices like H6097)
        try:
            success = await self.async_control_device(
                device_id, create_dreamview_command(enabled)
            )
            if success:
                _LOGGER.debug(
                    "Sent DreamView %s to %s via REST API",
                    "ON" if enabled else "OFF",
                    device.name,
                )
                return True
        except ConfigEntryAuthFailed:
            # Let authentication errors propagate so Home Assistant can handle reauth
            raise
        except Exception as err:
            _LOGGER.debug("REST DreamView failed for %s: %s", device.name, err)

        # Fall back to BLE passthrough for devices that need it
        if not self._ble_manager.available:
            _LOGGER.warning(
                "Cannot send DreamView for %s: MQTT not connected",
                device_id,
            )
            return False

        success = await self._ble_manager.async_send_dreamview(
            device_id, device.sku, enabled
        )

        if success:
            state = self._states.get(device_id)
            if state:
                state.apply_optimistic_dreamview(enabled)
            _LOGGER.debug(
                "Sent DreamView %s to %s via BLE passthrough",
                "ON" if enabled else "OFF",
                device.name,
            )

        return success

    async def async_send_diy_scene(
        self,
        device_id: str,
        scene_id: int,
        scene_name: str = "",
    ) -> bool:
        """Send DIY scene command via REST API, with BLE fallback.

        Args:
            device_id: Device identifier.
            scene_id: DIY scene ID from the API.
            scene_name: DIY scene name for logging/state.

        Returns:
            True if command was sent successfully.
        """
        device = self._devices.get(device_id)
        if not device:
            _LOGGER.error("Unknown device for DIY scene: %s", device_id)
            return False

        # Try REST API first
        try:
            command = DIYSceneCommand(scene_id=scene_id, scene_name=scene_name)
            success = await self.async_control_device(device_id, command)
            if success:
                _LOGGER.debug(
                    "Activated DIY scene '%s' on %s via REST API",
                    scene_name,
                    device.name,
                )
                return True
            _LOGGER.debug(
                "REST DIY scene returned failure for %s, trying BLE passthrough",
                device.name,
            )
        except ConfigEntryAuthFailed:
            raise
        except Exception as err:
            _LOGGER.debug("REST DIY scene failed for %s: %s", device.name, err)

        # Fall back to BLE passthrough
        if not self._ble_manager.available:
            _LOGGER.warning(
                "Cannot send DIY scene for %s: MQTT not connected",
                device_id,
            )
            return False

        success = await self._ble_manager.async_send_diy_scene(
            device_id, device.sku, scene_id
        )

        if success:
            state = self._states.get(device_id)
            if state:
                state.apply_optimistic_diy_scene(str(scene_id))
            _LOGGER.debug(
                "Activated DIY scene '%s' on %s via BLE passthrough",
                scene_name,
                device.name,
            )

        return success

    async def async_send_diy_style(
        self, device_id: str, style: str, speed: int = 50
    ) -> bool:
        """Send DIY style command via BLE passthrough.

        Note: DIY style changes require complex multi-packet BLE sequences.
        This is a placeholder that applies optimistic state only.
        Full BLE packet implementation is not yet available.

        Args:
            device_id: Device identifier.
            style: DIY style name (Fade, Jumping, Flicker, Marquee, Music).
            speed: Animation speed 0-100 (default 50).

        Returns:
            True if optimistic state was applied.
        """
        device = self._devices.get(device_id)
        if not device:
            _LOGGER.error("Unknown device for DIY style: %s", device_id)
            return False

        style_value = DIY_STYLE_NAMES.get(style)
        if style_value is None:
            _LOGGER.warning("Unknown DIY style: %s", style)
            return False

        _LOGGER.debug(
            "DIY style command for %s is optimistic only - no device command sent. "
            "Full BLE packet implementation is not yet available",
            device.name,
        )

        # Apply optimistic state update
        state = self._states.get(device_id)
        if state:
            state.apply_optimistic_diy_style(style, style_value)

        return False

    @staticmethod
    def _preserve_optimistic_field(
        existing: GoveeDeviceState,
        new: GoveeDeviceState,
        device_id: str,
        field: str,
        label: str,
    ) -> None:
        """Preserve an optimistic state field across API polls.

        If the existing state has a truthy value for the field, preserve it
        on the new state when the device is on. Clear it when the device is off.
        """
        if getattr(existing, field):
            if new.power_state:
                setattr(new, field, getattr(existing, field))
            else:
                _LOGGER.debug(
                    "Clearing %s for %s (device turned off)", label, device_id
                )

    def _apply_optimistic_update(
        self,
        device_id: str,
        command: DeviceCommand,
    ) -> None:
        """Apply optimistic state update based on command."""
        state = self._states.get(device_id)
        if not state:
            return

        if isinstance(command, PowerCommand):
            state.apply_optimistic_power(command.power_on)
        elif isinstance(command, BrightnessCommand):
            state.apply_optimistic_brightness(command.brightness)
        elif isinstance(command, ColorCommand):
            state.apply_optimistic_color(command.color)
        elif isinstance(command, ColorTempCommand):
            state.apply_optimistic_color_temp(command.kelvin)
        elif isinstance(command, SceneCommand):
            state.apply_optimistic_scene(str(command.scene_id), command.scene_name)
        elif isinstance(command, DIYSceneCommand):
            state.apply_optimistic_diy_scene(str(command.scene_id))
        elif isinstance(command, ModeCommand):
            if command.mode_instance == INSTANCE_HDMI_SOURCE:
                state.apply_optimistic_hdmi_source(command.value)
        elif isinstance(command, TemperatureSettingCommand):
            state.heater_temperature = command.temperature
            state.heater_auto_stop = command.auto_stop
        elif isinstance(command, WorkModeCommand):
            state.apply_optimistic_work_mode(command.work_mode, command.mode_value)
        elif isinstance(command, MusicModeCommand):
            # Look up mode name from device capabilities for display
            device = self._devices.get(device_id)
            mode_name = None
            if device:
                for opt in device.get_music_mode_options():
                    if opt.get("value") == command.music_mode:
                        mode_name = opt.get("name")
                        break
            state.apply_optimistic_music_mode_struct(
                command.music_mode,
                command.sensitivity,
                mode_name,
            )
        elif isinstance(command, ToggleCommand):
            # Handle toggle commands (DreamView, night light, thermostat, etc)
            if command.toggle_instance == INSTANCE_DREAMVIEW:
                state.apply_optimistic_dreamview(command.enabled)
            elif command.toggle_instance == INSTANCE_THERMOSTAT_TOGGLE:
                state.heater_auto_stop = 1 if command.enabled else 0

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
        device = self._devices.get(device_id)
        return await self._scene_cache.async_get_scenes(device_id, device, refresh)

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
        device = self._devices.get(device_id)
        return await self._scene_cache.async_get_diy_scenes(device_id, device, refresh)

    async def async_clear_scene(self, device_id: str) -> None:
        """Clear active scene by sending a color/color_temp command to exit it on the device.

        Brightness commands don't exit scenes, so we must send a color or color_temp
        command. Restores the last known color/color_temp when available.
        """
        state = self._states.get(device_id)
        device = self._devices.get(device_id)
        if not state or not device:
            return

        # Nothing to clear if no scene is active
        if not state.active_scene and not state.active_diy_scene:
            self.clear_scene(device_id)
            self.clear_diy_scene(device_id)
            return

        # Resolve the color to restore. Skip RGBColor(0,0,0) — the API returns
        # colorRgb=0 when a scene is running, which is not a meaningful restore target.
        color = state.color or state.last_color
        if color and color.as_packed_int == 0:
            color = state.last_color
        color_temp = state.color_temp_kelvin or state.last_color_temp_kelvin

        success = False
        if color and device.supports_rgb:
            success = await self.async_control_device(
                device_id, ColorCommand(color=color)
            )
        elif color_temp and device.supports_color_temp:
            success = await self.async_control_device(
                device_id, ColorTempCommand(kelvin=color_temp)
            )
        elif device.supports_color_temp:
            # Default to midpoint of device's color temp range
            ct_range = device.color_temp_range
            if ct_range:
                midpoint = (ct_range.min_kelvin + ct_range.max_kelvin) // 2
            else:
                midpoint = 4000
            success = await self.async_control_device(
                device_id, ColorTempCommand(kelvin=midpoint)
            )
        elif device.supports_rgb:
            success = await self.async_control_device(
                device_id, ColorCommand(color=RGBColor(255, 255, 255))
            )

        if success:
            # ColorCommand/ColorTempCommand already clear active_scene via optimistic handlers,
            # but we also need to clear active_diy_scene explicitly.
            self.clear_scene(device_id)
            self.clear_diy_scene(device_id)

    def clear_scene(self, device_id: str) -> None:
        """Clear active scene for a device."""
        state = self._states.get(device_id)
        if state:
            state.active_scene = None
            state.active_scene_name = None
            state.source = "optimistic"

    def clear_diy_scene(self, device_id: str) -> None:
        """Clear active DIY scene for a device."""
        state = self._states.get(device_id)
        if state:
            state.active_diy_scene = None
            state.source = "optimistic"

    def clear_music_mode(self, device_id: str) -> None:
        """Clear music mode state for a device."""
        state = self._states.get(device_id)
        if state:
            state.music_mode_enabled = False
            state.source = "optimistic"

    def restore_group_state(
        self, device_id: str, power: bool, brightness: int | None = None
    ) -> None:
        """Restore state for a group device from HA state machine."""
        state = self._states.get(device_id)
        if state:
            state.power_state = power
            if brightness is not None:
                state.brightness = brightness
            state.source = "optimistic"

    async def async_shutdown(self) -> None:
        """Shutdown coordinator and cleanup resources."""
        if self._mqtt_client:
            await self._mqtt_client.async_stop()
            self._mqtt_client = None

        await self._api_client.close()
