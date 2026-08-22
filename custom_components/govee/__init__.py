"""Govee integration for Home Assistant.

Controls Govee lights, LED strips, and smart devices via the Govee Cloud API.
Supports real-time state updates via AWS IoT MQTT.
"""

from __future__ import annotations

import logging
from dataclasses import asdict
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers import device_registry as dr, entity_registry as er

from homeassistant.helpers import issue_registry as ir

from .api import (
    Govee2FARequiredError,
    GoveeApiClient,
    GoveeAuthError,
    GoveeIotCredentials,
)
from .api.auth import GoveeAuthClient, _derive_client_id
from .const import (
    CONF_API_KEY,
    CONFIG_VERSION,
    CONF_EMAIL,
    CONF_ENABLE_DIY_SCENES,
    CONF_ENABLE_GROUPS,
    CONF_ENABLE_SCENES,
    CONF_PASSWORD,
    CONF_POLL_INTERVAL,
    DEFAULT_ENABLE_DIY_SCENES,
    DEFAULT_ENABLE_GROUPS,
    DEFAULT_ENABLE_SCENES,
    DEFAULT_POLL_INTERVAL,
    DEFAULT_SEGMENT_MODE,
    DOMAIN,
    KEY_IOT_CREDENTIALS,
    KEY_IOT_LOGIN_FAILED,
    SEGMENT_MODE_GROUPED,
    SEGMENT_MODE_INDIVIDUAL,
    SUFFIX_DIY_SCENE_SELECT,
    SUFFIX_GROUPED_SEGMENT,
    SUFFIX_SCENE_SELECT,
    SUFFIX_SEGMENT,
)
from .coordinator import GoveeCoordinator
from .services import (
    SERVICE_REFRESH_SCENES,
    async_setup_services,
    async_unload_services,
)

_LOGGER = logging.getLogger(__name__)

# Platforms to set up
# Order determines entity display order in device view
PLATFORMS: list[Platform] = [
    Platform.SELECT,  # Scene dropdowns - show first
    Platform.NUMBER,  # DIY speed controls
    Platform.LIGHT,  # Main light + segments
    Platform.FAN,  # Fan devices
    Platform.HUMIDIFIER,  # Humidifiers / dehumidifiers
    Platform.SWITCH,
    Platform.SENSOR,
    Platform.BINARY_SENSOR,
    Platform.EVENT,  # Leak sensor button presses
    Platform.BUTTON,
]

# Type alias for runtime data
type GoveeConfigEntry = ConfigEntry[GoveeCoordinator]


def _creds_to_dict(creds: GoveeIotCredentials) -> dict[str, Any]:
    """Serialize IoT credentials to a JSON-friendly dict for entry.data storage."""
    return asdict(creds)


def _creds_from_dict(d: Any) -> GoveeIotCredentials | None:
    """Rehydrate IoT credentials from entry.data; returns None if missing/malformed."""
    if not d:
        return None
    if isinstance(d, GoveeIotCredentials):
        # Legacy in-memory shape (pre-v2 hass.data). Pass through.
        return d
    if not isinstance(d, dict):
        return None
    try:
        return GoveeIotCredentials(**d)
    except TypeError:
        _LOGGER.warning("Stored IoT credentials are malformed; ignoring")
        return None


