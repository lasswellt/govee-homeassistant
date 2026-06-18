"""Diagnostics support for Govee integration.

Provides debug information for troubleshooting without exposing sensitive data.
Implements both config-entry diagnostics (the whole integration) and per-device
diagnostics (a single device, via the device's ⋮ menu → Download diagnostics).
"""

from __future__ import annotations

import dataclasses
import hashlib
import re
from datetime import datetime
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceEntry

from .api.lan import async_scan_lan_devices
from .const import CONF_API_KEY, CONF_EMAIL, CONF_PASSWORD, DOMAIN
from .coordinator import GoveeCoordinator
from .models.transport import TRANSPORT_KINDS

# Keys to redact from diagnostic output. Includes the raw-response identity
# fields ("device" = MAC in /device/state + device-list, "deviceName" = the
# user's chosen name, "hub_device_id" = the LoRa hub MAC) so the captured raw
# API/MQTT payloads and leak-sensor records stay PII-free.
TO_REDACT = {
    CONF_API_KEY,
    CONF_EMAIL,
    CONF_PASSWORD,
    "token",
    "refresh_token",
    "iot_cert",
    "iot_key",
    "iot_ca",
    "client_id",
    "account_topic",
    "device_id",
    "device",
    "deviceName",
    "hub_device_id",
    "mac",
    # Local network address from the LAN-discovery scan (#57) — a private IP is
    # still PII in a publicly-attached diagnostics download.
    "ip",
}

# Govee device IDs are MAC-derived: 8 colon-separated hex octets
# (e.g., "03:9C:DC:06:75:4B:10:7C"). Group device IDs are numeric-only.
_MAC_PATTERN = re.compile(r"^[0-9A-Fa-f]{2}(:[0-9A-Fa-f]{2}){5,7}$")


def _looks_like_mac(value: str) -> bool:
    """Return True if value matches the Govee MAC-derived device-id format."""
    return bool(_MAC_PATTERN.match(value))


def _anonymize_device_id(value: str) -> str:
    """Replace a MAC-derived id with a stable short hash (PII redaction)."""
    return f"device_{hashlib.sha256(value.encode()).hexdigest()[:8]}"


def _anon_id(value: str) -> str:
    """Anonymize a MAC-format id; leave non-MAC ids (e.g. group numerics) intact."""
    return _anonymize_device_id(value) if _looks_like_mac(value) else value


def _anonymize_device_keys(data: dict[str, Any]) -> dict[str, Any]:
    """Replace MAC-format dict keys with stable hashes; leave other keys intact."""
    return {
        (_anonymize_device_id(k) if isinstance(k, str) and _looks_like_mac(k) else k): v
        for k, v in data.items()
    }


def _serialize_state(state: Any) -> dict[str, Any] | None:
    """Full dump of a GoveeDeviceState (all fields, incl. sensor readings).

    Never raises — diagnostics must always produce output.
    """
    if state is None:
        return None
    try:
        return dataclasses.asdict(state)
    except Exception:  # pragma: no cover - defensive
        return {
            "online": getattr(state, "online", None),
            "power_state": getattr(state, "power_state", None),
            "source": getattr(state, "source", None),
        }


def _transport_health(coordinator: GoveeCoordinator, device_id: str) -> dict[str, Any]:
    """Per-transport connectivity health for a device (timestamps as ISO)."""
    out: dict[str, Any] = {}
    for kind in TRANSPORT_KINDS:
        health = coordinator.get_transport_health(device_id, kind)
        if health is None:
            continue
        out[kind] = {
            "is_available": health.is_available,
            "last_received": _iso(health.last_success_ts),
            "last_sent": _iso(health.last_send_ts),
            # Deprecated alias of last_received (pre-directional key); drop next release.
            "last_success": _iso(health.last_success_ts),
            "last_failure": _iso(health.last_failure_ts),
            "last_failure_reason": health.last_failure_reason,
        }
    return out


