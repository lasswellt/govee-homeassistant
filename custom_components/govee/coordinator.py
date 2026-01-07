"""DataUpdateCoordinator for Govee integration."""
from __future__ import annotations

import asyncio
import logging
import math
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from collections.abc import Callable, Coroutine
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.helpers import issue_registry as ir
from homeassistant.exceptions import ConfigEntryAuthFailed

from .api import GoveeApiClient, GoveeApiError, GoveeAuthError, GoveeRateLimitError
from .const import (
    CONF_ENABLE_GROUP_DEVICES,
    DOMAIN,
    POLL_INTERVAL_CRITICAL,
    POLL_INTERVAL_DANGER,
    POLL_INTERVAL_MIN,
    POLL_INTERVAL_WARNING,
    QUOTA_CRITICAL,
    QUOTA_DANGER,
    QUOTA_OK,
    QUOTA_WARNING,
    UNSUPPORTED_DEVICE_SKUS,
)
from .models import GoveeConfigEntry, GoveeDevice, GoveeDeviceState, SceneOption
from .mqtt import GoveeMqttClient

_LOGGER = logging.getLogger(__name__)


@dataclass
class PendingCommand:
    """A command queued for batch execution."""

    capability_type: str
    instance: str
    value: Any


@dataclass
class CommandBatch:
    """Collects commands for batched execution with single state notification."""

    device_id: str
    commands: list[PendingCommand] = field(default_factory=list)

    def add(self, capability_type: str, instance: str, value: Any) -> None:
        """Add a command to the batch."""
        self.commands.append(PendingCommand(capability_type, instance, value))


