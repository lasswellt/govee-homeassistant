"""Test fixtures for Govee integration tests."""

from __future__ import annotations

from collections.abc import Generator
from typing import Any
from unittest.mock import AsyncMock

import pytest

from custom_components.govee.api import (
    GoveeApiClient,
    GoveeIotCredentials,
)
from custom_components.govee.models import (
    GoveeCapability,
    GoveeDevice,
    GoveeDeviceState,
    RGBColor,
)
from custom_components.govee.models.device import (
    CAPABILITY_COLOR_SETTING,
    CAPABILITY_DYNAMIC_SCENE,
    CAPABILITY_MODE,
    CAPABILITY_ON_OFF,
    CAPABILITY_RANGE,
    CAPABILITY_SEGMENT_COLOR,
    CAPABILITY_TOGGLE,
    CAPABILITY_WORK_MODE,
    INSTANCE_BRIGHTNESS,
    INSTANCE_COLOR_RGB,
    INSTANCE_COLOR_TEMP,
    INSTANCE_HDMI_SOURCE,
    INSTANCE_OSCILLATION,
    INSTANCE_POWER,
    INSTANCE_SCENE,
    INSTANCE_WORK_MODE,
)

# Capability constants for test devices
DEVICE_TYPE_LIGHT = "devices.types.light"
DEVICE_TYPE_PLUG = "devices.types.socket"
DEVICE_TYPE_FAN = "devices.types.fan"


@pytest.fixture
def mock_api_client() -> Generator[AsyncMock, None, None]:
    """Create a mock API client."""
    client = AsyncMock(spec=GoveeApiClient)
    client.rate_limit_remaining = 100
    client.rate_limit_total = 100
    client.rate_limit_reset = 0
    client.get_devices = AsyncMock(return_value=[])
    client.get_device_state = AsyncMock()
    client.control_device = AsyncMock(return_value=True)
    client.get_dynamic_scenes = AsyncMock(return_value=[])
    client.close = AsyncMock()
    yield client


@pytest.fixture
def mock_iot_credentials() -> GoveeIotCredentials:
    """Create mock IoT credentials."""
    return GoveeIotCredentials(
        token="test_token",
        refresh_token="test_refresh",
        account_topic="GA/test_account",
        iot_cert="-----BEGIN CERTIFICATE-----\ntest\n-----END CERTIFICATE-----",
        iot_key="-----BEGIN PRIVATE KEY-----\ntest\n-----END PRIVATE KEY-----",
        iot_ca=None,
        client_id="AP/12345/testclient",
        endpoint="test.iot.amazonaws.com",
    )


@pytest.fixture
def light_capabilities() -> tuple[GoveeCapability, ...]:
    """Create capabilities for a typical light device."""
    return (
        GoveeCapability(
            type=CAPABILITY_ON_OFF,
            instance=INSTANCE_POWER,
            parameters={},
        ),
        GoveeCapability(
            type=CAPABILITY_RANGE,
            instance=INSTANCE_BRIGHTNESS,
            parameters={"range": {"min": 0, "max": 100}},
        ),
        GoveeCapability(
            type=CAPABILITY_COLOR_SETTING,
            instance=INSTANCE_COLOR_RGB,
            parameters={},
        ),
        GoveeCapability(
            type=CAPABILITY_COLOR_SETTING,
            instance=INSTANCE_COLOR_TEMP,
            parameters={"range": {"min": 2000, "max": 9000}},
        ),
        GoveeCapability(
            type=CAPABILITY_DYNAMIC_SCENE,
            instance=INSTANCE_SCENE,
            parameters={},
        ),
    )


