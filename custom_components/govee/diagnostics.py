"""Diagnostics support for Govee integration."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_API_KEY
from homeassistant.core import HomeAssistant

from .models import GoveeConfigEntry

TO_REDACT = {CONF_API_KEY}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: GoveeConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry.

    This provides comprehensive diagnostic information for troubleshooting
    the Govee integration, including:
    - Config entry metadata (with sensitive data redacted)
    - Device inventory with capability summary
    - Coordinator health status
    - Rate limit information
    - Scene cache statistics

    Args:
        hass: Home Assistant instance
        entry: Config entry for this integration

    Returns:
        Dictionary containing diagnostic data with sensitive information redacted
    """
    coordinator = entry.runtime_data.coordinator

    return {
        "entry": {
            "title": entry.title,
            "data": async_redact_data(entry.data, TO_REDACT),
            "options": dict(entry.options),
        },
        "devices": {
            device_id: {
                "sku": device.sku,
                "name": device.device_name,
                "type": device.device_type,
                "firmware_version": device.firmware_version,
                "capabilities_count": len(device.capabilities),
                "supports": {
                    "on_off": device.supports_on_off,
                    "brightness": device.supports_brightness,
                    "color": device.supports_color,
                    "color_temp": device.supports_color_temp,
                    "scenes": device.supports_scenes,
                    "diy_scenes": device.supports_diy_scenes,
                    "segments": device.supports_segments,
                    "music_mode": device.supports_music_mode,
                    "nightlight": device.supports_nightlight,
                },
                "ranges": {
                    "brightness": device.get_brightness_range(),
                    "color_temp": device.get_color_temp_range(),
                },
            }
            for device_id, device in coordinator.devices.items()
        },
        "coordinator": {
            "last_update_success": coordinator.last_update_success,
            "device_count": len(coordinator.devices),
            "scene_cache_size": len(coordinator._scene_cache),
            "diy_scene_cache_size": len(coordinator._diy_scene_cache),
        },
        "rate_limits": {
            "remaining_minute": coordinator.rate_limit_remaining_minute,
            "remaining_day": coordinator.rate_limit_remaining,
        },
    }
