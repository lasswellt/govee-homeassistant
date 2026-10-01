"""Decoder for the status frames a Govee H7175 kettle pushes over AWS IoT.

The H7175 reports in its AWS IoT push, besides ``sta`` (see
:mod:`.manager`), BLE-format frames in ``op.command``. Frames are 20 bytes
with an XOR checksum of the first 19 in the last byte; a frame that fails it
is skipped. Byte 0 is ``aa`` for a status frame and ``3a`` for a command or
its echo. Layouts (see ``docs/_research/2026-09-30_h7175-kettle.md``):

==================================  ==============================================
Frame                               Meaning
==================================  ==============================================
``aa 05 00 01 <slot>``              selected mode Custom, slot ``<slot>``
``aa 05 00 <workMode>``             selected built-in mode (byte 4 is not a slot)
``aa 05 00 06 <hi> <lo>``           manual: a target set directly, no preset
``aa 05 01 <page> [hi lo slot 00]`` two custom-slot temperatures per page
``aa 05 <wm> <hi> <lo>`` (wm >= 2)  built-in preset temperature
``aa 10 01 <hi> <lo>``              current water temperature, always °F
``aa 19 <code>``                    heating status (see HEATING_STATUS)
``aa 22 <on> <minutes u16> <left>`` keep warm: on/off, set minutes, minutes left
``3a 22 <on> <minutes u16> <lo>``   echo of a keep-warm command (byte 5 repeats)
==================================  ==============================================

Temperatures are big-endian u16 hundredths in the unit the kettle displays,
except ``aa 10``. In a custom-slot entry the top bit of ``hi`` is set on every
slot but the one the Govee app marks "DIY" (its current custom slot); it is
masked off the value. workMode 6 is not among the capability's options; it
was seen after the target was set directly, carrying that target, and is
taken as "manual" (inferred from live observation). Any other unknown
workMode decodes to the workMode alone.

In ``aa 22`` byte 5 is the keep-warm time left, in minutes: it equals the set
duration until keep warm runs, then counts down (``aa22 01 0078 74`` = 120
set, 116 left, seen while keeping warm). A frame whose byte 5 exceeds the set
duration is skipped.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field

from ..api.ble_packet import calculate_checksum

FRAME_LENGTH = 20
STATUS_PREFIX = 0xAA
COMMAND_PREFIX = 0x3A
_OPCODE_MODE = 0x05
_OPCODE_TEMPERATURE = 0x10
_OPCODE_HEATING = 0x19
_OPCODE_KEEP_WARM = 0x22
_SUB_SELECTED_MODE = 0x00
_SUB_CUSTOM_SLOTS = 0x01
_SUB_CURRENT_TEMPERATURE = 0x01

# workMode the custom-slot pages belong to, and the "manual" workMode.
KETTLE_CUSTOM_WORK_MODE = 1
KETTLE_MANUAL_WORK_MODE = 6

_SLOT_FLAG = 0x80
_SLOT_VALUE_MASK = 0x7FFF

# aa 19 heating codes. 06 was seen while a start was scheduled in the app.
HEATING_STATUS = {
    0x00: "idle",
    0x01: "heating",
    0x02: "keeping_warm",
    0x04: "reached_target",
    0x06: "scheduled",
}


def checksum_ok(frame: bytes) -> bool:
    """Whether a 20-byte frame's last byte is the XOR of the others."""
    return len(frame) == FRAME_LENGTH and calculate_checksum(list(frame[:-1])) == frame[-1]


def centi(hi: int, lo: int, mask: int = 0xFFFF) -> float:
    """Big-endian u16 hundredths to a float."""
    return (((hi << 8) | lo) & mask) / 100.0