@pytest.fixture
def rgbic_capabilities(light_capabilities) -> tuple[GoveeCapability, ...]:
    """Create capabilities for an RGBIC device.

    Matches real API response structure with fields/elementRange.
    """
    return light_capabilities + (
        GoveeCapability(
            type=CAPABILITY_SEGMENT_COLOR,
            instance="segmentedColorRgb",
            parameters={
                "dataType": "STRUCT",
                "fields": [
                    {
                        "fieldName": "segment",
                        "size": {"min": 1, "max": 15},
                        "dataType": "Array",
                        "elementRange": {"min": 0, "max": 14},
                        "elementType": "INTEGER",
                        "required": True,
                    },
                    {
                        "fieldName": "rgb",
                        "dataType": "INTEGER",
                        "range": {"min": 0, "max": 16777215},
                        "required": True,
                    },
                ],
            },
        ),
    )


@pytest.fixture
def plug_capabilities() -> tuple[GoveeCapability, ...]:
    """Create capabilities for a smart plug."""
    return (
        GoveeCapability(
            type=CAPABILITY_ON_OFF,
            instance=INSTANCE_POWER,
            parameters={},
        ),
    )


@pytest.fixture
def mock_light_device(light_capabilities) -> GoveeDevice:
    """Create a mock light device."""
    return GoveeDevice(
        device_id="AA:BB:CC:DD:EE:FF:00:11",
        sku="H6072",
        name="Living Room Light",
        device_type=DEVICE_TYPE_LIGHT,
        capabilities=light_capabilities,
        is_group=False,
    )


@pytest.fixture
def mock_rgbic_device(rgbic_capabilities) -> GoveeDevice:
    """Create a mock RGBIC LED strip device."""
    return GoveeDevice(
        device_id="AA:BB:CC:DD:EE:FF:00:22",
        sku="H6167",
        name="Bedroom LED Strip",
        device_type=DEVICE_TYPE_LIGHT,
        capabilities=rgbic_capabilities,
        is_group=False,
    )


@pytest.fixture
def mock_plug_device(plug_capabilities) -> GoveeDevice:
    """Create a mock smart plug device."""
    return GoveeDevice(
        device_id="AA:BB:CC:DD:EE:FF:00:33",
        sku="H5080",
        name="Office Plug",
        device_type=DEVICE_TYPE_PLUG,
        capabilities=plug_capabilities,
        is_group=False,
    )


@pytest.fixture
def mock_group_device(light_capabilities) -> GoveeDevice:
    """Create a mock group device."""
    return GoveeDevice(
        device_id="GROUP:AA:BB:CC:DD:EE:FF",
        sku="GROUP",
        name="All Lights",
        device_type="devices.types.group",
        capabilities=light_capabilities,
        is_group=True,
    )


@pytest.fixture
def mock_device_state() -> GoveeDeviceState:
    """Create a mock device state."""
    return GoveeDeviceState(
        device_id="AA:BB:CC:DD:EE:FF:00:11",
        online=True,
        power_state=True,
        brightness=75,
        color=RGBColor(r=255, g=128, b=64),
        color_temp_kelvin=None,
        active_scene=None,
        source="api",
    )


@pytest.fixture
def mock_device_state_off() -> GoveeDeviceState:
    """Create a mock device state (off)."""
    return GoveeDeviceState(
        device_id="AA:BB:CC:DD:EE:FF:00:11",
        online=True,
        power_state=False,
        brightness=0,
        color=None,
        color_temp_kelvin=None,
        active_scene=None,
        source="api",
    )


@pytest.fixture
def mock_scenes() -> list[dict[str, Any]]:
    """Create mock scene data."""
    return [
        {"name": "Sunrise", "value": {"id": 1}},
        {"name": "Sunset", "value": {"id": 2}},
        {"name": "Party", "value": {"id": 3}},
        {"name": "Movie", "value": {"id": 4}},
    ]


@pytest.fixture
def api_device_response() -> dict[str, Any]:
    """Create a mock API device response."""
    return {
        "device": "AA:BB:CC:DD:EE:FF:00:11",
        "sku": "H6072",
        "deviceName": "Living Room Light",
        "type": "devices.types.light",
        "capabilities": [
            {"type": CAPABILITY_ON_OFF, "instance": INSTANCE_POWER, "parameters": {}},
            {
                "type": CAPABILITY_RANGE,
                "instance": INSTANCE_BRIGHTNESS,
                "parameters": {"range": {"min": 0, "max": 100}},
            },
            {"type": CAPABILITY_COLOR_SETTING, "instance": INSTANCE_COLOR_RGB, "parameters": {}},
        ],
    }