def _iso(value: datetime | None) -> str | None:
    """ISO-format a datetime, or None."""
    return value.isoformat() if value else None


def _device_diag(
    coordinator: GoveeCoordinator,
    device_id: str,
    device: Any,
    raw_state: dict[str, Any],
    raw_mqtt: dict[str, Any],
) -> dict[str, Any]:
    """Build the diagnostic record for a single device.

    Carries parsed state (ALL fields, incl. sensor_temperature/humidity), the
    verbatim /device/state payload it parsed from, the last AWS IoT push, and
    per-transport health — enough to debug state-shape issues (e.g. #83) from a
    download alone.
    """
    return {
        "sku": device.sku,
        "name": device.name,
        "device_type": device.device_type,
        "is_group": device.is_group,
        "capabilities": [
            {
                "type": cap.type,
                "instance": cap.instance,
                "parameters": cap.parameters,
            }
            for cap in device.capabilities
        ],
        "state": _serialize_state(coordinator.get_state(device_id)),
        "raw_api_state": raw_state.get(device_id),
        "last_mqtt_message": raw_mqtt.get(device_id),
        "transport": {
            "cloud_api": True,
            "mqtt": coordinator.mqtt_connected,
            "ble": coordinator.is_ble_available(device_id),
        },
        "transport_health": _transport_health(coordinator, device_id),
    }


def _leak_diag(coordinator: GoveeCoordinator) -> dict[str, Any]:
    """Dump discovered leak sensors and their live state (keyed by device_id)."""
    states = coordinator.leak_states
    out: dict[str, Any] = {}
    for device_id, sensor in coordinator.leak_sensors.items():
        state = states.get(device_id)
        out[device_id] = {
            "sensor": dataclasses.asdict(sensor),
            "state": dataclasses.asdict(state) if state else None,
        }
    return out


def _runtime_diag(coordinator: GoveeCoordinator) -> dict[str, Any]:
    """Integration-wide runtime signals shared by entry + device diagnostics."""
    mqtt_client = coordinator.mqtt_client
    mqtt_info: dict[str, Any] | None = None
    recent_multisync: list[dict[str, Any]] = []
    if mqtt_client:
        mqtt_info = {
            "available": mqtt_client.available,
            "connected": mqtt_client.connected,
            "tracked_devices": len(mqtt_client.last_messages),
        }
        # Recent hub multiSync packets (hex) — lets undecoded leak-sensor
        # packet subtypes be reverse-engineered from a download alone (#87).
        recent_multisync = mqtt_client.recent_multisync
    return {
        "mqtt": mqtt_info,
        "recent_multisync": recent_multisync,
        # PII-free census of the BFF device list — shows whether the BFF API
        # returns a given leak SKU and if it carries discovery fields (#87).
        "bff_device_census": coordinator.bff_device_census,
        # PII-free shape of the raw BFF response — distinguishes "absent" from
        # "present under an unexpected path/shape" when the census is empty.
        "bff_response_skeleton": coordinator.bff_response_skeleton,
        "has_iot_credentials": coordinator.has_iot_credentials,
        "device_topic_count": coordinator.device_topic_count,
        "api": {
            "rate_limit_remaining": coordinator.api_rate_limit_remaining,
            "rate_limit_total": coordinator.api_rate_limit_total,
            "rate_limit_reset": coordinator.api_rate_limit_reset,
        },
        "scene_cache_count": coordinator.scene_cache_count,
        "diy_scene_cache_count": coordinator.diy_scene_cache_count,
    }


async def _lan_discovery_diag() -> dict[str, Any]:
    """Run one read-only LAN scan for the diagnostics download (issue #57).

    Captures which of the user's devices answer Govee's local UDP discovery and
    what they report, so the community can supply the data the full LAN feature
    needs. Never raises — diagnostics must always produce output; the IP of each
    responder is redacted by the shared ``_redact`` pass.
    """
    try:
        devices = await async_scan_lan_devices()
        return {
            "scan_attempted": True,
            "device_count": len(devices),
            "devices": devices,
            "error": None,
        }
    except Exception as err:  # never break the diagnostics download
        return {
            "scan_attempted": True,
            "device_count": 0,
            "devices": [],
            "error": str(err),
        }


