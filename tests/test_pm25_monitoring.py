"""Live PM2.5 (and a fresher temperature/humidity pair) for the H5106 AQI
monitor — issue #200.

Reverse-engineered from homebridge-govee's ``sensor-monitor.js``, which
identifies the useful frame among an AWS IoT push's ``op.command`` list by
ruling out a fixed set of boilerplate/settings prefixes rather than matching
one in — a weaker identification than every other frame this integration
decodes, which is why a physical-plausibility check on the decoded
temperature and humidity guards the result.

Confirmed against the Govee app on two independent H5106 units, all three
fields matching on each. The frames below are taken verbatim from real
diagnostics downloads.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.govee.coordinator import GoveeCoordinator
from custom_components.govee.models import GoveeCapability, GoveeDevice, GoveeDeviceState
from custom_components.govee.models.device import CAPABILITY_PROPERTY, INSTANCE_SENSOR_TEMPERATURE
from custom_components.govee.transport_health import TransportHealthTracker

DEVICE_ID = "11:66:C0:EB:1D:75:5C:F3"

# Unit 1: 19.00 degC / 54.2% / 1 ug/m3 — matched the app.
FRAME_UNIT_1 = bytes.fromhex("076c0000011770f830152c000001271000000001")
# Unit 2: 25.10 degC / 54.8% / 0 ug/m3 — matched the app, independently.
FRAME_UNIT_2 = bytes.fromhex("09ce0000001388fc18156800000026ac00000000")

# Verbatim boilerplate frames from the same push as FRAME_UNIT_1 — both start
# with an ignored prefix ("0103", "0100") and must be skipped.
FRAME_IGNORED_0103 = bytes.fromhex("0103e80000000101010700123b05011300063b01")
FRAME_IGNORED_0100 = bytes.fromhex("0100000000000000000000000000000000000000")

# A frame that is NOT on the ignore list but decodes to a physically
# impossible temperature (200 degC) — the plausibility check must still
# reject it.
FRAME_IMPLAUSIBLE = bytes.fromhex("4e20000000000000000000000000000000000000")


def _h5106() -> GoveeDevice:
    return GoveeDevice(
        device_id=DEVICE_ID,
        sku="H5106",
        name="Living Room Air Quality Monitor",
        device_type="devices.types.sensor",
        capabilities=(GoveeCapability(type=CAPABILITY_PROPERTY, instance=INSTANCE_SENSOR_TEMPERATURE, parameters={}),),
    )


def _other_thermometer() -> GoveeDevice:
    """A plain thermometer — must NOT be treated as a PM2.5 source."""
    return GoveeDevice(
        device_id="11:66:C0:EB:1D:75:5C:F4",
        sku="H5179",
        name="Backyard Sensor",
        device_type="devices.types.thermometer",
        capabilities=(GoveeCapability(type=CAPABILITY_PROPERTY, instance=INSTANCE_SENSOR_TEMPERATURE, parameters={}),),
    )


class TestSupportsPm25Frame:
    def test_h5106_supports_pm25_frame(self):
        assert _h5106().supports_pm25_frame is True

    def test_other_thermometer_does_not_support_pm25_frame(self):
        assert _other_thermometer().supports_pm25_frame is False


class TestUpdatePm25FromFrames:
    def test_unit_1_matches_the_app(self):
        state = GoveeDeviceState.create_empty(DEVICE_ID)
        assert state.update_pm25_from_frames([FRAME_UNIT_1]) is True
        assert state.sensor_temperature == pytest.approx(19.0)
        assert state.sensor_humidity == pytest.approx(54.2)
        assert state.pm25 == 1

    def test_unit_2_matches_the_app(self):
        """A second, independent unit — proves the offsets, not one lucky frame."""
        state = GoveeDeviceState.create_empty(DEVICE_ID)
        assert state.update_pm25_from_frames([FRAME_UNIT_2]) is True
        assert state.sensor_temperature == pytest.approx(25.1)
        assert state.sensor_humidity == pytest.approx(54.8)
        assert state.pm25 == 0

    def test_ignored_prefixes_are_skipped(self):
        """Boilerplate frames from the same push must not be misread."""
        state = GoveeDeviceState.create_empty(DEVICE_ID)
        assert state.update_pm25_from_frames([FRAME_IGNORED_0103, FRAME_IGNORED_0100]) is False
        assert state.sensor_temperature is None
        assert state.pm25 is None

    def test_picks_the_right_frame_out_of_a_full_push(self):
        state = GoveeDeviceState.create_empty(DEVICE_ID)
        frames = [FRAME_IGNORED_0103, FRAME_IGNORED_0100, FRAME_UNIT_1]
        assert state.update_pm25_from_frames(frames) is True
        assert state.pm25 == 1

    def test_implausible_temperature_is_rejected(self):
        """Weaker identification than the rest of this module — a frame that
        slips past the ignore list but decodes to a physically impossible
        reading must still be rejected rather than stored.
        """
        state = GoveeDeviceState.create_empty(DEVICE_ID)
        assert state.update_pm25_from_frames([FRAME_IMPLAUSIBLE]) is False
        assert state.sensor_temperature is None

    def test_wrong_length_frame_is_ignored(self):
        state = GoveeDeviceState.create_empty(DEVICE_ID)
        assert state.update_pm25_from_frames([bytes(19)]) is False
        assert state.sensor_temperature is None


def _coordinator_for_mqtt_push(device: GoveeDevice) -> GoveeCoordinator:
    """A minimal coordinator, bypassing __init__, for driving
    _on_mqtt_state_update directly — mirrors test_power_monitoring.py.
    """
    coordinator = GoveeCoordinator.__new__(GoveeCoordinator)
    coordinator._devices = {device.device_id: device}
    coordinator._states = {device.device_id: GoveeDeviceState.create_empty(device.device_id)}
    coordinator._transport = TransportHealthTracker()
    coordinator.async_set_updated_data = MagicMock()
    return coordinator


class TestCoordinatorAppliesPm25FromAnMqttPush:
    def test_h5106_gets_its_readings_applied(self):
        coordinator = _coordinator_for_mqtt_push(_h5106())

        coordinator._on_mqtt_state_update(
            DEVICE_ID, {"onOff": 1, "_op_frames": [FRAME_IGNORED_0103.hex(), FRAME_UNIT_1.hex()]}
        )

        state = coordinator._states[DEVICE_ID]
        assert state.pm25 == 1
        assert state.sensor_temperature == pytest.approx(19.0)
        coordinator.async_set_updated_data.assert_called_once()

    def test_other_thermometer_is_left_untouched(self):
        """A plain thermometer must never reach the PM2.5 decoder, even if it
        somehow carried a matching frame shape."""
        other = _other_thermometer()
        coordinator = _coordinator_for_mqtt_push(other)

        coordinator._on_mqtt_state_update(other.device_id, {"onOff": 1, "_op_frames": [FRAME_UNIT_1.hex()]})

        assert coordinator._states[other.device_id].pm25 is None
        assert coordinator._states[other.device_id].sensor_temperature is None


class TestPreservedAcrossDeveloperPoll:
    """The Developer /device/state poll has no PM2.5 field at all — it only
    ever comes from AWS IoT push frames — so a naive poll would flicker the
    sensor to "unknown" every ~60s (same bug class as pump_state).
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
        coord._devices[DEVICE_ID] = _h5106()
        return coord

    @pytest.mark.asyncio
    async def test_pm25_survives_a_poll_that_knows_nothing_about_it(self):
        coord = self._coord()
        existing = GoveeDeviceState.create_empty(DEVICE_ID)
        existing.pm25 = 1
        coord._states[DEVICE_ID] = existing

        fresh = GoveeDeviceState.create_empty(DEVICE_ID)
        coord._api_client.get_device_state = AsyncMock(return_value=fresh)

        result = await coord._fetch_device_state(DEVICE_ID, coord._devices[DEVICE_ID])

        assert result.pm25 == 1

    @pytest.mark.asyncio
    async def test_a_fresh_push_value_is_not_masked_by_a_stale_preserved_one(self):
        coord = self._coord()
        existing = GoveeDeviceState.create_empty(DEVICE_ID)
        existing.pm25 = 1
        coord._states[DEVICE_ID] = existing

        fresh = GoveeDeviceState.create_empty(DEVICE_ID)
        fresh.pm25 = 0  # e.g. a push landed between poll start and finish
        coord._api_client.get_device_state = AsyncMock(return_value=fresh)

        result = await coord._fetch_device_state(DEVICE_ID, coord._devices[DEVICE_ID])

        assert result.pm25 == 0


