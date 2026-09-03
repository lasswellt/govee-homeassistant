"""Ceiling-fan combo (H1310/H1370) state from AWS IoT status frames — issue #181.

The Developer API returns "" for every fan value on these units, and their
device-wide ``onOff`` is the unit's power rather than the light's. The fan
and both lights are reported as BLE-format frames in the push's
``op.command`` list (homebridge-govee ``fan-ceiling.js``, captures in
homebridge-govee #1352 / #1358).
"""

from __future__ import annotations

import base64
import json
from unittest.mock import MagicMock

import pytest

from custom_components.govee.api.auth import GoveeIotCredentials
from custom_components.govee.api.mqtt import GoveeAwsIotClient
from custom_components.govee.coordinator import GoveeCoordinator
from custom_components.govee.models import (
    GoveeCapability,
    GoveeDevice,
    GoveeDeviceState,
    ModeCommand,
    ToggleCommand,
)
from custom_components.govee.models.device import (
    CAPABILITY_MODE,
    CAPABILITY_ON_OFF,
    CAPABILITY_TOGGLE,
    INSTANCE_BACKGROUND_LIGHT_TOGGLE,
    INSTANCE_FAN_OSCILLATE,
    INSTANCE_FAN_SPEED_MODE,
    INSTANCE_FAN_TOGGLE,
    INSTANCE_MAIN_LIGHT_TOGGLE,
    INSTANCE_POWER,
    INSTANCE_REVERSE_AIRFLOW,
)
from custom_components.govee.transport_health import TransportHealthTracker

FAN_ID = "AA:BB:CC:DD:EE:FF:13:10"
LAMP_ID = "AA:BB:CC:DD:EE:FF:60:54"


def _frame(*body: int) -> bytes:
    """Zero-pad a status frame to the 20 bytes the fan sends."""
    return bytes(body) + bytes(20 - len(body))


# Labelled captures from homebridge-govee #1352 / #1358.
FAN_RUNNING_SPEED4_DOWN = _frame(0xAA, 0x31, 0x01, 0x04, 0x00)
FAN_RUNNING_SPEED1_UP = _frame(0xAA, 0x31, 0x01, 0x01, 0x01)
FAN_STOPPED = _frame(0xAA, 0x31, 0x00, 0x04, 0x00)
FAN_SWINGING = _frame(0xAA, 0x31, 0x01, 0x04, 0x00, 0x00, 0x00, 0x01)
LIGHTS_MASK_BOTH = _frame(0xAA, 0x42, 0xE0)
LIGHTS_MASK_MAIN = _frame(0xAA, 0x42, 0xC0)
LIGHTS_MASK_BACKGROUND = _frame(0xAA, 0x42, 0xA0)
LIGHTS_MASK_NONE = _frame(0xAA, 0x42, 0x00)
LIGHTS_PAIR_MAIN_ONLY = _frame(0xAA, 0x36, 0x01, 0x00)
LIGHTS_PAIR_NONE = _frame(0xAA, 0x36, 0x00, 0x00)


def _h1310() -> GoveeDevice:
    on = {"name": "on", "value": 1}
    off = {"name": "off", "value": 0}
    toggle = {"dataType": "ENUM", "options": [on, off]}
    return GoveeDevice(
        device_id=FAN_ID,
        sku="H1310",
        name="Ceiling Fan",
        device_type="devices.types.light",
        capabilities=(
            GoveeCapability(type=CAPABILITY_ON_OFF, instance=INSTANCE_POWER, parameters={}),
            GoveeCapability(type=CAPABILITY_TOGGLE, instance=INSTANCE_FAN_TOGGLE, parameters=toggle),
            GoveeCapability(
                type=CAPABILITY_MODE,
                instance=INSTANCE_FAN_SPEED_MODE,
                parameters={
                    "dataType": "ENUM",
                    "options": [{"name": f"Speed {i}", "value": i} for i in range(1, 7)],
                },
            ),
            GoveeCapability(type=CAPABILITY_TOGGLE, instance=INSTANCE_REVERSE_AIRFLOW, parameters=toggle),
            GoveeCapability(type=CAPABILITY_TOGGLE, instance=INSTANCE_MAIN_LIGHT_TOGGLE, parameters=toggle),
            GoveeCapability(
                type=CAPABILITY_TOGGLE, instance=INSTANCE_BACKGROUND_LIGHT_TOGGLE, parameters=toggle
            ),
        ),
    )


