"""Shared test fixtures for Govee integration tests."""
from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from collections.abc import Generator

import pytest
from homeassistant.const import CONF_API_KEY
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.govee.const import (
    DOMAIN,
    CONF_POLL_INTERVAL,
    CONF_USE_ASSUMED_STATE,
    CONF_OFFLINE_IS_OFF,
    CONF_ENABLE_GROUP_DEVICES,
)
from custom_components.govee.models import (
    GoveeDevice,
    GoveeDeviceState,
    DeviceCapability,
    CapabilityParameter,
)


# ==============================================================================
# Config Entry Fixtures
# ==============================================================================


@pytest.fixture
def mock_config_entry() -> MockConfigEntry:
    """Create a mock Govee config entry."""
    return MockConfigEntry(
        domain=DOMAIN,
        title="Govee",
        data={
            CONF_API_KEY: "test_api_key_123456789",
        },
        options={
            CONF_POLL_INTERVAL: 60,
            CONF_USE_ASSUMED_STATE: False,
            CONF_OFFLINE_IS_OFF: False,
            CONF_ENABLE_GROUP_DEVICES: False,
        },
        entry_id="test_entry_id",
        unique_id="govee_test",
    )


@pytest.fixture
def mock_config_entry_with_options() -> MockConfigEntry:
    """Create a mock Govee config entry with custom options."""
    return MockConfigEntry(
        domain=DOMAIN,
        title="Govee",
        data={
            CONF_API_KEY: "test_api_key_123456789",
        },
        options={
            CONF_POLL_INTERVAL: 30,
            CONF_USE_ASSUMED_STATE: True,
            CONF_OFFLINE_IS_OFF: True,
            CONF_ENABLE_GROUP_DEVICES: True,
        },
        entry_id="test_entry_with_options",
        unique_id="govee_test_options",
    )


# ==============================================================================
# Device Model Fixtures (Factories for different device types)
# ==============================================================================


@pytest.fixture
def device_capability_on_off() -> DeviceCapability:
    """Create an on/off capability."""
    return DeviceCapability(
        type="devices.capabilities.on_off",
        instance="powerSwitch",
        parameters=None,
    )


@pytest.fixture
def device_capability_brightness() -> DeviceCapability:
    """Create a brightness capability."""
    return DeviceCapability(
        type="devices.capabilities.range",
        instance="brightness",
        parameters=CapabilityParameter(
            data_type="INTEGER",
            min_value=0,
            max_value=100,
            precision=1,
        ),
    )


@pytest.fixture
def device_capability_color_rgb() -> DeviceCapability:
    """Create an RGB color capability."""
    return DeviceCapability(
        type="devices.capabilities.color_setting",
        instance="colorRgb",
        parameters=CapabilityParameter(
            data_type="STRUCT",
            fields=[
                {"fieldName": "r", "type": "INTEGER", "range": {"min": 0, "max": 255}},
                {"fieldName": "g", "type": "INTEGER", "range": {"min": 0, "max": 255}},
                {"fieldName": "b", "type": "INTEGER", "range": {"min": 0, "max": 255}},
            ],
        ),
    )


@pytest.fixture
def device_capability_color_temp() -> DeviceCapability:
    """Create a color temperature capability."""
    return DeviceCapability(
        type="devices.capabilities.color_setting",
        instance="colorTemperatureK",
        parameters=CapabilityParameter(
            data_type="INTEGER",
            min_value=2000,
            max_value=9000,
            precision=1,
        ),
    )


@pytest.fixture
def device_capability_scene() -> DeviceCapability:
    """Create a dynamic scene capability."""
    return DeviceCapability(
        type="devices.capabilities.dynamic_scene",
        instance="lightScene",
        parameters=CapabilityParameter(
            data_type="ENUM",
            options=[
                {"name": "Sunrise", "value": 1},
                {"name": "Sunset", "value": 2},
                {"name": "Movie", "value": 3},
                {"name": "Romantic", "value": 4},
            ],
        ),
    )


@pytest.fixture
def mock_device_light(
    device_capability_on_off: DeviceCapability,
    device_capability_brightness: DeviceCapability,
    device_capability_color_rgb: DeviceCapability,
    device_capability_color_temp: DeviceCapability,
) -> GoveeDevice:
    """Create a mock RGB+CCT light device."""
    return GoveeDevice(
        device_id="AA:BB:CC:DD:EE:FF:11:22",
        sku="H6160",
        device_name="Bedroom Strip",
        device_type="devices.types.light",
        capabilities=[
            device_capability_on_off,
            device_capability_brightness,
            device_capability_color_rgb,
            device_capability_color_temp,
        ],
        firmware_version="1.02.03",
    )


