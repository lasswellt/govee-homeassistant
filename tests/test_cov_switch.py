"""Coverage tests for the switch platform.

The feature-specific files (test_light_zone.py, test_issue_114.py,
test_heater.py, ...) each cover one entity in depth. This file covers the
paths between them: every ``async_setup_entry`` branch, the probe
live-polling switch, the plug and appliance power switches, the night-light
switch, both music-mode transports, DreamView, the H713C auto-stop shape,
the AWS IoT outlet switch, and RestoreEntity restoration on every optimistic
switch.
"""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock, MagicMock

from homeassistant.components.switch import SwitchDeviceClass
from homeassistant.const import EntityCategory
from homeassistant.core import State
from homeassistant.exceptions import HomeAssistantError
import pytest

from custom_components.govee import switch as switch_mod
from custom_components.govee.const import (
    CONF_ENABLE_H6104_BLE,
    SUFFIX_DREAMVIEW,
    SUFFIX_HEATER_AUTO_STOP,
    SUFFIX_MAIN_LIGHT,
    SUFFIX_MUSIC_MODE,
    SUFFIX_NIGHT_LIGHT,
)
from custom_components.govee.models import (
    GoveeCapability,
    GoveeDevice,
    GoveeDeviceState,
    MusicModeCommand,
    PowerCommand,
    TemperatureSettingCommand,
    ToggleCommand,
)
from custom_components.govee.models.device import (
    CAPABILITY_COLOR_SETTING,
    CAPABILITY_MODE,
    CAPABILITY_MUSIC_MODE,
    CAPABILITY_ON_OFF,
    CAPABILITY_RANGE,
    CAPABILITY_TEMPERATURE_SETTING,
    CAPABILITY_TOGGLE,
    DEVICE_TYPE_AROMA_DIFFUSER,
    DEVICE_TYPE_HEATER,
    DEVICE_TYPE_KETTLE,
    DEVICE_TYPE_LIGHT,
    DEVICE_TYPE_PLUG,
    INSTANCE_BRIGHTNESS,
    INSTANCE_COLOR_RGB,
    INSTANCE_MAIN_LIGHT_TOGGLE,
    INSTANCE_MUSIC_MODE,
    INSTANCE_NIGHT_LIGHT,
    INSTANCE_POWER,
    INSTANCE_TARGET_TEMPERATURE,
    INSTANCE_THERMOSTAT_TOGGLE,
)
from custom_components.govee.switch import (
    GoveeAppliancePowerSwitchEntity,
    GoveeAutoStopSwitchEntity,
    GoveeDreamViewSwitchEntity,
    GoveeMqttOutletSwitchEntity,
    GoveeMusicModeSwitchEntity,
    GoveeNamedLightSwitchEntity,
    GoveeNightLightSwitchEntity,
    GoveePlugSwitchEntity,
    GoveeProbeLivePollingSwitch,
    GoveeSocketSwitchEntity,
)
from .coordinator_mocks import follow_online_flag

# --------------------------------------------------------------------------- #
# Device builders
# --------------------------------------------------------------------------- #


def _cap(cap_type: str, instance: str, params: dict | None = None) -> GoveeCapability:
    return GoveeCapability(type=cap_type, instance=instance, parameters=params or {})


def _device(device_id: str, sku: str, name: str, device_type: str, *caps: GoveeCapability) -> GoveeDevice:
    return GoveeDevice(
        device_id=device_id,
        sku=sku,
        name=name,
        device_type=device_type,
        capabilities=tuple(caps),
        is_group=False,
    )


_MUSIC_OPTIONS = [
    {"name": "Energic", "value": 5},
    {"name": "Rhythm", "value": 3},
    {"name": "Spectrum", "value": 6},
]

_TEMPERATURE_STRUCT = {
    "dataType": "STRUCT",
    "fields": [
        {
            "fieldName": "autoStop",
            "defaultValue": 0,
            "options": [{"name": "Maintain", "value": 0}, {"name": "Auto stop", "value": 1}],
        },
        {"fieldName": "temperature", "range": {"min": 5, "max": 30}},
        {"fieldName": "unit", "defaultValue": "Celsius"},
    ],
}


def _probe() -> GoveeDevice:
    return GoveeDevice.synthetic_probe_thermometer("AA:BB:CC:DD:EE:FF:51:92", "H5192", "Grill Probe")


def _light_with_night_light() -> GoveeDevice:
    """A real RGB light with a nightlightToggle: keeps the plain switch."""
    return _device(
        "AA:BB:CC:DD:EE:FF:60:01",
        "H6001",
        "Desk Lamp",
        DEVICE_TYPE_LIGHT,
        _cap(CAPABILITY_ON_OFF, INSTANCE_POWER),
        _cap(CAPABILITY_RANGE, INSTANCE_BRIGHTNESS),
        _cap(CAPABILITY_COLOR_SETTING, INSTANCE_COLOR_RGB),
        _cap(CAPABILITY_TOGGLE, INSTANCE_NIGHT_LIGHT),
    )


