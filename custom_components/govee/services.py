"""Service actions for the Govee integration.

Provides:
- ``govee.refresh_scenes``: re-fetch the scene catalog for one or all devices.
- ``govee.set_segment_color``: set the colour of individual RGBIC segments.
- ``govee.send_raw_ptreal``: send a raw BLE ptReal frame (developer/debug aid).
- ``govee.request_status``: ask a device for its status over AWS IoT, once or
  repeatedly for a while (a status burst).

Actions are registered once from ``async_setup`` so automations that reference
them validate even while no config entry is loaded (quality-scale rule
``action-setup``), and invalid input raises ``ServiceValidationError`` (rule
``action-exceptions``). ``device_id`` accepts either the Home Assistant device
registry ID (what the device selector produces) or the Govee device ID, so
automations written against the Govee ID keep working.
"""

from __future__ import annotations

import logging

import voluptuous as vol
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv, device_registry as dr
from homeassistant.helpers.service import async_register_admin_service

from .api.ble_packet import calculate_checksum
from .const import DOMAIN
from .coordinator import GoveeCoordinator
from .models import RGBColor, SegmentColorCommand
from .status_burst import STATUS_BURST_MAX_DURATION, STATUS_BURST_MIN_INTERVAL

_LOGGER = logging.getLogger(__name__)

ATTR_DEVICE_ID = "device_id"
ATTR_RGB_COLOR = "rgb_color"
ATTR_SEGMENTS = "segments"
ATTR_FRAME = "frame"
ATTR_DURATION = "duration"
ATTR_INTERVAL = "interval"

SERVICE_REFRESH_SCENES = "refresh_scenes"
SERVICE_SET_SEGMENT_COLOR = "set_segment_color"
SERVICE_SEND_RAW_PTREAL = "send_raw_ptreal"
SERVICE_REQUEST_STATUS = "request_status"

SERVICE_REFRESH_SCENES_SCHEMA = vol.Schema(
    {
        vol.Optional(ATTR_DEVICE_ID): cv.string,
    }
)

SERVICE_SEND_RAW_PTREAL_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_DEVICE_ID): cv.string,
        vol.Required(ATTR_FRAME): cv.string,
    }
)

SERVICE_REQUEST_STATUS_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_DEVICE_ID): cv.string,
        vol.Optional(ATTR_DURATION, default=0): vol.All(
            vol.Coerce(float), vol.Range(min=0, max=STATUS_BURST_MAX_DURATION)
        ),
        vol.Optional(ATTR_INTERVAL, default=5): vol.All(vol.Coerce(float), vol.Range(min=STATUS_BURST_MIN_INTERVAL)),
    }
)

SERVICE_SET_SEGMENT_COLOR_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_DEVICE_ID): cv.string,
        vol.Required(ATTR_SEGMENTS): vol.All(cv.ensure_list, [cv.positive_int]),
        vol.Required(ATTR_RGB_COLOR): vol.All(
            vol.ExactSequence((cv.byte, cv.byte, cv.byte)),
            vol.Coerce(tuple),
        ),
    }
)


def _loaded_coordinators(hass: HomeAssistant) -> list[GoveeCoordinator]:
    """Return the coordinator of every loaded Govee config entry."""
    return [
        entry.runtime_data
        for entry in hass.config_entries.async_entries(DOMAIN)
        if entry.state is ConfigEntryState.LOADED
    ]


def _resolve_device_id(hass: HomeAssistant, raw_id: str) -> str:
    """Map a Home Assistant device registry ID to the Govee device ID.

    Anything that is not a registry ID (a Govee device ID, for instance)
    passes through unchanged.
    """
    device_entry = dr.async_get(hass).async_get(raw_id)
    if device_entry is not None:
        for domain, identifier in device_entry.identifiers:
            if domain == DOMAIN:
                return identifier
    return raw_id


def _get_coordinator_for_device(hass: HomeAssistant, raw_id: str) -> tuple[GoveeCoordinator, str] | None:
    """Return ``(coordinator, govee_device_id)`` for a device, or None if unknown."""
    device_id = _resolve_device_id(hass, raw_id)
    for coordinator in _loaded_coordinators(hass):
        if device_id in coordinator.devices:
            return coordinator, device_id
    return None


def _device_not_found(raw_id: str) -> ServiceValidationError:
    """Error for a ``device_id`` that no loaded entry knows."""
    return ServiceValidationError(
        translation_domain=DOMAIN,
        translation_key="device_not_found",
        translation_placeholders={"device_id": raw_id},
    )


async def async_refresh_scenes_handler(hass: HomeAssistant, call: ServiceCall) -> None:
    """Handle ``govee.refresh_scenes`` for one device or every device."""
    raw_id = call.data.get(ATTR_DEVICE_ID)
    if raw_id:
        found = _get_coordinator_for_device(hass, raw_id)
        if found is None:
            raise _device_not_found(raw_id)
        coordinator, device_id = found
        await coordinator.async_get_scenes(device_id, refresh=True)
        _LOGGER.debug("Refreshed scenes for device %s", device_id)
        return

    coordinators = _loaded_coordinators(hass)
    if not coordinators:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="not_loaded",
        )
    for coordinator in coordinators:
        for dev_id, device in coordinator.devices.items():
            if device.supports_scenes:
                await coordinator.async_get_scenes(dev_id, refresh=True)
    _LOGGER.debug("Refreshed scenes for all devices")


