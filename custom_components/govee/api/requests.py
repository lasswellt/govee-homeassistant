"""TypedDict definitions for Govee API request payloads.

This module contains TypedDict definitions for all API request structures.
Each request type corresponds to a specific API endpoint and operation.

All requests follow the Govee API v2.0 format with requestId and payload.
"""

from __future__ import annotations

from typing import Any, TypedDict

from .types import CapabilityCommandDict


class DeviceIdentifier(TypedDict):
    """Device identifier used in request payloads.

    Example:
        {
            "sku": "H6160",
            "device": "AA:BB:CC:DD:EE:FF:GG:HH"
        }
    """

    sku: str  # Product model
    device: str  # MAC address / identifier


class DeviceStateRequestPayload(TypedDict):
    """Payload for device state query request.

    Used by: POST /device/state

    Example:
        {
            "requestId": "uuid-string",
            "payload": {
                "sku": "H6160",
                "device": "AA:BB:CC:DD:EE:FF:GG:HH"
            }
        }
    """

    requestId: str
    payload: DeviceIdentifier


class ControlRequestInnerPayload(TypedDict):
    """Inner payload for device control request.

    Example:
        {
            "sku": "H6160",
            "device": "AA:BB:CC:DD:EE:FF:GG:HH",
            "capability": {
                "type": "devices.capabilities.on_off",
                "instance": "powerSwitch",
                "value": 1
            }
        }
    """

    sku: str
    device: str
    capability: CapabilityCommandDict


class ControlRequestPayload(TypedDict):
    """Payload for device control request.

    Used by: POST /device/control

    Example:
        {
            "requestId": "uuid-string",
            "payload": {
                "sku": "H6160",
                "device": "AA:BB:CC:DD:EE:FF:GG:HH",
                "capability": {
                    "type": "devices.capabilities.on_off",
                    "instance": "powerSwitch",
                    "value": 1
                }
            }
        }
    """

    requestId: str
    payload: ControlRequestInnerPayload


class SceneRequestPayload(TypedDict):
    """Payload for scene query requests.

    Used by:
    - POST /device/scenes (dynamic scenes)
    - POST /device/diy-scenes (DIY scenes)

    Example:
        {
            "requestId": "uuid-string",
            "payload": {
                "sku": "H6160",
                "device": "AA:BB:CC:DD:EE:FF:GG:HH"
            }
        }
    """

    requestId: str
    payload: DeviceIdentifier


# Specific control value structures for type safety

class OnOffValue(TypedDict):
    """Value for on/off capability (not actually a dict, just int 0 or 1)."""


class BrightnessValue(TypedDict):
    """Value for brightness capability (not actually a dict, just int 0-100)."""


class ColorRGBValue(TypedDict):
    """Value for RGB color capability (not actually a dict, just int 24-bit RGB)."""


class ColorTempValue(TypedDict):
    """Value for color temperature capability (not actually a dict, just int Kelvin)."""


class SceneValue(TypedDict):
    """Value for scene capability.

    Example (dynamic scene):
        {
            "id": 1,
            "name": "Sunrise"
        }

    Example (DIY scene):
        123  # Just an integer ID
    """

    id: Any  # int or dict depending on scene type
    name: str | None  # Optional name


class SegmentColorValue(TypedDict):
    """Value for segment color control.

    Example:
        {
            "segment": [0],
            "rgb": 16711680
        }
    """

    segment: list[int]  # Segment indices
    rgb: int  # 24-bit RGB color


class SegmentBrightnessValue(TypedDict):
    """Value for segment brightness control.

    Example:
        {
            "segment": [0],
            "brightness": 75
        }
    """

    segment: list[int]  # Segment indices
    brightness: int  # Brightness 0-100


class MusicModeValue(TypedDict):
    """Value for music mode control.

    Example:
        {
            "musicMode": "Energic",
            "sensitivity": 50,
            "autoColor": 1,
            "color": 16711680
        }
    """

    musicMode: str  # Mode name
    sensitivity: int  # Sensitivity 0-100
    autoColor: int  # 0 or 1
    color: int | None  # 24-bit RGB when autoColor is 0