def _struct_music_light(options: list[dict] | None = None) -> GoveeDevice:
    """H6022-shaped light with a STRUCT musicMode capability (REST path)."""
    return _device(
        "AA:BB:CC:DD:EE:FF:60:22",
        "H6022",
        "Lava lamp",
        DEVICE_TYPE_LIGHT,
        _cap(CAPABILITY_ON_OFF, INSTANCE_POWER),
        _cap(
            CAPABILITY_MUSIC_MODE,
            INSTANCE_MUSIC_MODE,
            {
                "dataType": "STRUCT",
                "fields": [
                    {
                        "fieldName": "musicMode",
                        "dataType": "ENUM",
                        "options": _MUSIC_OPTIONS if options is None else options,
                    },
                    {"fieldName": "sensitivity", "dataType": "INTEGER", "range": {"min": 0, "max": 100}},
                ],
            },
        ),
    )


def _ble_music_light() -> GoveeDevice:
    """Legacy light whose musicMode capability has no STRUCT fields (BLE path)."""
    return _device(
        "AA:BB:CC:DD:EE:FF:61:99",
        "H6199",
        "Old strip",
        DEVICE_TYPE_LIGHT,
        _cap(CAPABILITY_ON_OFF, INSTANCE_POWER),
        _cap(CAPABILITY_MUSIC_MODE, INSTANCE_MUSIC_MODE),
    )


def _heater_h7130() -> GoveeDevice:
    """Heater with a dedicated thermostatToggle capability."""
    return _device(
        "AA:BB:CC:DD:EE:FF:71:30",
        "H7130",
        "Living Room Heater",
        DEVICE_TYPE_HEATER,
        _cap(CAPABILITY_ON_OFF, INSTANCE_POWER),
        _cap(CAPABILITY_TEMPERATURE_SETTING, INSTANCE_TARGET_TEMPERATURE, _TEMPERATURE_STRUCT),
        _cap(CAPABILITY_TOGGLE, INSTANCE_THERMOSTAT_TOGGLE),
    )


def _heater_h713c() -> GoveeDevice:
    """Heater carrying autoStop inside the targetTemperature STRUCT (issue #29)."""
    return _device(
        "AA:BB:CC:DD:EE:FF:71:3C",
        "H713C",
        "Office Heater",
        DEVICE_TYPE_HEATER,
        _cap(CAPABILITY_ON_OFF, INSTANCE_POWER),
        _cap(CAPABILITY_TEMPERATURE_SETTING, INSTANCE_TARGET_TEMPERATURE, _TEMPERATURE_STRUCT),
    )


def _kettle() -> GoveeDevice:
    return _device(
        "AA:BB:CC:DD:EE:FF:71:7A",
        "H717A",
        "Smart Kettle Pro",
        DEVICE_TYPE_KETTLE,
        _cap(CAPABILITY_ON_OFF, INSTANCE_POWER),
    )


def _aroma_diffuser() -> GoveeDevice:
    return _device(
        "AA:BB:CC:DD:EE:FF:71:61",
        "H7161",
        "Aroma Diffuser Pro",
        DEVICE_TYPE_AROMA_DIFFUSER,
        _cap(CAPABILITY_ON_OFF, INSTANCE_POWER),
    )


def _h5160() -> GoveeDevice:
    """Three-outlet strip the Developer API exposes as a single powerSwitch."""
    return _device(
        "AA:BB:CC:DD:EE:FF:51:60",
        "H5160",
        "Strip",
        DEVICE_TYPE_PLUG,
        _cap(CAPABILITY_ON_OFF, INSTANCE_POWER),
    )


def _h5089() -> GoveeDevice:
    """Outlet extender: two sockets plus a nightlight with its own light entity."""
    return _device(
        "AA:BB:CC:DD:EE:FF:50:89",
        "H5089",
        "Smart Outlet Extender",
        DEVICE_TYPE_PLUG,
        _cap(CAPABILITY_ON_OFF, INSTANCE_POWER),
        _cap(CAPABILITY_TOGGLE, INSTANCE_NIGHT_LIGHT),
        _cap(CAPABILITY_RANGE, INSTANCE_BRIGHTNESS),
        _cap(CAPABILITY_COLOR_SETTING, INSTANCE_COLOR_RGB),
        _cap(CAPABILITY_TOGGLE, "socketToggle2"),
        _cap(CAPABILITY_TOGGLE, "socketToggle1"),
    )


def _h1310() -> GoveeDevice:
    return _device(
        "AA:BB:CC:DD:EE:FF:13:10",
        "H1310",
        "Bedroom Fan Light",
        DEVICE_TYPE_LIGHT,
        _cap(CAPABILITY_ON_OFF, INSTANCE_POWER),
        _cap(CAPABILITY_TOGGLE, INSTANCE_MAIN_LIGHT_TOGGLE),
        _cap(CAPABILITY_TOGGLE, "backgroundLightToggle"),
        _cap(CAPABILITY_TOGGLE, "fanToggle"),
        _cap(CAPABILITY_MODE, "fanSpeedMode"),
    )


# --------------------------------------------------------------------------- #
# Coordinator / entity helpers
# --------------------------------------------------------------------------- #


