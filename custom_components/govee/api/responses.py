"""TypedDict definitions for Govee API response structures.

This module contains TypedDict definitions for all API response payloads.
Each response type corresponds to a specific API endpoint.

All responses follow the Govee API v2.0 format with code, message, and optional payload.
"""

from __future__ import annotations

from typing import TypedDict

from typing_extensions import NotRequired

from .types import DeviceCapabilityDict, ParametersDict, SceneOptionDict, StateCapabilityDict


class DeviceDict(TypedDict):
    """Device information from discovery endpoint.

    Example:
        {
            "device": "AA:BB:CC:DD:EE:FF:GG:HH",
            "sku": "H6160",
            "deviceName": "Living Room Light",
            "type": "devices.types.light",
            "capabilities": [
                {
                    "type": "devices.capabilities.on_off",
                    "instance": "powerSwitch"
                },
                {
                    "type": "devices.capabilities.range",
                    "instance": "brightness",
                    "parameters": {
                        "dataType": "INTEGER",
                        "range": {"min": 0, "max": 100, "precision": 1}
                    }
                }
            ],
            "version": "1.02.03"
        }
    """

    device: str  # MAC address / identifier
    sku: str  # Product model (e.g., "H6160")
    deviceName: str  # User-assigned name
    type: str  # Device type (e.g., "devices.types.light")
    capabilities: list[DeviceCapabilityDict]
    version: NotRequired[str]  # Firmware version


class DevicesResponsePayload(TypedDict):
    """Payload from get_devices endpoint.

    This is returned by the /devices endpoint.
    """

    # Note: The actual response is a list directly, not wrapped in an object
    # This type represents the unwrapped list of devices


class DeviceStatePayload(TypedDict):
    """Payload from get_device_state endpoint.

    Example:
        {
            "capabilities": [
                {
                    "type": "devices.capabilities.on_off",
                    "instance": "powerSwitch",
                    "state": {"value": 1}
                },
                {
                    "type": "devices.capabilities.range",
                    "instance": "brightness",
                    "state": {"value": 75}
                },
                {
                    "type": "devices.capabilities.color_setting",
                    "instance": "colorRgb",
                    "state": {"value": 16711680}
                }
            ]
        }
    """

    capabilities: list[StateCapabilityDict]


class ControlResponsePayload(TypedDict):
    """Payload from control_device endpoint.

    Example:
        {}

    Note: Control commands typically return empty payload on success.
    """


class SceneCapabilityDict(TypedDict):
    """Capability containing scene options in scene query responses.

    Example:
        {
            "type": "devices.capabilities.dynamic_scene",
            "instance": "lightScene",
            "parameters": {
                "dataType": "ENUM",
                "options": [
                    {"name": "Sunrise", "value": {"id": 1, "name": "Sunrise"}},
                    {"name": "Sunset", "value": {"id": 2, "name": "Sunset"}}
                ]
            }
        }
    """

    type: str
    instance: str
    parameters: ParametersDict


class DynamicScenesPayload(TypedDict):
    """Payload from get_dynamic_scenes endpoint.

    Example:
        {
            "capabilities": [
                {
                    "type": "devices.capabilities.dynamic_scene",
                    "instance": "lightScene",
                    "parameters": {
                        "dataType": "ENUM",
                        "options": [
                            {"name": "Sunrise", "value": {"id": 1, "name": "Sunrise"}},
                            {"name": "Sunset", "value": {"id": 2, "name": "Sunset"}}
                        ]
                    }
                }
            ]
        }
    """

    capabilities: list[SceneCapabilityDict]


class DIYScenesPayload(TypedDict):
    """Payload from get_diy_scenes endpoint.

    Example:
        {
            "capabilities": [
                {
                    "type": "devices.capabilities.dynamic_scene",
                    "instance": "diyScene",
                    "parameters": {
                        "dataType": "ENUM",
                        "options": [
                            {"name": "My Scene 1", "value": 1},
                            {"name": "My Scene 2", "value": 2}
                        ]
                    }
                }
            ]
        }
    """

    capabilities: list[SceneCapabilityDict]


class ApiResponseBase(TypedDict):
    """Base structure for all API responses.

    Example (success):
        {
            "code": 200,
            "message": "Success",
            "payload": {...}
        }

    Example (error):
        {
            "code": 400,
            "message": "Invalid request"
        }
    """

    code: int
    message: str
    payload: NotRequired[dict[str, object]]  # Type varies by endpoint


class DevicesResponse(ApiResponseBase):
    """Response from GET /devices endpoint.

    Example:
        {
            "code": 200,
            "message": "Success",
            "data": [
                {
                    "device": "AA:BB:CC:DD:EE:FF:GG:HH",
                    "sku": "H6160",
                    "deviceName": "Living Room Light",
                    "type": "devices.types.light",
                    "capabilities": [...]
                }
            ]
        }

    Note: Uses 'data' instead of 'payload' for device list.
    """

    data: NotRequired[list[DeviceDict]]


class DeviceStateResponse(ApiResponseBase):
    """Response from POST /device/state endpoint.

    Example:
        {
            "code": 200,
            "message": "Success",
            "payload": {
                "capabilities": [
                    {
                        "type": "devices.capabilities.on_off",
                        "instance": "powerSwitch",
                        "state": {"value": 1}
                    }
                ]
            }
        }
    """

    payload: NotRequired[DeviceStatePayload]


class ControlResponse(ApiResponseBase):
    """Response from POST /device/control endpoint.

    Example:
        {
            "code": 200,
            "message": "Success",
            "payload": {}
        }
    """

    payload: NotRequired[ControlResponsePayload]


class DynamicScenesResponse(ApiResponseBase):
    """Response from POST /device/scenes endpoint.

    Example:
        {
            "code": 200,
            "message": "Success",
            "payload": {
                "capabilities": [
                    {
                        "type": "devices.capabilities.dynamic_scene",
                        "instance": "lightScene",
                        "parameters": {
                            "dataType": "ENUM",
                            "options": [...]
                        }
                    }
                ]
            }
        }
    """

    payload: NotRequired[DynamicScenesPayload]


class DIYScenesResponse(ApiResponseBase):
    """Response from POST /device/diy-scenes endpoint.

    Example:
        {
            "code": 200,
            "message": "Success",
            "payload": {
                "capabilities": [
                    {
                        "type": "devices.capabilities.dynamic_scene",
                        "instance": "diyScene",
                        "parameters": {
                            "dataType": "ENUM",
                            "options": [...]
                        }
                    }
                ]
            }
        }
    """

    payload: NotRequired[DIYScenesPayload]
