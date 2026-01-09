"""Govee integration for Home Assistant.

Provides control of Govee devices via:
- REST API (polling, requires API key)
- AWS IoT MQTT (real-time, requires email/password)

State management:
- Main devices: Source-based state (poll after command)
- Segments/Scenes: Optimistic state with RestoreEntity
"""
from __future__ import annotations

import logging
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_API_KEY, CONF_EMAIL, CONF_PASSWORD, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import GoveeApiClient, GoveeApiError, GoveeAuthError, validate_govee_credentials
from .coordinator import GoveeCoordinator
from .models import GoveeConfigEntry, GoveeRuntimeData

_LOGGER = logging.getLogger(__name__)

DOMAIN = "govee"
CONF_POLL_INTERVAL = "delay"  # Match strings.json key
DEFAULT_POLL_INTERVAL = 60

PLATFORMS = [Platform.LIGHT]


async def async_setup_entry(hass: HomeAssistant, entry: GoveeConfigEntry) -> bool:
    """Set up Govee from config entry."""
    _LOGGER.debug("Setting up Govee integration")

    # Get credentials from entry.data (immutable, sensitive)
    api_key = entry.data[CONF_API_KEY]
    email = entry.data.get(CONF_EMAIL)
    password = entry.data.get(CONF_PASSWORD)

    # Get preferences from entry.options (mutable)
    poll_interval = entry.options.get(CONF_POLL_INTERVAL, DEFAULT_POLL_INTERVAL)

    # Create API client
    session = async_get_clientsession(hass)
    client = GoveeApiClient(api_key, session=session)

    # Create coordinator
    coordinator = GoveeCoordinator(
        hass,
        entry,
        client,
        update_interval=timedelta(seconds=poll_interval),
    )

    # First refresh - fetches devices and initial state
    try:
        await coordinator.async_config_entry_first_refresh()
    except GoveeAuthError as err:
        _LOGGER.error("Invalid API key: %s", err)
        raise ConfigEntryAuthFailed("Invalid API key") from err
    except GoveeApiError as err:
        _LOGGER.error("Failed to connect to Govee API: %s", err)
        raise ConfigEntryNotReady(f"Failed to connect: {err}") from err

    # Store runtime data
    entry.runtime_data = GoveeRuntimeData(
        client=client,
        coordinator=coordinator,
        devices=coordinator.devices,
    )

    # Set up AWS IoT MQTT if credentials provided
    iot_status = "disabled"
    if email and password:
        try:
            credentials = await validate_govee_credentials(email, password, session)
            await coordinator.async_setup_iot(credentials)
            iot_status = "enabled"
        except GoveeAuthError as err:
            _LOGGER.warning(
                "Govee login failed, real-time updates disabled: %s", err
            )
            iot_status = "auth_failed"
        except Exception as err:
            _LOGGER.warning(
                "Failed to set up AWS IoT, real-time updates disabled: %s", err
            )
            iot_status = "error"

    # Register cleanup
    entry.async_on_unload(coordinator.async_stop_iot)
    entry.async_on_unload(entry.add_update_listener(async_options_updated))

    # Forward to platforms
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    _LOGGER.info(
        "Govee integration set up: %d devices, poll %ds, IoT %s",
        len(coordinator.devices),
        poll_interval,
        iot_status,
    )

    return True


async def async_unload_entry(hass: HomeAssistant, entry: GoveeConfigEntry) -> bool:
    """Unload Govee config entry."""
    _LOGGER.debug("Unloading Govee integration")

    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_options_updated(hass: HomeAssistant, entry: GoveeConfigEntry) -> None:
    """Handle options update - update poll interval dynamically."""
    coordinator = entry.runtime_data.coordinator
    new_interval = entry.options.get(CONF_POLL_INTERVAL, DEFAULT_POLL_INTERVAL)

    if coordinator.update_interval != timedelta(seconds=new_interval):
        coordinator.update_interval = timedelta(seconds=new_interval)
        _LOGGER.info("Poll interval updated to %ds", new_interval)


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Migrate config entry to new version."""
    _LOGGER.debug("Migrating config entry from version %s", entry.version)

    if entry.version < 3:
        # Migrate to v3: credentials in data, preferences in options
        new_data = {
            CONF_API_KEY: entry.data.get(CONF_API_KEY) or entry.options.get(CONF_API_KEY, ""),
            CONF_EMAIL: entry.data.get(CONF_EMAIL),
            CONF_PASSWORD: entry.data.get(CONF_PASSWORD),
        }
        # Preserve all options, handling both old "poll_interval" and "delay" keys
        poll_interval = (
            entry.options.get("delay")
            or entry.options.get("poll_interval")
            or entry.data.get("delay", DEFAULT_POLL_INTERVAL)
        )
        new_options = {
            CONF_POLL_INTERVAL: poll_interval,
            # Preserve other options with their defaults
            "inter_command_delay": entry.options.get("inter_command_delay", 500),
            "use_assumed_state": entry.options.get("use_assumed_state", True),
            "offline_is_off": entry.options.get("offline_is_off", False),
            "enable_group_devices": entry.options.get("enable_group_devices", False),
        }

        hass.config_entries.async_update_entry(
            entry,
            data=new_data,
            options=new_options,
            version=3,
        )
        _LOGGER.info("Migrated config entry to version 3")

    return True
