"""Native LAN control payload builders.

Maps domain command objects to Govee's LAN (UDP) command payloads
(``turn`` / ``brightness``), mirroring :mod:`mqtt_control` but encoding the
LAN protocol's own conventions. Pure functions — no I/O — so they are
trivially unit-testable and free of socket/asyncio dependencies (Clean
Architecture: domain-adjacent).

Scope covers **power**, **brightness**, **colour** and **colour temperature** —
exactly the four readable fields of the LAN ``devStatus`` reply (``onOff``,
``brightness`` 0-100, ``color``, ``colorTemInKelvin``), so every one of them
can be verified by reading the device back. Colour was originally left out
because a device that ignores a ``colorwc`` write still answers the read; that
is handled by comparing the *reported value*, so an ignored write simply fails
to confirm and falls through to REST. Routing colour over LAN matters for the
strips whose colour the Govee cloud accepts with HTTP 200 and then never
delivers (issues #149, #158).

Everything else (scenes, segments, music, DIY, work modes, toggles) has no LAN
representation and keeps using REST. The H5080/H5083 power-quirk SKUs that
:mod:`mqtt_control` special-cases (17/16 power encoding) also fall back to REST
so their quirk is honoured.

Two protocol differences from MQTT are encoded here:

* LAN power uses the key ``value`` with ``1``/``0`` (not MQTT's ``val`` with
  the 17/16 quirk).
* LAN brightness is always on a 0-100 scale, so device-native brightness is
  scaled into that range before being sent.

Reference: docs/govee-protocol-reference.md and the Sprint-6 LAN design.
"""

from __future__ import annotations

from typing import Any

from ..const import LAN_POWER_OFF_EXCLUDED_SKUS
from ..models.commands import (
    BrightnessCommand,
    ColorCommand,
    ColorTempCommand,
    DeviceCommand,
    PowerCommand,
)
from .mqtt_control import POWER_QUIRK_SKUS

# Govee LAN brightness is always reported and accepted on a 0-100 scale.
LAN_BRIGHTNESS_MIN = 0
LAN_BRIGHTNESS_MAX = 100


def _clamp(value: int, low: int, high: int) -> int:
    """Clamp ``value`` to the inclusive ``[low, high]`` range."""
    return max(low, min(high, value))


def device_brightness_to_lan(native: int, brightness_range: tuple[int, int]) -> int:
    """Scale a device-native brightness down to the LAN 0-100 scale.

    Identity (clamped to 0-100) when the device's brightness range tops out at
    100, since the LAN scale already matches the device scale. Otherwise the
    value is rescaled proportionally across ``brightness_range``.

    Args:
        native: Brightness on the device-native scale (within
            ``brightness_range``).
        brightness_range: The device's ``(min, max)`` brightness bounds.

    Returns:
        Brightness on the LAN 0-100 scale.
    """
    minimum, maximum = brightness_range
    if maximum == LAN_BRIGHTNESS_MAX:
        return _clamp(native, LAN_BRIGHTNESS_MIN, LAN_BRIGHTNESS_MAX)
    span = maximum - minimum
    if span <= 0:
        return LAN_BRIGHTNESS_MIN
    ratio = (native - minimum) / span
    scaled = round(ratio * LAN_BRIGHTNESS_MAX)
    return _clamp(scaled, LAN_BRIGHTNESS_MIN, LAN_BRIGHTNESS_MAX)


def lan_brightness_to_device(v_0_100: int, brightness_range: tuple[int, int]) -> int:
    """Scale a LAN 0-100 brightness up to the device-native scale.

    Inverse of :func:`device_brightness_to_lan`: identity (clamped to 0-100)
    when the device range tops out at 100, otherwise rescaled across
    ``brightness_range``. Round-trips through :func:`device_brightness_to_lan`
    are stable from the LAN side (the coarser 0-100 domain).

    Args:
        v_0_100: Brightness on the LAN 0-100 scale.
        brightness_range: The device's ``(min, max)`` brightness bounds.

    Returns:
        Brightness on the device-native scale.
    """
    minimum, maximum = brightness_range
    if maximum == LAN_BRIGHTNESS_MAX:
        return _clamp(v_0_100, LAN_BRIGHTNESS_MIN, LAN_BRIGHTNESS_MAX)
    span = maximum - minimum
    if span <= 0:
        return minimum
    ratio = _clamp(v_0_100, LAN_BRIGHTNESS_MIN, LAN_BRIGHTNESS_MAX) / LAN_BRIGHTNESS_MAX
    scaled = round(minimum + ratio * span)
    return _clamp(scaled, minimum, maximum)


def command_to_lan(
    command: DeviceCommand, sku: str, brightness_range: tuple[int, int]
) -> tuple[str, dict[str, Any]] | None:
    """Map a command to ``(cmd, data)`` for native LAN control.

    Power, brightness, colour and colour temperature are routed over LAN.
    Returns ``None`` for every other command (scenes, segments, music, DIY,
    work modes, toggles), signalling the caller to fall back to the REST
    control path. ``None`` is also returned for the H5080/H5083 power-quirk
    SKUs so their 17/16 power encoding (honoured only by the REST/MQTT path)
    is preserved. Power-off is likewise ``None`` for
    ``LAN_POWER_OFF_EXCLUDED_SKUS`` (H60A6 reboots on LAN off, #308).

    Colour and colour temperature share the one ``colorwc`` command. The
    protocol expects the unused half zeroed: an RGB write sends
    ``colorTemInKelvin: 0``, and a colour-temperature write sends a zeroed
    ``color`` triplet — sending both makes the firmware pick one arbitrarily.

    Args:
        command: The domain command to translate.
        sku: The device SKU (model), used to detect power-quirk SKUs.
        brightness_range: The device's ``(min, max)`` brightness bounds, used
            to scale brightness into the LAN 0-100 range.

    Returns:
        A ``(cmd, data)`` tuple for the LAN protocol, or ``None`` if the
        command has no safe LAN representation.
    """
    if isinstance(command, PowerCommand):
        if sku in POWER_QUIRK_SKUS:
            return None
        if not command.power_on and sku.upper() in LAN_POWER_OFF_EXCLUDED_SKUS:
            return None
        return ("turn", {"value": 1 if command.power_on else 0})
    if isinstance(command, BrightnessCommand):
        lan_value = device_brightness_to_lan(command.brightness, brightness_range)
        return ("brightness", {"value": lan_value})
    if isinstance(command, ColorCommand):
        color = command.color
        return (
            "colorwc",
            {
                "color": {"r": color.r, "g": color.g, "b": color.b},
                "colorTemInKelvin": 0,
            },
        )
    if isinstance(command, ColorTempCommand):
        return (
            "colorwc",
            {
                "color": {"r": 0, "g": 0, "b": 0},
                "colorTemInKelvin": command.kelvin,
            },
        )
    return None