def _online_state(device: GoveeDevice) -> GoveeDeviceState:
    state = GoveeDeviceState.create_empty(device.device_id)
    state.online = True
    return state


def _coordinator(device: GoveeDevice, state: GoveeDeviceState | None, **overrides) -> MagicMock:
    coordinator = MagicMock()
    coordinator.devices = {device.device_id: device}
    coordinator.get_state = MagicMock(return_value=state)
    coordinator.async_control_device = AsyncMock(return_value=True)
    coordinator.last_update_success = True
    coordinator.mqtt_connected = False
    for name, value in overrides.items():
        setattr(coordinator, name, value)
    return follow_online_flag(coordinator)


def _attach(entity):
    """Give the entity what async_write_ha_state needs without a real hass."""
    entity.hass = MagicMock()
    entity.async_write_ha_state = MagicMock()
    return entity


async def _restore(entity, last_state: State | None) -> None:
    """Run async_added_to_hass with a canned previous state."""
    entity.async_get_last_state = AsyncMock(return_value=last_state)
    await entity.async_added_to_hass()


def _names(entities) -> list[str]:
    return [type(e).__name__ for e in entities]


# --------------------------------------------------------------------------- #
# async_setup_entry
# --------------------------------------------------------------------------- #


class TestSetupEntry:
    @pytest.mark.parametrize("enabled", [False, True])
    async def test_h6104_tv_switch_requires_experimental_option(self, enabled):
        device = _device(
            "tv",
            "H6104",
            "TV",
            DEVICE_TYPE_LIGHT,
            _cap(CAPABILITY_ON_OFF, INSTANCE_POWER),
        )
        entry = MagicMock()
        entry.runtime_data.devices = {"tv": device}
        entry.options = {CONF_ENABLE_H6104_BLE: enabled}
        added = []
        await switch_mod.async_setup_entry(MagicMock(), entry, added.extend)
        assert any(isinstance(entity, GoveeDreamViewSwitchEntity) for entity in added) is enabled

    async def _setup(self, *devices: GoveeDevice) -> list:
        coordinator = MagicMock()
        coordinator.devices = {d.device_id: d for d in devices}
        entry = MagicMock()
        entry.runtime_data = coordinator
        entry.options = {}
        added: list = []
        await switch_mod.async_setup_entry(MagicMock(), entry, added.extend)
        return added

    async def test_probe_thermometer_gets_only_the_polling_switch(self):
        added = await self._setup(_probe())
        assert _names(added) == ["GoveeProbeLivePollingSwitch"]
        assert added[0].unique_id == "AA:BB:CC:DD:EE:FF:51:92_probe_live_polling"

    async def test_plain_plug_gets_the_outlet_switch(self, mock_plug_device):
        added = await self._setup(mock_plug_device)
        assert _names(added) == ["GoveePlugSwitchEntity"]

    async def test_real_light_keeps_the_night_light_switch(self):
        added = await self._setup(_light_with_night_light())
        assert _names(added) == ["GoveeNightLightSwitchEntity"]

    async def test_outlet_extender_gets_sockets_but_no_night_light_switch(self):
        added = await self._setup(_h5089())
        names = _names(added)
        assert names.count("GoveeSocketSwitchEntity") == 2
        assert "GoveePlugSwitchEntity" in names
        # Its nightlight has brightness/colour, so the light platform owns it.
        assert "GoveeNightLightSwitchEntity" not in names
        # Developer-API socket toggles win over the AWS IoT bitmask outlets.
        assert "GoveeMqttOutletSwitchEntity" not in names

    async def test_group_device_gets_no_switches_and_says_why(self, mock_group_device, caplog):
        with caplog.at_level(logging.DEBUG, logger="custom_components.govee.switch"):
            added = await self._setup(mock_group_device)
        assert added == []
        assert "Skipping music mode/DreamView switches for group device All Lights" in caplog.text

    async def test_struct_music_mode_switch_uses_rest(self):
        added = await self._setup(_struct_music_light())
        (music,) = [e for e in added if isinstance(e, GoveeMusicModeSwitchEntity)]
        assert music._use_rest_api is True

    async def test_legacy_music_mode_switch_uses_ble(self):
        added = await self._setup(_ble_music_light())
        (music,) = [e for e in added if isinstance(e, GoveeMusicModeSwitchEntity)]
        assert music._use_rest_api is False

    @pytest.mark.parametrize("heater", [_heater_h7130, _heater_h713c])
    async def test_both_heater_shapes_get_auto_stop_and_power(self, heater):
        added = await self._setup(heater())
        assert sorted(_names(added)) == ["GoveeAppliancePowerSwitchEntity", "GoveeAutoStopSwitchEntity"]

    @pytest.mark.parametrize("appliance", [_kettle, _aroma_diffuser])
    async def test_kettle_and_diffuser_get_a_power_switch(self, appliance):
        added = await self._setup(appliance())
        assert _names(added) == ["GoveeAppliancePowerSwitchEntity"]

    async def test_dreamview_device_gets_the_switch(self, mock_dreamview_device):
        added = await self._setup(mock_dreamview_device)
        assert _names(added) == ["GoveeDreamViewSwitchEntity"]

    async def test_h5160_gets_three_mqtt_outlet_switches(self):
        added = await self._setup(_h5160())
        outlets = [e for e in added if isinstance(e, GoveeMqttOutletSwitchEntity)]
        assert [o._outlet_index for o in outlets] == [0, 1, 2]
        assert "GoveePlugSwitchEntity" in _names(added)

    async def test_mixed_inventory_is_set_up_in_one_pass(self, mock_plug_device):
        added = await self._setup(_probe(), mock_plug_device, _h1310(), _kettle())
        names = _names(added)
        assert names.count("GoveeNamedLightSwitchEntity") == 2
        assert "GoveeProbeLivePollingSwitch" in names
        assert "GoveePlugSwitchEntity" in names
        assert "GoveeAppliancePowerSwitchEntity" in names


