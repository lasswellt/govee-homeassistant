"""Brightness scaling between Home Assistant and Govee device ranges."""

from __future__ import annotations

from homeassistant.util.color import brightness_to_value, value_to_brightness


def ha_to_device_brightness(device_range: tuple[int, int], ha_brightness: int) -> int:
    """Convert HA brightness (1-255) to the device's range.

    Rounds rather than truncating so the pair with ``device_to_ha_brightness``
    is a true inverse: re-applying the reported brightness never drifts. A
    device range that starts at 0 is scaled from 1, because 0 means dark and a
    light that is on must never be sent it.
    """
    if ha_brightness <= 0:
        return device_range[0]
    low = max(device_range[0], 1)
    value = round(brightness_to_value((low, device_range[1]), ha_brightness))
    return max(low, min(device_range[1], value))


def device_to_ha_brightness(device_range: tuple[int, int], device_brightness: int) -> int:
    """Convert a device brightness to HA's 1-255 scale (see ``ha_to_device_brightness``)."""
    if device_range[1] <= device_range[0]:
        return 0
    low = max(device_range[0], 1)
    return value_to_brightness((low, device_range[1]), device_brightness)
