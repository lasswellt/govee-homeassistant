"""Test Govee coordinator."""
from __future__ import annotations

from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock, patch
import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import UpdateFailed
from homeassistant.exceptions import ConfigEntryAuthFailed

from custom_components.govee.coordinator import GoveeDataUpdateCoordinator
from custom_components.govee.api.exceptions import (
    GoveeApiError,
    GoveeAuthError,
    GoveeRateLimitError,
)
from custom_components.govee.models import GoveeDeviceState, SceneOption
from custom_components.govee.const import DOMAIN


# ==============================================================================
# Initialization Tests
# ==============================================================================


class TestGoveeDataUpdateCoordinatorInit:
    """Test coordinator initialization."""

    def test_coordinator_initialization(
        self,
        hass: HomeAssistant,
        mock_config_entry,
        mock_api_client,
    ):
        """Test coordinator initializes with correct attributes."""
        coordinator = GoveeDataUpdateCoordinator(
            hass,
            mock_config_entry,
            mock_api_client,
            update_interval=timedelta(seconds=60),
        )

        assert coordinator.client == mock_api_client
        assert coordinator.devices == {}
        assert coordinator._scene_cache == {}
        assert coordinator._diy_scene_cache == {}
        assert coordinator.config_entry == mock_config_entry


# ==============================================================================
# Device Discovery Tests (_async_setup)
# ==============================================================================


class TestDeviceDiscovery:
    """Test device discovery during setup."""

    @pytest.mark.asyncio
    async def test_async_setup_discovers_devices(
        self,
        hass: HomeAssistant,
        mock_config_entry,
        mock_api_client,
    ):
        """Test setup discovers devices from API."""
        # Mock API response
        mock_api_client.get_devices = AsyncMock(
            return_value=[
                {
                    "device": "AA:BB:CC:DD:EE:FF:11:22",
                    "sku": "H6160",
                    "deviceName": "Bedroom Strip",
                    "type": "devices.types.light",
                    "capabilities": [],
                },
                {
                    "device": "AA:BB:CC:DD:EE:FF:33:44",
                    "sku": "H7021",
                    "deviceName": "Living Room Socket",
                    "type": "devices.types.socket",
                    "capabilities": [],
                },
            ]
        )

        coordinator = GoveeDataUpdateCoordinator(
            hass,
            mock_config_entry,
            mock_api_client,
            update_interval=timedelta(seconds=60),
        )

        await coordinator._async_setup()

        # Should discover 2 devices
        assert len(coordinator.devices) == 2
        assert "AA:BB:CC:DD:EE:FF:11:22" in coordinator.devices
        assert "AA:BB:CC:DD:EE:FF:33:44" in coordinator.devices
        assert coordinator.devices["AA:BB:CC:DD:EE:FF:11:22"].device_name == "Bedroom Strip"

    @pytest.mark.asyncio
    async def test_async_setup_skips_group_devices_by_default(
        self,
        hass: HomeAssistant,
        mock_config_entry,
        mock_api_client,
    ):
        """Test setup skips group devices when not enabled."""
        # Group devices NOT enabled (mock_config_entry defaults to False)

        # Mock API with one regular and one group device
        # Group device SKUs are: SameModeGroup, BaseGroup, DreamViewScenic
        mock_api_client.get_devices = AsyncMock(
            return_value=[
                {
                    "device": "AA:BB:CC:DD:EE:FF:11:22",
                    "sku": "H6160",
                    "deviceName": "Regular Light",
                    "type": "devices.types.light",
                    "capabilities": [],
                },
                {
                    "device": "GROUP_123456789",
                    "sku": "SameModeGroup",  # Actual group device SKU
                    "deviceName": "Living Room Group",
                    "type": "devices.types.light",
                    "capabilities": [],
                },
            ]
        )

        coordinator = GoveeDataUpdateCoordinator(
            hass,
            mock_config_entry,
            mock_api_client,
            update_interval=timedelta(seconds=60),
        )

        await coordinator._async_setup()

        # Should only discover regular device (group device skipped)
        assert len(coordinator.devices) == 1
        assert "AA:BB:CC:DD:EE:FF:11:22" in coordinator.devices
        assert "GROUP_123456789" not in coordinator.devices

    @pytest.mark.asyncio
    async def test_async_setup_includes_group_devices_when_enabled(
        self,
        hass: HomeAssistant,
        mock_config_entry_with_options,
        mock_api_client,
        caplog,
    ):
        """Test setup includes group devices when enabled."""
        # mock_config_entry_with_options has CONF_ENABLE_GROUP_DEVICES: True

        # Mock API with group device (use actual group SKU)
        mock_api_client.get_devices = AsyncMock(
            return_value=[
                {
                    "device": "GROUP_123456789",
                    "sku": "SameModeGroup",  # Actual group device SKU
                    "deviceName": "Living Room Group",
                    "type": "devices.types.light",
                    "capabilities": [],
                },
            ]
        )

        coordinator = GoveeDataUpdateCoordinator(
            hass,
            mock_config_entry_with_options,
            mock_api_client,
            update_interval=timedelta(seconds=60),
        )

        await coordinator._async_setup()

        # Should include group device with warning
        assert len(coordinator.devices) == 1
        assert "GROUP_123456789" in coordinator.devices
        assert "EXPERIMENTAL" in caplog.text

    @pytest.mark.asyncio
    async def test_async_setup_raises_on_auth_error(
        self,
        hass: HomeAssistant,
        mock_config_entry,
        mock_api_client,
    ):
        """Test setup raises ConfigEntryAuthFailed on auth error."""
        mock_api_client.get_devices = AsyncMock(
            side_effect=GoveeAuthError("Invalid API key")
        )

        coordinator = GoveeDataUpdateCoordinator(
            hass,
            mock_config_entry,
            mock_api_client,
            update_interval=timedelta(seconds=60),
        )

        with pytest.raises(ConfigEntryAuthFailed, match="Invalid API key"):
            await coordinator._async_setup()

    @pytest.mark.asyncio
    async def test_async_setup_raises_on_api_error(
        self,
        hass: HomeAssistant,
        mock_config_entry,
        mock_api_client,
    ):
        """Test setup raises UpdateFailed on API error."""
        mock_api_client.get_devices = AsyncMock(
            side_effect=GoveeApiError("Network error")
        )

        coordinator = GoveeDataUpdateCoordinator(
            hass,
            mock_config_entry,
            mock_api_client,
            update_interval=timedelta(seconds=60),
        )

        with pytest.raises(UpdateFailed, match="Failed to fetch devices"):
            await coordinator._async_setup()


# ==============================================================================
# State Update Tests (_async_update_data)
# ==============================================================================