def _lamp() -> GoveeDevice:
    return GoveeDevice(
        device_id=LAMP_ID,
        sku="H6054",
        name="Lamp",
        device_type="devices.types.light",
        capabilities=(
            GoveeCapability(type=CAPABILITY_ON_OFF, instance=INSTANCE_POWER, parameters={}),
        ),
    )


# --------------------------------------------------------------------------- #
# GoveeDeviceState.update_ceiling_fan_from_frames
# --------------------------------------------------------------------------- #
class TestCeilingFanFrames:
    def test_speed_frame_sets_running_speed_and_direction(self):
        state = GoveeDeviceState.create_empty(FAN_ID)

        assert state.update_ceiling_fan_from_frames([FAN_RUNNING_SPEED4_DOWN]) is True

        assert state.ceiling_fan_on is True
        assert state.ceiling_fan_speed == 4
        assert state.ceiling_fan_reverse is False
        assert state.ceiling_fan_swing is False

    def test_reverse_airflow_is_byte_four(self):
        state = GoveeDeviceState.create_empty(FAN_ID)
        state.update_ceiling_fan_from_frames([FAN_RUNNING_SPEED1_UP])
        assert state.ceiling_fan_reverse is True
        assert state.ceiling_fan_speed == 1

    def test_stopped_fan_keeps_its_last_speed(self):
        """The speed byte is the fan's setting; it survives the fan stopping."""
        state = GoveeDeviceState.create_empty(FAN_ID)
        state.update_ceiling_fan_from_frames([FAN_RUNNING_SPEED4_DOWN])
        state.update_ceiling_fan_from_frames([FAN_STOPPED])
        assert state.ceiling_fan_on is False
        assert state.ceiling_fan_speed == 4

    def test_zero_speed_byte_does_not_clobber_speed(self):
        state = GoveeDeviceState.create_empty(FAN_ID)
        state.ceiling_fan_speed = 3
        state.update_ceiling_fan_from_frames([_frame(0xAA, 0x31, 0x00, 0x00, 0x00)])
        assert state.ceiling_fan_speed == 3

    def test_oscillation_is_byte_seven(self):
        state = GoveeDeviceState.create_empty(FAN_ID)
        state.update_ceiling_fan_from_frames([FAN_SWINGING])
        assert state.ceiling_fan_swing is True

    def test_short_speed_frame_leaves_swing_alone(self):
        state = GoveeDeviceState.create_empty(FAN_ID)
        state.ceiling_fan_swing = True
        state.update_ceiling_fan_from_frames([bytes([0xAA, 0x31, 0x01, 0x02, 0x00])])
        assert state.ceiling_fan_on is True
        assert state.ceiling_fan_swing is True

    @pytest.mark.parametrize(
        ("frame", "main", "background", "lit"),
        [
            (LIGHTS_MASK_BOTH, True, True, True),
            (LIGHTS_MASK_MAIN, True, False, True),
            (LIGHTS_MASK_BACKGROUND, False, True, True),
            (LIGHTS_MASK_NONE, False, False, False),
            (LIGHTS_PAIR_MAIN_ONLY, True, False, True),
            (LIGHTS_PAIR_NONE, False, False, False),
        ],
    )
    def test_light_frames_drive_toggles_and_power(self, frame, main, background, lit):
        state = GoveeDeviceState.create_empty(FAN_ID)
        state.power_state = not lit  # prove the frame decides it

        assert state.update_ceiling_fan_from_frames([frame]) is True

        assert state.toggles[INSTANCE_MAIN_LIGHT_TOGGLE] is main
        assert state.toggles[INSTANCE_BACKGROUND_LIGHT_TOGGLE] is background
        assert state.power_state is lit

    def test_unknown_and_short_frames_are_ignored(self):
        state = GoveeDeviceState.create_empty(FAN_ID)
        unknown = _frame(0xAA, 0x05, 0x01)
        short = bytes([0xAA, 0x31])
        not_status = _frame(0x33, 0x31, 0x01, 0x04, 0x00)

        assert state.update_ceiling_fan_from_frames([unknown, short, not_status, b""]) is False

        assert state.ceiling_fan_on is None
        assert state.toggles == {}

    def test_ceiling_fan_push_ignores_device_wide_on_off(self):
        """onOff 1 was captured alongside fan off + both lights off (#1352)."""
        state = GoveeDeviceState.create_empty(FAN_ID)
        state.power_state = False

        state.update_from_mqtt({"onOff": 1, "brightness": 2}, ceiling_fan=True)

        assert state.power_state is False
        assert state.brightness == 2
        assert state.source == "mqtt"

    def test_ordinary_push_still_applies_on_off(self):
        state = GoveeDeviceState.create_empty(LAMP_ID)
        state.update_from_mqtt({"onOff": 1})
        assert state.power_state is True


