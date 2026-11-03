"""Coverage tests for the button platform and the GoveeEntity base class.

Button: platform wiring for both buttons and the refresh-scenes press.

GoveeEntity: the availability rules (coordinator failure, group devices,
offline or unknown devices), the opt-in transport attributes, the hub link
in device_info, and the command dispatch that turns a rejected command into
a translated HomeAssistantError.
"""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock, MagicMock

from homeassistant.const import EntityCategory
from homeassistant.exceptions import HomeAssistantError
import pytest

from custom_components.govee import button as button_mod
from custom_components.govee.button import GoveeClearWaterFullButton, GoveeRefreshScenesButton
from custom_components.govee.const import CONF_EXPOSE_TRANSPORT_ENTITIES, DOMAIN, SUFFIX_REFRESH_SCENES
from custom_components.govee.entity import GoveeEntity
from custom_components.govee.models import GoveeCapability, GoveeDevice, GoveeDeviceState, PowerCommand
from custom_components.govee.models.device import (
    CAPABILITY_EVENT,
    CAPABILITY_ON_OFF,
    DEVICE_TYPE_DEHUMIDIFIER,
    INSTANCE_POWER,
    INSTANCE_WATER_FULL_EVENT,
)
from .coordinator_mocks import follow_online_flag


def _dehumidifier(*, is_group: bool = False) -> GoveeDevice:
    return GoveeDevice(
        device_id="11825917" if is_group else "0A:E8:D4:AD:FC:7A:05:2A",
        sku="H7150",
        name="Dehumidifier",
        device_type=DEVICE_TYPE_DEHUMIDIFIER,
        capabilities=(
            GoveeCapability(type=CAPABILITY_ON_OFF, instance=INSTANCE_POWER, parameters={}),
            GoveeCapability(type=CAPABILITY_EVENT, instance=INSTANCE_WATER_FULL_EVENT, parameters={}),
        ),
        is_group=is_group,
    )


def _coordinator(device: GoveeDevice, state: GoveeDeviceState | None, last_update_success: bool = True) -> MagicMock:
    coordinator = MagicMock()
    coordinator.devices = {device.device_id: device}
    coordinator.get_state = MagicMock(return_value=state)
    coordinator.async_control_device = AsyncMock(return_value=True)
    coordinator.last_update_success = last_update_success
    return follow_online_flag(coordinator)


def _state(device: GoveeDevice, online: bool = True) -> GoveeDeviceState:
    state = GoveeDeviceState.create_empty(device.device_id)
    state.online = online
    return state


# --------------------------------------------------------------------------- #
# Button platform
# --------------------------------------------------------------------------- #


class TestButtonSetup:
    async def _setup(self, *devices: GoveeDevice) -> list:
        coordinator = MagicMock()
        coordinator.devices = {d.device_id: d for d in devices}
        entry = MagicMock()
        entry.runtime_data = coordinator
        entry.options = {}
        added: list = []
        await button_mod.async_setup_entry(MagicMock(), entry, added.extend)
        return added

    async def test_scene_devices_get_the_refresh_button(self, mock_light_device, mock_plug_device):
        added = await self._setup(mock_light_device, mock_plug_device)
        (button,) = added
        assert isinstance(button, GoveeRefreshScenesButton)
        assert button._device is mock_light_device

    async def test_clear_water_button_skips_groups(self):
        added = await self._setup(_dehumidifier(), _dehumidifier(is_group=True))
        assert [type(e).__name__ for e in added] == ["GoveeClearWaterFullButton"]
        assert isinstance(added[0], GoveeClearWaterFullButton)


class TestRefreshScenesButton:
    def _entity(self, mock_light_device):
        coordinator = _coordinator(mock_light_device, _state(mock_light_device))
        coordinator.async_refresh_scenes = AsyncMock()
        return GoveeRefreshScenesButton(coordinator, mock_light_device), coordinator

    def test_identity(self, mock_light_device):
        entity, _ = self._entity(mock_light_device)
        assert entity.unique_id == f"{mock_light_device.device_id}{SUFFIX_REFRESH_SCENES}"
        assert entity.entity_category is EntityCategory.CONFIG
        assert entity.translation_key == "refresh_scenes"

    async def test_press_forces_a_scene_refresh(self, mock_light_device, caplog):
        entity, coordinator = self._entity(mock_light_device)

        with caplog.at_level(logging.DEBUG, logger="custom_components.govee.button"):
            await entity.async_press()

        # The coordinator refreshes the catalogs and tells the selects and light.
        coordinator.async_refresh_scenes.assert_awaited_once_with(mock_light_device.device_id)
        assert "Refreshing scenes for Living Room Light" in caplog.text
        assert "Scenes refreshed for Living Room Light" in caplog.text


