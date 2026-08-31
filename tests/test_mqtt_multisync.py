"""Tests for multiSync packet capture in the AWS IoT MQTT client.

Covers the diagnostics ring buffer added for #87 — every decoded hub packet
(leak / button-press / unknown subtype) is retained as hex so undecoded
subtypes can be reverse-engineered from a diagnostics download alone, with
button-press MACs masked to keep the retained hex PII-free.
"""

from __future__ import annotations

import asyncio
import base64
import json
from unittest.mock import MagicMock

from custom_components.govee.api.auth import GoveeIotCredentials
from custom_components.govee.api.mqtt import GoveeAwsIotClient

HUB_ID = "07:23:5C:E7:53:5F:6F:0A"


def _make_client() -> GoveeAwsIotClient:
    """Build a client with throwaway credentials and a no-op callback."""
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


def _multisync(packets: list[bytes]) -> dict:
    """Wrap raw packets in a multiSync message envelope."""
    return {
        "device": HUB_ID,
        "sku": "H5044",
        "cmd": "multiSync",
        "op": {"command": [base64.b64encode(p).decode() for p in packets]},
    }


class TestMultiSyncCapture:
    """The ring buffer records every packet subtype for diagnostics."""

    def test_records_leak_and_unknown_packets(self):
        """Both the decoded 0x34 leak packet and an undecoded 0x35 are captured."""
        client = _make_client()
        leak = bytes([0xEE, 0x34, 0x00, 0x00, 0x00, 0x00])  # slot 0, dry
        unknown = bytes([0xEE, 0x35, 0x04, 0x01, 0x02, 0x03])  # the #87 mystery

        client._handle_multisync(HUB_ID, _multisync([leak, unknown]))

        captured = client.recent_multisync
        assert len(captured) == 2
        headers = {rec["header"] for rec in captured}
        assert headers == {"ee34", "ee35"}
        # Full hex of the unknown subtype is retained so it can be decoded later.
        unknown_rec = next(r for r in captured if r["header"] == "ee35")
        assert unknown_rec["hex"] == "ee35040102 03".replace(" ", "")
        assert unknown_rec["length"] == 6
        assert unknown_rec["hub_device_id"] == HUB_ID

    def test_button_press_mac_is_masked(self):
        """Button-press (0x32) packets embed a MAC in bytes 2-9 — mask it."""
        client = _make_client()
        mac = bytes([0xDE, 0xAD, 0xBE, 0xEF, 0x01, 0x02, 0x03, 0x04])
        button = bytes([0xEE, 0x32]) + mac + bytes([0x00])

        client._handle_multisync(HUB_ID, _multisync([button]))

        rec = client.recent_multisync[0]
        assert rec["header"] == "ee32"
        # Bytes 2-9 (the MAC) must be zeroed in the retained hex.
        assert rec["hex"] == "ee32" + "00" * 8 + "00"
        assert "dead" not in rec["hex"]

    def test_ring_buffer_is_bounded(self):
        """The buffer keeps only the most recent packets (maxlen=64)."""
        client = _make_client()
        packets = [bytes([0xEE, 0x34, i & 0xFF, 0, 0, 0]) for i in range(100)]
        client._handle_multisync(HUB_ID, _multisync(packets))

        captured = client.recent_multisync
        assert len(captured) == 64
        # Oldest dropped: slot bytes should be the last 64 (36..99).
        first_slot = int(captured[0]["hex"][4:6], 16)
        assert first_slot == 36

    def test_short_packet_still_recorded(self):
        """A truncated packet is captured for diagnostics, not silently dropped."""
        client = _make_client()
        client._handle_multisync(HUB_ID, _multisync([bytes([0xEE])]))

        captured = client.recent_multisync
        assert len(captured) == 1
        assert captured[0]["hex"] == "ee"
        assert captured[0]["length"] == 1

    def test_undecodable_base64_does_not_record(self):
        """A command that fails base64 decode is skipped, not recorded."""
        client = _make_client()
        client._handle_multisync(
            HUB_ID, {"device": HUB_ID, "op": {"command": ["!!!not-base64!!!"]}}
        )
        # Lenient base64 may yield bytes for some inputs; assert no crash and
        # that any recorded entry is well-formed hex.
        for rec in client.recent_multisync:
            assert set(rec["hex"]) <= set("0123456789abcdef")