async def async_set_segment_color_handler(hass: HomeAssistant, call: ServiceCall) -> None:
    """Handle ``govee.set_segment_color``.

    Rejects any segment index outside the device's effective
    ``segment_count`` (which already factors in ``SKU_SEGMENT_OVERRIDES`` for
    SKUs like the H7075 that the API over-reports) with a
    ``ServiceValidationError``, so the caller learns why nothing happened
    instead of the cloud silently refusing the command.
    """
    raw_id = call.data[ATTR_DEVICE_ID]
    segments: list[int] = call.data[ATTR_SEGMENTS]
    rgb = call.data[ATTR_RGB_COLOR]

    found = _get_coordinator_for_device(hass, raw_id)
    if found is None:
        raise _device_not_found(raw_id)
    coordinator, device_id = found

    device = coordinator.devices.get(device_id)
    device_name = device.name if device is not None else device_id
    if device is not None:
        count = device.segment_count
        bad = [index for index in segments if index >= count]
        if bad:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="segment_out_of_range",
                translation_placeholders={
                    "device": device_name,
                    "indices": ", ".join(str(index) for index in bad),
                    "count": str(count),
                },
            )

    command = SegmentColorCommand(
        segment_indices=tuple(segments),
        color=RGBColor(r=rgb[0], g=rgb[1], b=rgb[2]),
    )
    if not await coordinator.async_control_device(device_id, command):
        raise HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key="command_failed",
            translation_placeholders={"device": device_name},
        )
    _LOGGER.debug("Set segments %s to color %s on device %s", segments, rgb, device_id)


async def async_send_raw_ptreal_handler(hass: HomeAssistant, call: ServiceCall) -> None:
    """Handle ``govee.send_raw_ptreal`` (developer/debug aid for issue #208).

    Sends an arbitrary BLE ptReal command frame to a device over the AWS IoT
    passthrough. Frames of 19 bytes or fewer get a checksum appended by the
    coordinator; a 20-byte frame must already carry a valid XOR checksum.
    """
    raw_id = call.data[ATTR_DEVICE_ID]
    raw_frame = call.data[ATTR_FRAME]

    cleaned = raw_frame.replace(" ", "").replace(":", "")
    try:
        frame = bytes.fromhex(cleaned)
    except ValueError:
        frame = b""

    if not cleaned or len(cleaned) % 2 != 0 or not frame or len(frame) > 20:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="invalid_ptreal_frame",
            translation_placeholders={"frame": raw_frame},
        )

    if len(frame) == 20 and calculate_checksum(list(frame[:19])) != frame[19]:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="invalid_ptreal_frame",
            translation_placeholders={"frame": raw_frame},
        )

    found = _get_coordinator_for_device(hass, raw_id)
    if found is None:
        raise _device_not_found(raw_id)
    coordinator, device_id = found

    device = coordinator.devices.get(device_id)
    device_name = device.name if device is not None else device_id

    if not await coordinator.async_send_raw_ptreal(device_id, frame):
        raise HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key="ptreal_unavailable",
            translation_placeholders={"device": device_name},
        )
    _LOGGER.debug("Sent raw ptReal frame %s to device %s", frame.hex(), device_id)


async def async_request_status_handler(hass: HomeAssistant, call: ServiceCall) -> None:
    """Handle ``govee.request_status``.

    Sends an AWS IoT status query now, and with a ``duration`` keeps sending
    one every ``interval`` seconds until it has passed. The device's pushes
    are applied as usual. MQTT only: no cloud requests are spent.
    """
    found = _get_coordinator_for_device(hass, call.data[ATTR_DEVICE_ID])
    if found is None:
        raise _device_not_found(call.data[ATTR_DEVICE_ID])
    coordinator, device_id = found
    if not coordinator.status_query_possible(device_id):
        device = coordinator.devices.get(device_id)
        raise HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key="status_query_unavailable",
            translation_placeholders={"device": device.name if device is not None else device_id},
        )
    duration = call.data[ATTR_DURATION]
    if duration > 0:
        coordinator.status_burst.start(device_id, duration, call.data[ATTR_INTERVAL])
    else:
        await coordinator.async_request_status(device_id)


@callback
def async_setup_services(hass: HomeAssistant) -> None:
    """Register the Govee service actions (called once from ``async_setup``)."""

    async def _refresh_scenes(call: ServiceCall) -> None:
        await async_refresh_scenes_handler(hass, call)

    async def _set_segment_color(call: ServiceCall) -> None:
        await async_set_segment_color_handler(hass, call)

    async def _send_raw_ptreal(call: ServiceCall) -> None:
        await async_send_raw_ptreal_handler(hass, call)

    async def _request_status(call: ServiceCall) -> None:
        await async_request_status_handler(hass, call)

    hass.services.async_register(
        DOMAIN,
        SERVICE_REQUEST_STATUS,
        _request_status,
        schema=SERVICE_REQUEST_STATUS_SCHEMA,
    )

    hass.services.async_register(
        DOMAIN,
        SERVICE_REFRESH_SCENES,
        _refresh_scenes,
        schema=SERVICE_REFRESH_SCENES_SCHEMA,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_SET_SEGMENT_COLOR,
        _set_segment_color,
        schema=SERVICE_SET_SEGMENT_COLOR_SCHEMA,
    )
    # Admin-only: it sends arbitrary frames to the device (#208 debug aid).
    async_register_admin_service(
        hass,
        DOMAIN,
        SERVICE_SEND_RAW_PTREAL,
        _send_raw_ptreal,
        schema=SERVICE_SEND_RAW_PTREAL_SCHEMA,
    )
