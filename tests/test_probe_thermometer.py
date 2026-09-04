"""Tests for the H5192 probe thermometer decoder.

Every fixture is a real frame captured from an H5192 and verified against what
the Govee app displayed at the same moment. The values in the docstrings are
what the app showed, so a failure here means the decoder drifted away from the
device, not away from an invented expectation.
"""

from __future__ import annotations

import base64

import pytest

from custom_components.govee.api.probe_thermometer import (
    ProbeLimits,
    REGISTER_LIMITS,
    SENTINEL,
    WRITE_PREFIX,
    build_limits_read_packet,
    build_limits_write_packet,
    build_probe_read_packet,
    concat_command_blocks,
    decode_limits,
    decode_probe_reading,
    decode_status_frame,
    verify_checksum,
)

# --- Captures -------------------------------------------------------------

# Status frame, app showed: probe 1 core 46 degC, ambient 29 degC, target
# 19/50 degC, ambient corridor -10/70 degC; probe 2 unplugged with its
# 17/60 degC corridor still stored.
STATUS_PROBE2_UNPLUGGED = [
    "Rw8AAQH0AQIBABH4E4gHbAtUG1g=",
    "/Bhe7v////8XcAak////////Zu4=",
]

# Status frame with both probes plugged in, before any ambient corridor was set.
STATUS_BOTH_PROBES = [
    "Rw8AAQH0AQIDAA1IE4gHbAu4//8=",
    "//9f7v//DUgXcAakCoz/////XO4=",
]

# Same shape, but byte 0 is 0x44 instead of 0x47 — app showed 36/31 degC.
STATUS_BYTE0_0X44 = [
    "RA8AAQH0AQIDAA4QE4gHbAwcG1g=",
    "/Bhb7v//DawXcAakDBz/////X+4=",
]

# Device-volunteered probe frame (byte 0 0x33), app showed 45 degC / 29 degC.
PROBE_VOLUNTEERED = [
    "MyQBAAYAAO2mjGoAAgAAAAAAAAA=",
    "AAYRlAtUETALVBDMC1QQzArw//8=",
]

# Reply to our read request (byte 0 0xAA), app showed 36 degC / 26 degC.
PROBE_READ_REPLY = [
    "qiQBAAAAAAAAAAAAAQAAAAAAAAA=",
    "AAAOEAoo//////////////////8=",
]

# Reply to a read of register 0x12 after we had written 99/7 degC.
LIMITS_REPLY = ["qhIBJqwCvBtY/Bju//8AAAAAAMQ="]
# Same probe after clearing every alarm in the Govee app: all four values are
# the sentinel and the byte-11 flag reads FF.
LIMITS_EMPTY = ["qhIB//////////////8AAAAAAEY="]
# ... and after setting three of the four from Home Assistant, leaving
# ambient_min unset. Byte 11 reads EE.
LIMITS_PARTIAL = ["qhIBHUwB9GGo///u//8AAAAAADo="]

# A light strip's status packet — must never reach the probe decoder.
LIGHT_STRIP_PACKET = ["qgUBAAAAAAAAAAAAAAAAAAAAAK4="]


def _frame(blocks: list[str]) -> bytes:
    raw = concat_command_blocks(blocks)
    assert raw is not None
    return raw


# --- Status frames --------------------------------------------------------


def test_status_frame_matches_app_display() -> None:
    """Probe 1 at 46/29 degC with both corridors, probe 2 unplugged."""
    probes = decode_status_frame(_frame(STATUS_PROBE2_UNPLUGGED))
    assert probes is not None

    assert probes[1] == {
        "core": 46.0,
        "core_max": 50.0,
        "core_min": 19.0,
        "ambient": 29.0,
        "ambient_max": 70.0,
        "ambient_min": -10.0,
    }
    # The corridor of an unplugged probe stays stored, exactly as the app shows it.
    assert probes[2]["core"] is None
    assert probes[2]["ambient"] is None
    assert probes[2]["core_max"] == 60.0
    assert probes[2]["core_min"] == 17.0


def test_status_frame_unset_ambient_corridor_is_none() -> None:
    """Before a corridor is set, its two words are the sentinel, not 0."""
    probes = decode_status_frame(_frame(STATUS_BOTH_PROBES))
    assert probes is not None
    assert probes[1]["core"] == 34.0
    assert probes[1]["ambient"] == 30.0
    assert probes[1]["ambient_max"] is None
    assert probes[1]["ambient_min"] is None
    assert probes[2]["core"] == 34.0
    assert probes[2]["ambient"] == 27.0


def test_status_frame_ignores_byte_zero() -> None:
    """0x44 and 0x47 are the same frame; only byte 1 identifies it."""
    probes = decode_status_frame(_frame(STATUS_BYTE0_0X44))
    assert probes is not None
    assert probes[1]["core"] == 36.0
    assert probes[1]["ambient"] == 31.0
    assert probes[2]["core"] == 35.0


def test_negative_ambient_minimum_is_signed() -> None:
    """FC18 is -10.00 degC, which is what proves the values are signed."""
    probes = decode_status_frame(_frame(STATUS_PROBE2_UNPLUGGED))
    assert probes is not None
    assert probes[1]["ambient_min"] == -10.0


def test_status_frame_rejects_short_input() -> None:
    assert decode_status_frame(b"\x47\x0f\x00") is None


def test_status_frame_rejects_other_register() -> None:
    assert decode_status_frame(bytes([0x47, 0x24]) + bytes(40)) is None


