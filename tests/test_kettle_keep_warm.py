"""Experimental H7175 keep-warm control: frames, reconciliation, entities, option."""

from __future__ import annotations

import asyncio
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed

from custom_components.govee import _async_cleanup_orphaned_entities
from custom_components.govee.api.ble_packet import build_packet
from custom_components.govee.const import (
    CONF_API_KEY,
    CONF_KETTLE_FRAME_CONTROL,
    CONF_POLL_INTERVAL,
    DOMAIN,
    KETTLE_FRAME_CONFIRM_TIMEOUT,
    KETTLE_FRAME_REQUERY_INTERVAL,
)
from custom_components.govee.coordinator import GoveeCoordinator
from custom_components.govee.kettle.control import WriteResult, keep_warm_command
from custom_components.govee.kettle.entities import (
    GoveeKettleKeepWarmDurationSelect,
    GoveeKettleKeepWarmSwitch,
    keep_warm_switches,
    kettle_selects,
)
from custom_components.govee.models import GoveeDevice, GoveeDeviceState

from .kettle_samples import (
    DEVICE_ID,
    H7175_DEVICE,
    H7175_STATE,
    KEEP_WARM_ECHO_ON_1H,
    KEEP_WARM_ECHO_ON_2H,
    KEEP_WARM_ECHO_ON_90M,
    KEEP_WARM_STATUS_OFF_2H,
    KEEP_WARM_STATUS_ON_2H_116_LEFT,
)


def _device(sku: str = "H7175") -> GoveeDevice:
    return GoveeDevice.from_api_response({**H7175_DEVICE, "sku": sku})


def _polled(**fields) -> GoveeDeviceState:
    state = GoveeDeviceState.create_empty(DEVICE_ID)
    state.update_from_api(H7175_STATE)
    state.kettle_keep_warm_enabled, state.kettle_keep_warm_minutes = False, 120
    for name, value in fields.items():
        setattr(state, name, value)
    return state


def _coordinator(hass: HomeAssistant, *, sent: bool = True) -> GoveeCoordinator:
    entry = MagicMock(entry_id="entry", options={})
    coordinator = GoveeCoordinator(
        hass=hass, config_entry=entry, api_client=MagicMock(), iot_credentials=None, poll_interval=60
    )
    coordinator._devices[DEVICE_ID] = _device()
    coordinator._states[DEVICE_ID] = _polled()
    coordinator._mqtt_client = MagicMock(connected=True, async_publish_status_query=AsyncMock(return_value=True))
    coordinator._device_topics[DEVICE_ID] = "GD/kettle"
    coordinator._ble_manager = MagicMock(available=True, async_send_ble_packet=AsyncMock(return_value=sent))
    coordinator.async_set_updated_data = MagicMock()
    coordinator.async_update_listeners = MagicMock()
    return coordinator


@pytest.fixture
async def coordinator(hass: HomeAssistant):
    coordinator = _coordinator(hass)
    yield coordinator
    coordinator.kettles.async_shutdown()


def _push(coordinator: GoveeCoordinator, *frames: str) -> None:
    coordinator._on_mqtt_state_update(DEVICE_ID, {"result": 1, "_op_frames": list(frames)})


def _keep_warm(coordinator: GoveeCoordinator) -> tuple[bool | None, int | None]:
    state = coordinator._states[DEVICE_ID]
    return state.kettle_keep_warm_enabled, state.kettle_keep_warm_minutes


def _queries(coordinator: GoveeCoordinator) -> int:
    return coordinator._mqtt_client.async_publish_status_query.await_count


async def _advance(hass: HomeAssistant, freezer, seconds: float) -> None:
    freezer.tick(timedelta(seconds=seconds))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()


def test_the_command_is_the_apps_frame():
    """Built with the shared BLE packet helper, it is byte for byte the captured echo."""
    assert build_packet(keep_warm_command(True, 60)) == bytes.fromhex(KEEP_WARM_ECHO_ON_1H)
    assert build_packet(keep_warm_command(True, 120)) == bytes.fromhex(KEEP_WARM_ECHO_ON_2H)