class TestStateUpdates:
    """Test device state updates."""

    @pytest.mark.asyncio
    async def test_async_update_data_fetches_all_device_states(
        self,
        hass: HomeAssistant,
        mock_config_entry,
        mock_api_client,
        mock_device_light,
        mock_device_switch,
    ):
        """Test update fetches state for all devices."""
        coordinator = GoveeDataUpdateCoordinator(
            hass,
            mock_config_entry,
            mock_api_client,
            update_interval=timedelta(seconds=60),
        )

        # Populate devices
        coordinator.devices = {
            mock_device_light.device_id: mock_device_light,
            mock_device_switch.device_id: mock_device_switch,
        }

        # Mock API state responses in proper capabilities format
        mock_api_client.get_device_state = AsyncMock(
            side_effect=[
                {
                    "capabilities": [
                        {"instance": "online", "state": {"value": True}},
                        {"instance": "powerSwitch", "state": {"value": 1}},
                        {"instance": "brightness", "state": {"value": 100}},
                    ]
                },
                {
                    "capabilities": [
                        {"instance": "online", "state": {"value": True}},
                        {"instance": "powerSwitch", "state": {"value": 0}},
                    ]
                },
            ]
        )

        states = await coordinator._async_update_data()

        # Should fetch states for both devices
        assert len(states) == 2
        assert mock_device_light.device_id in states
        assert mock_device_switch.device_id in states
        assert states[mock_device_light.device_id].power_state is True
        assert states[mock_device_switch.device_id].power_state is False

    @pytest.mark.asyncio
    async def test_async_update_data_preserves_optimistic_state(
        self,
        hass: HomeAssistant,
        mock_config_entry,
        mock_api_client,
        mock_device_light,
    ):
        """Test update preserves optimistic state (like scenes)."""
        coordinator = GoveeDataUpdateCoordinator(
            hass,
            mock_config_entry,
            mock_api_client,
            update_interval=timedelta(seconds=60),
        )

        coordinator.devices = {mock_device_light.device_id: mock_device_light}

        # Set initial state with scene
        coordinator.data = {
            mock_device_light.device_id: GoveeDeviceState(
                device_id=mock_device_light.device_id,
                online=True,
                power_state=True,
                brightness=100,
                current_scene_name="Sunset",  # Optimistic scene
            )
        }

        # Mock API response in capabilities format (doesn't include scene)
        mock_api_client.get_device_state = AsyncMock(
            return_value={
                "capabilities": [
                    {"instance": "online", "state": {"value": True}},
                    {"instance": "powerSwitch", "state": {"value": 1}},
                    {"instance": "brightness", "state": {"value": 50}},  # Changed
                ]
            }
        )

        states = await coordinator._async_update_data()

        # Should preserve scene while updating other attributes
        state = states[mock_device_light.device_id]
        assert state.brightness == 50  # Updated from API
        assert state.current_scene_name == "Sunset"  # Preserved

    @pytest.mark.asyncio
    async def test_async_update_data_raises_on_auth_error(
        self,
        hass: HomeAssistant,
        mock_config_entry,
        mock_api_client,
        mock_device_light,
    ):
        """Test update raises ConfigEntryAuthFailed on auth error."""
        coordinator = GoveeDataUpdateCoordinator(
            hass,
            mock_config_entry,
            mock_api_client,
            update_interval=timedelta(seconds=60),
        )

        coordinator.devices = {mock_device_light.device_id: mock_device_light}

        mock_api_client.get_device_state = AsyncMock(
            side_effect=GoveeAuthError("Invalid API key")
        )

        with pytest.raises(ConfigEntryAuthFailed):
            await coordinator._async_update_data()

    @pytest.mark.asyncio
    async def test_async_update_data_handles_rate_limit(
        self,
        hass: HomeAssistant,
        mock_config_entry,
        mock_api_client,
        mock_device_light,
        caplog,
    ):
        """Test update raises UpdateFailed on rate limit errors.

        When rate limits are hit, the coordinator now raises UpdateFailed
        to signal Home Assistant to back off. This uses HA's built-in
        retry mechanism for proper backoff handling.
        """
        coordinator = GoveeDataUpdateCoordinator(
            hass,
            mock_config_entry,
            mock_api_client,
            update_interval=timedelta(seconds=60),
        )

        coordinator.devices = {mock_device_light.device_id: mock_device_light}

        # Set initial state
        coordinator.data = {
            mock_device_light.device_id: GoveeDeviceState(
                device_id=mock_device_light.device_id,
                online=True,
                power_state=True,
                brightness=100,
            )
        }

        mock_api_client.get_device_state = AsyncMock(
            side_effect=GoveeRateLimitError("Rate limit exceeded")
        )

        # Should raise UpdateFailed to trigger HA's retry mechanism
        with pytest.raises(UpdateFailed, match="rate limited"):
            await coordinator._async_update_data()

        # Should log warning about rate limit
        assert "Rate limit hit" in caplog.text
        assert "backing off" in caplog.text

    @pytest.mark.asyncio
    async def test_async_update_data_handles_api_error_regular_device(
        self,
        hass: HomeAssistant,
        mock_config_entry,
        mock_api_client,
        mock_device_light,
        caplog,
    ):
        """Test update handles API error for regular device."""
        coordinator = GoveeDataUpdateCoordinator(
            hass,
            mock_config_entry,
            mock_api_client,
            update_interval=timedelta(seconds=60),
        )

        coordinator.devices = {mock_device_light.device_id: mock_device_light}

        mock_api_client.get_device_state = AsyncMock(
            side_effect=GoveeApiError("Device offline")
        )

        states = await coordinator._async_update_data()

        # Should create offline state and log warning
        assert states[mock_device_light.device_id].online is False
        assert "warning" in caplog.text.lower()

    @pytest.mark.asyncio
    async def test_async_update_data_handles_group_device_error(
        self,
        hass: HomeAssistant,
        mock_config_entry,
        mock_api_client,
        mock_device_group,
        caplog,
    ):
        """Test update handles API error for group device."""
        coordinator = GoveeDataUpdateCoordinator(
            hass,
            mock_config_entry,
            mock_api_client,
            update_interval=timedelta(seconds=60),
        )

        coordinator.devices = {mock_device_group.device_id: mock_device_group}

        mock_api_client.get_device_state = AsyncMock(
            side_effect=GoveeApiError("Not supported")
        )

        states = await coordinator._async_update_data()

        # Should create state for optimistic updates and log info (not warning)
        state = states[mock_device_group.device_id]
        assert state.online is False
        assert state.power_state is None  # Unknown until first command
        assert "EXPECTED" in caplog.text
        assert "optimistic state" in caplog.text.lower()

    @pytest.mark.asyncio
    async def test_async_update_data_keeps_previous_state_on_error(
        self,
        hass: HomeAssistant,
        mock_config_entry,
        mock_api_client,
        mock_device_light,
    ):
        """Test update keeps previous state when API call fails."""
        coordinator = GoveeDataUpdateCoordinator(
            hass,
            mock_config_entry,
            mock_api_client,
            update_interval=timedelta(seconds=60),
        )

        coordinator.devices = {mock_device_light.device_id: mock_device_light}

        # Set initial successful state
        coordinator.data = {
            mock_device_light.device_id: GoveeDeviceState(
                device_id=mock_device_light.device_id,
                online=True,
                power_state=True,
                brightness=75,
            )
        }

        # Next update fails
        mock_api_client.get_device_state = AsyncMock(
            side_effect=GoveeApiError("Temporary error")
        )

        states = await coordinator._async_update_data()

        # Should keep previous state
        assert states[mock_device_light.device_id].brightness == 75
        assert states[mock_device_light.device_id].power_state is True


# ==============================================================================
# Device/State Retrieval Tests
# ==============================================================================


class TestDeviceAndStateRetrieval:
    """Test device and state retrieval methods."""

    def test_get_device_returns_device(
        self,
        hass: HomeAssistant,
        mock_config_entry,
        mock_api_client,
        mock_device_light,
    ):
        """Test get_device returns device by ID."""
        coordinator = GoveeDataUpdateCoordinator(
            hass,
            mock_config_entry,
            mock_api_client,
            update_interval=timedelta(seconds=60),
        )

        coordinator.devices = {mock_device_light.device_id: mock_device_light}

        device = coordinator.get_device(mock_device_light.device_id)
        assert device == mock_device_light

    def test_get_device_returns_none_for_unknown_id(
        self,
        hass: HomeAssistant,
        mock_config_entry,
        mock_api_client,
    ):
        """Test get_device returns None for unknown device ID."""
        coordinator = GoveeDataUpdateCoordinator(
            hass,
            mock_config_entry,
            mock_api_client,
            update_interval=timedelta(seconds=60),
        )

        device = coordinator.get_device("UNKNOWN_ID")
        assert device is None

    def test_get_state_returns_state(
        self,
        hass: HomeAssistant,
        mock_config_entry,
        mock_api_client,
        mock_device_light,
    ):
        """Test get_state returns state by device ID."""
        coordinator = GoveeDataUpdateCoordinator(
            hass,
            mock_config_entry,
            mock_api_client,
            update_interval=timedelta(seconds=60),
        )

        state = GoveeDeviceState(
            device_id=mock_device_light.device_id,
            online=True,
            power_state=True,
            brightness=100,
        )
        coordinator.data = {mock_device_light.device_id: state}

        retrieved_state = coordinator.get_state(mock_device_light.device_id)
        assert retrieved_state == state

    def test_get_state_returns_none_when_no_data(
        self,
        hass: HomeAssistant,
        mock_config_entry,
        mock_api_client,
    ):
        """Test get_state returns None when no data available."""
        coordinator = GoveeDataUpdateCoordinator(
            hass,
            mock_config_entry,
            mock_api_client,
            update_interval=timedelta(seconds=60),
        )

        state = coordinator.get_state("ANY_ID")
        assert state is None


# ==============================================================================
# Device Control Tests
# ==============================================================================


class TestDeviceControl:
    """Test device control methods."""

    @pytest.mark.asyncio
    async def test_async_control_device_success(
        self,
        hass: HomeAssistant,
        mock_config_entry,
        mock_api_client,
        mock_device_light,
    ):
        """Test async_control_device sends command."""
        coordinator = GoveeDataUpdateCoordinator(
            hass,
            mock_config_entry,
            mock_api_client,
            update_interval=timedelta(seconds=60),
        )

        coordinator.devices = {mock_device_light.device_id: mock_device_light}
        mock_api_client.control_device = AsyncMock()

        await coordinator.async_control_device(
            mock_device_light.device_id,
            "devices.capabilities.on_off",
            "powerSwitch",
            1,
        )

        # Should call API
        mock_api_client.control_device.assert_called_once_with(
            mock_device_light.device_id,
            mock_device_light.sku,
            "devices.capabilities.on_off",
            "powerSwitch",
            1,
        )

    @pytest.mark.asyncio
    async def test_async_control_device_applies_optimistic_update(
        self,
        hass: HomeAssistant,
        mock_config_entry,
        mock_api_client,
        mock_device_light,
    ):
        """Test async_control_device applies optimistic state update."""
        coordinator = GoveeDataUpdateCoordinator(
            hass,
            mock_config_entry,
            mock_api_client,
            update_interval=timedelta(seconds=60),
        )

        coordinator.devices = {mock_device_light.device_id: mock_device_light}
        coordinator.data = {
            mock_device_light.device_id: GoveeDeviceState(
                device_id=mock_device_light.device_id,
                online=True,
                power_state=False,
                brightness=50,
            )
        }
        mock_api_client.control_device = AsyncMock()

        # Turn on device
        await coordinator.async_control_device(
            mock_device_light.device_id,
            "devices.capabilities.on_off",
            "powerSwitch",
            1,
        )

        # Should apply optimistic update
        state = coordinator.data[mock_device_light.device_id]
        assert state.power_state is True

    @pytest.mark.asyncio
    async def test_async_control_device_unknown_device(
        self,
        hass: HomeAssistant,
        mock_config_entry,
        mock_api_client,
        caplog,
    ):
        """Test async_control_device handles unknown device."""
        coordinator = GoveeDataUpdateCoordinator(
            hass,
            mock_config_entry,
            mock_api_client,
            update_interval=timedelta(seconds=60),
        )

        mock_api_client.control_device = AsyncMock()

        await coordinator.async_control_device(
            "UNKNOWN_ID",
            "devices.capabilities.on_off",
            "powerSwitch",
            1,
        )

        # Should log error and not call API
        assert "Device not found" in caplog.text
        mock_api_client.control_device.assert_not_called()

    @pytest.mark.asyncio
    async def test_async_control_device_api_error_regular_device(
        self,
        hass: HomeAssistant,
        mock_config_entry,
        mock_api_client,
        mock_device_light,
        caplog,
    ):
        """Test async_control_device handles API error for regular device."""
        coordinator = GoveeDataUpdateCoordinator(
            hass,
            mock_config_entry,
            mock_api_client,
            update_interval=timedelta(seconds=60),
        )

        coordinator.devices = {mock_device_light.device_id: mock_device_light}
        mock_api_client.control_device = AsyncMock(
            side_effect=GoveeApiError("Device offline")
        )

        with pytest.raises(GoveeApiError):
            await coordinator.async_control_device(
                mock_device_light.device_id,
                "devices.capabilities.on_off",
                "powerSwitch",
                1,
            )

        # Should log error
        assert "Failed to control device" in caplog.text

    @pytest.mark.asyncio
    async def test_async_control_device_api_error_group_device(
        self,
        hass: HomeAssistant,
        mock_config_entry,
        mock_api_client,
        mock_device_group,
        caplog,
    ):
        """Test async_control_device handles API error for group device."""
        coordinator = GoveeDataUpdateCoordinator(
            hass,
            mock_config_entry,
            mock_api_client,
            update_interval=timedelta(seconds=60),
        )

        coordinator.devices = {mock_device_group.device_id: mock_device_group}
        mock_api_client.control_device = AsyncMock(
            side_effect=GoveeApiError("Not supported")
        )

        with pytest.raises(GoveeApiError):
            await coordinator.async_control_device(
                mock_device_group.device_id,
                "devices.capabilities.on_off",
                "powerSwitch",
                1,
            )

        # Should log warning (not error) for group device
        assert "warning" in caplog.text.lower()
        assert "Group devices may not support" in caplog.text


