"""Govee integration for Home Assistant.

Controls Govee lights, LED strips, and smart devices via the Govee Cloud API.
Supports real-time state updates via AWS IoT MQTT.
"""

from __future__ import annotations

import logging
from dataclasses import asdict
from typing import Any

from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers import (
    config_validation as cv,
    device_registry as dr,
    entity_registry as er,
    issue_registry as ir,
)
from homeassistant.helpers.device_registry import DeviceEntry
from homeassistant.helpers.typing import ConfigType

from .api import (
    Govee2FARequiredError,
    GoveeApiClient,
    GoveeApiError,
    GoveeAuthError,
    GoveeIotCredentials,
)
from .api.auth import GoveeAuthClient, _derive_client_id
from .const import (
    CONF_API_KEY,
    CONF_EMAIL,
    CONF_ENABLE_DIY_SCENES,
    CONF_ENABLE_GROUPS,
    CONF_ENABLE_SCENES,
    CONF_PASSWORD,
    CONF_POLL_INTERVAL,
    CONF_SEGMENT_MODE_BY_DEVICE,
    CONFIG_VERSION,
    DEFAULT_ENABLE_DIY_SCENES,
    DEFAULT_ENABLE_GROUPS,
    DEFAULT_ENABLE_SCENES,
    DEFAULT_POLL_INTERVAL,
    DEFAULT_SEGMENT_MODE,
    DOMAIN,
    HUB_DEVICE_IDENTIFIER,
    KEY_IOT_CREDENTIALS,
    KEY_IOT_LOGIN_FAILED,
    SEGMENT_MODE_BOTH,
    SEGMENT_MODE_GROUPED,
    SEGMENT_MODE_INDIVIDUAL,
    SUFFIX_DIY_SCENE_SELECT,
    SUFFIX_DIY_STYLE_SELECT,
    SUFFIX_GROUPED_SEGMENT,
    SUFFIX_SCENE_SELECT,
    SUFFIX_SEGMENT,
)
from .coordinator import GoveeConfigEntry, GoveeCoordinator
from .repairs import async_cleanup_legacy_issues, async_create_mqtt_issue
from .services import async_setup_services

__all__ = ["GoveeConfigEntry"]

_LOGGER = logging.getLogger(__name__)

# Configuration is UI-only; reject any YAML under the ``govee:`` key.
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

