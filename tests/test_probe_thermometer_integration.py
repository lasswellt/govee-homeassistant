"""Wiring tests for probe thermometers (H5192).

The decoder itself is covered by ``test_probe_thermometer.py``. This file
covers what happens around it: which MQTT envelopes reach the decoder, how a
partial frame is merged into coordinator state, and the two places where a
wrong answer would silently cost the owner a reading — the five-minute BFF
refresh and the limits write.
"""

from __future__ import annotations

import base64
import json
from unittest.mock import MagicMock

import pytest

import custom_components.govee.coordinator as coord_mod
from custom_components.govee import const
from custom_components.govee.api.auth import GoveeIotCredentials
from custom_components.govee.api.mqtt import GoveeAwsIotClient
from custom_components.govee.coordinator import GoveeCoordinator
from custom_components.govee.models import GoveeDevice, GoveeDeviceState, ProbeReading

DEVICE_ID = "AA:BB:CC:DD:EE:FF:00:11"

# Real captures, same frames the decoder tests use.
STATUS_BOTH_PROBES = ["Rw8AAQH0AQIDAA1IE4gHbAu4//8=", "//9f7v//DUgXcAakCoz/////XO4="]
PROBE_READ_REPLY = ["qiQBAAAAAAAAAAAAAQAAAAAAAAA=", "AAAOEAoo//////////////////8="]
LIMITS_REPLY = ["qhIBJqwCvBtY/Bju//8AAAAAAMQ="]
# A light strip's segment-color packet. Same op.command field, same 0xAA
# prefix — it must never be routed to the probe decoder.
LIGHT_STRIP_PACKET = ["qgUBAAAAAAAAAAAAAAAAAAAAAK4="]


def _make_client() -> GoveeAwsIotClient:
    """Build an MQTT client with throwaway credentials and a mock callback."""
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
    return GoveeAwsIotClient(creds, on_state_update=MagicMock())


def _message(sku: str, cmd: str, commands: list[str]) -> MagicMock:
    """Wrap base64 command blocks in an AWS IoT message envelope."""
    message = MagicMock()
    message.payload = json.dumps(
        {
            "device": DEVICE_ID,
            "sku": sku,
            "cmd": cmd,
            "op": {"command": commands},
        }
    ).encode()
    return message


class TestMqttDispatch:
    """Only probe SKUs reach the probe decoder, and every frame is retained."""

    async def test_status_frame_reaches_the_state_callback(self):
        client = _make_client()

        await client._handle_message(_message("H5192", "status", STATUS_BOTH_PROBES))

        client._on_state_update.assert_called_once()
        device_id, payload = client._on_state_update.call_args[0]
        assert device_id == DEVICE_ID
        assert payload["_probe_frame"] is True
        # Status frames carry both probes with all six values each.
        assert set(payload["probes"]) == {1, 2}
        assert payload["probes"][1]["core"] == pytest.approx(34.0)

    async def test_ptreal_reply_reaches_the_state_callback(self):
        client = _make_client()

        await client._handle_message(_message("H5192", "ptReal", PROBE_READ_REPLY))

        _, payload = client._on_state_update.call_args[0]
        assert set(payload["probes"]) == {1}
        assert set(payload["probes"][1]) == {"core", "ambient"}

    async def test_light_strip_packet_is_not_decoded_as_a_probe(self):
        """0xAA 0x05 from a light strip must not become a temperature."""
        client = _make_client()

        await client._handle_message(_message("H6072", "status", LIGHT_STRIP_PACKET))

        for call in client._on_state_update.call_args_list:
            assert "_probe_frame" not in call[0][1]
        assert client.recent_probe_frames == []

    async def test_frames_are_retained_for_diagnostics(self):
        client = _make_client()

        await client._handle_message(_message("H5192", "status", STATUS_BOTH_PROBES))

        (record,) = client.recent_probe_frames
        assert record["header"] == "470f"
        assert record["length"] == 40
        assert record["device_id"] == DEVICE_ID

    async def test_unknown_probe_frame_is_retained_but_not_dispatched(self):
        """An undecodable frame still has to survive into a diagnostics dump."""
        client = _make_client()
        unknown = ["qn8AAAAAAAAAAAAAAAAAAAAAANU="]

        await client._handle_message(_message("H5192", "ptReal", unknown))

        assert len(client.recent_probe_frames) == 1
        for call in client._on_state_update.call_args_list:
            assert "_probe_frame" not in call[0][1]