def _persist_iot_credentials(
    hass: HomeAssistant,
    entry: GoveeConfigEntry,
    creds: GoveeIotCredentials | None,
    login_failed_reason: str | None,
) -> None:
    """Write IoT cred state into entry.data (canonical post-v2 storage).

    Either ``creds`` or ``login_failed_reason`` should be set; the other
    field is cleared. Calling with both None clears both.
    """
    new_data = dict(entry.data)
    if creds is not None:
        new_data[KEY_IOT_CREDENTIALS] = _creds_to_dict(creds)
        new_data.pop(KEY_IOT_LOGIN_FAILED, None)
    elif login_failed_reason is not None:
        new_data[KEY_IOT_LOGIN_FAILED] = login_failed_reason
    else:
        new_data.pop(KEY_IOT_CREDENTIALS, None)
        new_data.pop(KEY_IOT_LOGIN_FAILED, None)
    hass.config_entries.async_update_entry(entry, data=new_data)


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
    _LOGGER.info("Setting up Govee integration (entry_id=%s)", entry.entry_id)
    _LOGGER.debug("Entry options: %s", entry.options)

    api_key = entry.data[CONF_API_KEY]

    # Create API client (uses HA-managed clientsession via hass=hass).
    api_client = GoveeApiClient(api_key, hass=hass)

    # Optionally get IoT credentials for MQTT
    # Credentials are cached to avoid repeated login attempts on reload
    iot_credentials: GoveeIotCredentials | None = None
    email = entry.data.get(CONF_EMAIL)
    password = entry.data.get(CONF_PASSWORD)

    if email and password:
        # Read IoT-cred cache and login-failure marker from entry.data (v2 storage).
        cached_creds = _creds_from_dict(entry.data.get(KEY_IOT_CREDENTIALS))
        login_failed = entry.data.get(KEY_IOT_LOGIN_FAILED)

        if cached_creds:
            iot_credentials = cached_creds
            _LOGGER.debug("Using cached MQTT credentials from entry.data")
        elif login_failed:
            _LOGGER.debug(
                "Skipping MQTT login - previous attempt failed: %s. "
                "Reconfigure integration to retry.",
                login_failed,
            )
        else:
            # Attempt fresh login.
            try:
                async with GoveeAuthClient(hass=hass) as auth_client:
                    iot_credentials = await auth_client.login(
                        email,
                        password,
                        client_id=_derive_client_id(email),
                    )
                    _LOGGER.info("MQTT credentials obtained for real-time updates")
                _persist_iot_credentials(hass, entry, iot_credentials, None)

            except Govee2FARequiredError:
                _LOGGER.warning(
                    "Govee account requires email verification (2FA). "
                    "If you do not need real-time MQTT updates, use Reconfigure "
                    "to remove the email and password — the API key alone is "
                    "sufficient for polling. Otherwise, use Reconfigure to "
                    "re-enter credentials with a verification code. "
                    "Continuing with polling-only mode."
                )
                _persist_iot_credentials(hass, entry, None, "2FA verification required")
                ir.async_create_issue(
                    hass,
                    DOMAIN,
                    f"mqtt_2fa_required_{entry.entry_id}",
                    is_fixable=False,
                    severity=ir.IssueSeverity.WARNING,
                    translation_key="mqtt_2fa_required",
                    translation_placeholders={"entry_title": entry.title},
                )
            except GoveeAuthError as err:
                _LOGGER.warning("Failed to get MQTT credentials: %s", err)
                _persist_iot_credentials(hass, entry, None, str(err))
            except Exception as err:
                _LOGGER.warning("MQTT setup failed: %s", err)
                _persist_iot_credentials(hass, entry, None, str(err))

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

    # Discover devices, start MQTT, and perform initial refresh
    # _async_setup() is called automatically by async_config_entry_first_refresh()
    try:
        await coordinator.async_config_entry_first_refresh()
    except ConfigEntryAuthFailed:
        await api_client.close()
        raise
    except Exception as err:
        await api_client.close()
        raise ConfigEntryNotReady(f"Failed to set up Govee: {err}") from err

    # Clean up orphaned entities (e.g., groups that are now disabled)
    await _async_cleanup_orphaned_entities(hass, entry, coordinator)

    # Store coordinator in entry
    entry.runtime_data = coordinator

    # Subscribe to BLE advertisements for nearby Govee devices (transparent
    # local transport enhancement — no user configuration needed).
    for unsub in coordinator.setup_ble_subscriptions():
        entry.async_on_unload(unsub)

    # Set up platforms
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Set up services (only once per HA lifetime; idempotent across reloads).
    if not hass.services.has_service(DOMAIN, SERVICE_REFRESH_SCENES):
        await async_setup_services(hass)

    # Register update listener for options changes
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))

    return True


