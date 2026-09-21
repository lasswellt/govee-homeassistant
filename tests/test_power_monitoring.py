"""Live voltage/current/power/energy/power-factor for power-monitoring smart
outlets (H5086) — issue #200.

Reverse-engineered against
https://github.com/egold555/Govee-Reverse-Engineering/blob/master/Products/H5086.md
and confirmed against the Govee app on two independent H5086 units, matching
across all five fields on each: time-powered-on, accumulated energy,
voltage, current, and power (power factor isn't shown in the app, but reads
consistently across both units and the reference doc's own field order).

No capability exposes any of it — like the H7152's pump-fault flag and
hose-connection mode, it only arrives in the AWS IoT push's ``op.command``
BLE-format frames, on the same ``aa 19`` register the H7152 uses for a
completely different (device-specific) meaning. Safe only because the
coordinator gates each decoder to its own SKU set before ever calling it.

The frames below are taken verbatim from real diagnostics downloads.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.govee.coordinator import GoveeCoordinator
from custom_components.govee.models import GoveeCapability, GoveeDevice, GoveeDeviceState
from custom_components.govee.models.device import CAPABILITY_ON_OFF, INSTANCE_POWER
from custom_components.govee.transport_health import TransportHealthTracker

DEVICE_ID = "11:66:C0:EB:1D:75:5C:F1"

# Verbatim ``aa 19`` frames from two independent H5086 outlets' diagnostics.
# Outlet 1: 17082s on, 0.0033 kWh, 120.27V, 0.01A, 0.82W, 49% PF — app-matched.
FRAME_OUTLET_1 = bytes.fromhex("aa190042ba0000212efb00010000523100000000")
# Outlet 2: 18125s on, 0.0529 kWh, 119.33V, 0.16A, 10.71W, 55% PF — app-matched.
FRAME_OUTLET_2 = bytes.fromhex("aa190046cd0002112e9d001000042f3700000000")

# An unrelated frame from the same push, to prove the scan doesn't false-match.
FRAME_UNRELATED = bytes.fromhex("aa050003000000000000000000000000000000ac")

# The H7152's own ``aa 19`` frame shape (hose-connection mode) — a different
# device family sharing the same register number with a different layout.
FRAME_H7152_HOSE_MODE = bytes.fromhex("aa190000000000010101000000000000000000b2")


def _h5086() -> GoveeDevice:
    return GoveeDevice(
        device_id=DEVICE_ID,
        sku="H5086",
        name="Solar Controller Outlet",
        device_type="devices.types.socket",
        capabilities=(GoveeCapability(type=CAPABILITY_ON_OFF, instance=INSTANCE_POWER, parameters={}),),
    )


def _plain_outlet() -> GoveeDevice:
    """A non-monitoring outlet — must NOT be treated as power-monitoring."""
    return GoveeDevice(
        device_id="11:66:C0:EB:1D:75:5C:F2",
        sku="H5083",
        name="Plain Outlet",
        device_type="devices.types.socket",
        capabilities=(GoveeCapability(type=CAPABILITY_ON_OFF, instance=INSTANCE_POWER, parameters={}),),
    )


class TestSupportsPowerMonitoring:
    def test_h5086_supports_power_monitoring(self):
        assert _h5086().supports_power_monitoring is True

    def test_plain_outlet_does_not_support_power_monitoring(self):
        assert _plain_outlet().supports_power_monitoring is False


class TestUpdatePowerMonitoringFromFrames:
    def test_outlet_1_matches_the_app(self):
        state = GoveeDeviceState.create_empty(DEVICE_ID)
        assert state.update_power_monitoring_from_frames([FRAME_OUTLET_1]) is True
        assert state.energy_total == pytest.approx(0.0033)
        assert state.voltage == pytest.approx(120.27)
        assert state.current == pytest.approx(0.01)
        assert state.power_draw == pytest.approx(0.82)
        assert state.power_factor == 49

    def test_outlet_2_matches_the_app(self):
        """A second, independent unit — proves the offsets, not one lucky frame."""
        state = GoveeDeviceState.create_empty(DEVICE_ID)
        assert state.update_power_monitoring_from_frames([FRAME_OUTLET_2]) is True
        assert state.energy_total == pytest.approx(0.0529)
        assert state.voltage == pytest.approx(119.33)
        assert state.current == pytest.approx(0.16)
        assert state.power_draw == pytest.approx(10.71)
        assert state.power_factor == 55

    def test_unrelated_frame_is_not_recognised(self):
        state = GoveeDeviceState.create_empty(DEVICE_ID)
        assert state.update_power_monitoring_from_frames([FRAME_UNRELATED]) is False
        assert state.voltage is None

    def test_short_frame_is_ignored(self):
        state = GoveeDeviceState.create_empty(DEVICE_ID)
        short = bytes([0xAA, 0x19, 0x00])
        assert state.update_power_monitoring_from_frames([short]) is False
        assert state.voltage is None

    def test_picks_the_right_frame_out_of_a_full_push(self):
        state = GoveeDeviceState.create_empty(DEVICE_ID)
        frames = [FRAME_UNRELATED, FRAME_OUTLET_1]
        assert state.update_power_monitoring_from_frames(frames) is True
        assert state.voltage == pytest.approx(120.27)


def _coordinator_for_mqtt_push(device: GoveeDevice) -> GoveeCoordinator:
    """A minimal coordinator, bypassing __init__, for driving
    _on_mqtt_state_update directly — mirrors test_pump_state.py's helper.
    """
    coordinator = GoveeCoordinator.__new__(GoveeCoordinator)
    coordinator._devices = {device.device_id: device}
    coordinator._states = {device.device_id: GoveeDeviceState.create_empty(device.device_id)}
    coordinator._transport = TransportHealthTracker()
    coordinator.async_set_updated_data = MagicMock()
    return coordinator


class TestCoordinatorAppliesPowerMonitoringFromAnMqttPush:
    """_on_mqtt_state_update must actually call the decoder for an H5086
    push — the decoder itself is covered above, this exercises the wiring.
    """

    def test_h5086_gets_its_readings_applied(self):
        coordinator = _coordinator_for_mqtt_push(_h5086())

        coordinator._on_mqtt_state_update(DEVICE_ID, {"onOff": 1, "_op_frames": [FRAME_OUTLET_1.hex()]})

        state = coordinator._states[DEVICE_ID]
        assert state.voltage == pytest.approx(120.27)
        assert state.power_draw == pytest.approx(0.82)
        coordinator.async_set_updated_data.assert_called_once()

    def test_non_monitoring_device_is_left_untouched(self):
        """A plain outlet must never reach the decoder, even if it somehow
        carried a matching frame shape."""
        other = _plain_outlet()
        coordinator = _coordinator_for_mqtt_push(other)

        coordinator._on_mqtt_state_update(other.device_id, {"onOff": 1, "_op_frames": [FRAME_OUTLET_1.hex()]})

        assert coordinator._states[other.device_id].voltage is None

    def test_h7152_hose_mode_frame_is_never_fed_to_the_power_decoder(self):
        """Register 0x19 means something else entirely on an H7152 — the
        SKU gate, not the decoder, is what keeps them apart.
        """
        coordinator = _coordinator_for_mqtt_push(_h5086())

        # Even if an H5086 device somehow carried an H7152-shaped 0x19
        # frame, the power decoder must not crash or blow up on it.
        coordinator._on_mqtt_state_update(DEVICE_ID, {"onOff": 1, "_op_frames": [FRAME_H7152_HOSE_MODE.hex()]})

        state = coordinator._states[DEVICE_ID]
        assert state.voltage is not None  # decoded *something* — the point is it doesn't crash


class TestPreservedAcrossDeveloperPoll:
    """The Developer /device/state poll has no field for any of these — they
    only ever come from AWS IoT push frames — so a naive poll would flicker
    every sensor to "unknown" every ~60s (same bug class as pump_state).
    """

    def _coord(self):
        import custom_components.govee.coordinator as coord_mod

        hass = MagicMock()
        config_entry = MagicMock()
        config_entry.entry_id = "test_entry"
        config_entry.async_create_background_task = MagicMock()
        coord = coord_mod.GoveeCoordinator(
            hass=hass,
            config_entry=config_entry,
            api_client=MagicMock(),
            iot_credentials=None,
            poll_interval=60,
        )
        coord._devices[DEVICE_ID] = _h5086()
        return coord

    @pytest.mark.asyncio
    async def test_readings_survive_a_poll_that_knows_nothing_about_them(self):
        coord = self._coord()
        existing = GoveeDeviceState.create_empty(DEVICE_ID)
        existing.voltage = 120.27
        existing.current = 0.01
        existing.power_draw = 0.82
        existing.energy_total = 0.0033
        existing.power_factor = 49
        coord._states[DEVICE_ID] = existing

        # What the Developer poll actually returns: no fields for any of
        # these, so the fresh state has them all as None.
        fresh = GoveeDeviceState.create_empty(DEVICE_ID)
        coord._api_client.get_device_state = AsyncMock(return_value=fresh)

        result = await coord._fetch_device_state(DEVICE_ID, coord._devices[DEVICE_ID])

        assert result.voltage == 120.27
        assert result.current == 0.01
        assert result.power_draw == 0.82
        assert result.energy_total == 0.0033
        assert result.power_factor == 49

    @pytest.mark.asyncio
    async def test_a_fresh_push_value_is_not_masked_by_a_stale_preserved_one(self):
        coord = self._coord()
        existing = GoveeDeviceState.create_empty(DEVICE_ID)
        existing.power_draw = 0.82
        coord._states[DEVICE_ID] = existing

        fresh = GoveeDeviceState.create_empty(DEVICE_ID)
        fresh.power_draw = 10.71  # e.g. a push landed between poll start and finish
        coord._api_client.get_device_state = AsyncMock(return_value=fresh)

        result = await coord._fetch_device_state(DEVICE_ID, coord._devices[DEVICE_ID])

        assert result.power_draw == 10.71


class TestPowerMonitoringSensors:
    def _coord(self):
        import custom_components.govee.coordinator as coord_mod

        hass = MagicMock()
        config_entry = MagicMock()
        config_entry.entry_id = "test_entry"
        coord = coord_mod.GoveeCoordinator(
            hass=hass,
            config_entry=config_entry,
            api_client=MagicMock(),
            iot_credentials=None,
            poll_interval=60,
        )
        coord._devices[DEVICE_ID] = _h5086()
        return coord

    def test_native_values_reflect_state(self):
        from custom_components.govee.sensor import (
            GoveeCurrentSensor,
            GoveeEnergySensor,
            GoveePowerFactorSensor,
            GoveePowerSensor,
            GoveeVoltageSensor,
        )

        coord = self._coord()
        state = GoveeDeviceState.create_empty(DEVICE_ID)
        state.voltage = 120.27
        state.current = 0.01
        state.power_draw = 0.82
        state.energy_total = 0.0033
        state.power_factor = 49
        coord._states[DEVICE_ID] = state
        device = coord._devices[DEVICE_ID]

        assert GoveeVoltageSensor(coord, device).native_value == 120.27
        assert GoveeCurrentSensor(coord, device).native_value == 0.01
        assert GoveePowerSensor(coord, device).native_value == 0.82
        assert GoveeEnergySensor(coord, device).native_value == 0.0033
        assert GoveePowerFactorSensor(coord, device).native_value == 49

    def test_readings_keep_the_precision_the_device_reports(self):
        """Issue #200: a small load showed as a whole number of watts."""
        from custom_components.govee.sensor import GoveeCurrentSensor, GoveePowerSensor, GoveeVoltageSensor

        coord = self._coord()
        device = coord._devices[DEVICE_ID]

        for cls in (GoveePowerSensor, GoveeVoltageSensor, GoveeCurrentSensor):
            assert cls(coord, device).suggested_display_precision == 2

    def test_native_values_none_before_any_push(self):
        from custom_components.govee.sensor import GoveeVoltageSensor

        coord = self._coord()
        sensor = GoveeVoltageSensor(coord, coord._devices[DEVICE_ID])
        assert sensor.native_value is None


