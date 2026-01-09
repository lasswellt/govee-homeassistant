"""Config flow for Govee integration.

Credential storage follows HA best practices:
- Sensitive data (api_key, email, password) in entry.data (immutable)
- User preferences (poll_interval) in entry.options (mutable)
- TextSelectorType.PASSWORD for credential fields
- Reauthentication flow for expired credentials
"""
from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.config_entries import ConfigFlowResult
from homeassistant.const import CONF_API_KEY, CONF_EMAIL, CONF_PASSWORD
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    BooleanSelector,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    section,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .api import (
    GoveeApiClient,
    GoveeApiError,
    GoveeAuthError,
    validate_govee_credentials,
)

_LOGGER = logging.getLogger(__name__)

DOMAIN = "govee"
CONF_POLL_INTERVAL = "delay"  # Match strings.json key
CONF_INTER_COMMAND_DELAY = "inter_command_delay"
CONF_USE_ASSUMED_STATE = "use_assumed_state"
CONF_OFFLINE_IS_OFF = "offline_is_off"
CONF_ENABLE_GROUP_DEVICES = "enable_group_devices"

DEFAULT_POLL_INTERVAL = 60
MIN_POLL_INTERVAL = 30
MAX_POLL_INTERVAL = 300
DEFAULT_INTER_COMMAND_DELAY = 500


class GoveeConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle Govee config flow.

    Two authentication modes:
    1. API key only: Basic functionality with polling
    2. API key + email/password: Full functionality with AWS IoT real-time updates
    """

    VERSION = 3  # Increment for new credential storage format

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle user configuration step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            api_key = user_input[CONF_API_KEY]
            email = user_input.get(CONF_EMAIL, "").strip() or None
            password = user_input.get(CONF_PASSWORD, "").strip() or None

            # Validate API key
            try:
                session = async_get_clientsession(self.hass)
                async with GoveeApiClient(api_key, session=session) as client:
                    await client.test_connection()
            except GoveeAuthError:
                errors[CONF_API_KEY] = "invalid_api_key"
            except GoveeApiError as err:
                _LOGGER.error("API connection failed: %s", err)
                errors["base"] = "cannot_connect"
            except Exception:
                _LOGGER.exception("Unexpected error validating API key")
                errors["base"] = "unknown"

            # Validate Govee credentials if provided
            if not errors and email and password:
                try:
                    await validate_govee_credentials(email, password, session)
                except GoveeAuthError:
                    errors[CONF_EMAIL] = "invalid_credentials"
                except GoveeApiError as err:
                    _LOGGER.warning("Govee login failed: %s (IoT disabled)", err)
                    # Don't block setup - IoT is optional enhancement
                except Exception:
                    _LOGGER.exception("Unexpected error validating credentials")
                    # Don't block setup - continue without IoT

            if not errors:
                # Store credentials in data (immutable, sensitive)
                # Store preferences in options (mutable)
                return self.async_create_entry(
                    title="Govee",
                    data={
                        CONF_API_KEY: api_key,
                        CONF_EMAIL: email,
                        CONF_PASSWORD: password,
                    },
                    options={
                        CONF_POLL_INTERVAL: user_input.get(CONF_POLL_INTERVAL, DEFAULT_POLL_INTERVAL),
                    },
                )

        # Build schema with proper input types
        schema = vol.Schema(
            {
                vol.Required(CONF_API_KEY): TextSelector(
                    TextSelectorConfig(type=TextSelectorType.PASSWORD)
                ),
                vol.Optional(CONF_EMAIL): TextSelector(
                    TextSelectorConfig(
                        type=TextSelectorType.EMAIL,
                        autocomplete="username",
                    )
                ),
                vol.Optional(CONF_PASSWORD): TextSelector(
                    TextSelectorConfig(
                        type=TextSelectorType.PASSWORD,
                        autocomplete="current-password",
                    )
                ),
                vol.Optional(CONF_POLL_INTERVAL, default=DEFAULT_POLL_INTERVAL): NumberSelector(
                    NumberSelectorConfig(
                        min=MIN_POLL_INTERVAL,
                        max=MAX_POLL_INTERVAL,
                        step=1,
                        mode=NumberSelectorMode.BOX,
                        unit_of_measurement="seconds",
                    )
                ),
            }
        )

        return self.async_show_form(
            step_id="user",
            data_schema=schema,
            errors=errors,
            description_placeholders={
                "iot_note": "Email/password enables real-time updates via AWS IoT MQTT"
            },
        )

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """Handle reauthentication triggered by ConfigEntryAuthFailed."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle reauth confirmation step."""
        errors: dict[str, str] = {}
        reauth_entry = self._get_reauth_entry()

        if user_input is not None:
            api_key = user_input[CONF_API_KEY]
            password = user_input.get(CONF_PASSWORD, "").strip() or None

            # Validate API key
            try:
                session = async_get_clientsession(self.hass)
                async with GoveeApiClient(api_key, session=session) as client:
                    await client.test_connection()
            except GoveeAuthError:
                errors[CONF_API_KEY] = "invalid_api_key"
            except GoveeApiError:
                errors["base"] = "cannot_connect"
            except Exception:
                errors["base"] = "unknown"

            # Validate Govee credentials if email exists and password provided
            email = reauth_entry.data.get(CONF_EMAIL)
            if not errors and email and password:
                try:
                    await validate_govee_credentials(email, password, session)
                except GoveeAuthError:
                    errors[CONF_PASSWORD] = "invalid_credentials"
                except Exception:
                    pass  # IoT is optional

            if not errors:
                # Update credentials
                data_updates: dict[str, Any] = {CONF_API_KEY: api_key}
                if password:
                    data_updates[CONF_PASSWORD] = password

                return self.async_update_reload_and_abort(
                    reauth_entry,
                    data_updates=data_updates,
                )

        # Show form with existing email (read-only context)
        existing_email = reauth_entry.data.get(CONF_EMAIL)

        schema = vol.Schema(
            {
                vol.Required(CONF_API_KEY): TextSelector(
                    TextSelectorConfig(type=TextSelectorType.PASSWORD)
                ),
            }
        )

        # Add password field if email exists
        if existing_email:
            schema = schema.extend(
                {
                    vol.Optional(CONF_PASSWORD): TextSelector(
                        TextSelectorConfig(
                            type=TextSelectorType.PASSWORD,
                            autocomplete="current-password",
                        )
                    ),
                }
            )

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=schema,
            errors=errors,
            description_placeholders={"email": existing_email or "not configured"},
        )

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> GoveeOptionsFlow:
        """Get options flow handler."""
        return GoveeOptionsFlow()