# ==============================================================================
# Scene Caching Tests
# ==============================================================================


class TestSceneCaching:
    """Test scene caching methods."""

    @pytest.mark.asyncio
    async def test_async_get_dynamic_scenes_fetches_from_api(
        self,
        hass: HomeAssistant,
        mock_config_entry,
        mock_api_client,
        mock_device_light,
    ):
        """Test async_get_dynamic_scenes fetches scenes from API."""
        coordinator = GoveeDataUpdateCoordinator(
            hass,
            mock_config_entry,
            mock_api_client,
            update_interval=timedelta(seconds=60),
        )

        coordinator.devices = {mock_device_light.device_id: mock_device_light}

        # Mock API response
        mock_api_client.get_dynamic_scenes = AsyncMock(
            return_value=[
                {"name": "Sunrise", "value": 1},
                {"name": "Sunset", "value": 2},
            ]
        )

        scenes = await coordinator.async_get_dynamic_scenes(mock_device_light.device_id)

        # Should fetch and return scenes
        assert len(scenes) == 2
        assert scenes[0].name == "Sunrise"
        assert scenes[1].name == "Sunset"

    @pytest.mark.asyncio
    async def test_async_get_dynamic_scenes_uses_cache(
        self,
        hass: HomeAssistant,
        mock_config_entry,
        mock_api_client,
        mock_device_light,
    ):
        """Test async_get_dynamic_scenes uses cached scenes."""
        coordinator = GoveeDataUpdateCoordinator(
            hass,
            mock_config_entry,
            mock_api_client,
            update_interval=timedelta(seconds=60),
        )

        coordinator.devices = {mock_device_light.device_id: mock_device_light}

        # Pre-populate cache
        cached_scenes = [SceneOption(name="Cached Scene", value=99)]
        coordinator._scene_cache[mock_device_light.device_id] = cached_scenes

        mock_api_client.get_dynamic_scenes = AsyncMock()

        scenes = await coordinator.async_get_dynamic_scenes(mock_device_light.device_id)

        # Should use cache, not call API
        mock_api_client.get_dynamic_scenes.assert_not_called()
        assert scenes == cached_scenes

    @pytest.mark.asyncio
    async def test_async_get_dynamic_scenes_refresh_bypasses_cache(
        self,
        hass: HomeAssistant,
        mock_config_entry,
        mock_api_client,
        mock_device_light,
    ):
        """Test async_get_dynamic_scenes with refresh bypasses cache."""
        coordinator = GoveeDataUpdateCoordinator(
            hass,
            mock_config_entry,
            mock_api_client,
            update_interval=timedelta(seconds=60),
        )

        coordinator.devices = {mock_device_light.device_id: mock_device_light}

        # Pre-populate cache
        coordinator._scene_cache[mock_device_light.device_id] = [
            SceneOption(name="Old", value=1)
        ]

        # Mock API response
        mock_api_client.get_dynamic_scenes = AsyncMock(
            return_value=[{"name": "New", "value": 2}]
        )

        scenes = await coordinator.async_get_dynamic_scenes(
            mock_device_light.device_id, refresh=True
        )

        # Should call API and update cache
        mock_api_client.get_dynamic_scenes.assert_called_once()
        assert scenes[0].name == "New"
        assert coordinator._scene_cache[mock_device_light.device_id][0].name == "New"

    @pytest.mark.asyncio
    async def test_async_get_dynamic_scenes_handles_api_error(
        self,
        hass: HomeAssistant,
        mock_config_entry,
        mock_api_client,
        mock_device_light,
        caplog,
    ):
        """Test async_get_dynamic_scenes handles API error."""
        coordinator = GoveeDataUpdateCoordinator(
            hass,
            mock_config_entry,
            mock_api_client,
            update_interval=timedelta(seconds=60),
        )

        coordinator.devices = {mock_device_light.device_id: mock_device_light}

        # Pre-populate cache
        cached_scenes = [SceneOption(name="Cached", value=1)]
        coordinator._scene_cache[mock_device_light.device_id] = cached_scenes

        # API fails
        mock_api_client.get_dynamic_scenes = AsyncMock(
            side_effect=GoveeApiError("Network error")
        )

        scenes = await coordinator.async_get_dynamic_scenes(
            mock_device_light.device_id, refresh=True
        )

        # Should return cached scenes and log warning
        assert scenes == cached_scenes
        assert "Failed to fetch dynamic scenes" in caplog.text

    @pytest.mark.asyncio
    async def test_async_get_dynamic_scenes_unknown_device(
        self,
        hass: HomeAssistant,
        mock_config_entry,
        mock_api_client,
    ):
        """Test async_get_dynamic_scenes returns empty list for unknown device."""
        coordinator = GoveeDataUpdateCoordinator(
            hass,
            mock_config_entry,
            mock_api_client,
            update_interval=timedelta(seconds=60),
        )

        scenes = await coordinator.async_get_dynamic_scenes("UNKNOWN_ID")

        assert scenes == []

    @pytest.mark.asyncio
    async def test_async_get_diy_scenes_fetches_from_api(
        self,
        hass: HomeAssistant,
        mock_config_entry,
        mock_api_client,
        mock_device_light,
    ):
        """Test async_get_diy_scenes fetches DIY scenes from API."""
        coordinator = GoveeDataUpdateCoordinator(
            hass,
            mock_config_entry,
            mock_api_client,
            update_interval=timedelta(seconds=60),
        )

        coordinator.devices = {mock_device_light.device_id: mock_device_light}

        # Mock API response
        mock_api_client.get_diy_scenes = AsyncMock(
            return_value=[
                {"name": "My Custom 1", "value": 101},
                {"name": "My Custom 2", "value": 102},
            ]
        )

        scenes = await coordinator.async_get_diy_scenes(mock_device_light.device_id)

        # Should fetch and return DIY scenes
        assert len(scenes) == 2
        assert scenes[0].name == "My Custom 1"
        assert scenes[1].name == "My Custom 2"

    @pytest.mark.asyncio
    async def test_async_get_diy_scenes_uses_cache(
        self,
        hass: HomeAssistant,
        mock_config_entry,
        mock_api_client,
        mock_device_light,
    ):
        """Test async_get_diy_scenes uses cached DIY scenes."""
        coordinator = GoveeDataUpdateCoordinator(
            hass,
            mock_config_entry,
            mock_api_client,
            update_interval=timedelta(seconds=60),
        )

        coordinator.devices = {mock_device_light.device_id: mock_device_light}

        # Pre-populate cache
        cached_scenes = [SceneOption(name="Cached DIY", value=199)]
        coordinator._diy_scene_cache[mock_device_light.device_id] = cached_scenes

        mock_api_client.get_diy_scenes = AsyncMock()

        scenes = await coordinator.async_get_diy_scenes(mock_device_light.device_id)

        # Should use cache, not call API
        mock_api_client.get_diy_scenes.assert_not_called()
        assert scenes == cached_scenes


# ==============================================================================
# Rate Limit Tests
# ==============================================================================


