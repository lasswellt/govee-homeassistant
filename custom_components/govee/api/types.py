"""Shared TypedDict definitions for Govee API structures.

This module contains TypedDict definitions for nested structures that are reused
across API requests and responses. These types provide static typing for the
Govee API v2.0 protocol structures.

Example JSON structures are included in docstrings to document the expected format.
"""

from __future__ import annotations

from typing import Any, TypedDict

from typing_extensions import NotRequired


class RangeDict(TypedDict):
    """Range specification for capability parameters.

    Example:
        {
            "min": 0,
            "max": 100,
            "precision": 1
        }
    """

    min: int
    max: int
    precision: NotRequired[int]


class OptionDict(TypedDict):
    """Option specification for ENUM capability parameters.

    Example:
        {
            "name": "Energic",
            "value": "energic"
        }
    """

    name: str
    value: Any  # Can be int, str, or dict depending on capability type


class FieldDict(TypedDict):
    """Field specification for STRUCT capability parameters.

    Example:
        {
            "fieldName": "segment",
            "type": "INTEGER",
            "elementRange": {"min": 0, "max": 14}
        }
    """

    fieldName: str
    type: str
    elementRange: NotRequired[RangeDict]


class ParametersDict(TypedDict):
    """Parameters for a device capability.

    Example (RANGE):
        {
            "dataType": "INTEGER",
            "range": {"min": 0, "max": 100, "precision": 1}
        }

    Example (ENUM):
        {
            "dataType": "ENUM",
            "options": [
                {"name": "Energic", "value": "energic"},
                {"name": "Rhythm", "value": "rhythm"}
            ]
        }

    Example (STRUCT):
        {
            "dataType": "STRUCT",
            "fields": [
                {
                    "fieldName": "segment",
                    "type": "INTEGER",
                    "elementRange": {"min": 0, "max": 14}
                }
            ]
        }
    """

    dataType: str  # "INTEGER", "ENUM", "STRUCT", etc.
    range: NotRequired[RangeDict]
    options: NotRequired[list[OptionDict]]
    fields: NotRequired[list[FieldDict]]


class CapabilityStateDict(TypedDict):
    """State value for a capability in device state response.

    Example:
        {
            "value": 1
        }
    """

    value: Any  # Type depends on capability (int, str, dict, etc.)


class StateCapabilityDict(TypedDict):
    """Capability in device state response.

    Example:
        {
            "type": "devices.capabilities.on_off",
            "instance": "powerSwitch",
            "state": {"value": 1}
        }
    """

    type: str
    instance: str
    state: CapabilityStateDict


class DeviceCapabilityDict(TypedDict):
    """Device capability specification from device discovery.

    Example:
        {
            "type": "devices.capabilities.range",
            "instance": "brightness",
            "parameters": {
                "dataType": "INTEGER",
                "range": {"min": 0, "max": 100, "precision": 1}
            }
        }
    """

    type: str
    instance: str
    parameters: NotRequired[ParametersDict]


class SceneOptionDict(TypedDict):
    """Scene option from scene query response.

    Example (dynamic scene):
        {
            "name": "Sunrise",
            "value": {"id": 1, "name": "Sunrise"}
        }

    Example (DIY scene):
        {
            "name": "My Custom Scene",
            "value": 123
        }
    """

    name: str
    value: Any  # int for DIY scenes, dict for dynamic scenes
    category: NotRequired[str]


class CapabilityCommandDict(TypedDict):
    """Capability command for device control requests.

    Example (on/off):
        {
            "type": "devices.capabilities.on_off",
            "instance": "powerSwitch",
            "value": 1
        }

    Example (RGB color):
        {
            "type": "devices.capabilities.color_setting",
            "instance": "colorRgb",
            "value": 16711680
        }

    Example (segment color):
        {
            "type": "devices.capabilities.segment_color_setting",
            "instance": "segmentedColorRgb",
            "value": {
                "segment": [0],
                "rgb": 16711680
            }
        }
    """

    type: str
    instance: str
    value: Any  # Type depends on capability