# --------------------------------------------------------------------------- #
# Probe live-polling switch
# --------------------------------------------------------------------------- #


class TestProbeLivePollingSwitch:
    def _entity(self, polling: bool = False, last_update_success: bool = True):
        device = _probe()
        coordinator = _coordinator(device, None, last_update_success=last_update_success)
        coordinator.is_probe_polling = MagicMock(return_value=polling)
        coordinator.set_probe_polling = MagicMock()
        return _attach(GoveeProbeLivePollingSwitch(coordinator, device)), coordinator, device

    def test_identity(self):
        entity, _, device = self._entity()
        assert entity.unique_id == f"{device.device_id}_probe_live_polling"
        assert entity.entity_category is EntityCategory.CONFIG
        assert entity.translation_key == "probe_live_polling"

    def test_is_on_follows_the_coordinator(self):
        entity, coordinator, device = self._entity(polling=True)
        assert entity.is_on is True
        coordinator.is_probe_polling.assert_called_with(device.device_id)

    def test_always_available(self):
        # A local toggle: neither a failed poll nor missing device state hides it.
        entity, _, _ = self._entity(last_update_success=False)
        assert entity.available is True

    async def test_turn_on_arms_polling(self):
        entity, coordinator, device = self._entity()
        await entity.async_turn_on()
        coordinator.set_probe_polling.assert_called_once_with(device.device_id, True)
        entity.async_write_ha_state.assert_called_once()

    async def test_turn_off_disarms_polling(self):
        entity, coordinator, device = self._entity(polling=True)
        await entity.async_turn_off()
        coordinator.set_probe_polling.assert_called_once_with(device.device_id, False)
        entity.async_write_ha_state.assert_called_once()

    async def test_restores_armed_polling(self):
        entity, coordinator, device = self._entity()
        await _restore(entity, State("switch.grill_probe_live_polling", "on"))
        coordinator.set_probe_polling.assert_called_once_with(device.device_id, True)

    @pytest.mark.parametrize("previous", [None, "off"])
    async def test_off_or_missing_state_leaves_polling_disarmed(self, previous):
        entity, coordinator, _ = self._entity()
        last = State("switch.grill_probe_live_polling", previous) if previous else None
        await _restore(entity, last)
        coordinator.set_probe_polling.assert_not_called()


# --------------------------------------------------------------------------- #
# Plug and appliance power switches
# --------------------------------------------------------------------------- #


class TestPlugSwitch:
    def _entity(self, mock_plug_device, state):
        coordinator = _coordinator(mock_plug_device, state)
        return GoveePlugSwitchEntity(coordinator, mock_plug_device), coordinator

    def test_identity(self, mock_plug_device):
        entity, _ = self._entity(mock_plug_device, _online_state(mock_plug_device))
        assert entity.device_class is SwitchDeviceClass.OUTLET
        assert entity.unique_id == mock_plug_device.device_id
        assert entity.name is None  # takes the device name

    @pytest.mark.parametrize("power", [True, False])
    def test_is_on_reads_power_state(self, mock_plug_device, power):
        state = _online_state(mock_plug_device)
        state.power_state = power
        entity, _ = self._entity(mock_plug_device, state)
        assert entity.is_on is power

    def test_is_on_unknown_without_state(self, mock_plug_device):
        entity, _ = self._entity(mock_plug_device, None)
        assert entity.is_on is None

    @pytest.mark.parametrize("power", [True, False])
    async def test_turn_on_off_sends_power_command(self, mock_plug_device, power):
        entity, coordinator = self._entity(mock_plug_device, _online_state(mock_plug_device))
        if power:
            await entity.async_turn_on()
        else:
            await entity.async_turn_off()
        device_id, cmd = coordinator.async_control_device.call_args[0]
        assert device_id == mock_plug_device.device_id
        assert isinstance(cmd, PowerCommand)
        assert cmd.power_on is power

    async def test_rejected_command_raises(self, mock_plug_device):
        entity, coordinator = self._entity(mock_plug_device, _online_state(mock_plug_device))
        coordinator.async_control_device.return_value = False
        with pytest.raises(HomeAssistantError) as err:
            await entity.async_turn_on()
        assert err.value.translation_key == "command_failed"
        assert err.value.translation_placeholders == {"device": mock_plug_device.name}


