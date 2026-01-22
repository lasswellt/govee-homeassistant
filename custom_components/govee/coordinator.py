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
from .api.auth import GoveeAuthClient
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

        # Light effect library cache {sku: library_data}
        # Contains speed support info for regular scenes
        self._light_effect_cache: dict[str, dict[str, Any]] = {}

        # Observers for state changes
        self._observers: list[IStateObserver] = []

        # MQTT client for real-time updates
        self._mqtt_client: GoveeAwsIotClient | None = None

        # Device-specific MQTT topics from undocumented API
        # Maps device_id -> MQTT topic for publishing commands
        self._device_topics: dict[str, str] = {}

        # Track rate limit state to avoid spamming repair issues
        self._rate_limited: bool = False

    @property
    def devices(self) -> dict[str, GoveeDevice]:
        """Get all discovered devices."""
        return self._devices

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

            # Pre-populate scene cache for devices with scene capabilities
            # This ensures scene entities are created on initial setup
            _LOGGER.debug("Pre-populating scene cache for %d devices", len(self._devices))
            for device_id, device in self._devices.items():
                if device.supports_scenes:
                    try:
                        scenes = await self._api_client.get_dynamic_scenes(device_id, device.sku)
                        self._scene_cache[device_id] = scenes
                        _LOGGER.debug("Cached %d scenes for %s", len(scenes), device.name)
                    except GoveeApiError as err:
                        _LOGGER.warning("Failed to pre-fetch scenes for %s: %s", device.name, err)
                        self._scene_cache[device_id] = []

                if device.supports_diy_scenes:
                    try:
                        diy_scenes = await self._api_client.get_diy_scenes(device_id, device.sku)
                        self._diy_scene_cache[device_id] = diy_scenes
                        _LOGGER.debug("Cached %d DIY scenes for %s", len(diy_scenes), device.name)
                    except GoveeApiError as err:
                        _LOGGER.warning("Failed to pre-fetch DIY scenes for %s: %s", device.name, err)
                        self._diy_scene_cache[device_id] = []

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

    async def async_send_diy_speed(self, device_id: str, speed: int) -> bool:
        """Send DIY scene speed via BLE passthrough.

        Sends a ptReal MQTT command to adjust DIY scene playback speed.
        This feature requires MQTT connection as there is no REST API fallback.

        IMPORTANT: The speed command includes the animation style byte, which
        must match the active DIY scene's style. If the style doesn't match,
        the device will ignore the speed command. The style is tracked in
        the device state when a DIY scene or style is selected.

        Args:
            device_id: Device identifier.
            speed: Playback speed 0-100 (0 = static, 100 = fastest).

        Returns:
            True if command was sent successfully.
        """
        if not self.mqtt_connected:
            _LOGGER.warning(
                "Cannot send DIY speed for %s: MQTT not connected",
                device_id,
            )
            return False

        device = self._devices.get(device_id)
        if not device:
            _LOGGER.error("Unknown device for DIY speed: %s", device_id)
            return False

        # Get the current style value from state (critical for speed command to work)
        state = self._states.get(device_id)
        style_value = 0  # Default to Fade
        if state and state.diy_style_value is not None:
            style_value = state.diy_style_value
            _LOGGER.debug(
                "Using tracked style %d (%s) for speed command",
                style_value,
                state.diy_style or "unknown",
            )

        # Build and send BLE packet
        from .api.ble_packet import build_diy_speed_packet, encode_packet_base64

        packet = build_diy_speed_packet(speed, style_value)
        encoded = encode_packet_base64(packet)

        # Get device-specific MQTT topic for publishing
        device_topic = self._device_topics.get(device_id)

        if self._mqtt_client is None:
            return False

        success = await self._mqtt_client.async_publish_ptreal(
            device_id,
            device.sku,
            encoded,
            device_topic,
        )

        if success:
            # Apply optimistic update to state
            if state:
                state.diy_speed = speed
                state.source = "optimistic"
            _LOGGER.debug(
                "Sent DIY speed %d (style=%d) to %s",
                speed,
                style_value,
                device.name,
            )

        return success

    async def async_send_diy_style(self, device_id: str, style: str, speed: int = 50) -> bool:
        """Send DIY animation style via BLE passthrough.

        Sends a ptReal MQTT command to set the DIY scene animation style.
        This feature requires MQTT connection as there is no REST API fallback.

        Args:
            device_id: Device identifier.
            style: Animation style name (Fade, Jumping, Flicker, Marquee, Music).
            speed: Playback speed 0-100 (default 50).

        Returns:
            True if command was sent successfully.
        """
        if not self.mqtt_connected:
            _LOGGER.warning(
                "Cannot send DIY style for %s: MQTT not connected",
                device_id,
            )
            return False

        device = self._devices.get(device_id)
        if not device:
            _LOGGER.error("Unknown device for DIY style: %s", device_id)
            return False

        # Build and send BLE packet
        from .api.ble_packet import DIY_STYLE_NAMES, build_diy_style_packet, encode_packet_base64

        style_value = DIY_STYLE_NAMES.get(style)
        if style_value is None:
            _LOGGER.error("Unknown DIY style: %s", style)
            return False

        packet = build_diy_style_packet(style_value, speed)
        encoded = encode_packet_base64(packet)

        # Get device-specific MQTT topic for publishing
        device_topic = self._device_topics.get(device_id)

        if self._mqtt_client is None:
            return False

        success = await self._mqtt_client.async_publish_ptreal(
            device_id,
            device.sku,
            encoded,
            device_topic,
        )

        if success:
            # Apply optimistic update to state
            state = self._states.get(device_id)
            if state:
                state.apply_optimistic_diy_style(style, int(style_value))
                state.diy_speed = speed
            _LOGGER.debug("Sent DIY style '%s' (speed=%d) to %s", style, speed, device.name)

        return success

    async def async_send_music_mode(self, device_id: str, enabled: bool, sensitivity: int = 50) -> bool:
        """Send music mode command via BLE passthrough.

        Sends a ptReal MQTT command to enable/disable music reactive mode.
        This feature requires MQTT connection as there is no REST API fallback.

        Args:
            device_id: Device identifier.
            enabled: True to enable music mode, False to disable.
            sensitivity: Microphone sensitivity 0-100 (default 50).

        Returns:
            True if command was sent successfully.
        """
        if not self.mqtt_connected:
            _LOGGER.warning(
                "Cannot send music mode for %s: MQTT not connected",
                device_id,
            )
            return False

        device = self._devices.get(device_id)
        if not device:
            _LOGGER.error("Unknown device for music mode: %s", device_id)
            return False

        # Build and send BLE packet
        from .api.ble_packet import build_music_mode_packet, encode_packet_base64

        packet = build_music_mode_packet(enabled, sensitivity)
        encoded = encode_packet_base64(packet)

        # Get device-specific MQTT topic for publishing
        device_topic = self._device_topics.get(device_id)

        if self._mqtt_client is None:
            return False

        success = await self._mqtt_client.async_publish_ptreal(
            device_id,
            device.sku,
            encoded,
            device_topic,
        )

        if success:
            # Apply optimistic update to state
            state = self._states.get(device_id)
            if state:
                state.apply_optimistic_music_mode(enabled)
            _LOGGER.debug(
                "Sent music mode %s (sensitivity=%d) to %s",
                "ON" if enabled else "OFF",
                sensitivity,
                device.name,
            )

        return success

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
            ModeCommand,
            MusicModeCommand,
            PowerCommand,
            SceneCommand,
        )
        from .models.device import INSTANCE_HDMI_SOURCE

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
        elif isinstance(command, ModeCommand):
            if command.mode_instance == INSTANCE_HDMI_SOURCE:
                state.apply_optimistic_hdmi_source(command.value)
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
            cached_scenes = self._scene_cache[device_id]
            _LOGGER.debug(
                "Returning %d cached scenes for %s",
                len(cached_scenes),
                device_id,
            )
            return cached_scenes

        device = self._devices.get(device_id)
        if not device:
            _LOGGER.warning("Device %s not found in coordinator for scene fetch", device_id)
            return []

        _LOGGER.debug(
            "Fetching scenes from API for %s (sku=%s)",
            device.name,
            device.sku,
        )

        try:
            scenes = await self._api_client.get_dynamic_scenes(device_id, device.sku)
            self._scene_cache[device_id] = scenes
            _LOGGER.info(
                "Fetched and cached %d scenes for %s",
                len(scenes),
                device.name,
            )
            return scenes
        except GoveeApiError as err:
            _LOGGER.error(
                "API error fetching scenes for %s: %s",
                device.name,
                err,
            )
            # Return cached scenes if available, otherwise empty list
            cached = self._scene_cache.get(device_id, [])
            _LOGGER.debug("Returning %d cached scenes after error", len(cached))
            return cached

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
            cached_scenes = self._diy_scene_cache[device_id]
            _LOGGER.debug(
                "Returning %d cached DIY scenes for %s",
                len(cached_scenes),
                device_id,
            )
            return cached_scenes

        device = self._devices.get(device_id)
        if not device:
            _LOGGER.warning("Device %s not found in coordinator for DIY scene fetch", device_id)
            return []

        _LOGGER.debug(
            "Fetching DIY scenes from API for %s (sku=%s)",
            device.name,
            device.sku,
        )

        try:
            scenes = await self._api_client.get_diy_scenes(device_id, device.sku)
            self._diy_scene_cache[device_id] = scenes
            _LOGGER.info(
                "Fetched and cached %d DIY scenes for %s",
                len(scenes),
                device.name,
            )
            return scenes
        except GoveeApiError as err:
            _LOGGER.error(
                "API error fetching DIY scenes for %s: %s",
                device.name,
                err,
            )
            # Return cached scenes if available, otherwise empty list
            cached = self._diy_scene_cache.get(device_id, [])
            _LOGGER.debug("Returning %d cached DIY scenes after error", len(cached))
            return cached

    async def async_get_light_effect_library(
        self,
        device_id: str,
        refresh: bool = False,
    ) -> dict[str, Any]:
        """Get light effect library for a device.

        The light effect library contains information about scene speed support,
        including whether the device supports speed control and per-scene speed ranges.

        Args:
            device_id: Device identifier.
            refresh: Force refresh from API.

        Returns:
            Light effect library data with structure:
            {
                "support_speed": 0 or 1,
                "scenes": [...],
            }
        """
        device = self._devices.get(device_id)
        if not device:
            _LOGGER.warning("Device %s not found for light effect library fetch", device_id)
            return {}

        sku = device.sku

        # Check cache (keyed by SKU since library is per-model)
        if not refresh and sku in self._light_effect_cache:
            _LOGGER.debug("Returning cached light effect library for %s", sku)
            return self._light_effect_cache[sku]

        # Need IoT credentials to access undocumented API
        if not self._iot_credentials:
            _LOGGER.debug("No IoT credentials, cannot fetch light effect library")
            return {}

        _LOGGER.debug("Fetching light effect library for %s (sku=%s)", device.name, sku)

        try:
            async with GoveeAuthClient() as auth_client:
                library = await auth_client.fetch_light_effect_library(
                    self._iot_credentials.token,
                    sku,
                )
                self._light_effect_cache[sku] = library
                _LOGGER.info(
                    "Fetched light effect library for %s: support_speed=%s",
                    sku,
                    library.get("support_speed", 0),
                )
                return library

        except GoveeApiError as err:
            _LOGGER.warning("Failed to fetch light effect library for %s: %s", sku, err)
            # Return cached data if available
            return self._light_effect_cache.get(sku, {})
        except Exception as err:
            _LOGGER.warning("Unexpected error fetching light effect library: %s", err)
            return self._light_effect_cache.get(sku, {})

    def supports_scene_speed(self, device_id: str) -> bool:
        """Check if a device supports scene speed control.

        Args:
            device_id: Device identifier.

        Returns:
            True if device supports scene speed control.
        """
        device = self._devices.get(device_id)
        if not device:
            return False

        library = self._light_effect_cache.get(device.sku, {})
        support_speed: int = library.get("support_speed", 0)
        return support_speed == 1

    def get_scene_speed_range(self, device_id: str) -> tuple[int, int] | None:
        """Get the speed range for scene speed control.

        Args:
            device_id: Device identifier.

        Returns:
            Tuple of (min, max) speed values, or None if not supported.
        """
        device = self._devices.get(device_id)
        if not device:
            return None

        library = self._light_effect_cache.get(device.sku, {})
        if library.get("support_speed", 0) != 1:
            return None

        # Default range if not specified
        return (1, 100)

    async def async_send_scene_speed(self, device_id: str, speed: int) -> bool:
        """Send regular scene speed via BLE passthrough.

        Sends a ptReal MQTT command to adjust scene playback speed.
        This feature requires MQTT connection as there is no REST API fallback.

        Unlike DIY scene speed, regular scene speed doesn't require matching
        the animation style byte.

        Args:
            device_id: Device identifier.
            speed: Playback speed (typically 1-100, lower = slower).

        Returns:
            True if command was sent successfully.
        """
        if not self.mqtt_connected:
            _LOGGER.warning(
                "Cannot send scene speed for %s: MQTT not connected",
                device_id,
            )
            return False

        device = self._devices.get(device_id)
        if not device:
            _LOGGER.error("Unknown device for scene speed: %s", device_id)
            return False

        # Build and send BLE packet
        from .api.ble_packet import build_scene_speed_packet, encode_packet_base64

        packet = build_scene_speed_packet(speed)
        encoded = encode_packet_base64(packet)

        # Get device-specific MQTT topic for publishing
        device_topic = self._device_topics.get(device_id)

        if self._mqtt_client is None:
            return False

        success = await self._mqtt_client.async_publish_ptreal(
            device_id,
            device.sku,
            encoded,
            device_topic,
        )

        if success:
            # Apply optimistic update to state
            state = self._states.get(device_id)
            if state:
                state.apply_optimistic_scene_speed(speed)
            _LOGGER.debug("Sent scene speed %d to %s", speed, device.name)

        return success

    async def async_shutdown(self) -> None:
        """Shutdown coordinator and cleanup resources."""
        if self._mqtt_client:
            await self._mqtt_client.async_stop()
            self._mqtt_client = None

        await self._api_client.close()