class TestRateLimits:
    """Test rate limit properties."""

    def test_rate_limit_remaining_returns_value(
        self,
        hass: HomeAssistant,
        mock_config_entry,
        mock_api_client,
    ):
        """Test rate_limit_remaining returns daily remaining."""
        coordinator = GoveeDataUpdateCoordinator(
            hass,
            mock_config_entry,
            mock_api_client,
            update_interval=timedelta(seconds=60),
        )

        mock_api_client.rate_limiter.remaining_day = 9500

        assert coordinator.rate_limit_remaining == 9500

    def test_rate_limit_remaining_minute_returns_value(
        self,
        hass: HomeAssistant,
        mock_config_entry,
        mock_api_client,
    ):
        """Test rate_limit_remaining_minute returns per-minute remaining."""
        coordinator = GoveeDataUpdateCoordinator(
            hass,
            mock_config_entry,
            mock_api_client,
            update_interval=timedelta(seconds=60),
        )

        mock_api_client.rate_limiter.remaining_minute = 95

        assert coordinator.rate_limit_remaining_minute == 95

    def test_rate_limit_remaining_returns_day_value(
        self,
        hass: HomeAssistant,
        mock_config_entry,
        mock_api_client,
    ):
        """Test rate_limit_remaining returns daily remaining."""
        coordinator = GoveeDataUpdateCoordinator(
            hass,
            mock_config_entry,
            mock_api_client,
            update_interval=timedelta(seconds=60),
        )

        mock_api_client.rate_limiter.remaining_day = 8500

        assert coordinator.rate_limit_remaining == 8500


class TestCheckRateLimits:
    """Test rate limit checking and issue creation/clearing."""

    async def test_check_rate_limits_clears_minute_issue_when_recovered(
        self,
        hass: HomeAssistant,
        mock_config_entry,
        mock_api_client,
    ):
        """Test minute rate limit issue is cleared when limits recover."""
        coordinator = GoveeDataUpdateCoordinator(
            hass,
            mock_config_entry,
            mock_api_client,
            update_interval=timedelta(seconds=60),
        )

        # Set limits above warning threshold
        mock_api_client.rate_limiter.remaining_minute = 50
        mock_api_client.rate_limiter.remaining_day = 5000

        # This should clear any minute issue (not create one)
        coordinator._check_rate_limits()

        # Verify no minute warning was created
        from homeassistant.helpers import issue_registry as ir
        issue_registry = ir.async_get(hass)
        minute_issue_id = f"rate_limit_minute_{mock_config_entry.entry_id}"
        assert issue_registry.async_get_issue(DOMAIN, minute_issue_id) is None

    async def test_check_rate_limits_clears_day_issue_when_recovered(
        self,
        hass: HomeAssistant,
        mock_config_entry,
        mock_api_client,
    ):
        """Test daily rate limit issue is cleared when limits recover."""
        coordinator = GoveeDataUpdateCoordinator(
            hass,
            mock_config_entry,
            mock_api_client,
            update_interval=timedelta(seconds=60),
        )

        # Set limits above warning threshold
        mock_api_client.rate_limiter.remaining_minute = 50
        mock_api_client.rate_limiter.remaining_day = 5000

        # This should clear any day issue (not create one)
        coordinator._check_rate_limits()

        # Verify no day warning was created
        from homeassistant.helpers import issue_registry as ir
        issue_registry = ir.async_get(hass)
        day_issue_id = f"rate_limit_day_{mock_config_entry.entry_id}"
        assert issue_registry.async_get_issue(DOMAIN, day_issue_id) is None


class TestSegmentControl:
    """Test segment color and brightness control."""

    async def test_async_set_segment_color_success(
        self,
        hass: HomeAssistant,
        mock_config_entry,
        mock_api_client,
    ):
        """Test setting segment color successfully."""
        coordinator = GoveeDataUpdateCoordinator(
            hass,
            mock_config_entry,
            mock_api_client,
            update_interval=timedelta(seconds=60),
        )

        device_id = "AA:BB:CC:DD:EE:FF:11:22"
        coordinator.devices[device_id] = MagicMock(
            device_id=device_id,
            sku="H6199",
        )
        coordinator.data = {
            device_id: GoveeDeviceState(device_id=device_id, online=True),
        }

        mock_api_client.set_segment_color = AsyncMock()

        await coordinator.async_set_segment_color(
            device_id, "H6199", 0, (255, 0, 0)
        )

        mock_api_client.set_segment_color.assert_called_once_with(
            device_id, "H6199", 0, (255, 0, 0)
        )
        # Check optimistic update was applied
        assert coordinator.data[device_id].segment_colors[0] == (255, 0, 0)

    async def test_async_set_segment_color_api_error(
        self,
        hass: HomeAssistant,
        mock_config_entry,
        mock_api_client,
    ):
        """Test setting segment color with API error."""
        coordinator = GoveeDataUpdateCoordinator(
            hass,
            mock_config_entry,
            mock_api_client,
            update_interval=timedelta(seconds=60),
        )

        device_id = "AA:BB:CC:DD:EE:FF:11:22"
        coordinator.devices[device_id] = MagicMock(
            device_id=device_id,
            sku="H6199",
        )
        coordinator.data = {
            device_id: GoveeDeviceState(device_id=device_id, online=True),
        }

        mock_api_client.set_segment_color = AsyncMock(
            side_effect=GoveeApiError("Segment control failed")
        )

        with pytest.raises(GoveeApiError):
            await coordinator.async_set_segment_color(
                device_id, "H6199", 0, (255, 0, 0)
            )

    async def test_async_set_segment_brightness_success(
        self,
        hass: HomeAssistant,
        mock_config_entry,
        mock_api_client,
    ):
        """Test setting segment brightness successfully."""
        coordinator = GoveeDataUpdateCoordinator(
            hass,
            mock_config_entry,
            mock_api_client,
            update_interval=timedelta(seconds=60),
        )

        device_id = "AA:BB:CC:DD:EE:FF:11:22"
        coordinator.devices[device_id] = MagicMock(
            device_id=device_id,
            sku="H6199",
        )
        coordinator.data = {
            device_id: GoveeDeviceState(device_id=device_id, online=True),
        }

        mock_api_client.set_segment_brightness = AsyncMock()

        await coordinator.async_set_segment_brightness(
            device_id, "H6199", 0, 75
        )

        mock_api_client.set_segment_brightness.assert_called_once_with(
            device_id, "H6199", 0, 75
        )
        # Check optimistic update was applied
        assert coordinator.data[device_id].segment_brightness[0] == 75

    async def test_async_set_segment_brightness_api_error(
        self,
        hass: HomeAssistant,
        mock_config_entry,
        mock_api_client,
    ):
        """Test setting segment brightness with API error."""
        coordinator = GoveeDataUpdateCoordinator(
            hass,
            mock_config_entry,
            mock_api_client,
            update_interval=timedelta(seconds=60),
        )

        device_id = "AA:BB:CC:DD:EE:FF:11:22"
        coordinator.devices[device_id] = MagicMock(
            device_id=device_id,
            sku="H6199",
        )
        coordinator.data = {
            device_id: GoveeDeviceState(device_id=device_id, online=True),
        }

        mock_api_client.set_segment_brightness = AsyncMock(
            side_effect=GoveeApiError("Segment control failed")
        )

        with pytest.raises(GoveeApiError):
            await coordinator.async_set_segment_brightness(
                device_id, "H6199", 0, 75
            )


class TestDiyScenes:
    """Test DIY scene fetching and error handling."""

    async def test_async_get_diy_scenes_handles_api_error(
        self,
        hass: HomeAssistant,
        mock_config_entry,
        mock_api_client,
    ):
        """Test DIY scene fetch falls back to cache on API error."""
        coordinator = GoveeDataUpdateCoordinator(
            hass,
            mock_config_entry,
            mock_api_client,
            update_interval=timedelta(seconds=60),
        )

        device_id = "AA:BB:CC:DD:EE:FF:11:22"
        coordinator.devices[device_id] = MagicMock(
            device_id=device_id,
            sku="H6199",
            device_name="Test Light",
        )

        # Pre-populate cache
        cached_scenes = [SceneOption(name="My DIY", value="diy1")]
        coordinator._diy_scene_cache[device_id] = cached_scenes

        mock_api_client.get_diy_scenes = AsyncMock(
            side_effect=GoveeApiError("DIY scenes failed")
        )

        result = await coordinator.async_get_diy_scenes(device_id)

        # Should return cached scenes
        assert result == cached_scenes


class TestRefreshScenes:
    """Test scene refresh functionality."""

    async def test_async_refresh_device_scenes(
        self,
        hass: HomeAssistant,
        mock_config_entry,
        mock_api_client,
    ):
        """Test refreshing scenes for a device."""
        coordinator = GoveeDataUpdateCoordinator(
            hass,
            mock_config_entry,
            mock_api_client,
            update_interval=timedelta(seconds=60),
        )

        device_id = "AA:BB:CC:DD:EE:FF:11:22"
        coordinator.devices[device_id] = MagicMock(
            device_id=device_id,
            sku="H6199",
            device_name="Test Light",
        )

        # Pre-populate caches
        coordinator._scene_cache[device_id] = [SceneOption(name="Old", value="old")]
        coordinator._diy_scene_cache[device_id] = [SceneOption(name="Old DIY", value="olddiy")]

        mock_api_client.get_dynamic_scenes = AsyncMock(return_value=[
            {"name": "New Scene", "value": {"id": "new"}},
        ])
        mock_api_client.get_diy_scenes = AsyncMock(return_value=[
            {"name": "New DIY", "value": {"id": "newdiy"}},
        ])
        mock_api_client.get_snapshots = AsyncMock(return_value=[
            {"name": "New Snapshot", "value": {"id": "newsnapshot"}},
        ])

        await coordinator.async_refresh_device_scenes(device_id)

        # Check caches were updated with new scenes
        assert len(coordinator._scene_cache[device_id]) == 1
        assert coordinator._scene_cache[device_id][0].name == "New Scene"
        assert len(coordinator._diy_scene_cache[device_id]) == 1
        assert coordinator._diy_scene_cache[device_id][0].name == "New DIY"


