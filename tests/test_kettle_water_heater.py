"""H7175 water heater, its status entities, and the manager's poll and command rules."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.components.water_heater import WaterHeaterEntityFeature
from homeassistant.const import STATE_OFF, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.restore_state import RestoredExtraData

from custom_components.govee.const import SUFFIX_KETTLE
from custom_components.govee.diagnostics import _device_diag
from custom_components.govee.kettle.entities import (
    GoveeKettleDiySlotSensor,
    GoveeKettleHeatingStatusSensor,
    GoveeKettleKeepWarmBinarySensor,
    GoveeKettleKeepWarmMinutesSensor,
    GoveeKettleKeepWarmRemainingSensor,
    kettle_binary_sensors,
    kettle_sensors,
)
from custom_components.govee.kettle.manager import KettleManager
from custom_components.govee.kettle.modes import kettle_modes, mode_key, selected_mode
from custom_components.govee.models import (
    GoveeDevice,
    GoveeDeviceState,
    PowerCommand,
    TemperatureSettingCommand,
    WorkModeCommand,
)
from custom_components.govee.models.device import INSTANCE_SLIDER_TEMPERATURE
from custom_components.govee.switch import GoveeAppliancePowerSwitchEntity
from custom_components.govee.water_heater import GoveeKettleWaterHeater

from .kettle_samples import (
    DEVICE_ID,
    H7175_DEVICE,
    H7175_MQTT,
    H7175_STATE,
    H7175_STATE_MANUAL,
    MODE_MANUAL_176,
    mqtt_frames,
)
from .test_setup_entry import _setup_entry

OPERATIONS = [
    "off",
    "custom_1",
    "custom_2",
    "custom_3",
    "custom_4",
    "green_tea",
    "oolong_tea",
    "coffee",
    "black_tea_boil",
    "manual",
]


def _slider(temperature: int, unit: str = "Fahrenheit") -> TemperatureSettingCommand:
    return TemperatureSettingCommand(
        temperature=temperature, unit=unit, auto_stop=None, setting_instance=INSTANCE_SLIDER_TEMPERATURE
    )


def _device(sku: str = "H7175", **changes) -> GoveeDevice:
    return GoveeDevice.from_api_response({**H7175_DEVICE, "sku": sku, **changes})


def _state(*, pushed: bool = True, power: bool = False, api=H7175_STATE) -> GoveeDeviceState:
    state = GoveeDeviceState.create_empty(DEVICE_ID)
    state.update_from_api(api)
    if pushed:
        _manager(state).on_push(DEVICE_ID, state, H7175_MQTT, mqtt_frames())
        state.update_from_api(api)  # the next poll reports {"workMode": 1} alone
    state.power_state = power
    return state


def _manager(state: GoveeDeviceState) -> KettleManager:
    coordinator = SimpleNamespace(
        config_entry=SimpleNamespace(options={}),
        devices={DEVICE_ID: _device()},
        get_state=lambda _id: state,
        account_temperature_unit=lambda _id: None,
    )
    return KettleManager(coordinator)  # type: ignore[arg-type]


def _coordinator(state: GoveeDeviceState | None, *, fahrenheit: bool = True) -> MagicMock:
    coordinator = MagicMock()
    coordinator.get_state = MagicMock(return_value=state)
    coordinator.async_control_device = AsyncMock(return_value=True)
    coordinator.kettles.reports_fahrenheit = MagicMock(return_value=fahrenheit)
    coordinator.last_update_success = True
    return coordinator


def _heater(state: GoveeDeviceState | None, *, device: GoveeDevice | None = None, **kwargs):
    coordinator = _coordinator(state, **kwargs)
    entity = GoveeKettleWaterHeater(coordinator, device or _device())
    entity.hass = MagicMock()
    entity.async_write_ha_state = MagicMock()
    return entity, coordinator


def _sent(coordinator: MagicMock) -> list:
    return [c.args[1] for c in coordinator.async_control_device.await_args_list]


# --------------------------------------------------------------------------- #
# Modes
# --------------------------------------------------------------------------- #


class TestModes:
    def test_stable_keys_from_the_capability_then_manual(self):
        modes = kettle_modes(_device())
        assert list(modes) == OPERATIONS[1:]
        assert (modes["custom_3"], modes["coffee"], modes["manual"]) == ((1, 3), (4, 0), (6, 0))

    def test_unknown_names_are_keyed_gracefully(self):
        cap = {
            "type": "devices.capabilities.work_mode",
            "instance": "workMode",
            "parameters": {
                "fields": [
                    {
                        "fieldName": "workMode",
                        "options": [
                            {"name": "Tè 茶", "value": 7},
                            {"name": "茶", "value": 8},
                            {"name": "", "value": 3},
                            {"name": "Broken", "value": "?"},
                        ],
                    },
                    {"fieldName": "modeValue", "options": [{"name": "Tè 茶", "defaultValue": 2}]},
                ]
            },
        }
        device = GoveeDevice.from_api_response({**H7175_DEVICE, "capabilities": [cap]})
        assert [opt["key"] for opt in device.get_kettle_mode_options()] == ["t", "mode_8_0"]

    def test_selected_mode(self):
        modes = kettle_modes(_device())
        assert selected_mode(modes, _state()) == "custom_4"
        assert selected_mode(modes, _state(pushed=False)) is None  # which slot is unknown
        assert selected_mode(modes, None) is None
        state = _state()
        state.work_mode = 9
        assert selected_mode(modes, state) is None
        assert mode_key(modes, 1, 9) is None


# --------------------------------------------------------------------------- #
# Water heater
# --------------------------------------------------------------------------- #


class TestWaterHeater:
    def test_identity_features_and_range(self):
        entity, _ = _heater(_state())
        assert entity.unique_id == f"{DEVICE_ID}{SUFFIX_KETTLE}"
        assert entity.operation_list == OPERATIONS
        assert entity.supported_features == (
            WaterHeaterEntityFeature.TARGET_TEMPERATURE
            | WaterHeaterEntityFeature.OPERATION_MODE
            | WaterHeaterEntityFeature.ON_OFF
        )
        assert (entity.temperature_unit, entity.min_temp, entity.max_temp) == (UnitOfTemperature.FAHRENHEIT, 104, 212)
        celsius, _ = _heater(_state(), fahrenheit=False)
        assert (celsius.temperature_unit, celsius.min_temp, celsius.max_temp) == (UnitOfTemperature.CELSIUS, 40, 100)

    def test_state(self):
        entity, _ = _heater(_state(power=True))
        assert (entity.current_operation, entity.current_temperature, entity.target_temperature) == (
            "custom_4",
            91.0,
            176.0,
        )
        assert _heater(_state(power=False))[0].current_operation == STATE_OFF
        empty, _ = _heater(None)
        assert (empty.current_operation, empty.current_temperature, empty.target_temperature) == (None, None, None)

    def test_manual_from_the_poll_and_the_push(self):
        """The live bug: workMode 6 must be a valid state, not unknown."""
        polled = GoveeDeviceState.create_empty(DEVICE_ID)
        polled.update_from_api(H7175_STATE_MANUAL)
        polled.power_state = True
        assert _heater(polled)[0].current_operation == "manual"
        state = _state(power=True)
        _manager(state).on_push(DEVICE_ID, state, {}, [bytes.fromhex(MODE_MANUAL_176)])
        assert _heater(state)[0].current_operation == "manual"

    def test_without_power_or_modes(self):
        no_power = _device(capabilities=[c for c in H7175_DEVICE["capabilities"] if c["instance"] != "powerSwitch"])
        entity, _ = _heater(_state(power=False), device=no_power)
        assert entity.operation_list == OPERATIONS[1:]
        assert entity.current_operation == "custom_4"
        bare = _device(capabilities=[c for c in H7175_DEVICE["capabilities"] if c["instance"] == "powerSwitch"])
        entity, _ = _heater(_state(), device=bare)
        assert entity.supported_features == WaterHeaterEntityFeature.ON_OFF

    async def test_mode_while_off_switches_on_mode_first(self):
        entity, coordinator = _heater(_state(power=False))
        await entity.async_set_operation_mode("green_tea")
        assert _sent(coordinator) == [WorkModeCommand(work_mode=2, mode_value=0), PowerCommand(power_on=True)]

    async def test_mode_while_on_and_off(self):
        entity, coordinator = _heater(_state(power=True))
        await entity.async_set_operation_mode("custom_2")
        await entity.async_set_operation_mode(STATE_OFF)
        await entity.async_turn_on()
        assert _sent(coordinator) == [
            WorkModeCommand(work_mode=1, mode_value=2),
            PowerCommand(power_on=False),
            PowerCommand(power_on=True),
        ]

    async def test_manual_resends_the_target(self):
        entity, coordinator = _heater(_state(power=True))
        await entity.async_set_operation_mode("manual")
        assert _sent(coordinator) == [_slider(176)]
        state = _state(power=True)
        state.kettle_target_temperature = 80.0
        celsius, coordinator = _heater(state, fahrenheit=False)
        await celsius.async_set_operation_mode("manual")
        assert _sent(coordinator) == [_slider(80, "Celsius")]

    @pytest.mark.parametrize(
        ("mode", "key"), [("espresso", "unsupported_mode"), ("manual", "kettle_manual_no_target")]
    )
    async def test_refused_modes(self, mode, key):
        state = _state()
        state.kettle_target_temperature = None
        entity, coordinator = _heater(state)
        with pytest.raises(ServiceValidationError) as err:
            await entity.async_set_operation_mode(mode)
        assert err.value.translation_key == key
        coordinator.async_control_device.assert_not_awaited()

    async def test_set_temperature_clamps_and_selects_manual(self):
        entity, coordinator = _heater(_state(power=False))
        await entity.async_set_temperature(temperature=250)
        await entity.async_set_temperature(temperature=180, operation_mode="manual")
        assert _sent(coordinator) == [_slider(212), _slider(180), PowerCommand(power_on=True)]

    async def test_set_temperature_with_another_mode_is_refused(self):
        entity, coordinator = _heater(_state())
        with pytest.raises(ServiceValidationError) as err:
            await entity.async_set_temperature(temperature=180, operation_mode="green_tea")
        assert err.value.translation_key == "kettle_target_with_mode"
        coordinator.async_control_device.assert_not_awaited()

    async def test_set_temperature_with_a_mode_only(self):
        entity, coordinator = _heater(_state(power=True))
        await entity.async_set_temperature(operation_mode="coffee")
        await entity.async_set_temperature()
        assert _sent(coordinator) == [WorkModeCommand(work_mode=4, mode_value=0)]


class TestRestore:
    """The poll reports {"workMode": 1} alone; the custom slot comes back from the restore data."""

    async def _added(self, state, data):
        entity, coordinator = _heater(state)
        entity.async_get_last_extra_data = AsyncMock(return_value=RestoredExtraData(data) if data else None)
        await entity.async_added_to_hass()
        return entity, coordinator

    async def test_restored(self):
        state = _state(pushed=False, power=True)
        entity, _ = await self._added(state, {"work_mode": 1, "mode_value": 3})
        assert entity.current_operation == "custom_3"
        assert entity.extra_restore_state_data.as_dict() == {"work_mode": 1, "mode_value": 3}

    async def test_waits_for_the_first_state(self):
        entity, coordinator = await self._added(None, {"work_mode": 1, "mode_value": 3})
        state = _state(pushed=False, power=True)
        coordinator.get_state.return_value = state
        entity._handle_coordinator_update()
        assert state.kettle_mode_value == 3

    @pytest.mark.parametrize(
        "data",
        [
            None,
            {"work_mode": 1, "mode_value": 9},
            {"work_mode": 6, "mode_value": 0},
            {"work_mode": "1", "mode_value": 3},
        ],
    )
    async def test_nothing_usable(self, data):
        state = _state(pushed=False, power=True)
        await self._added(state, data)
        assert state.kettle_mode_value is None

    async def test_a_known_slot_or_another_mode_wins(self):
        known = _state(power=True)
        await self._added(known, {"work_mode": 1, "mode_value": 2})
        assert known.kettle_mode_value == 4
        other = _state(pushed=False, power=True)
        other.work_mode = 2
        await self._added(other, {"work_mode": 1, "mode_value": 2})
        assert other.kettle_mode_value is None

    def test_only_a_slot_is_stored(self):
        manual = GoveeDeviceState.create_empty(DEVICE_ID)
        manual.update_from_api(H7175_STATE_MANUAL)
        assert _heater(manual)[0].extra_restore_state_data is None


# --------------------------------------------------------------------------- #
# Status entities and the power switch
# --------------------------------------------------------------------------- #


class TestStatusEntities:
    def test_values(self):
        state = _state()
        coordinator = _coordinator(state)
        device = _device()
        heating = GoveeKettleHeatingStatusSensor(coordinator, device)
        minutes = GoveeKettleKeepWarmMinutesSensor(coordinator, device)
        diy = GoveeKettleDiySlotSensor(coordinator, device)
        keep_warm = GoveeKettleKeepWarmBinarySensor(coordinator, device)
        assert (heating.native_value, minutes.native_value, diy.native_value, keep_warm.is_on) == (
            "idle",
            120,
            "custom_4",
            True,
        )
        assert diy.options == ["custom_1", "custom_2", "custom_3", "custom_4"]
        assert heating.unique_id == f"{DEVICE_ID}_kettle_heating_status"

    def test_keep_warm_remaining_only_while_on(self):
        state = _state()
        state.kettle_keep_warm_remaining = 116
        sensor = GoveeKettleKeepWarmRemainingSensor(_coordinator(state), _device())
        assert sensor.native_value == 116
        state.kettle_keep_warm_enabled = False
        assert sensor.native_value is None
        assert GoveeKettleKeepWarmRemainingSensor(_coordinator(None), _device()).native_value is None

    def test_unknown_until_pushed(self):
        coordinator = _coordinator(None)
        device = _device()
        entities = [
            GoveeKettleHeatingStatusSensor(coordinator, device).native_value,
            GoveeKettleKeepWarmMinutesSensor(coordinator, device).native_value,
            GoveeKettleDiySlotSensor(coordinator, device).native_value,
            GoveeKettleKeepWarmBinarySensor(coordinator, device).is_on,
        ]
        assert entities == [None, None, None, None]

    def test_only_h7175_kettles_get_them(self):
        no_slots = _device(device="AA:BB:CC:DD:71:75:00:02")
        no_slots = GoveeDevice.from_api_response(
            {
                **H7175_DEVICE,
                "device": "AA:BB:CC:DD:71:75:00:02",
                "capabilities": [c for c in H7175_DEVICE["capabilities"] if c["instance"] != "workMode"],
            }
        )
        devices = [_device(), no_slots, _device("H717A", device="x"), _device(device="12345")]
        coordinator = MagicMock(devices={d.device_id: d for d in devices})
        assert [type(e).__name__ for e in kettle_sensors(coordinator)] == [
            "GoveeKettleHeatingStatusSensor",
            "GoveeKettleKeepWarmMinutesSensor",
            "GoveeKettleKeepWarmRemainingSensor",
            "GoveeKettleDiySlotSensor",
            "GoveeKettleHeatingStatusSensor",
            "GoveeKettleKeepWarmMinutesSensor",
            "GoveeKettleKeepWarmRemainingSensor",
        ]
        assert len(kettle_binary_sensors(coordinator)) == 2

    def test_power_switch_name(self):
        """On an H7175 the water heater takes the device name; other kettles keep theirs."""
        h7175 = GoveeAppliancePowerSwitchEntity(_coordinator(_state()), _device())
        h717a = GoveeAppliancePowerSwitchEntity(_coordinator(_state()), _device("H717A"))
        assert "_attr_name" not in vars(h7175)
        assert h717a.name is None


# --------------------------------------------------------------------------- #
# Manager: polls and commands
# --------------------------------------------------------------------------- #


class TestManager:
    def test_poll_carries_push_only_fields_and_the_slot(self):
        existing = _state()
        polled = GoveeDeviceState.create_empty(DEVICE_ID)
        polled.update_from_api(H7175_STATE)
        _manager(existing).merge_poll(DEVICE_ID, existing, polled)
        assert (polled.kettle_mode_value, polled.kettle_diy_slot, polled.kettle_heating_status) == (4, 4, "idle")
        assert (polled.kettle_keep_warm_enabled, polled.kettle_preset_temperatures[2]) == (True, {0: 180.0})
        assert polled.kettle_keep_warm_remaining == 120

    def test_poll_of_another_work_mode_drops_the_slot(self):
        existing = _state()
        polled = GoveeDeviceState.create_empty(DEVICE_ID)
        polled.update_from_api(H7175_STATE_MANUAL)
        _manager(existing).merge_poll(DEVICE_ID, existing, polled)
        assert (polled.work_mode, polled.kettle_mode_value) == (6, None)

    def test_unlisted_work_mode_is_logged_once(self, caplog):
        caplog.set_level("DEBUG", logger="custom_components.govee.kettle.manager")
        state = _state()
        manager = _manager(state)
        for _ in range(2):
            polled = GoveeDeviceState.create_empty(DEVICE_ID)
            polled.work_mode = 9
            manager.merge_poll(DEVICE_ID, state, polled)
        manager.merge_poll("gone", state, polled)
        assert caplog.text.count("reports workMode 9") == 1

    def test_target_command_shows_manual_in_the_kettle_unit(self):
        state = _state()
        _manager(state).apply_command(DEVICE_ID, state, _slider(85, "Celsius"))
        assert (state.work_mode, state.kettle_mode_value, state.kettle_target_temperature) == (6, None, 185.0)

    def test_mode_command_remembers_the_slot(self):
        state = _state()
        _manager(state).apply_command(DEVICE_ID, state, WorkModeCommand(work_mode=1, mode_value=2))
        assert (state.work_mode, state.mode_value, state.kettle_mode_value) == (1, 2, 2)

    def test_diagnostics_frame_history(self):
        state = _state()
        manager = _manager(state)
        frames = mqtt_frames()
        manager.on_push(DEVICE_ID, state, {}, frames)
        manager.on_push(DEVICE_ID, state, {}, frames + [bytes.fromhex("3a2201003c3c0000000000000000000000000019")])
        recent = [entry["frame"] for entry in manager.recent_frames(DEVICE_ID)]
        # Distinct status frames once each (the two aa 19 are one kind), the echo kept.
        assert len(recent) == len(frames) - 1 + 1
        assert recent.count("aa050100c45c0100aaf802000000000000000067") == 1
        coordinator = MagicMock()
        coordinator.kettles = manager
        diag = _device_diag(coordinator, DEVICE_ID, _device(), {}, {})
        assert diag["recent_frames"] == manager.recent_frames(DEVICE_ID)


# --------------------------------------------------------------------------- #
# Through Home Assistant
# --------------------------------------------------------------------------- #


@pytest.fixture
def _custom_integrations(hass: HomeAssistant, enable_custom_integrations: None) -> None:
    """Load the integration without starting Bluetooth (as in test_setup_entry)."""
    hass.config.components.add("bluetooth_adapters")
    hass.config.components.add("network")


@pytest.mark.usefixtures("_custom_integrations")
class TestThroughHomeAssistant:
    async def test_entities_and_commands(self, hass: HomeAssistant):
        entry = await _setup_entry(hass, _device(), _state(pushed=False))
        registry = er.async_get(hass)
        by_unique_id = {e.unique_id: e for e in er.async_entries_for_config_entry(registry, entry.entry_id)}
        assert by_unique_id[DEVICE_ID].domain == "switch"  # unique id kept
        for suffix, domain in [
            (SUFFIX_KETTLE, "water_heater"),
            ("_kettle_heating_status", "sensor"),
            ("_kettle_keep_warm_minutes", "sensor"),
            ("_kettle_keep_warm_remaining", "sensor"),
            ("_kettle_diy_slot", "sensor"),
            ("_kettle_keep_warm_status", "binary_sensor"),
        ]:
            assert by_unique_id[f"{DEVICE_ID}{suffix}"].domain == domain
        heater = by_unique_id[f"{DEVICE_ID}{SUFFIX_KETTLE}"].entity_id
        state = hass.states.get(heater)
        assert state.state == STATE_OFF
        assert state.attributes["operation_list"] == OPERATIONS
        # Metric test instance: 176 °F and 91 °F shown in °C.
        assert (state.attributes["temperature"], state.attributes["current_temperature"]) == (80, 33)

        coordinator = entry.runtime_data
        coordinator._api_client.control_device = AsyncMock(return_value=True)
        await hass.services.async_call(
            "water_heater", "set_operation_mode", {"entity_id": heater, "operation_mode": "coffee"}, True
        )
        assert hass.states.get(heater).state == "coffee"
        # 85 °C from the metric UI is 185 °F on the wire, and shows manual.
        await hass.services.async_call(
            "water_heater", "set_temperature", {"entity_id": heater, "temperature": 85}, True
        )
        assert coordinator._api_client.control_device.await_args.args[2] == _slider(185)
        assert hass.states.get(heater).state == "manual"

    async def test_other_kettles_get_no_water_heater(self, hass: HomeAssistant):
        entry = await _setup_entry(hass, _device("H717A"), _state(pushed=False))
        registry = er.async_get(hass)
        unique_ids = {e.unique_id for e in er.async_entries_for_config_entry(registry, entry.entry_id)}
        assert DEVICE_ID in unique_ids  # the power switch
        assert not any("_kettle" in unique_id for unique_id in unique_ids)
