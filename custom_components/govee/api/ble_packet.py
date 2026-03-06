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

# Music mode packet constants
MUSIC_PACKET_PREFIX = 0x33
MUSIC_MODE_COMMAND = 0x05
MUSIC_MODE_INDICATOR = 0x01

# DreamView (Movie Mode) packet constants
DREAMVIEW_COMMAND = 0x05  # Same as music mode command byte
DREAMVIEW_INDICATOR = 0x04  # Scene mode indicator (vs 0x01 for music)
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


def build_dreamview_packet(enabled: bool) -> bytes:
    """Build DreamView (Movie Mode) control packet.

    Uses the scene mode indicator (0x04) with on/off value.
    Follows same pattern as music mode but with different indicator.

    Args:
        enabled: True to enable DreamView, False to disable.

    Returns:
        20-byte BLE packet for DreamView command.
    """
    # Packet: 33 05 04 [enabled] 00...00 [XOR]
    data = [
        MUSIC_PACKET_PREFIX,  # 0x33 - Standard command prefix
        DREAMVIEW_COMMAND,  # 0x05 - Color/mode command
        DREAMVIEW_INDICATOR,  # 0x04 - Scene mode indicator
        0x01 if enabled else 0x00,  # Enabled state
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


def encode_packet_base64(packet: bytes) -> str:
    """Base64 encode a packet for ptReal command.

    Args:
        packet: Raw BLE packet bytes.

    Returns:
        Base64-encoded ASCII string.
    """
    return base64.b64encode(packet).decode("ascii")