@pytest.fixture
def api_state_response() -> dict[str, Any]:
    """Create a mock API state response."""
    return {
        "capabilities": [
            {
                "type": "devices.capabilities.online",
                "instance": "online",
                "state": {"value": True},
            },
            {
                "type": CAPABILITY_ON_OFF,
                "instance": INSTANCE_POWER,
                "state": {"value": 1},
            },
            {
                "type": CAPABILITY_RANGE,
                "instance": INSTANCE_BRIGHTNESS,
                "state": {"value": 75},
            },
            {
                "type": CAPABILITY_COLOR_SETTING,
                "instance": INSTANCE_COLOR_RGB,
                "state": {"value": 16744512},  # RGB(255, 128, 64)
            },
        ],
    }


@pytest.fixture
def mqtt_state_message() -> dict[str, Any]:
    """Create a mock MQTT state message."""
    return {
        "device": "AA:BB:CC:DD:EE:FF:00:11",
        "sku": "H6072",
        "state": {
            "onOff": 1,
            "brightness": 75,
            "color": {"r": 255, "g": 128, "b": 64},
            "colorTemInKelvin": 0,
        },
    }


@pytest.fixture
def fan_capabilities() -> tuple[GoveeCapability, ...]:
    """Create capabilities for a fan device (H7101)."""
    return (
        GoveeCapability(
            type=CAPABILITY_ON_OFF,
            instance=INSTANCE_POWER,
            parameters={},
        ),
        GoveeCapability(
            type=CAPABILITY_TOGGLE,
            instance=INSTANCE_OSCILLATION,
            parameters={},
        ),
        GoveeCapability(
            type=CAPABILITY_WORK_MODE,
            instance=INSTANCE_WORK_MODE,
            parameters={
                "options": [
                    {"name": "gearMode", "value": {"workMode": 1, "modeValue": [1, 2, 3]}},
                    {"name": "Auto", "value": {"workMode": 3, "modeValue": [0]}},
                ],
            },
        ),
    )


@pytest.fixture
def mock_fan_device(fan_capabilities) -> GoveeDevice:
    """Create a mock fan device (H7101)."""
    return GoveeDevice(
        device_id="AA:BB:CC:DD:EE:FF:00:44",
        sku="H7101",
        name="Living Room Fan",
        device_type=DEVICE_TYPE_FAN,
        capabilities=fan_capabilities,
        is_group=False,
    )


@pytest.fixture
def mock_fan_device_state() -> GoveeDeviceState:
    """Create a mock fan device state."""
    state = GoveeDeviceState(
        device_id="AA:BB:CC:DD:EE:FF:00:44",
        online=True,
        power_state=True,
        brightness=100,
        source="api",
    )
    state.oscillating = True
    state.work_mode = 1  # gearMode
    state.mode_value = 2  # Medium speed
    return state


@pytest.fixture
def api_fan_device_response() -> dict[str, Any]:
    """Create a mock API fan device response (H7101)."""
    return {
        "device": "AA:BB:CC:DD:EE:FF:00:44",
        "sku": "H7101",
        "deviceName": "Living Room Fan",
        "type": "devices.types.fan",
        "capabilities": [
            {"type": CAPABILITY_ON_OFF, "instance": INSTANCE_POWER, "parameters": {}},
            {"type": CAPABILITY_TOGGLE, "instance": INSTANCE_OSCILLATION, "parameters": {}},
            {
                "type": CAPABILITY_WORK_MODE,
                "instance": INSTANCE_WORK_MODE,
                "parameters": {
                    "options": [
                        {"name": "gearMode", "value": {"workMode": 1, "modeValue": [1, 2, 3]}},
                        {"name": "Auto", "value": {"workMode": 3, "modeValue": [0]}},
                    ],
                },
            },
        ],
    }