def _redact(data: dict[str, Any]) -> dict[str, Any]:
    """Redact sensitive keys, then hash any MAC-format device-map keys."""
    redacted: dict[str, Any] = async_redact_data(data, TO_REDACT)
    for key in ("devices", "leak_sensors"):
        if isinstance(redacted.get(key), dict):
            redacted[key] = _anonymize_device_keys(redacted[key])
    return redacted


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant,
    entry: ConfigEntry,
) -> dict[str, Any]:
    """Return diagnostics for the whole config entry."""
    coordinator: GoveeCoordinator = entry.runtime_data
    raw_state = coordinator.api_client.last_raw_state
    raw_mqtt = coordinator.mqtt_client.last_messages if coordinator.mqtt_client else {}

    devices_info = {
        device_id: _device_diag(coordinator, device_id, device, raw_state, raw_mqtt)
        for device_id, device in coordinator.devices.items()
    }

    diagnostics_data = {
        "config_entry": {
            "entry_id": entry.entry_id,
            "version": entry.version,
            "data": dict(entry.data),
            "options": dict(entry.options),
        },
        "devices": devices_info,
        "device_count": len(coordinator.devices),
        # Verbatim device-list response from the most recent discovery poll.
        "raw_api_devices": coordinator.api_client.last_raw_devices,
        "leak_sensors": _leak_diag(coordinator),
        # Read-only local-network scan to seed the LAN-API work (issue #57).
        "lan_discovery": await _lan_discovery_diag(),
        **_runtime_diag(coordinator),
    }
    return _redact(diagnostics_data)


async def async_get_device_diagnostics(
    hass: HomeAssistant,
    entry: ConfigEntry,
    device: DeviceEntry,
) -> dict[str, Any]:
    """Return diagnostics for a single device (its ⋮ menu → Download diagnostics).

    Resolves the device's Govee id(s) from its registry identifiers and dumps
    just the matching regular device, leak sensor, and/or leak hub — plus the
    shared runtime context.
    """
    coordinator: GoveeCoordinator = entry.runtime_data
    raw_state = coordinator.api_client.last_raw_state
    raw_mqtt = coordinator.mqtt_client.last_messages if coordinator.mqtt_client else {}

    govee_ids = [ident[1] for ident in device.identifiers if ident[0] == DOMAIN]

    devices_info: dict[str, Any] = {}
    leak_info: dict[str, Any] = {}
    for govee_id in govee_ids:
        if govee_id in coordinator.devices:
            devices_info[govee_id] = _device_diag(
                coordinator,
                govee_id,
                coordinator.devices[govee_id],
                raw_state,
                raw_mqtt,
            )
        # A device entry may be a leak sensor itself, or a hub that other leak
        # sensors link to via_device — include both.
        for leak_id, sensor in coordinator.leak_sensors.items():
            if leak_id == govee_id or sensor.hub_device_id == govee_id:
                state = coordinator.leak_states.get(leak_id)
                leak_info[leak_id] = {
                    "sensor": dataclasses.asdict(sensor),
                    "state": dataclasses.asdict(state) if state else None,
                }

    diagnostics_data = {
        "device": {
            "ha_id": device.id,
            "name": device.name_by_user or device.name,
            # Anonymize the MAC inside each (domain, id) identifier tuple — it is
            # a list element, so async_redact_data's key match won't reach it.
            "identifiers": [
                [dom, _anon_id(ident)] for dom, ident in device.identifiers
            ],
            "model": device.model,
            "sw_version": device.sw_version,
            "hw_version": device.hw_version,
        },
        "matched_govee_ids": [_anon_id(g) for g in govee_ids],
        "devices": devices_info,
        "leak_sensors": leak_info,
        **_runtime_diag(coordinator),
    }
    return _redact(diagnostics_data)
