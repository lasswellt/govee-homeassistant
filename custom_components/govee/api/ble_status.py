"""Conservative H6104 UART status decoding; command echoes are not state."""

from dataclasses import dataclass


@dataclass(frozen=True)
class H6104Report:
    """Only fields actually present in one validated device reply."""

    power: bool | None = None
    brightness: int | None = None  # raw H6104 scale (0-254)
    mode: str | None = None
    rgb: tuple[int, int, int] | None = None
    music_effect: int | None = None
    sensitivity: int | None = None


def decode_h6104_status(
    frame: bytes, *, query: bytes | None = None, power_read_validated: bool = False
) -> H6104Report | None:
    """Decode checksummed AA read replies and EE20 brightness notifications.

    Source: app-derived spec/devices.yaml h6104, govee_state.ksy power_state,
    mode_report/rgb_flag_tint, and OPCODE_COVERAGE build_music_old_0x01:
    https://github.com/Brady-Woods/govee-ble-local/tree/5fc5296d29a94f012062402361f85e740973edac/spec

    An all-zero OFF reply is identical to AA01's request. Do not trust it
    until a non-echo ON reply establishes that this firmware answers power.
    """
    if len(frame) != 20:
        return None
    checksum = 0
    for value in frame:
        checksum ^= value
    if checksum:
        return None
    head, command = frame[:2]
    if head == 0xEE and command == 0x20:
        return H6104Report(brightness=frame[2])
    if head != 0xAA:
        return None
    if query is not None and frame == query and not (command == 1 and power_read_validated):
        return None
    if command == 1 and frame[2] in (0, 1):
        if frame[2] == 0 and not power_read_validated:
            return None
        return H6104Report(power=bool(frame[2]))
    if command == 4 and 0 < frame[2] <= 254:
        return H6104Report(brightness=frame[2])
    if command != 5:
        return None
    mode = frame[2]
    if mode == 0:
        return H6104Report(mode="tv")
    if mode == 1:
        effect = frame[3] if frame[3] in (0, 1, 2, 3) else None
        sensitivity = frame[4] if frame[4] <= 100 else None
        return H6104Report(mode="music", music_effect=effect, sensitivity=sensitivity)
    if mode == 2:
        # H6104 RGB has a following tint flag. A tint has no explicit Kelvin
        # bytes, so do not invent a temperature or report its white marker as RGB.
        if frame[6] != 0:
            return H6104Report(mode="color_temp")
        return H6104Report(mode="rgb", rgb=(frame[3], frame[4], frame[5]))
    if mode in (4, 0x0A):
        return H6104Report(mode="scene" if mode == 4 else "diy")
    return None
