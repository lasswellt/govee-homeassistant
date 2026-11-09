"""H7130 heater: heat-level/setpoint writes imply power on; the setpoint rounds (issue #265)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.govee.models import GoveeCapability, GoveeDevice
from homeassistant.exceptions import HomeAssistantError

from custom_components.govee import switch as switch_mod
from custom_components.govee.models.commands import (
    OscillationCommand,
    TemperatureSettingCommand,
    ToggleCommand,
    WorkModeCommand,
)
from custom_components.govee.models.state import GoveeDeviceState
from custom_components.govee.switch import GoveeHeaterOscillationSwitchEntity
from custom_components.govee.number import GoveeHeaterTemperatureNumber
from tests.coordinator_mocks import follow_online_flag
from tests.test_cov_coordinator_control import _add, _coordinator

HEATER_ID = "AA:BB:CC:DD:EE:FF:00:22"
FAN_ID = "AA:BB:CC:DD:EE:FF:00:33"

# Developer-API capabilities the H7130 advertises (heat level, setpoint, power).
_CAPS = (
    GoveeCapability(type="devices.capabilities.on_off", instance="powerSwitch", parameters={}),
    GoveeCapability(
        type="devices.capabilities.work_mode",
        instance="workMode",
        parameters={
            "dataType": "STRUCT",
            "fields": [
                {
                    "fieldName": "workMode",
                    "dataType": "ENUM",
                    "options": [
                        {"name": "Low", "value": 1},
                        {"name": "Medium", "value": 2},
                        {"name": "High", "value": 3},
                    ],
                },
                {"fieldName": "modeValue", "dataType": "ENUM", "options": [{"name": "Low", "value": 0}]},
            ],
        },
    ),
    GoveeCapability(
        type="devices.capabilities.temperature_setting",
        instance="targetTemperature",
        parameters={
            "dataType": "STRUCT",
            "fields": [
                {"fieldName": "temperature", "dataType": "INTEGER", "range": {"min": 5, "max": 30, "precision": 1}},
            ],
        },
    ),
)


def _device(device_id: str, device_type: str) -> GoveeDevice:
    return GoveeDevice(device_id=device_id, sku="H7130", name="Heater", device_type=device_type, capabilities=_CAPS)


def _heater_state() -> GoveeDeviceState:
    state = GoveeDeviceState.create_empty(HEATER_ID)
    state.power_state = False
    return state


def test_heat_level_write_turns_power_on() -> None:
    coord = _coordinator()
    state = _add(coord, _device(HEATER_ID, "devices.types.heater"), _heater_state())
    coord._apply_optimistic_update(HEATER_ID, WorkModeCommand(work_mode=3, mode_value=0))
    assert state.power_state is True
    assert state.work_mode == 3


def test_setpoint_write_turns_power_on() -> None:
    coord = _coordinator()
    state = _add(coord, _device(HEATER_ID, "devices.types.heater"), _heater_state())
    coord._apply_optimistic_update(HEATER_ID, TemperatureSettingCommand(temperature=21, auto_stop=0))
    assert state.power_state is True
    assert state.heater_temperature == 21


def test_fan_work_mode_write_leaves_power_alone() -> None:
    coord = _coordinator()
    fan_state = GoveeDeviceState.create_empty(FAN_ID)
    fan_state.power_state = False
    state = _add(coord, _device(FAN_ID, "devices.types.fan"), fan_state)
    coord._apply_optimistic_update(FAN_ID, WorkModeCommand(work_mode=1, mode_value=2))
    assert state.power_state is False
    assert state.work_mode == 1


def _number() -> tuple[GoveeHeaterTemperatureNumber, MagicMock]:
    device = _device(HEATER_ID, "devices.types.heater")
    state = GoveeDeviceState.create_empty(HEATER_ID)
    state.heater_auto_stop = 0
    coordinator = MagicMock()
    coordinator.devices = {HEATER_ID: device}
    coordinator.get_state = MagicMock(return_value=state)
    coordinator.async_control_device = AsyncMock(return_value=True)
    follow_online_flag(coordinator)
    entity = GoveeHeaterTemperatureNumber(coordinator=coordinator, device=device, temp_range=(5, 30))
    entity.hass = MagicMock()
    entity.async_write_ha_state = MagicMock()
    return entity, coordinator


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (20.999999999999996, 21),  # 69.8 F converted to C; int() gave 20
        (21.4, 21),
        (21.5, 22),  # round() is half-to-even: 21.5 -> 22 (22 is even)
        (22.5, 22),  # half-to-even again: 22.5 -> 22
        (2.0, 5),  # clamped to native min
        (45.0, 30),  # clamped to native max
    ],
)
async def test_setpoint_rounds_not_truncates(value: float, expected: int) -> None:
    entity, coordinator = _number()
    await entity.async_set_native_value(value)
    command = coordinator.async_control_device.await_args.args[1]
    assert isinstance(command, TemperatureSettingCommand)
    assert command.temperature == expected


_OSCILLATION_CAP = GoveeCapability(
    type="devices.capabilities.toggle",
    instance="oscillationToggle",
    parameters={"dataType": "ENUM", "options": [{"name": "on", "value": 1}, {"name": "off", "value": 0}]},
)


def _oscillating_device(device_type: str = "devices.types.heater", with_cap: bool = True) -> GoveeDevice:
    caps = _CAPS + ((_OSCILLATION_CAP,) if with_cap else ())
    return GoveeDevice(device_id=HEATER_ID, sku="H7130", name="Heater", device_type=device_type, capabilities=caps)


async def _added_switches(device: GoveeDevice) -> list:
    coordinator = MagicMock()
    coordinator.devices = {device.device_id: device}
    entry = MagicMock()
    entry.runtime_data = coordinator
    entry.options = {}
    added: list = []
    await switch_mod.async_setup_entry(MagicMock(), entry, added.extend)
    return [e for e in added if isinstance(e, GoveeHeaterOscillationSwitchEntity)]


@pytest.mark.parametrize(
    ("device_type", "with_cap", "expected"),
    [
        ("devices.types.heater", True, 1),
        ("devices.types.heater", False, 0),
        ("devices.types.fan", True, 0),
    ],
)
async def test_oscillation_switch_creation(device_type: str, with_cap: bool, expected: int) -> None:
    created = await _added_switches(_oscillating_device(device_type, with_cap))
    assert len(created) == expected
    if expected:
        assert created[0].unique_id == f"{HEATER_ID}_heater_oscillation"


def _oscillation_entity(oscillating: bool | None, accept: bool = True):
    device = _oscillating_device()
    state = GoveeDeviceState.create_empty(HEATER_ID)
    state.online = True
    state.oscillating = oscillating
    coordinator = MagicMock()
    coordinator.devices = {HEATER_ID: device}
    coordinator.get_state = MagicMock(return_value=state)
    coordinator.async_control_device = AsyncMock(return_value=accept)
    follow_online_flag(coordinator)
    return GoveeHeaterOscillationSwitchEntity(coordinator, device), coordinator, state


def test_oscillation_switch_follows_state() -> None:
    entity, _, state = _oscillation_entity(True)
    assert entity.is_on is True
    state.oscillating = False
    assert entity.is_on is False


def test_oscillation_switch_unknown_without_state() -> None:
    entity, coordinator, _ = _oscillation_entity(None)
    coordinator.get_state.return_value = None
    assert entity.is_on is None


@pytest.mark.parametrize(("method", "value"), [("async_turn_on", True), ("async_turn_off", False)])
async def test_oscillation_switch_sends_command(method: str, value: bool) -> None:
    entity, coordinator, _ = _oscillation_entity(not value)
    await getattr(entity, method)()
    coordinator.async_control_device.assert_awaited_once_with(HEATER_ID, OscillationCommand(oscillating=value))


async def test_oscillation_switch_rejected_command_raises() -> None:
    entity, _, _ = _oscillation_entity(False, accept=False)
    with pytest.raises(HomeAssistantError):
        await entity.async_turn_on()


# --- Auto Stop via the STRUCT field, thermostat as its own switch ---

_THERMOSTAT_CAP = GoveeCapability(
    type="devices.capabilities.toggle",
    instance="thermostatToggle",
    parameters={"dataType": "ENUM", "options": [{"name": "on", "value": 1}, {"name": "off", "value": 0}]},
)
_AUTO_STOP_TEMP_CAP = GoveeCapability(
    type="devices.capabilities.temperature_setting",
    instance="targetTemperature",
    parameters={
        "dataType": "STRUCT",
        "fields": [
            {"fieldName": "temperature", "dataType": "INTEGER", "range": {"min": 5, "max": 30, "precision": 1}},
            {
                "fieldName": "autoStop",
                "dataType": "ENUM",
                "options": [{"name": "Auto Stop", "value": 1}, {"name": "Maintain", "value": 0}],
            },
        ],
    },
)


def _both_caps_device(sku: str) -> GoveeDevice:
    caps = tuple(c for c in _CAPS if c.instance != "targetTemperature") + (_AUTO_STOP_TEMP_CAP, _THERMOSTAT_CAP)
    return GoveeDevice(
        device_id=HEATER_ID, sku=sku, name="Heater", device_type="devices.types.heater", capabilities=caps
    )


def _entity(cls, sku: str = "H7130", restored: str | None = None):
    device = _both_caps_device(sku)
    state = GoveeDeviceState.create_empty(HEATER_ID)
    state.online = True
    state.heater_temperature = 23
    coordinator = MagicMock()
    coordinator.devices = {HEATER_ID: device}
    coordinator.get_state = MagicMock(return_value=state)
    coordinator.async_control_device = AsyncMock(return_value=True)
    follow_online_flag(coordinator)
    entity = cls(coordinator, device)
    entity.hass = MagicMock()
    entity.async_write_ha_state = MagicMock()
    if restored is not None:
        entity.async_get_last_state = AsyncMock(return_value=MagicMock(state=restored))
    return entity, coordinator, state


@pytest.mark.parametrize(("method", "value"), [("async_turn_on", 1), ("async_turn_off", 0)])
async def test_h7130_auto_stop_sends_struct(method: str, value: int) -> None:
    entity, coordinator, _ = _entity(switch_mod.GoveeAutoStopSwitchEntity)
    await getattr(entity, method)()
    coordinator.async_control_device.assert_awaited_once_with(
        HEATER_ID, TemperatureSettingCommand(temperature=23, auto_stop=value)
    )


async def test_other_heater_with_both_caps_still_uses_thermostat_toggle() -> None:
    entity, coordinator, _ = _entity(switch_mod.GoveeAutoStopSwitchEntity, sku="H7131")
    await entity.async_turn_on()
    coordinator.async_control_device.assert_awaited_once_with(
        HEATER_ID, ToggleCommand(toggle_instance="thermostatToggle", enabled=True)
    )


@pytest.mark.parametrize(("method", "value"), [("async_turn_on", True), ("async_turn_off", False)])
async def test_h7130_thermostat_switch_sends_toggle(method: str, value: bool) -> None:
    entity, coordinator, _ = _entity(switch_mod.GoveeHeaterThermostatSwitchEntity)
    await getattr(entity, method)()
    coordinator.async_control_device.assert_awaited_once_with(
        HEATER_ID, ToggleCommand(toggle_instance="thermostatToggle", enabled=value)
    )
    assert entity.unique_id == f"{HEATER_ID}_heater_thermostat"


async def test_thermostat_switch_rejected_command_raises() -> None:
    entity, coordinator, _ = _entity(switch_mod.GoveeHeaterThermostatSwitchEntity)
    coordinator.async_control_device.return_value = False
    with pytest.raises(HomeAssistantError):
        await entity.async_turn_on()


async def test_thermostat_switch_restores_and_prefers_state() -> None:
    entity, _, state = _entity(switch_mod.GoveeHeaterThermostatSwitchEntity, restored="on")
    await entity.async_added_to_hass()
    assert state.heater_thermostat is True
    assert entity.is_on is True
    state.heater_thermostat = False
    assert entity.is_on is False
    state.heater_thermostat = None
    assert entity.is_on is True


async def test_thermostat_switch_restore_ignores_unavailable() -> None:
    entity, _, state = _entity(switch_mod.GoveeHeaterThermostatSwitchEntity, restored="unavailable")
    await entity.async_added_to_hass()
    assert state.heater_thermostat is None
    assert entity.is_on is False


async def test_thermostat_switch_created_only_for_h7130() -> None:
    for sku, expected in (("H7130", 1), ("H7131", 0)):
        device = _both_caps_device(sku)
        coordinator = MagicMock()
        coordinator.devices = {HEATER_ID: device}
        entry = MagicMock()
        entry.runtime_data = coordinator
        entry.options = {}
        added: list = []
        await switch_mod.async_setup_entry(MagicMock(), entry, added.extend)
        assert len([e for e in added if isinstance(e, switch_mod.GoveeHeaterThermostatSwitchEntity)]) == expected
        assert len([e for e in added if isinstance(e, switch_mod.GoveeAutoStopSwitchEntity)]) == 1


@pytest.mark.parametrize(("sku", "thermo", "auto_stop"), [("H7130", True, None), ("H7131", None, 1)])
def test_thermostat_toggle_optimistic_target(sku: str, thermo: bool | None, auto_stop: int | None) -> None:
    coord = _coordinator()
    device = _both_caps_device(sku)
    state = _add(coord, device, _heater_state())
    coord._apply_optimistic_update(HEATER_ID, ToggleCommand(toggle_instance="thermostatToggle", enabled=True))
    assert state.heater_thermostat is thermo
    assert state.heater_auto_stop == auto_stop
