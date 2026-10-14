"""Reset and reboot tracking for the H5086 energy counter (issue #200).

The readings in these scenarios are captured from two H5086 plugs, a
dehumidifier and a freezer, unless noted otherwise.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.components.sensor import SensorStateClass

from custom_components.govee.coordinator import GoveeCoordinator
from custom_components.govee.entity import GoveeEntity
from custom_components.govee.models import GoveeDeviceState
from custom_components.govee.models.energy_counter import EnergyCounter, answers_other_client, uptime_from_sta
from custom_components.govee.sensor import GoveeEnergySensor

from .test_power_monitoring import DEVICE_ID, _h5086

START = datetime(2026, 10, 10, 4, 0, tzinfo=timezone.utc)
MINUTE = timedelta(minutes=1)


class TestUptimeFromSta:
    def test_fourth_number_of_stc(self):
        assert uptime_from_sta({"stc": "19_0_77_40_1"}) == 40

    @pytest.mark.parametrize("sta", [None, 5, {}, {"stc": 5}, {"stc": "19_0_77"}, {"stc": "19_0_77_x_1"}])
    def test_other_shapes(self, sta):
        assert uptime_from_sta(sta) is None


class TestAnswersOtherClient:
    def test_reply_to_our_query(self):
        assert not answers_other_client({"stc": "19_0_77_40_1"})

    @pytest.mark.parametrize("stc", ["0_0_70_325070_1", "1_0_80_20_1"])
    def test_reply_to_the_govee_app_or_right_after_a_reboot(self, stc):
        assert answers_other_client({"stc": stc})

    @pytest.mark.parametrize("sta", [None, {}, {"stc": 5}])
    def test_without_stc(self, sta):
        assert not answers_other_client(sta)


class TestResets:
    def test_midnight(self):
        # Uptime keeps running
        counter = EnergyCounter.new(START, 58518, 86284, 29790)
        counter.update(START + MINUTE, 1, 5, 29850)
        assert counter.last_reset == START + MINUTE
        assert counter.energy_deciwatt_hours == 1

    def test_midnight_after_a_day_with_less_than_1_wh_used(self):
        # Made up: the outlet was on, but drew next to nothing
        counter = EnergyCounter.new(START, 8, 86000, 29790)
        counter.update(START + MINUTE, 0, 5, 29850)
        assert counter.last_reset == START + MINUTE

    def test_midnight_passed_while_offline_for_8_hours(self):
        later = START + timedelta(hours=8)
        counter = EnergyCounter.new(START, 58518, 86284, 29790)
        counter.update(later, 3120, 26000, 56800)
        assert counter.last_reset == later

    def test_tiny_dips(self):
        counter = EnergyCounter.new(START, 5055, 695, 1000)
        counter.update(START + MINUTE, 5054, 656, 1056)
        assert counter.last_reset == START

    def test_switching_the_outlet_off_and_on(self):
        counter = EnergyCounter.new(START, 5055, 695, 1000)
        counter.update(START + MINUTE, 5055, 695, 1056)
        assert counter.last_reset == START


class TestReboots:
    def test_reboot_with_restore(self):
        # The counter drops along with the uptime, and isn't tracked until
        # the saved copy is restored
        counter = EnergyCounter.new(START, 38910, 54568, 52410)
        counter.update(START + MINUTE, 5, 26, 20)
        counter.update(START + MINUTE, 5, 27, 20)
        assert counter.reboot_seen == START + MINUTE
        assert counter.energy_deciwatt_hours == 38910
        counter.update(START + MINUTE * 2, 38920, 54652, 40)
        assert counter.last_reset == START
        assert counter.reboot_seen is None
        assert counter.energy_deciwatt_hours == 38920

    def test_reboot_with_a_small_saved_copy(self):
        # A heater that ran for 4 minutes: the restore shows in on-time
        # growing faster than the clock
        counter = EnergyCounter.new(START, 1000, 240, 5000)
        counter.update(START + MINUTE, 2, 20, 20)
        assert counter.reboot_seen is not None
        counter.update(START + MINUTE * 2, 1000, 240, 76)
        assert counter.reboot_seen is None
        assert counter.last_reset == START
        assert counter.energy_deciwatt_hours == 1000

    def test_delayed_readings_dont_look_like_a_restore(self):
        # Two readings a minute apart that reach us at the same time, e.g.
        # after a reconnect
        counter = EnergyCounter.new(START, 38910, 54568, 52410)
        counter.update(START + MINUTE, 5, 26, 20)
        counter.update(START + MINUTE * 2, 9, 82, 76)
        counter.update(START + MINUTE * 2, 14, 142, 132)
        assert counter.reboot_seen is not None

    def test_powered_off_over_the_reset(self):
        # No restore comes, so the reboot becomes the reset once we've waited
        # for it long enough
        counter = EnergyCounter.new(START, 58518, 86284, 29790)
        boot = START + MINUTE * 20
        counter.update(boot, 3, 20, 20)
        counter.update(boot + MINUTE * 5, 30, 320, 300)
        assert counter.last_reset == START
        assert counter.energy_deciwatt_hours == 58518
        counter.update(boot + MINUTE * 11, 70, 680, 640)
        assert counter.last_reset == boot
        assert counter.reboot_seen is None
        assert counter.energy_deciwatt_hours == 70

    def test_rebooted_during_a_3_hour_gap(self):
        # Up for longer than it was before by now; the restored copy was
        # 0.84 kWh behind
        counter = EnergyCounter.new(START, 71964, 78032, 5000)
        counter.update(START + timedelta(hours=3), 63553, 69631, 6800)
        assert counter.last_reset == START
        assert counter.reboot_seen is None

    def test_reboot_detection(self):
        counter = EnergyCounter.new(START, 0, 0, 29790)
        # Uptime grows by about 56 seconds per minute
        assert not counter.rebooted(START + MINUTE, 29846)
        assert not counter.rebooted(START + timedelta(days=1), 29790 + 81216)
        assert counter.rebooted(START + MINUTE * 2, 20)
        assert counter.rebooted(START + timedelta(hours=3), 6800)


class TestSerialization:
    def test_round_trip(self):
        counter = EnergyCounter.new(START, 38910, 54568, 52410)
        counter.update(START + MINUTE, 5, 26, 20)
        assert EnergyCounter.from_dict(counter.as_dict()) == counter

    @pytest.mark.parametrize("data", [{}, {"last_reset": "garbage"}, {"last_reset": None}])
    def test_bad_data(self, data):
        assert EnergyCounter.from_dict(data) is None


def _sensor() -> tuple[GoveeEnergySensor, GoveeDeviceState]:
    coordinator = GoveeCoordinator.__new__(GoveeCoordinator)
    state = GoveeDeviceState.create_empty(DEVICE_ID)
    coordinator._states = {DEVICE_ID: state}
    sensor = GoveeEnergySensor(coordinator, _h5086())
    sensor.async_write_ha_state = MagicMock()
    return sensor, state


def _feed(sensor: GoveeEnergySensor, now: datetime, *reading: int) -> None:
    """Update the sensor at ``now``, with a new (energy, on-time, uptime) reading if given."""
    state = sensor.device_state
    if reading:
        state.energy_total = reading[0] / 10000
        state.on_time, state.uptime = reading[1:]
    with patch("custom_components.govee.sensor.dt_util.utcnow", return_value=now):
        sensor._handle_coordinator_update()


class TestEnergySensor:
    def test_total_with_last_reset(self):
        sensor, _ = _sensor()
        assert sensor.state_class == SensorStateClass.TOTAL
        assert sensor.native_value is None
        assert sensor.last_reset is None

        _feed(sensor, START, 58518, 86284, 29790)
        assert sensor.native_value == pytest.approx(5.8518)
        assert sensor.last_reset == START

        _feed(sensor, START + MINUTE, 1, 5, 29850)
        assert sensor.native_value == pytest.approx(0.0001)
        assert sensor.last_reset == START + MINUTE

    def test_holds_the_value_until_the_restore(self):
        sensor, _ = _sensor()
        _feed(sensor, START, 38910, 54568, 52410)
        _feed(sensor, START + MINUTE, 5, 26, 20)
        assert sensor.native_value == pytest.approx(3.891)
        _feed(sensor, START + MINUTE * 2, 38920, 54652, 40)
        assert sensor.native_value == pytest.approx(3.892)
        assert sensor.last_reset == START

    def test_unrelated_updates_dont_count_as_readings(self):
        # A poll that only refreshes the online flag must not make the
        # counter think time passed without the uptime growing
        sensor, _ = _sensor()
        _feed(sensor, START, 71964, 78032, 5000)
        _feed(sensor, START + timedelta(hours=2))
        _feed(sensor, START + timedelta(hours=3), 63553, 69631, 6800)
        assert sensor.last_reset == START

    def test_readings_without_uptime_are_not_tracked(self):
        sensor, state = _sensor()
        state.energy_total, state.on_time = 5.8518, 86284
        _feed(sensor, START)
        assert sensor.native_value is None

    @pytest.mark.asyncio
    async def test_counter_survives_a_restart(self):
        sensor, _ = _sensor()
        _feed(sensor, START, 58518, 86284, 29790)
        saved = sensor.extra_restore_state_data

        restarted, _ = _sensor()
        restarted.async_get_last_extra_data = AsyncMock(return_value=saved)
        with patch.object(GoveeEntity, "async_added_to_hass", AsyncMock()):
            await restarted.async_added_to_hass()
        assert restarted.native_value == pytest.approx(5.8518)
        assert restarted.last_reset == START

        _feed(restarted, START + timedelta(minutes=5), 58600, 86584, 30070)
        assert restarted.native_value == pytest.approx(5.86)
        assert restarted.last_reset == START
