"""H7175 kettle: the temperature no longer goes stale.

The kettle pushes its temperatures under ``sta`` and as BLE-format frames.
Neither was decoded, yet the push marked the kettle locally fresh, so cloud
polls were skipped and the temperature stopped moving. Only the H7175 is
decoded; other kettles are handled as before.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.govee.coordinator import GoveeCoordinator
from custom_components.govee.kettle.frames import checksum_ok, decode_kettle_frames
from custom_components.govee.kettle.manager import KettleManager
from custom_components.govee.models import GoveeDevice, GoveeDeviceState
from custom_components.govee.sensor import GoveeTemperatureSensor

from .kettle_samples import DEVICE_ID, H7175_DEVICE, H7175_MQTT, H7175_STATE, mqtt_frames


def _frame(*head: int) -> bytes:
    body = list(head) + [0] * (19 - len(head))
    checksum = 0
    for byte in body:
        checksum ^= byte
    return bytes(body + [checksum])


def _device(sku: str = "H7175") -> GoveeDevice:
    return GoveeDevice.from_api_response({**H7175_DEVICE, "sku": sku})


def _polled() -> GoveeDeviceState:
    state = GoveeDeviceState.create_empty(DEVICE_ID)
    state.update_from_api(H7175_STATE)
    return state


def _coordinator(device: GoveeDevice | None = None, options: dict | None = None) -> GoveeCoordinator:
    entry = MagicMock(entry_id="entry", options=options or {})
    coordinator = GoveeCoordinator(
        hass=MagicMock(), config_entry=entry, api_client=MagicMock(), iot_credentials=None, poll_interval=60
    )
    coordinator._devices[DEVICE_ID] = device or _device()
    coordinator.async_set_updated_data = MagicMock()
    return coordinator


# --------------------------------------------------------------------------- #
# Frames
# --------------------------------------------------------------------------- #


class TestFrames:
    def test_every_captured_frame_passes_its_checksum(self):
        assert all(checksum_ok(frame) for frame in mqtt_frames())

    def test_a_flipped_byte_or_a_short_frame_fails(self):
        frame = bytearray(mqtt_frames()[9])
        frame[4] ^= 0x01
        assert checksum_ok(bytes(frame)) is False
        assert checksum_ok(b"\xaa\x10") is False

    def test_current_temperature_from_aa10(self):
        """0x2328 = 90.00 °F, as the push's curTem."""
        assert decode_kettle_frames(mqtt_frames()).current_temperature == 90.0

    def test_bad_checksum_echo_and_other_frames_are_ignored(self):
        bad = bytearray(_frame(0xAA, 0x10, 0x01, 0x23, 0x28))
        bad[-1] ^= 0xFF
        frames = [bytes(bad), _frame(0x3A, 0x10, 0x01, 0x23, 0x28), _frame(0xAA, 0x10, 0x81, 0x03, 0x39)]
        assert decode_kettle_frames(frames).current_temperature is None


class TestModel:
    @pytest.mark.parametrize(
        ("sku", "decoded"), [("H7175", True), ("h7175", True), ("H717A", False), ("H7170", False)]
    )
    def test_only_the_h7175_is_decoded(self, sku, decoded):
        assert _device(sku).decodes_kettle_frames is decoded

    def test_needs_a_kettle(self):
        light = GoveeDevice.from_api_response({**H7175_DEVICE, "type": "devices.types.light"})
        assert light.decodes_kettle_frames is False

    def test_poll_target(self):
        state = _polled()
        assert (state.kettle_target_temperature, state.device_temperature_unit) == (176.0, "Fahrenheit")

    @pytest.mark.parametrize(
        ("value", "target"),
        [({"unit": "Celsius", "temperature": 80}, 80.0), ({"unit": "Celsius"}, None), ("", None)],
    )
    def test_target_shapes(self, value, target):
        state = GoveeDeviceState.create_empty(DEVICE_ID)
        cap = {"type": "devices.capabilities.temperature_setting", "instance": "sliderTemperature"}
        state.update_from_api({"capabilities": [{**cap, "state": {"value": value}}]})
        assert state.kettle_target_temperature == target


# --------------------------------------------------------------------------- #
# Units
# --------------------------------------------------------------------------- #


