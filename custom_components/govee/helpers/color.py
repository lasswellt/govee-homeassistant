"""Color conversion utilities for Govee integration.

Provides functions for converting between color representations:
- RGB tuples and 24-bit integers
- Kelvin color temperature to RGB approximation
"""

from __future__ import annotations


def rgb_to_int(r: int, g: int, b: int) -> int:
    """Convert RGB values to a 24-bit integer.

    Args:
        r: Red component (0-255)
        g: Green component (0-255)
        b: Blue component (0-255)

    Returns:
        24-bit integer representation (0xRRGGBB)

    Example:
        >>> rgb_to_int(255, 128, 64)
        16744512  # 0xFF8040
    """
    return (r << 16) | (g << 8) | b


def int_to_rgb(value: int) -> tuple[int, int, int]:
    """Convert a 24-bit integer to RGB tuple.

    Args:
        value: 24-bit integer (0xRRGGBB)

    Returns:
        Tuple of (r, g, b) values, each 0-255

    Example:
        >>> int_to_rgb(16744512)
        (255, 128, 64)
    """
    r = (value >> 16) & 0xFF
    g = (value >> 8) & 0xFF
    b = value & 0xFF
    return (r, g, b)


def kelvin_to_rgb(kelvin: int) -> tuple[int, int, int]:
    """Convert Kelvin color temperature to RGB approximation.

    Uses Tanner Helland's algorithm for color temperature conversion.
    This provides a reasonable approximation for UI display purposes.

    Args:
        kelvin: Color temperature in Kelvin (1000-40000)

    Returns:
        Tuple of (r, g, b) values, each 0-255

    Example:
        >>> kelvin_to_rgb(6500)  # Daylight
        (255, 249, 253)
        >>> kelvin_to_rgb(2700)  # Warm white
        (255, 166, 87)
    """
    # Clamp temperature to reasonable range
    temp = max(1000, min(40000, kelvin)) / 100

    # Calculate red
    if temp <= 66:
        red = 255.0
    else:
        red = temp - 60
        red = 329.698727446 * (red ** -0.1332047592)
        red = max(0.0, min(255.0, red))

    # Calculate green
    if temp <= 66:
        green = temp
        green = 99.4708025861 * (green ** 0.39) - 161.1195681661 if green > 0 else 0
    else:
        green = temp - 60
        green = 288.1221695283 * (green ** -0.0755148492)
    green = max(0, min(255, green))

    # Calculate blue
    if temp >= 66:
        blue = 255.0
    elif temp <= 19:
        blue = 0.0
    else:
        blue = temp - 10
        blue = 138.5177312231 * (blue ** 0.50) - 305.0447927307 if blue > 0 else 0.0
        blue = max(0.0, min(255.0, blue))

    return (int(red), int(green), int(blue))
