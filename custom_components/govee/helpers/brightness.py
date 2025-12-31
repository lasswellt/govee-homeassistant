"""Brightness conversion utilities for Govee integration.

Handles conversion between Home Assistant brightness (0-255) and
Govee API brightness values, which vary by device:
- Most devices: 0-100
- Some devices: 0-254

The device_range parameter specifies the API's brightness range.
"""

from __future__ import annotations


def brightness_to_api(
    brightness: int,
    device_range: tuple[int, int] = (0, 100),
) -> int:
    """Convert Home Assistant brightness to Govee API value.

    Args:
        brightness: Home Assistant brightness (0-255)
        device_range: Tuple of (min, max) for device brightness range

    Returns:
        API brightness value scaled to device range

    Example:
        >>> brightness_to_api(255)  # Max brightness
        100
        >>> brightness_to_api(127)  # Half brightness
        50
        >>> brightness_to_api(255, (0, 254))  # Different device range
        254
    """
    if brightness <= 0:
        return device_range[0]
    if brightness >= 255:
        return device_range[1]

    # Scale from 0-255 to device range
    range_size = device_range[1] - device_range[0]
    scaled = round((brightness / 255) * range_size) + device_range[0]
    return max(device_range[0], min(device_range[1], scaled))


def brightness_from_api(
    api_value: int,
    device_range: tuple[int, int] = (0, 100),
) -> int:
    """Convert Govee API brightness value to Home Assistant brightness.

    Args:
        api_value: API brightness value
        device_range: Tuple of (min, max) for device brightness range

    Returns:
        Home Assistant brightness (0-255)

    Example:
        >>> brightness_from_api(100)  # Max API brightness
        255
        >>> brightness_from_api(50)  # Half API brightness
        127
        >>> brightness_from_api(254, (0, 254))  # Different device range
        255
    """
    if api_value <= device_range[0]:
        return 0
    if api_value >= device_range[1]:
        return 255

    # Scale from device range to 0-255
    range_size = device_range[1] - device_range[0]
    if range_size == 0:
        return 255 if api_value > device_range[0] else 0

    normalized = (api_value - device_range[0]) / range_size
    return round(normalized * 255)
