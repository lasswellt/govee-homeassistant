"""Config flow for Govee integration."""
from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant import config_entries, core, exceptions
from homeassistant.config_entries import ConfigFlowResult
from homeassistant.const import CONF_API_KEY, CONF_DELAY
from homeassistant.core import callback
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from collections.abc import Mapping

from .api import GoveeApiClient, GoveeApiError, GoveeAuthError
from .const import (
    CONFIG_ENTRY_VERSION,
    CONF_DISABLE_ATTRIBUTE_UPDATES,
    CONF_ENABLE_GROUP_DEVICES,
    CONF_OFFLINE_IS_OFF,
    CONF_USE_ASSUMED_STATE,
    DEFAULT_POLL_INTERVAL,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)


async def validate_api_key(
    hass: core.HomeAssistant, user_input: dict[str, Any]
) -> dict[str, Any]:
    api_key = user_input[CONF_API_KEY]
    session = async_get_clientsession(hass)

    async with GoveeApiClient(api_key, session=session) as client:
        try:
            await client.test_connection()
        except GoveeAuthError as err:
            raise CannotConnect("Invalid API key") from err
        except GoveeApiError as err:
            raise CannotConnect(str(err)) from err

    return user_input


class GoveeFlowHandler(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Govee."""

    VERSION = CONFIG_ENTRY_VERSION

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the initial user configuration step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            try:
                user_input = await validate_api_key(self.hass, user_input)

            except CannotConnect as conn_ex:
                _LOGGER.exception("Cannot connect: %s", conn_ex)
                errors[CONF_API_KEY] = "cannot_connect"
            except Exception as ex:  # pylint: disable=broad-except
                _LOGGER.exception("Unexpected exception: %s", ex)
                errors["base"] = "unknown"

            if not errors:
                return self.async_create_entry(title=DOMAIN, data=user_input)

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_API_KEY): cv.string,
                    vol.Optional(CONF_DELAY, default=DEFAULT_POLL_INTERVAL): cv.positive_int,
                }
            ),
            errors=errors,
        )

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """Handle reauth triggered by ConfigEntryAuthFailed."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the reauth confirmation step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            try:
                user_input = await validate_api_key(self.hass, user_input)

            except CannotConnect as conn_ex:
                _LOGGER.exception("Cannot connect during reauth: %s", conn_ex)
                errors[CONF_API_KEY] = "cannot_connect"
            except Exception as ex:  # pylint: disable=broad-except
                _LOGGER.exception("Unexpected exception during reauth: %s", ex)
                errors["base"] = "unknown"

            if not errors:
                # Use modern reauth pattern
                return self.async_update_reload_and_abort(
                    self._get_reauth_entry(),
                    data_updates={CONF_API_KEY: user_input[CONF_API_KEY]},
                )

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_API_KEY): cv.string,
                }
            ),
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> GoveeOptionsFlowHandler:
        return GoveeOptionsFlowHandler(config_entry)


class GoveeOptionsFlowHandler(config_entries.OptionsFlow):
    """Handle Govee options flow."""

    VERSION = 1

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        """Initialize options flow."""
        self.options = dict(config_entry.options)

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle options flow initialization."""
        return await self.async_step_user()

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle options flow user step."""
        old_api_key = self.config_entry.options.get(
            CONF_API_KEY, self.config_entry.data.get(CONF_API_KEY, "")
        )

        errors: dict[str, str] = {}

        if user_input is not None:
            try:
                api_key = user_input[CONF_API_KEY]
                if old_api_key != api_key:
                    user_input = await validate_api_key(self.hass, user_input)

            except CannotConnect as conn_ex:
                _LOGGER.exception("Cannot connect: %s", conn_ex)
                errors[CONF_API_KEY] = "cannot_connect"
            except Exception as ex:  # pylint: disable=broad-except
                _LOGGER.exception("Unexpected exception: %s", ex)
                errors["base"] = "unknown"

            if not errors:
                self.options.update(user_input)
                return await self._update_options()

        options_schema = vol.Schema(
            {
                vol.Required(
                    CONF_API_KEY,
                    default=old_api_key,
                ): cv.string,
                vol.Optional(
                    CONF_DELAY,
                    default=self.config_entry.options.get(
                        CONF_DELAY,
                        self.config_entry.data.get(CONF_DELAY, DEFAULT_POLL_INTERVAL),
                    ),
                ): cv.positive_int,
                vol.Required(
                    CONF_USE_ASSUMED_STATE,
                    default=self.config_entry.options.get(CONF_USE_ASSUMED_STATE, True),
                ): cv.boolean,
                vol.Required(
                    CONF_OFFLINE_IS_OFF,
                    default=self.config_entry.options.get(CONF_OFFLINE_IS_OFF, False),
                ): cv.boolean,
                vol.Required(
                    CONF_ENABLE_GROUP_DEVICES,
                    default=self.config_entry.options.get(CONF_ENABLE_GROUP_DEVICES, False),
                ): cv.boolean,
                vol.Optional(
                    CONF_DISABLE_ATTRIBUTE_UPDATES,
                    default=self.config_entry.options.get(
                        CONF_DISABLE_ATTRIBUTE_UPDATES, ""
                    ),
                ): cv.string,
            },
        )

        return self.async_show_form(
            step_id="user",
            data_schema=options_schema,
            errors=errors,
        )

    async def _update_options(self) -> ConfigFlowResult:
        """Update options and invalidate scene cache."""
        # Invalidate scene cache when options change
        # (especially important when API key changes)
        if hasattr(self.config_entry, "runtime_data") and self.config_entry.runtime_data:
            coordinator = self.config_entry.runtime_data.coordinator
            coordinator.invalidate_scene_cache()
            _LOGGER.debug("Invalidated scene cache on options update")

        return self.async_create_entry(title=DOMAIN, data=self.options)


class CannotConnect(exceptions.HomeAssistantError):
    pass