class TestAppliancePowerSwitch:
    def _entity(self, state):
        device = _kettle()
        coordinator = _coordinator(device, state)
        return GoveeAppliancePowerSwitchEntity(coordinator, device), coordinator, device

    def test_identity(self):
        entity, _, device = self._entity(_online_state(device := _kettle()))
        assert entity.unique_id == device.device_id
        assert entity.translation_key == "govee_appliance_power"
        assert entity.name is None

    @pytest.mark.parametrize("power", [True, False])
    def test_is_on_reads_power_state(self, power):
        state = _online_state(_kettle())
        state.power_state = power
        entity, _, _ = self._entity(state)
        assert entity.is_on is power

    def test_is_on_unknown_without_state(self):
        entity, _, _ = self._entity(None)
        assert entity.is_on is None

    async def test_turn_on_sends_power_on(self):
        entity, coordinator, device = self._entity(_online_state(_kettle()))
        await entity.async_turn_on()
        device_id, cmd = coordinator.async_control_device.call_args[0]
        assert device_id == device.device_id
        assert isinstance(cmd, PowerCommand) and cmd.power_on is True

    async def test_turn_off_sends_power_off(self):
        entity, coordinator, _ = self._entity(_online_state(_kettle()))
        await entity.async_turn_off()
        cmd = coordinator.async_control_device.call_args[0][1]
        assert isinstance(cmd, PowerCommand) and cmd.power_on is False

    async def test_rejected_command_raises(self):
        entity, coordinator, _ = self._entity(_online_state(_kettle()))
        coordinator.async_control_device.return_value = False
        with pytest.raises(HomeAssistantError):
            await entity.async_turn_off()


# --------------------------------------------------------------------------- #
# Night-light switch
# --------------------------------------------------------------------------- #


class TestNightLightSwitch:
    def _entity(self):
        device = _light_with_night_light()
        coordinator = _coordinator(device, _online_state(device))
        return _attach(GoveeNightLightSwitchEntity(coordinator, device)), coordinator, device

    def test_identity_and_default(self):
        entity, _, device = self._entity()
        assert entity.unique_id == f"{device.device_id}{SUFFIX_NIGHT_LIGHT}"
        assert entity.is_on is False

    async def test_turn_on_sends_nightlight_toggle(self):
        entity, coordinator, device = self._entity()
        await entity.async_turn_on()
        device_id, cmd = coordinator.async_control_device.call_args[0]
        assert device_id == device.device_id
        assert isinstance(cmd, ToggleCommand)
        assert cmd.toggle_instance == INSTANCE_NIGHT_LIGHT
        assert cmd.enabled is True
        assert entity.is_on is True
        entity.async_write_ha_state.assert_called_once()

    async def test_turn_off_sends_nightlight_toggle(self):
        entity, coordinator, _ = self._entity()
        entity._is_on = True
        await entity.async_turn_off()
        cmd = coordinator.async_control_device.call_args[0][1]
        assert isinstance(cmd, ToggleCommand)
        assert cmd.toggle_instance == INSTANCE_NIGHT_LIGHT
        assert cmd.enabled is False
        assert entity.is_on is False

    async def test_rejected_command_keeps_state(self):
        entity, coordinator, _ = self._entity()
        coordinator.async_control_device.return_value = False
        with pytest.raises(HomeAssistantError):
            await entity.async_turn_on()
        assert entity.is_on is False
        entity.async_write_ha_state.assert_not_called()

    @pytest.mark.parametrize(("previous", "expected"), [("on", True), ("off", False), (None, False)])
    async def test_restores_optimistic_state(self, previous, expected):
        entity, _, _ = self._entity()
        last = State("switch.desk_lamp_night_light", previous) if previous else None
        await _restore(entity, last)
        assert entity.is_on is expected


# --------------------------------------------------------------------------- #
# Socket, AWS IoT outlet and named light switches
# --------------------------------------------------------------------------- #


class TestSocketSwitchTurnOn:
    async def test_turn_on_sends_toggle_and_updates_live_state(self):
        device = _h5089()
        state = _online_state(device)
        state.toggles = {"socketToggle1": True, "socketToggle2": False}
        coordinator = _coordinator(device, state)
        entity = _attach(GoveeSocketSwitchEntity(coordinator, device, "socketToggle2", 1))
        assert entity.is_on is False

        await entity.async_turn_on()

        cmd = coordinator.async_control_device.call_args[0][1]
        assert isinstance(cmd, ToggleCommand)
        assert cmd.toggle_instance == "socketToggle2"
        assert cmd.enabled is True
        assert state.toggles["socketToggle2"] is True
        assert entity.is_on is True
        entity.async_write_ha_state.assert_called_once()

    async def test_turn_on_without_state_still_writes(self):
        device = _h5089()
        coordinator = _coordinator(device, None)
        entity = _attach(GoveeSocketSwitchEntity(coordinator, device, "socketToggle1", 0))
        await entity.async_turn_on()
        assert entity.is_on is None  # no live state to read back
        entity.async_write_ha_state.assert_called_once()


