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


# ==============================================================================
# Multi-Packet Scene Speed Protocol (0xA3)
# ==============================================================================

# Multi-packet constants
MULTI_PACKET_ID = 0xA3
MULTI_PACKET_FIRST_INDEX = 0x00
MULTI_PACKET_LAST_INDEX = 0xFF

# Scene activation constants
SCENE_ACTIVATION_PREFIX = 0x33
SCENE_ACTIVATION_COMMAND = 0x05
SCENE_ACTIVATION_INDICATOR = 0x04


def modify_scene_speed(scence_param_b64: str, speed_index: int, new_speed: int) -> bytes:
    """Decode scenceParam and modify the speed byte at speedIndex.

    The scenceParam is base64-encoded animation data from the light effect
    library. The speedIndex indicates which byte position contains the speed
    value that needs to be modified.

    Args:
        scence_param_b64: Base64-encoded scenceParam from light effect library.
        speed_index: Byte position of the speed value in the decoded data.
        new_speed: New speed value (will be clamped to valid range).

    Returns:
        Modified animation data as bytes.

    Raises:
        ValueError: If speed_index is out of bounds or base64 is invalid.
    """
    try:
        # Decode base64 to get raw animation data
        data = bytearray(base64.b64decode(scence_param_b64))
    except Exception as err:
        raise ValueError(f"Invalid base64 scenceParam: {err}") from err

    # Validate speed_index is within bounds
    if speed_index < 0 or speed_index >= len(data):
        raise ValueError(
            f"speedIndex {speed_index} out of bounds for data length {len(data)}"
        )

    # Clamp speed to valid range (typically 1-100 for regular scenes)
    new_speed = max(1, min(100, new_speed))

    # Modify the speed byte
    data[speed_index] = new_speed

    return bytes(data)


def build_multi_packet_sequence(data: bytes, scene_type: int = 2) -> list[bytes]:
    """Build 0xA3 multi-packet sequence for scene animation data.

    Multi-packet format:
    - First packet:  [0xA3, 0x00, count, scene_type, data[0:14]...]
    - Middle packet: [0xA3, index, data[offset:offset+17]...]
    - Last packet:   [0xA3, 0xFF, remaining_data...]

    Each packet is 20 bytes (19 data + 1 XOR checksum).

    Args:
        data: Animation data bytes to send.
        scene_type: Scene type indicator (default 2 for regular scenes).

    Returns:
        List of 20-byte packets ready for transmission.
    """
    packets: list[bytes] = []

    # Calculate how many packets we need
    # First packet has 14 bytes of data (after 0xA3, 0x00, count, scene_type)
    # Middle packets have 17 bytes of data (after 0xA3, index)
    # Last packet has remaining data (after 0xA3, 0xFF)

    if len(data) == 0:
        # Edge case: empty data, just send a minimal packet
        packets.append(build_packet([MULTI_PACKET_ID, MULTI_PACKET_FIRST_INDEX, 1, scene_type]))
        packets.append(build_packet([MULTI_PACKET_ID, MULTI_PACKET_LAST_INDEX]))
        return packets

    # First packet: 0xA3, 0x00, count, scene_type, data[0:14]
    first_data_size = 14
    first_chunk = data[:first_data_size]
    remaining = data[first_data_size:]

    # Calculate total packet count (including first and last)
    middle_data_size = 17
    if len(remaining) == 0:
        total_packets = 2  # Just first and last
    else:
        # Middle packets + last packet for remaining data
        middle_packet_count = (len(remaining) - 1) // middle_data_size + 1
        total_packets = 1 + middle_packet_count  # First + middle/last

    # Build first packet
    first_packet_data = [MULTI_PACKET_ID, MULTI_PACKET_FIRST_INDEX, total_packets, scene_type]
    first_packet_data.extend(first_chunk)
    packets.append(build_packet(first_packet_data))

    # Build middle packets (if needed)
    index = 1
    offset = 0
    while offset < len(remaining):
        chunk = remaining[offset : offset + middle_data_size]
        is_last = offset + middle_data_size >= len(remaining)

        if is_last:
            # Last packet uses 0xFF as index
            packet_data = [MULTI_PACKET_ID, MULTI_PACKET_LAST_INDEX]
        else:
            # Middle packet uses sequential index
            packet_data = [MULTI_PACKET_ID, index]

        packet_data.extend(chunk)
        packets.append(build_packet(packet_data))

        offset += middle_data_size
        index += 1

    # If we only had first packet data, add an empty last packet
    if len(remaining) == 0:
        packets.append(build_packet([MULTI_PACKET_ID, MULTI_PACKET_LAST_INDEX]))

    return packets


def build_scene_activation_packet(scene_code: int) -> bytes:
    """Build scene activation packet (0x33 0x05 0x04).

    This packet is sent after the multi-packet scene data to activate
    the scene on the device.

    Packet format:
    [0x33, 0x05, 0x04, code_lo, code_hi, 0x00...] + checksum

    Args:
        scene_code: Scene code from light effect library (16-bit value).

    Returns:
        20-byte BLE packet for scene activation.
    """
    # Extract low and high bytes of scene code
    code_lo = scene_code & 0xFF
    code_hi = (scene_code >> 8) & 0xFF

    # Build activation packet
    data = [
        SCENE_ACTIVATION_PREFIX,  # 0x33
        SCENE_ACTIVATION_COMMAND,  # 0x05
        SCENE_ACTIVATION_INDICATOR,  # 0x04
        code_lo,
        code_hi,
    ]

    return build_packet(data)