@dataclass(frozen=True)
class KettleFrameReport:
    """What the frames of one push said; fields stay None / empty when absent.

    Attributes:
        work_mode: Selected workMode.
        mode_value: Selected custom slot (workMode 1 only).
        manual_target: Target carried by a workMode 6 selection.
        current_temperature: Water temperature from ``aa 10``, in °F.
        preset_temperatures: ``{workMode: {modeValue: temperature}}``;
            built-in presets use modeValue 0.
        slot_flags: ``{slot: flag bit set}`` for the custom-slot entries seen.
        heating_seen: Whether an ``aa 19`` frame was present.
        heating_status: Its status (None for an undocumented code).
        keep_warm: ``(enabled, minutes)``; a status wins over an echo.
        keep_warm_frame: The frame keep warm came from.
        keep_warm_remaining: Minutes of keep warm left (``aa 22`` only).
    """

    work_mode: int | None = None
    mode_value: int | None = None
    manual_target: float | None = None
    current_temperature: float | None = None
    preset_temperatures: dict[int, dict[int, float]] = field(default_factory=dict)
    slot_flags: dict[int, bool] = field(default_factory=dict)
    heating_seen: bool = False
    heating_status: str | None = None
    keep_warm: tuple[bool, int] | None = None
    keep_warm_frame: bytes | None = None
    keep_warm_remaining: int | None = None


def _keep_warm(raw: bytes) -> tuple[bool, int] | None:
    """``<on> <minutes u16> <minutes left>``; None when inconsistent."""
    minutes = (raw[3] << 8) | raw[4]
    if raw[2] not in (0x00, 0x01) or raw[5] > minutes:
        return None
    return raw[2] == 0x01, minutes


def decode_kettle_frames(frames: Iterable[bytes]) -> KettleFrameReport:
    """Decode the kettle frames of one push; unknown frames are ignored."""
    work_mode: int | None = None
    mode_value: int | None = None
    manual_target: float | None = None
    current: float | None = None
    presets: dict[int, dict[int, float]] = {}
    slot_flags: dict[int, bool] = {}
    heating_seen = False
    heating_status: str | None = None
    keep_warm: tuple[bool, int] | None = None
    keep_warm_frame: bytes | None = None
    for raw in frames:
        if not checksum_ok(raw) or raw[0] not in (STATUS_PREFIX, COMMAND_PREFIX):
            continue
        opcode, sub = raw[1], raw[2]
        if opcode == _OPCODE_KEEP_WARM:
            decoded = _keep_warm(raw)
            # A status wins over an echo in the same push.
            if decoded is not None and (
                raw[0] == STATUS_PREFIX or keep_warm_frame is None or keep_warm_frame[0] != STATUS_PREFIX
            ):
                keep_warm, keep_warm_frame = decoded, bytes(raw)
        elif raw[0] != STATUS_PREFIX:
            continue
        elif opcode == _OPCODE_HEATING:
            heating_seen, heating_status = True, HEATING_STATUS.get(sub)
        elif opcode == _OPCODE_TEMPERATURE and sub == _SUB_CURRENT_TEMPERATURE:
            current = centi(raw[3], raw[4])
        elif opcode == _OPCODE_MODE and sub == _SUB_SELECTED_MODE and raw[3] > 0:
            work_mode = raw[3]
            mode_value = raw[4] if work_mode == KETTLE_CUSTOM_WORK_MODE else None
            manual = centi(raw[4], raw[5]) if work_mode == KETTLE_MANUAL_WORK_MODE else 0
            manual_target = manual or None
        elif opcode == _OPCODE_MODE and sub == _SUB_CUSTOM_SLOTS:
            for offset in (4, 8):
                hi, lo, slot = raw[offset], raw[offset + 1], raw[offset + 2]
                temperature = centi(hi, lo, _SLOT_VALUE_MASK)
                if slot > 0 and temperature > 0:
                    presets.setdefault(KETTLE_CUSTOM_WORK_MODE, {})[slot] = temperature
                    slot_flags[slot] = bool(hi & _SLOT_FLAG)
        elif opcode == _OPCODE_MODE and sub > _SUB_CUSTOM_SLOTS:
            temperature = centi(raw[3], raw[4])
            if temperature > 0:
                presets.setdefault(sub, {})[0] = temperature
    return KettleFrameReport(
        work_mode=work_mode,
        mode_value=mode_value,
        manual_target=manual_target,
        current_temperature=current,
        preset_temperatures=presets,
        slot_flags=slot_flags,
        heating_seen=heating_seen,
        heating_status=heating_status,
        keep_warm=keep_warm,
        keep_warm_frame=keep_warm_frame,
        keep_warm_remaining=(
            keep_warm_frame[5] if keep_warm_frame is not None and keep_warm_frame[0] == STATUS_PREFIX else None
        ),
    )