class TestMqttOutletSwitch:
    def _entity(self, accepted: bool = True, state: GoveeDeviceState | None = None):
        device = _h5160()
        coordinator = _coordinator(device, state, mqtt_connected=True)
        coordinator.async_set_mqtt_outlet = AsyncMock(return_value=accepted)
        return _attach(GoveeMqttOutletSwitchEntity(coordinator, device, 0)), coordinator, device

    async def test_turn_off_routes_over_aws_iot(self):
        entity, coordinator, device = self._entity()
        entity._is_on = True
        await entity.async_turn_off()
        coordinator.async_set_mqtt_outlet.assert_awaited_once_with(device.device_id, 0, False)
        assert entity.is_on is False
        entity.async_write_ha_state.assert_called_once()

    async def test_rejected_command_raises_and_keeps_state(self):
        entity, _, _ = self._entity(accepted=False)
        with pytest.raises(HomeAssistantError):
            await entity.async_turn_on()
        assert entity.is_on is False
        entity.async_write_ha_state.assert_not_called()

    @pytest.mark.parametrize(("previous", "expected"), [("on", True), ("off", False), (None, False)])
    async def test_restores_optimistic_state(self, previous, expected):
        entity, _, _ = self._entity(state=_online_state(_h5160()))
        last = State("switch.strip_socket_1", previous) if previous else None
        await _restore(entity, last)
        assert entity.is_on is expected
        assert entity.assumed_state is True  # nothing reported yet


class TestNamedLightSwitch:
    def _entity(self, state: GoveeDeviceState | None):
        device = _h1310()
        coordinator = _coordinator(device, state)
        entity = GoveeNamedLightSwitchEntity(
            coordinator, device, INSTANCE_MAIN_LIGHT_TOGGLE, "govee_main_light", SUFFIX_MAIN_LIGHT
        )
        return _attach(entity), coordinator, device

    def test_identity(self):
        entity, _, device = self._entity(None)
        assert entity.unique_id == f"{device.device_id}{SUFFIX_MAIN_LIGHT}"
        assert entity.translation_key == "govee_main_light"

    @pytest.mark.parametrize(("live", "optimistic", "expected"), [(True, False, True), (False, True, False)])
    def test_reported_toggle_beats_optimistic_state(self, live, optimistic, expected):
        state = _online_state(_h1310())
        state.toggles[INSTANCE_MAIN_LIGHT_TOGGLE] = live
        entity, _, _ = self._entity(state)
        entity._is_on = optimistic
        assert entity.is_on is expected

    def test_falls_back_to_optimistic_when_unreported(self):
        entity, _, _ = self._entity(_online_state(_h1310()))
        entity._is_on = True
        assert entity.is_on is True

    async def test_turn_off_sends_toggle(self):
        entity, coordinator, _ = self._entity(_online_state(_h1310()))
        entity._is_on = True
        await entity.async_turn_off()
        cmd = coordinator.async_control_device.call_args[0][1]
        assert isinstance(cmd, ToggleCommand)
        assert cmd.toggle_instance == INSTANCE_MAIN_LIGHT_TOGGLE
        assert cmd.enabled is False
        assert entity.is_on is False
        entity.async_write_ha_state.assert_called_once()

    async def test_rejected_turn_off_keeps_state(self):
        entity, coordinator, _ = self._entity(_online_state(_h1310()))
        entity._is_on = True
        coordinator.async_control_device.return_value = False
        with pytest.raises(HomeAssistantError):
            await entity.async_turn_off()
        assert entity.is_on is True

    @pytest.mark.parametrize(("previous", "expected"), [("on", True), ("off", False), (None, False)])
    async def test_restores_optimistic_state(self, previous, expected):
        entity, _, _ = self._entity(_online_state(_h1310()))
        last = State("switch.bedroom_fan_light_main_light", previous) if previous else None
        await _restore(entity, last)
        assert entity.is_on is expected


# --------------------------------------------------------------------------- #
# Music mode switch
# --------------------------------------------------------------------------- #


