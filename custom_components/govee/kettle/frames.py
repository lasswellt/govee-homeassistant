"""Decoder for the status frames a Govee H7175 kettle pushes over AWS IoT.

The H7175 reports its water temperature in two places in its AWS IoT push:
``sta.curTem`` (hundredths of a degree in the unit the kettle displays) and a
BLE-format ``aa 10 01 <hi> <lo>`` frame in ``op.command`` (hundredths of a
degree Fahrenheit, whatever the kettle displays). Frames are 20 bytes with an
XOR checksum of the first 19 in the last byte; a frame that fails it is
skipped. See ``docs/_research/2026-09-30_h7175-kettle.md``.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from ..api.ble_packet import calculate_checksum

FRAME_LENGTH = 20
STATUS_PREFIX = 0xAA
_OPCODE_TEMPERATURE = 0x10
_SUB_CURRENT_TEMPERATURE = 0x01


def checksum_ok(frame: bytes) -> bool:
    """Whether a 20-byte frame's last byte is the XOR of the others."""
    return len(frame) == FRAME_LENGTH and calculate_checksum(list(frame[:-1])) == frame[-1]


def centi(hi: int, lo: int, mask: int = 0xFFFF) -> float:
    """Big-endian u16 hundredths to a float."""
    return (((hi << 8) | lo) & mask) / 100.0


@dataclass(frozen=True)
class KettleFrameReport:
    """What the frames of one push said.

    Attributes:
        current_temperature: Water temperature from ``aa 10``, in °F.
    """

    current_temperature: float | None = None


def decode_kettle_frames(frames: Iterable[bytes]) -> KettleFrameReport:
    """Decode the kettle frames of one push; unknown frames are ignored."""
    current: float | None = None
    for raw in frames:
        if not checksum_ok(raw) or raw[0] != STATUS_PREFIX:
            continue
        if raw[1] == _OPCODE_TEMPERATURE and raw[2] == _SUB_CURRENT_TEMPERATURE:
            current = centi(raw[3], raw[4])
    return KettleFrameReport(current_temperature=current)
