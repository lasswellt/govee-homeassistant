"""Second batch of review follow-ups (September 2026).

- #184: per-outlet switches for the H5160/H5161 over the AWS IoT bitmask.
- MQTT status entities react to disconnects immediately (on_disconnected).
- A push that changes nothing does not wake every entity.
- Leak/button frames coalesce into one pending BFF poll.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.govee.coordinator import GoveeCoordinator
from custom_components.govee.models import (
    GoveeCapability,
    GoveeDevice,
    GoveeDeviceState,
)
from custom_components.govee.models.device import (
    CAPABILITY_ON_OFF,
    CAPABILITY_TOGGLE,
    INSTANCE_POWER,
)
from custom_components.govee.switch import GoveeMqttOutletSwitchEntity
from custom_components.govee.transport_health import TransportHealthTracker

PLUG = "AA:BB:CC:DD:EE:FF:51:60"


def _plug(sku="H5160", with_socket_toggles=False) -> GoveeDevice:
    caps = [GoveeCapability(type=CAPABILITY_ON_OFF, instance=INSTANCE_POWER, parameters={})]
    if with_socket_toggles:
        caps += [
            GoveeCapability(type=CAPABILITY_TOGGLE, instance=f"socketToggle{i}", parameters={})
            for i in (1, 2, 3)
        ]
    return GoveeDevice(
        device_id=PLUG, sku=sku, name="Strip", device_type="devices.types.socket", capabilities=tuple(caps)
    )


class TestOutletDetection:
    def test_h5160_gets_three_mqtt_outlets(self):
        assert _plug().mqtt_outlet_count == 3

    def test_lower_case_sku(self):
        assert _plug(sku="h5161").mqtt_outlet_count == 3

    def test_developer_api_toggles_win(self):
        """If Govee ever advertises socketToggle{N}, the live REST switches take over."""
        assert _plug(with_socket_toggles=True).mqtt_outlet_count == 0

    def test_other_plugs_unaffected(self):
        assert _plug(sku="H5083").mqtt_outlet_count == 0


def _coordinator(*, connected=True) -> GoveeCoordinator:
    coord = GoveeCoordinator.__new__(GoveeCoordinator)
    coord._devices = {PLUG: _plug()}
    coord._states = {PLUG: GoveeDeviceState.create_empty(PLUG)}
    coord._transport = TransportHealthTracker()
    coord._api_client = MagicMock()
    coord._mqtt_client = MagicMock()
    coord._mqtt_client.connected = connected
    coord._mqtt_client.async_publish_command = AsyncMock(return_value=True)
    coord._ensure_device_topic = AsyncMock(return_value="GD/plug")
    coord.async_set_updated_data = MagicMock()
    return coord


class TestSetMqttOutlet:
    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("index", "enabled", "value"),
        [(0, True, 17), (0, False, 16), (1, True, 34), (1, False, 32), (2, True, 68), (2, False, 64)],
    )
    async def test_bitmask_matches_homebridge(self, index, enabled, value):
        coord = _coordinator()

        assert await coord.async_set_mqtt_outlet(PLUG, index, enabled) is True

        coord._mqtt_client.async_publish_command.assert_awaited_once_with("GD/plug", "turn", {"val": value})
        assert coord._states[PLUG].toggles[f"outlet{index + 1}"] is enabled
        coord.async_set_updated_data.assert_called_once()
        args, kwargs = coord._api_client.record_local_command.call_args
        assert args[2] == "mqtt" and kwargs["delivered"] is True

    @pytest.mark.asyncio
    async def test_no_session_means_no_rest_fallback(self):
        coord = _coordinator(connected=False)

        assert await coord.async_set_mqtt_outlet(PLUG, 0, True) is False

        coord._mqtt_client.async_publish_command.assert_not_awaited()
        assert "outlet1" not in coord._states[PLUG].toggles

    @pytest.mark.asyncio
    async def test_out_of_range_outlet_rejected(self):
        coord = _coordinator()
        assert await coord.async_set_mqtt_outlet(PLUG, 3, True) is False
        coord._mqtt_client.async_publish_command.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_failed_publish_leaves_state_alone(self):
        coord = _coordinator()
        coord._mqtt_client.async_publish_command.return_value = False

        assert await coord.async_set_mqtt_outlet(PLUG, 1, True) is False
        assert coord._states[PLUG].toggles == {}
        coord.async_set_updated_data.assert_not_called()


class TestOutletEntity:
    def _entity(self, connected=True):
        device = _plug()
        state = GoveeDeviceState.create_empty(PLUG)
        state.online = True
        coordinator = MagicMock()
        coordinator.devices = {PLUG: device}
        coordinator.get_state = MagicMock(return_value=state)
        coordinator.mqtt_connected = connected
        coordinator.async_set_mqtt_outlet = AsyncMock(return_value=True)
        entity = GoveeMqttOutletSwitchEntity(coordinator, device, 2)
        entity.async_write_ha_state = MagicMock()
        return entity, coordinator, state

    def test_identity(self):
        entity, _, _ = self._entity()
        assert entity.unique_id == f"{PLUG}_mqtt_outlet_2"
        assert entity.translation_placeholders == {"socket": "3"}
        assert entity.assumed_state is True

    def test_unavailable_without_mqtt(self):
        entity, _, _ = self._entity(connected=False)
        assert entity.available is False

    @pytest.mark.asyncio
    async def test_turn_on_routes_to_coordinator(self):
        entity, coordinator, state = self._entity()

        await entity.async_turn_on()

        coordinator.async_set_mqtt_outlet.assert_awaited_once_with(PLUG, 2, True)
        assert entity.is_on is True

    def test_shared_state_wins_over_restored(self):
        entity, _, state = self._entity()
        entity._is_on = True
        state.toggles["outlet3"] = False
        assert entity.is_on is False


class TestPushChangeDetection:
    def _coordinator(self):
        coord = GoveeCoordinator.__new__(GoveeCoordinator)
        lamp = GoveeDevice(
            device_id=PLUG, sku="H6054", name="Lamp", device_type="devices.types.light",
            capabilities=(GoveeCapability(type=CAPABILITY_ON_OFF, instance=INSTANCE_POWER, parameters={}),),
        )
        coord._devices = {PLUG: lamp}
        state = GoveeDeviceState.create_empty(PLUG)
        state.online = True
        state.source = "mqtt"
        state.power_state = True
        state.brightness = 50
        coord._states = {PLUG: state}
        coord._transport = TransportHealthTracker()
        coord.async_set_updated_data = MagicMock()
        return coord

    def test_identical_push_does_not_notify(self):
        coord = self._coordinator()

        coord._on_mqtt_state_update(PLUG, {"onOff": 1, "brightness": 50})

        coord.async_set_updated_data.assert_not_called()
        # ...but the transport health still records the receive.
        assert coord._transport.get(PLUG, "mqtt").last_success_ts is not None

    def test_changed_push_notifies(self):
        coord = self._coordinator()

        coord._on_mqtt_state_update(PLUG, {"onOff": 0})

        coord.async_set_updated_data.assert_called_once()
        assert coord._states[PLUG].power_state is False


class TestLeakPollCoalescing:
    def _coordinator(self):
        coord = GoveeCoordinator.__new__(GoveeCoordinator)
        coord.hass = MagicMock()
        coord._config_entry = MagicMock()
        coord._bff_poll_task = None
        coord._poll_bff_leak_state = MagicMock(return_value=MagicMock())
        return coord

    def test_second_frame_reuses_pending_poll(self):
        coord = self._coordinator()
        pending = MagicMock()
        pending.done.return_value = False
        coord._config_entry.async_create_background_task.return_value = pending

        coord._schedule_bff_leak_poll()
        coord._schedule_bff_leak_poll()

        assert coord._config_entry.async_create_background_task.call_count == 1

    def test_finished_poll_allows_a_new_one(self):
        coord = self._coordinator()
        done = MagicMock()
        done.done.return_value = True
        coord._config_entry.async_create_background_task.return_value = done

        coord._schedule_bff_leak_poll()
        coord._schedule_bff_leak_poll()

        assert coord._config_entry.async_create_background_task.call_count == 2


class TestDisconnectHook:
    def test_disconnect_and_connect_nudge_listeners(self, monkeypatch):
        from custom_components.govee import coordinator as coord_mod

        monkeypatch.setattr(coord_mod, "async_delete_mqtt_issue", MagicMock())
        coord = GoveeCoordinator.__new__(GoveeCoordinator)
        coord.hass = MagicMock()
        coord._config_entry = MagicMock()
        coord._states = {}
        coord.async_set_updated_data = MagicMock()

        coord._on_mqtt_disconnected()
        coord._on_mqtt_connected()

        assert coord.async_set_updated_data.call_count == 2


class TestOutletReadback:
    """The strip reports its outlets as a bitmask on both channels (#184)."""

    def test_push_on_off_bitmask_sets_toggles(self):
        coord = _coordinator()
        coord._on_mqtt_state_update(PLUG, {"onOff": 2})
        state = coord._states[PLUG]
        assert state.toggles == {"outlet1": False, "outlet2": True, "outlet3": False}
        assert state.power_state is True

    def test_poll_power_switch_bitmask_sets_toggles(self):
        coord = _coordinator()
        coord._api_client.last_raw_state = {
            PLUG: {"capabilities": [{"instance": "powerSwitch", "state": {"value": 6}}]}
        }
        state = GoveeDeviceState.create_empty(PLUG)
        coord._apply_outlet_mask(coord._devices[PLUG], state, coord._raw_power_switch_value(PLUG))
        assert state.toggles == {"outlet1": False, "outlet2": True, "outlet3": True}
        assert state.power_state is True

    def test_all_off_and_out_of_range(self):
        coord = _coordinator()
        state = GoveeDeviceState.create_empty(PLUG)
        coord._apply_outlet_mask(coord._devices[PLUG], state, 0)
        assert state.toggles == {"outlet1": False, "outlet2": False, "outlet3": False}
        assert state.power_state is False
        coord._apply_outlet_mask(coord._devices[PLUG], state, 17)  # a send value, not a report
        assert state.toggles["outlet1"] is False
        coord._apply_outlet_mask(coord._devices[PLUG], state, True)
        assert state.toggles["outlet1"] is False

    def test_entity_stops_being_assumed_once_reported(self):
        device = _plug()
        state = GoveeDeviceState.create_empty(PLUG)
        state.online = True
        coordinator = MagicMock()
        coordinator.devices = {PLUG: device}
        coordinator.get_state = MagicMock(return_value=state)
        coordinator.mqtt_connected = True
        entity = GoveeMqttOutletSwitchEntity(coordinator, device, 1)
        assert entity.assumed_state is True
        state.toggles["outlet2"] = True
        assert entity.assumed_state is False
        assert entity.is_on is True
