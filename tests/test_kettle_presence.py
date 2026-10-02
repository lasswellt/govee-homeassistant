"""H7175: the on-base frame and the frame capture for diagnostics."""

from __future__ import annotations

import copy
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.components.binary_sensor import BinarySensorDeviceClass
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from custom_components.govee.const import KETTLE_FRAME_HISTORY_PER_KIND
from custom_components.govee.kettle.entities import GoveeKettleOnBaseBinarySensor
from custom_components.govee.kettle.frames import decode_kettle_frames
from custom_components.govee.kettle.manager import KettleManager
from custom_components.govee.models import GoveeDevice, GoveeDeviceState, PowerCommand

from .kettle_samples import (
    BASE_OFF,
    BASE_ON,
    BASE_ON_BUTTON,
    DEVICE_ID,
    H7175_DEVICE,
    H7175_STATE,
    HEATING_HEATING,
    HEATING_REACHED_TARGET,
    LIFT_PUSH,
    UNKNOWN_AB,
    mqtt_frames,
)
from .test_setup_entry import _setup_entry


def _frame(*head: int) -> bytes:
    body = list(head) + [0] * (19 - len(head))
    checksum = 0
    for byte in body:
        checksum ^= byte
    return bytes(body + [checksum])


def _manager():
    state = GoveeDeviceState.create_empty(DEVICE_ID)
    state.update_from_api(H7175_STATE)
    coordinator = SimpleNamespace(
        config_entry=SimpleNamespace(options={}),
        devices={DEVICE_ID: GoveeDevice.from_api_response(H7175_DEVICE)},
        get_state=lambda _id: state,
        account_temperature_unit=lambda _id: None,
    )
    return KettleManager(coordinator), state  # type: ignore[arg-type]


class TestOnBase:
    def test_captured_frames(self):
        assert decode_kettle_frames([bytes.fromhex(BASE_OFF)]).on_base is False
        assert decode_kettle_frames([bytes.fromhex(BASE_ON)]).on_base is True
        assert decode_kettle_frames([_frame(0xAA, 0x17, 0x01, 0x02)]).on_base is None  # other sub-opcodes
        assert decode_kettle_frames(mqtt_frames()).on_base is True  # the sample push: idle on the base

    def test_the_button_bit_does_not_flip_it(self):
        report = decode_kettle_frames([bytes.fromhex(BASE_ON_BUTTON)])
        assert (report.on_base, report.base_frame) == (True, bytes.fromhex(BASE_ON_BUTTON))

    def test_a_lift_while_keeping_warm(self):
        """Every lift pushes aa 10 and aa 22 too; the time left is back at the full 120."""
        manager, state = _manager()
        state.kettle_keep_warm_enabled, state.kettle_keep_warm_remaining = True, 91
        manager.on_push(DEVICE_ID, state, {}, [bytes.fromhex(frame) for frame in LIFT_PUSH])
        assert (state.kettle_on_base, state.kettle_keep_warm_remaining, state.sensor_temperature) == (
            False,
            120,
            176.0,
        )
        assert state.kettle_base_frame == BASE_OFF

    def test_unknown_prefixes_are_ignored_but_kept(self, caplog):
        caplog.set_level(logging.DEBUG, logger="custom_components.govee.kettle.capture")
        manager, state = _manager()
        before = (state.work_mode, state.sensor_temperature)
        report = manager.on_push(DEVICE_ID, state, {}, [bytes.fromhex(UNKNOWN_AB)])
        assert report == decode_kettle_frames([])
        assert (state.work_mode, state.sensor_temperature) == before
        assert [entry["frame"] for entry in manager.recent_frames(DEVICE_ID)] == [UNKNOWN_AB]
        assert UNKNOWN_AB in caplog.text

    def test_push_poll_and_entity(self):
        manager, state = _manager()
        manager.on_push(DEVICE_ID, state, {}, [bytes.fromhex(BASE_OFF)])
        assert state.kettle_on_base is False
        polled = GoveeDeviceState.create_empty(DEVICE_ID)
        manager.merge_poll(DEVICE_ID, state, polled)
        assert polled.kettle_on_base is False
        coordinator = MagicMock()
        coordinator.get_state = MagicMock(return_value=state)
        sensor = GoveeKettleOnBaseBinarySensor(coordinator, GoveeDevice.from_api_response(H7175_DEVICE))
        assert (sensor.is_on, sensor.device_class) == (False, BinarySensorDeviceClass.PLUG)
        assert sensor.unique_id == f"{DEVICE_ID}_kettle_on_base"
        coordinator.get_state.return_value = None
        assert sensor.is_on is None


