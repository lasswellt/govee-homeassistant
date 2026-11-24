"""Native MQTT control payload builders.

Maps domain command objects to Govee's native MQTT command payloads
(``turn`` / ``brightness`` / ``colorwc``), encoding documented device
quirks. Pure functions — no I/O — so they are trivially unit-testable and
free of aiohttp/mqtt dependencies (Clean Architecture: domain-adjacent).

Reference: docs/govee-protocol-reference.md §3.5-3.8 and
docs/_research/2026-06-04_mqtt-push-control-latency.md.
"""

from __future__ import annotations

from typing import Any

from ..models.commands import (
    BrightnessCommand,
    ColorCommand,
    DeviceCommand,
    PowerCommand,
)

# SKUs whose power values are 17 (on) / 16 (off) instead of 1 / 0.
# Source: docs/govee-protocol-reference.md, AWS IoT "Device-Specific Quirks"
# (outgoing commands only; whether pushes carry 16/17 is unconfirmed).
POWER_QUIRK_SKUS = frozenset({"H5080", "H5083"})


def build_turn_data(power_on: bool, sku: str) -> dict[str, Any]:
    """Build the ``data`` payload for a native ``turn`` command.

    H5080/H5083 use 17 (on) / 16 (off); all other SKUs use 1 / 0.
    """
    if sku in POWER_QUIRK_SKUS:
        return {"val": 17 if power_on else 16}
    return {"val": 1 if power_on else 0}


def build_brightness_data(brightness_1_100: int) -> dict[str, Any]:
    """Build the ``data`` payload for a native ``brightness`` command.

    Args:
        brightness_1_100: Brightness on Govee's 1-100 scale (the caller is
            responsible for mapping HA's 0-255 range before calling).
    """
    return {"val": brightness_1_100}


def device_brightness_to_mqtt(brightness: int, brightness_range: tuple[int, int] = (0, 100)) -> int:
    """Map a device-native brightness onto the 1-100 scale ``brightness`` takes.

    ``BrightnessCommand.brightness`` is device-native (most lights advertise
    0-100 or 1-100, a few 0-254). A range that already fits in 0-100 passes
    through untouched so the common case sends exactly what the REST path
    would; a wider range is rescaled, mirroring the LAN path.
    """
    low, high = brightness_range
    if high > 100 and high > low:
        brightness = round((brightness - low) / (high - low) * 100)
    return max(1, min(100, int(brightness)))


def build_color_data(r: int, g: int, b: int) -> dict[str, Any]:
    """Build the ``data`` payload for a native ``colorwc`` command (preferred)."""
    return {"color": {"r": r, "g": g, "b": b}, "colorTemInKelvin": 0}


def build_color_legacy_data(r: int, g: int, b: int) -> dict[str, Any]:
    """Build the ``data`` payload for the legacy ``color`` command.

    Used as a fallback for older devices that do not respond to ``colorwc``.
    Sent with ``cmdVersion=1``.
    """
    return {"r": r, "g": g, "b": b}


def color_legacy_followup(
    command: DeviceCommand,
) -> tuple[str, dict[str, Any], int] | None:
    """Return the legacy ``color`` publish that should follow a ``colorwc``.

    Some older devices only act on the legacy ``color`` command and ignore
    ``colorwc`` entirely (docs/govee-protocol-reference.md §"Some older
    devices"). An MQTT publish is unacknowledged, so there is no reply to
    branch on — but both forms carry the same RGB triplet, so sending the
    legacy one as a follow-up is harmless for devices that already honoured
    ``colorwc`` and is the whole fix for devices that didn't (issues #149,
    #158).

    Returns ``None`` for any command that isn't a colour write.
    """
    if not isinstance(command, ColorCommand):
        return None
    color = command.color
    return ("color", build_color_legacy_data(color.r, color.g, color.b), 1)


def command_to_mqtt(
    command: DeviceCommand, sku: str, brightness_range: tuple[int, int] = (0, 100)
) -> tuple[str, dict[str, Any], int] | None:
    """Map a command to ``(cmd, data, cmd_version)`` for native MQTT control.

    Returns ``None`` for commands that have no native MQTT representation
    (color temperature, scenes, segments, etc.), signalling the caller to
    fall back to the REST control path. ``brightness_range`` is the device's
    native range, used to put brightness on the 1-100 MQTT scale.
    """
    if isinstance(command, PowerCommand):
        return ("turn", build_turn_data(command.power_on, sku), 0)
    if isinstance(command, BrightnessCommand):
        return (
            "brightness",
            build_brightness_data(device_brightness_to_mqtt(command.brightness, brightness_range)),
            0,
        )
    if isinstance(command, ColorCommand):
        color = command.color
        return ("colorwc", build_color_data(color.r, color.g, color.b), 0)
    return None
