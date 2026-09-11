"""Live temperature and humidity for pump-model dehumidifiers (H7152 "Max")
— issue #114 follow-up.

The H7152 has no ``sensorTemperature``/``sensorHumidity`` capability at all
(confirmed: absent from the discovered capabilities list even though the app
shows live readings for both) — they travel over the AWS IoT status push's
BLE-format ``op.command`` frames instead. Bytes 3-5 of the ``aa 10 81``
frame are a single big-endian 3-byte packed value: temperature and humidity
each x10 and concatenated (``temp_decidegrees * 1000 + humidity_decipercent``).

The frames below are taken verbatim from real (app-screenshot + diagnostics)
capture pairs, confirmed against the app's own displayed temperature and
humidity with zero residual error. All of them happen to fall in the
~19.7-26.2 degC band where byte 3 of the packed value reads ``0x03`` — see
``test_decodes_outside_the_captured_temperature_band`` for why the frame
match must not key on that byte.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from custom_components.govee.coordinator import GoveeCoordinator
from custom_components.govee.models import (
    GoveeCapability,
    GoveeDevice,
    GoveeDeviceState,
)
from custom_components.govee.models.device import CAPABILITY_ON_OFF, INSTANCE_POWER
from custom_components.govee.transport_health import TransportHealthTracker

DEVICE_ID = "11:66:C0:EB:1D:75:5C:E7"
LAMP_ID = "AA:BB:CC:DD:EE:FF:60:54"

# An unrelated frame from the same push, to prove the scan doesn't false-match.
FRAME_UNRELATED = bytes.fromhex("aa050003000000000000000000000000000000ac")

# Verbatim ``aa 10 81`` frames from 5 real (app-screenshot + diagnostics)
# capture pairs. Named by their app-displayed temperature. Humidity values
# are the same packed-value formula applied to the same verbatim frames.
FRAME_TEMP_69_6F = bytes.fromhex("aa10810332ce00000000000000000000000000c4")
FRAME_TEMP_70_7F = bytes.fromhex("aa1081034a370000000000000000000000000045")
FRAME_TEMP_71_1F = bytes.fromhex("aa10810355ff0000000000000000000000000092")
FRAME_TEMP_72_1F = bytes.fromhex("aa10810369c50000000000000000000000000094")
FRAME_TEMP_72_3F = bytes.fromhex("aa1081036db000000000000000000000000000e5")

# Synthetic frames outside the captured 19.7-26.2 degC band, where the
# packed value's high byte (frame[3]) is NOT 0x03 — 15.0 degC/45.0% packs to
# high byte 0x02, 30.0 degC/55.0% to 0x04. Built from the same confirmed
# formula, not captured; they exist to prove the frame match keys on the
# 3-byte ``aa 10 81`` prefix alone and not on frame[3]'s incidental value.
FRAME_TEMP_15_0C_BELOW_BAND = bytes.fromhex("aa1081024bb200000000000000000000000000")
FRAME_TEMP_30_0C_ABOVE_BAND = bytes.fromhex("aa108104960600000000000000000000000000")


def _h7152() -> GoveeDevice:
    return GoveeDevice(
        device_id=DEVICE_ID,
        sku="H7152",
        name="Smart Dehumidifier Max",
        device_type="devices.types.dehumidifier",
        capabilities=(
            GoveeCapability(
                type=CAPABILITY_ON_OFF, instance=INSTANCE_POWER, parameters={}
            ),
        ),
    )


def _h7150() -> GoveeDevice:
    """Non-pump variant — must NOT get the frame-based readings."""
    return GoveeDevice(
        device_id="11:66:C0:EB:1D:75:5C:E8",
        sku="H7150",
        name="Smart Dehumidifier",
        device_type="devices.types.dehumidifier",
        capabilities=(
            GoveeCapability(
                type=CAPABILITY_ON_OFF, instance=INSTANCE_POWER, parameters={}
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
            GoveeCapability(
                type=CAPABILITY_ON_OFF, instance=INSTANCE_POWER, parameters={}
            ),
        ),
    )


class TestSupportsTemperatureSensorOnPumpDehumidifier:
    def test_h7152_supports_temperature_sensor(self):
        assert _h7152().supports_temperature_sensor is True

    def test_h7150_does_not_support_temperature_sensor(self):
        """No sensorTemperature capability and not confirmed on H7150 frames."""
        assert _h7150().supports_temperature_sensor is False


class TestSupportsHumiditySensorOnPumpDehumidifier:
    def test_h7152_supports_humidity_sensor(self):
        assert _h7152().supports_humidity_sensor is True

    def test_h7150_does_not_support_humidity_sensor(self):
        """No sensorHumidity capability and not confirmed on H7150 frames."""
        assert _h7150().supports_humidity_sensor is False


class TestUpdateTemperatureFromFrames:
    def test_recognises_the_frame(self):
        state = GoveeDeviceState.create_empty(DEVICE_ID)
        assert state.update_temperature_from_frames([FRAME_TEMP_70_7F]) is True

    def test_unrelated_frame_is_not_recognised(self):
        state = GoveeDeviceState.create_empty(DEVICE_ID)
        assert state.update_temperature_from_frames([FRAME_UNRELATED]) is False
        assert state.sensor_temperature is None
        assert state.sensor_humidity is None

    def test_short_frame_is_ignored(self):
        state = GoveeDeviceState.create_empty(DEVICE_ID)
        short = bytes(
            [0xAA, 0x10, 0x81, 0x03, 0x32]
        )  # one byte short of the packed value
        assert state.update_temperature_from_frames([short]) is False
        assert state.sensor_temperature is None

    @pytest.mark.parametrize(
        "frame,expected_celsius,expected_humidity",
        [
            (FRAME_TEMP_69_6F, 20.9, 61.4),
            (FRAME_TEMP_70_7F, 21.5, 60.7),
            (FRAME_TEMP_71_1F, 21.8, 62.3),
            (FRAME_TEMP_72_1F, 22.3, 68.5),
            (FRAME_TEMP_72_3F, 22.4, 68.8),
        ],
    )
    def test_decodes_the_exact_value(self, frame, expected_celsius, expected_humidity):
        """Bytes 3-5 are a single big-endian packed value — an exact decode
        confirmed against the app's own displayed temperature with zero
        residual error, not a fit. Humidity is the same formula applied to
        the same verbatim frame (see the method's docstring)."""
        state = GoveeDeviceState.create_empty(DEVICE_ID)
        state.update_temperature_from_frames([frame])
        assert state.sensor_temperature == expected_celsius
        assert state.sensor_humidity == expected_humidity

    @pytest.mark.parametrize(
        "frame,expected_celsius,expected_humidity",
        [
            (FRAME_TEMP_15_0C_BELOW_BAND, 15.0, 45.0),
            (FRAME_TEMP_30_0C_ABOVE_BAND, 30.0, 55.0),
        ],
    )
    def test_decodes_outside_the_captured_temperature_band(
        self, frame, expected_celsius, expected_humidity
    ):
        """Regression test: the frame match must key on the 3-byte
        ``aa 10 81`` prefix only. Every captured real frame happens to fall
        in a band where the packed value's high byte (frame[3]) reads
        0x03 — matching on that byte too would silently stop recognising
        the frame (and freeze the reading) outside ~19.7-26.2 degC.
        """
        state = GoveeDeviceState.create_empty(DEVICE_ID)
        assert state.update_temperature_from_frames([frame]) is True
        assert state.sensor_temperature == expected_celsius
        assert state.sensor_humidity == expected_humidity

    def test_picks_the_right_frame_out_of_a_full_push(self):
        state = GoveeDeviceState.create_empty(DEVICE_ID)
        frames = [FRAME_UNRELATED, FRAME_TEMP_72_3F]
        assert state.update_temperature_from_frames(frames) is True
        assert state.sensor_temperature == 22.4
        assert state.sensor_humidity == 68.8


class TestCoordinatorAppliesTemperatureFromAnMqttPush:
    """_on_mqtt_state_update must actually call the decoder for an H7152
    push — the decoder itself is covered above, this exercises the wiring
    in the coordinator that calls it.
    """

    def _coordinator(self) -> GoveeCoordinator:
        coordinator = GoveeCoordinator.__new__(GoveeCoordinator)
        coordinator._devices = {DEVICE_ID: _h7152(), LAMP_ID: _lamp()}
        coordinator._states = {
            DEVICE_ID: GoveeDeviceState.create_empty(DEVICE_ID),
            LAMP_ID: GoveeDeviceState.create_empty(LAMP_ID),
        }
        coordinator._transport = TransportHealthTracker()
        coordinator.async_set_updated_data = MagicMock()
        return coordinator

    def test_temperature_and_humidity_applied_from_push(self):
        coordinator = self._coordinator()

        coordinator._on_mqtt_state_update(
            DEVICE_ID,
            {"onOff": 1, "_op_frames": [FRAME_TEMP_72_3F.hex()]},
        )

        state = coordinator._states[DEVICE_ID]
        assert state.sensor_temperature == 22.4
        assert state.sensor_humidity == 68.8

    def test_non_pump_device_is_left_untouched(self):
        coordinator = self._coordinator()

        coordinator._on_mqtt_state_update(
            LAMP_ID,
            {"onOff": 1, "_op_frames": [FRAME_TEMP_72_3F.hex()]},
        )

        state = coordinator._states[LAMP_ID]
        assert state.sensor_temperature is None
        assert state.sensor_humidity is None