def _coordinator(state: GoveeDeviceState | None = None) -> GoveeCoordinator:
    """Build a coordinator with only the attributes the probe path touches."""
    coordinator = GoveeCoordinator.__new__(GoveeCoordinator)
    device = GoveeDevice.synthetic_probe_thermometer(
        device_id=DEVICE_ID, sku="H5192", name="Grill"
    )
    coordinator._devices = {DEVICE_ID: device}
    coordinator._states = {DEVICE_ID: state or GoveeDeviceState.create_empty(DEVICE_ID)}
    coordinator._sensor_reading_changed_at = {}
    coordinator._probe_polling_enabled = set()
    coordinator._probe_poll_unsub = None
    coordinator._ble_manager = MagicMock()
    coordinator.hass = MagicMock()
    coordinator._ensure_transport_health = MagicMock()
    coordinator._record_transport_success = MagicMock()
    coordinator.async_set_updated_data = MagicMock()
    return coordinator


class TestFrameMerge:
    """A partial frame must not blank the fields it does not carry."""

    def test_limits_reply_keeps_the_live_readings(self):
        state = GoveeDeviceState.create_empty(DEVICE_ID)
        state.probes = {1: ProbeReading(core=34.0, ambient=99.0)}
        coordinator = _coordinator(state)

        coordinator._handle_probe_frame(
            DEVICE_ID,
            {"probes": {1: {"core_max": 75.0, "core_min": 5.0}}},
        )

        reading = coordinator._states[DEVICE_ID].probes[1]
        assert reading.core == 34.0
        assert reading.ambient == 99.0
        assert reading.core_max == 75.0

    def test_reading_reply_keeps_the_limits(self):
        state = GoveeDeviceState.create_empty(DEVICE_ID)
        state.probes = {1: ProbeReading(core_max=75.0, ambient_min=5.0)}
        coordinator = _coordinator(state)

        coordinator._handle_probe_frame(
            DEVICE_ID, {"probes": {1: {"core": 40.0, "ambient": 120.0}}}
        )

        reading = coordinator._states[DEVICE_ID].probes[1]
        assert reading.core_max == 75.0
        assert reading.ambient_min == 5.0
        assert reading.core == 40.0

    def test_frame_marks_the_device_online(self):
        """The BFF online flag is false for this SKU; a frame is proof of life."""
        coordinator = _coordinator()
        coordinator._states[DEVICE_ID].online = False

        coordinator._handle_probe_frame(DEVICE_ID, {"probes": {1: {"core": 40.0}}})

        assert coordinator._states[DEVICE_ID].online is True

    def test_unknown_device_is_ignored(self):
        coordinator = _coordinator()
        coordinator._handle_probe_frame("nope", {"probes": {1: {"core": 40.0}}})
        assert coordinator._states[DEVICE_ID].probes == {}


class TestBffRefreshSkip:
    """The five-minute BFF refresh must leave probe state alone."""

    @pytest.mark.asyncio
    async def test_refresh_does_not_wipe_probe_readings(self, monkeypatch):
        state = GoveeDeviceState.create_empty(DEVICE_ID)
        state.probes = {1: ProbeReading(core=34.0)}
        coordinator = _coordinator(state)
        coordinator._iot_credentials = MagicMock(token="tok")
        coordinator._bff_thermometer_ids = {DEVICE_ID}
        coordinator._bff_thermo_pending = set()
        coordinator._bff_thermo_hubs = {}
        coordinator._display_fahrenheit = {}

        async def _sensors(*args, **kwargs):
            # What the BFF actually returns for an H5192: no reading at all.
            return [
                {
                    "device_id": DEVICE_ID,
                    "name": "Grill",
                    "sku": "H5192",
                    "temperature": None,
                    "humidity": None,
                    "online": False,
                }
            ]

        monkeypatch.setattr(coordinator, "_async_bff_call", _sensors)

        await coordinator._refresh_bff_thermometers()

        assert coordinator._states[DEVICE_ID].probes[1].core == 34.0


