"""Test the Govee light platform."""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.govee.const import (
    COLOR_TEMP_KELVIN_MIN,
    COLOR_TEMP_KELVIN_MAX,
    DOMAIN,
)
from custom_components.govee.light import GoveeLightEntity, GoveeDataUpdateCoordinator


@pytest.fixture
def mock_device():
    """Create a mock Govee device."""
    device = MagicMock()
    device.device = "AA:BB:CC:DD:EE:FF"
    device.device_name = "Test Light"
    device.model = "H6160"
    device.support_color = True
    device.support_color_tem = True
    device.support_brightness = True
    device.power_state = True
    device.online = True
    device.brightness = 128
    device.color = (255, 128, 64)
    device.color_temp = 4000
    return device


@pytest.fixture
def mock_hub():
    """Create a mock Govee hub."""
    hub = MagicMock()
    hub.turn_on = AsyncMock(return_value=(None, None))
    hub.turn_off = AsyncMock(return_value=(None, None))
    hub.set_brightness = AsyncMock(return_value=(None, None))
    hub.set_color = AsyncMock(return_value=(None, None))
    hub.set_color_temp = AsyncMock(return_value=(None, None))
    hub.rate_limit_total = 10000
    hub.rate_limit_remaining = 9999
    hub.rate_limit_reset_seconds = 3600.0
    hub.rate_limit_reset = 1700000000
    hub.rate_limit_on = False
    return hub


@pytest.fixture
def mock_coordinator():
    """Create a mock coordinator."""
    coordinator = MagicMock(spec=GoveeDataUpdateCoordinator)
    coordinator.use_assumed_state = True
    coordinator.async_add_listener = MagicMock()
    return coordinator


class TestGoveeLightEntityColorTemp:
    """Test color temperature properties."""

    def test_min_color_temp_kelvin_returns_minimum(self, mock_hub, mock_coordinator, mock_device):
        """Test min_color_temp_kelvin returns the minimum (warmest) value."""
        entity = GoveeLightEntity(mock_hub, "test", mock_coordinator, mock_device)

        assert entity.min_color_temp_kelvin == COLOR_TEMP_KELVIN_MIN
        assert entity.min_color_temp_kelvin == 2000

    def test_max_color_temp_kelvin_returns_maximum(self, mock_hub, mock_coordinator, mock_device):
        """Test max_color_temp_kelvin returns the maximum (coldest) value."""
        entity = GoveeLightEntity(mock_hub, "test", mock_coordinator, mock_device)

        assert entity.max_color_temp_kelvin == COLOR_TEMP_KELVIN_MAX
        assert entity.max_color_temp_kelvin == 9000

    def test_color_temp_range_is_valid(self, mock_hub, mock_coordinator, mock_device):
        """Test that min < max for color temperature range."""
        entity = GoveeLightEntity(mock_hub, "test", mock_coordinator, mock_device)

        assert entity.min_color_temp_kelvin < entity.max_color_temp_kelvin


class TestGoveeLightEntityTurnOff:
    """Test turn off functionality."""

    @pytest.mark.asyncio
    async def test_async_turn_off_calls_hub(self, mock_hub, mock_coordinator, mock_device):
        """Test async_turn_off calls the hub turn_off method."""
        entity = GoveeLightEntity(mock_hub, "test", mock_coordinator, mock_device)

        await entity.async_turn_off()

        mock_hub.turn_off.assert_called_once_with(mock_device)

    @pytest.mark.asyncio
    async def test_async_turn_off_handles_success(self, mock_hub, mock_coordinator, mock_device):
        """Test async_turn_off handles successful response."""
        mock_hub.turn_off = AsyncMock(return_value=(None, None))
        entity = GoveeLightEntity(mock_hub, "test", mock_coordinator, mock_device)

        # Should not raise
        await entity.async_turn_off()

    @pytest.mark.asyncio
    async def test_async_turn_off_handles_error(self, mock_hub, mock_coordinator, mock_device, caplog):
        """Test async_turn_off logs errors from the API."""
        mock_hub.turn_off = AsyncMock(return_value=(None, "API error: device offline"))
        entity = GoveeLightEntity(mock_hub, "test", mock_coordinator, mock_device)

        await entity.async_turn_off()

        # Should log a warning with the error
        assert "async_turn_off failed" in caplog.text
        assert "API error: device offline" in caplog.text


class TestGoveeLightEntityTurnOn:
    """Test turn on functionality."""

    @pytest.mark.asyncio
    async def test_async_turn_on_calls_hub(self, mock_hub, mock_coordinator, mock_device):
        """Test async_turn_on calls the hub turn_on method when no kwargs."""
        entity = GoveeLightEntity(mock_hub, "test", mock_coordinator, mock_device)

        await entity.async_turn_on()

        mock_hub.turn_on.assert_called_once_with(mock_device)

    @pytest.mark.asyncio
    async def test_async_turn_on_with_brightness(self, mock_hub, mock_coordinator, mock_device):
        """Test async_turn_on sets brightness when provided."""
        entity = GoveeLightEntity(mock_hub, "test", mock_coordinator, mock_device)

        await entity.async_turn_on(brightness=128)

        # brightness - 1 = 127 (convert from HA 1-255 to Govee 0-254)
        mock_hub.set_brightness.assert_called_once_with(mock_device, 127)
        mock_hub.turn_on.assert_not_called()

    @pytest.mark.asyncio
    async def test_async_turn_on_handles_error(self, mock_hub, mock_coordinator, mock_device, caplog):
        """Test async_turn_on logs errors from the API."""
        mock_hub.turn_on = AsyncMock(return_value=(None, "API error: rate limited"))
        entity = GoveeLightEntity(mock_hub, "test", mock_coordinator, mock_device)

        await entity.async_turn_on()

        assert "async_turn_on failed" in caplog.text
        assert "API error: rate limited" in caplog.text