class GoveeDataUpdateCoordinator(DataUpdateCoordinator[dict[str, GoveeDeviceState]]):
    """Coordinator for Govee device discovery, state polling, and scene caching."""

    config_entry: GoveeConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        config_entry: GoveeConfigEntry,
        client: GoveeApiClient,
        update_interval: timedelta,
    ) -> None:
        self.client = client
        self.devices: dict[str, GoveeDevice] = {}
        self._scene_cache: dict[str, list[SceneOption]] = {}
        self._diy_scene_cache: dict[str, list[SceneOption]] = {}
        self._snapshot_cache: dict[str, list[SceneOption]] = {}
        # Store user's configured interval for adaptive polling to restore later
        self._user_interval = update_interval
        # MQTT client for real-time device events
        self._mqtt_client: GoveeMqttClient | None = None
        self._mqtt_enabled = False

        super().__init__(
            hass,
            _LOGGER,
            config_entry=config_entry,
            name="Govee",
            update_interval=update_interval,
            always_update=False,  # Only notify listeners when data changes
        )

    def _create_group_device_issue(self, device: GoveeDevice) -> None:
        """Create informational issue for group device limitations.

        Group devices from the Govee Home app have limited API support:
        - Control commands work (on/off, brightness, color, scenes)
        - State queries fail (API limitation)
        - Integration uses optimistic state tracking
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

        - Per-minute: Warning at < 20 remaining (of 100)
        - Per-day: Warning at < 2000 remaining (of 10,000)
        """
        minute_issue_id = f"rate_limit_minute_{self.config_entry.entry_id}"
        day_issue_id = f"rate_limit_day_{self.config_entry.entry_id}"

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
            ir.async_delete_issue(self.hass, DOMAIN, minute_issue_id)

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
            ir.async_delete_issue(self.hass, DOMAIN, day_issue_id)

    async def _async_setup(self) -> None:
        """Fetch devices on first load.

        Group Device Handling:
        - Govee Home app groups (SameModeGroup, BaseGroup, DreamViewScenic)
          are identified by SKU and filtered by default
        - If CONF_ENABLE_GROUP_DEVICES is True, groups are included with
          experimental support (control works, state queries fail)
        - Group devices use optimistic state tracking
        """
        _LOGGER.debug("Setting up Govee coordinator - fetching devices")
        skipped_count = 0
        try:
            raw_devices = await self.client.get_devices()
            for raw_device in raw_devices:
                device = GoveeDevice.from_api(raw_device)

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

        Preserves optimistic state (e.g., selected scenes) that API doesn't report.

        Group Device Behavior:
        - Group devices cannot be queried for state (API limitation)
        - State queries fail with expected errors (logged at info level)
        - Group devices use optimistic state tracking instead
        - Marked as available even with online=False
        """
        _LOGGER.debug("Updating Govee device states")
        states: dict[str, GoveeDeviceState] = {}

        async def fetch_device_state(
            device_id: str, device: GoveeDevice
        ) -> tuple[str, GoveeDeviceState | Exception]:
            try:
                raw_state = await self.client.get_device_state(device_id, device.sku)
                if self.data and device_id in self.data:
                    state = self.data[device_id]
                    state.update_from_api(raw_state)
                else:
                    state = GoveeDeviceState.from_api(device_id, raw_state)
                return (device_id, state)
            except Exception as err:
                return (device_id, err)

        tasks = [
            fetch_device_state(device_id, device)
            for device_id, device in self.devices.items()
        ]

        try:
            results = await asyncio.wait_for(
                asyncio.gather(*tasks, return_exceptions=False),
                timeout=30.0,
            )
        except asyncio.TimeoutError:
            _LOGGER.warning("State update timed out after 30s")
            raise UpdateFailed("State update timeout") from None

        # Track rate limit errors to signal HA to back off
        rate_limit_error: GoveeRateLimitError | None = None

        for device_id, result in results:
            device = self.devices[device_id]

            if isinstance(result, GoveeAuthError):
                raise ConfigEntryAuthFailed("Invalid API key") from result

            elif isinstance(result, GoveeRateLimitError):
                _LOGGER.warning(
                    "Rate limit hit while updating %s, will retry: %s",
                    device_id,
                    result,
                )
                # Capture the first rate limit error (with retry_after info)
                if rate_limit_error is None:
                    rate_limit_error = result
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
                if self.data and device_id in self.data:
                    states[device_id] = self.data[device_id]
                else:
                    if is_group_device:
                        states[device_id] = GoveeDeviceState(
                            device_id=device_id,
                            online=False,
                            power_state=None,
                        )
                    else:
                        states[device_id] = GoveeDeviceState(
                            device_id=device_id, online=False
                        )

            elif isinstance(result, Exception):
                _LOGGER.error(
                    "Unexpected error updating %s: %s",
                    device_id,
                    result,
                )
                if self.data and device_id in self.data:
                    states[device_id] = self.data[device_id]

            else:
                states[device_id] = result

        self._check_rate_limits()
        self._adapt_polling_interval()

        # If rate limits were hit, raise UpdateFailed with retry_after
        # This signals HA to back off and retry later using built-in mechanisms
        if rate_limit_error is not None:
            retry_after = rate_limit_error.retry_after or 300  # Default 5 min
            _LOGGER.warning(
                "Rate limit encountered during update cycle, backing off for %ds",
                retry_after,
            )
            raise UpdateFailed(
                f"Govee API rate limited, retry in {retry_after}s"
            ) from rate_limit_error

        return states

    def get_device(self, device_id: str) -> GoveeDevice | None:
        return self.devices.get(device_id)

    def get_state(self, device_id: str) -> GoveeDeviceState | None:
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
        """Send a control command with optimistic update and rollback on failure.

        Optimistic Updates with Rollback:
        - State is captured before any changes
        - Optimistic update is applied immediately for responsive UI
        - On API failure, state is rolled back to previous values
        - Next polling cycle will sync actual state from API
        - Critical for group devices where state queries fail
        """
        device = self.devices.get(device_id)
        if not device:
            _LOGGER.error("Device not found: %s", device_id)
            return

        # Capture state for rollback before any changes
        previous_state: GoveeDeviceState | None = None
        if self.data and device_id in self.data:
            previous_state = GoveeDeviceState.from_state(self.data[device_id])
            # Apply optimistic update immediately for responsive UI
            self.data[device_id].apply_optimistic_update(instance, value)
            self.async_set_updated_data(self.data)

        try:
            await self.client.control_device(
                device_id,
                device.sku,
                capability_type,
                instance,
                value,
            )
            # Success - optimistic update was correct, nothing more to do

        except GoveeApiError as err:
            # Rollback optimistic update on failure
            if previous_state and self.data and device_id in self.data:
                self.data[device_id] = previous_state
                self.async_set_updated_data(self.data)
                _LOGGER.debug(
                    "Rolled back optimistic update for %s after control failure",
                    device_id,
                )

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

    async def async_execute_batch(
        self,
        device_id: str,
        batch: CommandBatch,
        inter_command_delay: float = 0.5,
    ) -> None:
        """Execute a batch of commands with single state update.

        This prevents UI flickering by:
        1. Applying ALL optimistic updates at once
        2. Notifying Home Assistant once (single UI update)
        3. Sending commands sequentially with delays
        4. Rolling back ALL changes on any failure

        Args:
            device_id: The device to control
            batch: CommandBatch containing all commands to execute
            inter_command_delay: Seconds between API calls (default 0.5s)
        """
        device = self.devices.get(device_id)
        if not device or not batch.commands:
            return

        # Capture state for potential rollback
        previous_state: GoveeDeviceState | None = None
        if self.data and device_id in self.data:
            previous_state = GoveeDeviceState.from_state(self.data[device_id])

            # Apply ALL optimistic updates at once before sending any commands
            for cmd in batch.commands:
                self.data[device_id].apply_optimistic_update(cmd.instance, cmd.value)

            # Single notification to Home Assistant (prevents UI flickering)
            self.async_set_updated_data(self.data)

        try:
            # Send commands sequentially with delays
            for i, cmd in enumerate(batch.commands):
                await self.client.control_device(
                    device_id,
                    device.sku,
                    cmd.capability_type,
                    cmd.instance,
                    cmd.value,
                )
                # Add delay between commands (not after the last one)
                if i < len(batch.commands) - 1:
                    await asyncio.sleep(inter_command_delay)

        except GoveeApiError:
            # Rollback ALL optimistic updates on any failure
            if previous_state and self.data and device_id in self.data:
                self.data[device_id] = previous_state
                self.async_set_updated_data(self.data)
                _LOGGER.debug(
                    "Rolled back all optimistic updates for %s after batch failure",
                    device_id,
                )
            raise

    async def async_set_segment_color(
        self,
        device_id: str,
        sku: str,
        segment_index: int,
        rgb: tuple[int, int, int],
    ) -> None:
        try:
            await self.client.set_segment_color(device_id, sku, segment_index, rgb)

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
        try:
            await self.client.set_segment_brightness(
                device_id, sku, segment_index, brightness
            )

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

    async def _async_get_scenes_cached(
        self,
        device_id: str,
        cache: dict[str, list[SceneOption]],
        fetch_func: Callable[[str, str], Coroutine[Any, Any, list[dict[str, Any]]]],
        scene_type: str,
        refresh: bool,
    ) -> list[SceneOption]:
        if not refresh and device_id in cache:
            return cache[device_id]

        device = self.devices.get(device_id)
        if not device:
            return []

        try:
            raw_scenes = await fetch_func(device_id, device.sku)
            scenes = [SceneOption.from_api(s) for s in raw_scenes]
            cache[device_id] = scenes
            _LOGGER.debug(
                "Fetched %d %s scenes for %s",
                len(scenes),
                scene_type,
                device.device_name,
            )
            return scenes
        except GoveeApiError as err:
            _LOGGER.warning(
                "Failed to fetch %s scenes for %s: %s", scene_type, device_id, err
            )
            return cache.get(device_id, [])

    async def async_get_dynamic_scenes(
        self, device_id: str, refresh: bool = False
    ) -> list[SceneOption]:
        return await self._async_get_scenes_cached(
            device_id,
            self._scene_cache,
            self.client.get_dynamic_scenes,
            "dynamic",
            refresh,
        )

    async def async_get_diy_scenes(
        self, device_id: str, refresh: bool = False
    ) -> list[SceneOption]:
        return await self._async_get_scenes_cached(
            device_id,
            self._diy_scene_cache,
            self.client.get_diy_scenes,
            "DIY",
            refresh,
        )

    async def async_get_snapshots(
        self, device_id: str, refresh: bool = False
    ) -> list[SceneOption]:
        """Get snapshots from device capabilities.

        Unlike dynamic and DIY scenes which have separate API endpoints,
        snapshots are embedded in the device's capabilities from /user/devices.
        """
        if not refresh and device_id in self._snapshot_cache:
            return self._snapshot_cache[device_id]

        device = self.devices.get(device_id)
        if not device:
            return []

        raw_snapshots = device.get_snapshot_options()
        scenes = [SceneOption.from_api(s) for s in raw_snapshots]
        self._snapshot_cache[device_id] = scenes

        if scenes:
            _LOGGER.debug(
                "Found %d snapshots for %s from device capabilities",
                len(scenes),
                device.device_name,
            )

        return scenes

    def invalidate_scene_cache(self, device_id: str | None = None) -> None:
        """Invalidate scene cache for one or all devices.

        Call this when configuration changes that might affect scenes,
        such as API key changes or options flow updates.

        Args:
            device_id: If provided, invalidate only this device's cache.
                       If None, invalidate all cached scenes.
        """
        if device_id:
            self._scene_cache.pop(device_id, None)
            self._diy_scene_cache.pop(device_id, None)
            self._snapshot_cache.pop(device_id, None)
            _LOGGER.debug("Invalidated scene cache for device %s", device_id)
        else:
            self._scene_cache.clear()
            self._diy_scene_cache.clear()
            self._snapshot_cache.clear()
            _LOGGER.debug("Invalidated all scene caches")

    @property
    def rate_limit_remaining(self) -> int:
        return self.client.rate_limiter.remaining_day

    @property
    def rate_limit_remaining_minute(self) -> int:
        return self.client.rate_limiter.remaining_minute

    @property
    def mqtt_connected(self) -> bool:
        """Return True if MQTT client is connected."""
        return self._mqtt_client is not None and self._mqtt_client.connected

    async def async_setup_mqtt(self, api_key: str) -> None:
        """Set up MQTT subscription for real-time device events.

        Connects to Govee's cloud MQTT broker to receive push notifications
        for device events. This reduces the need for constant API polling.

        Note: MQTT events only cover EVENT-type capabilities (sensors, alerts).
        Power state and brightness changes still require polling.

        Args:
            api_key: Govee API key used for MQTT authentication.
        """

        def on_mqtt_event(event: dict[str, Any]) -> None:
            """Handle MQTT event by updating device state."""
            device_id = event.get("device")
            if not device_id or not self.data:
                return

            if device_id in self.data:
                # Update state from event capabilities
                state = self.data[device_id]
                capabilities = event.get("capabilities", [])
                for cap in capabilities:
                    # Process event capabilities
                    cap_type = cap.get("type", "")
                    instance = cap.get("instance", "")
                    cap_state = cap.get("state", [])

                    _LOGGER.debug(
                        "Processing MQTT event for %s: %s.%s = %s",
                        device_id,
                        cap_type,
                        instance,
                        cap_state,
                    )

                    # Apply capability updates to state
                    # Event capabilities typically contain sensor/alert data
                    for state_item in cap_state:
                        state.apply_mqtt_event(instance, state_item)

                # Push update to all listeners (resets poll timer)
                self.async_set_updated_data(self.data)
                _LOGGER.debug("Updated device %s from MQTT event", device_id)

        self._mqtt_client = GoveeMqttClient(api_key, on_mqtt_event)
        await self._mqtt_client.async_start()
        self._mqtt_enabled = True
        _LOGGER.info("MQTT event subscription active")

    async def async_stop_mqtt(self) -> None:
        """Stop MQTT subscription.

        Called during integration unload to clean up the MQTT connection.
        """
        if self._mqtt_client:
            await self._mqtt_client.async_stop()
            self._mqtt_client = None
            self._mqtt_enabled = False
            _LOGGER.debug("MQTT client stopped")

    def _hours_until_reset(self, reset_timestamp: float | None) -> float:
        """Calculate hours until daily quota reset."""
        if not reset_timestamp:
            # Assume reset at midnight UTC if not provided
            now = datetime.now(timezone.utc)
            midnight = (now + timedelta(days=1)).replace(
                hour=0, minute=0, second=0, microsecond=0
            )
            return (midnight - now).total_seconds() / 3600
        return max(0, (reset_timestamp - time.time()) / 3600)

    def _adapt_polling_interval(self) -> None:
        """Dynamically adjust polling based on remaining quota.

        This implements adaptive rate limiting:
        - Monitors daily API quota from Govee rate limit headers
        - Calculates time-aware sustainable rate based on hours until reset
        - Applies tiered slowdown when quota gets low
        - Restores user's configured interval when quota recovers
        """
        status = self.client.rate_limiter.status
        remaining = status.remaining_day

        # Calculate time-aware sustainable rate
        hours_left = self._hours_until_reset(status.reset_day)
        if hours_left > 0 and remaining > 0:
            sustainable_per_hour = remaining / hours_left
            # Calculate minimum safe interval: 3600s/hour / calls_per_hour
            time_based_min: float = (
                max(POLL_INTERVAL_MIN, int(3600 / sustainable_per_hour))
                if sustainable_per_hour > 0
                else POLL_INTERVAL_CRITICAL
            )
        else:
            time_based_min = float(POLL_INTERVAL_CRITICAL)

        # Apply tiered slowdown based on remaining quota
        user_seconds: float = self._user_interval.total_seconds()
        new_interval: float
        severity: str | None

        if remaining < QUOTA_CRITICAL:
            new_interval = float(max(time_based_min, POLL_INTERVAL_CRITICAL))
            severity = "CRITICAL"
        elif remaining < QUOTA_DANGER:
            new_interval = float(max(time_based_min, POLL_INTERVAL_DANGER))
            severity = "danger"
        elif remaining < QUOTA_WARNING:
            new_interval = float(max(time_based_min, POLL_INTERVAL_WARNING))
            severity = "warning"
        elif remaining < QUOTA_OK:
            # Slightly throttled but not aggressively
            new_interval = max(time_based_min, user_seconds, 90.0)
            severity = "elevated"
        else:
            # Quota is healthy, use user's preferred interval
            new_interval = user_seconds
            severity = None

        if self.update_interval is None:
            current_seconds = 0.0
        else:
            current_seconds = self.update_interval.total_seconds()

        # Only log and update if interval actually changed
        if abs(new_interval - current_seconds) >= 1:
            self.update_interval = timedelta(seconds=new_interval)
            if severity:
                _LOGGER.warning(
                    "[%s] Adjusted poll interval to %ds (quota: %d/10000, %.1fh until reset)",
                    severity.upper(),
                    int(new_interval),
                    remaining,
                    hours_left,
                )
            else:
                _LOGGER.debug(
                    "Restored poll interval to user setting: %ds (quota healthy: %d)",
                    int(new_interval),
                    remaining,
                )

    @staticmethod
    def calculate_sustainable_interval(device_count: int) -> int:
        """Calculate minimum sustainable poll interval for device count.

        Formula: Ensure daily calls stay under 90% of 10,000 limit
        At interval I: calls/day = device_count × (86400/I)
        Solving for I: I > device_count × 86400 / 9000

        Args:
            device_count: Number of devices being polled

        Returns:
            Minimum safe poll interval in seconds
        """
        if device_count == 0:
            return POLL_INTERVAL_MIN
        min_interval = math.ceil(device_count * 86400 / 9000)
        return max(POLL_INTERVAL_MIN, min_interval)

    async def async_refresh_device_scenes(self, device_id: str) -> None:
        """Refresh scene lists for a device (used by refresh scenes button)."""
        self._scene_cache.pop(device_id, None)
        self._diy_scene_cache.pop(device_id, None)
        self._snapshot_cache.pop(device_id, None)

        await self.async_get_dynamic_scenes(device_id)
        await self.async_get_diy_scenes(device_id)
        await self.async_get_snapshots(device_id)

        _LOGGER.debug("Refreshed scenes for device %s", device_id)

    async def async_identify_device(self, device_id: str) -> None:
        """Identify a device by flashing it off and back on."""
        from .api.const import CAPABILITY_ON_OFF, INSTANCE_POWER_SWITCH

        await self.async_control_device(
            device_id,
            CAPABILITY_ON_OFF,
            INSTANCE_POWER_SWITCH,
            0,
        )
        await asyncio.sleep(0.5)
        await self.async_control_device(
            device_id,
            CAPABILITY_ON_OFF,
            INSTANCE_POWER_SWITCH,
            1,
        )
        _LOGGER.debug("Identified device %s", device_id)

    async def async_set_power_state(self, device_id: str, power_on: bool) -> None:
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