class TestProbePollInterval:
    """The poll interval is an option, and a hand-edited bad value must not arm."""

    def _coord_with_options(self, options):
        config_entry = MagicMock()
        config_entry.entry_id = "test_entry"
        config_entry.options = options
        return coord_mod.GoveeCoordinator(
            hass=MagicMock(),
            config_entry=config_entry,
            api_client=MagicMock(),
            iot_credentials=MagicMock(token="tok"),
            poll_interval=60,
        )

    def test_default_when_option_unset(self):
        coord = self._coord_with_options({})
        assert coord._probe_poll_interval == const.DEFAULT_PROBE_POLL_INTERVAL

    def test_configured_value_is_used(self):
        coord = self._coord_with_options({const.CONF_PROBE_POLL_INTERVAL: 120})
        assert coord._probe_poll_interval == 120

    @pytest.mark.parametrize(
        "value",
        [
            const.MIN_PROBE_POLL_INTERVAL - 1,
            const.MAX_PROBE_POLL_INTERVAL + 1,
            "not-a-number",
            None,
        ],
    )
    def test_out_of_range_or_bad_value_falls_back(self, value):
        coord = self._coord_with_options({const.CONF_PROBE_POLL_INTERVAL: value})
        assert coord._probe_poll_interval == const.DEFAULT_PROBE_POLL_INTERVAL

    def test_schedule_uses_configured_interval(self, monkeypatch):
        coord = self._coord_with_options({const.CONF_PROBE_POLL_INTERVAL: 45})
        seen = {}

        def _capture(hass, delay, callback):
            seen["delay"] = delay
            return lambda: None

        monkeypatch.setattr(coord_mod, "async_call_later", _capture)
        coord._schedule_probe_poll()

        assert seen["delay"] == 45


class TestPollingSwitch:
    """Polling is armed per device and the timer stops with the last one."""

    def test_disarming_the_last_device_cancels_the_timer(self):
        coordinator = _coordinator()
        cancelled = []
        coordinator._probe_polling_enabled = {DEVICE_ID}
        coordinator._probe_poll_unsub = lambda: cancelled.append(True)

        coordinator.set_probe_polling(DEVICE_ID, False)

        assert cancelled == [True]
        assert coordinator._probe_poll_unsub is None
        assert not coordinator.is_probe_polling(DEVICE_ID)

    @pytest.mark.asyncio
    async def test_poll_is_a_noop_without_mqtt(self, monkeypatch):
        coordinator = _coordinator()
        coordinator._probe_polling_enabled = {DEVICE_ID}
        monkeypatch.setattr(
            type(coordinator), "mqtt_client", property(lambda self: None)
        )

        await coordinator._poll_probe_thermometers()

        coordinator._ble_manager.async_send_ble_packet.assert_not_called()


class TestLimitsWrite:
    """Register 0x12 has no partial update, so the other three come from state."""

    @pytest.mark.asyncio
    async def test_unknown_limits_go_out_as_the_sentinel(self):
        """A fresh probe has all four unset; the first write must still land.

        The device reports 0xFFFF for a limit that is not set and accepts it
        back, so an unknown value is not a reason to refuse — refusing would
        make the very first limit unsettable.
        """
        state = GoveeDeviceState.create_empty(DEVICE_ID)
        state.probes = {1: ProbeReading()}
        coordinator = _coordinator(state)
        sent = []

        async def _send(device_id, sku, packet):
            sent.append(packet)
            return True

        coordinator._ble_manager.async_send_ble_packet = MagicMock(side_effect=_send)

        result = await coordinator.async_set_probe_limits(DEVICE_ID, 1, core_max=75.0)

        assert result is True
        raw = base64.b64decode(sent[0])
        assert raw[3:5] == bytes([0x1D, 0x4C])  # 75.00 C
        assert raw[5:11] == bytes([0xFF]) * 6  # the three untouched limits

    @pytest.mark.asyncio
    async def test_write_carries_the_other_three_values_over(self):
        state = GoveeDeviceState.create_empty(DEVICE_ID)
        state.probes = {
            1: ProbeReading(
                core_max=75.0, core_min=5.0, ambient_max=250.0, ambient_min=5.0
            )
        }
        coordinator = _coordinator(state)

        async def _send(*args, **kwargs):
            return True

        coordinator._ble_manager.async_send_ble_packet = MagicMock(side_effect=_send)

        result = await coordinator.async_set_probe_limits(DEVICE_ID, 1, core_max=80.0)

        assert result is True
        # One write, then a read-back: the device acknowledges the packet, not
        # the stored value.
        assert coordinator._ble_manager.async_send_ble_packet.call_count == 2


class TestSyntheticDevice:
    """The synthetic device must not claim a capability it cannot serve."""

    def test_has_no_sensor_temperature_capability(self):
        device = GoveeDevice.synthetic_probe_thermometer(
            device_id=DEVICE_ID, sku="H5192", name="Grill"
        )
        assert device.is_probe_thermometer
        assert not device.supports_temperature_sensor
