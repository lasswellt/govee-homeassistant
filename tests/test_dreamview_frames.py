"""H66A0 DreamView switch follows the light's own ``aa 05`` mode report (issue #213)."""

from __future__ import annotations

from custom_components.govee.models import GoveeCapability, GoveeDevice, GoveeDeviceState
from custom_components.govee.models.device import INSTANCE_DREAMVIEW, CAPABILITY_TOGGLE
from tests.test_cov_coordinator_control import _POWER, _add, _coordinator

DEV = "AA:BB:CC:DD:EE:FF:00:11"
_DREAMVIEW = GoveeCapability(type=CAPABILITY_TOGGLE, instance=INSTANCE_DREAMVIEW, parameters={})


def _frame(mode: int, *, tail: bytes = b"\x00\x02") -> bytes:
    body = bytes([0xAA, 0x05, mode]) + b"\x00\x08\x64" + tail
    body = body.ljust(19, b"\x00")
    checksum = 0
    for byte in body:
        checksum ^= byte
    return body + bytes([checksum])


def _state(enabled: bool = False) -> GoveeDeviceState:
    state = GoveeDeviceState.create_empty(DEV)
    state.dreamview_enabled = enabled
    return state


class TestDecoder:
    def test_video_mode_is_on(self):
        state = _state(False)
        assert state.update_dreamview_from_frames([_frame(0x00)]) is True
        assert state.dreamview_enabled is True

    def test_other_modes_are_off(self):
        for mode in (0x01, 0x04):
            state = _state(True)
            assert state.update_dreamview_from_frames([_frame(mode)]) is True
            assert state.dreamview_enabled is False

    def test_unchanged_returns_false(self):
        state = _state(True)
        assert state.update_dreamview_from_frames([_frame(0x00)]) is False
        assert state.dreamview_enabled is True

    def test_invalid_frames_ignored(self):
        bad_sum = bytearray(_frame(0x00))
        bad_sum[19] ^= 0xFF
        other = bytearray(_frame(0x00))
        other[1] = 0x06
        state = _state(False)
        frames = [bytes(bad_sum), _frame(0x00)[:19], _frame(0x00) + b"\x00", bytes(other), b""]
        assert state.update_dreamview_from_frames(frames) is False
        assert state.dreamview_enabled is False


def _push(sku: str, initial: bool, frame: bytes) -> GoveeDeviceState:
    coord = _coordinator()
    device = GoveeDevice(
        device_id=DEV, sku=sku, name="Light", device_type="devices.types.light", capabilities=(_POWER, _DREAMVIEW)
    )
    state = _add(coord, device, _state(initial))
    state.power_state = True
    coord._on_mqtt_state_update(DEV, {"onOff": 1, "_op_frames": [frame.hex()]})
    return state


class TestMqttPush:
    def test_h66a0_clears_stale_optimistic_on(self):
        assert _push("H66A0", True, _frame(0x04)).dreamview_enabled is False

    def test_h66a0_sets_on_from_off(self):
        assert _push("H66A0", False, _frame(0x00)).dreamview_enabled is True

    def test_other_dreamview_models_unchanged(self):
        assert _push("H605B", True, _frame(0x04)).dreamview_enabled is True
        assert _push("H6199", False, _frame(0x00)).dreamview_enabled is False

    def test_non_dreamview_device_unaffected(self):
        coord = _coordinator()
        device = GoveeDevice(
            device_id=DEV, sku="H66A0", name="Light", device_type="devices.types.light", capabilities=(_POWER,)
        )
        state = _add(coord, device, _state(False))
        coord._on_mqtt_state_update(DEV, {"onOff": 1, "_op_frames": [_frame(0x00).hex()]})
        assert state.dreamview_enabled is False
