"""Heater target temperature and auto-stop: restore and the shared STRUCT.

The target-temperature number and the auto-stop switch write the same
``targetTemperature`` STRUCT, so each must send the other half's best known
value. These tests drive the entities against a real ``hass`` so the number
restore goes through Home Assistant's own unit conversion.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.components.number import NumberExtraStoredData
from homeassistant.const import UnitOfTemperature
from homeassistant.core import HomeAssistant, State
from homeassistant.util.unit_system import METRIC_SYSTEM, US_CUSTOMARY_SYSTEM
from pytest_homeassistant_custom_component.common import mock_restore_cache, mock_restore_cache_with_extra_data

from custom_components.govee.models import (
    GoveeCapability,
    GoveeDevice,
    GoveeDeviceState,
    TemperatureSettingCommand,
)
from custom_components.govee.models.device import (
    CAPABILITY_ON_OFF,
    CAPABILITY_TEMPERATURE_SETTING,
    DEVICE_TYPE_HEATER,
    INSTANCE_POWER,
    INSTANCE_TARGET_TEMPERATURE,
)
from custom_components.govee.number import GoveeHeaterTemperatureNumber
from custom_components.govee.switch import GoveeAutoStopSwitchEntity

DEVICE_ID = "AA:BB:CC:DD:EE:FF:71:3B"
NUMBER_ID = "number.living_room_heater_target_temperature"
SWITCH_ID = "switch.living_room_heater_auto_stop"


def _heater() -> GoveeDevice:
    """A heater whose auto-stop lives inside the targetTemperature STRUCT (no thermostatToggle)."""
    return GoveeDevice(
        device_id=DEVICE_ID,
        sku="H713B",
        name="Living Room Heater",
        device_type=DEVICE_TYPE_HEATER,
        capabilities=(
            GoveeCapability(type=CAPABILITY_ON_OFF, instance=INSTANCE_POWER, parameters={}),
            GoveeCapability(
                type=CAPABILITY_TEMPERATURE_SETTING,
                instance=INSTANCE_TARGET_TEMPERATURE,
                parameters={
                    "fields": [
                        {"fieldName": "autoStop", "defaultValue": 0},
                        {"fieldName": "temperature", "range": {"min": 5, "max": 30}},
                        {"fieldName": "unit", "defaultValue": "Celsius"},
                    ],
                },
            ),
        ),
        is_group=False,
    )


def _state() -> GoveeDeviceState:
    state = GoveeDeviceState.create_empty(DEVICE_ID)
    state.online = True
    state.power_state = True
    return state


def _coordinator(device: GoveeDevice, state: GoveeDeviceState | None) -> MagicMock:
    coordinator = MagicMock()
    coordinator.devices = {device.device_id: device}
    coordinator.get_state = MagicMock(return_value=state)
    coordinator.async_control_device = AsyncMock(return_value=True)
    coordinator.last_update_success = True
    return coordinator


def _attach(entity, hass: HomeAssistant, entity_id: str):
    entity.hass = hass
    entity.entity_id = entity_id
    entity.async_write_ha_state = MagicMock()
    return entity


def _number_extra(native_value: float | None) -> dict:
    return NumberExtraStoredData(
        native_max_value=30.0,
        native_min_value=5.0,
        native_step=1.0,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        native_value=native_value,
    ).as_dict()


async def _number(hass: HomeAssistant, coordinator: MagicMock, device: GoveeDevice) -> GoveeHeaterTemperatureNumber:
    entity = _attach(GoveeHeaterTemperatureNumber(coordinator, device, temp_range=(5, 30)), hass, NUMBER_ID)
    await entity.async_added_to_hass()
    return entity


async def _switch(hass: HomeAssistant, coordinator: MagicMock, device: GoveeDevice) -> GoveeAutoStopSwitchEntity:
    entity = _attach(GoveeAutoStopSwitchEntity(coordinator, device), hass, SWITCH_ID)
    await entity.async_added_to_hass()
    return entity


def _sent(coordinator: MagicMock) -> TemperatureSettingCommand:
    coordinator.async_control_device.assert_awaited_once()
    _, command = coordinator.async_control_device.call_args.args
    assert isinstance(command, TemperatureSettingCommand)
    return command


class TestHeaterTargetRestore:
    async def test_imperial_install_restores_the_native_celsius_target(self, hass: HomeAssistant):
        """68 °F shown before the restart is 20 °C, not 68 °C (which would show as 154 °F)."""
        hass.config.units = US_CUSTOMARY_SYSTEM
        mock_restore_cache_with_extra_data(
            hass,
            [(State(NUMBER_ID, "68", {"unit_of_measurement": UnitOfTemperature.FAHRENHEIT}), _number_extra(20.0))],
        )
        device = _heater()
        entity = await _number(hass, _coordinator(device, _state()), device)

        assert entity.native_value == 20.0
        assert entity.state == 68

    async def test_restart_after_restart_does_not_drift(self, hass: HomeAssistant):
        hass.config.units = US_CUSTOMARY_SYSTEM
        device = _heater()
        shown, extra = "68", _number_extra(20.0)
        for _ in range(3):
            mock_restore_cache_with_extra_data(hass, [(State(NUMBER_ID, shown), extra)])
            entity = await _number(hass, _coordinator(device, _state()), device)
            assert entity.native_value == 20.0
            assert entity.state == 68
            # What Home Assistant saves at shutdown feeds the next restart.
            shown, extra = str(entity.state), entity.extra_restore_state_data.as_dict()

    @pytest.mark.parametrize(
        ("shown", "unit", "expected"),
        [
            ("68", UnitOfTemperature.FAHRENHEIT, 20.0),
            ("22", UnitOfTemperature.CELSIUS, 22.0),
            ("154", UnitOfTemperature.FAHRENHEIT, 17.0),  # garbage from the old bug, discarded
            ("warm", UnitOfTemperature.CELSIUS, 17.0),
            ("22", None, 17.0),
        ],
    )
    async def test_state_saved_before_native_data_is_converted_from_its_unit(
        self, hass: HomeAssistant, shown, unit, expected
    ):
        """The first restart after upgrading has only the displayed state to go on."""
        hass.config.units = US_CUSTOMARY_SYSTEM
        attributes = {"unit_of_measurement": unit} if unit else {}
        mock_restore_cache(hass, [State(NUMBER_ID, shown, attributes)])
        device = _heater()
        state = _state()
        entity = await _number(hass, _coordinator(device, state), device)
        assert entity.native_value == expected
        if expected == 17.0:
            assert state.heater_temperature is None  # nothing seeded for the auto-stop switch to send

    async def test_out_of_range_restored_value_is_clamped(self, hass: HomeAssistant):
        """A value saved by the old restore bug (154 °C) is pulled back into the slider range."""
        hass.config.units = METRIC_SYSTEM
        mock_restore_cache_with_extra_data(hass, [(State(NUMBER_ID, "154"), _number_extra(154.0))])
        device = _heater()
        entity = await _number(hass, _coordinator(device, _state()), device)
        assert entity.native_value == 30.0

    @pytest.mark.parametrize("extra", [None, {}, {"native_value": None}])
    async def test_nothing_usable_keeps_the_midpoint(self, hass: HomeAssistant, extra):
        hass.config.units = METRIC_SYSTEM
        if extra is None:
            mock_restore_cache(hass, [])
        else:
            payload = _number_extra(None) if extra == {"native_value": None} else extra
            mock_restore_cache_with_extra_data(hass, [(State(NUMBER_ID, "unknown"), payload)])
        device = _heater()
        state = _state()
        entity = await _number(hass, _coordinator(device, state), device)
        assert entity.native_value == 17.0
        assert state.heater_temperature is None

    async def test_polled_target_wins_over_the_restored_value(self, hass: HomeAssistant):
        hass.config.units = METRIC_SYSTEM
        mock_restore_cache_with_extra_data(hass, [(State(NUMBER_ID, "20"), _number_extra(20.0))])
        device = _heater()
        state = _state()
        state.heater_temperature = 24
        entity = await _number(hass, _coordinator(device, state), device)
        assert entity.native_value == 24.0

    async def test_a_later_poll_with_a_new_target_shows_up(self, hass: HomeAssistant):
        """A target changed in the Govee app reaches the slider once the poll reports it."""
        hass.config.units = METRIC_SYSTEM
        mock_restore_cache_with_extra_data(hass, [(State(NUMBER_ID, "20"), _number_extra(20.0))])
        device = _heater()
        state = _state()
        coordinator = _coordinator(device, state)
        entity = await _number(hass, coordinator, device)
        assert entity.native_value == 20.0

        polled = _state()
        polled.heater_temperature = 26
        coordinator.get_state.return_value = polled
        assert entity.native_value == 26.0

    async def test_no_device_state_falls_back_to_the_restored_value(self, hass: HomeAssistant):
        hass.config.units = METRIC_SYSTEM
        mock_restore_cache_with_extra_data(hass, [(State(NUMBER_ID, "21"), _number_extra(21.0))])
        device = _heater()
        entity = await _number(hass, _coordinator(device, None), device)
        assert entity.native_value == 21.0


class TestSharedStructKeepsTheOtherHalf:
    async def test_auto_stop_switch_sends_the_restored_target_not_20(self, hass: HomeAssistant):
        """The poll omits the target; toggling auto-stop must not reset the heater to 20 °C."""
        hass.config.units = METRIC_SYSTEM
        mock_restore_cache_with_extra_data(hass, [(State(NUMBER_ID, "24"), _number_extra(24.0))])
        device = _heater()
        state = _state()
        coordinator = _coordinator(device, state)
        await _number(hass, coordinator, device)
        switch = await _switch(hass, coordinator, device)

        await switch.async_turn_on()

        command = _sent(coordinator)
        assert command.temperature == 24
        assert command.auto_stop == 1

    async def test_target_number_sends_the_restored_auto_stop_not_0(self, hass: HomeAssistant):
        """The poll omits autoStop; setting the target must not silently turn auto-stop off."""
        hass.config.units = METRIC_SYSTEM
        mock_restore_cache(hass, [State(SWITCH_ID, "on")])
        device = _heater()
        state = _state()
        coordinator = _coordinator(device, state)
        switch = await _switch(hass, coordinator, device)
        number = await _number(hass, coordinator, device)

        await number.async_set_native_value(22.0)

        command = _sent(coordinator)
        assert command.temperature == 22
        assert command.auto_stop == 1
        assert switch.is_on is True

    async def test_polled_values_are_not_overwritten_by_restored_ones(self, hass: HomeAssistant):
        hass.config.units = METRIC_SYSTEM
        mock_restore_cache_with_extra_data(
            hass,
            [
                (State(NUMBER_ID, "24"), _number_extra(24.0)),
                (State(SWITCH_ID, "on"), {}),
            ],
        )
        device = _heater()
        state = _state()
        state.heater_temperature = 18
        state.heater_auto_stop = 0
        coordinator = _coordinator(device, state)
        await _number(hass, coordinator, device)
        switch = await _switch(hass, coordinator, device)

        assert state.heater_temperature == 18
        assert state.heater_auto_stop == 0
        assert switch.is_on is False

    @pytest.mark.parametrize("previous", ["unavailable", "unknown"])
    async def test_switch_without_a_known_previous_state_seeds_nothing(self, hass: HomeAssistant, previous):
        hass.config.units = METRIC_SYSTEM
        mock_restore_cache(hass, [State(SWITCH_ID, previous)])
        device = _heater()
        state = _state()
        await _switch(hass, _coordinator(device, state), device)
        assert state.heater_auto_stop is None

    async def test_nothing_known_at_all_keeps_the_documented_defaults(self, hass: HomeAssistant):
        hass.config.units = METRIC_SYSTEM
        mock_restore_cache(hass, [])
        device = _heater()
        state = _state()
        coordinator = _coordinator(device, state)
        switch = await _switch(hass, coordinator, device)

        await switch.async_turn_off()

        command = _sent(coordinator)
        assert command.temperature == 20
        assert command.auto_stop == 0
