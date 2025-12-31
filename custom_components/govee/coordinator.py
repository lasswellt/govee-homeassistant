"""DataUpdateCoordinator for Govee integration."""
from __future__ import annotations

import asyncio
import logging
from datetime import timedelta
from typing import TYPE_CHECKING, Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.helpers import issue_registry as ir
from homeassistant.exceptions import ConfigEntryAuthFailed

from .api import GoveeApiClient, GoveeApiError, GoveeAuthError, GoveeRateLimitError
from .const import CONF_ENABLE_GROUP_DEVICES, DOMAIN, UNSUPPORTED_DEVICE_SKUS
from .models import GoveeConfigEntry, GoveeDevice, GoveeDeviceState, SceneOption

_LOGGER = logging.getLogger(__name__)


class GoveeDataUpdateCoordinator(DataUpdateCoordinator[dict[str, GoveeDeviceState]]):
    """Coordinator for Govee device state updates.

    This coordinator manages device discovery, state polling, and scene caching
    for the Govee integration. It handles both regular devices and experimental
    group devices with optimistic state tracking.

    Key Features:
    - Device discovery with filtering (regular vs group devices)
    - Periodic state polling from Govee cloud API
    - Scene caching (dynamic and DIY scenes)
    - Rate limit tracking and enforcement
    - Optimistic state updates for group devices

    Architecture:
    - Inherits from Home Assistant's DataUpdateCoordinator
    - Provides centralized state management for all Govee entities
    - Implements retry logic and error handling for API failures
    - Caches scenes to minimize API calls
    """

    config_entry: GoveeConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        config_entry: GoveeConfigEntry,
        client: GoveeApiClient,
        update_interval: timedelta,
    ) -> None:
        """Initialize the coordinator.

        Args:
            hass: Home Assistant instance
            config_entry: Config entry for this integration
            client: Govee API client instance
            update_interval: How often to poll for state updates
        """
        self.client = client
        self.devices: dict[str, GoveeDevice] = {}  # device_id -> GoveeDevice
        self._scene_cache: dict[str, list[SceneOption]] = {}  # device_id -> scenes
        self._diy_scene_cache: dict[str, list[SceneOption]] = {}  # device_id -> DIY scenes

        super().__init__(
            hass,
            _LOGGER,
            config_entry=config_entry,
            name="Govee",
            update_interval=update_interval,
        )

    def _create_group_device_issue(self, device: GoveeDevice) -> None:
        """Create informational issue for group device limitations.

        Group devices from the Govee Home app have limited API support:
        - Control commands work (on/off, brightness, color, scenes)
        - State queries fail (API limitation)
        - Integration uses optimistic state tracking

        This creates a persistent warning to inform users about these limitations.

        Args:
            device: The group device that triggered this issue
        """
        ir.async_create_issue(
            self.hass,
            DOMAIN,
            f"group_device_{self.config_entry.entry_id}",
            is_fixable=False,
            is_persistent=True,
            severity=ir.IssueSeverity.WARNING,
            translation_key="group_device_limitation",
            translation_placeholders={
                "device_name": device.device_name,
            },
            learn_more_url="https://developer.govee.com/reference/get-devices",
        )

    def _check_rate_limits(self) -> None:
        """Check rate limits and create/clear warnings as needed.

        Monitors API rate limits and creates non-persistent warnings when
        approaching limits:
        - Per-minute: Warning at < 20 remaining (of 100)
        - Per-day: Warning at < 2000 remaining (of 10,000)

        Warnings automatically clear when limits recover.
        """
        minute_issue_id = f"rate_limit_minute_{self.config_entry.entry_id}"
        day_issue_id = f"rate_limit_day_{self.config_entry.entry_id}"

        # Per-minute limit warning (< 20 remaining)
        if self.rate_limit_remaining_minute < 20:
            ir.async_create_issue(
                self.hass,
                DOMAIN,
                minute_issue_id,
                is_fixable=False,
                is_persistent=False,
                severity=ir.IssueSeverity.WARNING,
                translation_key="rate_limit_minute_warning",
                translation_placeholders={
                    "remaining": str(self.rate_limit_remaining_minute),
                },
            )
        else:
            # Clear issue if limit is no longer approached
            ir.async_delete_issue(self.hass, DOMAIN, minute_issue_id)

        # Per-day limit warning (< 2000 remaining)
        if self.rate_limit_remaining < 2000:
            ir.async_create_issue(
                self.hass,
                DOMAIN,
                day_issue_id,
                is_fixable=False,
                is_persistent=False,
                severity=ir.IssueSeverity.WARNING,
                translation_key="rate_limit_day_warning",
                translation_placeholders={
                    "remaining": str(self.rate_limit_remaining),
                },
            )
        else:
            # Clear issue if limit is no longer approached
            ir.async_delete_issue(self.hass, DOMAIN, day_issue_id)

    async def _async_setup(self) -> None:
        """Set up the coordinator - fetch devices on first load.

        Device Discovery Process:
        1. Fetch all devices from Govee cloud API
        2. Filter out unsupported group devices (unless explicitly enabled)
        3. Store discovered devices in self.devices dictionary
        4. Log discovery statistics

        Group Device Handling:
        - Govee Home app groups (SameModeGroup, BaseGroup, DreamViewScenic)
          are identified by SKU and filtered by default
        - If CONF_ENABLE_GROUP_DEVICES is True, groups are included with
          experimental support (control works, state queries fail)
        - Group devices use optimistic state tracking

        Raises:
            ConfigEntryAuthFailed: If API key is invalid (401 error)
            UpdateFailed: If device discovery fails for other reasons
        """
        _LOGGER.debug("Setting up Govee coordinator - fetching devices")
        skipped_count = 0
        try:
            raw_devices = await self.client.get_devices()
            for raw_device in raw_devices:
                device = GoveeDevice.from_api(raw_device)

                # Skip unsupported device types (Govee Home app groups) unless enabled
                # Group devices have limited API support: control works but state queries fail
                if device.sku in UNSUPPORTED_DEVICE_SKUS:
                    if not self.config_entry.options.get(CONF_ENABLE_GROUP_DEVICES, False):
                        _LOGGER.debug(
                            "Skipping unsupported device group: %s (%s) - "
                            "Govee Home app groups do not support API control. "
                            "Enable 'Enable Group Devices' in options to test.",
                            device.device_name,
                            device.sku,
                        )
                        skipped_count += 1
                        continue
                    else:
                        _LOGGER.warning(
                            "EXPERIMENTAL: Including group device: %s (%s) - "
                            "Control commands work but state queries fail. "
                            "Device will use optimistic state tracking (assumed state).",
                            device.device_name,
                            device.sku,
                        )

                self.devices[device.device_id] = device
                _LOGGER.debug(
                    "Discovered device: %s (%s) - %s",
                    device.device_name,
                    device.sku,
                    device.device_type,
                )

            # Count experimental group devices that were included
            group_device_count = sum(
                1 for device in self.devices.values()
                if device.sku in UNSUPPORTED_DEVICE_SKUS
            )

            if group_device_count > 0:
                _LOGGER.warning(
                    "Discovered %d Govee devices (%d skipped, %d EXPERIMENTAL groups included)",
                    len(self.devices),
                    skipped_count,
                    group_device_count,
                )
                # Create informational issue for group device limitations
                # Find first group device for issue creation
                for device in self.devices.values():
                    if device.sku in UNSUPPORTED_DEVICE_SKUS:
                        self._create_group_device_issue(device)
                        break
            else:
                _LOGGER.info(
                    "Discovered %d Govee devices (%d unsupported groups skipped)",
                    len(self.devices),
                    skipped_count,
                )

        except GoveeAuthError as err:
            raise ConfigEntryAuthFailed("Invalid API key") from err
        except GoveeApiError as err:
            raise UpdateFailed(f"Failed to fetch devices: {err}") from err

    async def _async_update_data(self) -> dict[str, GoveeDeviceState]:
        """Fetch state for all devices.

        State Update Process:
        1. Iterate through all discovered devices
        2. Query each device's state from Govee API
        3. Preserve optimistic state (e.g., selected scenes) that API doesn't report
        4. Handle errors gracefully with fallback to previous state

        Rate Limiting:
        - Enforced by GoveeApiClient rate limiter
        - Per-minute limit: 100 requests
        - Per-day limit: 10,000 requests
        - Rate limit errors result in keeping previous state

        Group Device Behavior:
        - Group devices cannot be queried for state (API limitation)
        - State queries fail with expected errors (logged at info level)
        - Group devices use optimistic state tracking instead
        - Marked as available even with online=False

        Error Handling:
        - Auth errors (401): Triggers re-authentication flow via ConfigEntryAuthFailed
        - Rate limits (429): Logs warning, keeps previous state
        - Other API errors: Logs warning/error, keeps previous state when available

        Returns:
            Dictionary mapping device_id to GoveeDeviceState

        Raises:
            ConfigEntryAuthFailed: If API key is invalid, triggers reauth flow
        """
        _LOGGER.debug("Updating Govee device states")
        states: dict[str, GoveeDeviceState] = {}

        # Parallel State Fetching Optimization:
        # Fetch all device states concurrently instead of sequentially
        # For 10 devices: ~10x faster (10s → 1s)
        # Significantly improves user experience with multiple devices
        async def fetch_device_state(
            device_id: str, device: GoveeDevice
        ) -> tuple[str, GoveeDeviceState | Exception]:
            """Fetch state for a single device, returning result or exception."""
            try:
                raw_state = await self.client.get_device_state(device_id, device.sku)
                # Preserve optimistic state (like scenes) that API doesn't report
                if self.data and device_id in self.data:
                    state = self.data[device_id]
                    state.update_from_api(raw_state)
                else:
                    state = GoveeDeviceState.from_api(device_id, raw_state)
                return (device_id, state)
            except Exception as err:
                return (device_id, err)

        # Create tasks for all devices
        tasks = [
            fetch_device_state(device_id, device)
            for device_id, device in self.devices.items()
        ]

        # Execute all tasks in parallel with timeout
        try:
            results = await asyncio.wait_for(
                asyncio.gather(*tasks, return_exceptions=False),
                timeout=30.0,
            )
        except asyncio.TimeoutError:
            _LOGGER.warning("State update timed out after 30s")
            raise UpdateFailed("State update timeout") from None

        # Process results
        for device_id, result in results:
            device = self.devices[device_id]

            if isinstance(result, GoveeAuthError):
                # Auth error: trigger re-authentication flow
                raise ConfigEntryAuthFailed("Invalid API key") from result

            elif isinstance(result, GoveeRateLimitError):
                _LOGGER.warning(
                    "Rate limit hit while updating %s, will retry: %s",
                    device_id,
                    result,
                )
                # Keep previous state if available
                if self.data and device_id in self.data:
                    states[device_id] = self.data[device_id]

            elif isinstance(result, GoveeApiError):
                is_group_device = device.sku in UNSUPPORTED_DEVICE_SKUS
                log_level = _LOGGER.info if is_group_device else _LOGGER.warning

                if is_group_device:
                    log_level(
                        "State query failed for group device %s (%s) [EXPECTED]: %s. "
                        "Using optimistic state tracking - device will show as available with assumed state.",
                        device.device_name,
                        device_id,
                        result,
                    )
                else:
                    log_level(
                        "Failed to get state for %s (%s): %s",
                        device.device_name,
                        device_id,
                        result,
                    )
                # Keep previous state if available
                if self.data and device_id in self.data:
                    states[device_id] = self.data[device_id]
                else:
                    # For group devices, initialize with structure suitable for optimistic updates
                    if is_group_device:
                        states[device_id] = GoveeDeviceState(
                            device_id=device_id,
                            online=False,  # Can't query state
                            power_state=None,  # Unknown until first command or restoration
                        )
                    else:
                        # Regular offline device
                        states[device_id] = GoveeDeviceState(
                            device_id=device_id, online=False
                        )

            elif isinstance(result, Exception):
                # Unexpected error
                _LOGGER.error(
                    "Unexpected error updating %s: %s",
                    device_id,
                    result,
                )
                # Keep previous state if available
                if self.data and device_id in self.data:
                    states[device_id] = self.data[device_id]

            else:
                # Success: result is GoveeDeviceState
                states[device_id] = result

        # Check rate limits and create/clear warnings as needed
        self._check_rate_limits()

        return states

    def get_device(self, device_id: str) -> GoveeDevice | None:
        """Get device by ID."""
        return self.devices.get(device_id)

    def get_state(self, device_id: str) -> GoveeDeviceState | None:
        """Get current state for a device."""
        if self.data:
            return self.data.get(device_id)
        return None

    async def async_control_device(
        self,
        device_id: str,
        capability_type: str,
        instance: str,
        value: Any,
    ) -> None:
        """Send a control command and apply optimistic update.

        Control Flow:
        1. Validate device exists
        2. Send control command to Govee API
        3. Apply optimistic state update immediately (don't wait for API confirmation)
        4. Notify entities of state change via async_set_updated_data()

        Optimistic Updates:
        - State is updated immediately before API confirmation
        - Provides responsive UI (no wait for cloud round-trip)
        - Next polling cycle will sync actual state from API
        - Critical for group devices where state queries fail

        Args:
            device_id: Device identifier
            capability_type: Type of capability (e.g., "devices.capabilities.on_off")
            instance: Capability instance (e.g., "powerSwitch")
            value: Value to set (type depends on capability)

        Raises:
            GoveeApiError: If control command fails
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

            # Apply optimistic state update
            if self.data and device_id in self.data:
                self.data[device_id].apply_optimistic_update(instance, value)
                self.async_set_updated_data(self.data)

        except GoveeApiError as err:
            is_group_device = device.sku in UNSUPPORTED_DEVICE_SKUS

            if is_group_device:
                _LOGGER.warning(
                    "Failed to control group device %s (%s.%s = %s): %s - "
                    "Group devices may not support API control.",
                    device_id,
                    capability_type,
                    instance,
                    value,
                    err,
                )
            else:
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
        sku: str,
        segment_index: int,
        rgb: tuple[int, int, int],
    ) -> None:
        """Set color for a specific segment on an RGBIC device.

        Args:
            device_id: Device identifier
            sku: Device SKU/model
            segment_index: Zero-based segment index
            rgb: RGB color tuple (0-255 per channel)

        Raises:
            GoveeApiError: If segment control command fails
        """
        try:
            await self.client.set_segment_color(device_id, sku, segment_index, rgb)

            # Apply optimistic state update
            if self.data and device_id in self.data:
                self.data[device_id].apply_segment_update(segment_index, rgb)
                self.async_set_updated_data(self.data)

        except GoveeApiError as err:
            _LOGGER.error(
                "Failed to set segment %d color on device %s: %s",
                segment_index,
                device_id,
                err,
            )
            raise

    async def async_set_segment_brightness(
        self,
        device_id: str,
        sku: str,
        segment_index: int,
        brightness: int,
    ) -> None:
        """Set brightness for a specific segment on an RGBIC device.

        Args:
            device_id: Device identifier
            sku: Device SKU/model
            segment_index: Zero-based segment index
            brightness: Brightness level (0-100)

        Raises:
            GoveeApiError: If segment control command fails
        """
        try:
            await self.client.set_segment_brightness(
                device_id, sku, segment_index, brightness
            )

            # Apply optimistic state update
            if self.data and device_id in self.data:
                self.data[device_id].apply_segment_brightness_update(
                    segment_index, brightness
                )
                self.async_set_updated_data(self.data)

        except GoveeApiError as err:
            _LOGGER.error(
                "Failed to set segment %d brightness on device %s: %s",
                segment_index,
                device_id,
                err,
            )
            raise

    async def async_get_dynamic_scenes(
        self, device_id: str, refresh: bool = False
    ) -> list[SceneOption]:
        """Get dynamic scenes for a device (cached).

        Args:
            device_id: Device identifier
            refresh: Force refresh from API

        Returns:
            List of available scenes
        """
        if not refresh and device_id in self._scene_cache:
            return self._scene_cache[device_id]

        device = self.devices.get(device_id)
        if not device:
            return []

        try:
            raw_scenes = await self.client.get_dynamic_scenes(device_id, device.sku)
            scenes = [SceneOption.from_api(s) for s in raw_scenes]
            self._scene_cache[device_id] = scenes
            _LOGGER.debug(
                "Fetched %d dynamic scenes for %s",
                len(scenes),
                device.device_name,
            )
            return scenes

        except GoveeApiError as err:
            _LOGGER.warning("Failed to fetch scenes for %s: %s", device_id, err)
            return self._scene_cache.get(device_id, [])

    async def async_get_diy_scenes(
        self, device_id: str, refresh: bool = False
    ) -> list[SceneOption]:
        """Get DIY scenes for a device (cached).

        Args:
            device_id: Device identifier
            refresh: Force refresh from API

        Returns:
            List of available DIY scenes
        """
        if not refresh and device_id in self._diy_scene_cache:
            return self._diy_scene_cache[device_id]

        device = self.devices.get(device_id)
        if not device:
            return []

        try:
            raw_scenes = await self.client.get_diy_scenes(device_id, device.sku)
            scenes = [SceneOption.from_api(s) for s in raw_scenes]
            self._diy_scene_cache[device_id] = scenes
            _LOGGER.debug(
                "Fetched %d DIY scenes for %s",
                len(scenes),
                device.device_name,
            )
            return scenes

        except GoveeApiError as err:
            _LOGGER.warning("Failed to fetch DIY scenes for %s: %s", device_id, err)
            return self._diy_scene_cache.get(device_id, [])

    @property
    def rate_limit_remaining(self) -> int:
        """Get remaining daily rate limit."""
        return self.client.rate_limiter.remaining_day

    @property
    def rate_limit_remaining_minute(self) -> int:
        """Get remaining per-minute rate limit."""
        return self.client.rate_limiter.remaining_minute

    @property
    def rate_limit_remaining_day(self) -> int:
        """Get remaining daily rate limit."""
        return self.client.rate_limiter.remaining_day

    async def async_refresh_device_scenes(self, device_id: str) -> None:
        """Refresh scene lists for a device.

        Clears cached scenes and fetches fresh lists from the API.
        Used by the refresh scenes button.
        """
        # Clear caches for this device
        self._scene_cache.pop(device_id, None)
        self._diy_scene_cache.pop(device_id, None)

        # Fetch fresh scenes
        await self.async_get_dynamic_scenes(device_id)
        await self.async_get_diy_scenes(device_id)

        _LOGGER.debug("Refreshed scenes for device %s", device_id)

    async def async_identify_device(self, device_id: str) -> None:
        """Identify a device by flashing it briefly.

        Turns the device off and back on to help users identify
        which physical device corresponds to this entity.
        """
        from .api.const import CAPABILITY_ON_OFF, INSTANCE_POWER_SWITCH

        # Quick flash: off then on
        await self.async_control_device(
            device_id,
            CAPABILITY_ON_OFF,
            INSTANCE_POWER_SWITCH,
            0,
        )
        import asyncio
        await asyncio.sleep(0.5)
        await self.async_control_device(
            device_id,
            CAPABILITY_ON_OFF,
            INSTANCE_POWER_SWITCH,
            1,
        )
        _LOGGER.debug("Identified device %s", device_id)

    async def async_set_power_state(self, device_id: str, power_on: bool) -> None:
        """Set power state for a device.

        Args:
            device_id: Device MAC address / identifier
            power_on: True to turn on, False to turn off
        """
        from .api.const import CAPABILITY_ON_OFF, INSTANCE_POWER_SWITCH

        await self.async_control_device(
            device_id,
            CAPABILITY_ON_OFF,
            INSTANCE_POWER_SWITCH,
            1 if power_on else 0,
        )
        _LOGGER.debug(
            "Set power state for device %s to %s",
            device_id,
            "on" if power_on else "off",
        )