# --- Probe readings -------------------------------------------------------


@pytest.mark.parametrize(
    ("blocks", "expected"),
    [
        (PROBE_VOLUNTEERED, (1, 45.0, 29.0)),
        (PROBE_READ_REPLY, (1, 36.0, 26.0)),
    ],
)
def test_probe_reading_decodes_both_byte_zero_variants(
    blocks: list[str], expected: tuple[int, float, float]
) -> None:
    """0x33 (device speaks) and 0xAA (device answers) carry the same payload."""
    assert decode_probe_reading(_frame(blocks)) == expected


def test_probe_reading_rejects_unknown_probe_number() -> None:
    raw = bytearray(_frame(PROBE_READ_REPLY))
    raw[2] = 7
    assert decode_probe_reading(bytes(raw)) is None


def test_probe_reading_rejects_light_strip_packet() -> None:
    """Light strips push 0xAA 0x05 packets; those must not decode as probes."""
    assert decode_probe_reading(_frame(LIGHT_STRIP_PACKET)) is None
    assert decode_limits(_frame(LIGHT_STRIP_PACKET)) is None
    assert decode_status_frame(_frame(LIGHT_STRIP_PACKET)) is None


# --- Limits ---------------------------------------------------------------


def test_limits_reply_matches_written_values() -> None:
    """We had written 99/7 degC; the ambient corridor was 70/-10 degC."""
    decoded = decode_limits(_frame(LIMITS_REPLY))
    assert decoded is not None
    probe, limits = decoded
    assert probe == 1
    assert limits == ProbeLimits(
        core_max=99.0, core_min=7.0, ambient_max=70.0, ambient_min=-10.0
    )


def test_sentinel_decodes_to_none_not_minus_hundredth() -> None:
    """0xFFFF must be caught unsigned; as int16 it would be -0.01 degC."""
    raw = bytes([0xAA, REGISTER_LIMITS, 0x01]) + b"\xff\xff" * 4 + bytes(9)
    decoded = decode_limits(raw)
    assert decoded is not None
    _, limits = decoded
    assert limits == ProbeLimits(None, None, None, None)


# --- Packet building ------------------------------------------------------


def test_read_packets_carry_valid_checksum() -> None:
    for packet in (build_probe_read_packet(1), build_limits_read_packet(2)):
        raw = base64.b64decode(packet)
        assert len(raw) == 20
        assert verify_checksum(raw)


def test_write_packet_layout_round_trips_through_the_decoder() -> None:
    """A written frame differs from the read reply only in byte 0."""
    limits = ProbeLimits(
        core_max=99.0, core_min=7.0, ambient_max=70.0, ambient_min=-10.0
    )
    raw = base64.b64decode(build_limits_write_packet(1, limits))

    assert raw[0] == WRITE_PREFIX
    assert raw[1] == REGISTER_LIMITS
    assert raw[2] == 1
    assert verify_checksum(raw)

    reply = _frame(LIMITS_REPLY)
    assert raw[1:19] == reply[1:19]


def test_write_packet_matches_what_the_device_stored() -> None:
    """The packet we build equals the frame the device reported back.

    LIMITS_PARTIAL was captured after setting exactly these three values on a
    live H5192, leaving the fourth alone. Rebuilding it here pins the layout,
    the sentinel for the unset value and the byte-11 flag against hardware
    rather than against an expectation invented in this file.
    """
    limits = ProbeLimits(
        core_max=75.0, core_min=5.0, ambient_max=250.0, ambient_min=None
    )
    raw = base64.b64decode(build_limits_write_packet(1, limits))
    stored = _frame(LIMITS_PARTIAL)

    assert raw[0] == WRITE_PREFIX
    assert raw[1:19] == stored[1:19]


def test_write_packet_clears_a_limit_with_the_sentinel() -> None:
    """None means "no limit" — the device reports and accepts 0xFFFF for it."""
    limits = ProbeLimits(
        core_max=75.0, core_min=None, ambient_max=None, ambient_min=None
    )
    raw = base64.b64decode(build_limits_write_packet(1, limits))

    assert raw[5:11] == bytes([SENTINEL >> 8, SENTINEL & 0xFF]) * 3
    # At least one limit is set, so the byte-11 flag says so.
    assert raw[11] == 0xEE


def test_write_packet_marks_a_fully_empty_corridor() -> None:
    """All four cleared: every value is the sentinel and byte 11 flips to FF.

    LIMITS_EMPTY is the factory/cleared state, captured from a probe whose
    alarms had all been switched off in the Govee app.
    """
    raw = base64.b64decode(
        build_limits_write_packet(1, ProbeLimits(None, None, None, None))
    )
    assert raw[1:19] == _frame(LIMITS_EMPTY)[1:19]
    assert raw[11] == 0xFF


def test_write_packet_encodes_negative_values() -> None:
    limits = ProbeLimits(
        core_max=99.0, core_min=7.0, ambient_max=70.0, ambient_min=-10.0
    )
    raw = base64.b64decode(build_limits_write_packet(1, limits))
    assert raw[9:11] == b"\xfc\x18"


# --- Frame helpers --------------------------------------------------------


def test_bad_checksum_is_detected() -> None:
    raw = bytearray(base64.b64decode(build_probe_read_packet(1)))
    raw[19] ^= 0xFF
    assert not verify_checksum(bytes(raw))


def test_concat_returns_none_on_garbage() -> None:
    assert concat_command_blocks([]) is None
    assert concat_command_blocks(["not base64 !!"]) is None
