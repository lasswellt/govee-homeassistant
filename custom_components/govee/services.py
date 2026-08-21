"""Custom services for Govee integration.

Provides services for:
- Refreshing the scene catalog (``govee.refresh_scenes``)
- Setting per-segment colors on RGBIC devices (``govee.set_segment_color``)
"""

from __future__ import annotations

import logging

import voluptuous as vol
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers import config_validation as cv

from .const import DOMAIN
from .coordinator import GoveeCoordinator
from .models import RGBColor, SegmentColorCommand

_LOGGER = logging.getLogger(__name__)

# Service names
SERVICE_REFRESH_SCENES = "refresh_scenes"
SERVICE_SET_SEGMENT_COLOR = "set_segment_color"

# Service schemas
SERVICE_REFRESH_SCENES_SCHEMA = vol.Schema(
    {
        vol.Optional("device_id"): cv.string,
    }
)

SERVICE_SET_SEGMENT_COLOR_SCHEMA = vol.Schema(
    {
        vol.Required("device_id"): cv.string,
        vol.Required("segments"): vol.All(cv.ensure_list, [cv.positive_int]),
        vol.Required("rgb_color"): vol.All(
            vol.ExactSequence((cv.byte, cv.byte, cv.byte)),
            vol.Coerce(tuple),
        ),
    }
)


async def async_set_segment_color_handler(
    hass: HomeAssistant, call: ServiceCall
) -> None:
    """Handle ``govee.set_segment_color`` — module-level, testable.

    Rejects any segment index outside the device's effective
    ``segment_count`` (which already factors in ``SKU_SEGMENT_OVERRIDES``
    for SKUs like H7075 that the API over-reports). On rejection the
    service logs a warning and returns without dispatching — this keeps
    the HA service call visible in the log instead of silently dropping
    a command that the cloud would refuse anyway.
    """
    device_id = call.data["device_id"]
    segments = call.data["segments"]
    rgb = call.data["rgb_color"]

    coordinator = _get_coordinator_for_device(hass, device_id)
    if not coordinator:
        _LOGGER.error("Device %s not found", device_id)
        return

    device = coordinator.devices.get(device_id)
    if device is not None:
        n = device.segment_count
        bad = [i for i in segments if i >= n]
        if bad:
            _LOGGER.warning(
                "Rejecting set_segment_color for device %s: indices %s are out "
                "of range (segment_count=%s)",
                device_id,
                bad,
                n,
            )
            return

    color = RGBColor(r=rgb[0], g=rgb[1], b=rgb[2])
    command = SegmentColorCommand(
        segment_indices=tuple(segments),
        color=color,
    )

    await coordinator.async_control_device(device_id, command)
    _LOGGER.info(
        "Set segments %s to color %s on device %s",
        segments,
        rgb,
        device_id,
    )


async def async_setup_services(hass: HomeAssistant) -> None:
    """Set up Govee services."""

    async def async_refresh_scenes(call: ServiceCall) -> None:
        """Refresh scenes for device(s)."""
        device_id = call.data.get("device_id")

        # Get all coordinators
        coordinators = _get_coordinators(hass)

        for coordinator in coordinators:
            if device_id:
                # Refresh specific device
                if device_id in coordinator.devices:
                    await coordinator.async_get_scenes(device_id, refresh=True)
                    _LOGGER.info("Refreshed scenes for device %s", device_id)
            else:
                # Refresh all devices
                for dev_id, device in coordinator.devices.items():
                    if device.supports_scenes:
                        await coordinator.async_get_scenes(dev_id, refresh=True)
                _LOGGER.info("Refreshed scenes for all devices")

    async def _set_segment_color_bound(call: ServiceCall) -> None:
        """Bind ``hass`` for the registered service handler."""
        await async_set_segment_color_handler(hass, call)

    # Register services
    hass.services.async_register(
        DOMAIN,
        SERVICE_REFRESH_SCENES,
        async_refresh_scenes,
        schema=SERVICE_REFRESH_SCENES_SCHEMA,
    )

    hass.services.async_register(
        DOMAIN,
        SERVICE_SET_SEGMENT_COLOR,
        _set_segment_color_bound,
        schema=SERVICE_SET_SEGMENT_COLOR_SCHEMA,
    )

    _LOGGER.debug("Govee services registered")


async def async_unload_services(hass: HomeAssistant) -> None:
    """Unload Govee services."""
    hass.services.async_remove(DOMAIN, SERVICE_REFRESH_SCENES)
    hass.services.async_remove(DOMAIN, SERVICE_SET_SEGMENT_COLOR)
    _LOGGER.debug("Govee services unloaded")


def _get_coordinators(hass: HomeAssistant) -> list[GoveeCoordinator]:
    """Get all Govee coordinators."""
    coordinators = []
    for entry in hass.config_entries.async_entries(DOMAIN):
        if hasattr(entry, "runtime_data") and isinstance(
            entry.runtime_data, GoveeCoordinator
        ):
            coordinators.append(entry.runtime_data)
    return coordinators


def _get_coordinator_for_device(
    hass: HomeAssistant,
    device_id: str,
) -> GoveeCoordinator | None:
    """Get coordinator that manages a specific device."""
    for coordinator in _get_coordinators(hass):
        if device_id in coordinator.devices:
            return coordinator
    return None