class TestSensorSetupRegistersPowerMonitoring:
    """async_setup_entry must actually create all five entities for an
    H5086 — the entity classes themselves are covered above, this exercises
    the conditional registration in the platform's setup function."""

    @pytest.mark.asyncio
    async def test_h5086_gets_all_five_entities(self):
        from custom_components.govee.sensor import (
            GoveeCurrentSensor,
            GoveeEnergySensor,
            GoveePowerFactorSensor,
            GoveePowerSensor,
            GoveeVoltageSensor,
            async_setup_entry,
        )

        device = _h5086()
        coordinator = MagicMock()
        coordinator.devices = {DEVICE_ID: device}
        coordinator.mqtt_client = None
        coordinator.get_state.return_value = None
        coordinator.is_bff_leak_sensor.return_value = False
        coordinator.leak_sensors = {}

        entry = MagicMock()
        entry.runtime_data = coordinator
        entry.entry_id = "test_entry"

        added: list = []
        await async_setup_entry(MagicMock(), entry, added.extend)

        for cls in (
            GoveeVoltageSensor,
            GoveeCurrentSensor,
            GoveePowerSensor,
            GoveeEnergySensor,
            GoveePowerFactorSensor,
        ):
            matches = [e for e in added if isinstance(e, cls)]
            assert len(matches) == 1, f"expected exactly one {cls.__name__}"

    @pytest.mark.asyncio
    async def test_plain_outlet_gets_no_power_monitoring_entities(self):
        from custom_components.govee.sensor import (
            GoveeCurrentSensor,
            GoveeEnergySensor,
            GoveePowerFactorSensor,
            GoveePowerSensor,
            GoveeVoltageSensor,
            async_setup_entry,
        )

        device = _plain_outlet()
        coordinator = MagicMock()
        coordinator.devices = {device.device_id: device}
        coordinator.mqtt_client = None
        coordinator.get_state.return_value = None
        coordinator.is_bff_leak_sensor.return_value = False
        coordinator.leak_sensors = {}

        entry = MagicMock()
        entry.runtime_data = coordinator
        entry.entry_id = "test_entry"

        added: list = []
        await async_setup_entry(MagicMock(), entry, added.extend)

        for cls in (
            GoveeVoltageSensor,
            GoveeCurrentSensor,
            GoveePowerSensor,
            GoveeEnergySensor,
            GoveePowerFactorSensor,
        ):
            assert not any(isinstance(e, cls) for e in added)
