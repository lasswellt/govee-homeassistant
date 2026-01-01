from __future__ import annotations

from datetime import timedelta
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_API_KEY, CONF_DELAY
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import GoveeApiClient, GoveeApiError, GoveeAuthError
from .const import CONFIG_ENTRY_VERSION, DEFAULT_POLL_INTERVAL, DOMAIN, PLATFORMS
from .coordinator import GoveeDataUpdateCoordinator
from .models import GoveeConfigEntry, GoveeRuntimeData

__all__ = ["GoveeConfigEntry", "GoveeRuntimeData"]

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry: GoveeConfigEntry) -> bool:
    _LOGGER.debug("Setting up Govee integration")

    config = entry.data
    options = entry.options
    api_key = options.get(CONF_API_KEY, config.get(CONF_API_KEY, ""))
    poll_interval = options.get(CONF_DELAY, config.get(CONF_DELAY, DEFAULT_POLL_INTERVAL))

    session = async_get_clientsession(hass)
    client = GoveeApiClient(api_key, session=session)

    coordinator = GoveeDataUpdateCoordinator(
        hass,
        entry,
        client,
        update_interval=timedelta(seconds=poll_interval),
    )

    try:
        await coordinator.async_config_entry_first_refresh()
    except GoveeAuthError as err:
        _LOGGER.error("Invalid API key: %s", err)
        await client.close()
        raise ConfigEntryAuthFailed("Invalid API key") from err
    except GoveeApiError as err:
        _LOGGER.error("Failed to connect to Govee API: %s", err)
        await client.close()
        raise ConfigEntryNotReady(f"Failed to connect: {err}") from err

    entry.runtime_data = GoveeRuntimeData(
        client=client,
        coordinator=coordinator,
        devices=coordinator.devices,
    )

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    entry.async_on_unload(entry.add_update_listener(async_options_updated))

    _LOGGER.info(
        "Govee integration set up with %d devices",
        len(coordinator.devices),
    )

    return True


async def async_unload_entry(hass: HomeAssistant, entry: GoveeConfigEntry) -> bool:
    _LOGGER.debug("Unloading Govee integration")

    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

    if unload_ok:
        await entry.runtime_data.client.close()

    return unload_ok


async def async_options_updated(hass: HomeAssistant, entry: GoveeConfigEntry) -> None:
    _LOGGER.debug("Options updated, reloading integration")
    await hass.config_entries.async_reload(entry.entry_id)


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    _LOGGER.debug("Migrating config entry from version %s", entry.version)

    if entry.version < CONFIG_ENTRY_VERSION:
        new_data = dict(entry.data)
        new_options = dict(entry.options)

        hass.config_entries.async_update_entry(
            entry,
            data=new_data,
            options=new_options,
            version=CONFIG_ENTRY_VERSION,
        )

        _LOGGER.info("Migration to version %s successful", CONFIG_ENTRY_VERSION)

    return True
