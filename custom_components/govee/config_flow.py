"""Config flow for Govee integration.

Fresh version 1 - no migration complexity.
Supports API key authentication with optional account login for MQTT.
"""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import callback

from .api import (
    GoveeApiError,
    GoveeAuthError,
    GoveeIotCredentials,
    validate_govee_credentials,
)
from .api.client import validate_api_key
from .const import (
    CONF_API_KEY,
    CONF_EMAIL,
    CONF_ENABLE_DIY_SCENES,
    CONF_ENABLE_GROUPS,
    CONF_ENABLE_SCENES,
    CONF_ENABLE_SEGMENTS,
    CONF_PASSWORD,
    CONF_POLL_INTERVAL,
    CONFIG_VERSION,
    DEFAULT_ENABLE_DIY_SCENES,
    DEFAULT_ENABLE_GROUPS,
    DEFAULT_ENABLE_SCENES,
    DEFAULT_ENABLE_SEGMENTS,
    DEFAULT_POLL_INTERVAL,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)


class GoveeConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Govee.

    Steps:
    1. User enters API key (required)
    2. Optionally enter email/password for MQTT real-time updates
    3. Create config entry
    """

    VERSION = CONFIG_VERSION

    def __init__(self) -> None:
        """Initialize the config flow."""
        self._api_key: str | None = None
        self._email: str | None = None
        self._password: str | None = None
        self._iot_credentials: GoveeIotCredentials | None = None

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        """Get the options flow for this handler."""
        return GoveeOptionsFlow(config_entry)

    async def async_step_user(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> ConfigFlowResult:
        """Handle the initial step - API key entry."""
        errors: dict[str, str] = {}

        if user_input is not None:
            api_key = user_input[CONF_API_KEY]

            try:
                await validate_api_key(api_key)
                self._api_key = api_key

                # Proceed to optional account step for MQTT
                return await self.async_step_account()

            except GoveeAuthError as err:
                _LOGGER.warning(
                    "API key validation failed: %s (code=%s)", err, getattr(err, "code", None)
                )
                errors["base"] = "invalid_auth"
            except GoveeApiError as err:
                _LOGGER.error("API validation failed: %s", err)
                errors["base"] = "cannot_connect"
            except Exception:
                _LOGGER.exception("Unexpected error during API validation")
                errors["base"] = "unknown"

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_API_KEY): str,
                }
            ),
            errors=errors,
            description_placeholders={
                "api_url": "https://developer.govee.com/",
            },
        )

    async def async_step_account(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> ConfigFlowResult:
        """Handle optional account credentials for MQTT.

        Users can skip this step if they don't want real-time updates.
        """
        errors: dict[str, str] = {}

        if user_input is not None:
            # Check if user wants to skip MQTT
            if not user_input.get(CONF_EMAIL):
                # Skip MQTT, create entry with API key only
                return self._create_entry()

            email = user_input[CONF_EMAIL]
            password = user_input[CONF_PASSWORD]

            try:
                self._iot_credentials = await validate_govee_credentials(email, password)
                self._email = email
                self._password = password

                return self._create_entry()

            except GoveeAuthError as err:
                _LOGGER.warning(
                    "Govee account validation failed for '%s': %s (code=%s)",
                    email,
                    err,
                    getattr(err, "code", None),
                )
                errors["base"] = "invalid_auth"
            except GoveeApiError as err:
                _LOGGER.error("Account validation failed: %s", err)
                errors["base"] = "cannot_connect"
            except Exception:
                _LOGGER.exception("Unexpected error during account validation")
                errors["base"] = "unknown"

        return self.async_show_form(
            step_id="account",
            data_schema=vol.Schema(
                {
                    vol.Optional(CONF_EMAIL): str,
                    vol.Optional(CONF_PASSWORD): str,
                }
            ),
            errors=errors,
            description_placeholders={
                "note": "Optional: Enter Govee account for real-time MQTT updates",
            },
        )

    def _clear_mqtt_cache(self, entry_id: str) -> None:
        """Clear cached MQTT credentials and login failure for an entry.

        This allows a fresh login attempt after reconfigure.
        """
        if DOMAIN not in self.hass.data:
            return

        # Clear cached credentials
        if "iot_credentials" in self.hass.data[DOMAIN]:
            self.hass.data[DOMAIN]["iot_credentials"].pop(entry_id, None)

        # Clear login failure flag
        if "iot_login_failed" in self.hass.data[DOMAIN]:
            self.hass.data[DOMAIN]["iot_login_failed"].pop(entry_id, None)

        _LOGGER.debug("Cleared MQTT cache for entry %s", entry_id)

    def _create_entry(self) -> ConfigFlowResult:
        """Create the config entry."""
        data: dict[str, Any] = {
            CONF_API_KEY: self._api_key,
        }

        # Add account credentials if provided
        if self._email and self._password:
            data[CONF_EMAIL] = self._email
            data[CONF_PASSWORD] = self._password

        return self.async_create_entry(
            title="Govee",
            data=data,
            options={
                CONF_POLL_INTERVAL: DEFAULT_POLL_INTERVAL,
                CONF_ENABLE_GROUPS: DEFAULT_ENABLE_GROUPS,
                CONF_ENABLE_SCENES: DEFAULT_ENABLE_SCENES,
                CONF_ENABLE_DIY_SCENES: DEFAULT_ENABLE_DIY_SCENES,
                CONF_ENABLE_SEGMENTS: DEFAULT_ENABLE_SEGMENTS,
            },
        )

    async def async_step_reauth(
        self,
        entry_data: dict[str, Any],
    ) -> ConfigFlowResult:
        """Handle re-authentication request."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> ConfigFlowResult:
        """Handle re-authentication confirmation."""
        errors: dict[str, str] = {}

        if user_input is not None:
            api_key = user_input[CONF_API_KEY]

            try:
                await validate_api_key(api_key)

                # Update existing entry
                entry = self.hass.config_entries.async_get_entry(
                    self.context["entry_id"]
                )
                if entry:
                    self.hass.config_entries.async_update_entry(
                        entry,
                        data={**entry.data, CONF_API_KEY: api_key},
                    )
                    await self.hass.config_entries.async_reload(entry.entry_id)
                    return self.async_abort(reason="reauth_successful")

            except GoveeAuthError as err:
                _LOGGER.warning(
                    "API key validation failed during reauth: %s (code=%s)",
                    err,
                    getattr(err, "code", None),
                )
                errors["base"] = "invalid_auth"
            except GoveeApiError as err:
                _LOGGER.warning("API validation failed during reauth: %s", err)
                errors["base"] = "cannot_connect"
            except Exception:
                _LOGGER.exception("Unexpected error during reauth")
                errors["base"] = "unknown"

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_API_KEY): str,
                }
            ),
            errors=errors,
        )

    async def async_step_reconfigure(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> ConfigFlowResult:
        """Handle reconfiguration of the integration.

        Allows users to update API key and account credentials without
        removing and re-adding the integration.
        """
        errors: dict[str, str] = {}
        reconfigure_entry = self._get_reconfigure_entry()

        if user_input is not None:
            api_key = user_input[CONF_API_KEY]

            try:
                await validate_api_key(api_key)

                # Build updated data
                new_data: dict[str, Any] = {
                    **reconfigure_entry.data,
                    CONF_API_KEY: api_key,
                }

                # Handle optional account credentials
                email = user_input.get(CONF_EMAIL, "").strip()
                password = user_input.get(CONF_PASSWORD, "").strip()

                if email and password:
                    # Validate account credentials if provided
                    try:
                        await validate_govee_credentials(email, password)
                        new_data[CONF_EMAIL] = email
                        new_data[CONF_PASSWORD] = password
                    except GoveeAuthError as err:
                        _LOGGER.warning(
                            "Govee account validation failed for '%s' during reconfigure: %s (code=%s)",
                            email,
                            err,
                            getattr(err, "code", None),
                        )
                        errors["base"] = "invalid_account"
                        # Continue to show form with error
                    except GoveeApiError as err:
                        _LOGGER.warning("Account validation failed during reconfigure: %s", err)
                        errors["base"] = "cannot_connect"
                elif not email and not password:
                    # Remove account credentials if both are empty
                    new_data.pop(CONF_EMAIL, None)
                    new_data.pop(CONF_PASSWORD, None)

                if not errors:
                    # Clear cached MQTT credentials/failure to allow fresh login attempt
                    self._clear_mqtt_cache(reconfigure_entry.entry_id)

                    return self.async_update_reload_and_abort(
                        reconfigure_entry,
                        data_updates=new_data,
                    )

            except GoveeAuthError as err:
                _LOGGER.warning(
                    "API key validation failed during reconfigure: %s (code=%s)",
                    err,
                    getattr(err, "code", None),
                )
                errors["base"] = "invalid_auth"
            except GoveeApiError as err:
                _LOGGER.warning("API validation failed during reconfigure: %s", err)
                errors["base"] = "cannot_connect"
            except Exception:
                _LOGGER.exception("Unexpected error during reconfigure")
                errors["base"] = "unknown"

        # Pre-fill current values (except sensitive data)
        current_email = reconfigure_entry.data.get(CONF_EMAIL, "")

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_API_KEY): str,
                    vol.Optional(CONF_EMAIL, default=current_email): str,
                    vol.Optional(CONF_PASSWORD): str,
                }
            ),
            errors=errors,
            description_placeholders={
                "current_email": current_email or "not configured",
            },
        )