class TestUnits:
    def _manager(self, *, declared: str | None, api_unit: str = "auto", account: str | None = None, sku="H7175"):
        state = GoveeDeviceState.create_empty(DEVICE_ID)
        state.device_temperature_unit = declared
        coordinator = SimpleNamespace(
            config_entry=SimpleNamespace(options={"api_temperature_unit": api_unit}),
            devices={DEVICE_ID: _device(sku)},
            get_state=lambda _id: state,
            account_temperature_unit=lambda _id: account,
        )
        return KettleManager(coordinator)  # type: ignore[arg-type]

    @pytest.mark.parametrize("api_unit", ["auto", "celsius", "fahrenheit"])
    def test_the_declared_unit_wins_over_the_api_unit_option(self, api_unit):
        manager = self._manager(declared="Fahrenheit", api_unit=api_unit)
        assert manager.reports_fahrenheit(DEVICE_ID) is True
        assert manager.unit_known(DEVICE_ID) is True
        assert self._manager(declared="Celsius", api_unit=api_unit).reports_fahrenheit(DEVICE_ID) is False

    @pytest.mark.parametrize(
        ("api_unit", "account", "fahrenheit", "known"),
        [
            ("auto", None, False, False),
            ("auto", "fahrenheit", True, True),
            ("celsius", None, False, True),
            ("fahrenheit", None, True, True),
        ],
    )
    def test_without_a_declared_unit(self, api_unit, account, fahrenheit, known):
        manager = self._manager(declared=None, api_unit=api_unit, account=account)
        assert manager.reports_fahrenheit(DEVICE_ID) is fahrenheit
        assert manager.unit_known(DEVICE_ID) is known
        assert manager.must_poll(DEVICE_ID) is not known

    def test_fahrenheit_sku_list_counts_as_known(self, monkeypatch):
        import custom_components.govee.kettle.manager as manager_mod

        monkeypatch.setattr(manager_mod, "FAHRENHEIT_REPORTING_SKUS", frozenset({"H7175"}))
        assert self._manager(declared=None).unit_known(DEVICE_ID) is True

    def test_no_state_device_or_entry(self):
        coordinator = SimpleNamespace(
            config_entry=None, devices={}, get_state=lambda _id: None, account_temperature_unit=lambda _id: None
        )
        manager = KettleManager(coordinator)  # type: ignore[arg-type]
        assert (manager.reports_fahrenheit("x"), manager.unit_known("x")) == (False, False)

    def test_temperature_sensor_uses_the_kettle_decision(self):
        """A kettle declaring °F is converted even with the API-unit option on Celsius."""
        state = _polled()
        coordinator = _coordinator(options={"api_temperature_unit": "celsius"})
        coordinator._states[DEVICE_ID] = state
        sensor = GoveeTemperatureSensor(coordinator, coordinator._devices[DEVICE_ID])
        assert round(sensor.native_value, 1) == 32.8  # 91 °F


# --------------------------------------------------------------------------- #
# Pushes and polls
# --------------------------------------------------------------------------- #


class TestPush:
    def test_push_moves_the_temperatures(self):
        coordinator = _coordinator()
        coordinator._states[DEVICE_ID] = _polled()
        coordinator._on_mqtt_state_update(DEVICE_ID, {**H7175_MQTT, "sta": {"setTem": 17500, "curTem": 12050}})
        state = coordinator._states[DEVICE_ID]
        assert (state.sensor_temperature, state.kettle_target_temperature) == (120.5, 175.0)
        coordinator.async_set_updated_data.assert_called_once()

    def test_aa10_when_sta_has_no_current_temperature(self):
        coordinator = _coordinator()
        coordinator._states[DEVICE_ID] = _polled()
        coordinator._on_mqtt_state_update(DEVICE_ID, {**H7175_MQTT, "sta": {"curTem": "?"}})
        assert coordinator._states[DEVICE_ID].sensor_temperature == 90.0

    def test_aa10_is_ignored_on_a_celsius_kettle(self):
        coordinator = _coordinator()
        state = _polled()
        state.device_temperature_unit = "Celsius"
        coordinator._states[DEVICE_ID] = state
        coordinator._on_mqtt_state_update(DEVICE_ID, {"_op_frames": H7175_MQTT["_op_frames"], "sta": None})
        assert state.sensor_temperature == 91.0

    def test_withheld_until_the_unit_is_known(self):
        """A push before the first poll must not store °F as °C."""
        coordinator = _coordinator()
        coordinator._on_mqtt_state_update(DEVICE_ID, dict(H7175_MQTT))
        state = coordinator._states[DEVICE_ID]
        assert (state.sensor_temperature, state.kettle_target_temperature) == (None, None)
        assert state.power_state is False  # onOff still applies

    def test_other_kettles_are_unchanged(self):
        coordinator = _coordinator(_device("H717A"))
        coordinator._states[DEVICE_ID] = _polled()
        coordinator._on_mqtt_state_update(DEVICE_ID, dict(H7175_MQTT))
        assert coordinator._states[DEVICE_ID].sensor_temperature == 91.0


class TestPoll:
    def _fresh(self, coordinator: GoveeCoordinator) -> set[str]:
        coordinator._transport.record_read(DEVICE_ID, "mqtt")
        coordinator._local_fresh_skips[DEVICE_ID] = 0
        return coordinator._locally_fresh_devices(dict(coordinator._devices))

    def test_poll_that_brings_the_unit_is_never_skipped(self):
        coordinator = _coordinator()
        coordinator._on_mqtt_state_update(DEVICE_ID, dict(H7175_MQTT))
        assert self._fresh(coordinator) == set()

    def test_once_the_unit_is_known_a_push_may_stand_in(self):
        coordinator = _coordinator()
        coordinator._states[DEVICE_ID] = _polled()
        assert self._fresh(coordinator) == {DEVICE_ID}

    def test_other_kettles_keep_their_skip(self):
        coordinator = _coordinator(_device("H717A"))
        coordinator._states[DEVICE_ID] = GoveeDeviceState.create_empty(DEVICE_ID)
        assert self._fresh(coordinator) == {DEVICE_ID}

    async def test_target_kept_when_the_poll_has_none(self):
        coordinator = _coordinator()
        coordinator._states[DEVICE_ID] = _polled()
        coordinator._api_client.get_device_state = AsyncMock(return_value=GoveeDeviceState.create_empty(DEVICE_ID))
        result = await coordinator._fetch_device_state(DEVICE_ID, coordinator._devices[DEVICE_ID])
        assert result.kettle_target_temperature == 176.0

    async def test_a_fresh_poll_target_wins(self):
        coordinator = _coordinator()
        old = _polled()
        old.kettle_target_temperature = 150.0
        coordinator._states[DEVICE_ID] = old
        coordinator._api_client.get_device_state = AsyncMock(return_value=_polled())
        result = await coordinator._fetch_device_state(DEVICE_ID, coordinator._devices[DEVICE_ID])
        assert result.kettle_target_temperature == 176.0