class TestMusicModeSwitch:
    def _entity(self, *, use_rest_api: bool, state=None, mqtt_connected: bool = False, accepted: bool = True):
        device = _struct_music_light() if use_rest_api else _ble_music_light()
        coordinator = _coordinator(device, state, mqtt_connected=mqtt_connected)
        coordinator.async_control_device = AsyncMock(return_value=accepted)
        coordinator.async_send_music_mode = AsyncMock(return_value=accepted)
        entity = _attach(GoveeMusicModeSwitchEntity(coordinator, device, use_rest_api=use_rest_api))
        return entity, coordinator, device

    def test_unique_id(self):
        entity, _, device = self._entity(use_rest_api=True)
        assert entity.unique_id == f"{device.device_id}{SUFFIX_MUSIC_MODE}"

    def test_ble_switch_needs_mqtt(self):
        device = _ble_music_light()
        entity, _, _ = self._entity(use_rest_api=False, state=_online_state(device), mqtt_connected=False)
        assert entity.available is False

    @pytest.mark.parametrize("online", [True, False])
    def test_ble_switch_with_mqtt_follows_device_online(self, online):
        state = _online_state(_ble_music_light())
        state.online = online
        entity, _, _ = self._entity(use_rest_api=False, state=state, mqtt_connected=True)
        assert entity.available is online

    def test_rest_switch_does_not_need_mqtt(self):
        entity, _, _ = self._entity(use_rest_api=True, state=_online_state(_struct_music_light()))
        assert entity.available is True

    @pytest.mark.parametrize(("reported", "optimistic", "expected"), [(True, False, True), (False, True, False)])
    def test_is_on_prefers_reported_state(self, reported, optimistic, expected):
        state = _online_state(_struct_music_light())
        state.music_mode_enabled = reported
        entity, _, _ = self._entity(use_rest_api=True, state=state)
        entity._is_on = optimistic
        assert entity.is_on is expected

    def test_is_on_falls_back_to_optimistic(self):
        entity, _, _ = self._entity(use_rest_api=True, state=_online_state(_struct_music_light()))
        entity._is_on = True
        assert entity.is_on is True

    async def test_rest_turn_on_reuses_reported_sensitivity_and_mode(self):
        state = _online_state(_struct_music_light())
        state.music_sensitivity = 80
        state.music_mode_value = 6
        entity, coordinator, device = self._entity(use_rest_api=True, state=state)

        await entity.async_turn_on()

        device_id, cmd = coordinator.async_control_device.call_args[0]
        assert device_id == device.device_id
        assert isinstance(cmd, MusicModeCommand)
        assert (cmd.music_mode, cmd.sensitivity, cmd.auto_color) == (6, 80, 1)
        coordinator.async_send_music_mode.assert_not_awaited()
        assert entity.is_on is True
        entity.async_write_ha_state.assert_called_once()

    async def test_rest_turn_on_defaults_without_state(self):
        entity, coordinator, _ = self._entity(use_rest_api=True, state=None)
        await entity.async_turn_on()
        cmd = coordinator.async_control_device.call_args[0][1]
        # First advertised mode and the 50 % default sensitivity.
        assert (cmd.music_mode, cmd.sensitivity) == (5, 50)

    async def test_ble_turn_on_uses_the_mqtt_passthrough(self):
        entity, coordinator, device = self._entity(use_rest_api=False, mqtt_connected=True)
        await entity.async_turn_on()
        coordinator.async_send_music_mode.assert_awaited_once_with(device.device_id, enabled=True)
        coordinator.async_control_device.assert_not_awaited()
        assert entity.is_on is True

    @pytest.mark.parametrize("use_rest_api", [True, False])
    async def test_rejected_turn_on_raises_and_keeps_state(self, use_rest_api):
        entity, _, _ = self._entity(use_rest_api=use_rest_api, mqtt_connected=True, accepted=False)
        with pytest.raises(HomeAssistantError):
            await entity.async_turn_on()
        assert entity.is_on is False
        entity.async_write_ha_state.assert_not_called()

    async def test_turn_off_hands_the_last_scene_to_the_coordinator(self):
        state = _online_state(_struct_music_light())
        state.last_scene_id = "12"
        state.last_scene_name = "Sunset"
        entity, coordinator, device = self._entity(use_rest_api=True, state=state)
        entity._is_on = True

        await entity.async_turn_off()

        coordinator.async_send_music_mode.assert_awaited_once_with(
            device.device_id, enabled=False, last_scene_id="12", last_scene_name="Sunset"
        )
        assert entity.is_on is False
        entity.async_write_ha_state.assert_called_once()

    async def test_turn_off_without_state_passes_no_scene(self):
        entity, coordinator, device = self._entity(use_rest_api=False, state=None, mqtt_connected=True)
        await entity.async_turn_off()
        coordinator.async_send_music_mode.assert_awaited_once_with(
            device.device_id, enabled=False, last_scene_id=None, last_scene_name=None
        )

    async def test_rejected_turn_off_raises_and_keeps_state(self):
        entity, _, _ = self._entity(use_rest_api=True, accepted=False)
        entity._is_on = True
        with pytest.raises(HomeAssistantError):
            await entity.async_turn_off()
        assert entity.is_on is True
        entity.async_write_ha_state.assert_not_called()


# --------------------------------------------------------------------------- #
# DreamView switch
# --------------------------------------------------------------------------- #