@pytest.fixture
def mock_device_light_with_scenes(
    mock_device_light: GoveeDevice,
    device_capability_scene: DeviceCapability,
) -> GoveeDevice:
    """Create a mock light device with scene support."""
    return GoveeDevice(
        device_id=mock_device_light.device_id,
        sku=mock_device_light.sku,
        device_name=mock_device_light.device_name,
        device_type=mock_device_light.device_type,
        capabilities=mock_device_light.capabilities + [device_capability_scene],
        firmware_version=mock_device_light.firmware_version,
    )


@pytest.fixture
def mock_device_switch(
    device_capability_on_off: DeviceCapability,
) -> GoveeDevice:
    """Create a mock switch/socket device."""
    return GoveeDevice(
        device_id="AA:BB:CC:DD:EE:FF:33:44",
        sku="H5080",
        device_name="Living Room Outlet",
        device_type="devices.types.socket",
        capabilities=[device_capability_on_off],
        firmware_version="1.01.02",
    )


@pytest.fixture
def mock_device_brightness_only(
    device_capability_on_off: DeviceCapability,
    device_capability_brightness: DeviceCapability,
) -> GoveeDevice:
    """Create a mock light device with only on/off and brightness."""
    return GoveeDevice(
        device_id="AA:BB:CC:DD:EE:FF:55:66",
        sku="H6001",
        device_name="Desk Lamp",
        device_type="devices.types.light",
        capabilities=[device_capability_on_off, device_capability_brightness],
        firmware_version="1.00.05",
    )


@pytest.fixture
def mock_device_group() -> GoveeDevice:
    """Create a mock group device (unsupported for state queries)."""
    return GoveeDevice(
        device_id="GROUP_123456789",
        sku="HY2XX.XX",  # Group SKU pattern
        device_name="Living Room Group",
        device_type="devices.types.light",
        capabilities=[
            DeviceCapability(
                type="devices.capabilities.on_off",
                instance="powerSwitch",
            ),
            DeviceCapability(
                type="devices.capabilities.range",
                instance="brightness",
                parameters=CapabilityParameter(
                    data_type="INTEGER",
                    min_value=0,
                    max_value=100,
                ),
            ),
        ],
        firmware_version=None,
    )


# ==============================================================================
# Device State Fixtures
# ==============================================================================


@pytest.fixture
def mock_state_light_on() -> GoveeDeviceState:
    """Create a mock light state (on, RGB, full brightness)."""
    return GoveeDeviceState(
        online=True,
        power_state=True,
        brightness=100,
        color_rgb=(255, 128, 64),
        color_temp=None,
        scene_code=None,
    )


@pytest.fixture
def mock_state_light_off() -> GoveeDeviceState:
    """Create a mock light state (off)."""
    return GoveeDeviceState(
        online=True,
        power_state=False,
        brightness=0,
        color_rgb=None,
        color_temp=None,
        scene_code=None,
    )


@pytest.fixture
def mock_state_light_color_temp() -> GoveeDeviceState:
    """Create a mock light state with color temperature."""
    return GoveeDeviceState(
        online=True,
        power_state=True,
        brightness=75,
        color_rgb=None,
        color_temp=4000,
        scene_code=None,
    )


@pytest.fixture
def mock_state_offline() -> GoveeDeviceState:
    """Create a mock offline device state."""
    return GoveeDeviceState(
        online=False,
        power_state=False,
        brightness=0,
        color_rgb=None,
        color_temp=None,
        scene_code=None,
    )


# ==============================================================================
# API Client Fixtures
# ==============================================================================


@pytest.fixture
def mock_api_client() -> MagicMock:
    """Create a mocked Govee API client."""
    client = MagicMock()

    # Mock async methods
    client.get_devices = AsyncMock(return_value=[])
    client.get_device_state = AsyncMock(return_value=None)
    client.control_device = AsyncMock(return_value=None)
    client.turn_on = AsyncMock(return_value=None)
    client.turn_off = AsyncMock(return_value=None)
    client.set_brightness = AsyncMock(return_value=None)
    client.set_color_rgb = AsyncMock(return_value=None)
    client.set_color_temp = AsyncMock(return_value=None)
    client.set_scene = AsyncMock(return_value=None)
    client.get_dynamic_scenes = AsyncMock(return_value=[])
    client.get_diy_scenes = AsyncMock(return_value=[])

    # Mock rate limiter properties
    client.rate_limiter = MagicMock()
    client.rate_limiter.remaining_minute = 100
    client.rate_limiter.remaining_day = 10000
    client.rate_limiter.reset_minute = 60
    client.rate_limiter.reset_day = 86400

    return client


@pytest.fixture
def mock_api_client_with_devices(
    mock_api_client: MagicMock,
    mock_device_light: GoveeDevice,
    mock_device_switch: GoveeDevice,
) -> MagicMock:
    """Create a mocked API client that returns test devices."""
    mock_api_client.get_devices = AsyncMock(
        return_value=[mock_device_light, mock_device_switch]
    )
    return mock_api_client


