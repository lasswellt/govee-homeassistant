"""BLE packet construction for Govee devices.

Builds 20-byte BLE packets that can be sent via the AWS IoT MQTT
ptReal (passthrough real) command to control device features not
exposed via the REST API.

Packet format:
- Bytes 0-18: Command data (padded with 0x00)
- Byte 19: XOR checksum of bytes 0-18

Music Mode packet:
- Byte 0: 0x33 (standard command prefix)
- Byte 1: 0x05 (color/mode command)
- Byte 2: 0x01 (music mode indicator)
- Byte 3: enabled (0x01=on, 0x00=off)
- Byte 4: sensitivity (0-100)
"""

from __future__ import annotations

import base64

from ..const import PTREAL_ZONE_MAX_KELVIN, PTREAL_ZONE_MIN_KELVIN
from ..models.state import RGBColor

# Music mode packet constants
MUSIC_PACKET_PREFIX = 0x33
MUSIC_MODE_COMMAND = 0x05
MUSIC_MODE_INDICATOR = 0x01

# DreamView (Video/Movie Mode) packet constants
DREAMVIEW_COMMAND = 0x05  # Same as music mode command byte
DREAMVIEW_INDICATOR = 0x00  # Video mode indicator (0x04 is a scene preset!)
DREAMVIEW_SEGMENTS_ALL = 0x01  # 0x00 = partial, 0x01 = all segments
DREAMVIEW_STYLE_MOVIE = 0x00  # 0x00 = movie, 0x01 = game
DREAMVIEW_SATURATION_MAX = 0x64  # Saturation, 0x00-0x64
DIY_MODE_INDICATOR = 0x0A  # DIY mode indicator

# DIY style name to value mapping for select entity
DIY_STYLE_NAMES: dict[str, int] = {
    "Fade": 0x00,
    "Jumping": 0x01,
    "Flicker": 0x02,
    "Marquee": 0x03,
    "Music": 0x04,
}


def calculate_checksum(data: list[int]) -> int:
    """Calculate XOR checksum of all bytes.

    Args:
        data: List of byte values to checksum.

    Returns:
        XOR of all bytes, masked to 8 bits.
    """
    checksum = 0
    for byte in data:
        checksum ^= byte
    return checksum & 0xFF


def build_packet(data: list[int]) -> bytes:
    """Build a 20-byte BLE packet with checksum.

    Pads the data to 19 bytes and appends XOR checksum.

    Args:
        data: Command bytes (will be padded to 19 bytes).

    Returns:
        20-byte packet as bytes.
    """
    packet = list(data)

    # Pad to 19 bytes
    while len(packet) < 19:
        packet.append(0x00)

    # Truncate if too long
    packet = packet[:19]

    # Append checksum
    packet.append(calculate_checksum(packet))

    return bytes(packet)


def build_music_mode_packet(enabled: bool, sensitivity: int = 50) -> bytes:
    """Build music mode control packet.

    Args:
        enabled: True to enable music mode, False to disable.
        sensitivity: Microphone sensitivity 0-100.

    Returns:
        20-byte BLE packet for music mode command.
    """
    # Clamp sensitivity to valid range
    sensitivity = max(0, min(100, sensitivity))

    # Build command data
    # Packet: 33 05 01 [ENABLED] [SENSITIVITY] ...
    data = [
        MUSIC_PACKET_PREFIX,  # 0x33 - Standard command prefix
        MUSIC_MODE_COMMAND,  # 0x05 - Color/mode command
        MUSIC_MODE_INDICATOR,  # 0x01 - Music mode indicator
        0x01 if enabled else 0x00,  # Enabled state
        sensitivity,  # Sensitivity 0-100
    ]

    return build_packet(data)