class TestDreamViewSwitch:
    def _entity(self, mock_dreamview_device, state, accepted: bool = True, last_update_success: bool = True):
        coordinator = _coordinator(mock_dreamview_device, state, last_update_success=last_update_success)
        coordinator.async_send_dreamview = AsyncMock(return_value=accepted)
        return _attach(GoveeDreamViewSwitchEntity(coordinator, mock_dreamview_device)), coordinator

    def test_unique_id(self, mock_dreamview_device):
        entity, _ = self._entity(mock_dreamview_device, None)
        assert entity.unique_id == f"{mock_dreamview_device.device_id}{SUFFIX_DREAMVIEW}"

    @pytest.mark.parametrize("online", [True, False])
    def test_available_follows_device_online(self, mock_dreamview_device, online):
        state = _online_state(mock_dreamview_device)
        state.online = online
        entity, _ = self._entity(mock_dreamview_device, state)
        assert entity.available is online

    def test_unavailable_when_coordinator_failed(self, mock_dreamview_device):
        entity, _ = self._entity(
            mock_dreamview_device, _online_state(mock_dreamview_device), last_update_success=False
        )
        assert entity.available is False

    @pytest.mark.parametrize(("reported", "expected"), [(True, True), (False, False), (None, False)])
    def test_is_on_reads_device_state(self, mock_dreamview_device, reported, expected):
        state = _online_state(mock_dreamview_device)
        state.dreamview_enabled = reported
        entity, _ = self._entity(mock_dreamview_device, state)
        assert entity.is_on is expected

    def test_is_on_false_without_state(self, mock_dreamview_device):
        entity, _ = self._entity(mock_dreamview_device, None)
        assert entity.is_on is False

    @pytest.mark.parametrize("enabled", [True, False])
    async def test_turn_on_off_delegates_to_coordinator(self, mock_dreamview_device, enabled):
        entity, coordinator = self._entity(mock_dreamview_device, _online_state(mock_dreamview_device))
        if enabled:
            await entity.async_turn_on()
        else:
            await entity.async_turn_off()
        coordinator.async_send_dreamview.assert_awaited_once_with(mock_dreamview_device.device_id, enabled=enabled)
        entity.async_write_ha_state.assert_called_once()

    @pytest.mark.parametrize("enabled", [True, False])
    async def test_rejected_command_raises(self, mock_dreamview_device, enabled):
        entity, _ = self._entity(mock_dreamview_device, _online_state(mock_dreamview_device), accepted=False)
        with pytest.raises(HomeAssistantError):
            if enabled:
                await entity.async_turn_on()
            else:
                await entity.async_turn_off()
        entity.async_write_ha_state.assert_not_called()


# --------------------------------------------------------------------------- #
# Heater auto-stop switch
# --------------------------------------------------------------------------- #


class TestAutoStopSwitch:
    def _entity(self, device: GoveeDevice, state: GoveeDeviceState | None, accepted: bool = True):
        coordinator = _coordinator(device, state)
        coordinator.async_control_device = AsyncMock(return_value=accepted)
        return _attach(GoveeAutoStopSwitchEntity(coordinator, device)), coordinator

    def test_unique_id(self):
        device = _heater_h713c()
        entity, _ = self._entity(device, None)
        assert entity.unique_id == f"{device.device_id}{SUFFIX_HEATER_AUTO_STOP}"

    def test_is_on_falls_back_to_optimistic_when_unreported(self):
        device = _heater_h7130()
        entity, _ = self._entity(device, _online_state(device))  # heater_auto_stop stays None
        entity._is_on = True
        assert entity.is_on is True

    @pytest.mark.parametrize(("previous", "expected"), [("on", True), ("off", False), (None, False)])
    async def test_restores_optimistic_state(self, previous, expected):
        device = _heater_h7130()
        entity, _ = self._entity(device, _online_state(device))
        last = State("switch.living_room_heater_auto_stop", previous) if previous else None
        await _restore(entity, last)
        assert entity.is_on is expected

    async def test_struct_shape_sends_the_current_target_temperature(self):
        # H713C: autoStop lives in the targetTemperature STRUCT, so the write
        # must carry the current target along or it would be clobbered (#29).
        device = _heater_h713c()
        state = _online_state(device)
        state.heater_temperature = 24
        entity, coordinator = self._entity(device, state)

        await entity.async_turn_on()

        device_id, cmd = coordinator.async_control_device.call_args[0]
        assert device_id == device.device_id
        assert isinstance(cmd, TemperatureSettingCommand)
        assert (cmd.temperature, cmd.auto_stop) == (24, 1)
        assert entity.is_on is True
        entity.async_write_ha_state.assert_called_once()

    async def test_struct_shape_defaults_to_20_degrees_without_state(self):
        entity, coordinator = self._entity(_heater_h713c(), None)
        entity._is_on = True

        await entity.async_turn_off()

        cmd = coordinator.async_control_device.call_args[0][1]
        assert isinstance(cmd, TemperatureSettingCommand)
        assert (cmd.temperature, cmd.auto_stop) == (20, 0)
        assert entity.is_on is False

    async def test_toggle_shape_sends_thermostat_toggle(self):
        device = _heater_h7130()
        entity, coordinator = self._entity(device, _online_state(device))
        await entity.async_turn_on()
        cmd = coordinator.async_control_device.call_args[0][1]
        assert isinstance(cmd, ToggleCommand)
        assert cmd.toggle_instance == INSTANCE_THERMOSTAT_TOGGLE
        assert cmd.enabled is True

    async def test_rejected_turn_off_raises_and_keeps_state(self):
        device = _heater_h7130()
        entity, _ = self._entity(device, _online_state(device), accepted=False)
        entity._is_on = True
        with pytest.raises(HomeAssistantError):
            await entity.async_turn_off()
        assert entity.is_on is True
        entity.async_write_ha_state.assert_not_called()
