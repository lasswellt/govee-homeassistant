"""Test Govee integration setup and lifecycle."""
from __future__ import annotations

from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock, patch
import pytest
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.const import CONF_API_KEY

from custom_components.govee import (
    GoveeRuntimeData,
    async_setup_entry,
    async_unload_entry,
    async_options_updated,
    async_migrate_entry,
)
from custom_components.govee.const import (
    CONFIG_ENTRY_VERSION,
    DEFAULT_POLL_INTERVAL,
    DOMAIN,
    PLATFORMS,
)


class TestGoveeRuntimeData:
    """Test GoveeRuntimeData dataclass."""

    def test_runtime_data_creation(
        self,
        mock_api_client,
        mock_coordinator,
        mock_device_light,
    ):
        """Test creating runtime data."""
        runtime_data = GoveeRuntimeData(
            client=mock_api_client,
            coordinator=mock_coordinator,
            devices={mock_device_light.device_id: mock_device_light},
        )

        assert runtime_data.client == mock_api_client
        assert runtime_data.coordinator == mock_coordinator
        assert len(runtime_data.devices) == 1


class TestAsyncSetupEntry:
    """Test async_setup_entry function."""

    @pytest.mark.asyncio
    async def test_setup_entry_success(
        self,
        hass: HomeAssistant,
        mock_config_entry,
        mock_api_client_with_devices,
        mock_device_light,
        mock_device_switch,
    ):
        """Test successful setup."""
        with patch(
            "custom_components.govee.GoveeApiClient",
            return_value=mock_api_client_with_devices,
        ), patch(
            "custom_components.govee.GoveeDataUpdateCoordinator",
        ) as mock_coord_class, patch.object(
            hass.config_entries, "async_forward_entry_setups"
        ) as mock_forward:
            # Setup mock coordinator
            mock_coordinator = MagicMock()
            mock_coordinator.async_config_entry_first_refresh = AsyncMock()
            mock_coordinator.devices = {
                mock_device_light.device_id: mock_device_light,
                mock_device_switch.device_id: mock_device_switch,
            }
            mock_coord_class.return_value = mock_coordinator

            result = await async_setup_entry(hass, mock_config_entry)

            assert result is True
            # Should forward setup to platforms
            mock_forward.assert_called_once_with(mock_config_entry, PLATFORMS)
            # Should have runtime data
            assert hasattr(mock_config_entry, "runtime_data")
            assert mock_config_entry.runtime_data.client == mock_api_client_with_devices
            assert len(mock_config_entry.runtime_data.devices) == 2

    @pytest.mark.asyncio
    async def test_setup_entry_with_auth_error(
        self,
        hass: HomeAssistant,
        mock_config_entry,
        mock_api_client,
        mock_auth_error,
    ):
        """Test setup with authentication error."""
        with patch(
            "custom_components.govee.GoveeApiClient",
            return_value=mock_api_client,
        ), patch(
            "custom_components.govee.GoveeDataUpdateCoordinator",
        ) as mock_coord_class:
            # Setup mock coordinator that raises auth error
            mock_coordinator = MagicMock()
            mock_coordinator.async_config_entry_first_refresh = AsyncMock(
                side_effect=mock_auth_error
            )
            mock_coord_class.return_value = mock_coordinator

            # Should raise ConfigEntryNotReady
            with pytest.raises(ConfigEntryNotReady, match="Invalid API key"):
                await async_setup_entry(hass, mock_config_entry)

            # Should close client
            mock_api_client.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_setup_entry_with_api_error(
        self,
        hass: HomeAssistant,
        mock_config_entry,
        mock_api_client,
    ):
        """Test setup with API error."""
        from custom_components.govee.api.exceptions import GoveeApiError

        with patch(
            "custom_components.govee.GoveeApiClient",
            return_value=mock_api_client,
        ), patch(
            "custom_components.govee.GoveeDataUpdateCoordinator",
        ) as mock_coord_class:
            # Setup mock coordinator that raises API error
            mock_coordinator = MagicMock()
            mock_coordinator.async_config_entry_first_refresh = AsyncMock(
                side_effect=GoveeApiError("API connection failed")
            )
            mock_coord_class.return_value = mock_coordinator

            # Should raise ConfigEntryNotReady
            with pytest.raises(ConfigEntryNotReady, match="Failed to connect"):
                await async_setup_entry(hass, mock_config_entry)

            # Should close client
            mock_api_client.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_setup_entry_uses_api_key_from_options(
        self,
        hass: HomeAssistant,
        mock_config_entry_with_options,
        mock_api_client,
    ):
        """Test setup uses API key from options if available."""
        mock_config_entry_with_options.data = {CONF_API_KEY: "old_key"}
        mock_config_entry_with_options.options = {CONF_API_KEY: "new_key"}

        with patch(
            "custom_components.govee.GoveeApiClient",
        ) as mock_client_class, patch(
            "custom_components.govee.GoveeDataUpdateCoordinator",
        ) as mock_coord_class, patch.object(
            hass.config_entries, "async_forward_entry_setups"
        ):
            mock_client = MagicMock()
            mock_client.close = AsyncMock()
            mock_client_class.return_value = mock_client

            mock_coordinator = MagicMock()
            mock_coordinator.async_config_entry_first_refresh = AsyncMock()
            mock_coordinator.devices = {}
            mock_coord_class.return_value = mock_coordinator

            await async_setup_entry(hass, mock_config_entry_with_options)

            # Should use API key from options
            mock_client_class.assert_called_once()
            call_args = mock_client_class.call_args
            assert call_args[0][0] == "new_key"  # First positional arg is API key

    @pytest.mark.asyncio
    async def test_setup_entry_uses_custom_poll_interval(
        self,
        hass: HomeAssistant,
        mock_config_entry_with_options,
        mock_api_client,
    ):
        """Test setup uses custom poll interval from options."""
        mock_config_entry_with_options.options = {"delay": 30}

        with patch(
            "custom_components.govee.GoveeApiClient",
            return_value=mock_api_client,
        ), patch(
            "custom_components.govee.GoveeDataUpdateCoordinator",
        ) as mock_coord_class, patch.object(
            hass.config_entries, "async_forward_entry_setups"
        ):
            mock_coordinator = MagicMock()
            mock_coordinator.async_config_entry_first_refresh = AsyncMock()
            mock_coordinator.devices = {}
            mock_coord_class.return_value = mock_coordinator

            await async_setup_entry(hass, mock_config_entry_with_options)

            # Should create coordinator with custom interval
            mock_coord_class.assert_called_once()
            call_kwargs = mock_coord_class.call_args.kwargs
            assert call_kwargs["update_interval"] == timedelta(seconds=30)

    @pytest.mark.asyncio
    async def test_setup_entry_registers_update_listener(
        self,
        hass: HomeAssistant,
        mock_config_entry,
        mock_api_client_with_devices,
    ):
        """Test setup registers options update listener."""
        with patch(
            "custom_components.govee.GoveeApiClient",
            return_value=mock_api_client_with_devices,
        ), patch(
            "custom_components.govee.GoveeDataUpdateCoordinator",
        ) as mock_coord_class, patch.object(
            hass.config_entries, "async_forward_entry_setups"
        ):
            mock_coordinator = MagicMock()
            mock_coordinator.async_config_entry_first_refresh = AsyncMock()
            mock_coordinator.devices = {}
            mock_coord_class.return_value = mock_coordinator

            # Mock update listener registration
            mock_config_entry.add_update_listener = MagicMock(return_value=MagicMock())
            mock_config_entry.async_on_unload = MagicMock()

            await async_setup_entry(hass, mock_config_entry)

            # Should register update listener
            mock_config_entry.add_update_listener.assert_called_once()
            mock_config_entry.async_on_unload.assert_called_once()


