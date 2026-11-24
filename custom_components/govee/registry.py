"""Device-registry lookups that work across the supported Home Assistant versions."""

from __future__ import annotations

import dataclasses
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr


def get_registry_device(
    registry: dr.DeviceRegistry, identifier: tuple[str, str], config_entry_id: str
) -> dr.DeviceEntry | None:
    """Return the config entry's device with this identifier, if any.

    Home Assistant 2026.10 deprecates ``DeviceRegistry.async_get_device``
    (removed in 2027.8; it raises in tests) in favour of
    ``async_get_device_by_identifier``, which older versions lack. The older
    lookup does not filter by entry, so its result is checked here instead.
    """
    by_identifier = getattr(registry, "async_get_device_by_identifier", None)
    if by_identifier is not None:
        device: dr.DeviceEntry | None = by_identifier(identifier, config_entry_id)
        return device
    legacy = registry.async_get_device(identifiers={identifier})  # pre-2026.10 path of get_registry_device
    return legacy if legacy is not None and config_entry_id in device_config_entry_ids(legacy) else None


def device_config_entry_ids(device: dr.DeviceEntry) -> set[str]:
    """Return the config entries a device belongs to.

    Home Assistant 2026.10 gives every device a single ``config_entry_id`` and
    deprecates the ``config_entries`` set that older versions use.
    """
    entry_id: str | None = getattr(device, "config_entry_id", None)
    if entry_id is not None:
        return {entry_id}
    return set(device.config_entries)


def supports_via_device_id() -> bool:
    """Return whether this Home Assistant's ``DeviceInfo`` accepts ``via_device_id``.

    Home Assistant 2026.11 turns ``DeviceInfo`` into a dataclass with a
    ``via_device_id`` field and deprecates ``via_device`` (removed in 2027.8).
    Older versions use a TypedDict that rejects the new key.
    """
    info: Any = dr.DeviceInfo
    return dataclasses.is_dataclass(info) and "via_device_id" in {field.name for field in dataclasses.fields(info)}


def hub_link_info(hass: HomeAssistant, hub_identifier: tuple[str, str], config_entry_id: str) -> dict[str, Any]:
    """Return the ``DeviceInfo`` keys that link a child device to its hub.

    Newer Home Assistant versions need the hub's registry id (``via_device_id``),
    which only exists once the hub is registered, so an unregistered hub yields no
    link. Older versions take the hub identifier as ``via_device``.

    Args:
        hass: Home Assistant instance.
        hub_identifier: The hub's device identifier, e.g. ``(DOMAIN, hub_id)``.
        config_entry_id: The config entry that owns the hub device.

    Returns:
        Keys to merge into ``DeviceInfo``; empty when the hub is not registered yet.
    """
    if not supports_via_device_id():
        return {"via_device": hub_identifier}
    hub = get_registry_device(dr.async_get(hass), hub_identifier, config_entry_id)
    return {"via_device_id": hub.id} if hub is not None else {}