# Music mode as the Govee app writes it (sub-mode 0x13, the app's
# SubModeMusicV3). Layout per teh-hippo/ha-govee-led-ble, decompiled from the
# Android app 7.6.01 and captured on the H617A/H6099/H6199/H6102:
#   33 05 13 <effect> <sensitivity 0-100> [<style> <fixed colour> <R G B>]
# The bracketed tail exists only for the four legacy effects. An H612F status
# frame confirms the shape: aa 05 13 31 19 = Shiny at sensitivity 25 (#215).
MUSIC_V3_INDICATOR = 0x13
MUSIC_V3_LEGACY_EFFECTS = frozenset({0x03, 0x04, 0x05, 0x06})
# The Developer API's musicMode values are per-model list positions (the
# H612F lists Rhythm=0, Shiny=2), so the effect is matched by name. Keys are
# lowercased with non-letters removed; "energic" is the API's own spelling.
MUSIC_V3_EFFECT_CODES: dict[str, int] = {
    "rhythm": 0x03,
    "spectrum": 0x04,
    "energic": 0x05,
    "energetic": 0x05,
    "rolling": 0x06,
    "bloom": 0x30,
    "shiny": 0x31,
    "separation": 0x32,
    "hopping": 0x33,
    "pianokeys": 0x34,
    "fountain": 0x35,
    "dayandnight": 0x37,
}


def music_v3_effect_code(name: str) -> int | None:
    """App effect code for a Developer-API music mode name, or None if unknown."""
    key = "".join(ch for ch in name.lower() if ch.isalpha())
    return MUSIC_V3_EFFECT_CODES.get(key)


def build_music_mode_v3_packet(effect_code: int, sensitivity: int) -> bytes:
    """Build the app's music-mode selector frame.

    Legacy effects get the dynamic style and automatic colour; newer effects
    carry only the effect and sensitivity and play the palette the device
    already holds.

    Args:
        effect_code: App effect code (see ``MUSIC_V3_EFFECT_CODES``).
        sensitivity: Microphone sensitivity 0-100.

    Returns:
        20-byte BLE packet.
    """
    data = [
        MUSIC_PACKET_PREFIX,
        MUSIC_MODE_COMMAND,
        MUSIC_V3_INDICATOR,
        effect_code & 0xFF,
        max(0, min(100, sensitivity)),
    ]
    if effect_code in MUSIC_V3_LEGACY_EFFECTS:
        data.extend([0x00, 0x00])  # dynamic style, no fixed colour
    return build_packet(data)


# H1232 "Ceiling Light Pro" ptReal segment frames (issue #223). The fixture has
# 17 internal segments (1-16 = RGBIC ring, 17 = main panel) behind a single
# 4-byte little-endian bitmask (the 4th byte is the old pad, #310), bit i = internal segment i+1 (bits 0-15 =
# ring, bit 16 = main panel). Verified visually on hardware with
# ``send_raw_ptreal``, frames before the ``build_packet`` checksum:
#   ring + panel green:          33 05 15 01 00 FF 00 00 00 00 00 00 FF FF 01
#   segments 14-16 + panel red:  33 05 15 01 FF 00 00 00 00 00 00 00 00 E0 01
#   panel only blue:             33 05 15 01 00 00 FF 00 00 00 00 00 00 00 01
#   ring only at 10 %:           33 05 15 02 0A FF FF 00
SEGMENT_PTREAL_COMMAND = 0x05
SEGMENT_PTREAL_INDICATOR = 0x15
SEGMENT_PTREAL_COLOR_OP = 0x01
SEGMENT_PTREAL_BRIGHTNESS_OP = 0x02

# Ceiling Light Pro light-zone switch (H1252, issue #223): ``33 30 <zone> <on>``
# switches one zone; the device reports both as ``aa 30 <main> <ring>``.
LIGHT_ZONE_PTREAL_COMMAND = 0x30
LIGHT_ZONE_MAIN = 0x00
LIGHT_ZONE_RING = 0x01


def build_segment_color_ptreal(rgb: RGBColor, mask: int) -> list[int]:
    """Build the masked ptReal segment colour frame data (H1232, issue #223).

    Args:
        rgb: Target colour.
        mask: 4-byte little-endian segment bitmask, bit i = internal
              segment i+1 (bits 0-15 = ring, bit 16 = main panel; the
              H7038 uses bits 0-29 for its 30 bulbs, #310).

    Returns:
        Packet data bytes (before ``build_packet`` padding/checksum).
    """
    return [
        MUSIC_PACKET_PREFIX,  # 0x33
        SEGMENT_PTREAL_COMMAND,  # 0x05
        SEGMENT_PTREAL_INDICATOR,  # 0x15
        SEGMENT_PTREAL_COLOR_OP,  # 0x01
        rgb.r,
        rgb.g,
        rgb.b,
        0x00,
        0x00,
        0x00,
        0x00,
        0x00,
        mask & 0xFF,
        (mask >> 8) & 0xFF,
        (mask >> 16) & 0xFF,
        (mask >> 24) & 0xFF,
    ]


