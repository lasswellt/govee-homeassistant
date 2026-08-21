"""Read-only Govee LAN (UDP) observation helper.

Govee exposes a local UDP control API (must be toggled on per device in the
Govee app) on a subset of mostly-light SKUs. This module performs ONLY read-only
probes so a user can capture which of their devices answer on the LAN and what
state they report, and attach it to a diagnostics download. That community data
is the prerequisite for the full LAN transport requested in issue #57 (the
maintainer has no LAN hardware to test against).

Two probes, both safe to attach to a diagnostics download:

- ``async_scan_lan_devices`` — one bounded multicast ``scan`` (discovery): which
  devices answer and their identity/firmware metadata.
- ``async_probe_lan_raw`` — a "reality probe": fire a battery of safe READ-ONLY
  queries (``devStatus`` + ``status`` + a unicast ``scan``) at each discovered
  device and capture **every** datagram it emits, completely unfiltered — whole
  payload, any command, any field, even undecodable bytes. We do NOT trust any
  other integration's notion of which commands exist or which fields a reply
  carries (``govee-local-api`` parses only 4 ``devStatus`` fields and never sends
  ``status`` at all); the point is to measure on real hardware what the firmware
  actually exposes rather than inherit someone else's parser. In particular the
  ``status`` command's ``pt`` (BLE-passthrough hex) field may carry
  segment/scene/sensor state that the 4-field ``devStatus`` omits.

``ptReal`` and the other control verbs (``turn``/``brightness``/``colorwc``) are
deliberately NOT sent: they are state-changing writes, forbidden in this
read-only module. Capturing what the device *volunteers* in response to read
queries is the safe way to map the surface.

Deliberately scoped: no control writes, no entities, no persistent socket — each
call opens a socket, collects responses for a short timeout, and returns them.
Protocol per ``docs/govee-protocol-reference.md`` §6:

- Scan request    -> 239.255.255.250:4001  ``{"msg":{"cmd":"scan",...}}``
- Scan response   -> 239.255.255.250:4002  ``{"msg":{"cmd":"scan","data":{...}}}``
- Read queries    -> <device-ip>:4003/4001 ``{"msg":{"cmd":"devStatus|status|scan","data":{}}}``
- Replies         -> our :4002 (unicast OR multicast, firmware-dependent)

Critical protocol detail (the reason early builds returned zero devices, issue
#57): a Govee device sends its scan *response* as **multicast** to the group on
port 4002 — it does NOT unicast the reply back to the sender. So the receive
socket MUST join the ``239.255.255.250`` group via ``IP_ADD_MEMBERSHIP`` or the
kernel silently drops every reply before it reaches us. Binding port 4002 alone
is not enough. The reality probe reuses the same group-joined 4002 socket so it
catches replies whether a given firmware answers unicast or multicast. This
mirrors ``govee-local-api`` (the library behind Home Assistant's
``govee_light_local``) and ``wez/govee2mqtt``.
"""

from __future__ import annotations

import asyncio
import json
import logging
import socket
from ipaddress import AddressValueError, IPv4Address, IPv4Network
from typing import Any

from homeassistant.components import network
from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)

# A LAN-target CIDR may expand to at most this many addresses (a /24). Larger
# subnets would unicast-sweep thousands of hosts per scan and are rejected by
# ``expand_lan_targets``. Kept here so the parser is self-contained/testable.
MAX_LAN_TARGET_ADDRESSES = 256

# Govee LAN API network parameters (docs/govee-protocol-reference.md §6).
LAN_MULTICAST_GROUP = "239.255.255.250"
LAN_DISCOVERY_PORT = 4001  # devices listen here for the scan request
LAN_RESPONSE_PORT = 4002  # devices multicast scan responses here; we listen
LAN_COMMAND_PORT = 4003  # devices listen here for unicast devStatus/control
LAN_MULTICAST_TTL = 2  # let a scan / reply cross at most one router hop

# Reality-probe budget. All probes share ONE socket and ONE collection window
# (sends are fire-and-forget; replies arrive asynchronously), so total wall time
# is bounded by the window regardless of device_count — 11 devices cost the same
# ~2s as one. The cap bounds send-loop work against a large CIDR sweep, not wait.
LAN_PROBE_WINDOW = 2.5  # seconds to collect all probe replies
LAN_PROBE_MAX_DEVICES = 64  # hard cap on how many IPs we probe in one batch
LAN_PROBE_MAX_REPLIES_PER_IP = 32  # guard against a chatty device flooding output