class TestAsyncUnloadEntry:
    """Test async_unload_entry function."""

    @pytest.mark.asyncio
    async def test_unload_entry_success(
        self,
        hass: HomeAssistant,
        mock_config_entry,
        mock_api_client,
    ):
        """Test successful unload."""
        # Setup runtime data
        mock_coordinator = MagicMock()
        mock_config_entry.runtime_data = GoveeRuntimeData(
            client=mock_api_client,
            coordinator=mock_coordinator,
            devices={},
        )

        with patch.object(
            hass.config_entries,
            "async_unload_platforms",
            return_value=True,
        ) as mock_unload:
            result = await async_unload_entry(hass, mock_config_entry)

            assert result is True
            # Should unload platforms
            mock_unload.assert_called_once_with(mock_config_entry, PLATFORMS)
            # Should close client
            mock_api_client.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_unload_entry_platform_unload_fails(
        self,
        hass: HomeAssistant,
        mock_config_entry,
        mock_api_client,
    ):
        """Test unload when platform unload fails."""
        # Setup runtime data
        mock_coordinator = MagicMock()
        mock_config_entry.runtime_data = GoveeRuntimeData(
            client=mock_api_client,
            coordinator=mock_coordinator,
            devices={},
        )

        with patch.object(
            hass.config_entries,
            "async_unload_platforms",
            return_value=False,
        ):
            result = await async_unload_entry(hass, mock_config_entry)

            assert result is False
            # Should NOT close client if unload failed
            mock_api_client.close.assert_not_called()


