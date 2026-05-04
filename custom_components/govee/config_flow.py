"""Config flow for Govee integration.

Fresh version 1 - no migration complexity.
Supports API key authentication with optional account login for MQTT.
Handles Govee 2FA (email verification code) when required.
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
from homeassistant.helpers import config_validation as cv

from .api import (
    Govee2FACodeInvalidError,
    Govee2FARequiredError,
    GoveeApiError,
    GoveeAuthClient,
    GoveeAuthError,
    GoveeIotCredentials,
    GoveeLoginRejectedError,
    validate_govee_credentials,
)
from .api.auth import _derive_client_id
from .api.client import validate_api_key
from .const import (
    CONF_API_KEY,
    CONF_API_TEMPERATURE_UNIT,
    CONF_EMAIL,
    CONF_ENABLE_DIY_SCENES,
    CONF_ENABLE_GROUPS,
    CONF_ENABLE_SCENES,
    CONF_ENABLE_SEGMENTS,
    CONF_EXPOSE_TRANSPORT_ENTITIES,
    CONF_PASSWORD,
    CONF_POLL_INTERVAL,
    CONF_SEGMENT_MODE,
    CONFIG_VERSION,
    DEFAULT_API_TEMPERATURE_UNIT,
    DEFAULT_ENABLE_DIY_SCENES,
    DEFAULT_ENABLE_GROUPS,
    DEFAULT_ENABLE_SCENES,
    DEFAULT_ENABLE_SEGMENTS,
    DEFAULT_EXPOSE_TRANSPORT_ENTITIES,
    DEFAULT_POLL_INTERVAL,
    DEFAULT_SEGMENT_MODE,
    DOMAIN,
    KEY_IOT_CREDENTIALS,
    KEY_IOT_LOGIN_FAILED,
    SEGMENT_MODE_DISABLED,
    SEGMENT_MODE_GROUPED,
    SEGMENT_MODE_INDIVIDUAL,
)

_LOGGER = logging.getLogger(__name__)


def _validate_api_key_format(api_key: str) -> tuple[str, str | None]:
    """Validate API key format before making API call.

    Returns (cleaned_key, error_key or None).
    """
    if not api_key:
        return api_key, "invalid_api_key_format"

    cleaned = api_key.strip()

    # Govee API keys are UUID format (36 chars with hyphens) or similar
    if len(cleaned) < 36:
        return api_key, "invalid_api_key_format"

    # Check for obvious mistakes - spaces in the middle
    if " " in cleaned:
        return api_key, "invalid_api_key_format"

    return cleaned, None


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
        self._client_id: str | None = None
        self._iot_credentials: GoveeIotCredentials | None = None

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        """Get the options flow for this handler.

        The OptionsFlow base class exposes ``self.config_entry`` as a property —
        do not pass it to ``__init__`` (deprecated in HA 2025.12).
        """
        return GoveeOptionsFlow()

    async def async_step_user(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> ConfigFlowResult:
        """Handle the initial step - API key entry."""
        errors: dict[str, str] = {}

        if user_input is not None:
            api_key = user_input[CONF_API_KEY]

            # Validate format before making API call
            cleaned_key, format_error = _validate_api_key_format(api_key)
            if format_error:
                errors["base"] = format_error
            else:
                try:
                    await validate_api_key(cleaned_key)
                    self._api_key = cleaned_key

                    # Proceed to optional account step for MQTT
                    return await self.async_step_account()

                except GoveeAuthError as err:
                    _LOGGER.warning(
                        "API key validation failed: %s (code=%s)",
                        err,
                        getattr(err, "code", None),
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
            email = user_input.get(CONF_EMAIL, "").strip()
            password = user_input.get(CONF_PASSWORD, "").strip()

            # Both empty = skip MQTT
            if not email and not password:
                return self._create_entry()

            # Basic email format check
            if email and "@" not in email:
                errors["base"] = "invalid_email_format"
            # One provided without the other
            elif email and not password:
                errors["base"] = "email_without_password"
            elif password and not email:
                errors["base"] = "password_without_email"
            else:
                # Both provided - validate with API
                # Derive deterministic client_id from email so the same
                # ID is used across login, verification code request,
                # and all subsequent reconfigurations for this account
                self._client_id = _derive_client_id(email)
                try:
                    self._iot_credentials = await validate_govee_credentials(
                        email,
                        password,
                        client_id=self._client_id,
                        hass=self.hass,
                    )
                    self._email = email
                    self._password = password

                    return self._create_entry()

                except Govee2FARequiredError:
                    _LOGGER.info(
                        "Govee 2FA required for '%s' — requesting verification code",
                        email,
                    )
                    self._email = email
                    self._password = password
                    try:
                        async with GoveeAuthClient(hass=self.hass) as client:
                            await client.request_verification_code(
                                email, self._client_id
                            )
                    except GoveeApiError as err:
                        _LOGGER.warning("Failed to request verification code: %s", err)
                        errors["base"] = "cannot_connect"
                    else:
                        return await self.async_step_verification_code()
                except GoveeAuthError as err:
                    _LOGGER.warning(
                        "Govee account validation failed for '%s': %s (code=%s)",
                        email,
                        err,
                        getattr(err, "code", None),
                    )
                    errors["base"] = "invalid_account"
                except GoveeLoginRejectedError as err:
                    _LOGGER.warning("Govee login rejected: %s", err)
                    errors["base"] = "login_rejected"
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
        )

    async def async_step_verification_code(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> ConfigFlowResult:
        """Handle 2FA verification code entry.

        Shown when Govee requires a verification code sent to the user's email.
        """
        errors: dict[str, str] = {}

        if user_input is not None:
            code = user_input["verification_code"].strip()
            if self._email is None or self._password is None:
                # Defensive — the verification step should never be reached
                # without these already set by the preceding account step.
                return self.async_abort(reason="missing_credentials")
            try:
                self._iot_credentials = await validate_govee_credentials(
                    self._email,
                    self._password,
                    code=code,
                    client_id=self._client_id,
                    hass=self.hass,
                )
                # Route based on flow source
                if self.source == "reconfigure":
                    reconfigure_entry = self._get_reconfigure_entry()
                    new_data: dict[str, Any] = {
                        **reconfigure_entry.data,
                        CONF_EMAIL: self._email,
                        CONF_PASSWORD: self._password,
                    }
                    if self._api_key:
                        new_data[CONF_API_KEY] = self._api_key
                    self._clear_mqtt_cache(reconfigure_entry.entry_id)
                    # Pre-cache the IoT credentials so the reload
                    # doesn't try to login again (which would hit 2FA)
                    self._cache_iot_credentials(reconfigure_entry.entry_id)
                    return self.async_update_reload_and_abort(
                        reconfigure_entry,
                        data_updates=new_data,
                    )
                return self._create_entry()

            except Govee2FACodeInvalidError:
                _LOGGER.warning("Govee 2FA code invalid or expired")
                errors["base"] = "invalid_verification_code"
            except GoveeAuthError as err:
                _LOGGER.warning("Govee auth failed during 2FA: %s", err)
                errors["base"] = "invalid_account"
            except GoveeApiError as err:
                _LOGGER.warning("API error during 2FA verification: %s", err)
                errors["base"] = "cannot_connect"
            except Exception:
                _LOGGER.exception("Unexpected error during 2FA verification")
                errors["base"] = "unknown"

        return self.async_show_form(
            step_id="verification_code",
            data_schema=vol.Schema(
                {
                    vol.Required("verification_code"): str,
                }
            ),
            errors=errors,
            description_placeholders={
                "email": self._email or "",
            },
        )

    def _cache_iot_credentials(self, entry_id: str) -> None:
        """Pre-cache IoT credentials in entry.data so reload can skip login.

        When 2FA is required, the config flow obtains IoT credentials during
        the verification step. Without caching, the reload would try to login
        again (without the code) and hit 2FA again.
        """
        if not self._iot_credentials:
            return

        from dataclasses import asdict, is_dataclass
        from typing import Any

        entry = self.hass.config_entries.async_get_entry(entry_id)
        if entry is None:
            _LOGGER.debug("Cannot cache IoT creds — entry %s not found", entry_id)
            return

        creds = self._iot_credentials
        if is_dataclass(creds) and not isinstance(creds, type):
            cred_dict: dict[str, Any] = asdict(creds)
        else:
            # Tolerate mock objects in tests by extracting fields explicitly.
            cred_dict = {
                f: getattr(creds, f, None)
                for f in (
                    "token",
                    "refresh_token",
                    "account_topic",
                    "iot_cert",
                    "iot_key",
                    "iot_ca",
                    "client_id",
                    "endpoint",
                )
            }

        new_data = dict(entry.data)
        new_data[KEY_IOT_CREDENTIALS] = cred_dict
        new_data.pop(KEY_IOT_LOGIN_FAILED, None)
        self.hass.config_entries.async_update_entry(entry, data=new_data)
        _LOGGER.debug("Pre-cached IoT credentials for entry %s", entry_id)

    def _clear_mqtt_cache(self, entry_id: str) -> None:
        """Clear cached MQTT credentials and login-failure marker.

        This allows a fresh login attempt after reconfigure.
        Also dismisses any 2FA repairs issue for this entry.
        """
        entry = self.hass.config_entries.async_get_entry(entry_id)
        if entry is not None:
            new_data = dict(entry.data)
            mutated = False
            if KEY_IOT_CREDENTIALS in new_data:
                new_data.pop(KEY_IOT_CREDENTIALS, None)
                mutated = True
            if KEY_IOT_LOGIN_FAILED in new_data:
                new_data.pop(KEY_IOT_LOGIN_FAILED, None)
                mutated = True
            if mutated:
                self.hass.config_entries.async_update_entry(entry, data=new_data)

        # Dismiss 2FA repairs issue if it exists
        from homeassistant.helpers import issue_registry as ir

        ir.async_delete_issue(self.hass, DOMAIN, f"mqtt_2fa_required_{entry_id}")

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
                CONF_SEGMENT_MODE: DEFAULT_SEGMENT_MODE,
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

            # Validate format before API call
            cleaned_key, format_error = _validate_api_key_format(api_key)
            if format_error:
                errors["base"] = format_error
            else:
                try:
                    await validate_api_key(cleaned_key)

                    # Update existing entry
                    entry = self.hass.config_entries.async_get_entry(
                        self.context["entry_id"]
                    )
                    if entry:
                        self.hass.config_entries.async_update_entry(
                            entry,
                            data={**entry.data, CONF_API_KEY: cleaned_key},
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

            # Validate API key format first
            cleaned_key, format_error = _validate_api_key_format(api_key)
            if format_error:
                errors["base"] = format_error
            else:
                try:
                    await validate_api_key(cleaned_key)

                    # Build updated data
                    new_data: dict[str, Any] = {
                        **reconfigure_entry.data,
                        CONF_API_KEY: cleaned_key,
                    }

                    # Handle optional account credentials
                    email = user_input.get(CONF_EMAIL, "").strip()
                    password = user_input.get(CONF_PASSWORD, "").strip()

                    if email and password:
                        # Validate account credentials if provided
                        # Derive deterministic client_id from email
                        self._client_id = _derive_client_id(email)
                        try:
                            self._iot_credentials = await validate_govee_credentials(
                                email,
                                password,
                                client_id=self._client_id,
                                hass=self.hass,
                            )
                            new_data[CONF_EMAIL] = email
                            new_data[CONF_PASSWORD] = password
                        except Govee2FARequiredError:
                            _LOGGER.info(
                                "Govee 2FA required during reconfigure for '%s'",
                                email,
                            )
                            self._email = email
                            self._password = password
                            self._api_key = cleaned_key
                            try:
                                async with GoveeAuthClient(hass=self.hass) as client:
                                    await client.request_verification_code(
                                        email, self._client_id
                                    )
                            except GoveeApiError as err:
                                _LOGGER.warning(
                                    "Failed to request verification code: %s",
                                    err,
                                )
                                errors["base"] = "cannot_connect"
                            else:
                                return await self.async_step_verification_code()
                        except GoveeAuthError as err:
                            _LOGGER.warning(
                                "Govee account validation failed for '%s' during reconfigure: %s (code=%s)",
                                email,
                                err,
                                getattr(err, "code", None),
                            )
                            errors["base"] = "invalid_account"
                        except GoveeLoginRejectedError as err:
                            _LOGGER.warning(
                                "Govee login rejected during reconfigure: %s", err
                            )
                            errors["base"] = "login_rejected"
                        except GoveeApiError as err:
                            _LOGGER.warning(
                                "Account validation failed during reconfigure: %s", err
                            )
                            errors["base"] = "cannot_connect"
                    elif email and not password:
                        # Email without password - check if keeping existing password
                        existing_email = reconfigure_entry.data.get(CONF_EMAIL, "")
                        existing_password = reconfigure_entry.data.get(
                            CONF_PASSWORD, ""
                        )
                        if email == existing_email and existing_password:
                            # Keeping same email with existing password - OK
                            new_data[CONF_EMAIL] = email
                            new_data[CONF_PASSWORD] = existing_password
                        else:
                            # New email without password
                            errors["base"] = "email_without_password"
                    elif password and not email:
                        errors["base"] = "password_without_email"
                    else:
                        # Both empty - remove account credentials
                        new_data.pop(CONF_EMAIL, None)
                        new_data.pop(CONF_PASSWORD, None)

                    if not errors:
                        # Clear cached MQTT credentials/failure to allow fresh login attempt
                        self._clear_mqtt_cache(reconfigure_entry.entry_id)
                        # Pre-cache IoT credentials so reload doesn't re-login
                        self._cache_iot_credentials(reconfigure_entry.entry_id)

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
    """Handle options for Govee integration.

    Per HA 2025.12 deprecation: do not store config_entry on __init__.
    The base class exposes ``self.config_entry`` as a property.
    """

    def __init__(self) -> None:
        """Initialize options flow."""
        self._global_options: dict[str, Any] = {}
        self._selected_devices: list[str] = []
        self._device_modes: dict[str, str] = {}
        self._device_index: int = 0

    async def async_step_init(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> ConfigFlowResult:
        """Handle global options flow."""
        if user_input is not None:
            # Save global options and proceed to device selection if applicable
            self._global_options = user_input
            _LOGGER.debug("Global options saved: %s", user_input)

            # Check if we have RGBIC devices to configure
            coordinator = self.config_entry.runtime_data
            rgbic_devices = [
                d for d in coordinator.devices.values() if d.segment_count > 0
            ]

            if rgbic_devices:
                _LOGGER.debug(
                    "Found %d RGBIC devices, proceeding to device selection",
                    len(rgbic_devices),
                )
                return await self.async_step_select_segment_devices()
            else:
                _LOGGER.debug("No RGBIC devices found, saving options")
                return self.async_create_entry(title="", data=user_input)

        options = self.config_entry.options
        _LOGGER.debug("Showing global options form with current values: %s", options)

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
                        default=options.get(
                            CONF_ENABLE_DIY_SCENES, DEFAULT_ENABLE_DIY_SCENES
                        ),
                    ): bool,
                    vol.Optional(
                        CONF_EXPOSE_TRANSPORT_ENTITIES,
                        default=options.get(
                            CONF_EXPOSE_TRANSPORT_ENTITIES,
                            DEFAULT_EXPOSE_TRANSPORT_ENTITIES,
                        ),
                    ): bool,
                    vol.Optional(
                        CONF_API_TEMPERATURE_UNIT,
                        default=options.get(
                            CONF_API_TEMPERATURE_UNIT, DEFAULT_API_TEMPERATURE_UNIT
                        ),
                    ): vol.In(["celsius", "fahrenheit"]),
                }
            ),
        )

    async def async_step_select_segment_devices(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> ConfigFlowResult:
        """Select which RGBIC devices to configure individually."""
        coordinator = self.config_entry.runtime_data
        rgbic_devices = {
            d.device_id: f"{d.name} ({d.device_id})"
            for d in coordinator.devices.values()
            if d.segment_count > 0
        }

        if user_input is not None:
            # User selected devices to configure
            self._selected_devices = user_input.get(
                "devices", list(rgbic_devices.keys())
            )
            _LOGGER.debug(
                "Selected %d devices for per-device configuration: %s",
                len(self._selected_devices),
                self._selected_devices,
            )

            if self._selected_devices:
                self._device_index = 0
                self._device_modes = {}
                return await self.async_step_configure_device_mode()
            else:
                # No devices selected, save global options only
                _LOGGER.debug("No devices selected, saving global options only")
                return self.async_create_entry(title="", data=self._global_options)

        # Show device selector
        all_device_ids = list(rgbic_devices.keys())
        _LOGGER.debug(
            "Showing device selector with %d RGBIC devices", len(rgbic_devices)
        )

        return self.async_show_form(
            step_id="select_segment_devices",
            data_schema=vol.Schema(
                {
                    vol.Optional(
                        "devices",
                        default=all_device_ids,
                    ): cv.multi_select(rgbic_devices),
                }
            ),
        )

    async def async_step_configure_device_mode(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> ConfigFlowResult:
        """Configure segment mode for one device at a time."""
        if user_input is not None:
            device_id = self._selected_devices[self._device_index]
            self._device_modes[device_id] = user_input["segment_mode"]
            self._device_index += 1

            # More devices? Loop back. Otherwise save.
            if self._device_index < len(self._selected_devices):
                return await self.async_step_configure_device_mode()

            # Done — build final data and save
            new_data = {
                **self._global_options,
                "segment_mode_by_device": self._device_modes,
            }
            _LOGGER.info("Options saved: %s", new_data)
            _LOGGER.debug("Device modes configured: %s", self._device_modes)
            return self.async_create_entry(title="", data=new_data)

        # Show form for the current device
        coordinator = self.config_entry.runtime_data
        current_device_modes = self.config_entry.options.get(
            "segment_mode_by_device", {}
        )

        device_id = self._selected_devices[self._device_index]
        device = coordinator.devices.get(device_id)
        device_name = device.name if device else device_id
        default_mode = current_device_modes.get(device_id, SEGMENT_MODE_INDIVIDUAL)

        _LOGGER.debug(
            "Showing segment mode form for device %d/%d: %s (%s)",
            self._device_index + 1,
            len(self._selected_devices),
            device_name,
            device_id,
        )

        return self.async_show_form(
            step_id="configure_device_mode",
            data_schema=vol.Schema(
                {
                    vol.Optional("segment_mode", default=default_mode): vol.In(
                        [
                            SEGMENT_MODE_DISABLED,
                            SEGMENT_MODE_GROUPED,
                            SEGMENT_MODE_INDIVIDUAL,
                        ]
                    ),
                }
            ),
            description_placeholders={
                "device_name": device_name,
                "device_id": device_id,
            },
        )
