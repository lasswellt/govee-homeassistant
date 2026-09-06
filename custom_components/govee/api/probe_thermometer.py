"""Decoding and encoding for Govee probe thermometers (H5192).

These are two-probe cooking thermometers. Unlike every other device in this
integration they are **pull devices**: they never volunteer readings, they only
answer requests. Frames arrive while the Govee app is open because the app
polls them on open — not because the device pushes. A 10 K core-temperature
change with the app closed produces zero frames, while other devices on the
same MQTT connection keep reporting, so it is not change-triggered either.

The payload travels as base64 blocks in ``op.command`` of a ``ptReal`` message.
Packets are 20 bytes with an XOR checksum over bytes 0-18 (see
:mod:`.ble_packet`). Byte 0 selects read or write, byte 1 the register, byte 2
the probe number.

Register map, established by scanning and correlating against values set in the
Govee app (33/88 degC for the core corridor, 70/-10 degC for the ambient one):

===========  ==========================================  =========================
Register     Contents                                    Evidence
===========  ==========================================  =========================
``0x08``     current probe values (core, ambient)        ``0D48 0A28`` = 34.00/26.00
``0x09``     core corridor (max, min)                    ``2260 0CE4`` = 88/33
``0x0C``     early-warning offset                        ``01F4`` = 5.00 (app value)
``0x0D``     hardware version                            ASCII ``1.02.00``
``0x0E``     wifi version                                ASCII ``2.10.03``
``0x0F``     MAC address                                 matched the app's info screen
``0x11``     ambient corridor (max, min)                 ``1B58 FC18`` = 70/-10
``0x12``     all four limits at once                     ``26AC 02BC 1B58 FC18``
``0x24``     probe data + history buffer                 see :func:`decode_probe_reading`
``0x26``     likely battery voltage                      ``0DA6`` = 3494 (mV)
===========  ==========================================  =========================

``0x12`` is the register this module uses for limits: one read returns all four
values for a probe, one write sets them. The device has no partial update, so a
caller changing one value must supply the other three; a limit that is not set
is carried as the sentinel, which the device both reports and accepts.

Byte 11 of a ``0x12`` frame is a flag for whether the probe has any limit at
all: ``0xFF`` when all four are unset, ``0xEE`` as soon as one is set. It does
not encode which limit or how many — captured across all four states on an
H5192 with firmware 1.00.81. Bytes 12-13 stayed ``FF FF`` throughout.

All temperatures are signed int16, big-endian, in hundredths of a degree
Celsius. ``0xFFFF`` is the "not present" sentinel, returned both by an unplugged
probe and by an unset limit — it is checked **unsigned before** the signed
conversion, otherwise it would decode to -0.01 degC.

Two inbound frame shapes carry readings:

* ``cmd: "status"``, byte 1 ``0x0F`` — both probes, six values each. The device
  sends this unprompted on power-on and when an app session opens; no request
  was found that triggers it.
* ``cmd: "ptReal"``, byte 1 ``0x24`` — one probe's core and ambient temperature
  plus a history buffer, newest first. Byte 0 is ``0x33`` when the device
  volunteers it and ``0xAA`` when it answers a read.

Byte 0 of the status frame varies (``0x40``, ``0x42``, ``0x44``, ``0x45`` and
``0x47`` all observed on one device), so **only byte 1 identifies a frame**.

The byte that looks like a battery percentage is a counter of buffered history
points: it dropped from ``0x29`` to ``0x01`` on a probe swap while the battery
was unchanged. The BFF device entry carries no battery field for this SKU
either, so battery level is only available as the raw voltage in ``0x26``.
"""

from __future__ import annotations

import base64
import binascii
import struct
from dataclasses import dataclass

from .ble_packet import build_packet, encode_packet_base64

READ_PREFIX = 0xAA
WRITE_PREFIX = 0x33

REGISTER_PROBE_DATA = 0x24
REGISTER_LIMITS = 0x12
REGISTER_STATUS = 0x0F

SENTINEL = 0xFFFF
SCALE = 100.0

PROBES = (1, 2)

# Status frame (byte 1 == 0x0F): six int16 per probe, 16 bytes apart.
_STATUS_PROBE_OFFSETS = {1: 10, 2: 26}
_STATUS_FIELDS = (
    "core",
    "core_max",
    "core_min",
    "ambient",
    "ambient_max",
    "ambient_min",
)

# ptReal frame (byte 1 == 0x24): newest (core, ambient) pair at offset 22.
_PROBE_NUMBER_OFFSET = 2
_PROBE_FIRST_PAIR = 22

# Limits frame (byte 1 == 0x12): four int16 from offset 3.
_LIMITS_FIRST = 3
_LIMITS_FIELDS = ("core_max", "core_min", "ambient_max", "ambient_min")
# Byte 11 says whether the probe has any limit set at all; bytes 12-13 were
# FF FF in every capture. See the module docstring for the evidence.
_LIMITS_ANY_SET = 0xEE
_LIMITS_NONE_SET = 0xFF
_LIMITS_TAIL_REST = (0xFF, 0xFF)