class TestCapture:
    def test_one_debug_line_per_distinct_frame(self, caplog):
        caplog.set_level(logging.DEBUG, logger="custom_components.govee.kettle.capture")
        manager, state = _manager()
        manager.on_push(DEVICE_ID, state, {}, [bytes.fromhex(BASE_OFF)])
        manager.on_push(DEVICE_ID, state, {}, [bytes.fromhex(BASE_OFF)])  # repeated: not logged again
        manager.on_push(DEVICE_ID, state, {}, [bytes.fromhex("3a2201003c3c0000000000000000000000000019")])
        lines = [r.getMessage() for r in caplog.records if r.name == "custom_components.govee.kettle.capture"]
        assert len(lines) == 2
        assert lines[0].startswith(DEVICE_ID) and lines[0].endswith(f"status {BASE_OFF}")
        assert lines[1].endswith("command 3a2201003c3c0000000000000000000000000019")

    def test_a_rare_frame_survives_a_heating_kettles_temperature_frames(self):
        manager, state = _manager()
        manager.on_push(DEVICE_ID, state, {}, [bytes.fromhex(BASE_OFF)])
        for step in range(60):
            manager.on_push(DEVICE_ID, state, {}, [_frame(0xAA, 0x10, 0x01, 0x23, step)])
        recent = [entry["frame"] for entry in manager.recent_frames(DEVICE_ID)]
        assert BASE_OFF in recent
        assert sum(frame.startswith("aa1001") for frame in recent) == KETTLE_FRAME_HISTORY_PER_KIND


# --------------------------------------------------------------------------- #
# Button presses and immediate heating status, through Home Assistant
# --------------------------------------------------------------------------- #


class TestButtonRule:
    def test_fires_once_per_press(self):
        manager, state = _manager()
        presses: list[int] = []
        remove = manager.add_button_listener(DEVICE_ID, lambda: presses.append(1))
        # The logged sequence: press, release (on the base), press, repeated
        # in a later status push, release (lifted).
        for frame in (BASE_ON_BUTTON, BASE_ON, BASE_ON_BUTTON, BASE_ON_BUTTON, BASE_OFF, BASE_ON_BUTTON):
            manager.on_push(DEVICE_ID, state, {}, [bytes.fromhex(frame)])
            assert state.kettle_on_base is (frame != BASE_OFF)  # the button never moves the base
        assert len(presses) == 3
        remove()
        manager.on_push(DEVICE_ID, state, {}, [bytes.fromhex(BASE_ON)])
        manager.on_push(DEVICE_ID, state, {}, [bytes.fromhex(BASE_ON_BUTTON)])
        assert len(presses) == 3


@pytest.fixture
def _custom_integrations(hass: HomeAssistant, enable_custom_integrations: None) -> None:
    """Load the integration without starting Bluetooth (as in test_setup_entry)."""
    hass.config.components.add("bluetooth_adapters")
    hass.config.components.add("network")


def _entity_id(hass: HomeAssistant, entry, suffix: str) -> str:
    registry = er.async_get(hass)
    return next(
        e.entity_id
        for e in er.async_entries_for_config_entry(registry, entry.entry_id)
        if e.unique_id == f"{DEVICE_ID}{suffix}"
    )


def _polled_state() -> GoveeDeviceState:
    state = GoveeDeviceState.create_empty(DEVICE_ID)
    state.update_from_api(H7175_STATE)
    return state


@pytest.mark.usefixtures("_custom_integrations")
class TestThroughHomeAssistant:
    async def test_button_event(self, hass: HomeAssistant):
        entry = await _setup_entry(hass, GoveeDevice.from_api_response(H7175_DEVICE), _polled_state())
        button = _entity_id(hass, entry, "_kettle_button")
        coordinator = entry.runtime_data
        coordinator._on_mqtt_state_update(DEVICE_ID, {"_op_frames": [BASE_ON_BUTTON]})
        first = hass.states.get(button)
        assert first.attributes["event_type"] == "pressed"
        coordinator._on_mqtt_state_update(DEVICE_ID, {"_op_frames": [BASE_ON_BUTTON]})  # repeated
        assert hass.states.get(button).state == first.state
        assert hass.states.get(_entity_id(hass, entry, "_kettle_on_base")).state == "on"

    async def test_heating_status_shows_at_push_time_and_survives_a_stale_poll(self, hass: HomeAssistant):
        """Heating start and reached target are timestamps for HA-side estimates: never held back."""
        entry = await _setup_entry(hass, GoveeDevice.from_api_response(H7175_DEVICE), _polled_state())
        heating = _entity_id(hass, entry, "_kettle_heating_status")
        coordinator = entry.runtime_data
        coordinator._api_client.get_device_state = AsyncMock(side_effect=lambda *_: copy.deepcopy(_polled_state()))
        coordinator._api_client.control_device = AsyncMock(return_value=True)
        # A command just went out (optimistic grace), and the unit is not even needed.
        await coordinator.async_control_device(DEVICE_ID, PowerCommand(power_on=True))
        coordinator._states[DEVICE_ID].device_temperature_unit = None
        coordinator._on_mqtt_state_update(DEVICE_ID, {"_op_frames": [HEATING_HEATING]})
        assert hass.states.get(heating).state == "heating"  # same tick, no await in between
        await coordinator.async_refresh()  # a cloud poll knows nothing of it
        await hass.async_block_till_done()
        assert hass.states.get(heating).state == "heating"
        coordinator._on_mqtt_state_update(DEVICE_ID, {"_op_frames": [HEATING_REACHED_TARGET]})
        assert hass.states.get(heating).state == "reached_target"
