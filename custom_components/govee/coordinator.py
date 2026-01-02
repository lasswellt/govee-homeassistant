"""DataUpdateCoordinator for Govee integration."""
from __future__ import annotations

import asyncio
import logging
from datetime import timedelta
from collections.abc import Callable, Coroutine
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.helpers import issue_registry as ir
from homeassistant.exceptions import ConfigEntryAuthFailed

from .api import GoveeApiClient, GoveeApiError, GoveeAuthError, GoveeRateLimitError
from .const import CONF_ENABLE_GROUP_DEVICES, DOMAIN, UNSUPPORTED_DEVICE_SKUS
from .models import GoveeConfigEntry, GoveeDevice, GoveeDeviceState, SceneOption

_LOGGER = logging.getLogger(__name__)


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