async def async_migrate_entry(hass: HomeAssistant, entry: GoveeConfigEntry) -> bool:
    """Migrate config entry data between schema versions.

    Schema history:
      v1 → v2: IoT credentials previously cached in hass.data[DOMAIN] move to
               entry.data. Pre-existing v1 installs with cached creds in
               hass.data are migrated transparently. v1 installs without cached
               creds simply re-attempt login on next setup (same as before).

    Returns False on unsupported downgrade so HA blocks the load.
    """
    if entry.version > CONFIG_VERSION:
        _LOGGER.error(
            "Config entry version %d is newer than supported %d (downgrade)",
            entry.version,
            CONFIG_VERSION,
        )
        return False

    if entry.version < 2:
        new_data = dict(entry.data)
        # Defensive: if a prior in-process v1 setup left IoT creds in hass.data,
        # move them into entry.data so the v2 reader path finds them. After a
        # normal HA reload, hass.data is already cleared by async_unload_entry
        # so this branch is a no-op — fresh login will repopulate entry.data.
        domain_data = hass.data.get(DOMAIN, {})
        legacy_creds = (
            domain_data.get(KEY_IOT_CREDENTIALS, {}).get(entry.entry_id)
            if isinstance(domain_data.get(KEY_IOT_CREDENTIALS), dict)
            else None
        )
        if legacy_creds is not None:
            new_data[KEY_IOT_CREDENTIALS] = (
                _creds_to_dict(legacy_creds)
                if isinstance(legacy_creds, GoveeIotCredentials)
                else legacy_creds
            )
            domain_data[KEY_IOT_CREDENTIALS].pop(entry.entry_id, None)
        legacy_fail = (
            domain_data.get(KEY_IOT_LOGIN_FAILED, {}).get(entry.entry_id)
            if isinstance(domain_data.get(KEY_IOT_LOGIN_FAILED), dict)
            else None
        )
        if legacy_fail is not None:
            new_data[KEY_IOT_LOGIN_FAILED] = legacy_fail
            domain_data[KEY_IOT_LOGIN_FAILED].pop(entry.entry_id, None)

        hass.config_entries.async_update_entry(entry, data=new_data, version=2)
        _LOGGER.info("Migrated config entry %s from v1 to v2", entry.entry_id)

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

        # IoT-cred storage moved to entry.data in v2 schema; no per-entry
        # hass.data sub-entries to clean up. Tear down services and clear
        # the domain bucket only when this is the last entry.
        remaining_entries = [
            other
            for other in hass.config_entries.async_entries(DOMAIN)
            if other.entry_id != entry.entry_id
        ]
        if not remaining_entries:
            await async_unload_services(hass)
            hass.data.pop(DOMAIN, None)

    return unload_ok


def _extract_device_id_from_unique_id(
    unique_id: str, known_device_ids: set[str]
) -> str | None:
    """Extract device_id from unique_id using longest prefix match.

    All unique_ids follow: device_id + suffix pattern.
    Device IDs vary in length: MAC (17 chars) or numeric/group (8 chars).
    Use longest-first matching for reliability.

    Args:
        unique_id: Entity unique_id from registry.
        known_device_ids: Set of device IDs from coordinator.

    Returns:
        Device ID if found, None otherwise.
    """
    for device_id in sorted(known_device_ids, key=len, reverse=True):
        if unique_id.startswith(device_id):
            return device_id
    return None


