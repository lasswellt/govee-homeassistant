"""Test Govee services."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch
import pytest
from homeassistant.core import HomeAssistant

from custom_components.govee.services import (
    async_setup_services,
    async_setup_select_services,
    SERVICE_SET_SEGMENT_COLOR,
    SERVICE_SET_SEGMENT_BRIGHTNESS,
    SERVICE_SET_MUSIC_MODE,
    SERVICE_REFRESH_SCENES,
)


class TestAsyncSetupServices:
    """Test async_setup_services function."""

    @pytest.mark.asyncio
    async def test_setup_services_called(self, hass: HomeAssistant):
        """Test that services setup is called."""
        with patch(
            "custom_components.govee.services.entity_platform.async_get_current_platform"
        ) as mock_platform:
            mock_platform.return_value.async_register_entity_service = MagicMock()

            await async_setup_services(hass)

            mock_platform.assert_called_once()

    @pytest.mark.asyncio
    async def test_registers_segment_color_service(self, hass: HomeAssistant):
        """Test set_segment_color service is registered."""
        with patch(
            "custom_components.govee.services.entity_platform.async_get_current_platform"
        ) as mock_platform:
            register_mock = MagicMock()
            mock_platform.return_value.async_register_entity_service = register_mock

            await async_setup_services(hass)

            # Check that set_segment_color was registered
            calls = register_mock.call_args_list
            service_names = [call[0][0] for call in calls]
            assert SERVICE_SET_SEGMENT_COLOR in service_names

    @pytest.mark.asyncio
    async def test_registers_segment_brightness_service(self, hass: HomeAssistant):
        """Test set_segment_brightness service is registered."""
        with patch(
            "custom_components.govee.services.entity_platform.async_get_current_platform"
        ) as mock_platform:
            register_mock = MagicMock()
            mock_platform.return_value.async_register_entity_service = register_mock

            await async_setup_services(hass)

            # Check that set_segment_brightness was registered
            calls = register_mock.call_args_list
            service_names = [call[0][0] for call in calls]
            assert SERVICE_SET_SEGMENT_BRIGHTNESS in service_names

    @pytest.mark.asyncio
    async def test_registers_music_mode_service(self, hass: HomeAssistant):
        """Test set_music_mode service is registered."""
        with patch(
            "custom_components.govee.services.entity_platform.async_get_current_platform"
        ) as mock_platform:
            register_mock = MagicMock()
            mock_platform.return_value.async_register_entity_service = register_mock

            await async_setup_services(hass)

            # Check that set_music_mode was registered
            calls = register_mock.call_args_list
            service_names = [call[0][0] for call in calls]
            assert SERVICE_SET_MUSIC_MODE in service_names

    @pytest.mark.asyncio
    async def test_registers_all_three_services(self, hass: HomeAssistant):
        """Test all three light services are registered."""
        with patch(
            "custom_components.govee.services.entity_platform.async_get_current_platform"
        ) as mock_platform:
            register_mock = MagicMock()
            mock_platform.return_value.async_register_entity_service = register_mock

            await async_setup_services(hass)

            # Should register exactly 3 services
            assert register_mock.call_count == 3

    @pytest.mark.asyncio
    async def test_segment_color_service_schema(self, hass: HomeAssistant):
        """Test set_segment_color service has correct schema."""
        with patch(
            "custom_components.govee.services.entity_platform.async_get_current_platform"
        ) as mock_platform:
            register_mock = MagicMock()
            mock_platform.return_value.async_register_entity_service = register_mock

            await async_setup_services(hass)

            # Find the set_segment_color call
            for call in register_mock.call_args_list:
                if call[0][0] == SERVICE_SET_SEGMENT_COLOR:
                    schema = call[0][1]
                    handler = call[0][2]

                    # Verify schema has required fields
                    assert "segments" in schema
                    assert "rgb_color" in schema

                    # Verify handler method name
                    assert handler == "async_set_segment_color"

    @pytest.mark.asyncio
    async def test_segment_brightness_service_schema(self, hass: HomeAssistant):
        """Test set_segment_brightness service has correct schema."""
        with patch(
            "custom_components.govee.services.entity_platform.async_get_current_platform"
        ) as mock_platform:
            register_mock = MagicMock()
            mock_platform.return_value.async_register_entity_service = register_mock

            await async_setup_services(hass)

            # Find the set_segment_brightness call
            for call in register_mock.call_args_list:
                if call[0][0] == SERVICE_SET_SEGMENT_BRIGHTNESS:
                    schema = call[0][1]
                    handler = call[0][2]

                    # Verify schema has required fields
                    assert "segments" in schema
                    assert "brightness" in schema

                    # Verify handler method name
                    assert handler == "async_set_segment_brightness"

    @pytest.mark.asyncio
    async def test_music_mode_service_schema(self, hass: HomeAssistant):
        """Test set_music_mode service has correct schema."""
        with patch(
            "custom_components.govee.services.entity_platform.async_get_current_platform"
        ) as mock_platform:
            register_mock = MagicMock()
            mock_platform.return_value.async_register_entity_service = register_mock

            await async_setup_services(hass)

            # Find the set_music_mode call
            for call in register_mock.call_args_list:
                if call[0][0] == SERVICE_SET_MUSIC_MODE:
                    schema = call[0][1]
                    handler = call[0][2]

                    # Verify schema has required and optional fields
                    assert "mode" in schema
                    assert "sensitivity" in schema
                    assert "auto_color" in schema
                    assert "rgb_color" in schema

                    # Verify handler method name
                    assert handler == "async_set_music_mode"


class TestAsyncSetupSelectServices:
    """Test async_setup_select_services function."""

    @pytest.mark.asyncio
    async def test_setup_select_services_called(self, hass: HomeAssistant):
        """Test that select services setup is called."""
        with patch(
            "custom_components.govee.services.entity_platform.async_get_current_platform"
        ) as mock_platform:
            mock_platform.return_value.async_register_entity_service = MagicMock()

            await async_setup_select_services(hass)

            mock_platform.assert_called_once()

    @pytest.mark.asyncio
    async def test_registers_refresh_scenes_service(self, hass: HomeAssistant):
        """Test refresh_scenes service is registered."""
        with patch(
            "custom_components.govee.services.entity_platform.async_get_current_platform"
        ) as mock_platform:
            register_mock = MagicMock()
            mock_platform.return_value.async_register_entity_service = register_mock

            await async_setup_select_services(hass)

            # Should register exactly 1 service
            assert register_mock.call_count == 1

            # Check that refresh_scenes was registered
            call = register_mock.call_args_list[0]
            assert call[0][0] == SERVICE_REFRESH_SCENES

    @pytest.mark.asyncio
    async def test_refresh_scenes_service_schema(self, hass: HomeAssistant):
        """Test refresh_scenes service has correct schema (empty)."""
        with patch(
            "custom_components.govee.services.entity_platform.async_get_current_platform"
        ) as mock_platform:
            register_mock = MagicMock()
            mock_platform.return_value.async_register_entity_service = register_mock

            await async_setup_select_services(hass)

            # Check schema and handler
            call = register_mock.call_args_list[0]
            schema = call[0][1]
            handler = call[0][2]

            # Should have empty schema (no parameters)
            assert schema == {}

            # Verify handler method name
            assert handler == "async_refresh_scenes"