# --------------------------------------------------------------------------- #
# MQTT client attaches the decoded frames to the state it hands over
# --------------------------------------------------------------------------- #
def _client(callback: MagicMock) -> GoveeAwsIotClient:
    creds = GoveeIotCredentials(
        token="t",
        refresh_token="r",
        account_topic="GA/account",
        iot_cert="cert",
        iot_key="key",
        iot_ca=None,
        client_id="cid",
        endpoint="endpoint",
    )
    return GoveeAwsIotClient(creds, on_state_update=callback)


def _status_message(device_id: str, sku: str, state: dict, frames: list[bytes | str]) -> MagicMock:
    payload = {
        "device": device_id,
        "sku": sku,
        "cmd": "status",
        "state": state,
        "op": {"command": [f if isinstance(f, str) else base64.b64encode(f).decode() for f in frames]},
    }
    message = MagicMock()
    message.topic = "GA/account"
    message.payload = json.dumps(payload).encode()
    return message


class TestClientAttachesFrames:
    @pytest.mark.asyncio
    async def test_status_push_carries_frames_as_hex(self):
        callback = MagicMock()
        client = _client(callback)

        await client._handle_message(
            _status_message(
                FAN_ID, "H1310", {"onOff": 1, "brightness": 2}, [FAN_RUNNING_SPEED4_DOWN, "@@bad@@", LIGHTS_MASK_NONE]
            )
        )

        callback.assert_called_once()
        device_id, state = callback.call_args[0]
        assert device_id == FAN_ID
        assert state["onOff"] == 1
        assert state["_op_frames"] == [FAN_RUNNING_SPEED4_DOWN.hex(), LIGHTS_MASK_NONE.hex()]
        # The diagnostics copy carries them too.
        assert client.last_messages[FAN_ID]["_op_frames"] == state["_op_frames"]

    @pytest.mark.asyncio
    async def test_push_without_frames_has_no_key(self):
        callback = MagicMock()
        client = _client(callback)

        await client._handle_message(_status_message(LAMP_ID, "H6054", {"onOff": 0}, []))

        assert "_op_frames" not in callback.call_args[0][1]


# --------------------------------------------------------------------------- #
# Coordinator routes the frames only for ceiling-fan combos
# --------------------------------------------------------------------------- #
def _coordinator() -> GoveeCoordinator:
    coordinator = GoveeCoordinator.__new__(GoveeCoordinator)
    coordinator._devices = {FAN_ID: _h1310(), LAMP_ID: _lamp()}
    coordinator._states = {
        FAN_ID: GoveeDeviceState.create_empty(FAN_ID),
        LAMP_ID: GoveeDeviceState.create_empty(LAMP_ID),
    }
    coordinator._transport = TransportHealthTracker()
    coordinator.async_set_updated_data = MagicMock()
    return coordinator


class TestCoordinatorCeilingFanPush:
    def test_ceiling_fan_push_decodes_frames_and_ignores_on_off(self):
        coordinator = _coordinator()

        coordinator._on_mqtt_state_update(
            FAN_ID,
            {
                "onOff": 1,
                "brightness": 2,
                "_op_frames": [FAN_RUNNING_SPEED4_DOWN.hex(), LIGHTS_MASK_NONE.hex(), "zz"],
            },
        )

        state = coordinator._states[FAN_ID]
        assert state.ceiling_fan_on is True
        assert state.ceiling_fan_speed == 4
        assert state.power_state is False  # both lights off, despite onOff 1
        assert state.toggles[INSTANCE_MAIN_LIGHT_TOGGLE] is False
        assert state.brightness == 2
        coordinator.async_set_updated_data.assert_called_once()

    def test_ordinary_light_push_untouched_by_frames(self):
        coordinator = _coordinator()

        coordinator._on_mqtt_state_update(
            LAMP_ID, {"onOff": 1, "_op_frames": [FAN_RUNNING_SPEED4_DOWN.hex()]}
        )

        state = coordinator._states[LAMP_ID]
        assert state.power_state is True
        assert state.ceiling_fan_on is None
        assert state.toggles == {}


