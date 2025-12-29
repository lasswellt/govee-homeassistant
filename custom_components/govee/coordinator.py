"""DataUpdateCoordinator for Govee integration."""
from __future__ import annotations

import logging
from datetime import timedelta
from typing import TYPE_CHECKING, Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.exceptions import ConfigEntryAuthFailed

from .api import GoveeApiClient, GoveeApiError, GoveeAuthError, GoveeRateLimitError
from .const import CONF_ENABLE_GROUP_DEVICES, UNSUPPORTED_DEVICE_SKUS
from .models import GoveeDevice, GoveeDeviceState, SceneOption

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry

_LOGGER = logging.getLogger(__name__)


class GoveeDataUpdateCoordinator(DataUpdateCoordinator[dict[str, GoveeDeviceState]]):
    """Coordinator for Govee device state updates."""

    config_entry: ConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        config_entry: ConfigEntry,
        client: GoveeApiClient,
        update_interval: timedelta,
    ) -> None:
        """Initialize the coordinator."""
        self.client = client
        self.devices: dict[str, GoveeDevice] = {}
        self._scene_cache: dict[str, list[SceneOption]] = {}
        self._diy_scene_cache: dict[str, list[SceneOption]] = {}

        super().__init__(
            hass,
            _LOGGER,
            config_entry=config_entry,
            name="Govee",
            update_interval=update_interval,
        )

    async def _async_setup(self) -> None:
        """Set up the coordinator - fetch devices on first load."""
        _LOGGER.debug("Setting up Govee coordinator - fetching devices")
        skipped_count = 0
        try:
            raw_devices = await self.client.get_devices()
            for raw_device in raw_devices:
                device = GoveeDevice.from_api(raw_device)

                # Skip unsupported device types (Govee Home app groups) unless enabled
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
                            "API support not guaranteed. Errors may occur.",
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
        """Fetch state for all devices."""
        _LOGGER.debug("Updating Govee device states")
        states: dict[str, GoveeDeviceState] = {}

        for device_id, device in self.devices.items():
            try:
                raw_state = await self.client.get_device_state(
                    device_id, device.sku
                )
                state = GoveeDeviceState.from_api(device_id, raw_state)
                states[device_id] = state

            except GoveeAuthError as err:
                raise ConfigEntryAuthFailed("Invalid API key") from err
            except GoveeRateLimitError as err:
                _LOGGER.warning(
                    "Rate limit hit while updating %s, will retry: %s",
                    device_id,
                    err,
                )
                # Keep previous state if available
                if self.data and device_id in self.data:
                    states[device_id] = self.data[device_id]
            except GoveeApiError as err:
                is_group_device = device.sku in UNSUPPORTED_DEVICE_SKUS
                log_level = _LOGGER.info if is_group_device else _LOGGER.warning

                log_level(
                    "Failed to get state for %s (%s)%s: %s",
                    device.device_name,
                    device_id,
                    " [GROUP DEVICE - expected]" if is_group_device else "",
                    err,
                )
                # Keep previous state if available
                if self.data and device_id in self.data:
                    states[device_id] = self.data[device_id]
                else:
                    # Create offline state
                    states[device_id] = GoveeDeviceState(
                        device_id=device_id, online=False
                    )

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
        """Send a control command and apply optimistic update."""
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