def build_segment_brightness_ptreal(pct: int, mask: int) -> list[int]:
    """Build the masked ptReal segment brightness frame data (H1232, issue #223).

    Args:
        pct: Brightness percentage, clamped to 0-100.
        mask: 3-byte little-endian segment bitmask, bit i = internal
              segment i+1 (bits 0-15 = ring, bit 16 = main panel).

    Returns:
        Packet data bytes (before ``build_packet`` padding/checksum).
    """
    pct = max(0, min(100, pct))
    return [
        MUSIC_PACKET_PREFIX,  # 0x33
        SEGMENT_PTREAL_COMMAND,  # 0x05
        SEGMENT_PTREAL_INDICATOR,  # 0x15
        SEGMENT_PTREAL_BRIGHTNESS_OP,  # 0x02
        pct,
        mask & 0xFF,
        (mask >> 8) & 0xFF,
        (mask >> 16) & 0xFF,
    ]


def build_segment_color_temp_ptreal(kelvin: int, white: RGBColor, mask: int) -> list[int]:
    """Build the masked ptReal segment colour-temperature frame data (H1252, issue #223).

    Same layout as ``build_segment_color_ptreal``, but the five bytes after
    the colour carry the colour temperature (big-endian Kelvin) and its RGB
    white point, with the colour itself full white. Verified on an H1252
    (6500 K and 2700 K on the main panel and the ring, from holger-dunst).

    Args:
        kelvin: Colour temperature, clamped to PTREAL_ZONE_MIN_KELVIN -
                PTREAL_ZONE_MAX_KELVIN.
        white: RGB approximation of ``kelvin``.
        mask: 4-byte little-endian segment bitmask, as for
              ``build_segment_color_ptreal``.

    Returns:
        Packet data bytes (before ``build_packet`` padding/checksum).
    """
    kelvin = max(PTREAL_ZONE_MIN_KELVIN, min(PTREAL_ZONE_MAX_KELVIN, int(kelvin)))
    return [
        MUSIC_PACKET_PREFIX,  # 0x33
        SEGMENT_PTREAL_COMMAND,  # 0x05
        SEGMENT_PTREAL_INDICATOR,  # 0x15
        SEGMENT_PTREAL_COLOR_OP,  # 0x01
        0xFF,
        0xFF,
        0xFF,
        (kelvin >> 8) & 0xFF,
        kelvin & 0xFF,
        white.r,
        white.g,
        white.b,
        mask & 0xFF,
        (mask >> 8) & 0xFF,
        (mask >> 16) & 0xFF,
        (mask >> 24) & 0xFF,
    ]


def build_light_zone_ptreal(zone: int, on: bool) -> list[int]:
    """Build the ptReal light-zone on/off frame data (H1252, issue #223).

    Args:
        zone: ``LIGHT_ZONE_MAIN`` (main panel) or ``LIGHT_ZONE_RING`` (ring).
        on: True to light the zone, False to switch it off.

    Returns:
        Packet data bytes (before ``build_packet`` padding/checksum).

    Raises:
        ValueError: ``zone`` is neither zone.
    """
    if zone not in (LIGHT_ZONE_MAIN, LIGHT_ZONE_RING):
        raise ValueError(f"unknown light zone {zone}")
    return [
        MUSIC_PACKET_PREFIX,  # 0x33
        LIGHT_ZONE_PTREAL_COMMAND,  # 0x30
        zone,
        0x01 if on else 0x00,
    ]


def build_ripple_flow_ptreal(speed: int) -> list[int]:
    """Build the H60B0 ripple flow-speed frame data: ``33 05 2c 01 03 <speed>``.

    From the H60B0 frame table posted on #211; verified on an H60B0 over
    LAN ptReal (2026-11): 10 slowed the ripple lens and 0 stopped it.

    Args:
        speed: Flow speed 0-100 (0 stops the ripple); clamped to that range.

    Returns:
        Packet data bytes (before ``build_packet`` padding/checksum).
    """
    return [MUSIC_PACKET_PREFIX, 0x05, 0x2C, 0x01, 0x03, max(0, min(100, int(speed)))]