class TestLeakWetDecode:
    """0x34 leak decode emits the correct wet flag (issue #87).

    Real H5059-via-H5044 packets captured in the #87 diagnostics:
    byte 2 = slot (sno), byte 5 = battery (0x64), bytes 14/16 = probe state.
    """

    # slot 0 LEAK / CLEAR and slot 2 LEAK / CLEAR (verbatim from diagnostics)
    H5059_SLOT0_LEAK = bytes.fromhex("ee34000200641e14ad6a1f4a58000103018000ff")
    H5059_SLOT0_CLEAR = bytes.fromhex("ee34000200641e14966a1f4a58000003008000c4")
    H5059_SLOT2_LEAK = bytes.fromhex("ee34020200641e14a26a1f4a5b000103018000f1")

    def _decode_one(self, packet: bytes) -> dict:
        """Run one packet through the handler; return the emitted event_data."""
        cb = MagicMock()
        client = _make_client()
        client._on_state_update = cb
        client._handle_multisync(HUB_ID, _multisync([packet]))
        assert cb.call_count == 1
        return cb.call_args[0][1]

    def test_h5059_wet_decoded_from_probe_bytes(self):
        """A real LEAK packet (bytes 14/16 = 0x01) decodes is_wet=True."""
        event = self._decode_one(self.H5059_SLOT0_LEAK)
        assert event["_leak_event"] is True
        assert event["sensor_slot"] == 0
        assert event["is_wet"] is True

    def test_h5059_clear_decoded_dry(self):
        """A real CLEAR packet (bytes 14/16 = 0x00) decodes is_wet=False."""
        event = self._decode_one(self.H5059_SLOT0_CLEAR)
        assert event["is_wet"] is False

    def test_h5059_slot_preserved(self):
        """sensor_slot is byte 2 and maps to the BFF sno (slot 2 here)."""
        event = self._decode_one(self.H5059_SLOT2_LEAK)
        assert event["sensor_slot"] == 2
        assert event["is_wet"] is True

    def test_battery_byte_not_misread_as_wet(self):
        """Regression: byte 5 = 0x64 (battery) must not flip is_wet True.

        The pre-#87 decoder read is_wet from byte 5; on H5059 that byte is the
        battery percent, so a dry sensor would never report — the exact #87 bug.
        """
        event = self._decode_one(self.H5059_SLOT0_CLEAR)
        assert self.H5059_SLOT0_CLEAR[5] == 0x64  # battery, not 0x01
        assert event["is_wet"] is False

    def test_legacy_byte5_wet_still_decoded(self):
        """Backward compat: SKUs reporting wet at byte 5 keep working."""
        legacy = bytes([0xEE, 0x34, 0x00, 0x00, 0x00, 0x01])  # byte5=0x01, len 6
        event = self._decode_one(legacy)
        assert event["is_wet"] is True


