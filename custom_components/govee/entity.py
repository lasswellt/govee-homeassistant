"""Base entity for Govee integration."""
from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import GoveeDataUpdateCoordinator
from .models import GoveeDevice, GoveeDeviceState


class GoveeEntity(CoordinatorEntity[GoveeDataUpdateCoordinator]):
    """Base entity for Govee devices."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: GoveeDataUpdateCoordinator,
        device: GoveeDevice,
    ) -> None:
        """Initialize the entity."""
        super().__init__(coordinator)
        self._device = device
        self._device_id = device.device_id

        # Set up device info for device registry
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, device.device_id)},
            name=device.device_name,
            manufacturer="Govee",
            model=device.sku,
            sw_version=device.firmware_version,
        )

    @property
    def device_state(self) -> GoveeDeviceState | None:
        """Get current device state from coordinator."""
        return self.coordinator.get_state(self._device_id)

    @property
    def _is_group_device(self) -> bool:
        """Check if this is a group device (experimental support)."""
        from .const import UNSUPPORTED_DEVICE_SKUS
        return self._device.sku in UNSUPPORTED_DEVICE_SKUS

    @property
    def available(self) -> bool:
        """Return if entity is available.

        Group devices are always available for control even though
        their state cannot be queried (online=False).
        """
        if not super().available:
            return False

        # Group devices: always available if coordinator is available
        # They can be controlled even though state queries fail
        if self._is_group_device:
            return True

        # Regular devices: require online state
        state = self.device_state
        if state is None:
            return False

        return state.online

    @property
    def extra_state_attributes(self) -> dict[str, any]:
        """Return extra state attributes."""
        attrs = {
            "device_id": self._device_id,
            "model": self._device.sku,
            "rate_limit_remaining": self.coordinator.rate_limit_remaining,
            "rate_limit_remaining_minute": self.coordinator.rate_limit_remaining_minute,
        }

        if self._is_group_device:
            attrs["group_device"] = True
            attrs["assumed_state_reason"] = "Group devices cannot be queried for state"

        return attrs