@dataclass(frozen=True)
class ProbeLimits:
    """The four alarm limits of a single probe, in degrees Celsius."""

    core_max: float | None
    core_min: float | None
    ambient_max: float | None
    ambient_min: float | None


def _read_temperature(raw: bytes, offset: int) -> float | None:
    """Read one int16 as degrees Celsius, or None for the sentinel.

    The unsigned word is compared against the sentinel *before* the signed
    conversion — ``0xFFFF`` interpreted as a signed value would decode to
    -0.01 degC and look like a plausible reading.
    """
    if offset + 2 > len(raw):
        return None
    if struct.unpack_from(">H", raw, offset)[0] == SENTINEL:
        return None
    value: int = struct.unpack_from(">h", raw, offset)[0]
    return value / SCALE


def concat_command_blocks(commands: list[str]) -> bytes | None:
    """Join the base64 blocks of an ``op.command`` list into one frame."""
    if not commands:
        return None
    try:
        return b"".join(base64.b64decode(block) for block in commands)
    except (binascii.Error, TypeError, ValueError):
        return None


def verify_checksum(raw: bytes) -> bool:
    """Return True if the first 20 bytes carry a valid XOR checksum."""
    if len(raw) < 20:
        return False
    checksum = 0
    for byte in raw[:19]:
        checksum ^= byte
    return checksum == raw[19]


def decode_status_frame(raw: bytes) -> dict[int, dict[str, float | None]] | None:
    """Decode a ``cmd: "status"`` frame into per-probe readings and limits.

    Returns a mapping of probe number to its six values, or None if the frame
    is too short or byte 1 does not identify a status frame.
    """
    if len(raw) < 40 or raw[1] != REGISTER_STATUS:
        return None

    result: dict[int, dict[str, float | None]] = {}
    for probe, base in _STATUS_PROBE_OFFSETS.items():
        result[probe] = {
            field: _read_temperature(raw, base + 2 * index)
            for index, field in enumerate(_STATUS_FIELDS)
        }
    return result


def decode_probe_reading(raw: bytes) -> tuple[int, float | None, float | None] | None:
    """Decode a ``0x24`` frame into ``(probe, core, ambient)``.

    Accepts both the device-volunteered form (byte 0 ``0x33``) and the reply to
    a read (byte 0 ``0xAA``); only byte 1 is checked.
    """
    if len(raw) < _PROBE_FIRST_PAIR + 4 or raw[1] != REGISTER_PROBE_DATA:
        return None

    probe = raw[_PROBE_NUMBER_OFFSET]
    if probe not in PROBES:
        return None

    return (
        probe,
        _read_temperature(raw, _PROBE_FIRST_PAIR),
        _read_temperature(raw, _PROBE_FIRST_PAIR + 2),
    )


def decode_limits(raw: bytes) -> tuple[int, ProbeLimits] | None:
    """Decode a ``0x12`` frame into ``(probe, ProbeLimits)``."""
    if len(raw) < _LIMITS_FIRST + 8 or raw[1] != REGISTER_LIMITS:
        return None

    probe = raw[_PROBE_NUMBER_OFFSET]
    if probe not in PROBES:
        return None

    values = {
        field: _read_temperature(raw, _LIMITS_FIRST + 2 * index)
        for index, field in enumerate(_LIMITS_FIELDS)
    }
    return probe, ProbeLimits(**values)


def build_probe_read_packet(probe: int) -> str:
    """Build the base64 read request for one probe's current values."""
    return encode_packet_base64(build_packet([READ_PREFIX, REGISTER_PROBE_DATA, probe]))


def build_limits_read_packet(probe: int) -> str:
    """Build the base64 read request for one probe's four limits."""
    return encode_packet_base64(build_packet([READ_PREFIX, REGISTER_LIMITS, probe]))


def build_limits_write_packet(probe: int, limits: ProbeLimits) -> str:
    """Build the base64 write packet setting all four limits of one probe.

    A limit of None is written as the sentinel, which is how the device itself
    represents "no limit": a factory-fresh probe reports all four as ``0xFFFF``,
    and clearing an alarm in the Govee app puts them back to it. Refusing to
    write while a value is unknown would therefore lock the common case — every
    probe starts with all four unset, so the first limit could never be set.

    The device has no partial update, so the caller supplies all four values;
    :meth:`GoveeCoordinator.async_set_probe_limits` fills the ones that are not
    being changed from state.
    """
    values = (
        limits.core_max,
        limits.core_min,
        limits.ambient_max,
        limits.ambient_min,
    )

    data = [WRITE_PREFIX, REGISTER_LIMITS, probe]
    for value in values:
        if value is None:
            data.extend(SENTINEL.to_bytes(2, "big"))
            continue
        data.extend(struct.pack(">h", round(float(value) * SCALE)))

    any_set = any(value is not None for value in values)
    data.append(_LIMITS_ANY_SET if any_set else _LIMITS_NONE_SET)
    data.extend(_LIMITS_TAIL_REST)

    return encode_packet_base64(build_packet(data))