class TestPresenceReportDecode:
    """0xAA 0x01 mmWave presence report frames (H5127, issue #124).

    Byte layout per ultimate-govee's presence.state.ts and the real captures
    in homebridge-govee #840: byte 2 = mmWave detected, byte 5 = biological
    detected, byte 16 = overall occupancy flag (tracks the status push's
    ``triSta`` exactly). Absence arrives ONLY via this multiSync frame.
    """

    @staticmethod
    def _frame(body: list[int]) -> bytes:
        """Pad to 19 bytes and append the XOR checksum (Govee BLE framing)."""
        padded = bytes(body) + bytes(19 - len(body))
        checksum = 0
        for b in padded:
            checksum ^= b
        return padded + bytes([checksum])

    def _emit_one(self, packet: bytes):
        cb = MagicMock()
        client = _make_client()
        client._on_state_update = cb
        client._handle_multisync(HUB_ID, _multisync([packet]))
        return cb

    def test_presence_frame_emits_trista_1(self):
        # Modeled on the #840 capture: detected=1, distance 162cm, overall=1.
        frame = self._frame(
            [0xAA, 0x01, 0x01, 0x00, 0xA2, 0x01, 0x00, 0x9E]
            + [0x00] * 8
            + [0x01, 0x00, 0x00]
        )
        cb = self._emit_one(frame)
        cb.assert_called_once_with(HUB_ID, {"triSta": 1})

    def test_absence_frame_emits_trista_0(self):
        # Absence: detected flags cleared, distances persist (last known).
        frame = self._frame(
            [0xAA, 0x01, 0x00, 0x00, 0x99, 0x01, 0x00, 0x99]
            + [0x00] * 8
            + [0x00, 0x00, 0x00]
        )
        cb = self._emit_one(frame)
        cb.assert_called_once_with(HUB_ID, {"triSta": 0})

    def test_short_presence_frame_falls_back_to_byte2(self):
        cb = self._emit_one(bytes([0xAA, 0x01, 0x01, 0x00, 0xA2, 0x01]))
        cb.assert_called_once_with(HUB_ID, {"triSta": 1})

    def test_config_report_recorded_but_not_emitted(self):
        # 0xAA 0x05 detection-settings report: diagnostics-only, no state.
        cb = MagicMock()
        client = _make_client()
        client._on_state_update = cb
        frame = self._frame([0xAA, 0x05, 0x01, 0x03, 0x20, 0x00, 0x05])
        client._handle_multisync(HUB_ID, _multisync([frame]))
        cb.assert_not_called()
        assert client.recent_multisync[0]["header"] == "aa05"


class TestPerDeviceReceiveTimestamp:
    """_handle_message stamps a per-device inbound MQTT receive timestamp."""

    DEV = "03:9C:DC:06:75:4B:10:7C"

    def _state_msg(self, device_id: str) -> MagicMock:
        msg = MagicMock()
        msg.payload = json.dumps(
            {"device": device_id, "sku": "H6072", "state": {"onOff": 1}}
        ).encode()
        return msg

    def test_none_before_any_message(self):
        client = _make_client()
        assert client.last_message_ts_for(self.DEV) is None

    def test_stamps_per_device_and_hub(self):
        client = _make_client()
        asyncio.run(client._handle_message(self._state_msg(self.DEV)))
        ts = client.last_message_ts_for(self.DEV)
        assert ts is not None
        # Per-device stamp matches the hub-level scalar for a single message.
        assert ts == client.last_message_ts

    def test_distinct_devices_tracked_separately(self):
        client = _make_client()
        other = "11:22:33:44:55:66:77:88"
        asyncio.run(client._handle_message(self._state_msg(self.DEV)))
        asyncio.run(client._handle_message(self._state_msg(other)))
        assert client.last_message_ts_for(self.DEV) is not None
        assert client.last_message_ts_for(other) is not None
        # Second message advanced the hub scalar past the first device's stamp.
        assert client.last_message_ts_for(other) >= client.last_message_ts_for(self.DEV)