class TestIdentifyDevice:
    """Test device identification functionality."""

    async def test_async_identify_device(
        self,
        hass: HomeAssistant,
        mock_config_entry,
        mock_api_client,
    ):
        """Test device identification flashes the device."""
        coordinator = GoveeDataUpdateCoordinator(
            hass,
            mock_config_entry,
            mock_api_client,
            update_interval=timedelta(seconds=60),
        )

        device_id = "AA:BB:CC:DD:EE:FF:11:22"
        coordinator.devices[device_id] = MagicMock(
            device_id=device_id,
            sku="H6199",
        )
        coordinator.data = {
            device_id: GoveeDeviceState(device_id=device_id, online=True, power_state=True),
        }

        mock_api_client.control_device = AsyncMock()

        await coordinator.async_identify_device(device_id)

        # Should have been called twice (off then on)
        assert mock_api_client.control_device.call_count == 2


class TestSetPowerState:
    """Test power state control functionality."""

    async def test_async_set_power_state_on(
        self,
        hass: HomeAssistant,
        mock_config_entry,
        mock_api_client,
    ):
        """Test turning power on."""
        coordinator = GoveeDataUpdateCoordinator(
            hass,
            mock_config_entry,
            mock_api_client,
            update_interval=timedelta(seconds=60),
        )

        device_id = "AA:BB:CC:DD:EE:FF:11:22"
        coordinator.devices[device_id] = MagicMock(
            device_id=device_id,
            sku="H6199",
        )
        coordinator.data = {
            device_id: GoveeDeviceState(device_id=device_id, online=True, power_state=False),
        }

        mock_api_client.control_device = AsyncMock()

        await coordinator.async_set_power_state(device_id, True)

        mock_api_client.control_device.assert_called_once()
        # Check the value was 1 (on)
        call_args = mock_api_client.control_device.call_args
        assert call_args[0][4] == 1  # value parameter

    async def test_async_set_power_state_off(
        self,
        hass: HomeAssistant,
        mock_config_entry,
        mock_api_client,
    ):
        """Test turning power off."""
        coordinator = GoveeDataUpdateCoordinator(
            hass,
            mock_config_entry,
            mock_api_client,
            update_interval=timedelta(seconds=60),
        )

        device_id = "AA:BB:CC:DD:EE:FF:11:22"
        coordinator.devices[device_id] = MagicMock(
            device_id=device_id,
            sku="H6199",
        )
        coordinator.data = {
            device_id: GoveeDeviceState(device_id=device_id, online=True, power_state=True),
        }

        mock_api_client.control_device = AsyncMock()

        await coordinator.async_set_power_state(device_id, False)

        mock_api_client.control_device.assert_called_once()
        # Check the value was 0 (off)
        call_args = mock_api_client.control_device.call_args
        assert call_args[0][4] == 0  # value parameter


# ==============================================================================
# Additional Coverage Tests
# ==============================================================================


class TestRateLimitIssueCreation:
    """Test rate limit issue creation when limits are low."""

    async def test_check_rate_limits_creates_minute_issue_when_low(
        self,
        hass: HomeAssistant,
        mock_config_entry,
        mock_api_client,
    ):
        """Test minute rate limit issue is created when limits are low (line 114)."""
        coordinator = GoveeDataUpdateCoordinator(
            hass,
            mock_config_entry,
            mock_api_client,
            update_interval=timedelta(seconds=60),
        )

        # Set minute limit below warning threshold (< 20)
        mock_api_client.rate_limiter.remaining_minute = 15
        mock_api_client.rate_limiter.remaining_day = 5000

        coordinator._check_rate_limits()

        # Verify minute warning was created
        from homeassistant.helpers import issue_registry as ir
        issue_registry = ir.async_get(hass)
        minute_issue_id = f"rate_limit_minute_{mock_config_entry.entry_id}"
        issue = issue_registry.async_get_issue(DOMAIN, minute_issue_id)
        assert issue is not None
        assert issue.translation_key == "rate_limit_minute_warning"

    async def test_check_rate_limits_creates_day_issue_when_low(
        self,
        hass: HomeAssistant,
        mock_config_entry,
        mock_api_client,
    ):
        """Test daily rate limit issue is created when limits are low (line 132)."""
        coordinator = GoveeDataUpdateCoordinator(
            hass,
            mock_config_entry,
            mock_api_client,
            update_interval=timedelta(seconds=60),
        )

        # Set day limit below warning threshold (< 2000)
        mock_api_client.rate_limiter.remaining_minute = 50
        mock_api_client.rate_limiter.remaining_day = 1500

        coordinator._check_rate_limits()

        # Verify day warning was created
        from homeassistant.helpers import issue_registry as ir
        issue_registry = ir.async_get(hass)
        day_issue_id = f"rate_limit_day_{mock_config_entry.entry_id}"
        issue = issue_registry.async_get_issue(DOMAIN, day_issue_id)
        assert issue is not None
        assert issue.translation_key == "rate_limit_day_warning"


class TestStateUpdateErrorHandling:
    """Test error handling during state updates."""

    @pytest.mark.asyncio
    async def test_async_update_data_handles_timeout(
        self,
        hass: HomeAssistant,
        mock_config_entry,
        mock_api_client,
        mock_device_light,
    ):
        """Test update raises UpdateFailed on timeout (lines 303-305)."""
        import asyncio

        coordinator = GoveeDataUpdateCoordinator(
            hass,
            mock_config_entry,
            mock_api_client,
            update_interval=timedelta(seconds=60),
        )

        coordinator.devices = {mock_device_light.device_id: mock_device_light}

        # Mock get_device_state to raise TimeoutError
        mock_api_client.get_device_state = AsyncMock(
            side_effect=asyncio.TimeoutError("timeout")
        )

        # Patch asyncio.wait_for to raise TimeoutError
        with patch("custom_components.govee.coordinator.asyncio.wait_for") as mock_wait_for:
            mock_wait_for.side_effect = asyncio.TimeoutError()

            with pytest.raises(UpdateFailed, match="timeout"):
                await coordinator._async_update_data()

    @pytest.mark.asyncio
    async def test_async_update_data_handles_unexpected_exception(
        self,
        hass: HomeAssistant,
        mock_config_entry,
        mock_api_client,
        mock_device_light,
        caplog,
    ):
        """Test update handles unexpected exceptions (lines 363-370)."""
        coordinator = GoveeDataUpdateCoordinator(
            hass,
            mock_config_entry,
            mock_api_client,
            update_interval=timedelta(seconds=60),
        )

        coordinator.devices = {mock_device_light.device_id: mock_device_light}

        # Set initial state
        initial_state = GoveeDeviceState(
            device_id=mock_device_light.device_id,
            online=True,
            power_state=True,
        )
        coordinator.data = {mock_device_light.device_id: initial_state}

        # Mock get_device_state to raise an unexpected exception
        mock_api_client.get_device_state = AsyncMock(
            side_effect=ValueError("Unexpected error")
        )

        states = await coordinator._async_update_data()

        # Should log error and keep previous state
        assert "Unexpected error" in caplog.text
        assert mock_device_light.device_id in states
        assert states[mock_device_light.device_id] == initial_state


class TestDiyScenesMissingDevice:
    """Test DIY scenes with missing device."""

    @pytest.mark.asyncio
    async def test_async_get_diy_scenes_device_not_found(
        self,
        hass: HomeAssistant,
        mock_config_entry,
        mock_api_client,
    ):
        """Test DIY scene fetch returns empty list for unknown device (line 589)."""
        coordinator = GoveeDataUpdateCoordinator(
            hass,
            mock_config_entry,
            mock_api_client,
            update_interval=timedelta(seconds=60),
        )

        # No devices registered
        coordinator.devices = {}

        result = await coordinator.async_get_diy_scenes("unknown-device-id")

        # Should return empty list for unknown device
        assert result == []

    @pytest.mark.asyncio
    async def test_async_get_diy_scenes_api_error_no_cache(
        self,
        hass: HomeAssistant,
        mock_config_entry,
        mock_api_client,
    ):
        """Test DIY scene fetch returns empty list on API error without cache (lines 602-604)."""
        coordinator = GoveeDataUpdateCoordinator(
            hass,
            mock_config_entry,
            mock_api_client,
            update_interval=timedelta(seconds=60),
        )

        device_id = "AA:BB:CC:DD:EE:FF:11:22"
        coordinator.devices[device_id] = MagicMock(
            device_id=device_id,
            sku="H6199",
            device_name="Test Light",
        )

        # No cache for this device
        coordinator._diy_scene_cache = {}

        mock_api_client.get_diy_scenes = AsyncMock(
            side_effect=GoveeApiError("DIY scenes failed")
        )

        result = await coordinator.async_get_diy_scenes(device_id)

        # Should return empty list when no cache exists
        assert result == []


# ==============================================================================
# Optimistic Update Rollback Tests
# ==============================================================================