# INADDR_ANY: join/egress on the kernel's default-route interface. Always added
# alongside any explicit interface IPs as a catch-all for single-NIC hosts.
_DEFAULT_INTERFACE = "0.0.0.0"

_SCAN_REQUEST = json.dumps({"msg": {"cmd": "scan", "data": {"account_topic": "reserve"}}}).encode("utf-8")

# Read-only LAN query battery for the reality probe (issue #57). We do NOT trust
# any other integration's field/command list — we send every safe READ query we
# know of and capture whatever the hardware actually emits, so the real LAN data
# surface is measured, not assumed. STRICTLY read-only: NO writes
# (turn/brightness/colorwc/ptReal) — a diagnostics probe must never mutate device
# state. Each entry is ``(cmd, port, data)`` sent unicast; ``data`` is empty so no
# parameters are set. Replies are captured raw on 4002 regardless of cmd.
#
# - ``devStatus`` (:4003) — the documented status read (4 known fields).
# - ``status``    (:4003) — undocumented in HA libs but a ``StatusResponse`` with
#   a ``pt`` (base64 BLE passthrough) field exists in govee-local-api yet is never
#   sent; it may carry segment/scene/sensor state the 4-field devStatus omits.
# - ``scan``      (:4001) — unicast discovery, captured WHOLE (not the 7-field
#   allowlist) so any extra identity/firmware fields surface.
LAN_PROBE_COMMANDS: tuple[tuple[str, int, dict[str, Any]], ...] = (
    ("devStatus", LAN_COMMAND_PORT, {}),
    ("status", LAN_COMMAND_PORT, {}),
    ("scan", LAN_DISCOVERY_PORT, {"account_topic": "reserve"}),
)

# Packed multicast group address, reused for every IP_ADD/DROP_MEMBERSHIP call.
_GROUP_BYTES = socket.inet_aton(LAN_MULTICAST_GROUP)

# Fields a scan response may carry; we surface exactly these (no control data).
_RESPONSE_FIELDS = (
    "ip",
    "device",
    "sku",
    "bleVersionHard",
    "bleVersionSoft",
    "wifiVersionHard",
    "wifiVersionSoft",
)


class LanTargetError(ValueError):
    """A configured LAN-target entry is not a valid IP / subnet (issue #57)."""


def expand_lan_targets(raw: str | None) -> list[str]:
    """Parse the user's LAN-targets option into concrete scan addresses.

    ``raw`` is a free-text list (comma / newline / whitespace separated). Each
    token is one of:

    - a device IP (``10.20.0.51``)        -> that address, unicast
    - a broadcast address (``10.20.0.255``) -> that address, broadcast
    - a CIDR subnet (``10.20.0.0/24``)    -> every host address plus the subnet
      broadcast, so cross-VLAN devices are reached by unicast sweep (inter-VLAN
      firewalls usually drop a single directed broadcast)
    - a ``device_id=ip`` override (see :func:`parse_lan_device_overrides`) —
      skipped here, not a scan target

    Returns a de-duplicated, order-preserving list of IPv4 address strings.
    Raises ``LanTargetError`` on an unparseable token or a subnet wider than
    ``MAX_LAN_TARGET_ADDRESSES`` (a /24), so the options flow can reject it.
    """
    if not raw:
        return []

    targets: list[str] = []
    seen: set[str] = set()

    def _add(address: str) -> None:
        if address not in seen:
            seen.add(address)
            targets.append(address)

    for token in raw.replace(",", " ").split():
        if "=" in token:
            continue  # a device_id=ip override — handled by the caller
        try:
            if "/" in token:
                network = IPv4Network(token, strict=False)
                if network.num_addresses > MAX_LAN_TARGET_ADDRESSES:
                    raise LanTargetError(
                        f"Subnet {token} is larger than /24 — list device IPs " "or a /24 (or smaller) subnet instead."
                    )
                for host in network.hosts():
                    _add(str(host))
                # The broadcast address covers flat same-VLAN networks cheaply.
                if network.num_addresses > 1:
                    _add(str(network.broadcast_address))
            else:
                _add(str(IPv4Address(token)))
        except LanTargetError:
            raise
        except (AddressValueError, ValueError) as err:
            raise LanTargetError(f"'{token}' is not a valid IP or subnet") from err

    return targets