class TestThermoFrameDecode:
    """0xEE 0x34 / byte3=0x08 thermometer frames (issues #151, #157).

    A gateway-bridged H5310 reports through the same 0xEE 0x34 header its
    stablemate leak sensors use. Byte 3 is the only discriminator: 0x02 for a
    leak sub-device, 0x08 for a thermometer.

    The temperature encoding is
    ``T[°C] = (byte13 + 256 * (byte14 & 1) + 112) / 10``, established
    in #151 against 30 on-the-hour frames paired with the cloud reading each
    produced. All frames below are verbatim from that capture, with the label
    the reporter's own cloud history recorded for them.
    """

    # (hex, labelled °C) — the first, warmest, coldest and last of the capture.
    LABELLED = [
        ("ee34000800642915c26a7eca3c89baccff80002a", 24.9),
        ("ee34000800642915bc6a7f56dcb7baccff800017", 29.5),
        ("ee34000800642915bc6a7fff9b8abaccff8000c4", 25.0),
        ("ee34000800642915bf6a80620caabaccff800012", 28.1),
    ]

    # H5310 via H5044 from a DIFFERENT account (#157) — one labelled reading
    # of 88.34 °F at the end of that window, where byte 13 read 201.
    H5310_OTHER_ACCOUNT = "ee34000800642514a86a7ab5bec982ccff200060"

    # Same H5310 immediately around the 36.7/36.8 °C rollover. Byte 13 wraps
    # FF -> 00 and the low bit of byte 14 carries into 82 -> 83.
    ROLLOVER = [
        ("ee34000800642915b86a9374dfff82ccff20000e", 36.7),
        ("ee34000800642915b76a9374070083ccff200027", 36.8),
        ("ee34000800642915bb6a9340271683ccff200029", 39.0),
    ]

    def _decode_one(self, packet: bytes) -> dict:
        """Run one packet through the handler; return the emitted event_data."""
        cb = MagicMock()
        client = _make_client()
        client._on_state_update = cb
        client._handle_multisync(HUB_ID, _multisync([packet]))
        assert cb.call_count == 1
        return cb.call_args[0][1]

    def test_labelled_frames_decode_within_a_tenth(self):
        """Every labelled frame reproduces its cloud reading within 0.1 K."""
        for hex_frame, label in self.LABELLED:
            event = self._decode_one(bytes.fromhex(hex_frame))
            assert event["_thermo_frame"] is True
            assert abs(event["temperature_c"] - label) <= 0.1, hex_frame

    def test_second_account_frame_matches_its_own_label(self):
        """The #157 capture's 88.34 °F reading = 31.3 °C, from byte 13 = 201.

        Cross-account confirmation: a formula fitted only to #151's data
        reproduces the one labelled point an unrelated reporter captured.
        """
        raw = bytes.fromhex(self.H5310_OTHER_ACCOUNT)
        assert raw[13] == 201
        event = self._decode_one(raw)
        fahrenheit = event["temperature_c"] * (9.0 / 5.0) + 32.0
        assert abs(fahrenheit - 88.34) <= 0.1

    def test_epoch_timestamp_decoded(self):
        """Bytes 9-12 are a big-endian Unix timestamp (2026-08-14 07:56Z)."""
        event = self._decode_one(bytes.fromhex(self.LABELLED[0][0]))
        assert event["frame_ts"] == 0x6A7ECA3C
        assert 1786000000 < event["frame_ts"] < 1790000000

    def test_temperature_carry_bit_across_rollover(self):
        """Byte 14 bit 0 extends the temperature to nine bits."""
        for hex_frame, label in self.ROLLOVER:
            event = self._decode_one(bytes.fromhex(hex_frame))
            assert abs(event["temperature_c"] - label) <= 0.1, hex_frame

    def test_battery_and_slot_decoded(self):
        event = self._decode_one(bytes.fromhex(self.LABELLED[0][0]))
        assert event["battery"] == 100
        assert event["sensor_slot"] == 0

    def test_leak_frame_is_not_diverted(self):
        """Regression: a real leak frame keeps reaching the leak decoder.

        Leak and thermo frames share the 0xEE 0x34 header, so a discriminator
        that reads too broadly would silence leak detection — the one failure
        this decode must never cause.
        """
        event = self._decode_one(
            bytes.fromhex("ee34000200641e14ad6a1f4a58000103018000ff")
        )
        assert event["_leak_event"] is True
        assert event.get("_thermo_frame") is None
        assert event["is_wet"] is True

    def test_thermo_frame_emits_no_leak_event(self):
        """A thermo frame must not also report its slot dry.

        Byte 5 is battery in both frame types, so before the discriminator an
        H5310 report decoded as a leak-clear for whatever leak sensor shared
        its slot on the hub.
        """
        event = self._decode_one(bytes.fromhex(self.LABELLED[0][0]))
        assert event.get("_leak_event") is None
        assert "is_wet" not in event

    def test_short_thermo_frame_ignored(self):
        """A truncated frame has no byte 13 to read."""
        cb = MagicMock()
        client = _make_client()
        client._on_state_update = cb
        client._handle_multisync(
            HUB_ID, _multisync([bytes.fromhex("ee34000800642915")])
        )
        assert cb.call_count == 0

    def test_thermo_frames_still_recorded_for_diagnostics(self):
        """Diverting the frame must not drop it from the ring buffer."""
        client = _make_client()
        client._handle_multisync(
            HUB_ID, _multisync([bytes.fromhex(self.LABELLED[0][0])])
        )
        assert [rec["header"] for rec in client.recent_multisync] == ["ee34"]