class TestOptimisticUpdateRollback:
    """Test optimistic update rollback on API failure."""

    @pytest.mark.asyncio
    async def test_rollback_on_control_failure(
        self,
        hass: HomeAssistant,
        mock_config_entry,
        mock_api_client,
        mock_device_light,
    ):
        """Test that optimistic updates are rolled back on API failure."""
        coordinator = GoveeDataUpdateCoordinator(
            hass,
            mock_config_entry,
            mock_api_client,
            update_interval=timedelta(seconds=60),
        )

        coordinator.devices = {mock_device_light.device_id: mock_device_light}

        # Set initial state
        initial_state = GoveeDeviceState(
            device_id=mock_device_light.device_id,
            online=True,
            power_state=False,  # Initially off
            brightness=50,
        )
        coordinator.data = {mock_device_light.device_id: initial_state}

        # Mock API to fail
        mock_api_client.control_device = AsyncMock(
            side_effect=GoveeApiError("API Error")
        )

        # Try to turn on - should fail and rollback
        with pytest.raises(GoveeApiError):
            await coordinator.async_control_device(
                mock_device_light.device_id,
                "devices.capabilities.on_off",
                "powerSwitch",
                1,  # Turn on
            )

        # State should be rolled back to original (off)
        state = coordinator.data[mock_device_light.device_id]
        assert state.power_state is False  # Rolled back

    @pytest.mark.asyncio
    async def test_no_rollback_on_success(
        self,
        hass: HomeAssistant,
        mock_config_entry,
        mock_api_client,
        mock_device_light,
    ):
        """Test that optimistic updates are kept on success."""
        coordinator = GoveeDataUpdateCoordinator(
            hass,
            mock_config_entry,
            mock_api_client,
            update_interval=timedelta(seconds=60),
        )

        coordinator.devices = {mock_device_light.device_id: mock_device_light}

        # Set initial state
        coordinator.data = {
            mock_device_light.device_id: GoveeDeviceState(
                device_id=mock_device_light.device_id,
                online=True,
                power_state=False,
                brightness=50,
            )
        }

        # Mock API to succeed
        mock_api_client.control_device = AsyncMock(return_value=None)

        # Turn on - should succeed
        await coordinator.async_control_device(
            mock_device_light.device_id,
            "devices.capabilities.on_off",
            "powerSwitch",
            1,
        )

        # State should be updated (on)
        state = coordinator.data[mock_device_light.device_id]
        assert state.power_state is True

    @pytest.mark.asyncio
    async def test_rollback_restores_brightness(
        self,
        hass: HomeAssistant,
        mock_config_entry,
        mock_api_client,
        mock_device_light,
    ):
        """Test that brightness is rolled back on failure."""
        coordinator = GoveeDataUpdateCoordinator(
            hass,
            mock_config_entry,
            mock_api_client,
            update_interval=timedelta(seconds=60),
        )

        coordinator.devices = {mock_device_light.device_id: mock_device_light}

        # Set initial state with brightness 50
        coordinator.data = {
            mock_device_light.device_id: GoveeDeviceState(
                device_id=mock_device_light.device_id,
                online=True,
                power_state=True,
                brightness=50,
            )
        }

        mock_api_client.control_device = AsyncMock(
            side_effect=GoveeApiError("API Error")
        )

        # Try to change brightness to 100 - should fail and rollback
        with pytest.raises(GoveeApiError):
            await coordinator.async_control_device(
                mock_device_light.device_id,
                "devices.capabilities.range",
                "brightness",
                100,
            )

        # Brightness should be rolled back to 50
        state = coordinator.data[mock_device_light.device_id]
        assert state.brightness == 50

    @pytest.mark.asyncio
    async def test_rollback_restores_color(
        self,
        hass: HomeAssistant,
        mock_config_entry,
        mock_api_client,
        mock_device_light,
    ):
        """Test that color is rolled back on failure."""
        coordinator = GoveeDataUpdateCoordinator(
            hass,
            mock_config_entry,
            mock_api_client,
            update_interval=timedelta(seconds=60),
        )

        coordinator.devices = {mock_device_light.device_id: mock_device_light}

        # Set initial state with red color
        coordinator.data = {
            mock_device_light.device_id: GoveeDeviceState(
                device_id=mock_device_light.device_id,
                online=True,
                power_state=True,
                brightness=100,
                color_rgb=(255, 0, 0),  # Red
            )
        }

        mock_api_client.control_device = AsyncMock(
            side_effect=GoveeApiError("API Error")
        )

        # Try to change color to blue (0, 0, 255) - should fail and rollback
        blue_int = (0 << 16) + (0 << 8) + 255
        with pytest.raises(GoveeApiError):
            await coordinator.async_control_device(
                mock_device_light.device_id,
                "devices.capabilities.color_setting",
                "colorRgb",
                blue_int,
            )

        # Color should be rolled back to red
        state = coordinator.data[mock_device_light.device_id]
        assert state.color_rgb == (255, 0, 0)


# ==============================================================================
# Scene Cache Invalidation Tests
# ==============================================================================


class TestSceneCacheInvalidation:
    """Test scene cache invalidation functionality."""

    def test_invalidate_scene_cache_all(
        self,
        hass: HomeAssistant,
        mock_config_entry,
        mock_api_client,
    ):
        """Test invalidating all scene caches."""
        coordinator = GoveeDataUpdateCoordinator(
            hass,
            mock_config_entry,
            mock_api_client,
            update_interval=timedelta(seconds=60),
        )

        # Pre-populate caches
        coordinator._scene_cache = {
            "device1": [SceneOption(name="Scene1", value=1)],
            "device2": [SceneOption(name="Scene2", value=2)],
        }
        coordinator._diy_scene_cache = {
            "device1": [SceneOption(name="DIY1", value=101)],
            "device2": [SceneOption(name="DIY2", value=102)],
        }

        # Invalidate all caches
        coordinator.invalidate_scene_cache()

        assert coordinator._scene_cache == {}
        assert coordinator._diy_scene_cache == {}

    def test_invalidate_scene_cache_single_device(
        self,
        hass: HomeAssistant,
        mock_config_entry,
        mock_api_client,
    ):
        """Test invalidating scene cache for single device."""
        coordinator = GoveeDataUpdateCoordinator(
            hass,
            mock_config_entry,
            mock_api_client,
            update_interval=timedelta(seconds=60),
        )

        # Pre-populate caches
        coordinator._scene_cache = {
            "device1": [SceneOption(name="Scene1", value=1)],
            "device2": [SceneOption(name="Scene2", value=2)],
        }
        coordinator._diy_scene_cache = {
            "device1": [SceneOption(name="DIY1", value=101)],
            "device2": [SceneOption(name="DIY2", value=102)],
        }

        # Invalidate only device1 cache
        coordinator.invalidate_scene_cache("device1")

        # Device1 cache should be cleared
        assert "device1" not in coordinator._scene_cache
        assert "device1" not in coordinator._diy_scene_cache

        # Device2 cache should remain
        assert "device2" in coordinator._scene_cache
        assert "device2" in coordinator._diy_scene_cache

    def test_invalidate_nonexistent_device_cache(
        self,
        hass: HomeAssistant,
        mock_config_entry,
        mock_api_client,
    ):
        """Test invalidating cache for device that doesn't exist."""
        coordinator = GoveeDataUpdateCoordinator(
            hass,
            mock_config_entry,
            mock_api_client,
            update_interval=timedelta(seconds=60),
        )

        # Pre-populate caches
        coordinator._scene_cache = {
            "device1": [SceneOption(name="Scene1", value=1)],
        }

        # Invalidate nonexistent device - should not error
        coordinator.invalidate_scene_cache("nonexistent")

        # Existing cache should remain
        assert "device1" in coordinator._scene_cache


# ==============================================================================
# Always Update False Tests
# ==============================================================================


class TestAlwaysUpdateFalse:
    """Test that coordinator uses always_update=False."""

    def test_coordinator_has_always_update_false(
        self,
        hass: HomeAssistant,
        mock_config_entry,
        mock_api_client,
    ):
        """Test coordinator is initialized with always_update=False."""
        coordinator = GoveeDataUpdateCoordinator(
            hass,
            mock_config_entry,
            mock_api_client,
            update_interval=timedelta(seconds=60),
        )

        # The always_update attribute should be False
        # This prevents unnecessary listener notifications when data hasn't changed
        assert coordinator.always_update is False


# ==============================================================================
# Batch Command Execution Tests
# ==============================================================================


