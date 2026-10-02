"""H7175: Brew mode select, follow-up reads, heating polls and push-wins protection."""

from __future__ import annotations

import asyncio
from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.restore_state import RestoredExtraData
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.govee.const import KETTLE_PROTECT_SECONDS
from custom_components.govee.coordinator import GoveeCoordinator
from custom_components.govee.kettle.entities import GoveeKettleBrewModeSelect, kettle_selects
from custom_components.govee.models import (
    GoveeDevice,
    GoveeDeviceState,
    PowerCommand,
    TemperatureSettingCommand,
    WorkModeCommand,
)
from custom_components.govee.models.device import INSTANCE_SLIDER_TEMPERATURE

from .kettle_samples import DEVICE_ID, H7175_DEVICE, H7175_MQTT, H7175_STATE, H7175_STATE_MANUAL, mqtt_frames

OPTIONS = [
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


def _slider(temperature: int) -> TemperatureSettingCommand:
    return TemperatureSettingCommand(
        temperature=temperature, unit="Fahrenheit", auto_stop=None, setting_instance=INSTANCE_SLIDER_TEMPERATURE
    )


def _device(sku: str = "H7175") -> GoveeDevice:
    return GoveeDevice.from_api_response({**H7175_DEVICE, "sku": sku})


def _polled(api=H7175_STATE, **fields) -> GoveeDeviceState:
    state = GoveeDeviceState.create_empty(DEVICE_ID)
    state.update_from_api(api)
    for name, value in fields.items():
        setattr(state, name, value)
    return state


def _coordinator(hass, sku: str = "H7175") -> GoveeCoordinator:
    entry = MagicMock(entry_id="entry", options={})
    coordinator = GoveeCoordinator(
        hass=hass, config_entry=entry, api_client=MagicMock(), iot_credentials=None, poll_interval=60
    )
    coordinator._devices[DEVICE_ID] = _device(sku)
    coordinator._states[DEVICE_ID] = _polled()
    coordinator._api_client.rate_limit_remaining = 100
    coordinator._api_client.requests_today = 0
    coordinator._api_client.control_device = AsyncMock(return_value=True)
    coordinator.async_set_updated_data = MagicMock()
    coordinator.async_update_listeners = MagicMock()
    return coordinator


async def _advance(hass: HomeAssistant, freezer, seconds: float) -> None:
    freezer.tick(timedelta(seconds=seconds))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()


# --------------------------------------------------------------------------- #
# Brew mode select
# --------------------------------------------------------------------------- #


def _select(state):
    coordinator = MagicMock()
    coordinator.get_state = MagicMock(return_value=state)
    coordinator.async_control_device = AsyncMock(return_value=True)
    coordinator.kettles.reports_fahrenheit = MagicMock(return_value=True)
    return GoveeKettleBrewModeSelect(coordinator, _device()), coordinator


class TestBrewModeSelect:
    def test_options_are_the_stable_keys(self):
        entity, _ = _select(_polled())
        assert entity.options == OPTIONS
        assert entity.unique_id == f"{DEVICE_ID}_kettle_brew_mode"

    def test_shows_the_mode_while_off_and_manual(self):
        assert _select(_polled(work_mode=2))[0].current_option == "green_tea"
        assert _select(_polled(api=H7175_STATE_MANUAL))[0].current_option == "manual"
        assert _select(None)[0].current_option is None

    async def test_selects_without_power(self):
        entity, coordinator = _select(_polled(power_state=False))
        await entity.async_select_option("custom_3")
        await entity.async_select_option("manual")
        assert [c.args[1] for c in coordinator.async_control_device.await_args_list] == [
            WorkModeCommand(work_mode=1, mode_value=3),
            _slider(176),
        ]

    @pytest.mark.parametrize(
        ("option", "key"), [("espresso", "unknown_option"), ("manual", "kettle_manual_no_target")]
    )
    async def test_refused(self, option, key):
        entity, coordinator = _select(_polled(kettle_target_temperature=None))
        with pytest.raises(ServiceValidationError) as err:
            await entity.async_select_option(option)
        assert err.value.translation_key == key
        coordinator.async_control_device.assert_not_awaited()

    async def test_restores_the_slot_once_the_state_is_in(self):
        entity, coordinator = _select(None)
        entity.async_get_last_extra_data = AsyncMock(return_value=RestoredExtraData({"work_mode": 1, "mode_value": 2}))
        await entity.async_added_to_hass()
        state = _polled()
        coordinator.get_state.return_value = state
        entity.async_write_ha_state = MagicMock()
        entity._handle_coordinator_update()
        assert entity.current_option == "custom_2"

    def test_only_h7175_kettles_with_modes(self):
        no_modes = GoveeDevice.from_api_response(
            {
                **H7175_DEVICE,
                "device": "x",
                "capabilities": [c for c in H7175_DEVICE["capabilities"] if c["instance"] != "workMode"],
            }
        )
        devices = [_device(), no_modes, GoveeDevice.from_api_response({**H7175_DEVICE, "sku": "H717A", "device": "y"})]
        coordinator = MagicMock(devices={d.device_id: d for d in devices})
        assert [e._device.device_id for e in kettle_selects(coordinator)] == [DEVICE_ID]


# --------------------------------------------------------------------------- #
# Push-wins protection
# --------------------------------------------------------------------------- #


class TestProtection:
    async def test_push_during_a_read_beats_the_command_and_the_cloud(self, hass: HomeAssistant):
        """Distinct values: command 150 / power on, push 160 / off, cloud 176 / on."""
        coordinator = _coordinator(hass)
        await coordinator.async_control_device(DEVICE_ID, PowerCommand(power_on=True))
        await coordinator.async_control_device(DEVICE_ID, _slider(150))
        read_started, release = asyncio.Event(), asyncio.Event()

        async def _get_device_state(device_id, sku):
            read_started.set()
            await release.wait()
            return _polled(power_state=True)

        coordinator._api_client.get_device_state = _get_device_state
        read = hass.async_create_task(coordinator.async_read_device(DEVICE_ID))
        await read_started.wait()
        coordinator._on_mqtt_state_update(DEVICE_ID, {"onOff": 0, "sta": {"setTem": 16000}})
        release.set()
        await read
        state = coordinator._states[DEVICE_ID]
        assert (state.kettle_target_temperature, state.power_state) == (160.0, False)
        coordinator.kettles.async_shutdown()

    async def test_a_confirming_push_keeps_protecting_until_the_cloud_agrees(self, hass: HomeAssistant, freezer):
        coordinator = _coordinator(hass)
        coordinator._api_client.get_device_state = AsyncMock(return_value=_polled())  # stale: 176
        await coordinator.async_control_device(DEVICE_ID, _slider(150))
        coordinator._on_mqtt_state_update(DEVICE_ID, {"sta": {"setTem": 15000}})  # confirms 150
        await _advance(hass, freezer, 5)
        assert coordinator._states[DEVICE_ID].kettle_target_temperature == 150.0
        await _advance(hass, freezer, 15)
        assert coordinator._states[DEVICE_ID].kettle_target_temperature == 150.0
        # The cloud catches up: protection ends, and a later cloud value is taken.
        coordinator._api_client.get_device_state.return_value = _polled(kettle_target_temperature=150.0)
        await coordinator.async_read_device(DEVICE_ID)
        coordinator._api_client.get_device_state.return_value = _polled(kettle_target_temperature=180.0)
        await coordinator.async_read_device(DEVICE_ID)
        assert coordinator._states[DEVICE_ID].kettle_target_temperature == 180.0

    async def test_protection_expires(self, hass: HomeAssistant, freezer):
        coordinator = _coordinator(hass)
        coordinator._api_client.get_device_state = AsyncMock(return_value=_polled())
        coordinator._on_mqtt_state_update(DEVICE_ID, {"sta": {"setTem": 15000}})
        freezer.tick(timedelta(seconds=KETTLE_PROTECT_SECONDS + 1))
        await coordinator.async_read_device(DEVICE_ID)
        assert coordinator._states[DEVICE_ID].kettle_target_temperature == 176.0

    async def test_the_regular_poll_is_protected_too(self, hass: HomeAssistant):
        coordinator = _coordinator(hass)
        coordinator._api_client.get_device_state = AsyncMock(return_value=_polled())
        coordinator._on_mqtt_state_update(DEVICE_ID, {**H7175_MQTT, "onOff": 1, "sta": {"setTem": 15000}})
        result = await coordinator._fetch_device_state(DEVICE_ID, coordinator._devices[DEVICE_ID])
        assert (result.kettle_target_temperature, result.power_state) == (150.0, True)

    async def test_only_the_fields_a_command_set(self, hass: HomeAssistant):
        coordinator = _coordinator(hass)
        await coordinator.async_control_device(DEVICE_ID, PowerCommand(power_on=True))
        coordinator._api_client.get_device_state = AsyncMock(return_value=_polled(kettle_target_temperature=180.0))
        result = await coordinator._fetch_device_state(DEVICE_ID, coordinator._devices[DEVICE_ID])
        assert (result.power_state, result.kettle_target_temperature) == (True, 180.0)
        coordinator.kettles.async_shutdown()

    async def test_a_mode_shows_its_preset_target(self, hass: HomeAssistant):
        coordinator = _coordinator(hass)
        coordinator._on_mqtt_state_update(DEVICE_ID, dict(H7175_MQTT))
        await coordinator.async_control_device(DEVICE_ID, WorkModeCommand(work_mode=4, mode_value=0))
        state = coordinator._states[DEVICE_ID]
        assert (state.work_mode, state.kettle_target_temperature) == (4, 205.0)
        await coordinator.async_control_device(DEVICE_ID, WorkModeCommand(work_mode=9, mode_value=0))
        assert state.kettle_target_temperature == 205.0  # no preset known: unchanged
        coordinator.kettles.async_shutdown()

    async def test_other_kettles_are_unchanged(self, hass: HomeAssistant):
        coordinator = _coordinator(hass, "H717A")
        await coordinator.async_control_device(DEVICE_ID, WorkModeCommand(work_mode=2, mode_value=0))
        state = coordinator._states[DEVICE_ID]
        assert (state.work_mode, state.kettle_mode_value) == (2, None)
        assert coordinator.kettles._followups == {}
        coordinator._api_client.get_device_state = AsyncMock(return_value=_polled())
        result = await coordinator._fetch_device_state(DEVICE_ID, coordinator._devices[DEVICE_ID])
        assert result.work_mode == 1  # the poll wins, as on main


# --------------------------------------------------------------------------- #
# Follow-up reads
# --------------------------------------------------------------------------- #


class TestFollowups:
    async def test_reads_then_queries_at_5_and_20_seconds(self, hass: HomeAssistant, freezer):
        coordinator = _coordinator(hass)
        order: list[str] = []

        async def _get_device_state(device_id, sku):
            order.append("read")
            return _polled()

        async def _query(topic):
            order.append("query")

        coordinator._api_client.get_device_state = _get_device_state
        coordinator._mqtt_client = MagicMock(connected=True, async_publish_status_query=_query)
        coordinator._device_topics[DEVICE_ID] = "GD/kettle"
        await coordinator.async_control_device(DEVICE_ID, PowerCommand(power_on=True))
        coordinator.async_set_updated_data.reset_mock()
        await _advance(hass, freezer, 4)
        assert order == []
        await _advance(hass, freezer, 1)
        assert order == ["read", "query"]
        coordinator.async_update_listeners.assert_called()
        coordinator.async_set_updated_data.assert_not_called()  # the poll keeps its schedule
        await _advance(hass, freezer, 15)
        assert order == ["read", "query"] * 2
        await _advance(hass, freezer, 60)
        assert len(order) == 4 and coordinator.kettles._followups == {}

    async def test_a_newer_command_restarts_the_reads(self, hass: HomeAssistant, freezer):
        coordinator = _coordinator(hass)
        coordinator._api_client.get_device_state = AsyncMock(return_value=_polled())
        await coordinator.async_control_device(DEVICE_ID, PowerCommand(power_on=True))
        await _advance(hass, freezer, 4)
        await coordinator.async_control_device(DEVICE_ID, PowerCommand(power_on=False))
        await _advance(hass, freezer, 4)
        assert coordinator._api_client.get_device_state.await_count == 0
        await _advance(hass, freezer, 1)
        assert coordinator._api_client.get_device_state.await_count == 1
        coordinator.kettles.async_shutdown()

    async def test_a_command_during_a_read_ends_the_old_chain(self, hass: HomeAssistant, freezer):
        coordinator = _coordinator(hass)
        coordinator._api_client.get_device_state = AsyncMock(return_value=_polled())
        await coordinator.async_control_device(DEVICE_ID, PowerCommand(power_on=True))

        async def _query(topic):
            await coordinator.async_control_device(DEVICE_ID, PowerCommand(power_on=False))

        coordinator._mqtt_client = MagicMock(connected=True, async_publish_status_query=_query)
        coordinator._device_topics[DEVICE_ID] = "GD/kettle"
        await _advance(hass, freezer, 5)
        await _advance(hass, freezer, 5)  # the new chain's first read, not the old chain's second
        assert coordinator._api_client.get_device_state.await_count == 2
        coordinator.kettles.async_shutdown()

    @pytest.mark.parametrize("reason", ["poll", "rate_limited", "remaining", "spent", "cadence"])
    async def test_reads_yield_but_the_status_query_goes_out(self, hass: HomeAssistant, freezer, reason):
        coordinator = _coordinator(hass)
        coordinator._api_client.get_device_state = AsyncMock(return_value=_polled())
        coordinator._mqtt_client = MagicMock(connected=True, async_publish_status_query=AsyncMock())
        coordinator._device_topics[DEVICE_ID] = "GD/kettle"
        if reason == "poll":
            coordinator._poll_in_progress = True
        elif reason == "rate_limited":
            coordinator._rate_limited = True
        elif reason == "remaining":
            coordinator._api_client.rate_limit_remaining = 3
        elif reason == "spent":
            coordinator._api_client.requests_today = coordinator._daily_request_budget
        else:
            coordinator._original_update_interval = timedelta(seconds=1)
        await coordinator.async_control_device(DEVICE_ID, PowerCommand(power_on=True))
        await _advance(hass, freezer, 5)
        coordinator._api_client.get_device_state.assert_not_awaited()
        coordinator._mqtt_client.async_publish_status_query.assert_awaited_once_with("GD/kettle")
        coordinator.kettles.async_shutdown()

    async def test_unload_cancels_them(self, hass: HomeAssistant, freezer):
        coordinator = _coordinator(hass)
        coordinator._api_client.get_device_state = AsyncMock(return_value=_polled())
        await coordinator.async_control_device(DEVICE_ID, PowerCommand(power_on=True))
        coordinator._api_client.close = AsyncMock()
        await coordinator.async_shutdown()
        await _advance(hass, freezer, 30)
        coordinator._api_client.get_device_state.assert_not_awaited()

    async def test_rejected_commands_and_other_devices_read_nothing(self, hass: HomeAssistant):
        coordinator = _coordinator(hass)
        coordinator._api_client.control_device = AsyncMock(return_value=False)
        await coordinator.async_control_device(DEVICE_ID, PowerCommand(power_on=True))
        assert coordinator.kettles._followups == {}

    async def test_read_of_a_missing_or_failing_device(self, hass: HomeAssistant):
        coordinator = _coordinator(hass)
        held = coordinator._states[DEVICE_ID]
        await coordinator.async_read_device("gone")
        coordinator._api_client.get_device_state = AsyncMock(side_effect=RuntimeError("boom"))
        await coordinator.async_read_device(DEVICE_ID)
        assert coordinator._states[DEVICE_ID] is held
        coordinator.async_update_listeners.assert_not_called()

    @pytest.mark.parametrize(
        ("connected", "topic", "quarantined"),
        [(False, "GD/kettle", False), (True, None, False), (True, "GD/kettle", True)],
    )
    async def test_status_query_only_when_it_can_go_out(self, hass: HomeAssistant, connected, topic, quarantined):
        coordinator = _coordinator(hass)
        coordinator._mqtt_client = MagicMock(connected=connected, async_publish_status_query=AsyncMock())
        if topic:
            coordinator._device_topics[DEVICE_ID] = topic
        if quarantined:
            coordinator._status_query_quarantine.add(DEVICE_ID)
        await coordinator.async_request_status(DEVICE_ID)
        coordinator._mqtt_client.async_publish_status_query.assert_not_awaited()

    async def test_poll_in_progress_flag(self, hass: HomeAssistant):
        coordinator = _coordinator(hass)
        seen: list[bool] = []

        async def _poll_all():
            seen.append(coordinator.poll_in_progress)
            return {}

        coordinator._async_poll_all = _poll_all
        await coordinator._async_update_data()
        assert (seen, coordinator.poll_in_progress) == ([True], False)


# --------------------------------------------------------------------------- #
# Heating kettles are polled every cycle (within the budget)
# --------------------------------------------------------------------------- #


class TestHeatingPoll:
    @pytest.mark.parametrize(
        ("fields", "heating"),
        [
            ({"power_state": True, "sensor_temperature": 126.0}, True),
            ({"power_state": True, "sensor_temperature": None}, True),
            ({"power_state": True, "sensor_temperature": 175.0}, False),  # within the tolerance
            ({"power_state": False, "sensor_temperature": 84.0}, False),
            ({"power_state": True, "sensor_temperature": 150.0, "kettle_heating_status": "heating"}, True),
            ({"power_state": True, "sensor_temperature": 175.0, "kettle_heating_status": "keeping_warm"}, False),
            # Put back on the base, it reheats while still "keeping warm" (111 to 176 °F seen).
            ({"power_state": True, "sensor_temperature": 111.0, "kettle_heating_status": "keeping_warm"}, True),
            ({"power_state": True, "sensor_temperature": 150.0, "kettle_heating_status": "reached_target"}, False),
        ],
    )
    async def test_heating(self, hass: HomeAssistant, fields, heating):
        coordinator = _coordinator(hass)
        coordinator._states[DEVICE_ID] = _polled(**fields)
        assert coordinator.kettles.heating(DEVICE_ID) is heating
        assert coordinator.kettles.must_poll(DEVICE_ID) is heating

    async def test_no_state_is_not_heating(self, hass: HomeAssistant):
        coordinator = _coordinator(hass)
        coordinator._states = {}
        assert coordinator.kettles.heating(DEVICE_ID) is False

    async def test_skips_on_a_tight_budget(self, hass: HomeAssistant):
        coordinator = _coordinator(hass)
        coordinator._states[DEVICE_ID] = _polled(power_state=True, sensor_temperature=100.0)
        coordinator._original_update_interval = timedelta(seconds=1)
        assert coordinator.kettles.must_poll(DEVICE_ID) is False

    async def test_freshness_skip(self, hass: HomeAssistant):
        coordinator = _coordinator(hass)
        coordinator._states[DEVICE_ID] = _polled(power_state=True, sensor_temperature=100.0)
        coordinator._transport.record_read(DEVICE_ID, "mqtt")
        coordinator._local_fresh_skips[DEVICE_ID] = 0
        assert coordinator._locally_fresh_devices(dict(coordinator._devices)) == set()
        coordinator._states[DEVICE_ID].kettle_heating_status = "reached_target"
        assert coordinator._locally_fresh_devices(dict(coordinator._devices)) == {DEVICE_ID}


def test_samples_still_decode():
    assert len(mqtt_frames()) == 15