class TestCoordinatorOptimisticCeilingFan:
    def test_fan_toggle_and_speed(self):
        coordinator = _coordinator()
        state = coordinator._states[FAN_ID]

        coordinator._apply_optimistic_update(
            FAN_ID, ToggleCommand(toggle_instance=INSTANCE_FAN_TOGGLE, enabled=True)
        )
        assert state.ceiling_fan_on is True

        coordinator._apply_optimistic_update(
            FAN_ID, ModeCommand(mode_instance=INSTANCE_FAN_SPEED_MODE, value=5)
        )
        assert state.ceiling_fan_speed == 5
        assert state.ceiling_fan_on is True

        coordinator._apply_optimistic_update(
            FAN_ID, ToggleCommand(toggle_instance=INSTANCE_FAN_TOGGLE, enabled=False)
        )
        assert state.ceiling_fan_on is False

    def test_direction_change_marks_fan_running(self):
        coordinator = _coordinator()
        state = coordinator._states[FAN_ID]
        state.ceiling_fan_on = False

        coordinator._apply_optimistic_update(
            FAN_ID, ToggleCommand(toggle_instance=INSTANCE_REVERSE_AIRFLOW, enabled=True)
        )

        assert state.ceiling_fan_reverse is True
        assert state.ceiling_fan_on is True

    def test_oscillation_and_named_light_toggles(self):
        coordinator = _coordinator()
        state = coordinator._states[FAN_ID]

        coordinator._apply_optimistic_update(
            FAN_ID, ToggleCommand(toggle_instance=INSTANCE_FAN_OSCILLATE, enabled=True)
        )
        coordinator._apply_optimistic_update(
            FAN_ID, ToggleCommand(toggle_instance=INSTANCE_MAIN_LIGHT_TOGGLE, enabled=True)
        )

        assert state.ceiling_fan_swing is True
        assert state.toggles[INSTANCE_MAIN_LIGHT_TOGGLE] is True
        assert INSTANCE_BACKGROUND_LIGHT_TOGGLE not in state.toggles


# --------------------------------------------------------------------------- #
# The Developer poll returns "" for all of it — the values must survive a poll
# --------------------------------------------------------------------------- #
class TestPollPreservesCeilingFanState:
    def _coordinator(self, fresh: GoveeDeviceState, existing: GoveeDeviceState) -> GoveeCoordinator:
        from unittest.mock import AsyncMock

        coordinator = GoveeCoordinator.__new__(GoveeCoordinator)
        coordinator._devices = {FAN_ID: _h1310()}
        coordinator._states = {FAN_ID: existing}
        coordinator._transport = TransportHealthTracker()
        coordinator._bff_thermometer_ids = set()
        coordinator._sensor_reading_changed_at = {}
        coordinator.update_interval = None
        coordinator._api_client = MagicMock()
        coordinator._api_client.get_device_state = AsyncMock(return_value=fresh)
        return coordinator

    @pytest.mark.asyncio
    async def test_fan_fields_and_light_toggles_carry_across_a_poll(self):
        existing = GoveeDeviceState.create_empty(FAN_ID)
        existing.update_ceiling_fan_from_frames([FAN_RUNNING_SPEED4_DOWN, LIGHTS_MASK_MAIN])
        fresh = GoveeDeviceState.create_empty(FAN_ID)  # what the "" poll parses to
        fresh.power_state = True
        coordinator = self._coordinator(fresh, existing)

        result = await coordinator._fetch_device_state(FAN_ID, coordinator._devices[FAN_ID])

        assert result is fresh
        assert fresh.ceiling_fan_on is True
        assert fresh.ceiling_fan_speed == 4
        assert fresh.ceiling_fan_reverse is False
        assert fresh.toggles[INSTANCE_MAIN_LIGHT_TOGGLE] is True
        assert fresh.toggles[INSTANCE_BACKGROUND_LIGHT_TOGGLE] is False

    @pytest.mark.asyncio
    async def test_toggles_the_poll_did_return_win(self):
        existing = GoveeDeviceState.create_empty(FAN_ID)
        existing.toggles["socketToggle1"] = True
        fresh = GoveeDeviceState.create_empty(FAN_ID)
        fresh.toggles["socketToggle1"] = False  # a live 0 from the poll
        coordinator = self._coordinator(fresh, existing)

        await coordinator._fetch_device_state(FAN_ID, coordinator._devices[FAN_ID])

        assert fresh.toggles["socketToggle1"] is False
