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
    CONF_ENABLE_DIY_SCENES,
    CONF_ENABLE_GROUPS,
    CONF_ENABLE_SCENES,
    CONF_ENABLE_SEGMENTS,
    CONF_PASSWORD,
    CONF_POLL_INTERVAL,
    DEFAULT_ENABLE_DIY_SCENES,
    DEFAULT_ENABLE_GROUPS,
    DEFAULT_ENABLE_SCENES,
    DEFAULT_ENABLE_SEGMENTS,
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

# Keys for storing cached data in hass.data[DOMAIN]
_KEY_IOT_CREDENTIALS = "iot_credentials"
_KEY_IOT_LOGIN_FAILED = "iot_login_failed"


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
    # Credentials are cached to avoid repeated login attempts on reload
    iot_credentials: GoveeIotCredentials | None = None
    email = entry.data.get(CONF_EMAIL)
    password = entry.data.get(CONF_PASSWORD)

    if email and password:
        # Initialize domain data if needed
        if DOMAIN not in hass.data:
            hass.data[DOMAIN] = {}

        # Check for cached credentials or previous login failure
        cached_creds = hass.data[DOMAIN].get(_KEY_IOT_CREDENTIALS, {}).get(entry.entry_id)
        login_failed = hass.data[DOMAIN].get(_KEY_IOT_LOGIN_FAILED, {}).get(entry.entry_id)

        if cached_creds:
            # Reuse cached credentials
            iot_credentials = cached_creds
            _LOGGER.debug("Using cached MQTT credentials")
        elif login_failed:
            # Skip login attempt - previous failure recorded
            _LOGGER.debug(
                "Skipping MQTT login - previous attempt failed: %s. "
                "Reconfigure integration to retry.",
                login_failed,
            )
        else:
            # Attempt fresh login
            try:
                async with GoveeAuthClient() as auth_client:
                    iot_credentials = await auth_client.login(email, password)
                    _LOGGER.info("MQTT credentials obtained for real-time updates")

                    # Cache successful credentials
                    if _KEY_IOT_CREDENTIALS not in hass.data[DOMAIN]:
                        hass.data[DOMAIN][_KEY_IOT_CREDENTIALS] = {}
                    hass.data[DOMAIN][_KEY_IOT_CREDENTIALS][entry.entry_id] = iot_credentials

            except GoveeAuthError as err:
                _LOGGER.warning("Failed to get MQTT credentials: %s", err)
                # Record failure to prevent repeated attempts
                if _KEY_IOT_LOGIN_FAILED not in hass.data[DOMAIN]:
                    hass.data[DOMAIN][_KEY_IOT_LOGIN_FAILED] = {}
                hass.data[DOMAIN][_KEY_IOT_LOGIN_FAILED][entry.entry_id] = str(err)
            except Exception as err:
                _LOGGER.warning("MQTT setup failed: %s", err)
                # Record failure to prevent repeated attempts
                if _KEY_IOT_LOGIN_FAILED not in hass.data[DOMAIN]:
                    hass.data[DOMAIN][_KEY_IOT_LOGIN_FAILED] = {}
                hass.data[DOMAIN][_KEY_IOT_LOGIN_FAILED][entry.entry_id] = str(err)

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
    """Remove entity registry entries for devices no longer discovered or features disabled.

    This handles cleanup when:
    - Devices are removed from the Govee account
    - Group devices are disabled via enable_groups option
    - Segment entities are disabled via enable_segments option
    - Scene entities are disabled via enable_scenes option
    - DIY scene entities are disabled via enable_diy_scenes option
    """
    entity_registry = er.async_get(hass)

    # Get current options
    options = entry.options
    enable_segments = options.get(CONF_ENABLE_SEGMENTS, DEFAULT_ENABLE_SEGMENTS)
    enable_scenes = options.get(CONF_ENABLE_SCENES, DEFAULT_ENABLE_SCENES)
    enable_diy_scenes = options.get(CONF_ENABLE_DIY_SCENES, DEFAULT_ENABLE_DIY_SCENES)

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
        if not unique_id:
            continue

        should_remove = False
        removal_reason = ""

        # Check for segment entities that should be removed
        if "_segment_" in unique_id:
            if not enable_segments:
                should_remove = True
                removal_reason = "segments disabled"
            else:
                # Check if parent device exists
                device_id = unique_id.split("_segment_")[0]
                if device_id not in coordinator.devices:
                    should_remove = True
                    removal_reason = f"device {device_id} not discovered"

        # Check for scene select entities that should be removed
        elif unique_id.endswith("_scene_select"):
            if not enable_scenes:
                should_remove = True
                removal_reason = "scenes disabled"
            else:
                device_id = unique_id[: -len("_scene_select")]
                if device_id not in coordinator.devices:
                    should_remove = True
                    removal_reason = f"device {device_id} not discovered"

        # Check for DIY scene select entities that should be removed
        elif unique_id.endswith("_diy_scene_select"):
            if not enable_diy_scenes:
                should_remove = True
                removal_reason = "DIY scenes disabled"
            else:
                device_id = unique_id[: -len("_diy_scene_select")]
                if device_id not in coordinator.devices:
                    should_remove = True
                    removal_reason = f"device {device_id} not discovered"

        # Check other entity types for device existence
        else:
            device_id = unique_id
            for suffix in entity_suffixes:
                if device_id.endswith(suffix):
                    device_id = device_id[: -len(suffix)]
                    break

            if device_id not in coordinator.devices:
                should_remove = True
                removal_reason = f"device {device_id} not discovered"

        if should_remove:
            entries_to_remove.append(entity_entry)
            _LOGGER.debug(
                "Marking orphaned entity for removal: %s (%s)",
                entity_entry.entity_id,
                removal_reason,
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