class TestAsyncOptionsUpdated:
    """Test async_options_updated function."""

    @pytest.mark.asyncio
    async def test_options_updated_reloads_entry(
        self,
        hass: HomeAssistant,
        mock_config_entry,
    ):
        """Test options update triggers reload."""
        with patch.object(
            hass.config_entries,
            "async_reload",
        ) as mock_reload:
            await async_options_updated(hass, mock_config_entry)

            # Should reload entry
            mock_reload.assert_called_once_with(mock_config_entry.entry_id)


class TestAsyncMigrateEntry:
    """Test async_migrate_entry function."""

    @pytest.mark.asyncio
    async def test_migrate_entry_from_v1_to_v2(
        self,
        hass: HomeAssistant,
    ):
        """Test migration from version 1 to current version."""
        from pytest_homeassistant_custom_component.common import MockConfigEntry

        # Create old version entry
        old_entry = MockConfigEntry(
            domain=DOMAIN,
            version=1,
            data={CONF_API_KEY: "test_key"},
            options={"delay": 60},
        )

        result = await async_migrate_entry(hass, old_entry)

        assert result is True
        # Should update version
        assert old_entry.version == CONFIG_ENTRY_VERSION
        # Should preserve data
        assert old_entry.data[CONF_API_KEY] == "test_key"
        assert old_entry.options["delay"] == 60

    @pytest.mark.asyncio
    async def test_migrate_entry_already_current_version(
        self,
        hass: HomeAssistant,
        mock_config_entry,
    ):
        """Test migration when already at current version."""
        original_version = mock_config_entry.version
        original_data = dict(mock_config_entry.data)

        result = await async_migrate_entry(hass, mock_config_entry)

        assert result is True
        # Should not modify version or data
        assert mock_config_entry.version == original_version
        assert mock_config_entry.data == original_data

    @pytest.mark.asyncio
    async def test_migrate_entry_preserves_all_data(
        self,
        hass: HomeAssistant,
    ):
        """Test migration preserves all data fields."""
        from pytest_homeassistant_custom_component.common import MockConfigEntry

        old_entry = MockConfigEntry(
            domain=DOMAIN,
            version=1,
            data={
                CONF_API_KEY: "test_key",
                "custom_field": "custom_value",
            },
            options={
                "delay": 30,
                "use_assumed_state": True,
                "offline_is_off": False,
            },
        )

        await async_migrate_entry(hass, old_entry)

        # Should preserve all custom fields
        assert old_entry.data["custom_field"] == "custom_value"
        assert old_entry.options["use_assumed_state"] is True
        assert old_entry.options["offline_is_off"] is False


class TestIntegrationLifecycle:
    """Test complete integration lifecycle."""

    @pytest.mark.asyncio
    async def test_full_lifecycle_setup_and_unload(
        self,
        hass: HomeAssistant,
        mock_config_entry,
        mock_api_client_with_devices,
        mock_device_light,
    ):
        """Test complete lifecycle: setup -> use -> unload."""
        # Setup
        with patch(
            "custom_components.govee.GoveeApiClient",
            return_value=mock_api_client_with_devices,
        ), patch(
            "custom_components.govee.GoveeDataUpdateCoordinator",
        ) as mock_coord_class, patch.object(
            hass.config_entries, "async_forward_entry_setups"
        ), patch.object(
            hass.config_entries, "async_unload_platforms", return_value=True
        ):
            mock_coordinator = MagicMock()
            mock_coordinator.async_config_entry_first_refresh = AsyncMock()
            mock_coordinator.devices = {mock_device_light.device_id: mock_device_light}
            mock_coord_class.return_value = mock_coordinator

            # Setup entry
            setup_result = await async_setup_entry(hass, mock_config_entry)
            assert setup_result is True
            assert hasattr(mock_config_entry, "runtime_data")

            # Unload entry
            unload_result = await async_unload_entry(hass, mock_config_entry)
            assert unload_result is True
            mock_api_client_with_devices.close.assert_called_once()
