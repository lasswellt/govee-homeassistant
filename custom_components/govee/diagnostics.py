"""Diagnostics support for Govee integration.

Provides debug information for troubleshooting without exposing sensitive data.
Implements both config-entry diagnostics (the whole integration) and per-device
diagnostics (a single device, via the device's ⋮ menu → Download diagnostics).
"""

from __future__ import annotations

import dataclasses
import hashlib
import logging
import re
from datetime import datetime
from ipaddress import IPv4Address, IPv4Network
from typing import Any

# LAN source-IP enumeration now lives in api/lan.async_get_lan_interface_ips, but
# the ``network`` module is still imported here so its attributes resolve under
# ``custom_components.govee.diagnostics.network`` (the patch point existing tests
# monkeypatch). It is the same shared module object the hoisted helper calls, so
# patches applied here still take effect there.
from homeassistant.components import network  # noqa: F401
from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceEntry

from .api.lan import (
    LanTargetError,
    async_get_lan_broadcast_addresses,
    async_get_lan_interface_ips,
    async_probe_lan_raw,
    async_scan_lan_devices,
    expand_lan_targets,
)
from .const import (
    CONF_API_KEY,
    CONF_EMAIL,
    CONF_LAN_TARGETS,
    CONF_PASSWORD,
    DOMAIN,
)
from .coordinator import GoveeCoordinator
from .models.transport import TRANSPORT_KINDS

_LOGGER = logging.getLogger(__name__)

# Coarse buckets so the maintainer can spot the most common LAN-discovery
# failure (issue #57) — HA running with a container-bridge source IP that cannot
# reach the physical LAN's multicast — WITHOUT exposing the host's actual IP.
_CONTAINER_BRIDGE = IPv4Network("172.16.0.0/12")
_TYPICAL_LAN = IPv4Network("192.168.0.0/16")
_PRIVATE_10 = IPv4Network("10.0.0.0/8")

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
    # The user's configured LAN targets reveal their internal subnets/device IPs;
    # the scan reports only the resulting target count, so redact the raw option.
    CONF_LAN_TARGETS,
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
    return {(_anonymize_device_id(k) if isinstance(k, str) and _looks_like_mac(k) else k): v for k, v in data.items()}


# A whole-string IPv4 dotted-quad (each octet 0-255). Anchored, so firmware
# versions like "1.02.03" (three parts, leading zeros) never match.
_IPV4_PATTERN = re.compile(r"^((25[0-5]|2[0-4]\d|1?\d?\d)\.){3}(25[0-5]|2[0-4]\d|1?\d?\d)$")


