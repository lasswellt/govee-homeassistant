"""BLE packet construction for Govee devices.

Builds 20-byte BLE packets that can be sent via the AWS IoT MQTT
ptReal (passthrough real) command to control device features not
exposed via the REST API.

Packet format:
- Bytes 0-18: Command data (padded with 0x00)
- Byte 19: XOR checksum of bytes 0-18

DIY Speed packet:
- Byte 0: 0xA1 (DIY packet identifier)
- Byte 1: 0x02 (DIY command type)
- Byte 2: 0x01 (number of segments/modes)
- Byte 3: style (MUST match active scene: 0x00=Fade, 0x01=Jumping, etc.)
- Byte 4: 0x00 (mode - default)
- Byte 5: speed (0-100, where 0 is static and 100 is fastest)

NOTE: The style byte is critical. If it doesn't match the active DIY scene's
animation style, the speed command will be ignored by the device.

DIY Style packet:
- Byte 0: 0xA1 (DIY packet identifier)
- Byte 1: 0x02 (DIY command type)
- Byte 2: 0x01 (number of segments/modes)
- Byte 3: style (0x00=Fade, 0x01=Jumping, 0x02=Flicker, 0x03=Marquee, 0x04=Music)
- Byte 4: 0x00 (mode - default)
- Byte 5: speed (0-100, where 0 is static and 100 is fastest)

Music Mode packet:
- Byte 0: 0x33 (standard command prefix)
- Byte 1: 0x05 (color/mode command)
- Byte 2: 0x01 (music mode indicator)
- Byte 3: enabled (0x01=on, 0x00=off)
- Byte 4: sensitivity (0-100)
"""

from __future__ import annotations

import base64
from enum import IntEnum

# DIY packet constants
DIY_PACKET_ID = 0xA1
DIY_COMMAND = 0x02

# Music mode packet constants
MUSIC_PACKET_PREFIX = 0x33
MUSIC_MODE_COMMAND = 0x05
MUSIC_MODE_INDICATOR = 0x01


class DIYStyle(IntEnum):
    """DIY animation style options."""

    FADE = 0x00
    JUMPING = 0x01
    FLICKER = 0x02
    MARQUEE = 0x03
    MUSIC = 0x04


# Style name to enum mapping for select entity
DIY_STYLE_NAMES = {
    "Fade": DIYStyle.FADE,
    "Jumping": DIYStyle.JUMPING,
    "Flicker": DIYStyle.FLICKER,
    "Marquee": DIYStyle.MARQUEE,
    "Music": DIYStyle.MUSIC,
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


def build_diy_speed_packet(speed: int, style: int = 0) -> bytes:
    """Build DIY scene speed control packet.

    The style parameter is critical: the speed command is ignored if the
    style byte doesn't match the active DIY scene's animation style.
    For example, if the active scene uses "jumping" (style=1), sending
    a speed packet with style=0 (fade) will have no effect.

    Args:
        speed: Playback speed 0-100, where 0 is static (no animation)
               and 100 is the fastest playback speed.
        style: Animation style value (0=Fade, 1=Jumping, 2=Flicker,
               3=Marquee, 4=Music). Must match the active DIY scene's style.

    Returns:
        20-byte BLE packet for DIY speed command.
    """
    # Clamp values to valid ranges
    speed = max(0, min(100, speed))
    style = max(0, min(4, style))

    # Build command data
    # Packet: A1 02 [NUM] [STYLE] [MODE] [SPEED] ...
    data = [
        DIY_PACKET_ID,  # 0xA1 - DIY packet identifier
        DIY_COMMAND,  # 0x02 - DIY command type
        0x01,  # Number of segments/modes
        style,  # Style value (must match active scene)
        0x00,  # Mode (default)
        speed,  # Speed value 0-100
    ]

    return build_packet(data)


def build_diy_style_packet(style: int | DIYStyle, speed: int = 50) -> bytes:
    """Build DIY scene style control packet.

    Args:
        style: Animation style (0=Fade, 1=Jumping, 2=Flicker, 3=Marquee, 4=Music).
        speed: Playback speed 0-100, where 0 is static and 100 is fastest.

    Returns:
        20-byte BLE packet for DIY style command.
    """
    # Clamp values to valid ranges
    style_val = max(0, min(4, int(style)))
    speed = max(0, min(100, speed))

    # Build command data
    # Packet: A1 02 [NUM] [STYLE] [MODE] [SPEED] ...
    data = [
        DIY_PACKET_ID,  # 0xA1 - DIY packet identifier
        DIY_COMMAND,  # 0x02 - DIY command type
        0x01,  # Number of segments/modes
        style_val,  # Style value
        0x00,  # Mode (default)
        speed,  # Speed value 0-100
    ]

    return build_packet(data)


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


def build_scene_speed_packet(speed: int) -> bytes:
    """Build regular scene speed control packet.

    This packet adjusts the playback speed for regular (non-DIY) scenes
    that support speed control. Unlike DIY scenes, regular scenes use
    a simpler packet format that doesn't require matching the style byte.

    Based on Govee BLE protocol analysis from govee2mqtt and GlowFin projects.

    Args:
        speed: Playback speed 0-100, where lower values are slower
               and higher values are faster.

    Returns:
        20-byte BLE packet for scene speed command.
    """
    # Clamp speed to valid range
    speed = max(0, min(100, speed))

    # Build command data
    # Packet format: 33 05 02 [SPEED] ...
    # 0x33 = standard command prefix
    # 0x05 = scene/mode command
    # 0x02 = speed subcommand indicator
    data = [
        MUSIC_PACKET_PREFIX,  # 0x33 - Standard command prefix (same as music mode)
        MUSIC_MODE_COMMAND,  # 0x05 - Scene/mode command
        0x02,  # Speed subcommand indicator
        speed,  # Speed value 0-100
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