def build_dreamview_packet() -> bytes:
    """Build the DreamView (video/camera sync) activation packet.

    Byte 2 of a ``0x33 0x05`` frame selects the colour operation mode, and
    video mode is ``0x00`` — see ``docs/govee-protocol-reference.md`` 6.4,
    which already documents ``0x00 = Video/DreamView mode``.

    This previously used ``0x04``, which is the *scene preset* sub-command:
    ``33 05 04 01`` is byte-for-byte the documented ``Scene(Sunset)`` frame,
    so "DreamView ON" put the device into a static orange scene instead of
    video sync.

    There is deliberately no "disable" counterpart: the protocol has no
    video-off opcode. A device leaves video mode by being given another
    mode, which the integration already does over the REST colour path.

    Returns:
        20-byte BLE packet that enables video mode.
    """
    # Packet: 33 05 00 [segments] [style] [saturation] 00...00 [XOR]
    data = [
        MUSIC_PACKET_PREFIX,  # 0x33 - Standard command prefix
        DREAMVIEW_COMMAND,  # 0x05 - Color/mode command
        DREAMVIEW_INDICATOR,  # 0x00 - Video mode indicator
        DREAMVIEW_SEGMENTS_ALL,
        DREAMVIEW_STYLE_MOVIE,
        DREAMVIEW_SATURATION_MAX,
    ]
    return build_packet(data)


def build_diy_scene_packet(scene_id: int) -> bytes:
    """Build DIY scene activation packet.

    Activates a saved DIY scene by ID. Uses the DIY mode indicator (0x0A)
    with the scene ID encoded as 4-byte little-endian.

    Args:
        scene_id: DIY scene ID from the API (e.g., 21104832).

    Returns:
        20-byte BLE packet for DIY scene activation.
    """
    # Encode scene_id as 4-byte little-endian
    id_bytes = scene_id.to_bytes(4, byteorder="little")

    # Packet: 33 05 0A [id_byte0] [id_byte1] [id_byte2] [id_byte3] 00...00 [XOR]
    data = [
        MUSIC_PACKET_PREFIX,  # 0x33 - Standard command prefix
        MUSIC_MODE_COMMAND,  # 0x05 - Color/mode command
        DIY_MODE_INDICATOR,  # 0x0A - DIY mode indicator
        id_bytes[0],
        id_bytes[1],
        id_bytes[2],
        id_bytes[3],
    ]

    return build_packet(data)


# Tower Fan oscillation (H7105/H7107 "Tower Fan 2" family). Reverse-engineered
# from homebridge-govee lib/device/fan-H7107.js (2026-08, hardware-confirmed).
# The dev-API oscillationToggle is a no-op for this family; only these raw
# ptReal/multiSync frames move the sweep motor. The ON frame carries 4
# "swing-range" bytes copied from the fan's own inbound aa1d status frame so the
# physically-configured arc is preserved; OFF is a bare zero-padded frame.
FAN_OSC_PTREAL_PREFIX = 0x33
FAN_OSC_MULTISYNC_PREFIX = 0x3A
FAN_OSC_COMMAND = 0x1D


def build_fan_oscillation_packet(
    enabled: bool,
    swing_tail: list[int] | None = None,
    *,
    prefix: int = FAN_OSC_PTREAL_PREFIX,
) -> bytes:
    """Build a Tower-Fan oscillation on/off packet.

    Args:
        enabled: True = oscillate (sweep), False = hold still.
        swing_tail: 4 swing-range bytes copied from the fan's aa1d report,
                    used only for the ON frame; None sends a bare ON.
        prefix: 0x33 for the ptReal frame, 0x3A for the multiSync twin.

    Returns:
        20-byte packet (19 data bytes + XOR checksum).
    """
    data = [prefix, FAN_OSC_COMMAND, 0x01 if enabled else 0x00]
    if enabled and swing_tail:
        data.extend(int(b) & 0xFF for b in swing_tail[:4])
    return build_packet(data)


def encode_packet_base64(packet: bytes) -> str:
    """Base64 encode a packet for ptReal command.

    Args:
        packet: Raw BLE packet bytes.

    Returns:
        Base64-encoded ASCII string.
    """
    return base64.b64encode(packet).decode("ascii")