def parse_lan_device_overrides(raw: str | None) -> dict[str, tuple[str, bool]]:
    """Parse ``device_id=ip[!]`` overrides out of the LAN-targets free text.

    A cross-VLAN target (:func:`expand_lan_targets`) only fixes *reaching* a
    device with the unicast ``scan`` request — correlation still needs that
    device's *reply*, which is multicast and so never crosses the VLAN
    boundary back to us regardless of firmware. A bare ``device_id=ip`` token
    routes around that structural limit: it binds the coordinator's known
    ``device_id`` straight to an IP, skipping discovery entirely.

    A trailing ``!`` (``device_id=ip!``) is for a further, firmware-specific
    case (issue #164): some devices never answer ``devStatus`` either, even
    from their own subnet — a genuine silence, not a VLAN artifact. The ``!``
    marks the device *write-only*, telling the caller to skip the read-health
    gate and write-confirm readback, since a device that never answers a read
    can never satisfy either.

    Malformed or duplicate tokens never raise: a later duplicate device_id
    wins, and a bad token (no ``=``, empty device_id/ip, invalid ip) is
    skipped silently, same contract as :func:`expand_lan_targets`'s errors.

    Returns ``{device_id: (ip, write_only)}``. The caller must still validate
    each ``device_id`` against its known devices — this function can't.
    """
    overrides: dict[str, tuple[str, bool]] = {}
    if not raw:
        return overrides

    for token in raw.replace(",", " ").split():
        if "=" not in token:
            continue
        device_id, _, value = token.partition("=")
        device_id = device_id.strip()
        write_only = value.endswith("!")
        ip = value[:-1] if write_only else value
        ip = ip.strip()
        if not device_id or not ip:
            continue
        try:
            IPv4Address(ip)
        except (AddressValueError, ValueError):
            continue
        overrides[device_id] = (ip, write_only)

    return overrides


def validate_lan_device_overrides(raw: str | None) -> dict[str, tuple[str, bool]]:
    """Strict counterpart to :func:`parse_lan_device_overrides`, for the form.

    Runtime parsing is deliberately lenient — one bad token must never stop the
    other overrides from binding, the same contract the scan-target path uses.
    That leniency is wrong at the options form, though: a mistyped IP would
    save cleanly and then do nothing, leaving the user with an accepted form,
    a stored option, and a device that never works. The existing targets
    validation exists for exactly this reason ("rejected in the form, not
    silently dropped at scan time", #57), so overrides get the same treatment.

    Raises ``LanTargetError`` on an override token with an empty device_id, an
    empty IP, or an unparseable IP. Returns the parsed overrides so the caller
    can run checks this module can't — notably whether each device_id is a
    device on the account.
    """
    overrides: dict[str, tuple[str, bool]] = {}
    if not raw:
        return overrides

    for token in raw.replace(",", " ").split():
        if "=" not in token:
            continue
        device_id, _, value = token.partition("=")
        device_id = device_id.strip()
        write_only = value.endswith("!")
        ip = (value[:-1] if write_only else value).strip()
        if not device_id:
            raise LanTargetError(f"'{token}' is missing a device ID before the '='")
        if not ip:
            raise LanTargetError(f"'{token}' is missing an IP address after the '='")
        try:
            IPv4Address(ip)
        except (AddressValueError, ValueError) as err:
            raise LanTargetError(f"'{ip}' in '{token}' is not a valid IP address") from err
        overrides[device_id] = (ip, write_only)

    return overrides


async def async_get_lan_interface_ips(hass: HomeAssistant) -> list[str]:
    """Return the host's enabled, non-loopback IPv4 source IPs as strings.

    Uses Home Assistant's ``network`` component so a multi-homed host can scan /
    join the multicast group on every enabled adapter. This is the single shared
    enumeration used by BOTH the diagnostics LAN scan and the persistent LAN
    transport client (issue #57), so the two never drift.

    Non-IPv4 (e.g. IPv6) and loopback addresses are excluded. Never raises —
    degrades to an empty list (meaning "default-route interface only") if the
    network component is unavailable or errors, so every caller can always
    proceed without a try/except of its own.
    """
    ips: list[str] = []
    try:
        for source_ip in await network.async_get_enabled_source_ips(hass):
            if isinstance(source_ip, IPv4Address) and not source_ip.is_loopback:
                ips.append(str(source_ip))
    except Exception as err:  # network component unavailable — fall back
        _LOGGER.debug("LAN discovery: could not enumerate source IPs: %s", err)
    return ips