class TestWriteAndReconcile:
    async def test_shown_at_once_and_confirmed_by_the_echo(self, hass, freezer, coordinator):
        control = coordinator.kettles.keep_warm
        assert await control.async_set(DEVICE_ID, True, 60) is WriteResult.SENT
        assert coordinator._ble_manager.async_send_ble_packet.await_count == 1
        assert _keep_warm(coordinator) == (True, 60)
        assert _queries(coordinator) == 1
        _push(coordinator, KEEP_WARM_ECHO_ON_1H)
        assert control.pending(DEVICE_ID) is None
        await _advance(hass, freezer, KETTLE_FRAME_REQUERY_INTERVAL)
        assert _queries(coordinator) == 1

    async def test_a_counting_down_status_confirms(self, coordinator):
        """Byte 5 of the status is the time left; the setting still matches."""
        control = coordinator.kettles.keep_warm
        await control.async_set(DEVICE_ID, True, 120)
        _push(coordinator, KEEP_WARM_STATUS_ON_2H_116_LEFT)
        assert control.pending(DEVICE_ID) is None
        assert coordinator._states[DEVICE_ID].kettle_keep_warm_remaining == 116

    async def test_requeried_until_the_deadline_then_unknown(self, hass, freezer, coordinator, caplog):
        await coordinator.kettles.keep_warm.async_set(DEVICE_ID, True, 60)
        for _ in range(int(KETTLE_FRAME_CONFIRM_TIMEOUT // KETTLE_FRAME_REQUERY_INTERVAL)):
            await _advance(hass, freezer, KETTLE_FRAME_REQUERY_INTERVAL)
        assert _queries(coordinator) == 3
        assert _keep_warm(coordinator) == (None, 60)  # unknown, not "off"
        assert "did not confirm keep warm on for 60 min" in caplog.text

    async def test_stale_status_in_grace_then_a_later_one_wins(self, hass, freezer, coordinator, caplog):
        control = coordinator.kettles.keep_warm
        await control.async_set(DEVICE_ID, True, 60)
        _push(coordinator, KEEP_WARM_STATUS_OFF_2H)  # a reply already in flight
        assert _keep_warm(coordinator) == (True, 60)
        freezer.tick(timedelta(seconds=16))
        _push(coordinator, KEEP_WARM_STATUS_OFF_2H)
        assert _keep_warm(coordinator) == (False, 120)
        assert control.pending(DEVICE_ID) is None
        assert "reports keep warm off for 120 min, not the on for 60 min requested" in caplog.text

    async def test_another_clients_echo_wins_and_our_superseded_echo_does_not(self, coordinator):
        control = coordinator.kettles.keep_warm
        await control.async_set(DEVICE_ID, True, 60)
        await control.async_set(DEVICE_ID, True, 120)
        _push(coordinator, KEEP_WARM_ECHO_ON_1H)  # our first write's echo, late
        assert (_keep_warm(coordinator), control.pending(DEVICE_ID)) == ((True, 120), (True, 120))
        _push(coordinator, KEEP_WARM_ECHO_ON_90M)  # the app, meanwhile
        assert (_keep_warm(coordinator), control.pending(DEVICE_ID)) == ((True, 90), None)

    async def test_decided_under_the_lock_from_the_state_then(self, hass, coordinator):
        """A duration change queued behind a switch write keeps the new on/off (m4)."""
        gate = asyncio.Event()
        sent: list[str] = []

        async def _send(device_id, sku, frame):
            sent.append(frame)
            if len(sent) == 1:
                await gate.wait()
            return True

        coordinator._ble_manager.async_send_ble_packet = _send
        control = coordinator.kettles.keep_warm
        first = hass.async_create_task(control.async_set(DEVICE_ID, True, None))
        second = hass.async_create_task(control.async_set(DEVICE_ID, None, 60))
        await asyncio.sleep(0)
        assert len(sent) == 1
        gate.set()
        await first
        await second
        assert control.pending(DEVICE_ID) == (True, 60)

    async def test_unknown_on_off_refuses_keep_as_is(self, coordinator):
        coordinator._states[DEVICE_ID].kettle_keep_warm_enabled = None
        assert await coordinator.kettles.keep_warm.async_set(DEVICE_ID, None, 60) is WriteResult.UNKNOWN_STATE
        coordinator._ble_manager.async_send_ble_packet.assert_not_awaited()

    async def test_default_duration_when_none_reported(self, coordinator):
        coordinator._states[DEVICE_ID].kettle_keep_warm_minutes = None
        await coordinator.kettles.keep_warm.async_set(DEVICE_ID, True, None)
        assert _keep_warm(coordinator) == (True, 120)

    async def test_an_unacknowledged_publish_is_unknown_and_asked_at_once(self, hass):
        """M1: a timed-out publish may still have reached the kettle."""
        coordinator = _coordinator(hass, sent=False)
        control = coordinator.kettles.keep_warm
        assert await control.async_set(DEVICE_ID, True, 60) is WriteResult.FAILED
        assert _keep_warm(coordinator) == (None, 60)
        assert _queries(coordinator) == 1
        # A poll replacing the state keeps it unknown; the kettle's echo then confirms.
        polled = _polled(kettle_keep_warm_enabled=None, kettle_keep_warm_minutes=None)
        coordinator.kettles.merge_poll(DEVICE_ID, coordinator._states[DEVICE_ID], polled)
        assert (polled.kettle_keep_warm_enabled, polled.kettle_keep_warm_minutes) == (None, 60)
        _push(coordinator, KEEP_WARM_STATUS_OFF_2H)  # in grace: still unknown
        assert _keep_warm(coordinator) == (None, 60)
        _push(coordinator, KEEP_WARM_ECHO_ON_1H)
        assert (_keep_warm(coordinator), control.pending(DEVICE_ID)) == ((True, 60), None)
        control.async_shutdown()

    async def test_a_poll_replacing_the_state_keeps_the_pending_write(self, coordinator):
        """m2: the poll's new state object shows the write, not the stale state."""
        await coordinator.kettles.keep_warm.async_set(DEVICE_ID, True, 90)
        coordinator._api_client.get_device_state = AsyncMock(return_value=_polled())
        result = await coordinator._fetch_device_state(DEVICE_ID, coordinator._devices[DEVICE_ID])
        assert (result.kettle_keep_warm_enabled, result.kettle_keep_warm_minutes) == (True, 90)

    async def test_unload_mid_confirmation_or_mid_query_stops_everything(self, hass, freezer, coordinator):
        control = coordinator.kettles.keep_warm

        async def _query(topic):
            control.async_shutdown()  # unloads while the status query is awaited
            return True

        coordinator._mqtt_client.async_publish_status_query = AsyncMock(side_effect=_query)
        await control.async_set(DEVICE_ID, True, 60)
        await _advance(hass, freezer, KETTLE_FRAME_CONFIRM_TIMEOUT + 1)
        assert _queries(coordinator) == 1
        assert control.pending(DEVICE_ID) is None
        # m5: a write that acquires the lock after the unload sends nothing.
        assert await control.async_set(DEVICE_ID, True, 60) is WriteResult.UNAVAILABLE

    async def test_unload_during_the_send_arms_nothing(self, coordinator):
        control = coordinator.kettles.keep_warm

        async def _send(device_id, sku, frame):
            control.async_shutdown()
            return True

        coordinator._ble_manager.async_send_ble_packet = _send
        assert await control.async_set(DEVICE_ID, True, 60) is WriteResult.SENT
        assert (_queries(coordinator), control.pending(DEVICE_ID)) == (0, None)

    @pytest.mark.parametrize("case", ["link_down", "no_state", "other_kettle", "unknown"])
    async def test_refused(self, coordinator, case):
        device_id = DEVICE_ID
        if case == "link_down":
            coordinator._mqtt_client.connected = False
        elif case == "no_state":
            coordinator._states = {}
        elif case == "other_kettle":
            coordinator._devices[DEVICE_ID] = _device("H717A")
        else:
            device_id = "unknown"
        assert await coordinator.kettles.keep_warm.async_set(device_id, True, 60) is WriteResult.UNAVAILABLE
        coordinator._ble_manager.async_send_ble_packet.assert_not_awaited()


# --------------------------------------------------------------------------- #
# Entities
# --------------------------------------------------------------------------- #


def _entities(result: WriteResult = WriteResult.SENT, state: GoveeDeviceState | None = None):
    coordinator = MagicMock()
    coordinator.get_state = MagicMock(return_value=state or _polled())
    coordinator.kettles.keep_warm.async_set = AsyncMock(return_value=result)
    coordinator.mqtt_connected = True
    coordinator.last_update_success = True
    device = _device()
    return (
        GoveeKettleKeepWarmSwitch(coordinator, device),
        GoveeKettleKeepWarmDurationSelect(coordinator, device),
        coordinator,
    )


class TestEntities:
    async def test_switch_and_select_write(self):
        switch, select, coordinator = _entities()
        assert (switch.is_on, select.current_option) == (False, "120_min")
        assert select.entity_category is EntityCategory.CONFIG
        assert switch.unique_id == f"{DEVICE_ID}_keep_warm"
        assert select.unique_id == f"{DEVICE_ID}_keep_warm_duration"
        await switch.async_turn_on()
        await switch.async_turn_off()
        await select.async_select_option("60_min")
        assert [c.args for c in coordinator.kettles.keep_warm.async_set.await_args_list] == [
            (DEVICE_ID, True, None),
            (DEVICE_ID, False, None),
            (DEVICE_ID, None, 60),
        ]

    async def test_errors(self):
        switch, select, _ = _entities(WriteResult.UNKNOWN_STATE)
        with pytest.raises(ServiceValidationError):
            await select.async_select_option("60_min")
        with pytest.raises(ServiceValidationError):
            await select.async_select_option("45_min")
        switch, _, _ = _entities(WriteResult.FAILED)
        with pytest.raises(HomeAssistantError):
            await switch.async_turn_on()

    def test_state_and_availability(self):
        switch, select, coordinator = _entities(state=_polled(kettle_keep_warm_minutes=45))
        assert select.current_option is None  # not one of the app's durations
        assert switch.available is True
        coordinator.mqtt_connected = False
        assert switch.available is False
        coordinator.get_state.return_value = None
        assert (switch.is_on, select.current_option) == (None, None)

    def test_created_only_with_the_option_on(self):
        devices = [_device(), GoveeDevice.from_api_response({**H7175_DEVICE, "sku": "H717A", "device": "x"})]
        on = MagicMock(
            devices={d.device_id: d for d in devices},
            config_entry=SimpleNamespace(options={CONF_KETTLE_FRAME_CONTROL: True}),
        )
        off = MagicMock(devices=on.devices, config_entry=SimpleNamespace(options={}))
        assert [type(e).__name__ for e in keep_warm_switches(on)] == ["GoveeKettleKeepWarmSwitch"]
        assert [type(e).__name__ for e in kettle_selects(on)] == [
            "GoveeKettleBrewModeSelect",
            "GoveeKettleKeepWarmDurationSelect",
        ]
        assert keep_warm_switches(off) == []


# --------------------------------------------------------------------------- #
# Option and cleanup
# --------------------------------------------------------------------------- #


@pytest.fixture
def _custom_integrations(hass: HomeAssistant, enable_custom_integrations: None) -> None:
    """Load the integration without starting Bluetooth (as in test_setup_entry)."""
    hass.config.components.add("bluetooth_adapters")
    hass.config.components.add("network")


@pytest.mark.usefixtures("_custom_integrations")
class TestOption:
    def _entry(self, hass, devices, options):
        entry = MockConfigEntry(domain=DOMAIN, data={CONF_API_KEY: "k"}, options=options, version=2)
        entry.add_to_hass(hass)
        if devices:
            entry.runtime_data = SimpleNamespace(devices={d.device_id: d for d in devices})
        return entry

    async def _general(self, hass, entry):
        result = await hass.config_entries.options.async_init(entry.entry_id)
        if result["type"] is FlowResultType.MENU:
            result = await hass.config_entries.options.async_configure(result["flow_id"], {"next_step_id": "general"})
        return result

    async def test_shown_only_while_an_h7175_is_loaded(self, hass: HomeAssistant):
        result = await self._general(hass, self._entry(hass, [_device()], {}))
        assert CONF_KETTLE_FRAME_CONTROL in {str(key) for key in result["data_schema"].schema}
        result = await self._general(hass, self._entry(hass, [_device("H717A")], {}))
        assert CONF_KETTLE_FRAME_CONTROL not in {str(key) for key in result["data_schema"].schema}

    async def test_kept_when_not_shown(self, hass: HomeAssistant):
        entry = self._entry(hass, [], {CONF_KETTLE_FRAME_CONTROL: True})
        result = await self._general(hass, entry)
        await hass.config_entries.options.async_configure(result["flow_id"], {CONF_POLL_INTERVAL: 90})
        assert entry.options[CONF_KETTLE_FRAME_CONTROL] is True

    @pytest.mark.parametrize(("enabled", "kept"), [(False, False), (True, True)])
    async def test_entities_removed_when_off(self, hass: HomeAssistant, enabled, kept):
        entry = self._entry(hass, [], {CONF_KETTLE_FRAME_CONTROL: enabled})
        registry = er.async_get(hass)
        ids = [
            registry.async_get_or_create(domain, DOMAIN, f"{DEVICE_ID}{suffix}", config_entry=entry).entity_id
            for domain, suffix in (("switch", "_keep_warm"), ("select", "_keep_warm_duration"))
        ]
        coordinator = SimpleNamespace(
            devices={DEVICE_ID: _device()}, leak_sensors={}, hub_device_ids=set(), discovery_incomplete=False
        )
        await _async_cleanup_orphaned_entities(hass, entry, coordinator)
        assert [registry.async_get(entity_id) is not None for entity_id in ids] == [kept, kept]