async def _async_cleanup_orphaned_entities(
    hass: HomeAssistant,
    entry: ConfigEntry,
    coordinator: GoveeCoordinator,
) -> None:
    """Remove entity registry entries for devices no longer discovered or features disabled.

    This handles cleanup when:
    - Devices are removed from the Govee account
    - Group devices are disabled via enable_groups option
    - Segment entities are reconfigured or disabled per device
    - Scene entities are disabled via enable_scenes option
    - DIY scene entities are disabled via enable_diy_scenes option
    """
    entity_registry = er.async_get(hass)

    # Get current options
    options = entry.options
    device_modes = options.get("segment_mode_by_device", {})
    enable_scenes = options.get(CONF_ENABLE_SCENES, DEFAULT_ENABLE_SCENES)
    enable_diy_scenes = options.get(CONF_ENABLE_DIY_SCENES, DEFAULT_ENABLE_DIY_SCENES)

    _LOGGER.debug(
        "Orphan cleanup: device_modes=%s, enable_scenes=%s, enable_diy_scenes=%s",
        len(device_modes),
        enable_scenes,
        enable_diy_scenes,
    )

    known_device_ids = set(coordinator.devices.keys())

    # Get all entity entries for this config entry
    all_entities = list(
        er.async_entries_for_config_entry(entity_registry, entry.entry_id)
    )
    _LOGGER.debug(
        "Checking %d entities for cleanup (coordinator has %d devices)",
        len(all_entities),
        len(coordinator.devices),
    )

    entries_to_remove = []
    for entity_entry in all_entities:
        unique_id = entity_entry.unique_id
        if not unique_id:
            continue

        should_remove = False
        removal_reason = ""

        # Extract device_id from unique_id using longest-first matching
        device_id = _extract_device_id_from_unique_id(unique_id, known_device_ids)

        # Check feature toggles first
        if device_id:
            # Get per-device mode (default to individual)
            segment_mode = device_modes.get(device_id, DEFAULT_SEGMENT_MODE)
            suffix = unique_id[len(device_id) :]

            # Use explicit suffix matching to avoid false positives
            if suffix == SUFFIX_GROUPED_SEGMENT:
                if segment_mode != SEGMENT_MODE_GROUPED:
                    should_remove = True
                    removal_reason = "grouped segments disabled"
            elif suffix.startswith(SUFFIX_SEGMENT):
                if segment_mode != SEGMENT_MODE_INDIVIDUAL:
                    should_remove = True
                    removal_reason = "individual segments disabled"
            elif unique_id.endswith(SUFFIX_SCENE_SELECT) and not enable_scenes:
                should_remove = True
                removal_reason = "scenes disabled"
            elif unique_id.endswith(SUFFIX_DIY_SCENE_SELECT) and not enable_diy_scenes:
                should_remove = True
                removal_reason = "DIY scenes disabled"
        else:
            # Device not in coordinator (unknown device)
            should_remove = True
            removal_reason = "device not discovered"

        if should_remove:
            entries_to_remove.append(entity_entry)
            _LOGGER.debug(
                "Marking orphaned entity for removal: %s (unique_id=%s, reason=%s)",
                entity_entry.entity_id,
                entity_entry.unique_id,
                removal_reason,
            )

    # Remove orphaned entries
    for entity_entry in entries_to_remove:
        _LOGGER.info(
            "Removing orphaned entity: %s (unique_id=%s, platform=%s)",
            entity_entry.entity_id,
            entity_entry.unique_id,
            entity_entry.platform,
        )

        # Entity registry removal cascades to the state machine; no manual
        # async_remove() needed (and racing it can drop legitimate updates).
        entity_registry.async_remove(entity_entry.entity_id)

    if entries_to_remove:
        _LOGGER.info("Cleaned up %d orphaned entities", len(entries_to_remove))

    # Clean up orphaned devices (devices with no remaining entities)
    # This ensures immediate removal when all entities for a device are removed
    device_registry = dr.async_get(hass)

    devices_to_remove = []
    for device_entry in dr.async_entries_for_config_entry(
        device_registry, entry.entry_id
    ):
        # Check if device has any remaining entities
        entity_entries = er.async_entries_for_device(
            entity_registry,
            device_entry.id,
            include_disabled_entities=True,
        )

        if not entity_entries:
            devices_to_remove.append(device_entry)
            _LOGGER.debug(
                "Marking orphaned device for removal: %s (no entities remain)",
                device_entry.name or device_entry.id,
            )

    # Remove orphaned devices
    for device_entry in devices_to_remove:
        _LOGGER.info(
            "Removing orphaned device: %s",
            device_entry.name or device_entry.id,
        )
        device_registry.async_remove_device(device_entry.id)

    if devices_to_remove:
        _LOGGER.info("Cleaned up %d orphaned devices", len(devices_to_remove))


async def _async_update_listener(
    hass: HomeAssistant,
    entry: GoveeConfigEntry,
) -> None:
    """Handle options update.

    Reloads the integration when options change — and only then. Home
    Assistant fires update listeners for any ``async_update_entry`` call, so a
    data-only write reaches here too. The integration writes ``entry.data`` at
    runtime to store a refreshed account token (#132); reloading for that would
    tear down every entity, drop the MQTT connection and re-fetch scenes, on a
    cadence set by how often Govee expires a token.
    """
    coordinator = getattr(entry, "runtime_data", None)
    previous = getattr(coordinator, "options_snapshot", None)
    if previous is not None and previous == dict(entry.options):
        _LOGGER.debug("Entry updated without an options change — not reloading")
        return

    _LOGGER.info("Options changed, reloading integration")
    _LOGGER.debug("Current options: %s", entry.options)

    # Log specific option changes for debugging
    enable_groups = entry.options.get(CONF_ENABLE_GROUPS, DEFAULT_ENABLE_GROUPS)
    enable_scenes = entry.options.get(CONF_ENABLE_SCENES, DEFAULT_ENABLE_SCENES)
    enable_diy_scenes = entry.options.get(
        CONF_ENABLE_DIY_SCENES, DEFAULT_ENABLE_DIY_SCENES
    )
    poll_interval = entry.options.get(CONF_POLL_INTERVAL, DEFAULT_POLL_INTERVAL)

    _LOGGER.debug(
        "Options: poll_interval=%s, enable_groups=%s, enable_scenes=%s, "
        "enable_diy_scenes=%s",
        poll_interval,
        enable_groups,
        enable_scenes,
        enable_diy_scenes,
    )

    await hass.config_entries.async_reload(entry.entry_id)