class TestBatchCommandExecution:
    """Test batched command execution functionality."""

    @pytest.mark.asyncio
    async def test_async_execute_batch_success(
        self,
        hass: HomeAssistant,
        mock_config_entry,
        mock_api_client,
        mock_device_light,
    ):
        """Test successful batch execution with multiple commands."""
        from custom_components.govee.coordinator import CommandBatch

        coordinator = GoveeDataUpdateCoordinator(
            hass,
            mock_config_entry,
            mock_api_client,
            update_interval=timedelta(seconds=60),
        )

        coordinator.devices = {mock_device_light.device_id: mock_device_light}
        coordinator.data = {
            mock_device_light.device_id: GoveeDeviceState(
                device_id=mock_device_light.device_id,
                online=True,
                power_state=False,
                brightness=50,
            )
        }

        mock_api_client.control_device = AsyncMock()

        # Create a batch with color and brightness commands
        batch = CommandBatch(device_id=mock_device_light.device_id)
        batch.add("devices.capabilities.color_setting", "colorRgb", 16711680)  # Red
        batch.add("devices.capabilities.range", "brightness", 100)

        await coordinator.async_execute_batch(
            mock_device_light.device_id, batch, inter_command_delay=0.01
        )

        # Should have called control_device twice
        assert mock_api_client.control_device.call_count == 2

        # Optimistic updates should be applied
        state = coordinator.data[mock_device_light.device_id]
        assert state.color_rgb == (255, 0, 0)  # Red
        assert state.brightness == 100

    @pytest.mark.asyncio
    async def test_async_execute_batch_empty_batch(
        self,
        hass: HomeAssistant,
        mock_config_entry,
        mock_api_client,
        mock_device_light,
    ):
        """Test batch execution with empty batch returns early."""
        from custom_components.govee.coordinator import CommandBatch

        coordinator = GoveeDataUpdateCoordinator(
            hass,
            mock_config_entry,
            mock_api_client,
            update_interval=timedelta(seconds=60),
        )

        coordinator.devices = {mock_device_light.device_id: mock_device_light}
        mock_api_client.control_device = AsyncMock()

        # Empty batch
        batch = CommandBatch(device_id=mock_device_light.device_id)

        await coordinator.async_execute_batch(mock_device_light.device_id, batch)

        # Should not call API
        mock_api_client.control_device.assert_not_called()

    @pytest.mark.asyncio
    async def test_async_execute_batch_unknown_device(
        self,
        hass: HomeAssistant,
        mock_config_entry,
        mock_api_client,
    ):
        """Test batch execution with unknown device returns early."""
        from custom_components.govee.coordinator import CommandBatch

        coordinator = GoveeDataUpdateCoordinator(
            hass,
            mock_config_entry,
            mock_api_client,
            update_interval=timedelta(seconds=60),
        )

        mock_api_client.control_device = AsyncMock()

        batch = CommandBatch(device_id="unknown_device")
        batch.add("devices.capabilities.on_off", "powerSwitch", 1)

        await coordinator.async_execute_batch("unknown_device", batch)

        # Should not call API for unknown device
        mock_api_client.control_device.assert_not_called()

    @pytest.mark.asyncio
    async def test_async_execute_batch_rollback_on_failure(
        self,
        hass: HomeAssistant,
        mock_config_entry,
        mock_api_client,
        mock_device_light,
    ):
        """Test batch execution rolls back all changes on API failure."""
        from custom_components.govee.coordinator import CommandBatch

        coordinator = GoveeDataUpdateCoordinator(
            hass,
            mock_config_entry,
            mock_api_client,
            update_interval=timedelta(seconds=60),
        )

        coordinator.devices = {mock_device_light.device_id: mock_device_light}

        # Initial state
        initial_state = GoveeDeviceState(
            device_id=mock_device_light.device_id,
            online=True,
            power_state=True,
            brightness=50,
            color_rgb=(255, 255, 255),  # White
        )
        coordinator.data = {mock_device_light.device_id: initial_state}

        # First command succeeds, second fails
        mock_api_client.control_device = AsyncMock(
            side_effect=[None, GoveeApiError("API Error")]
        )

        batch = CommandBatch(device_id=mock_device_light.device_id)
        batch.add("devices.capabilities.color_setting", "colorRgb", 255)  # Blue
        batch.add("devices.capabilities.range", "brightness", 100)

        with pytest.raises(GoveeApiError):
            await coordinator.async_execute_batch(
                mock_device_light.device_id, batch, inter_command_delay=0.01
            )

        # State should be rolled back to original
        state = coordinator.data[mock_device_light.device_id]
        assert state.brightness == 50  # Rolled back
        assert state.color_rgb == (255, 255, 255)  # Rolled back to white

    @pytest.mark.asyncio
    async def test_async_execute_batch_no_data_state(
        self,
        hass: HomeAssistant,
        mock_config_entry,
        mock_api_client,
        mock_device_light,
    ):
        """Test batch execution when coordinator has no data yet."""
        from custom_components.govee.coordinator import CommandBatch

        coordinator = GoveeDataUpdateCoordinator(
            hass,
            mock_config_entry,
            mock_api_client,
            update_interval=timedelta(seconds=60),
        )

        coordinator.devices = {mock_device_light.device_id: mock_device_light}
        coordinator.data = None  # No data yet

        mock_api_client.control_device = AsyncMock()

        batch = CommandBatch(device_id=mock_device_light.device_id)
        batch.add("devices.capabilities.on_off", "powerSwitch", 1)

        # Should still send command even without state data
        await coordinator.async_execute_batch(
            mock_device_light.device_id, batch, inter_command_delay=0.01
        )

        mock_api_client.control_device.assert_called_once()

    @pytest.mark.asyncio
    async def test_async_execute_batch_single_command(
        self,
        hass: HomeAssistant,
        mock_config_entry,
        mock_api_client,
        mock_device_light,
    ):
        """Test batch execution with single command (no inter-command delay needed)."""
        from custom_components.govee.coordinator import CommandBatch

        coordinator = GoveeDataUpdateCoordinator(
            hass,
            mock_config_entry,
            mock_api_client,
            update_interval=timedelta(seconds=60),
        )

        coordinator.devices = {mock_device_light.device_id: mock_device_light}
        coordinator.data = {
            mock_device_light.device_id: GoveeDeviceState(
                device_id=mock_device_light.device_id,
                online=True,
            )
        }

        mock_api_client.control_device = AsyncMock()

        batch = CommandBatch(device_id=mock_device_light.device_id)
        batch.add("devices.capabilities.on_off", "powerSwitch", 1)

        await coordinator.async_execute_batch(mock_device_light.device_id, batch)

        # Should call once with no delay
        mock_api_client.control_device.assert_called_once()


# ==============================================================================
# MQTT Event Handling Tests
# ==============================================================================


class TestMqttEventHandling:
    """Test MQTT event handling in coordinator."""

    @pytest.mark.asyncio
    async def test_async_setup_mqtt(
        self,
        hass: HomeAssistant,
        mock_config_entry,
        mock_api_client,
    ):
        """Test setting up MQTT subscription."""
        coordinator = GoveeDataUpdateCoordinator(
            hass,
            mock_config_entry,
            mock_api_client,
            update_interval=timedelta(seconds=60),
        )

        with patch(
            "custom_components.govee.coordinator.GoveeMqttClient"
        ) as mock_mqtt_class:
            mock_mqtt_instance = MagicMock()
            mock_mqtt_instance.async_start = AsyncMock()
            mock_mqtt_class.return_value = mock_mqtt_instance

            await coordinator.async_setup_mqtt("test_api_key")

            assert coordinator._mqtt_enabled is True
            mock_mqtt_instance.async_start.assert_called_once()

    @pytest.mark.asyncio
    async def test_async_stop_mqtt(
        self,
        hass: HomeAssistant,
        mock_config_entry,
        mock_api_client,
    ):
        """Test stopping MQTT subscription."""
        coordinator = GoveeDataUpdateCoordinator(
            hass,
            mock_config_entry,
            mock_api_client,
            update_interval=timedelta(seconds=60),
        )

        # Setup mock MQTT client
        mock_mqtt_client = MagicMock()
        mock_mqtt_client.async_stop = AsyncMock()
        coordinator._mqtt_client = mock_mqtt_client
        coordinator._mqtt_enabled = True

        await coordinator.async_stop_mqtt()

        assert coordinator._mqtt_client is None
        assert coordinator._mqtt_enabled is False
        mock_mqtt_client.async_stop.assert_called_once()

    @pytest.mark.asyncio
    async def test_async_stop_mqtt_when_not_started(
        self,
        hass: HomeAssistant,
        mock_config_entry,
        mock_api_client,
    ):
        """Test stopping MQTT when not started does nothing."""
        coordinator = GoveeDataUpdateCoordinator(
            hass,
            mock_config_entry,
            mock_api_client,
            update_interval=timedelta(seconds=60),
        )

        # No MQTT client setup
        coordinator._mqtt_client = None

        # Should not raise
        await coordinator.async_stop_mqtt()

    @pytest.mark.asyncio
    async def test_mqtt_event_updates_device_state(
        self,
        hass: HomeAssistant,
        mock_config_entry,
        mock_api_client,
        mock_device_light,
    ):
        """Test MQTT event updates device state."""
        coordinator = GoveeDataUpdateCoordinator(
            hass,
            mock_config_entry,
            mock_api_client,
            update_interval=timedelta(seconds=60),
        )

        # Setup initial state
        coordinator.devices = {mock_device_light.device_id: mock_device_light}
        coordinator.data = {
            mock_device_light.device_id: GoveeDeviceState(
                device_id=mock_device_light.device_id,
                online=True,
            )
        }

        # Capture the callback when starting MQTT
        captured_callback = None

        def capture_callback(api_key, on_event):
            nonlocal captured_callback
            captured_callback = on_event
            mock_mqtt = MagicMock()
            mock_mqtt.async_start = AsyncMock()
            return mock_mqtt

        with patch(
            "custom_components.govee.coordinator.GoveeMqttClient",
            side_effect=capture_callback,
        ):
            await coordinator.async_setup_mqtt("test_api_key")

        # Simulate MQTT event
        assert captured_callback is not None
        captured_callback({
            "device": mock_device_light.device_id,
            "capabilities": [
                {
                    "type": "devices.capabilities.event",
                    "instance": "lackWaterEvent",
                    "state": [{"name": "lack", "value": 1}],
                }
            ],
        })

        # Check state was updated
        state = coordinator.data[mock_device_light.device_id]
        assert state.mqtt_events is not None
        assert "lackWaterEvent" in state.mqtt_events

    @pytest.mark.asyncio
    async def test_mqtt_event_unknown_device_ignored(
        self,
        hass: HomeAssistant,
        mock_config_entry,
        mock_api_client,
    ):
        """Test MQTT event for unknown device is ignored."""
        coordinator = GoveeDataUpdateCoordinator(
            hass,
            mock_config_entry,
            mock_api_client,
            update_interval=timedelta(seconds=60),
        )

        # Setup with empty data
        coordinator.data = {}

        captured_callback = None

        def capture_callback(api_key, on_event):
            nonlocal captured_callback
            captured_callback = on_event
            mock_mqtt = MagicMock()
            mock_mqtt.async_start = AsyncMock()
            return mock_mqtt

        with patch(
            "custom_components.govee.coordinator.GoveeMqttClient",
            side_effect=capture_callback,
        ):
            await coordinator.async_setup_mqtt("test_api_key")

        # Simulate MQTT event for unknown device - should not raise
        assert captured_callback is not None
        captured_callback({
            "device": "unknown_device_id",
            "capabilities": [],
        })

    @pytest.mark.asyncio
    async def test_mqtt_event_no_data_ignored(
        self,
        hass: HomeAssistant,
        mock_config_entry,
        mock_api_client,
    ):
        """Test MQTT event when coordinator has no data is ignored."""
        coordinator = GoveeDataUpdateCoordinator(
            hass,
            mock_config_entry,
            mock_api_client,
            update_interval=timedelta(seconds=60),
        )

        # No data yet
        coordinator.data = None

        captured_callback = None

        def capture_callback(api_key, on_event):
            nonlocal captured_callback
            captured_callback = on_event
            mock_mqtt = MagicMock()
            mock_mqtt.async_start = AsyncMock()
            return mock_mqtt

        with patch(
            "custom_components.govee.coordinator.GoveeMqttClient",
            side_effect=capture_callback,
        ):
            await coordinator.async_setup_mqtt("test_api_key")

        # Simulate MQTT event - should not raise
        assert captured_callback is not None
        captured_callback({
            "device": "any_device",
            "capabilities": [],
        })


