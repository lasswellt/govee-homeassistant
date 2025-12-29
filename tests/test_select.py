"""Test Govee select platform."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch
import pytest
from homeassistant.core import HomeAssistant

from custom_components.govee.select import (
    async_setup_entry,
    GoveeSceneSelect,
)
from custom_components.govee.api.const import (
    INSTANCE_LIGHT_SCENE,
    INSTANCE_DIY_SCENE,
    CAPABILITY_DYNAMIC_SCENE,
)
from custom_components.govee.models import SceneOption


class TestAsyncSetupEntry:
    """Test async_setup_entry function."""

    @pytest.mark.asyncio
    async def test_setup_entry_with_scene_support(
        self,
        hass: HomeAssistant,
        mock_config_entry,
        mock_coordinator,
        mock_device_light_with_scenes,
    ):
        """Test setup creates select entity for device with scenes."""
        mock_config_entry.runtime_data = MagicMock()
        mock_config_entry.runtime_data.coordinator = mock_coordinator
        mock_config_entry.runtime_data.devices = {
            mock_device_light_with_scenes.device_id: mock_device_light_with_scenes
        }

        async_add_entities = MagicMock()

        with patch(
            "custom_components.govee.select.async_setup_select_services",
            new_callable=AsyncMock,
        ):
            await async_setup_entry(hass, mock_config_entry, async_add_entities)

        # Should add one scene select entity
        async_add_entities.assert_called_once()
        entities = async_add_entities.call_args[0][0]
        assert len(entities) == 1
        assert isinstance(entities[0], GoveeSceneSelect)

    @pytest.mark.asyncio
    async def test_setup_entry_with_no_scenes(
        self,
        hass: HomeAssistant,
        mock_config_entry,
        mock_coordinator,
        mock_device_light,
    ):
        """Test setup with light device without scene support."""
        mock_config_entry.runtime_data = MagicMock()
        mock_config_entry.runtime_data.coordinator = mock_coordinator
        mock_config_entry.runtime_data.devices = {
            mock_device_light.device_id: mock_device_light
        }

        async_add_entities = MagicMock()

        with patch(
            "custom_components.govee.select.async_setup_select_services",
            new_callable=AsyncMock,
        ):
            await async_setup_entry(hass, mock_config_entry, async_add_entities)

        # Should add no entities (no scene support)
        async_add_entities.assert_called_once()
        entities = async_add_entities.call_args[0][0]
        assert len(entities) == 0

    @pytest.mark.asyncio
    async def test_setup_entry_registers_services(
        self,
        hass: HomeAssistant,
        mock_config_entry,
        mock_coordinator,
        mock_device_light_with_scenes,
    ):
        """Test setup registers select services."""
        mock_config_entry.runtime_data = MagicMock()
        mock_config_entry.runtime_data.coordinator = mock_coordinator
        mock_config_entry.runtime_data.devices = {
            mock_device_light_with_scenes.device_id: mock_device_light_with_scenes
        }

        with patch(
            "custom_components.govee.select.async_setup_select_services",
            new_callable=AsyncMock,
        ) as mock_setup_services:
            await async_setup_entry(hass, mock_config_entry, MagicMock())

            # Should register select services
            mock_setup_services.assert_called_once_with(hass)


class TestGoveeSceneSelect:
    """Test GoveeSceneSelect class."""

    def test_select_entity_initialization_dynamic(
        self,
        mock_coordinator,
        mock_device_light_with_scenes,
    ):
        """Test select entity initializes correctly for dynamic scenes."""
        entity = GoveeSceneSelect(
            mock_coordinator,
            mock_device_light_with_scenes,
            scene_type="dynamic",
            instance=INSTANCE_LIGHT_SCENE,
        )

        assert entity._scene_type == "dynamic"
        assert entity._instance == INSTANCE_LIGHT_SCENE
        assert entity._attr_unique_id == f"govee_{mock_device_light_with_scenes.device_id}_dynamic_scene"
        assert entity._attr_translation_key == "scene"
        assert entity._attr_name == "Scene"
        assert entity._attr_options == []
        assert entity._attr_current_option is None

    def test_select_entity_initialization_diy(
        self,
        mock_coordinator,
        mock_device_light_with_scenes,
    ):
        """Test select entity initializes correctly for DIY scenes."""
        entity = GoveeSceneSelect(
            mock_coordinator,
            mock_device_light_with_scenes,
            scene_type="diy",
            instance=INSTANCE_DIY_SCENE,
        )

        assert entity._scene_type == "diy"
        assert entity._instance == INSTANCE_DIY_SCENE
        assert entity._attr_unique_id == f"govee_{mock_device_light_with_scenes.device_id}_diy_scene"
        assert entity._attr_translation_key == "diy_scene"
        assert entity._attr_name == "DIY Scene"
        assert entity._attr_entity_registry_enabled_default is False  # Disabled by default

    @pytest.mark.asyncio
    async def test_async_added_to_hass_loads_options(
        self,
        hass: HomeAssistant,
        mock_coordinator,
        mock_device_light_with_scenes,
        mock_dynamic_scenes,
    ):
        """Test async_added_to_hass loads scene options."""
        # Mock coordinator scene fetch
        mock_coordinator.async_get_dynamic_scenes = AsyncMock(
            return_value=[
                SceneOption(name="Sunrise", value=1),
                SceneOption(name="Sunset", value=2),
            ]
        )

        entity = GoveeSceneSelect(
            mock_coordinator,
            mock_device_light_with_scenes,
            scene_type="dynamic",
            instance=INSTANCE_LIGHT_SCENE,
        )

        entity.hass = hass
        await entity.async_added_to_hass()

        # Should have loaded options
        assert len(entity._attr_options) == 2
        assert "Sunrise" in entity._attr_options
        assert "Sunset" in entity._attr_options
        mock_coordinator.async_get_dynamic_scenes.assert_called_once()

    @pytest.mark.asyncio
    async def test_refresh_options_dynamic_scenes(
        self,
        mock_coordinator,
        mock_device_light_with_scenes,
    ):
        """Test _async_refresh_options for dynamic scenes."""
        scenes = [
            SceneOption(name="Movie", value=3),
            SceneOption(name="Romantic", value=4),
            SceneOption(name="Party", value=5),
        ]
        mock_coordinator.async_get_dynamic_scenes = AsyncMock(return_value=scenes)

        entity = GoveeSceneSelect(
            mock_coordinator,
            mock_device_light_with_scenes,
            scene_type="dynamic",
            instance=INSTANCE_LIGHT_SCENE,
        )

        await entity._async_refresh_options()

        # Should populate options
        assert len(entity._attr_options) == 3
        assert entity._attr_options == sorted(["Movie", "Romantic", "Party"])
        assert len(entity._options_map) == 3
        assert "Movie" in entity._options_map

    @pytest.mark.asyncio
    async def test_refresh_options_diy_scenes(
        self,
        mock_coordinator,
        mock_device_light_with_scenes,
    ):
        """Test _async_refresh_options for DIY scenes."""
        scenes = [
            SceneOption(name="My Custom 1", value=101),
            SceneOption(name="My Custom 2", value=102),
        ]
        mock_coordinator.async_get_diy_scenes = AsyncMock(return_value=scenes)

        entity = GoveeSceneSelect(
            mock_coordinator,
            mock_device_light_with_scenes,
            scene_type="diy",
            instance=INSTANCE_DIY_SCENE,
        )

        await entity._async_refresh_options()

        # Should populate options
        assert len(entity._attr_options) == 2
        assert "My Custom 1" in entity._attr_options
        assert "My Custom 2" in entity._attr_options

    @pytest.mark.asyncio
    async def test_refresh_options_handles_errors(
        self,
        mock_coordinator,
        mock_device_light_with_scenes,
        caplog,
    ):
        """Test _async_refresh_options handles API errors."""
        mock_coordinator.async_get_dynamic_scenes = AsyncMock(
            side_effect=Exception("API error")
        )

        entity = GoveeSceneSelect(
            mock_coordinator,
            mock_device_light_with_scenes,
            scene_type="dynamic",
            instance=INSTANCE_LIGHT_SCENE,
        )

        await entity._async_refresh_options()

        # Should log warning but not raise
        assert "Failed to load dynamic scenes" in caplog.text
        assert "API error" in caplog.text

    def test_current_option_returns_none_no_state(
        self,
        mock_coordinator,
        mock_device_light_with_scenes,
    ):
        """Test current_option returns None when no state."""
        mock_coordinator.get_state.return_value = None

        entity = GoveeSceneSelect(
            mock_coordinator,
            mock_device_light_with_scenes,
            scene_type="dynamic",
            instance=INSTANCE_LIGHT_SCENE,
        )

        assert entity.current_option is None

    def test_current_option_returns_matched_scene(
        self,
        mock_coordinator,
        mock_device_light_with_scenes,
    ):
        """Test current_option returns matched scene name."""
        from custom_components.govee.models import GoveeDeviceState

        state = GoveeDeviceState(
            online=True, power_state=True, brightness=100, current_scene="3"
        )
        mock_coordinator.get_state.return_value = state

        entity = GoveeSceneSelect(
            mock_coordinator,
            mock_device_light_with_scenes,
            scene_type="dynamic",
            instance=INSTANCE_LIGHT_SCENE,
        )

        # Populate options map
        entity._options_map = {
            "Movie": SceneOption(name="Movie", value=3),
            "Party": SceneOption(name="Party", value=5),
        }

        assert entity.current_option == "Movie"

    def test_current_option_with_dict_value(
        self,
        mock_coordinator,
        mock_device_light_with_scenes,
    ):
        """Test current_option with scene value as dict."""
        from custom_components.govee.models import GoveeDeviceState

        state = GoveeDeviceState(
            online=True, power_state=True, brightness=100, current_scene="42"
        )
        mock_coordinator.get_state.return_value = state

        entity = GoveeSceneSelect(
            mock_coordinator,
            mock_device_light_with_scenes,
            scene_type="dynamic",
            instance=INSTANCE_LIGHT_SCENE,
        )

        # Populate options map with dict value
        entity._options_map = {
            "Complex Scene": SceneOption(
                name="Complex Scene", value={"id": 42, "params": {}}
            ),
        }

        assert entity.current_option == "Complex Scene"

    def test_current_option_diy_scene(
        self,
        mock_coordinator,
        mock_device_light_with_scenes,
    ):
        """Test current_option for DIY scene."""
        from custom_components.govee.models import GoveeDeviceState

        state = GoveeDeviceState(
            online=True, power_state=True, brightness=100, current_scene="diy_101"
        )
        mock_coordinator.get_state.return_value = state

        entity = GoveeSceneSelect(
            mock_coordinator,
            mock_device_light_with_scenes,
            scene_type="diy",
            instance=INSTANCE_DIY_SCENE,
        )

        # Populate options map
        entity._options_map = {
            "My Custom": SceneOption(name="My Custom", value=101),
        }

        assert entity.current_option == "My Custom"

    @pytest.mark.asyncio
    async def test_async_select_option_success(
        self,
        mock_coordinator,
        mock_device_light_with_scenes,
    ):
        """Test async_select_option sets scene."""
        mock_coordinator.async_control_device = AsyncMock()

        entity = GoveeSceneSelect(
            mock_coordinator,
            mock_device_light_with_scenes,
            scene_type="dynamic",
            instance=INSTANCE_LIGHT_SCENE,
        )

        # Populate options map
        entity._options_map = {
            "Party": SceneOption(name="Party", value=5),
        }

        await entity.async_select_option("Party")

        # Should call coordinator to set scene
        mock_coordinator.async_control_device.assert_called_once_with(
            mock_device_light_with_scenes.device_id,
            CAPABILITY_DYNAMIC_SCENE,
            INSTANCE_LIGHT_SCENE,
            5,
        )

    @pytest.mark.asyncio
    async def test_async_select_option_unknown_scene(
        self,
        mock_coordinator,
        mock_device_light_with_scenes,
        caplog,
    ):
        """Test async_select_option with unknown scene."""
        mock_coordinator.async_control_device = AsyncMock()

        entity = GoveeSceneSelect(
            mock_coordinator,
            mock_device_light_with_scenes,
            scene_type="dynamic",
            instance=INSTANCE_LIGHT_SCENE,
        )

        entity._options_map = {}

        await entity.async_select_option("Unknown Scene")

        # Should log warning and not call coordinator
        assert "Unknown scene" in caplog.text
        mock_coordinator.async_control_device.assert_not_called()

    @pytest.mark.asyncio
    async def test_async_select_option_handles_errors(
        self,
        mock_coordinator,
        mock_device_light_with_scenes,
        caplog,
    ):
        """Test async_select_option handles API errors."""
        mock_coordinator.async_control_device = AsyncMock(
            side_effect=Exception("API error")
        )

        entity = GoveeSceneSelect(
            mock_coordinator,
            mock_device_light_with_scenes,
            scene_type="dynamic",
            instance=INSTANCE_LIGHT_SCENE,
        )

        entity._options_map = {
            "Movie": SceneOption(name="Movie", value=3),
        }

        await entity.async_select_option("Movie")

        # Should log error but not raise
        assert "Failed to set dynamic scene" in caplog.text
        assert "API error" in caplog.text

    @pytest.mark.asyncio
    async def test_async_refresh_scenes_service(
        self,
        hass: HomeAssistant,
        mock_coordinator,
        mock_device_light_with_scenes,
    ):
        """Test async_refresh_scenes service method."""
        mock_coordinator.async_get_dynamic_scenes = AsyncMock(
            return_value=[SceneOption(name="New Scene", value=10)]
        )

        entity = GoveeSceneSelect(
            mock_coordinator,
            mock_device_light_with_scenes,
            scene_type="dynamic",
            instance=INSTANCE_LIGHT_SCENE,
        )

        entity.hass = hass
        entity.async_write_ha_state = MagicMock()

        await entity.async_refresh_scenes()

        # Should refresh options and update state
        assert len(entity._attr_options) == 1
        assert "New Scene" in entity._attr_options
        entity.async_write_ha_state.assert_called_once()