class TestPm25Sensor:
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
        coord._devices[DEVICE_ID] = _h5106()
        return coord

    def test_native_value_reflects_state(self):
        from custom_components.govee.sensor import GoveePm25Sensor

        coord = self._coord()
        state = GoveeDeviceState.create_empty(DEVICE_ID)
        state.pm25 = 1
        coord._states[DEVICE_ID] = state

        sensor = GoveePm25Sensor(coord, coord._devices[DEVICE_ID])
        assert sensor.native_value == 1

    def test_native_value_none_before_any_push(self):
        from custom_components.govee.sensor import GoveePm25Sensor

        coord = self._coord()
        sensor = GoveePm25Sensor(coord, coord._devices[DEVICE_ID])
        assert sensor.native_value is None

    def test_unit_comes_from_the_shim_not_the_deprecated_constant(self):
        """Issue #210: CONCENTRATION_* is removed in Core 2027.8; the sensor uses the shimmed name."""
        from custom_components.govee.sensor import MICROGRAMS_PER_CUBIC_METER, GoveePm25Sensor

        coord = self._coord()
        sensor = GoveePm25Sensor(coord, coord._devices[DEVICE_ID])
        assert sensor.native_unit_of_measurement == MICROGRAMS_PER_CUBIC_METER


class TestSensorSetupRegistersPm25:
    """async_setup_entry must actually create the PM2.5 entity for an H5106
    — the entity class itself is covered above, this exercises the
    conditional registration in the platform's setup function."""

    @pytest.mark.asyncio
    async def test_h5106_gets_a_pm25_entity(self):
        from custom_components.govee.sensor import GoveePm25Sensor, async_setup_entry

        device = _h5106()
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

        assert sum(1 for e in added if isinstance(e, GoveePm25Sensor)) == 1

    @pytest.mark.asyncio
    async def test_other_thermometer_gets_no_pm25_entity(self):
        from custom_components.govee.sensor import GoveePm25Sensor, async_setup_entry

        device = _other_thermometer()
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

        assert not any(isinstance(e, GoveePm25Sensor) for e in added)