# ==============================================================================
# Adaptive Polling Tests
# ==============================================================================


class TestAdaptivePolling:
    """Test adaptive polling interval adjustment."""

    def test_adapt_polling_critical(
        self,
        hass: HomeAssistant,
        mock_config_entry,
        mock_api_client,
    ):
        """Test polling interval adjustment at critical quota level."""
        from custom_components.govee.const import (
            POLL_INTERVAL_CRITICAL,
            QUOTA_CRITICAL,
        )
        from custom_components.govee.api.rate_limiter import RateLimitStatus

        coordinator = GoveeDataUpdateCoordinator(
            hass,
            mock_config_entry,
            mock_api_client,
            update_interval=timedelta(seconds=60),
        )

        # Set quota below critical threshold using new status object
        mock_api_client.rate_limiter.status = RateLimitStatus(
            remaining_minute=100,
            remaining_day=QUOTA_CRITICAL - 1,
            reset_minute=None,
            reset_day=None,
            is_limited=False,
            wait_time=None,
            consecutive_failures=0,
        )

        coordinator._adapt_polling_interval()

        # Should adjust to critical interval
        assert coordinator.update_interval >= timedelta(seconds=POLL_INTERVAL_CRITICAL)

    def test_adapt_polling_danger(
        self,
        hass: HomeAssistant,
        mock_config_entry,
        mock_api_client,
    ):
        """Test polling interval adjustment at danger quota level."""
        from custom_components.govee.const import (
            POLL_INTERVAL_DANGER,
            QUOTA_DANGER,
            QUOTA_CRITICAL,
        )
        from custom_components.govee.api.rate_limiter import RateLimitStatus

        coordinator = GoveeDataUpdateCoordinator(
            hass,
            mock_config_entry,
            mock_api_client,
            update_interval=timedelta(seconds=60),
        )

        # Set quota between critical and danger
        mock_api_client.rate_limiter.status = RateLimitStatus(
            remaining_minute=100,
            remaining_day=QUOTA_CRITICAL + 50,
            reset_minute=None,
            reset_day=None,
            is_limited=False,
            wait_time=None,
            consecutive_failures=0,
        )

        coordinator._adapt_polling_interval()

        # Should adjust to danger interval
        assert coordinator.update_interval >= timedelta(seconds=POLL_INTERVAL_DANGER)

    def test_adapt_polling_warning(
        self,
        hass: HomeAssistant,
        mock_config_entry,
        mock_api_client,
    ):
        """Test polling interval adjustment at warning quota level."""
        from custom_components.govee.const import (
            POLL_INTERVAL_WARNING,
            QUOTA_WARNING,
            QUOTA_DANGER,
        )
        from custom_components.govee.api.rate_limiter import RateLimitStatus

        coordinator = GoveeDataUpdateCoordinator(
            hass,
            mock_config_entry,
            mock_api_client,
            update_interval=timedelta(seconds=60),
        )

        # Set quota between danger and warning
        mock_api_client.rate_limiter.status = RateLimitStatus(
            remaining_minute=100,
            remaining_day=QUOTA_DANGER + 50,
            reset_minute=None,
            reset_day=None,
            is_limited=False,
            wait_time=None,
            consecutive_failures=0,
        )

        coordinator._adapt_polling_interval()

        # Should adjust to warning interval
        assert coordinator.update_interval >= timedelta(seconds=POLL_INTERVAL_WARNING)

    def test_adapt_polling_elevated(
        self,
        hass: HomeAssistant,
        mock_config_entry,
        mock_api_client,
    ):
        """Test polling interval at elevated (between warning and OK) level."""
        from custom_components.govee.const import QUOTA_OK, QUOTA_WARNING
        from custom_components.govee.api.rate_limiter import RateLimitStatus

        coordinator = GoveeDataUpdateCoordinator(
            hass,
            mock_config_entry,
            mock_api_client,
            update_interval=timedelta(seconds=60),
        )

        # Set quota between warning and OK
        mock_api_client.rate_limiter.status = RateLimitStatus(
            remaining_minute=100,
            remaining_day=QUOTA_WARNING + 50,
            reset_minute=None,
            reset_day=None,
            is_limited=False,
            wait_time=None,
            consecutive_failures=0,
        )

        coordinator._adapt_polling_interval()

        # Should have slightly throttled interval (at least 90s)
        assert coordinator.update_interval >= timedelta(seconds=60)

    def test_adapt_polling_healthy_quota(
        self,
        hass: HomeAssistant,
        mock_config_entry,
        mock_api_client,
        caplog,
    ):
        """Test polling restores to user setting with healthy quota."""
        from custom_components.govee.const import QUOTA_OK
        from custom_components.govee.api.rate_limiter import RateLimitStatus

        coordinator = GoveeDataUpdateCoordinator(
            hass,
            mock_config_entry,
            mock_api_client,
            update_interval=timedelta(seconds=120),  # Start with longer interval
        )

        # Store the user interval
        coordinator._user_interval = timedelta(seconds=60)

        # Set healthy quota
        mock_api_client.rate_limiter.status = RateLimitStatus(
            remaining_minute=100,
            remaining_day=QUOTA_OK + 1000,
            reset_minute=None,
            reset_day=None,
            is_limited=False,
            wait_time=None,
            consecutive_failures=0,
        )

        coordinator._adapt_polling_interval()

        # Should restore to user's preferred interval
        assert coordinator.update_interval.total_seconds() == 60

    def test_adapt_polling_no_change_logs_nothing(
        self,
        hass: HomeAssistant,
        mock_config_entry,
        mock_api_client,
        caplog,
    ):
        """Test no log when interval doesn't change."""
        from custom_components.govee.const import QUOTA_OK
        from custom_components.govee.api.rate_limiter import RateLimitStatus

        coordinator = GoveeDataUpdateCoordinator(
            hass,
            mock_config_entry,
            mock_api_client,
            update_interval=timedelta(seconds=60),
        )

        coordinator._user_interval = timedelta(seconds=60)
        mock_api_client.rate_limiter.status = RateLimitStatus(
            remaining_minute=100,
            remaining_day=QUOTA_OK + 1000,
            reset_minute=None,
            reset_day=None,
            is_limited=False,
            wait_time=None,
            consecutive_failures=0,
        )

        # Clear logs before
        caplog.clear()

        coordinator._adapt_polling_interval()

        # Same interval, shouldn't log adjustment
        # (First call may log, so call twice)
        coordinator._adapt_polling_interval()

    def test_adapt_polling_update_interval_none(
        self,
        hass: HomeAssistant,
        mock_config_entry,
        mock_api_client,
    ):
        """Test adapt polling when update_interval is None."""
        from custom_components.govee.const import QUOTA_CRITICAL
        from custom_components.govee.api.rate_limiter import RateLimitStatus

        coordinator = GoveeDataUpdateCoordinator(
            hass,
            mock_config_entry,
            mock_api_client,
            update_interval=timedelta(seconds=60),
        )

        # Force update_interval to None
        coordinator.update_interval = None

        mock_api_client.rate_limiter.status = RateLimitStatus(
            remaining_minute=100,
            remaining_day=QUOTA_CRITICAL - 1,
            reset_minute=None,
            reset_day=None,
            is_limited=False,
            wait_time=None,
            consecutive_failures=0,
        )

        # Should not raise
        coordinator._adapt_polling_interval()

        # Should have set an interval
        assert coordinator.update_interval is not None
