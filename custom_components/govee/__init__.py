"""Govee integration for Home Assistant.

Controls Govee lights, LED strips, and smart devices via the Govee Cloud API.
Supports real-time state updates via AWS IoT MQTT.
"""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers import entity_registry as er

from .api import GoveeApiClient, GoveeAuthError, GoveeIotCredentials
from .api.auth import GoveeAuthClient
from .const import (
    CONF_API_KEY,
    CONF_EMAIL,
    CONF_ENABLE_GROUPS,
    CONF_PASSWORD,
    CONF_POLL_INTERVAL,
    DEFAULT_ENABLE_GROUPS,
    DEFAULT_POLL_INTERVAL,
    DOMAIN,
)
from .coordinator import GoveeCoordinator
from .services import async_setup_services, async_unload_services

_LOGGER = logging.getLogger(__name__)

# Platforms to set up
PLATFORMS: list[Platform] = [
    Platform.LIGHT,
    Platform.SELECT,  # Scene dropdowns
    Platform.SWITCH,
    Platform.SENSOR,
    Platform.BUTTON,
]

# Type alias for runtime data
type GoveeConfigEntry = ConfigEntry[GoveeCoordinator]


async def async_setup_entry(hass: HomeAssistant, entry: GoveeConfigEntry) -> bool:
    """Set up Govee from a config entry.

    Args:
        hass: Home Assistant instance.
        entry: Config entry being set up.

    Returns:
        True if setup was successful.

    Raises:
        ConfigEntryAuthFailed: Invalid API key.
        ConfigEntryNotReady: Temporary setup failure.
    """
    api_key = entry.data[CONF_API_KEY]

    # Create API client
    api_client = GoveeApiClient(api_key)

    # Optionally get IoT credentials for MQTT
    iot_credentials: GoveeIotCredentials | None = None
    email = entry.data.get(CONF_EMAIL)
    password = entry.data.get(CONF_PASSWORD)

    if email and password:
        try:
            async with GoveeAuthClient() as auth_client:
                iot_credentials = await auth_client.login(email, password)
                _LOGGER.info("MQTT credentials obtained for real-time updates")
        except GoveeAuthError as err:
            _LOGGER.warning("Failed to get MQTT credentials: %s", err)
            # Continue without MQTT - not a fatal error
        except Exception as err:
            _LOGGER.warning("MQTT setup failed: %s", err)

    # Get options
    options = entry.options
    poll_interval = options.get(CONF_POLL_INTERVAL, DEFAULT_POLL_INTERVAL)
    enable_groups = options.get(CONF_ENABLE_GROUPS, DEFAULT_ENABLE_GROUPS)

    # Create coordinator
    coordinator = GoveeCoordinator(
        hass=hass,
        config_entry=entry,
        api_client=api_client,
        iot_credentials=iot_credentials,
        poll_interval=poll_interval,
        enable_groups=enable_groups,
    )

    # Set up coordinator (discover devices, start MQTT)
    try:
        await coordinator.async_setup()
    except ConfigEntryAuthFailed:
        await api_client.close()
        raise
    except Exception as err:
        await api_client.close()
        raise ConfigEntryNotReady(f"Failed to set up Govee: {err}") from err

    # Initial refresh
    await coordinator.async_config_entry_first_refresh()

    # Clean up orphaned entities (e.g., groups that are now disabled)
    await _async_cleanup_orphaned_entities(hass, entry, coordinator)

    # Store coordinator in entry
    entry.runtime_data = coordinator

    # Set up platforms
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Set up services (only once)
    if not hass.data.get(DOMAIN):
        hass.data[DOMAIN] = {}
        await async_setup_services(hass)

    # Store coordinator in hass.data for services access
    hass.data[DOMAIN][entry.entry_id] = coordinator

    # Register update listener for options changes
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))

    return True


async def async_unload_entry(hass: HomeAssistant, entry: GoveeConfigEntry) -> bool:
    """Unload a config entry.

    Args:
        hass: Home Assistant instance.
        entry: Config entry being unloaded.

    Returns:
        True if unload was successful.
    """
    # Unload platforms
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

    if unload_ok:
        # Shutdown coordinator
        coordinator = entry.runtime_data
        await coordinator.async_shutdown()

        # Remove from hass.data
        hass.data[DOMAIN].pop(entry.entry_id, None)

        # Unload services if no more entries
        if not hass.data[DOMAIN]:
            await async_unload_services(hass)
            hass.data.pop(DOMAIN, None)

    return unload_ok


async def _async_cleanup_orphaned_entities(
    hass: HomeAssistant,
    entry: ConfigEntry,
    coordinator: GoveeCoordinator,
) -> None:
    """Remove entity registry entries for devices no longer discovered.

    This handles cleanup when group devices are disabled or devices are removed.
    """
    entity_registry = er.async_get(hass)

    # Suffixes used by various entity types
    entity_suffixes = (
        "_scene_select",
        "_diy_scene_select",
        "_refresh_scenes",
        "_night_light",
    )

    # Get all entity entries for this config entry
    entries_to_remove = []
    for entity_entry in er.async_entries_for_config_entry(entity_registry, entry.entry_id):
        # Extract device_id from unique_id
        unique_id = entity_entry.unique_id
        if unique_id:
            device_id = unique_id

            # Handle segment entities (e.g., "AA:BB:CC:DD_segment_0")
            if "_segment_" in device_id:
                device_id = device_id.split("_segment_")[0]

            # Handle other entity suffixes
            for suffix in entity_suffixes:
                if device_id.endswith(suffix):
                    device_id = device_id[: -len(suffix)]
                    break

            # Check if this device is still discovered
            if device_id not in coordinator.devices:
                entries_to_remove.append(entity_entry)
                _LOGGER.debug(
                    "Marking orphaned entity for removal: %s (device %s not discovered)",
                    entity_entry.entity_id,
                    device_id,
                )

    # Remove orphaned entries
    for entity_entry in entries_to_remove:
        _LOGGER.info("Removing orphaned entity: %s", entity_entry.entity_id)
        entity_registry.async_remove(entity_entry.entity_id)

    if entries_to_remove:
        _LOGGER.info("Cleaned up %d orphaned entities", len(entries_to_remove))


async def _async_update_listener(
    hass: HomeAssistant,
    entry: GoveeConfigEntry,
) -> None:
    """Handle options update.

    Reloads the integration when options change.
    """
    await hass.config_entries.async_reload(entry.entry_id)
