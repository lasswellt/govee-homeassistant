"""Base entity class for Govee integration.

Provides common functionality for all Govee entities:
- Device info
- Coordinator integration
- Availability tracking
- Command dispatch that raises when a command is not accepted
- Transport diagnostics (Cloud API / MQTT / BLE)
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONF_EXPOSE_TRANSPORT_ENTITIES, DOMAIN
from .registry import hub_link_info

if TYPE_CHECKING:
    from .coordinator import GoveeCoordinator
    from .models import GoveeDevice, GoveeDeviceState
    from .models.commands import DeviceCommand


class GoveeEntity(CoordinatorEntity["GoveeCoordinator"]):
    """Base class for Govee entities.

    Provides:
    - Automatic coordinator integration
    - Device info with rich metadata
    - Availability tracking
    - has_entity_name = True so entity names compose with the device name
    """

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: GoveeCoordinator,
        device: GoveeDevice,
    ) -> None:
        """Initialize the entity.

        Args:
            coordinator: Govee data coordinator.
            device: Device this entity represents.
        """
        super().__init__(coordinator)
        self._device = device
        self._device_id = device.device_id

        # Set unique_id based on device
        self._attr_unique_id = device.device_id

    @property
    def device_info(self) -> DeviceInfo:
        """Return device information for device registry."""
        # Gateway-bridged devices (e.g. H5310 via H5044) link to their hub so HA
        # shows the relationship. The hub is registered first (#86).
        hub_link: dict[str, Any] = {}
        if self._device.hub_device_id:
            hub_link = hub_link_info(
                self.hass, (DOMAIN, self._device.hub_device_id), self.coordinator._config_entry.entry_id
            )
        info = DeviceInfo(
            identifiers={(DOMAIN, self._device.device_id)},
            name=self._device.name,
            manufacturer="Govee",
            model=self._device.sku,
        )
        for key, value in hub_link.items():
            cast(Any, info)[key] = value
        return info

    @property
    def available(self) -> bool:
        """Return True if entity is available.

        Checks coordinator health first (via super().available which
        verifies last_update_success), then whether any transport reaches the
        device (``GoveeCoordinator.device_reachable``, #242). Group devices
        cannot be polled, so they follow coordinator health only.
        """
        if not super().available:
            return False
        if self._device.is_group:
            return True
        return self.coordinator.device_reachable(self._device_id)

    @property
    def device_state(self) -> GoveeDeviceState | None:
        """Get current device state from coordinator."""
        return self.coordinator.get_state(self._device_id)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return transport protocol diagnostics for this device.

        Off by default to keep the state machine lean for installs with many
        entities. The same data is available via dedicated diagnostic
        binary_sensor entities. Set
        ``CONF_EXPOSE_TRANSPORT_ENTITIES`` in entry options to True to opt in.
        """
        config_entry = self.coordinator.config_entry
        if config_entry is None or not config_entry.options.get(CONF_EXPOSE_TRANSPORT_ENTITIES, False):
            return {}
        return {
            "transport_cloud_api": True,
            "transport_mqtt": self.coordinator.mqtt_connected,
            "transport_ble": self.coordinator.is_ble_available(self._device_id),
        }

    def _command_failed(self) -> HomeAssistantError:
        """Build the error raised when Govee does not accept a command.

        The coordinator reports a rejected or undeliverable command as
        ``False`` after logging the cause. Turning that into a translated
        ``HomeAssistantError`` is what lets an automation or the UI see that
        ``light.turn_on`` did not happen (quality-scale rule
        ``action-exceptions``). When the device is known to be offline (Govee
        just said so, #242), the error says that instead.
        """
        state = self.coordinator.get_state(self._device_id)
        offline = state is not None and state.online is False and not self._device.is_group
        return HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key="device_offline" if offline else "command_failed",
            translation_placeholders={"device": self._device.name},
        )

    async def _async_send_command(self, command: DeviceCommand) -> None:
        """Send ``command`` through the coordinator; raise if it was not accepted."""
        if not await self.coordinator.async_control_device(self._device_id, command):
            raise self._command_failed()