@pytest.fixture
def mock_api_client_with_state(
    mock_api_client: MagicMock,
    mock_state_light_on: GoveeDeviceState,
) -> MagicMock:
    """Create a mocked API client that returns device state."""
    mock_api_client.get_device_state = AsyncMock(return_value=mock_state_light_on)
    return mock_api_client


# ==============================================================================
# Coordinator Fixtures
# ==============================================================================


@pytest.fixture
def mock_coordinator(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_api_client: MagicMock,
) -> MagicMock:
    """Create a mocked data update coordinator."""
    from custom_components.govee.coordinator import GoveeDataUpdateCoordinator

    coordinator = MagicMock(spec=GoveeDataUpdateCoordinator)
    coordinator.hass = hass
    coordinator.config_entry = mock_config_entry
    coordinator.client = mock_api_client
    coordinator.data = {}
    coordinator.last_update_success = True
    coordinator.rate_limit_remaining = 9999
    coordinator.rate_limit_remaining_minute = 99
    coordinator.async_add_listener = MagicMock()
    coordinator.async_remove_listener = MagicMock()
    coordinator.async_request_refresh = AsyncMock()
    coordinator.get_state = MagicMock(return_value=None)
    coordinator.set_optimistic_state = MagicMock()

    return coordinator


# ==============================================================================
# Home Assistant Fixtures
# ==============================================================================


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(
    enable_custom_integrations: Generator[None, None, None],
) -> Generator[None, None, None]:
    """Enable custom integrations for all tests."""
    yield


@pytest.fixture
def mock_setup_integration() -> Generator[None, None, None]:
    """Mock integration setup."""
    with patch(
        "custom_components.govee.async_setup",
        return_value=True,
    ), patch(
        "custom_components.govee.async_setup_entry",
        return_value=True,
    ):
        yield


# ==============================================================================
# Scene Fixtures
# ==============================================================================


@pytest.fixture
def mock_dynamic_scenes() -> list[dict[str, Any]]:
    """Create mock dynamic scenes from API."""
    return [
        {"sceneCode": 1, "sceneName": "Sunrise"},
        {"sceneCode": 2, "sceneName": "Sunset"},
        {"sceneCode": 3, "sceneName": "Movie"},
        {"sceneCode": 4, "sceneName": "Romantic"},
        {"sceneCode": 5, "sceneName": "Party"},
    ]


@pytest.fixture
def mock_diy_scenes() -> list[dict[str, Any]]:
    """Create mock DIY scenes from API."""
    return [
        {"sceneCode": 101, "sceneName": "My Custom Scene 1"},
        {"sceneCode": 102, "sceneName": "My Custom Scene 2"},
    ]


# ==============================================================================
# API Response Fixtures
# ==============================================================================


@pytest.fixture
def mock_api_device_response() -> dict[str, Any]:
    """Create a mock API device discovery response."""
    return {
        "device": "AA:BB:CC:DD:EE:FF:11:22",
        "sku": "H6160",
        "deviceName": "Bedroom Strip",
        "type": "devices.types.light",
        "capabilities": [
            {
                "type": "devices.capabilities.on_off",
                "instance": "powerSwitch",
            },
            {
                "type": "devices.capabilities.range",
                "instance": "brightness",
                "parameters": {
                    "dataType": "INTEGER",
                    "range": {"min": 0, "max": 100, "precision": 1},
                },
            },
            {
                "type": "devices.capabilities.color_setting",
                "instance": "colorRgb",
                "parameters": {
                    "dataType": "STRUCT",
                    "fields": [
                        {"fieldName": "r", "type": "INTEGER", "range": {"min": 0, "max": 255}},
                        {"fieldName": "g", "type": "INTEGER", "range": {"min": 0, "max": 255}},
                        {"fieldName": "b", "type": "INTEGER", "range": {"min": 0, "max": 255}},
                    ],
                },
            },
        ],
        "version": "1.02.03",
    }


@pytest.fixture
def mock_api_state_response() -> dict[str, Any]:
    """Create a mock API device state response."""
    return {
        "online": True,
        "powerState": "on",
        "brightness": 100,
        "color": {"r": 255, "g": 128, "b": 64},
    }


# ==============================================================================
# Error Fixtures
# ==============================================================================


@pytest.fixture
def mock_auth_error() -> Exception:
    """Create a mock authentication error."""
    from custom_components.govee.api.exceptions import GoveeAuthError

    return GoveeAuthError("Invalid API key")


@pytest.fixture
def mock_rate_limit_error() -> Exception:
    """Create a mock rate limit error."""
    from custom_components.govee.api.exceptions import GoveeRateLimitError

    return GoveeRateLimitError("Rate limit exceeded")


@pytest.fixture
def mock_connection_error() -> Exception:
    """Create a mock connection error."""
    from custom_components.govee.api.exceptions import GoveeConnectionError

    return GoveeConnectionError("Failed to connect to Govee API")