async def async_get_lan_broadcast_addresses(hass: HomeAssistant) -> list[str]:
    """Return the IPv4 directed-broadcast address of each enabled LAN adapter.

    Newer Govee devices — and some access points / firmware — silently ignore
    the multicast discovery scan yet DO answer a scan sent to the subnet's
    directed-broadcast address (issue #57: an H707B reported zero multicast
    discovery, yet a broadcast ``scan`` to ``x.x.x.255`` reached it and unblocked
    local control). This derives those ``x.x.x.255``-style broadcast addresses
    from Home Assistant's adapter list so the discovery scan reaches such devices
    automatically, without the user hand-configuring a broadcast LAN target.

    Uses HA's ``network.async_get_adapters`` for the per-adapter netmask
    (``async_get_enabled_source_ips`` returns bare IPs with no prefix). Skips
    disabled adapters, loopback / link-local addresses, and ``/31``-``/32``
    point-to-point addresses (which have no usable directed broadcast).
    Deduplicated, order-preserving, and never raises — degrades to an empty list
    if the network component is unavailable, so every caller can proceed.
    """
    broadcasts: list[str] = []
    seen: set[str] = set()
    try:
        for adapter in await network.async_get_adapters(hass):
            if not adapter.get("enabled", True):
                continue
            for ipv4 in adapter.get("ipv4", []):
                address = ipv4.get("address")
                prefix = ipv4.get("network_prefix")
                if not address or prefix is None:
                    continue
                try:
                    ip = IPv4Address(address)
                    if ip.is_loopback or ip.is_link_local:
                        continue
                    net = IPv4Network(f"{address}/{prefix}", strict=False)
                except (AddressValueError, ValueError):
                    continue
                # /31 and /32 have no usable directed-broadcast address.
                if net.num_addresses <= 2:
                    continue
                broadcast = str(net.broadcast_address)
                if broadcast not in seen:
                    seen.add(broadcast)
                    broadcasts.append(broadcast)
    except Exception as err:  # network component unavailable — fall back
        _LOGGER.debug("LAN discovery: could not enumerate adapters: %s", err)
    return broadcasts


class _ScanProtocol(asyncio.DatagramProtocol):
    """Collects well-formed Govee ``scan`` responses; ignores everything else."""

    def __init__(self) -> None:
        self.responses: dict[str, dict[str, Any]] = {}

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        try:
            payload = json.loads(data.decode("utf-8", errors="replace"))
            msg = payload.get("msg", {})
            if msg.get("cmd") != "scan":
                return
            body = msg.get("data", {})
            if not isinstance(body, dict):
                return
        except (ValueError, AttributeError):
            return

        record = {field: body[field] for field in _RESPONSE_FIELDS if field in body}
        if not record:
            return
        # Some firmwares omit "ip" in the body; fall back to the datagram source
        # address so every responder has a usable address (redacted downstream).
        record.setdefault("ip", addr[0])
        # Dedupe by the device id (MAC) when present, else by IP.
        key = str(record.get("device") or record["ip"])
        self.responses[key] = record

    def error_received(self, exc: Exception) -> None:  # pragma: no cover - rare
        _LOGGER.debug("LAN scan socket error: %s", exc)


class _RawProbeProtocol(asyncio.DatagramProtocol):
    """Captures EVERY datagram received during a probe, raw, keyed by source IP.

    Deliberately unfiltered — no ``cmd`` check, no field allowlist, no shape
    assumptions. The goal is to record exactly what the hardware emits, including
    commands and fields no reference library parses, so the real LAN data surface
    is *measured* rather than inherited from another integration's parser. The
    whole ``{"msg": ...}`` payload is kept; an undecodable datagram is captured as
    a truncated ``_unparsed`` string rather than dropped (even garbage is signal).

    Keyed by the datagram SOURCE IP — correct for both reply paths (a unicast
    reply to our 4002 source and a multicast reply to the group both carry the
    device's own IP as the UDP source). Each IP accumulates a LIST of replies so
    multiple commands' responses (devStatus + status + scan) are all retained.
    Redaction is downstream in diagnostics (key-name + value-level address scrub).
    """

    def __init__(self) -> None:
        self.replies: dict[str, list[Any]] = {}

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        bucket = self.replies.setdefault(addr[0], [])
        if len(bucket) >= LAN_PROBE_MAX_REPLIES_PER_IP:
            return  # chatty device / broadcast storm — keep the dump bounded
        try:
            payload: Any = json.loads(data.decode("utf-8", errors="replace"))
        except ValueError:
            payload = {"_unparsed": data.decode("utf-8", errors="replace")[:512]}
        bucket.append(payload)

    def error_received(self, exc: Exception) -> None:  # pragma: no cover - rare
        _LOGGER.debug("LAN raw-probe socket error: %s", exc)


