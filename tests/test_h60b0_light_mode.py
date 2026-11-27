"""H60B0 floor lamp: bottom light vs ripple light, side light with either.

The lamp never lights its bottom and ripple lights together: switching one on
turns the other off on the device. The side light runs alongside either. Govee reads every
one of these toggles back as ``""``, so the integration has to mirror that
itself, or the switch of the light the lamp just turned off stays "on".
A "Light mode" select picks bottom or ripple in one step.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.core import State
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError

from custom_components.govee import select as select_mod
from custom_components.govee import switch as switch_mod
from custom_components.govee.const import EXCLUSIVE_LIGHT_TOGGLE_MODES
from custom_components.govee.models import GoveeCapability, GoveeDevice, GoveeDeviceState, ToggleCommand
from custom_components.govee.models.device import (
    CAPABILITY_ON_OFF,
    CAPABILITY_TOGGLE,
    DEVICE_TYPE_LIGHT,
    INSTANCE_POWER,
)
from tests.test_cov_coordinator_control import _add, _coordinator

ROOT = Path(__file__).resolve().parent.parent / "custom_components/govee"
DEV = "AA:BB:CC:DD:EE:FF:60:B0"
H60B0_TOGGLES = ("bottomLightToggle", "rippleLightToggle", "sideLightToggle")


def _device(sku: str = "H60B0", toggles: tuple[str, ...] = H60B0_TOGGLES, device_id: str = DEV) -> GoveeDevice:
    return GoveeDevice(
        device_id=device_id,
        sku=sku,
        name="Floor lamp",
        device_type=DEVICE_TYPE_LIGHT,
        capabilities=(
            GoveeCapability(type=CAPABILITY_ON_OFF, instance=INSTANCE_POWER, parameters={}),
            *(GoveeCapability(type=CAPABILITY_TOGGLE, instance=t, parameters={}) for t in toggles),
        ),
    )


class TestModes:
    def test_h60b0_has_bottom_and_ripple_modes(self):
        assert _device().light_toggle_modes == {
            "bottom": ("bottomLightToggle",),
            "ripple": ("rippleLightToggle",),
        }

    def test_bottom_turns_off_ripple(self):
        assert _device().light_toggles_turned_off_by("bottomLightToggle") == ("rippleLightToggle",)

    def test_ripple_turns_off_bottom(self):
        assert _device().light_toggles_turned_off_by("rippleLightToggle") == ("bottomLightToggle",)

    @pytest.mark.parametrize("toggle", ["sideLightToggle", "gradientToggle"])
    def test_side_and_unrelated_toggles_turn_nothing_off(self, toggle):
        assert _device().light_toggles_turned_off_by(toggle) == ()

    def test_other_skus_have_no_modes(self):
        # The H60B3 shares side/bottom but has not been reported as exclusive.
        device = _device("H60B3", ("nebulaLightToggle", "sideLightToggle", "bottomLightToggle"))
        assert device.light_toggle_modes == {}
        assert device.light_toggles_turned_off_by("bottomLightToggle") == ()

    def test_one_group_left_is_not_a_choice(self):
        assert _device(toggles=("rippleLightToggle", "sideLightToggle")).light_toggle_modes == {}

    def test_group_devices_have_no_modes(self):
        from dataclasses import replace

        assert replace(_device(), is_group=True).light_toggle_modes == {}


class TestCoordinatorMirrorsTheLamp:
    def _state(self) -> tuple:
        coord = _coordinator()
        state = _add(coord, _device())
        return coord, state

    def test_bottom_on_marks_ripple_off_and_leaves_side(self):
        coord, state = self._state()
        state.toggles.update(rippleLightToggle=True, sideLightToggle=True)

        coord._apply_optimistic_update(DEV, ToggleCommand(toggle_instance="bottomLightToggle", enabled=True))

        assert state.toggles == {"bottomLightToggle": True, "rippleLightToggle": False, "sideLightToggle": True}

    def test_side_on_leaves_bottom_on(self):
        coord, state = self._state()
        state.toggles.update(bottomLightToggle=True)

        coord._apply_optimistic_update(DEV, ToggleCommand(toggle_instance="sideLightToggle", enabled=True))

        assert state.toggles == {"bottomLightToggle": True, "sideLightToggle": True}

    def test_ripple_on_marks_bottom_off_and_leaves_side(self):
        coord, state = self._state()
        state.toggles.update(bottomLightToggle=True, sideLightToggle=True)

        coord._apply_optimistic_update(DEV, ToggleCommand(toggle_instance="rippleLightToggle", enabled=True))

        assert state.toggles == {"bottomLightToggle": False, "rippleLightToggle": True, "sideLightToggle": True}

    def test_turning_off_touches_nothing_else(self):
        coord, state = self._state()
        state.toggles.update(rippleLightToggle=True)

        coord._apply_optimistic_update(DEV, ToggleCommand(toggle_instance="rippleLightToggle", enabled=False))

        assert state.toggles == {"rippleLightToggle": False}

    @pytest.mark.asyncio
    async def test_switch_of_the_other_group_follows(self):
        coord, state = self._state()
        coord.async_set_updated_data = MagicMock()
        await coord.async_control_device(DEV, ToggleCommand(toggle_instance="rippleLightToggle", enabled=True))

        bottom = switch_mod.GoveeNamedLightSwitchEntity(
            coord, _device(), "bottomLightToggle", "govee_bottom_light", "_bottom_light"
        )
        bottom._is_on = True  # restored as on from before
        assert bottom.is_on is False
        coord.async_set_updated_data.assert_called()


def _mock_coordinator(state: GoveeDeviceState | None) -> MagicMock:
    coordinator = MagicMock()
    device = _device()
    coordinator.devices = {device.device_id: device}
    coordinator.get_state = MagicMock(return_value=state)
    coordinator.async_control_device = AsyncMock(return_value=True)
    return coordinator


def _select(state: GoveeDeviceState | None = None) -> select_mod.GoveeLightModeSelectEntity:
    entity = select_mod.GoveeLightModeSelectEntity(_mock_coordinator(state), _device())
    entity.hass = MagicMock()
    entity.async_write_ha_state = MagicMock()
    return entity


class TestLightModeSelect:
    @pytest.mark.asyncio
    async def test_created_for_h60b0_only(self):
        lamp, other = _device(), _device("H60B3", device_id="AA:BB:CC:DD:EE:FF:60:B3")
        coordinator = MagicMock()
        coordinator.devices = {lamp.device_id: lamp, other.device_id: other}
        coordinator.async_get_scenes = AsyncMock(return_value=[])
        coordinator.async_get_diy_scenes = AsyncMock(return_value=[])
        entry = MagicMock()
        entry.runtime_data = coordinator
        entry.options = {}
        added: list = []
        await select_mod.async_setup_entry(MagicMock(), entry, added.extend)

        modes = [e for e in added if isinstance(e, select_mod.GoveeLightModeSelectEntity)]
        assert [e._device_id for e in modes] == [lamp.device_id]
        assert modes[0].options == ["bottom", "ripple"]
        assert modes[0].unique_id == f"{DEV}_light_mode_select"

    @pytest.mark.asyncio
    async def test_known_off_beats_the_restored_choice(self):
        state = GoveeDeviceState.create_empty(DEV)
        entity = _select(state)
        entity.async_get_last_state = AsyncMock(return_value=State("select.lamp_light_mode", "ripple"))
        await entity.async_added_to_hass()

        state.toggles.update(rippleLightToggle=False)  # ripple switched off by its switch
        assert entity.current_option is None

    def test_current_option_from_shared_toggles(self):
        state = GoveeDeviceState.create_empty(DEV)
        entity = _select(state)
        assert entity.current_option is None

        state.toggles.update(sideLightToggle=True)
        assert entity.current_option is None  # side alone says nothing about the mode

        state.toggles.update(rippleLightToggle=True, bottomLightToggle=False)
        assert entity.current_option == "ripple"

        state.toggles.update(rippleLightToggle=False, bottomLightToggle=True)
        assert entity.current_option == "bottom"

    @pytest.mark.asyncio
    async def test_restores_last_choice_until_state_is_known(self):
        entity = _select(GoveeDeviceState.create_empty(DEV))
        entity.async_get_last_state = AsyncMock(return_value=State("select.lamp_light_mode", "ripple"))
        await entity.async_added_to_hass()
        assert entity.current_option == "ripple"

    @pytest.mark.asyncio
    async def test_ignores_restored_garbage(self):
        entity = _select(None)
        entity.async_get_last_state = AsyncMock(return_value=State("select.lamp_light_mode", "unknown"))
        await entity.async_added_to_hass()
        assert entity.current_option is None

    @pytest.mark.asyncio
    async def test_ripple_switches_only_ripple_on(self):
        entity = _select()
        await entity.async_select_option("ripple")

        sent = [call.args[1] for call in entity.coordinator.async_control_device.await_args_list]
        assert sent == [ToggleCommand(toggle_instance="rippleLightToggle", enabled=True)]
        assert entity.current_option == "ripple"
        entity.async_write_ha_state.assert_called_once()

    @pytest.mark.asyncio
    async def test_bottom_switches_only_bottom_on(self):
        entity = _select()
        await entity.async_select_option("bottom")

        sent = [call.args[1] for call in entity.coordinator.async_control_device.await_args_list]
        assert sent == [ToggleCommand(toggle_instance="bottomLightToggle", enabled=True)]

    @pytest.mark.asyncio
    async def test_unknown_option_raises(self):
        with pytest.raises(ServiceValidationError):
            await _select().async_select_option("everything")

    @pytest.mark.asyncio
    async def test_rejected_command_raises_and_keeps_choice(self):
        entity = _select()
        entity.coordinator.async_control_device = AsyncMock(return_value=False)
        with pytest.raises(HomeAssistantError):
            await entity.async_select_option("bottom")
        assert entity.current_option is None
        entity.async_write_ha_state.assert_not_called()


@pytest.mark.parametrize("mode", sorted({m for modes in EXCLUSIVE_LIGHT_TOGGLE_MODES.values() for m in modes}))
def test_mode_names_and_icons_exist_for_every_language(mode):
    icons = json.loads((ROOT / "icons.json").read_text())
    assert icons["entity"]["select"]["govee_light_mode"]["state"][mode].startswith("mdi:")
    for path in [ROOT / "strings.json", *sorted((ROOT / "translations").glob("*.json"))]:
        strings = json.loads(path.read_text())
        assert strings["entity"]["select"]["govee_light_mode"]["name"], path.name
        assert strings["entity"]["select"]["govee_light_mode"]["state"][mode], path.name