class GoveeOptionsFlow(config_entries.OptionsFlow):
    """Handle Govee options flow with organized sections."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle options flow with sections."""
        errors: dict[str, str] = {}

        if user_input is not None:
            # Extract credentials from sections
            polling = user_input.get("polling", {})
            behavior = user_input.get("behavior", {})
            credentials = user_input.get("credentials", {})

            # Validate credentials if provided
            new_api_key = credentials.get(CONF_API_KEY, "").strip()
            new_email = credentials.get(CONF_EMAIL, "").strip() or None
            new_password = credentials.get(CONF_PASSWORD, "").strip() or None

            # Use existing values if not provided
            if not new_api_key:
                new_api_key = self.config_entry.data.get(CONF_API_KEY, "")

            # Validate API key if changed
            current_api_key = self.config_entry.data.get(CONF_API_KEY, "")
            if new_api_key and new_api_key != current_api_key:
                session = async_get_clientsession(self.hass)
                client = GoveeApiClient(new_api_key, session=session)
                try:
                    await client.get_devices()
                except GoveeAuthError:
                    errors["base"] = "cannot_connect"
                except GoveeApiError:
                    errors["base"] = "cannot_connect"

            # Validate Govee account if both email and password provided
            if new_email and new_password and not errors:
                session = async_get_clientsession(self.hass)
                try:
                    await validate_govee_credentials(new_email, new_password, session)
                except GoveeAuthError:
                    errors["base"] = "invalid_auth"
                except Exception:
                    pass  # Non-critical, MQTT just won't work

            if not errors:
                # Build flattened options dict
                new_options = {
                    CONF_POLL_INTERVAL: polling.get(CONF_POLL_INTERVAL, DEFAULT_POLL_INTERVAL),
                    CONF_INTER_COMMAND_DELAY: polling.get(CONF_INTER_COMMAND_DELAY, DEFAULT_INTER_COMMAND_DELAY),
                    CONF_USE_ASSUMED_STATE: behavior.get(CONF_USE_ASSUMED_STATE, True),
                    CONF_OFFLINE_IS_OFF: behavior.get(CONF_OFFLINE_IS_OFF, False),
                    CONF_ENABLE_GROUP_DEVICES: behavior.get(CONF_ENABLE_GROUP_DEVICES, False),
                }

                # Update entry.data if credentials changed
                new_data = dict(self.config_entry.data)
                data_changed = False

                if new_api_key and new_api_key != current_api_key:
                    new_data[CONF_API_KEY] = new_api_key
                    data_changed = True

                if new_email is not None:
                    new_data[CONF_EMAIL] = new_email
                    data_changed = True

                if new_password is not None:
                    new_data[CONF_PASSWORD] = new_password
                    data_changed = True

                if data_changed:
                    self.hass.config_entries.async_update_entry(
                        self.config_entry,
                        data=new_data,
                    )

                return self.async_create_entry(title="", data=new_options)

        # Get current values
        options = self.config_entry.options
        data = self.config_entry.data

        # Build schema with sections
        schema = vol.Schema(
            {
                # Polling section
                vol.Required("polling"): section(
                    vol.Schema(
                        {
                            vol.Optional(
                                CONF_POLL_INTERVAL,
                                default=options.get(CONF_POLL_INTERVAL, DEFAULT_POLL_INTERVAL),
                            ): NumberSelector(
                                NumberSelectorConfig(
                                    min=MIN_POLL_INTERVAL,
                                    max=MAX_POLL_INTERVAL,
                                    step=5,
                                    mode=NumberSelectorMode.SLIDER,
                                )
                            ),
                            vol.Optional(
                                CONF_INTER_COMMAND_DELAY,
                                default=options.get(CONF_INTER_COMMAND_DELAY, DEFAULT_INTER_COMMAND_DELAY),
                            ): NumberSelector(
                                NumberSelectorConfig(
                                    min=100,
                                    max=2000,
                                    step=100,
                                    mode=NumberSelectorMode.SLIDER,
                                )
                            ),
                        }
                    ),
                ),
                # Behavior section
                vol.Required("behavior"): section(
                    vol.Schema(
                        {
                            vol.Required(
                                CONF_USE_ASSUMED_STATE,
                                default=options.get(CONF_USE_ASSUMED_STATE, True),
                            ): BooleanSelector(),
                            vol.Required(
                                CONF_OFFLINE_IS_OFF,
                                default=options.get(CONF_OFFLINE_IS_OFF, False),
                            ): BooleanSelector(),
                            vol.Required(
                                CONF_ENABLE_GROUP_DEVICES,
                                default=options.get(CONF_ENABLE_GROUP_DEVICES, False),
                            ): BooleanSelector(),
                        }
                    ),
                ),
                # Credentials section (collapsed by default)
                vol.Required("credentials"): section(
                    vol.Schema(
                        {
                            vol.Optional(
                                CONF_API_KEY,
                                description={"suggested_value": data.get(CONF_API_KEY, "")},
                            ): TextSelector(
                                TextSelectorConfig(type=TextSelectorType.PASSWORD)
                            ),
                            vol.Optional(
                                CONF_EMAIL,
                                description={"suggested_value": data.get(CONF_EMAIL, "")},
                            ): TextSelector(
                                TextSelectorConfig(type=TextSelectorType.EMAIL)
                            ),
                            vol.Optional(
                                CONF_PASSWORD,
                            ): TextSelector(
                                TextSelectorConfig(type=TextSelectorType.PASSWORD)
                            ),
                        }
                    ),
                    {"collapsed": True},
                ),
            }
        )

        return self.async_show_form(step_id="init", data_schema=schema, errors=errors)
