"""Test the Govee config flow."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from homeassistant import config_entries, setup
from homeassistant.core import HomeAssistant
from homeassistant.const import CONF_API_KEY, CONF_DELAY
from homeassistant.data_entry_flow import FlowResultType

from custom_components.govee.const import (
    DOMAIN,
    CONF_USE_ASSUMED_STATE,
    CONF_OFFLINE_IS_OFF,
    CONF_ENABLE_GROUP_DEVICES,
    CONF_DISABLE_ATTRIBUTE_UPDATES,
    DEFAULT_POLL_INTERVAL,
)
from custom_components.govee.config_flow import (
    validate_api_key,
    CannotConnect,
    GoveeFlowHandler,
    GoveeOptionsFlowHandler,
)


# ==============================================================================
# Helper Function Tests
# ==============================================================================


class TestValidateApiKey:
    """Test validate_api_key helper function."""

    @pytest.mark.asyncio
    async def test_validate_api_key_success(self, hass: HomeAssistant):
        """Test API key validation succeeds with valid key."""
        user_input = {
            CONF_API_KEY: "valid_api_key",
            CONF_DELAY: 60,
        }

        with patch(
            "custom_components.govee.config_flow.GoveeApiClient"
        ) as mock_client_class:
            mock_client = MagicMock()
            mock_client.test_connection = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock()
            mock_client_class.return_value = mock_client

            result = await validate_api_key(hass, user_input)

            assert result == user_input
            mock_client.test_connection.assert_called_once()

    @pytest.mark.asyncio
    async def test_validate_api_key_auth_error(self, hass: HomeAssistant):
        """Test API key validation fails with auth error."""
        from custom_components.govee.api.exceptions import GoveeAuthError

        user_input = {
            CONF_API_KEY: "invalid_api_key",
        }

        with patch(
            "custom_components.govee.config_flow.GoveeApiClient"
        ) as mock_client_class:
            mock_client = MagicMock()
            mock_client.test_connection = AsyncMock(
                side_effect=GoveeAuthError("Invalid API key")
            )
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock()
            mock_client_class.return_value = mock_client

            with pytest.raises(CannotConnect, match="Invalid API key"):
                await validate_api_key(hass, user_input)

    @pytest.mark.asyncio
    async def test_validate_api_key_connection_error(self, hass: HomeAssistant):
        """Test API key validation fails with connection error."""
        from custom_components.govee.api.exceptions import GoveeApiError

        user_input = {
            CONF_API_KEY: "valid_api_key",
        }

        with patch(
            "custom_components.govee.config_flow.GoveeApiClient"
        ) as mock_client_class:
            mock_client = MagicMock()
            mock_client.test_connection = AsyncMock(
                side_effect=GoveeApiError("Network error")
            )
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock()
            mock_client_class.return_value = mock_client

            with pytest.raises(CannotConnect, match="Network error"):
                await validate_api_key(hass, user_input)


# ==============================================================================
# Config Flow Tests (User Step)
# ==============================================================================


class TestGoveeFlowHandler:
    """Test GoveeFlowHandler config flow."""

    @pytest.mark.asyncio
    async def test_form_shows_initially(self, hass: HomeAssistant):
        """Test we get the form on first load."""
        await setup.async_setup_component(hass, "persistent_notification", {})

        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )

        assert result["type"] == FlowResultType.FORM
        assert result["step_id"] == "user"
        assert result["errors"] == {}

    @pytest.mark.asyncio
    async def test_form_creates_entry_with_valid_input(self, hass: HomeAssistant):
        """Test we create entry with valid input."""
        with patch(
            "custom_components.govee.config_flow.validate_api_key",
            return_value={CONF_API_KEY: "test_key", CONF_DELAY: 60},
        ):
            result = await hass.config_entries.flow.async_init(
                DOMAIN, context={"source": config_entries.SOURCE_USER}
            )

            result2 = await hass.config_entries.flow.async_configure(
                result["flow_id"],
                {CONF_API_KEY: "test_key", CONF_DELAY: 60},
            )

            assert result2["type"] == FlowResultType.CREATE_ENTRY
            assert result2["title"] == DOMAIN
            assert result2["data"] == {
                CONF_API_KEY: "test_key",
                CONF_DELAY: 60,
            }

    @pytest.mark.asyncio
    async def test_form_shows_error_on_cannot_connect(self, hass: HomeAssistant):
        """Test we handle cannot connect error."""
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )

        with patch(
            "custom_components.govee.config_flow.validate_api_key",
            side_effect=CannotConnect("Cannot connect"),
        ):
            result2 = await hass.config_entries.flow.async_configure(
                result["flow_id"],
                {CONF_API_KEY: "test_key", CONF_DELAY: 60},
            )

            assert result2["type"] == FlowResultType.FORM
            assert result2["errors"] == {CONF_API_KEY: "cannot_connect"}

    @pytest.mark.asyncio
    async def test_form_shows_error_on_unknown_exception(self, hass: HomeAssistant):
        """Test we handle unknown exception."""
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )

        with patch(
            "custom_components.govee.config_flow.validate_api_key",
            side_effect=Exception("Unexpected error"),
        ):
            result2 = await hass.config_entries.flow.async_configure(
                result["flow_id"],
                {CONF_API_KEY: "test_key", CONF_DELAY: 60},
            )

            assert result2["type"] == FlowResultType.FORM
            assert result2["errors"] == {"base": "unknown"}

    @pytest.mark.asyncio
    async def test_form_uses_default_poll_interval(self, hass: HomeAssistant):
        """Test form shows default poll interval."""
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )

        # Check default value is set in schema
        assert result["data_schema"].schema[CONF_DELAY].default() == DEFAULT_POLL_INTERVAL

    @pytest.mark.asyncio
    async def test_options_flow_is_available(self, hass: HomeAssistant, mock_config_entry):
        """Test options flow is available."""
        mock_config_entry.add_to_hass(hass)

        options_flow = GoveeFlowHandler.async_get_options_flow(mock_config_entry)

        assert isinstance(options_flow, GoveeOptionsFlowHandler)


# ==============================================================================
# Options Flow Tests
# ==============================================================================


class TestGoveeOptionsFlowHandler:
    """Test GoveeOptionsFlowHandler options flow."""

    @pytest.mark.asyncio
    async def test_options_flow_init(self, hass: HomeAssistant, mock_config_entry):
        """Test options flow initialization."""
        mock_config_entry.add_to_hass(hass)

        options_flow = GoveeOptionsFlowHandler(mock_config_entry)

        assert options_flow.options == dict(mock_config_entry.options)

    @pytest.mark.asyncio
    async def test_options_flow_shows_form(self, hass: HomeAssistant, mock_config_entry):
        """Test options flow shows form with current values."""
        mock_config_entry.add_to_hass(hass)

        options_flow = GoveeOptionsFlowHandler(mock_config_entry)

        result = await options_flow.async_step_init()

        assert result["type"] == FlowResultType.FORM
        assert result["step_id"] == "user"

    @pytest.mark.asyncio
    async def test_options_flow_updates_options(
        self, hass: HomeAssistant, mock_config_entry
    ):
        """Test options flow updates options."""
        mock_config_entry.add_to_hass(hass)

        options_flow = GoveeOptionsFlowHandler(mock_config_entry)

        # Submit new options
        result = await options_flow.async_step_user(
            {
                CONF_API_KEY: "new_key",
                CONF_DELAY: 30,
                CONF_USE_ASSUMED_STATE: False,
                CONF_OFFLINE_IS_OFF: True,
                CONF_ENABLE_GROUP_DEVICES: True,
                CONF_DISABLE_ATTRIBUTE_UPDATES: "API:power_state",
            }
        )

        assert result["type"] == FlowResultType.CREATE_ENTRY
        assert result["data"][CONF_API_KEY] == "new_key"
        assert result["data"][CONF_DELAY] == 30
        assert result["data"][CONF_USE_ASSUMED_STATE] is False
        assert result["data"][CONF_OFFLINE_IS_OFF] is True
        assert result["data"][CONF_ENABLE_GROUP_DEVICES] is True
        assert result["data"][CONF_DISABLE_ATTRIBUTE_UPDATES] == "API:power_state"

    @pytest.mark.asyncio
    async def test_options_flow_validates_new_api_key(
        self, hass: HomeAssistant, mock_config_entry
    ):
        """Test options flow validates changed API key."""
        mock_config_entry.add_to_hass(hass)

        options_flow = GoveeOptionsFlowHandler(mock_config_entry)

        with patch(
            "custom_components.govee.config_flow.validate_api_key",
            return_value={CONF_API_KEY: "new_key", CONF_DELAY: 60},
        ) as mock_validate:
            await options_flow.async_step_user(
                {
                    CONF_API_KEY: "new_key",  # Different from config
                    CONF_DELAY: 60,
                    CONF_USE_ASSUMED_STATE: True,
                    CONF_OFFLINE_IS_OFF: False,
                    CONF_ENABLE_GROUP_DEVICES: False,
                }
            )

            # Should validate new key
            mock_validate.assert_called_once()

    @pytest.mark.asyncio
    async def test_options_flow_skips_validation_same_api_key(
        self, hass: HomeAssistant, mock_config_entry
    ):
        """Test options flow skips validation if API key unchanged."""
        mock_config_entry.add_to_hass(hass)

        options_flow = GoveeOptionsFlowHandler(mock_config_entry)

        with patch(
            "custom_components.govee.config_flow.validate_api_key"
        ) as mock_validate:
            # Use same API key as in config
            await options_flow.async_step_user(
                {
                    CONF_API_KEY: mock_config_entry.data[CONF_API_KEY],
                    CONF_DELAY: 30,
                    CONF_USE_ASSUMED_STATE: True,
                    CONF_OFFLINE_IS_OFF: False,
                    CONF_ENABLE_GROUP_DEVICES: False,
                }
            )

            # Should NOT validate (same key)
            mock_validate.assert_not_called()

    @pytest.mark.asyncio
    async def test_options_flow_handles_validation_error(
        self, hass: HomeAssistant, mock_config_entry
    ):
        """Test options flow handles API key validation error."""
        mock_config_entry.add_to_hass(hass)

        options_flow = GoveeOptionsFlowHandler(mock_config_entry)

        with patch(
            "custom_components.govee.config_flow.validate_api_key",
            side_effect=CannotConnect("Invalid key"),
        ):
            result = await options_flow.async_step_user(
                {
                    CONF_API_KEY: "invalid_key",
                    CONF_DELAY: 60,
                    CONF_USE_ASSUMED_STATE: True,
                    CONF_OFFLINE_IS_OFF: False,
                    CONF_ENABLE_GROUP_DEVICES: False,
                }
            )

            assert result["type"] == FlowResultType.FORM
            assert result["errors"] == {CONF_API_KEY: "cannot_connect"}

    @pytest.mark.asyncio
    async def test_options_flow_handles_unknown_error(
        self, hass: HomeAssistant, mock_config_entry
    ):
        """Test options flow handles unknown error."""
        mock_config_entry.add_to_hass(hass)

        options_flow = GoveeOptionsFlowHandler(mock_config_entry)

        with patch(
            "custom_components.govee.config_flow.validate_api_key",
            side_effect=Exception("Unexpected"),
        ):
            result = await options_flow.async_step_user(
                {
                    CONF_API_KEY: "new_key",
                    CONF_DELAY: 60,
                    CONF_USE_ASSUMED_STATE: True,
                    CONF_OFFLINE_IS_OFF: False,
                    CONF_ENABLE_GROUP_DEVICES: False,
                }
            )

            assert result["type"] == FlowResultType.FORM
            assert result["errors"] == {"base": "unknown"}

    @pytest.mark.asyncio
    async def test_options_flow_preserves_defaults(
        self, hass: HomeAssistant, mock_config_entry
    ):
        """Test options flow shows correct defaults from config."""
        mock_config_entry.add_to_hass(hass)

        options_flow = GoveeOptionsFlowHandler(mock_config_entry)

        result = await options_flow.async_step_init()

        # Check defaults are from config entry
        schema = result["data_schema"].schema
        assert schema[CONF_USE_ASSUMED_STATE].default() == False  # From fixture
        assert schema[CONF_OFFLINE_IS_OFF].default() == False
        assert schema[CONF_ENABLE_GROUP_DEVICES].default() == False

    @pytest.mark.asyncio
    async def test_options_flow_step_init_calls_user(
        self, hass: HomeAssistant, mock_config_entry
    ):
        """Test async_step_init delegates to async_step_user."""
        mock_config_entry.add_to_hass(hass)

        options_flow = GoveeOptionsFlowHandler(mock_config_entry)

        with patch.object(
            options_flow, "async_step_user", return_value={"test": "result"}
        ) as mock_user_step:
            result = await options_flow.async_step_init()

            mock_user_step.assert_called_once()
            assert result == {"test": "result"}


# ==============================================================================
# Exception Tests
# ==============================================================================


class TestCannotConnectException:
    """Test CannotConnect exception."""

    def test_cannot_connect_is_exception(self):
        """Test CannotConnect is an exception."""
        from homeassistant.exceptions import HomeAssistantError

        error = CannotConnect("Test message")

        assert isinstance(error, HomeAssistantError)
        assert str(error) == "Test message"
