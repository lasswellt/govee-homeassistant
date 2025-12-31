"""Test Govee button platform."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
import pytest
from homeassistant.core import HomeAssistant

from custom_components.govee.button import async_setup_entry
from custom_components.govee.entities.button import (
    GoveeIdentifyButton,
    GoveeRefreshScenesButton,
)
from custom_components.govee.entity_descriptions.button import BUTTON_DESCRIPTIONS


class TestAsyncSetupEntry:
    """Test async_setup_entry function."""

    @pytest.mark.asyncio
    async def test_setup_entry_creates_buttons(
        self,
        hass: HomeAssistant,
        mock_config_entry,
        mock_coordinator,
        mock_device_light,
    ):
        """Test setup creates button entities."""
        mock_config_entry.runtime_data = MagicMock()
        mock_config_entry.runtime_data.coordinator = mock_coordinator
        mock_config_entry.runtime_data.devices = {
            mock_device_light.device_id: mock_device_light
        }

        async_add_entities = MagicMock()

        await async_setup_entry(hass, mock_config_entry, async_add_entities)

        # Should add button entities
        async_add_entities.assert_called_once()
        entities = async_add_entities.call_args[0][0]
        # Should have identify button and refresh scenes button for light
        assert len(entities) == 2

    @pytest.mark.asyncio
    async def test_setup_entry_switch_device_no_refresh_scenes(
        self,
        hass: HomeAssistant,
        mock_config_entry,
        mock_coordinator,
        mock_device_switch,
    ):
        """Test setup with switch device only creates identify button."""
        mock_config_entry.runtime_data = MagicMock()
        mock_config_entry.runtime_data.coordinator = mock_coordinator
        mock_config_entry.runtime_data.devices = {
            mock_device_switch.device_id: mock_device_switch
        }

        async_add_entities = MagicMock()

        await async_setup_entry(hass, mock_config_entry, async_add_entities)

        # Should only have identify button (no refresh scenes for switches)
        async_add_entities.assert_called_once()
        entities = async_add_entities.call_args[0][0]
        assert len(entities) == 1
        assert isinstance(entities[0], GoveeIdentifyButton)


class TestGoveeRefreshScenesButton:
    """Test GoveeRefreshScenesButton class."""

    def test_button_initialization(self, mock_coordinator, mock_device_light):
        """Test button entity initializes correctly."""
        description = BUTTON_DESCRIPTIONS["refresh_scenes"]
        entity = GoveeRefreshScenesButton(
            mock_coordinator, mock_device_light, description
        )

        assert entity.entity_description == description
        assert entity._attr_unique_id == f"{mock_device_light.device_id}_refresh_scenes"

    @pytest.mark.asyncio
    async def test_async_press_success(self, mock_coordinator, mock_device_light):
        """Test button press triggers scene refresh."""
        mock_coordinator.async_refresh_device_scenes = AsyncMock()

        description = BUTTON_DESCRIPTIONS["refresh_scenes"]
        entity = GoveeRefreshScenesButton(
            mock_coordinator, mock_device_light, description
        )

        await entity.async_press()

        mock_coordinator.async_refresh_device_scenes.assert_called_once_with(
            mock_device_light.device_id
        )

    @pytest.mark.asyncio
    async def test_async_press_error(
        self, mock_coordinator, mock_device_light, caplog
    ):
        """Test button press handles errors."""
        mock_coordinator.async_refresh_device_scenes = AsyncMock(
            side_effect=Exception("API error")
        )

        description = BUTTON_DESCRIPTIONS["refresh_scenes"]
        entity = GoveeRefreshScenesButton(
            mock_coordinator, mock_device_light, description
        )

        with pytest.raises(Exception, match="API error"):
            await entity.async_press()

        assert "Failed to refresh scenes" in caplog.text


class TestGoveeIdentifyButton:
    """Test GoveeIdentifyButton class."""

    def test_button_initialization(self, mock_coordinator, mock_device_light):
        """Test button entity initializes correctly."""
        description = BUTTON_DESCRIPTIONS["identify"]
        entity = GoveeIdentifyButton(mock_coordinator, mock_device_light, description)

        assert entity.entity_description == description
        assert entity._attr_unique_id == f"{mock_device_light.device_id}_identify"

    @pytest.mark.asyncio
    async def test_async_press_success(self, mock_coordinator, mock_device_light):
        """Test button press triggers device identification."""
        mock_coordinator.async_identify_device = AsyncMock()

        description = BUTTON_DESCRIPTIONS["identify"]
        entity = GoveeIdentifyButton(mock_coordinator, mock_device_light, description)

        await entity.async_press()

        mock_coordinator.async_identify_device.assert_called_once_with(
            mock_device_light.device_id
        )

    @pytest.mark.asyncio
    async def test_async_press_error(
        self, mock_coordinator, mock_device_light, caplog
    ):
        """Test button press handles errors."""
        mock_coordinator.async_identify_device = AsyncMock(
            side_effect=Exception("Device error")
        )

        description = BUTTON_DESCRIPTIONS["identify"]
        entity = GoveeIdentifyButton(mock_coordinator, mock_device_light, description)

        with pytest.raises(Exception, match="Device error"):
            await entity.async_press()

        assert "Failed to identify device" in caplog.text
