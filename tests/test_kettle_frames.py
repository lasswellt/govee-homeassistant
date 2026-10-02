"""H7175 status frames: modes, presets, DIY slot, heating and keep warm."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from custom_components.govee.kettle.frames import HEATING_STATUS, decode_kettle_frames
from custom_components.govee.kettle.manager import KettleManager, _diy_slot
from custom_components.govee.models import GoveeDevice, GoveeDeviceState

from .kettle_samples import (
    DEVICE_ID,
    H7175_DEVICE,
    H7175_MQTT,
    H7175_STATE,
    HEATING_IDLE,
    KEEP_WARM_ECHO_ON_1H,
    KEEP_WARM_ECHO_ON_30M,
    KEEP_WARM_ECHO_ON_90M,
    KEEP_WARM_STATUS_OFF_2H,
    HEATING_KEEPING_WARM,
    HEATING_REACHED_TARGET,
    KEEP_WARM_STATUS_ON_2H,
    KEEP_WARM_STATUS_ON_2H_116_LEFT,
    MODE_MANUAL_176,
    SLOT_PAGE0_DIY_1,
    mqtt_frames,
)


def _frame(*head: int) -> bytes:
    body = list(head) + [0] * (19 - len(head))
    checksum = 0
    for byte in body:
        checksum ^= byte
    return bytes(body + [checksum])


def _hex(*frames: str) -> list[bytes]:
    return [bytes.fromhex(frame) for frame in frames]


class TestCapturedPush:
    def test_everything_in_the_sample(self):
        report = decode_kettle_frames(mqtt_frames())
        assert (report.work_mode, report.mode_value, report.manual_target) == (1, 4, None)
        assert report.preset_temperatures == {
            1: {1: 175.0, 2: 110.0, 3: 165.0, 4: 176.0},  # the flag bit masked off
            2: {0: 180.0},
            3: {0: 195.0},
            4: {0: 205.0},
            5: {0: 212.0},
        }
        assert report.slot_flags == {1: True, 2: True, 3: True, 4: False}
        assert (report.heating_seen, report.heating_status) == (True, "idle")
        assert report.keep_warm == (True, 120)

    def test_manual_frame(self):
        """workMode 6 with the target set directly: 0x44 is not a slot."""
        report = decode_kettle_frames(_hex(MODE_MANUAL_176))
        assert (report.work_mode, report.mode_value, report.manual_target) == (6, None, 176.0)

    @pytest.mark.parametrize(
        ("head", "expected"),
        [
            ((0xAA, 0x05, 0x00, 0x03, 0x02), (3, None, None)),  # a built-in mode: byte 4 is not a slot
            ((0xAA, 0x05, 0x00, 0x09, 0x44, 0xC0), (9, None, None)),  # unknown workMode
            ((0xAA, 0x05, 0x00, 0x06), (6, None, None)),  # manual without a target
            ((0xAA, 0x05, 0x00, 0x00, 0x04), (None, None, None)),  # workMode 0 is no selection
        ],
    )
    def test_selected_mode_frames(self, head, expected):
        report = decode_kettle_frames([_frame(*head)])
        assert (report.work_mode, report.mode_value, report.manual_target) == expected

    def test_empty_slots_and_zero_presets_are_skipped(self):
        report = decode_kettle_frames(
            [_frame(0xAA, 0x05, 0x01, 0x00, 0x00, 0x00, 0x01, 0x00, 0xC4, 0x5C, 0x00), _frame(0xAA, 0x05, 0x02)]
        )
        assert (report.preset_temperatures, report.slot_flags) == ({}, {})


class TestHeatingAndKeepWarm:
    @pytest.mark.parametrize("code", list(HEATING_STATUS))
    def test_documented_codes(self, code):
        assert decode_kettle_frames([_frame(0xAA, 0x19, code)]).heating_status == HEATING_STATUS[code]

    def test_an_undocumented_code_is_unknown_but_seen(self):
        report = decode_kettle_frames([_frame(0xAA, 0x19, 0x07)])
        assert (report.heating_seen, report.heating_status) == (True, None)
        assert decode_kettle_frames(_hex(HEATING_IDLE)).heating_status == "idle"

    @pytest.mark.parametrize(
        ("frames", "keep_warm"),
        [
            ((KEEP_WARM_STATUS_OFF_2H,), (False, 120)),
            ((KEEP_WARM_STATUS_ON_2H,), (True, 120)),
            ((KEEP_WARM_ECHO_ON_30M,), (True, 30)),
            ((KEEP_WARM_STATUS_OFF_2H, KEEP_WARM_ECHO_ON_1H), (False, 120)),  # a status wins over an echo
            ((KEEP_WARM_ECHO_ON_1H, KEEP_WARM_STATUS_OFF_2H), (False, 120)),
            ((KEEP_WARM_ECHO_ON_30M, KEEP_WARM_ECHO_ON_90M), (True, 90)),  # a later echo wins
        ],
    )
    def test_keep_warm(self, frames, keep_warm):
        assert decode_kettle_frames(_hex(*frames)).keep_warm == keep_warm

    @pytest.mark.parametrize("head", [(0xAA, 0x22, 0x02, 0x00, 0x78, 0x78), (0xAA, 0x22, 0x01, 0x00, 0x78, 0x79)])
    def test_inconsistent_keep_warm_is_skipped(self, head):
        assert decode_kettle_frames([_frame(*head)]).keep_warm is None

    def test_keep_warm_counting_down(self):
        """Byte 5 of the status is the time left, not a repeat of the duration."""
        report = decode_kettle_frames(_hex(HEATING_REACHED_TARGET, KEEP_WARM_STATUS_ON_2H_116_LEFT))
        assert (report.keep_warm, report.keep_warm_remaining, report.heating_status) == (
            (True, 120),
            116,
            "reached_target",
        )
        report = decode_kettle_frames(_hex(HEATING_KEEPING_WARM, KEEP_WARM_STATUS_ON_2H))
        assert (report.keep_warm_remaining, report.heating_status) == (120, "keeping_warm")
        assert decode_kettle_frames(_hex(KEEP_WARM_ECHO_ON_1H)).keep_warm_remaining is None  # echoes repeat

    def test_echoes_count_only_for_keep_warm(self):
        report = decode_kettle_frames([_frame(0x3A, 0x19, 0x01), _frame(0x3A, 0x05, 0x00, 0x02), _frame(0x33, 0x01)])
        assert (report.heating_seen, report.work_mode) == (False, None)


class TestDiySlot:
    @pytest.mark.parametrize(
        ("previous", "flags", "diy"),
        [
            (None, {1: True, 2: True, 3: True, 4: False}, 4),
            (4, {1: False, 2: True}, 1),  # page 0 alone, slot 1 clear: it moved
            (4, {1: True, 2: True}, 4),  # page 0 alone, all flagged: slot 4 is unseen, kept
            (2, {1: True, 2: True}, None),  # the known slot is now flagged
            (None, {1: False, 2: False}, None),  # two clear: ambiguous
        ],
    )
    def test_rule(self, previous, flags, diy):
        assert _diy_slot(previous, flags) == diy

    def test_captured_page(self):
        assert decode_kettle_frames(_hex(SLOT_PAGE0_DIY_1)).slot_flags == {1: False, 2: True}


class TestManagerPush:
    def _manager(self, *, declared: str | None = "Fahrenheit"):
        state = GoveeDeviceState.create_empty(DEVICE_ID)
        state.update_from_api(H7175_STATE)
        state.device_temperature_unit = declared
        coordinator = SimpleNamespace(
            config_entry=SimpleNamespace(options={}),
            devices={DEVICE_ID: GoveeDevice.from_api_response(H7175_DEVICE)},
            get_state=lambda _id: state,
            account_temperature_unit=lambda _id: None,
        )
        return KettleManager(coordinator), state  # type: ignore[arg-type]

    def test_sample_push(self):
        manager, state = self._manager()
        manager.on_push(DEVICE_ID, state, H7175_MQTT, mqtt_frames())
        assert (state.work_mode, state.mode_value, state.kettle_mode_value) == (1, 4, 4)
        assert state.kettle_preset_temperatures[2] == {0: 180.0}
        assert (state.kettle_diy_slot, state.kettle_heating_status) == (4, "idle")
        assert (state.kettle_keep_warm_enabled, state.kettle_keep_warm_minutes) == (True, 120)
        assert state.kettle_keep_warm_remaining == 120
        manager.on_push(DEVICE_ID, state, {}, _hex(KEEP_WARM_STATUS_ON_2H_116_LEFT))
        assert state.kettle_keep_warm_remaining == 116
        manager.on_push(DEVICE_ID, state, {}, _hex(KEEP_WARM_ECHO_ON_1H))  # an echo leaves it
        assert (state.kettle_keep_warm_minutes, state.kettle_keep_warm_remaining) == (60, 116)
        assert (state.sensor_temperature, state.kettle_target_temperature) == (90.0, 176.0)

    def test_manual_push_sets_mode_and_target(self):
        manager, state = self._manager()
        state.kettle_mode_value = 4
        manager.on_push(DEVICE_ID, state, {}, _hex(MODE_MANUAL_176))
        assert (state.work_mode, state.kettle_mode_value, state.kettle_target_temperature) == (6, None, 176.0)

    def test_sta_wins_over_the_manual_frame(self):
        manager, state = self._manager()
        manager.on_push(DEVICE_ID, state, {"sta": {"setTem": 17500}}, _hex(MODE_MANUAL_176))
        assert state.kettle_target_temperature == 175.0

    def test_without_the_unit_only_temperatures_wait(self):
        manager, state = self._manager(declared=None)
        manager.on_push(DEVICE_ID, state, H7175_MQTT, mqtt_frames())
        assert (state.work_mode, state.kettle_diy_slot, state.kettle_keep_warm_minutes) == (1, 4, 120)
        assert (state.kettle_preset_temperatures, state.sensor_temperature) == ({}, 91.0)

    def test_presets_merge_across_pushes(self):
        manager, state = self._manager()
        manager.on_push(DEVICE_ID, state, {}, _hex(SLOT_PAGE0_DIY_1))
        manager.on_push(DEVICE_ID, state, {}, mqtt_frames()[8:9])
        assert state.kettle_preset_temperatures[1] == {1: 175.0, 2: 110.0, 3: 165.0, 4: 176.0}
        assert state.kettle_diy_slot == 4  # page 1 has slot 4 clear: the DIY slot moved