# Platforms to set up
# Order determines entity display order in device view
PLATFORMS: list[Platform] = [
    Platform.SELECT,  # Scene dropdowns - show first
    Platform.NUMBER,  # Music sensitivity, heater target, probe alarm limits
    Platform.LIGHT,  # Main light + segments
    Platform.FAN,  # Fan devices
    Platform.HUMIDIFIER,  # Humidifiers / dehumidifiers
    Platform.WATER_HEATER,  # H7175 kettles
    Platform.SWITCH,
    Platform.SENSOR,
    Platform.BINARY_SENSOR,
    Platform.EVENT,  # Leak sensor button presses
    Platform.BUTTON,
]


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register the integration's service actions.

    Actions are registered here rather than per config entry so automations
    that reference them validate even while no entry is loaded (quality-scale
    rule ``action-setup``). Each action checks for a loaded entry when called.
    """
    async_setup_services(hass)
    return True


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
    _LOGGER.debug("Setting up entry %s with options %s", entry.entry_id, entry.options)

    async_cleanup_legacy_issues(hass, entry)

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
                "Skipping MQTT login - previous attempt failed: %s. " "Reconfigure integration to retry.",
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
                    _LOGGER.debug("MQTT credentials obtained for real-time updates")
                _persist_iot_credentials(hass, entry, iot_credentials, None)

            except Govee2FARequiredError:
                _LOGGER.warning(
                    "Govee account requires email verification (2FA). "
                    "If you do not need real-time MQTT updates, use Reconfigure "
                    "to remove the email and password; the API key alone is "
                    "sufficient for polling. Otherwise, use Reconfigure to "
                    "re-enter credentials with a verification code. "
                    "Continuing with polling-only mode"
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
                # Without this the install looks identical to one that never
                # configured account login: no push, no entity, no issue.
                async_create_mqtt_issue(hass, entry, f"account sign-in failed: {err}")
            except Exception as err:  # noqa: BLE001 - account login must never block setup
                _LOGGER.warning("MQTT setup failed: %s", err)
                _persist_iot_credentials(hass, entry, None, str(err))
                async_create_mqtt_issue(hass, entry, f"account sign-in failed: {err}")

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
    except (ConfigEntryAuthFailed, ConfigEntryNotReady):
        await api_client.close()
        raise
    except (GoveeApiError, TimeoutError, OSError) as err:
        await api_client.close()
        raise ConfigEntryNotReady(f"Failed to set up Govee: {err}") from err
    except Exception:
        # Anything else is a bug rather than a transient condition. Let Home
        # Assistant surface it as a setup error instead of retrying forever.
        await api_client.close()
        raise

    # Store coordinator in entry
    entry.runtime_data = coordinator

    # Clean up orphaned entities (e.g., groups that are now disabled)
    await _async_cleanup_orphaned_entities(hass, entry, coordinator)

    # Subscribe to BLE advertisements for nearby Govee devices (transparent
    # local transport enhancement — no user configuration needed).
    for unsub in coordinator.setup_ble_subscriptions():
        entry.async_on_unload(unsub)

    # Set up platforms
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

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
                _creds_to_dict(legacy_creds) if isinstance(legacy_creds, GoveeIotCredentials) else legacy_creds
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
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        await entry.runtime_data.async_shutdown()
    return unload_ok


async def async_remove_config_entry_device(
    hass: HomeAssistant, entry: GoveeConfigEntry, device_entry: DeviceEntry
) -> bool:
    """Allow the user to delete a device the account no longer reports.

    Devices still present in the coordinator (including gateway hubs) are
    protected; anything else can be removed from the device page. This is the
    manual path the ``stale-devices`` rule asks for when automatic removal
    cannot be certain a device is gone.
    """
    owned = _owned_device_identifiers(entry, entry.runtime_data)
    return not any(domain == DOMAIN and identifier in owned for domain, identifier in device_entry.identifiers)


def _extract_device_id_from_unique_id(unique_id: str, known_device_ids: set[str]) -> str | None:
    """Extract the owning ID from a unique_id using longest prefix match.

    All unique_ids follow: owner_id + suffix pattern, where the owner is a
    device ID (MAC or numeric group ID), a leak-sensor or hub ID, or the
    config entry ID for hub-level diagnostics. Longest-first matching keeps a
    short numeric group ID from claiming a longer ID that merely starts with
    the same digits.

    Args:
        unique_id: Entity unique_id from registry.
        known_device_ids: Set of owner IDs this entry currently manages.

    Returns:
        Owner ID if found, None otherwise.
    """
    for device_id in sorted(known_device_ids, key=len, reverse=True):
        if unique_id.startswith(device_id):
            return device_id
    return None


def _owned_device_identifiers(entry: GoveeConfigEntry, coordinator: GoveeCoordinator) -> set[str]:
    """Every ``(DOMAIN, id)`` identifier value this entry currently owns.

    Covers regular and BFF-synthesised devices, hub-attached leak sensors,
    their gateway hubs, and the integration-level diagnostics device.
    """
    owned = set(coordinator.devices)
    owned.update(coordinator.leak_sensors)
    owned.update(coordinator.hub_device_ids)
    owned.add(HUB_DEVICE_IDENTIFIER)
    return owned


async def _async_cleanup_orphaned_entities(
    hass: HomeAssistant,
    entry: GoveeConfigEntry,
    coordinator: GoveeCoordinator,
) -> None:
    """Remove entity registry entries for devices no longer discovered or features disabled.

    This handles cleanup when:
    - Devices are removed from the Govee account
    - Group devices are disabled via enable_groups option
    - Segment entities are reconfigured or disabled per device
    - Scene entities are disabled via enable_scenes option
    - DIY scene entities are disabled via enable_diy_scenes option

    Entities that belong to hub-attached leak sensors, gateway hubs, or the
    integration-level diagnostics device are never treated as orphans: they
    are not keyed by ``coordinator.devices`` but are just as live. Removal of
    unknown devices is skipped entirely when a startup discovery step failed
    (so a BFF timeout cannot delete every leak sensor) or when discovery came
    back empty (so an API glitch cannot delete everything).
    """
    entity_registry = er.async_get(hass)
    device_registry = dr.async_get(hass)

    # Get current options
    options = entry.options
    device_modes = options.get(CONF_SEGMENT_MODE_BY_DEVICE, {})
    enable_scenes = options.get(CONF_ENABLE_SCENES, DEFAULT_ENABLE_SCENES)
    enable_diy_scenes = options.get(CONF_ENABLE_DIY_SCENES, DEFAULT_ENABLE_DIY_SCENES)

    _LOGGER.debug(
        "Orphan cleanup: device_modes=%s, enable_scenes=%s, enable_diy_scenes=%s",
        len(device_modes),
        enable_scenes,
        enable_diy_scenes,
    )

    known_device_ids = set(coordinator.devices)
    owned_ids = _owned_device_identifiers(entry, coordinator) | {entry.entry_id}

    # Get all entity entries for this config entry
    all_entities = list(er.async_entries_for_config_entry(entity_registry, entry.entry_id))
    _LOGGER.debug(
        "Checking %d entities for cleanup (coordinator has %d devices)",
        len(all_entities),
        len(coordinator.devices),
    )

    if not known_device_ids and all_entities:
        _LOGGER.warning(
            "Device discovery returned no devices; keeping the %d existing "
            "entities rather than treating them as removed",
            len(all_entities),
        )
        return

    remove_unknown = not coordinator.discovery_incomplete
    if not remove_unknown:
        _LOGGER.debug(
            "A startup discovery step failed; entities of undiscovered devices "
            "are kept until the next successful setup"
        )

    entries_to_remove = []
    for entity_entry in all_entities:
        unique_id = entity_entry.unique_id
        if not unique_id:
            continue

        should_remove = False
        removal_reason = ""

        owner_id = _extract_device_id_from_unique_id(unique_id, owned_ids)

        if owner_id is None:
            # Nothing this entry manages owns the entity.
            if remove_unknown:
                should_remove = True
                removal_reason = "device not discovered"
        elif owner_id in known_device_ids:
            # Feature toggles only apply to regular devices; leak sensors, hubs
            # and the diagnostics device have no per-device options.
            segment_mode = device_modes.get(owner_id, DEFAULT_SEGMENT_MODE)
            suffix = unique_id[len(owner_id) :]

            # Use explicit suffix matching to avoid false positives
            if suffix == SUFFIX_GROUPED_SEGMENT:
                if segment_mode not in (SEGMENT_MODE_GROUPED, SEGMENT_MODE_BOTH):
                    should_remove = True
                    removal_reason = "grouped segments disabled"
            elif suffix.startswith(SUFFIX_SEGMENT):
                if segment_mode not in (SEGMENT_MODE_INDIVIDUAL, SEGMENT_MODE_BOTH):
                    should_remove = True
                    removal_reason = "individual segments disabled"
            elif suffix == SUFFIX_SCENE_SELECT and not enable_scenes:
                should_remove = True
                removal_reason = "scenes disabled"
            elif suffix == SUFFIX_DIY_SCENE_SELECT and not enable_diy_scenes:
                should_remove = True
                removal_reason = "DIY scenes disabled"
            elif suffix == SUFFIX_DIY_STYLE_SELECT:
                # The DIY style selector never sent a command; it was removed.
                should_remove = True
                removal_reason = "DIY style selector removed"

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

    # Clean up orphaned devices: registry devices this entry no longer owns
    # and that have no entities left. Owned devices are kept even without
    # entities, because hubs are registered before their first entity exists.
    owned_identifiers = _owned_device_identifiers(entry, coordinator)
    devices_to_remove = []
    for device_entry in dr.async_entries_for_config_entry(device_registry, entry.entry_id):
        if any(
            domain == DOMAIN and identifier in owned_identifiers for domain, identifier in device_entry.identifiers
        ):
            continue
        if not remove_unknown:
            continue

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

    Reloads the integration when options change, and only then. Home
    Assistant fires update listeners for any ``async_update_entry`` call, so a
    data-only write reaches here too. The integration writes ``entry.data`` at
    runtime to store a refreshed account token (#132); reloading for that would
    tear down every entity, drop the MQTT connection and re-fetch scenes, on a
    cadence set by how often Govee expires a token.
    """
    coordinator = getattr(entry, "runtime_data", None)
    previous = getattr(coordinator, "options_snapshot", None)
    if previous is not None and previous == dict(entry.options):
        _LOGGER.debug("Entry updated without an options change; not reloading")
        return

    _LOGGER.debug("Options changed to %s, reloading entry", entry.options)
    await hass.config_entries.async_reload(entry.entry_id)