# --------------------------------------------------------------------------- #
# GoveeEntity base class
# --------------------------------------------------------------------------- #


class TestEntityAvailability:
    def test_unavailable_when_the_coordinator_failed(self, mock_light_device):
        entity = GoveeEntity(_coordinator(mock_light_device, _state(mock_light_device), False), mock_light_device)
        assert entity.available is False

    def test_group_follows_the_coordinator_only(self, mock_group_device):
        # Groups cannot be polled, so no per-device state is needed.
        entity = GoveeEntity(_coordinator(mock_group_device, None), mock_group_device)
        assert entity.available is True
        entity = GoveeEntity(_coordinator(mock_group_device, None, False), mock_group_device)
        assert entity.available is False

    @pytest.mark.parametrize(("online", "expected"), [(True, True), (False, False)])
    def test_regular_device_needs_an_online_state(self, mock_light_device, online, expected):
        entity = GoveeEntity(_coordinator(mock_light_device, _state(mock_light_device, online)), mock_light_device)
        assert entity.available is expected

    def test_regular_device_unavailable_without_state(self, mock_light_device):
        entity = GoveeEntity(_coordinator(mock_light_device, None), mock_light_device)
        assert entity.available is False
        assert entity.device_state is None


class TestEntityAttributes:
    def _entity(self, device: GoveeDevice, options: dict | None) -> tuple[GoveeEntity, MagicMock]:
        coordinator = _coordinator(device, _state(device))
        if options is None:
            coordinator.config_entry = None
        else:
            coordinator.config_entry = MagicMock()
            coordinator.config_entry.options = options
        coordinator.mqtt_connected = True
        coordinator.is_ble_available = MagicMock(return_value=False)
        return GoveeEntity(coordinator, device), coordinator

    def test_no_attributes_without_a_config_entry(self, mock_light_device):
        entity, _ = self._entity(mock_light_device, None)
        assert entity.extra_state_attributes == {}

    def test_no_attributes_by_default(self, mock_light_device):
        entity, coordinator = self._entity(mock_light_device, {})
        assert entity.extra_state_attributes == {}
        coordinator.is_ble_available.assert_not_called()

    def test_transport_attributes_when_opted_in(self, mock_light_device):
        entity, coordinator = self._entity(mock_light_device, {CONF_EXPOSE_TRANSPORT_ENTITIES: True})
        assert entity.extra_state_attributes == {
            "transport_cloud_api": True,
            "transport_mqtt": True,
            "transport_ble": False,
        }
        coordinator.is_ble_available.assert_called_once_with(mock_light_device.device_id)

    def test_device_info_links_a_bridged_device_to_its_hub(self, mock_light_device):
        entity, _ = self._entity(mock_light_device, {})
        info = entity.device_info
        assert info["identifiers"] == {(DOMAIN, mock_light_device.device_id)}
        assert info["model"] == mock_light_device.sku
        assert "via_device" not in info

        bridged = GoveeDevice.synthetic_thermometer("AA:BB:CC:DD:EE:FF:53:10", "H5310", "Cellar", "AA:BB:HUB")
        entity, _ = self._entity(bridged, {})
        assert entity.device_info["via_device"] == (DOMAIN, "AA:BB:HUB")


class TestEntityCommands:
    def test_command_failed_error_is_translated(self, mock_light_device):
        entity = GoveeEntity(_coordinator(mock_light_device, _state(mock_light_device)), mock_light_device)
        err = entity._command_failed()
        assert isinstance(err, HomeAssistantError)
        assert err.translation_domain == DOMAIN
        assert err.translation_key == "command_failed"
        assert err.translation_placeholders == {"device": mock_light_device.name}

    async def test_accepted_command_passes_through(self, mock_light_device):
        coordinator = _coordinator(mock_light_device, _state(mock_light_device))
        entity = GoveeEntity(coordinator, mock_light_device)
        command = PowerCommand(power_on=True)
        await entity._async_send_command(command)
        coordinator.async_control_device.assert_awaited_once_with(mock_light_device.device_id, command)

    async def test_rejected_command_raises(self, mock_light_device):
        coordinator = _coordinator(mock_light_device, _state(mock_light_device))
        coordinator.async_control_device.return_value = False
        entity = GoveeEntity(coordinator, mock_light_device)
        with pytest.raises(HomeAssistantError) as err:
            await entity._async_send_command(PowerCommand(power_on=False))
        assert err.value.translation_placeholders == {"device": mock_light_device.name}