def _build_socket() -> socket.socket:
    """Create the bound UDP receive socket for the scan (raises ``OSError``).

    Binds the wildcard address on port 4002 so multicast replies are accepted
    regardless of which interface they arrive on, with ``SO_REUSEADDR`` /
    ``SO_REUSEPORT`` so we can co-bind alongside other LAN integrations
    (``govee_light_local``, the official Govee LAN app) already holding 4002.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    # SO_BROADCAST so a configured x.x.x.255 LAN target can be reached (#57).
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    reuse_port = getattr(socket, "SO_REUSEPORT", None)
    if reuse_port is not None:
        try:
            sock.setsockopt(socket.SOL_SOCKET, reuse_port, 1)
        except OSError:  # pragma: no cover - platform without SO_REUSEPORT support
            pass
    sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, LAN_MULTICAST_TTL)
    sock.setblocking(False)
    try:
        sock.bind(("", LAN_RESPONSE_PORT))
    except OSError:
        sock.close()
        raise
    return sock


def _join_group(sock: socket.socket, interfaces: list[str]) -> list[str]:
    """Join ``239.255.255.250`` on every interface; return those that succeeded.

    Joins per interface IP (plus the default route) so a multi-homed host
    receives the multicast replies on whichever adapter the devices live on.
    Without at least one successful join the kernel drops every reply (#57).
    """
    joined: list[str] = []
    for iface in [*interfaces, _DEFAULT_INTERFACE]:
        if iface in joined:
            continue
        try:
            mreq = _GROUP_BYTES + socket.inet_aton(iface)
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
            joined.append(iface)
        except OSError as err:  # interface gone / already joined — skip it
            _LOGGER.debug("LAN scan: group join on %s failed: %s", iface, err)
    return joined


def _drop_group(sock: socket.socket, joined: list[str]) -> None:
    """Leave the multicast group on every interface we joined (best effort)."""
    for iface in joined:
        try:
            mreq = _GROUP_BYTES + socket.inet_aton(iface)
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_DROP_MEMBERSHIP, mreq)
        except OSError:  # pragma: no cover - teardown best effort
            pass


def _send_scan(
    sock: socket.socket,
    transport: asyncio.DatagramTransport,
    interfaces: list[str],
) -> None:
    """Emit the scan out each interface so devices on every subnet receive it.

    ``IP_MULTICAST_IF`` selects the egress adapter per send; with no explicit
    interfaces we fall back to the default route.
    """
    for iface in interfaces or [_DEFAULT_INTERFACE]:
        try:
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton(iface))
        except OSError as err:  # pragma: no cover - bad interface, try the send anyway
            _LOGGER.debug("LAN scan: egress select on %s failed: %s", iface, err)
        transport.sendto(_SCAN_REQUEST, (LAN_MULTICAST_GROUP, LAN_DISCOVERY_PORT))


def _send_targets(
    transport: asyncio.DatagramTransport,
    targets: list[str],
) -> None:
    """Unicast / broadcast the scan to explicit targets (other subnets/VLANs).

    Devices reply unicast to our 4002 source, which routes back across the VLAN
    boundary that the local multicast scan cannot cross (issue #57).
    """
    for target in targets:
        transport.sendto(_SCAN_REQUEST, (target, LAN_DISCOVERY_PORT))


async def async_scan_lan_devices(
    timeout: float = 2.0,
    interface_ips: list[str] | None = None,
    extra_targets: list[str] | None = None,
    broadcast_targets: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Send one multicast ``scan`` and collect responses for ``timeout`` seconds.

    Returns a list of per-device dicts (deduped) limited to the discovery fields
    above — no control surface is touched. Uses asyncio datagram endpoints so it
    is safe to run on the Home Assistant event loop.

    ``interface_ips`` are the host's enabled LAN source IPs (from HA's network
    helper). The group is joined and the scan emitted on each, plus the default
    route, so multi-homed / multi-VLAN hosts are covered. When omitted, only the
    default-route interface is used.

    ``extra_targets`` are explicit unicast / broadcast addresses (from the user's
    LAN-targets option via ``expand_lan_targets``) for devices the local
    multicast cannot reach — e.g. on another VLAN. The scan is sent to each in
    addition to the multicast; replies return unicast to our 4002 source.

    ``broadcast_targets`` are the host's auto-derived per-subnet directed-broadcast
    addresses (from ``async_get_lan_broadcast_addresses``). Newer devices that
    ignore the multicast scan still answer a broadcast scan (issue #57), so the
    scan is sent to each of these as well — no user configuration required.

    Raises ``OSError`` if the response socket cannot be bound (e.g. port 4002 in
    use by another local-control app that does not share the port); callers
    should treat that as "no data".
    """
    loop = asyncio.get_running_loop()
    interfaces = list(interface_ips or [])
    targets = list(extra_targets or [])
    broadcasts = list(broadcast_targets or [])

    sock = _build_socket()  # raises OSError if port 4002 cannot be bound
    joined = _join_group(sock, interfaces)

    transport, protocol = await loop.create_datagram_endpoint(_ScanProtocol, sock=sock)
    assert isinstance(protocol, _ScanProtocol)
    try:
        _send_scan(sock, transport, interfaces)
        _send_targets(transport, targets)
        # Auto-derived subnet broadcast(s): reach newer devices that ignore the
        # multicast scan but answer a directed broadcast (issue #57).
        _send_targets(transport, broadcasts)
        await asyncio.sleep(timeout)
    finally:
        _drop_group(sock, joined)
        transport.close()

    return list(protocol.responses.values())


async def async_probe_lan_raw(
    ips: list[str],
    timeout: float = LAN_PROBE_WINDOW,
    interface_ips: list[str] | None = None,
    commands: tuple[tuple[str, int, dict[str, Any]], ...] = LAN_PROBE_COMMANDS,
) -> dict[str, list[Any]]:
    """Reality probe: fire a read-only query battery at each IP, capture all replies.

    Returns ``{responder_ip: [raw_payload, ...]}`` — the WHOLE ``{"msg": ...}`` of
    every datagram each device emits during the window, completely unfiltered. We
    do not trust any other integration's idea of which commands exist or which
    fields a reply carries: we send every safe READ query in ``commands`` and
    record exactly what comes back, so the real LAN data surface is measured. A
    device that does not answer simply has no entry.

    ``commands`` is ``((cmd, port, data), ...)`` — STRICTLY read-only (default
    ``LAN_PROBE_COMMANDS``: devStatus + status + unicast scan). No control writes
    are ever sent. Each is unicast to ``<ip>:port``; replies may return unicast to
    our 4002 source OR multicast to ``239.255.255.250:4002`` depending on
    firmware, so we reuse the scan socket pattern (bound 4002 + group-joined) to
    catch both. All probes share one socket and one collection window, so total
    wall time is bounded by ``timeout`` regardless of device count. ``ips`` is
    capped at ``LAN_PROBE_MAX_DEVICES``; per-IP capture is capped at
    ``LAN_PROBE_MAX_REPLIES_PER_IP``.

    ``interface_ips`` join the multicast group on each adapter (multi-homed
    coverage), mirroring ``async_scan_lan_devices``.

    Raises ``OSError`` if the response socket cannot be bound (port 4002 held by
    a non-sharing local-control app); callers should treat that as "no data",
    the same contract as ``async_scan_lan_devices``.
    """
    if not ips:
        return {}

    interfaces = list(interface_ips or [])
    targets = ips[:LAN_PROBE_MAX_DEVICES]
    requests = [
        (json.dumps({"msg": {"cmd": cmd, "data": data}}).encode("utf-8"), port) for cmd, port, data in commands
    ]

    loop = asyncio.get_running_loop()
    sock = _build_socket()  # raises OSError if port 4002 cannot be bound
    joined = _join_group(sock, interfaces)  # catch multicast replies too

    transport, protocol = await loop.create_datagram_endpoint(_RawProbeProtocol, sock=sock)
    assert isinstance(protocol, _RawProbeProtocol)
    try:
        for ip in targets:
            for request, port in requests:
                try:
                    transport.sendto(request, (ip, port))
                except OSError as err:  # one bad/unreachable IP must not abort the batch
                    _LOGGER.debug("LAN raw probe: send to %s:%s failed: %s", ip, port, err)
        await asyncio.sleep(timeout)
    finally:
        _drop_group(sock, joined)
        transport.close()

    return dict(protocol.replies)