@pytest.fixture
def api_fan_state_response() -> dict[str, Any]:
    """Create a mock API fan state response."""
    return {
        "capabilities": [
            {
                "type": "devices.capabilities.online",
                "instance": "online",
                "state": {"value": True},
            },
            {
                "type": CAPABILITY_ON_OFF,
                "instance": INSTANCE_POWER,
                "state": {"value": 1},
            },
            {
                "type": CAPABILITY_TOGGLE,
                "instance": INSTANCE_OSCILLATION,
                "state": {"value": 1},
            },
            {
                "type": CAPABILITY_WORK_MODE,
                "instance": INSTANCE_WORK_MODE,
                "state": {"value": {"workMode": 1, "modeValue": 2}},
            },
        ],
    }


@pytest.fixture
def hdmi_capabilities() -> tuple[GoveeCapability, ...]:
    """Create capabilities for an HDMI sync box device (H6604)."""
    return (
        GoveeCapability(
            type=CAPABILITY_ON_OFF,
            instance=INSTANCE_POWER,
            parameters={},
        ),
        GoveeCapability(
            type=CAPABILITY_MODE,
            instance=INSTANCE_HDMI_SOURCE,
            parameters={
                "options": [
                    {"name": "HDMI 1", "value": 1},
                    {"name": "HDMI 2", "value": 2},
                    {"name": "HDMI 3", "value": 3},
                    {"name": "HDMI 4", "value": 4},
                ],
            },
        ),
    )


@pytest.fixture
def mock_hdmi_device(hdmi_capabilities) -> GoveeDevice:
    """Create a mock HDMI sync box device (H6604)."""
    return GoveeDevice(
        device_id="AA:BB:CC:DD:EE:FF:00:55",
        sku="H6604",
        name="AI Sync Box",
        device_type=DEVICE_TYPE_LIGHT,
        capabilities=hdmi_capabilities,
        is_group=False,
    )


@pytest.fixture
def mock_hdmi_device_state() -> GoveeDeviceState:
    """Create a mock HDMI device state."""
    state = GoveeDeviceState(
        device_id="AA:BB:CC:DD:EE:FF:00:55",
        online=True,
        power_state=True,
        brightness=100,
        source="api",
    )
    state.hdmi_source = 1  # HDMI 1 selected
    return state


@pytest.fixture
def api_hdmi_device_response() -> dict[str, Any]:
    """Create a mock API HDMI device response (H6604)."""
    return {
        "device": "AA:BB:CC:DD:EE:FF:00:55",
        "sku": "H6604",
        "deviceName": "AI Sync Box",
        "type": "devices.types.light",
        "capabilities": [
            {"type": CAPABILITY_ON_OFF, "instance": INSTANCE_POWER, "parameters": {}},
            {
                "type": CAPABILITY_MODE,
                "instance": INSTANCE_HDMI_SOURCE,
                "parameters": {
                    "options": [
                        {"name": "HDMI 1", "value": 1},
                        {"name": "HDMI 2", "value": 2},
                        {"name": "HDMI 3", "value": 3},
                        {"name": "HDMI 4", "value": 4},
                    ],
                },
            },
        ],
    }


@pytest.fixture
def api_hdmi_state_response() -> dict[str, Any]:
    """Create a mock API HDMI state response."""
    return {
        "capabilities": [
            {
                "type": "devices.capabilities.online",
                "instance": "online",
                "state": {"value": True},
            },
            {
                "type": CAPABILITY_ON_OFF,
                "instance": INSTANCE_POWER,
                "state": {"value": 1},
            },
            {
                "type": CAPABILITY_MODE,
                "instance": INSTANCE_HDMI_SOURCE,
                "state": {"value": 2},
            },
        ],
    }
