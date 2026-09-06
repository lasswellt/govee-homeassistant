"""Follow-ups from the 2026-09 review sweep: issue #186 and PR #187.

- #186: the music-mode switch must send a mode the device advertises. The
  hard-coded default of 1 is rejected by the H6022 (valid: 3/4/5/6) with
  "Parameter value out of range".
- #187: DreamView OFF on the BLE-passthrough path has no opcode; the device
  leaves video mode when given another mode, so the coordinator restores the
  last colour and lets the switch settle to off.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.govee.coordinator import GoveeCoordinator
from custom_components.govee.models import (
    ColorCommand,
    GoveeCapability,
    GoveeDevice,
    GoveeDeviceState,
    MusicModeCommand,
    RGBColor,
    ToggleCommand,
)
from custom_components.govee.models.device import (
    CAPABILITY_MUSIC_MODE,
    CAPABILITY_ON_OFF,
    INSTANCE_MUSIC_MODE,
    INSTANCE_POWER,
)
from custom_components.govee.switch import GoveeMusicModeSwitchEntity

DEV = "AA:BB:CC:DD:EE:FF:60:22"


def _h6022(options) -> GoveeDevice:
    return GoveeDevice(
        device_id=DEV,
        sku="H6022",
        name="Lava lamp",
        device_type="devices.types.light",
        capabilities=(
            GoveeCapability(type=CAPABILITY_ON_OFF, instance=INSTANCE_POWER, parameters={}),
            GoveeCapability(
                type=CAPABILITY_MUSIC_MODE,
                instance=INSTANCE_MUSIC_MODE,
                parameters={
                    "dataType": "STRUCT",
                    "fields": [
                        {"fieldName": "musicMode", "dataType": "ENUM", "options": options, "required": True},
                        {"fieldName": "sensitivity", "dataType": "INTEGER", "required": True},
                    ],
                },
            ),
        ),
    )


def _switch(device, state):
    coordinator = MagicMock()
    coordinator.devices = {device.device_id: device}
    coordinator.get_state = MagicMock(return_value=state)
    coordinator.async_control_device = AsyncMock(return_value=True)
    coordinator.mqtt_connected = False
    entity = GoveeMusicModeSwitchEntity(coordinator, device, use_rest_api=True)
    entity.async_write_ha_state = MagicMock()
    return entity, coordinator


class TestMusicModeDefault:
    H6022_OPTIONS = [
        {"name": "Energic", "value": 5},
        {"name": "Rhythm", "value": 3},
        {"name": "Spectrum", "value": 6},
        {"name": "Rolling", "value": 4},
    ]

    @pytest.mark.asyncio
    async def test_first_advertised_mode_is_the_default(self):
        entity, coordinator = _switch(_h6022(self.H6022_OPTIONS), GoveeDeviceState.create_empty(DEV))

        await entity.async_turn_on()

        cmd = coordinator.async_control_device.call_args[0][1]
        assert isinstance(cmd, MusicModeCommand)
        assert cmd.music_mode == 5

    @pytest.mark.asyncio
    async def test_remembered_mode_is_used_when_valid(self):
        state = GoveeDeviceState.create_empty(DEV)
        state.music_mode_value = 4
        entity, coordinator = _switch(_h6022(self.H6022_OPTIONS), state)

        await entity.async_turn_on()

        assert coordinator.async_control_device.call_args[0][1].music_mode == 4

    @pytest.mark.asyncio
    async def test_remembered_mode_outside_the_advertised_set_is_ignored(self):
        state = GoveeDeviceState.create_empty(DEV)
        state.music_mode_value = 1  # what the old default left behind
        entity, coordinator = _switch(_h6022(self.H6022_OPTIONS), state)

        await entity.async_turn_on()

        assert coordinator.async_control_device.call_args[0][1].music_mode == 5

    @pytest.mark.asyncio
    async def test_device_without_options_keeps_the_old_default(self):
        entity, coordinator = _switch(_h6022([]), GoveeDeviceState.create_empty(DEV))

        await entity.async_turn_on()

        assert coordinator.async_control_device.call_args[0][1].music_mode == 1


class TestDreamviewOffOnPassthrough:
    def _coordinator(self, state):
        coordinator = GoveeCoordinator.__new__(GoveeCoordinator)
        device = MagicMock()
        device.name = "TV backlight"
        device.sku = "H605C"
        coordinator._devices = {DEV: device}
        coordinator._states = {DEV: state}
        coordinator._ble_manager = MagicMock()
        coordinator._ble_manager.available = True
        coordinator._ble_manager.async_send_dreamview = AsyncMock(return_value=True)
        return coordinator

    @pytest.mark.asyncio
    async def test_off_restores_last_colour_when_rest_toggle_fails(self):
        state = GoveeDeviceState.create_empty(DEV)
        state.dreamview_enabled = True
        state.last_color = RGBColor(10, 20, 30)
        coordinator = self._coordinator(state)

        async def control(_device_id, command):
            # The cloud rejects dreamViewToggle on this SKU; colour works.
            return isinstance(command, ColorCommand)

        coordinator.async_control_device = AsyncMock(side_effect=control)

        assert await coordinator.async_send_dreamview(DEV, False) is True

        calls = [c[0][1] for c in coordinator.async_control_device.call_args_list]
        assert isinstance(calls[0], ToggleCommand) and calls[0].enabled is False
        assert isinstance(calls[1], ColorCommand) and calls[1].color == RGBColor(10, 20, 30)
        coordinator._ble_manager.async_send_dreamview.assert_not_awaited()
        assert state.dreamview_enabled is False

    @pytest.mark.asyncio
    async def test_off_falls_back_to_white_without_a_known_colour(self):
        state = GoveeDeviceState.create_empty(DEV)
        state.color = None
        coordinator = self._coordinator(state)
        coordinator.async_control_device = AsyncMock(
            side_effect=lambda _d, cmd: isinstance(cmd, ColorCommand)
        )

        assert await coordinator.async_send_dreamview(DEV, False) is True

        assert coordinator.async_control_device.call_args[0][1].color == RGBColor(255, 255, 255)

    @pytest.mark.asyncio
    async def test_on_still_uses_the_passthrough(self):
        state = GoveeDeviceState.create_empty(DEV)
        coordinator = self._coordinator(state)
        coordinator.async_control_device = AsyncMock(return_value=False)

        assert await coordinator.async_send_dreamview(DEV, True) is True

        coordinator._ble_manager.async_send_dreamview.assert_awaited_once_with(DEV, "H605C")
        assert state.dreamview_enabled is True