class GoveeOptionsFlow(OptionsFlow):
    """Handle options for Govee integration."""

    def __init__(self, config_entry: ConfigEntry) -> None:
        """Initialize options flow."""
        self._config_entry = config_entry

    async def async_step_init(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> ConfigFlowResult:
        """Handle options flow."""
        if user_input is not None:
            _LOGGER.info("Options saved: %s", user_input)
            _LOGGER.debug(
                "Previous options: %s",
                self._config_entry.options,
            )
            return self.async_create_entry(title="", data=user_input)

        options = self._config_entry.options
        _LOGGER.debug("Showing options form with current values: %s", options)

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Optional(
                        CONF_POLL_INTERVAL,
                        default=options.get(CONF_POLL_INTERVAL, DEFAULT_POLL_INTERVAL),
                    ): vol.All(vol.Coerce(int), vol.Range(min=30, max=300)),
                    vol.Optional(
                        CONF_ENABLE_GROUPS,
                        default=options.get(CONF_ENABLE_GROUPS, DEFAULT_ENABLE_GROUPS),
                    ): bool,
                    vol.Optional(
                        CONF_ENABLE_SCENES,
                        default=options.get(CONF_ENABLE_SCENES, DEFAULT_ENABLE_SCENES),
                    ): bool,
                    vol.Optional(
                        CONF_ENABLE_DIY_SCENES,
                        default=options.get(CONF_ENABLE_DIY_SCENES, DEFAULT_ENABLE_DIY_SCENES),
                    ): bool,
                    vol.Optional(
                        CONF_ENABLE_SEGMENTS,
                        default=options.get(CONF_ENABLE_SEGMENTS, DEFAULT_ENABLE_SEGMENTS),
                    ): bool,
                }
            ),
        )