def _scrub_lan_addresses(obj: Any) -> Any:
    """Recursively value-redact MAC/IPv4 strings anywhere in a structure.

    The LAN reality probe captures unknown keys, so key-name redaction (TO_REDACT)
    can miss an address that arrives under an unexpected key. This walks the whole
    captured structure and scrubs by VALUE shape instead: a MAC-format string is
    anonymized to its stable ``device_`` hash (preserving same-device
    correlation), an IPv4 string becomes ``REDACTED_IP``. Non-address values pass
    through untouched.
    """
    if isinstance(obj, str):
        if _looks_like_mac(obj):
            return _anonymize_device_id(obj)
        if _IPV4_PATTERN.match(obj):
            return "REDACTED_IP"
        return obj
    if isinstance(obj, dict):
        return {k: _scrub_lan_addresses(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_scrub_lan_addresses(v) for v in obj]
    return obj


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


def _fan_swing_tail(coordinator: GoveeCoordinator, device_id: str) -> list[int] | None:
    """Last-seen Tower-Fan swing-range tail for a device, if the client has one."""
    client = coordinator.mqtt_client
    if client is None:
        return None
    try:
        tail = client.fan_swing_tail(device_id)
    except Exception:  # pragma: no cover - defensive
        return None
    return list(tail) if isinstance(tail, list) else None


def _transport_health(coordinator: GoveeCoordinator, device_id: str) -> dict[str, Any]:
    """Per-transport connectivity health for a device (timestamps as ISO)."""
    out: dict[str, Any] = {}
    for kind in TRANSPORT_KINDS:
        health = coordinator.get_transport_health(device_id, kind)
        if health is None:
            continue
        entry = {
            "is_available": health.is_available,
            "last_received": _iso(health.last_success_ts),
            "last_sent": _iso(health.last_send_ts),
            # Deprecated alias of last_received (pre-directional key); drop next release.
            "last_success": _iso(health.last_success_ts),
            "last_failure": _iso(health.last_failure_ts),
            "last_failure_reason": health.last_failure_reason,
        }
        if kind == "lan":
            # When True, LAN reads are healthy (is_available stays True) but the
            # device's WRITES are currently routed to MQTT/REST because recent LAN
            # writes did not confirm (issue #57). Distinguishes "LAN down" from
            # "LAN reads up, writes via cloud".
            entry["write_suppressed"] = coordinator.lan_write_suppressed(device_id)
        out[kind] = entry
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
        # Tower Fan 2 swing-range tail replayed on oscillation ON (PR #176);
        # None until the fan has reported an aa 1d frame on this session.
        "fan_swing_tail": _fan_swing_tail(coordinator, device_id),
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
    recent_probe_frames: list[dict[str, Any]] = []
    if mqtt_client:
        mqtt_info = {
            "available": mqtt_client.available,
            "connected": mqtt_client.connected,
            "tracked_devices": len(mqtt_client.last_messages),
        }
        # Recent hub multiSync packets (hex) — lets undecoded leak-sensor
        # packet subtypes be reverse-engineered from a download alone (#87).
        recent_multisync = mqtt_client.recent_multisync
        # Raw probe-thermometer frames (hex). Same purpose as the multiSync
        # buffer: the next probe SKU should be decodable from a download.
        recent_probe_frames = mqtt_client.recent_probe_frames
    openapi_client = coordinator.openapi_events_client
    openapi_info: dict[str, Any] | None = None
    if openapi_client:
        openapi_info = {
            "available": openapi_client.available,
            "connected": openapi_client.connected,
            # Recent devices.capabilities.event pushes (waterFullEvent,
            # lackWaterEvent, bodyAppearedEvent, ...) — lets event shapes for
            # new SKUs be captured from a download alone (#114, #118). MACs
            # inside payloads are value-redacted like everything else.
            "recent_events": openapi_client.recent_events,
        }
    return {
        "mqtt": mqtt_info,
        "openapi_events": openapi_info,
        "recent_multisync": recent_multisync,
        "recent_probe_frames": recent_probe_frames,
        # Recent /device/control sends with the exact capability payload and
        # Govee's HTTP status + response body — lets "command accepted but
        # device does nothing" reports (#127) be debugged from a download
        # alone, without asking the user to enable debug logging. The
        # "device" key in each record is redacted like everything else.
        "recent_commands": coordinator.api_client.recent_commands,
        # PII-free census of the BFF device list — shows whether the BFF API
        # returns a given leak SKU and if it carries discovery fields (#87).
        "bff_device_census": coordinator.bff_device_census,
        # PII-free shape of the raw BFF response — distinguishes "absent" from
        # "present under an unexpected path/shape" when the census is empty.
        "bff_response_skeleton": coordinator.bff_response_skeleton,
        # Redacted per-device BFF scalar values (deviceSettings + lastDeviceData)
        # — reveals which readings the BFF carries per device, to scope future
        # battery / temp-humidity / air-quality support beyond leak+thermo (#114).
        "bff_device_values": coordinator.bff_device_values,
        "has_iot_credentials": coordinator.has_iot_credentials,
        "device_topic_count": coordinator.device_topic_count,
        # How many devices are only commandable via their gateway's topic (#135).
        "gateway_route_count": coordinator.gateway_route_count,
        "api": {
            "rate_limit_remaining": coordinator.api_rate_limit_remaining,
            "rate_limit_total": coordinator.api_rate_limit_total,
            "rate_limit_reset": coordinator.api_rate_limit_reset,
        },
        "scene_cache_count": coordinator.scene_cache_count,
        "diy_scene_cache_count": coordinator.diy_scene_cache_count,
    }


def _classify_ip(ip: IPv4Address) -> str:
    """Bucket a host source IP without revealing it (see ``_CONTAINER_BRIDGE``)."""
    if ip.is_loopback:
        return "loopback"
    if ip.is_link_local:
        return "link-local"
    if ip in _CONTAINER_BRIDGE:
        return "private-172 (often container bridge)"
    if ip in _TYPICAL_LAN:
        return "private-192.168 (typical LAN)"
    if ip in _PRIVATE_10:
        return "private-10"
    if ip.is_private:
        return "private-other"
    return "public"


async def _lan_source_interfaces(hass: HomeAssistant) -> tuple[list[str], list[str]]:
    """Return the host's enabled IPv4 LAN source IPs and their coarse classes.

    Delegates the actual enumeration to the shared
    :func:`async_get_lan_interface_ips` helper (also used by the LAN transport
    client, issue #57) so there is a single implementation, then layers on the
    diagnostics-only coarse classification via :func:`_classify_ip` — the host's
    real IPs are never rendered, only their privacy-safe buckets. Never raises;
    an empty list means "default-route scan".
    """
    ips = await async_get_lan_interface_ips(hass)
    classes = [_classify_ip(IPv4Address(ip)) for ip in ips]
    return ips, classes


async def _lan_discovery_diag(hass: HomeAssistant, lan_targets_raw: str = "") -> dict[str, Any]:
    """Run one read-only LAN scan for the diagnostics download (issue #57).

    Captures which of the user's devices answer Govee's local UDP discovery and
    what they report, so the community can supply the data the full LAN feature
    needs. Never raises — diagnostics must always produce output. Each responder
    IP is redacted by the shared ``_redact`` pass; the host's own source IPs are
    reported only as coarse classes (``interface_classes``), never verbatim, so
    a publicly-attached download stays PII-free while still revealing the common
    container-bridge misconfiguration.

    ``lan_targets_raw`` is the user's ``CONF_LAN_TARGETS`` option — extra
    unicast / broadcast / CIDR addresses scanned for devices on another
    VLAN/subnet. Only the resulting target *count* is reported, never the
    addresses.
    """
    interface_ips, interface_classes = await _lan_source_interfaces(hass)
    broadcast_targets = await async_get_lan_broadcast_addresses(hass)
    extra_targets: list[str] = []
    try:
        extra_targets = expand_lan_targets(lan_targets_raw)
    except LanTargetError as err:  # validated at config time; tolerate stale opts
        _LOGGER.debug("LAN discovery: ignoring invalid lan_targets: %s", err)

    result: dict[str, Any] = {
        "scan_attempted": True,
        "device_count": 0,
        "devices": [],
        "interface_count": len(interface_ips),
        "interface_classes": interface_classes,
        "extra_target_count": len(extra_targets),
        # Auto-derived per-subnet broadcast targets the scan also hit (#57): a
        # newer device that ignores multicast but answers a broadcast should now
        # appear in device_count. Only the count is reported (no addresses).
        "broadcast_target_count": len(broadcast_targets),
        "error": None,
        "probe_attempted": False,
        "probe_response_count": 0,
        "probe_error": None,
        # Pre-initialized so the lan_discovery schema is identical on every return
        # path, incl. the scan-failure early return below (overwritten on success).
        "commands_answered": [],
    }
    try:
        devices = await async_scan_lan_devices(
            interface_ips=interface_ips,
            extra_targets=extra_targets,
            broadcast_targets=broadcast_targets,
        )
        result["device_count"] = len(devices)
        result["devices"] = devices
    except Exception as err:  # never break the diagnostics download
        result["error"] = str(err)
        return result

    # REALITY probe (issue #57): fire a read-only query battery (devStatus +
    # status + unicast scan) at each discovered device and capture EVERY datagram
    # it emits, completely unfiltered. We deliberately do NOT trust other
    # integrations' field/command lists — the goal is to measure on real hardware
    # what the firmware actually exposes (e.g. a `status` reply's `pt` BLE-hex may
    # carry segment/scene/sensor state that the 4-field devStatus omits). Best-
    # effort and never-raise, same contract as the scan.
    #
    # PII: because we capture unknown keys, key-name TO_REDACT is not enough.
    # _scrub_lan_addresses() additionally value-redacts any MAC- or IPv4-shaped
    # string anywhere in the capture (firmware versions like "1.02.03" are 3
    # dotted parts, not an IPv4 quad, so they survive). A reviewer should still
    # eyeball the first community downloads for a NEW PII key (hostname, ssid).
    probe_ips = [d["ip"] for d in devices if d.get("ip")]
    result["probe_attempted"] = bool(probe_ips)
    raw_by_ip: dict[str, list[Any]] = {}
    if probe_ips:
        try:
            raw_by_ip = await async_probe_lan_raw(probe_ips, interface_ips=interface_ips)
        except Exception as err:  # never break the diagnostics download
            result["probe_error"] = str(err)
    result["probe_response_count"] = len(raw_by_ip)
    # Aggregate which distinct commands answered across the whole fleet — a quick
    # at-a-glance map of the real readable surface.
    answered: set[str] = set()
    for replies in raw_by_ip.values():
        answered.update(_reply_cmds(replies))
    result["commands_answered"] = sorted(answered)
    # Attach per device: the full raw capture, a readable devStatus summary, and
    # which commands that device answered. Non-responders get empty/None so the
    # responder ratio and per-command support are explicit in the community data.
    for device in devices:
        device_ip = device.get("ip")
        replies = raw_by_ip.get(device_ip, []) if isinstance(device_ip, str) else []
        device["lan_raw"] = replies
        device["commands_answered"] = sorted(_reply_cmds(replies))
        device["status"] = _reply_data(replies, "devStatus")
    return result


def _reply_cmds(replies: list[Any]) -> set[str]:
    """The set of ``msg.cmd`` values present in a device's raw probe replies."""
    cmds: set[str] = set()
    for reply in replies:
        if isinstance(reply, dict):
            cmd = reply.get("msg", {}).get("cmd") if isinstance(reply.get("msg"), dict) else None
            if isinstance(cmd, str):
                cmds.add(cmd)
    return cmds


def _reply_data(replies: list[Any], cmd: str) -> dict[str, Any] | None:
    """The ``msg.data`` dict of the last reply matching ``cmd`` (readable summary)."""
    found: dict[str, Any] | None = None
    for reply in replies:
        if not isinstance(reply, dict):
            continue
        msg = reply.get("msg")
        if isinstance(msg, dict) and msg.get("cmd") == cmd and isinstance(msg.get("data"), dict):
            found = msg["data"]
    return found


def _redact(data: dict[str, Any]) -> dict[str, Any]:
    """Redact sensitive keys, then hash any MAC-format device-map keys."""
    redacted: dict[str, Any] = async_redact_data(data, TO_REDACT)
    for key in ("devices", "leak_sensors"):
        if isinstance(redacted.get(key), dict):
            redacted[key] = _anonymize_device_keys(redacted[key])
    # The LAN reality probe captures unknown keys, so additionally scrub the whole
    # lan_discovery subtree by value shape (catches a MAC/IP under any key name).
    if isinstance(redacted.get("lan_discovery"), dict):
        redacted["lan_discovery"] = _scrub_lan_addresses(redacted["lan_discovery"])
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
        "lan_discovery": await _lan_discovery_diag(hass, entry.options.get(CONF_LAN_TARGETS, "")),
        # PII-free LAN transport census (#57): how many devices are currently
        # correlated to a LAN address (active) vs. how many answered the scan but
        # did not match a coordinator device_id (unmatched — surfaces MAC-format
        # drift). Counts only — no address and no scan->device_id join is exposed,
        # so the auto-enabled LAN transport stays observable from a download
        # alone, without hardware and without leaking any address.
        "lan_active_count": len(coordinator._lan_devices),
        "lan_unmatched_count": len(coordinator._lan_unmatched),
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
            "identifiers": [[dom, _anon_id(ident)] for dom, ident in device.identifiers],
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
