"""Pure data models + helpers for the Govee LAN (UDP) transport (issue #57).

This module hosts the Govee LAN (UDP) transport (issue #57). Its first two
concerns are pure functions over plain data, trivially testable without hardware
or sockets:

1. ``parse_dev_status`` — turn a raw ``devStatus`` reply payload into a frozen
   :class:`LanDevStatus`. Govee's LAN ``devStatus`` reply exposes EXACTLY four
   runtime fields and nothing else (per ``docs/govee-protocol-reference.md`` §5.3
   and the reference libraries ``Galorhallen/govee-local-api`` +
   ``wez/govee2mqtt``): ``onOff``, ``brightness`` (0-100), ``color`` ``{r,g,b}``
   (whole-device) and ``colorTemInKelvin``. We extract those four and never
   fabricate anything else — a partial, wrong-command or undecodable datagram
   yields ``None`` so a malformed reply can never overwrite real device state.

2. ``correlate_scan`` — map the device identifiers reported by a LAN ``scan``
   onto the coordinator's known ``device_id`` values, so a later wave can decide
   which devices may use the LAN transport. Matching is exact-string first, then
   a hex-normalized fallback (MAC formatting differs between the scan reply and
   the developer API). Groups are skipped, IPs are NEVER guessed, and unmatched
   scan records are returned so MAC-format drift stays observable in diagnostics.

3. :class:`GoveeLanClient` — the coordinator-owned persistent client that owns
   the live UDP sockets (the only I/O in this module). It implements the
   lifecycle and the dual-socket design (a group-joined :4002 receive socket
   plus a dedicated ephemeral send socket) and forwards every inbound datagram
   to an injected callback. The public read/send surface and the
   parse-and-dispatch wiring arrive in a later wave (LAN-009).
"""

from __future__ import annotations

import asyncio
import json
import logging
import socket
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from ..const import LAN_READ_WINDOW, LAN_WRITE_CONFIRM_TIMEOUT
from ..models.device import looks_like_group_id
from ..models.state import RGBColor
from .lan import (
    LAN_COMMAND_PORT,
    LAN_MULTICAST_TTL,
    LAN_RESPONSE_PORT,
    _build_socket,
    _drop_group,
    _join_group,
    _ScanProtocol,
    _send_scan,
    _send_targets,
)

_LOGGER = logging.getLogger(__name__)

# The LAN ``devStatus`` reply carries EXACTLY these four runtime fields and
# nothing else (see module docstring). A reply missing any of them is partial
# and is rejected rather than fabricated into all-``None``.
_DEV_STATUS_FIELDS = frozenset({"onOff", "brightness", "color", "colorTemInKelvin"})

# Characters retained by ``_hex_normalize`` (after upper-casing). MAC/device
# identifiers differ only in separators and case between transports, so reducing
# both sides to their bare hex digits makes the comparison format-agnostic.
_HEX_DIGITS = frozenset("0123456789ABCDEF")


@dataclass(frozen=True)
class LanDevStatus:
    """Immutable snapshot of the four readable LAN ``devStatus`` fields.

    Every field is optional because the LAN data ceiling is hard: only these
    four values are ever readable, and ``color_temp_kelvin`` is ``None`` whenever
    the device is not in color-temperature mode (``colorTemInKelvin == 0``).
    """

    on: bool | None
    brightness_0_100: int | None
    color: RGBColor | None
    color_temp_kelvin: int | None


def _coerce_int(value: Any) -> int | None:
    """Return ``int(value)`` or ``None`` for a bool/empty/non-numeric value.

    Booleans are rejected explicitly: ``bool`` is an ``int`` subclass, so a
    JSON ``true``/``false`` where firmware should send ``0``/``1`` is treated as
    a malformed field rather than silently coerced to ``1``/``0``.
    """
    if isinstance(value, bool):
        return None
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return None


def _parse_color(value: Any) -> RGBColor | None:
    """Build an :class:`RGBColor` from a ``{r,g,b}`` dict, else ``None``."""
    if not isinstance(value, dict):
        return None
    try:
        return RGBColor.from_dict(value)
    except (TypeError, ValueError, OverflowError):
        return None


def parse_dev_status(payload: Any) -> LanDevStatus | None:
    """Parse a raw LAN ``devStatus`` reply into a :class:`LanDevStatus`.

    Args:
        payload: The decoded datagram, expected to be the whole Govee message
            envelope ``{"msg": {"cmd": "devStatus", "data": {...}}}``.

    Returns:
        A :class:`LanDevStatus` with exactly the four ceiling fields populated,
        or ``None`` when the payload is not a well-formed ``devStatus`` reply.
        ``None`` is returned for a wrong/absent command, a non-dict envelope, a
        partial reply (any of the four fields missing — e.g. the echoed empty
        query ``{"data": {}}``) or an undecodable field value, so a malformed
        datagram can never fabricate or overwrite real device state.
    """
    if not isinstance(payload, dict):
        return None
    msg = payload.get("msg")
    if not isinstance(msg, dict):
        return None
    if msg.get("cmd") != "devStatus":
        return None
    data = msg.get("data")
    if not isinstance(data, dict):
        return None
    # Reject a partial reply outright instead of fabricating ``None`` for the
    # absent fields. The empty-data status *query* (``{"data": {}}``) is the
    # canonical partial datagram this guards against. Extra keys are tolerated.
    if not _DEV_STATUS_FIELDS.issubset(data):
        return None

    on_raw = _coerce_int(data["onOff"])
    brightness = _coerce_int(data["brightness"])
    color = _parse_color(data["color"])
    color_temp_raw = _coerce_int(data["colorTemInKelvin"])

    # A present-but-undecodable field means the datagram is malformed; do not
    # apply it partially.
    if on_raw is None or brightness is None or color is None or color_temp_raw is None:
        return None

    return LanDevStatus(
        on=bool(on_raw),
        brightness_0_100=brightness,
        color=color,
        # ``colorTemInKelvin == 0`` is the firmware's "not in CT mode" sentinel,
        # so it maps to ``None`` rather than a literal 0 K.
        color_temp_kelvin=color_temp_raw if color_temp_raw > 0 else None,
    )


@dataclass(frozen=True)
class LanDeviceInfo:
    """A LAN-correlated device: its coordinator id plus scan-reported metadata.

    ``device_id`` is the coordinator's canonical identifier (the match target);
    ``mac`` is the raw identifier the scan reply reported, retained because its
    formatting can differ from ``device_id``. ``last_correlated_ts`` records when
    the correlation was last confirmed so a later wave can expire stale mappings.
    """

    device_id: str
    ip: str
    mac: str
    sku: str
    firmware: str
    last_correlated_ts: float


def _hex_normalize(value: str) -> str:
    """Reduce an identifier to bare upper-case hex digits for comparison.

    Strips separators (``:``/``-``) and case so ``"03:9c:dc:..."`` and
    ``"039CDC..."`` compare equal. Returns ``""`` for a value with no hex
    content, which never matches.
    """
    return "".join(ch for ch in value.upper() if ch in _HEX_DIGITS)


def _scan_firmware(record: Mapping[str, Any]) -> str:
    """Pick the most relevant firmware string from a scan record.

    Prefers the WiFi software version (most relevant to LAN reachability) and
    falls back through the other version fields a scan reply may carry.
    """
    for key in ("wifiVersionSoft", "bleVersionSoft", "wifiVersionHard", "bleVersionHard"):
        value = record.get(key)
        if value:
            return str(value)
    return ""


def correlate_scan(
    scan_records: Iterable[Mapping[str, Any]],
    device_ids: Iterable[str],
    now: float,
) -> tuple[dict[str, LanDeviceInfo], list[Mapping[str, Any]]]:
    """Correlate LAN scan replies to coordinator device ids.

    Each scan record's ``device`` identifier is matched against the coordinator's
    ``device_ids`` by exact string first, then by a hex-normalized fallback
    (upper-case, all non-hex characters stripped) so differing MAC formatting
    between the scan reply and the developer API still resolves. Group ids
    (:func:`looks_like_group_id`) are skipped — groups are virtual and never
    answer a LAN scan. An IP is NEVER used to guess identity.

    Args:
        scan_records: Decoded LAN scan replies (each a mapping with ``device``,
            ``ip``, ``sku`` and optional firmware fields).
        device_ids: The coordinator's known device identifiers.
        now: Timestamp stamped onto each match's ``last_correlated_ts``.

    Returns:
        A ``(matched, unmatched)`` tuple where ``matched`` maps each correlated
        coordinator ``device_id`` to its :class:`LanDeviceInfo`, and ``unmatched``
        is the list of scan records that matched no device (returned/counted, not
        used) so MAC-format drift is observable without hardware.
    """
    # Exclude groups: they are numeric, virtual, and never answer a LAN scan.
    non_group_ids = [device_id for device_id in device_ids if not looks_like_group_id(device_id)]
    exact_ids = set(non_group_ids)
    normalized_ids: dict[str, str] = {}
    for device_id in non_group_ids:
        normalized = _hex_normalize(device_id)
        # First id wins on the rare normalized collision; keep it deterministic.
        if normalized and normalized not in normalized_ids:
            normalized_ids[normalized] = device_id

    matched: dict[str, LanDeviceInfo] = {}
    unmatched: list[Mapping[str, Any]] = []

    for record in scan_records:
        scan_device = str(record.get("device") or "")
        matched_id: str | None
        if scan_device in exact_ids:
            matched_id = scan_device
        else:
            matched_id = normalized_ids.get(_hex_normalize(scan_device))
        if matched_id is None:
            unmatched.append(record)
            continue
        matched[matched_id] = LanDeviceInfo(
            device_id=matched_id,
            ip=str(record.get("ip") or ""),
            mac=scan_device,
            sku=str(record.get("sku") or ""),
            firmware=_scan_firmware(record),
            last_correlated_ts=now,
        )

    return matched, unmatched


# ---------------------------------------------------------------------------
# Persistent LAN transport client (issue #57, story LAN-008).
#
# Everything above this line is pure and I/O-free. Everything below owns live
# UDP sockets. The two halves share one module because the client is built
# directly on the parsers/correlator above.
# ---------------------------------------------------------------------------


def _build_send_socket() -> socket.socket:
    """Create the dedicated ephemeral UDP send socket this integration owns alone.

    This is the second half of the dual-socket design (story LAN-008). Unlike the
    group-joined :4002 receive socket (``api/lan.py._build_socket``) — which is
    co-bound with Home Assistant's official ``govee_light_local`` via
    ``SO_REUSEPORT`` — this socket binds an OS-assigned EPHEMERAL port that no
    other integration shares. devStatus/control writes are sent from here to
    ``<ip>:4003`` and the firmware unicasts its reply back to this socket's source
    port, so those unicast replies are never load-balanced away to a co-bound
    :4002 listener (which would silently halve our LAN reads — critic blocking #4).

    ``SO_BROADCAST`` lets a configured ``x.x.x.255`` LAN target be reached and the
    multicast TTL lets an outbound datagram cross at most one router hop (matching
    ``_build_socket``). Raises ``OSError`` if the bind fails.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, LAN_MULTICAST_TTL)
    sock.setblocking(False)
    try:
        # Port 0 -> the kernel assigns a free ephemeral port we solely own.
        sock.bind(("", 0))
    except OSError:
        sock.close()
        raise
    return sock


class _RealtimeProtocol(asyncio.DatagramProtocol):
    """Forward every received datagram to an injected callback, keyed by source IP.

    Deliberately dumb: no parsing, filtering or dispatch — it hands the raw
    payload and the datagram's source IP straight to ``on_datagram`` so a single
    instance can serve BOTH of :class:`GoveeLanClient`'s sockets (the group-joined
    :4002 socket carrying multicast scan responses + unsolicited multicast
    devStatus pushes, and the ephemeral send socket carrying the firmware's
    unicast replies). Parsing (``parse_dev_status``) and source-IP dispatch live
    in :meth:`GoveeLanClient._handle_datagram`, which the client injects here as
    ``on_datagram``; keeping this protocol I/O-shaped only means the source-IP key
    — the only device identity a devStatus reply carries — is preserved for the
    handler to correlate.
    """

    def __init__(self, on_datagram: Callable[[str, bytes], None]) -> None:
        """Store the callback invoked for every inbound datagram."""
        self._on_datagram = on_datagram
        # The most recent socket error. asyncio's datagram ``sendto`` never
        # raises OSError; it reports it here instead, usually before returning.
        self.last_error: Exception | None = None

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        """Forward the raw datagram to the callback keyed by its source IP."""
        self._on_datagram(addr[0], data)

    def error_received(self, exc: Exception) -> None:
        """Record and log a transient socket error; the socket stays open."""
        self.last_error = exc
        _LOGGER.debug("LAN realtime socket error: %s", exc)


class _ReadCollector:
    """A single in-flight solicited read awaiting a devStatus reply for one IP.

    A read (``async_read_one`` / one entry of ``async_read_batch``) registers a
    collector under the queried IP. When the persistent dispatch sees a valid
    devStatus from that source IP it calls :meth:`deliver`, which resolves the
    collector's future with the parsed status. The future also lets
    ``async_read_one`` return the instant the reply lands instead of waiting out
    its whole timeout. The FIRST reply wins; later duplicates for the same IP are
    ignored so a chatty device cannot overwrite an already-collected value.
    """

    def __init__(self, loop: asyncio.AbstractEventLoop) -> None:
        """Create the future the queried IP's reply will resolve."""
        self.future: asyncio.Future[LanDevStatus] = loop.create_future()

    def deliver(self, status: LanDevStatus) -> None:
        """Resolve the future with ``status`` unless it is already resolved."""
        if not self.future.done():
            self.future.set_result(status)


class GoveeLanClient:
    """Persistent, coordinator-owned Govee LAN (UDP) transport client (issue #57).

    Story LAN-008 implemented the lifecycle, the dual-socket design and the
    datagram-forwarding protocol. Story LAN-009 adds the read/send surface
    (``async_send_command`` / ``async_read_batch`` / ``async_read_one``) and the
    parse-and-dispatch wiring: every inbound datagram is parsed once via
    ``parse_dev_status`` and, for a VALID devStatus, dispatched keyed by the
    datagram SOURCE IP — the only device identity a devStatus reply carries.

    Source-IP dispatch + solicited/unsolicited split (:meth:`_handle_datagram`):

    - While a solicited read (``async_read_batch`` / ``async_read_one``) is in
      flight for a given IP, that IP's reply is routed to the read's collector and
      is NOT forwarded to the unsolicited push callback (no double-dispatch).
    - A devStatus from an IP with NO read in flight is an unsolicited push (an
      external change) and is delivered to ``on_dev_status`` for the coordinator
      to apply in a later wave.
    - A garbage / non-devStatus / scan datagram is parsed to ``None`` and ignored.

    Dual-socket design (critic blocking #4 — a single co-bound :4002 socket would
    have its unicast devStatus replies load-balanced ~50/50 with HA's official
    ``govee_light_local`` under ``SO_REUSEPORT``, silently halving our reads AND
    stealing half of theirs):

    1. A group-joined :4002 RECEIVE socket (``_build_socket`` + ``_join_group``),
       shared with ``govee_light_local``, used ONLY to receive multicast traffic:
       scan responses and unsolicited multicast devStatus pushes.
    2. A dedicated EPHEMERAL-port SEND socket (``_build_send_socket``) this
       integration solely owns, used to send devStatus/control to ``<ip>:4003``
       and to receive the firmware's UNICAST reply — never shared, never stolen.

    Clean degrade (critic blocking #5): if the :4002 socket cannot be bound (held
    by another local-control app without ``SO_REUSEPORT``), :meth:`async_start`
    catches the ``OSError``, logs once, and leaves the client DISABLED
    (:attr:`available` stays ``False``). It NEVER raises into config-entry setup;
    the caller treats a disabled client as "no LAN" and sets
    ``coordinator._lan_client = None`` so MQTT/REST are untouched.

    Usage::

        client = GoveeLanClient(on_dev_status)
        await client.async_start(interface_ips)
        if client.available:
            statuses = await client.async_read_batch(["10.0.0.5"])
            ok = await client.async_send_command("10.0.0.5", "turn", {"value": 1})
        await client.async_stop()
    """

    def __init__(self, on_dev_status: Callable[[str, LanDevStatus], None]) -> None:
        """Initialize the client.

        Args:
            on_dev_status: Synchronous callback ``(source_ip, status)`` invoked for
                every UNSOLICITED devStatus push — a valid devStatus from an IP
                with no solicited read in flight. The client parses every datagram
                itself; the callback receives only parsed, already-correlated
                pushes (the coordinator wires this in a later wave to apply
                real-time external-change pushes). Replies that satisfy an
                in-flight read are routed to that read instead and never fire this
                callback.
        """
        self._on_dev_status = on_dev_status
        self._available = False
        self._recv_sock: socket.socket | None = None
        self._send_sock: socket.socket | None = None
        self._recv_transport: asyncio.DatagramTransport | None = None
        self._send_transport: asyncio.DatagramTransport | None = None
        self._send_protocol: _RealtimeProtocol | None = None
        self._joined: list[str] = []
        # Solicited reads in flight, keyed by queried source IP. A list per IP
        # tolerates a batch read and a one-off read overlapping on the same IP;
        # both collectors receive the reply. Empty when no read is outstanding.
        self._pending_reads: dict[str, list[_ReadCollector]] = {}
        # Collects scan replies while :meth:`async_scan` is running.
        self._scan_collector: _ScanProtocol | None = None

    @property
    def available(self) -> bool:
        """Return ``True`` only when both sockets are bound and usable.

        The caller checks this after :meth:`async_start`; ``False`` means the LAN
        transport degraded cleanly (see the class docstring) and every LAN path
        must be skipped.
        """
        return self._available

    async def async_start(self, interface_ips: list[str]) -> None:
        """Bind both sockets + join the multicast group, or degrade cleanly.

        Builds the group-joined :4002 receive socket and the dedicated ephemeral
        send socket, attaches a :class:`_RealtimeProtocol` to each, and joins the
        ``239.255.255.250`` group on every interface so multicast scan responses
        and pushes are received. On ANY ``OSError`` — most importantly the :4002
        bind failing because another local-control app holds it without
        ``SO_REUSEPORT`` — the client is left DISABLED and this method NEVER
        raises, so config-entry setup is unaffected (critic blocking #5).

        Args:
            interface_ips: The host's enabled LAN source IPs (from
                ``async_get_lan_interface_ips``). The group is joined on each plus
                the default route; an empty list means default-route only.

        Idempotent: a second call while already available is a no-op.
        """
        if self._available:
            return

        loop = asyncio.get_running_loop()
        interfaces = list(interface_ips or [])

        try:
            recv_sock = _build_socket()
        except OSError as err:
            _LOGGER.warning(
                "Govee LAN transport disabled: could not bind UDP :%d — another "
                "local-control app (e.g. govee_light_local) may hold it without "
                "port sharing. Falling back to MQTT/REST. (%s)",
                LAN_RESPONSE_PORT,
                err,
            )
            return

        try:
            send_sock = _build_send_socket()
        except OSError as err:
            _LOGGER.warning("Govee LAN transport disabled: could not bind send socket: %s", err)
            recv_sock.close()
            return

        joined = _join_group(recv_sock, interfaces)

        recv_transport: asyncio.DatagramTransport | None = None
        try:
            recv_transport, _ = await loop.create_datagram_endpoint(
                lambda: _RealtimeProtocol(self._handle_datagram), sock=recv_sock
            )
            send_transport, send_protocol = await loop.create_datagram_endpoint(
                lambda: _RealtimeProtocol(self._handle_datagram), sock=send_sock
            )
        except OSError as err:
            _LOGGER.warning("Govee LAN transport disabled: could not attach datagram endpoint: %s", err)
            _drop_group(recv_sock, joined)
            if recv_transport is not None:
                # Unregister it from the loop; closing it also closes recv_sock.
                recv_transport.close()
            recv_sock.close()
            send_sock.close()
            return

        self._recv_sock = recv_sock
        self._send_sock = send_sock
        self._recv_transport = recv_transport
        self._send_transport = send_transport
        self._send_protocol = send_protocol if isinstance(send_protocol, _RealtimeProtocol) else None
        self._joined = joined
        self._available = True
        _LOGGER.debug(
            "Govee LAN transport started (recv :%d, group joined on %s; dedicated ephemeral send socket)",
            LAN_RESPONSE_PORT,
            joined,
        )

    async def async_stop(self) -> None:
        """Drop the multicast group and close BOTH sockets, idempotently.

        Safe to call when the client never started or already stopped: every step
        is guarded so a partially-built or fully-torn-down client is a no-op.
        Closing each transport closes its underlying socket, releasing the :4002
        bind so a reload/unload never leaks it.
        """
        self._available = False

        if self._recv_sock is not None and self._joined:
            _drop_group(self._recv_sock, self._joined)
        self._joined = []

        if self._recv_transport is not None:
            self._recv_transport.close()
            self._recv_transport = None
        if self._send_transport is not None:
            self._send_transport.close()
            self._send_transport = None
        self._send_protocol = None

        self._recv_sock = None
        self._send_sock = None

    # ------------------------------------------------------------------ #
    # Parse-and-dispatch (the persistent receive path, story LAN-009)
    # ------------------------------------------------------------------ #

    def _handle_datagram(self, src_ip: str, raw: bytes) -> None:
        """Parse one inbound datagram and dispatch a valid devStatus by source IP.

        Injected as the ``on_datagram`` callback of BOTH sockets'
        :class:`_RealtimeProtocol`, so it sees every datagram the client receives.
        The datagram is JSON-decoded and run through ``parse_dev_status``; anything
        that is not a well-formed devStatus reply (scan responses, ``status``
        replies, the empty-query echo, garbage bytes) parses to ``None`` and is
        silently ignored.

        A valid devStatus is dispatched keyed by ``src_ip`` — the only device
        identity a devStatus carries. If a solicited read is in flight for that IP
        the reply belongs to it and is routed there WITHOUT also firing the
        unsolicited push callback (no double-dispatch); otherwise it is an
        unsolicited push delivered to ``on_dev_status``.

        Args:
            src_ip: The datagram's source IP address.
            raw: The raw datagram bytes.
        """
        if self._scan_collector is not None:
            # A rescan is running on this socket; its collector keeps only
            # well-formed scan replies and ignores everything else.
            self._scan_collector.datagram_received(raw, (src_ip, LAN_RESPONSE_PORT))
        try:
            payload = json.loads(raw.decode("utf-8", errors="replace"))
        except (ValueError, RecursionError):
            return  # undecodable or absurdly nested bytes — not a devStatus, ignore
        status = parse_dev_status(payload)
        if status is None:
            return  # scan/status/garbage/partial — never fabricate state

        collectors = self._pending_reads.get(src_ip)
        if collectors:
            # Solicited: route to the in-flight read(s) for this IP and do NOT
            # also fire the unsolicited push callback (avoids double-dispatch).
            for collector in list(collectors):
                collector.deliver(status)
            return
        # Unsolicited push: an external change with no read awaiting this IP.
        self._on_dev_status(src_ip, status)

    def _register_read(self, ip: str, collector: _ReadCollector) -> None:
        """Mark ``collector`` as awaiting a solicited reply from ``ip``."""
        self._pending_reads.setdefault(ip, []).append(collector)

    def _unregister_read(self, ip: str, collector: _ReadCollector) -> None:
        """Remove ``collector`` from ``ip``'s in-flight list, pruning empties."""
        collectors = self._pending_reads.get(ip)
        if not collectors:
            return
        if collector in collectors:
            collectors.remove(collector)
        if not collectors:
            del self._pending_reads[ip]

    # ------------------------------------------------------------------ #
    # Public read/send surface (story LAN-009)
    # ------------------------------------------------------------------ #

    async def async_send_command(self, ip: str, cmd: str, data: dict[str, Any]) -> bool:
        """Fire-and-forget a single LAN command at ``<ip>:4003``.

        Builds ``{"msg": {"cmd": cmd, "data": data}}`` and sends it from the
        DEDICATED EPHEMERAL send socket — never the shared :4002 receive socket —
        so the firmware's unicast reply comes back to a port this integration
        solely owns (critic blocking #4). This is fire-and-forget: it does not
        wait for or confirm a reply.

        Args:
            ip: The device's LAN IP address.
            cmd: The Govee LAN command verb (e.g. ``"devStatus"``, ``"turn"``).
            data: The command's ``data`` payload (``{}`` for a status query).

        Returns:
            ``True`` iff the datagram was handed to the socket without an
            ``OSError``; ``False`` (logged at debug) when the client is
            unavailable or the send failed, whether ``sendto`` raised or (as
            asyncio does) reported it through ``error_received``.
        """
        if not self._available or self._send_transport is None:
            return False
        message = json.dumps({"msg": {"cmd": cmd, "data": data}}).encode("utf-8")
        protocol = self._send_protocol
        if protocol is not None:
            protocol.last_error = None
        try:
            self._send_transport.sendto(message, (ip, LAN_COMMAND_PORT))
        except OSError as err:
            _LOGGER.debug("LAN send %s to %s:%d failed: %s", cmd, ip, LAN_COMMAND_PORT, err)
            return False
        # asyncio reports an immediate send failure through error_received
        # instead of raising, so check what it recorded during this send.
        if protocol is not None and protocol.last_error is not None:
            _LOGGER.debug("LAN send %s to %s:%d failed: %s", cmd, ip, LAN_COMMAND_PORT, protocol.last_error)
            return False
        return True

    async def async_scan(
        self,
        *,
        interface_ips: list[str],
        extra_targets: list[str],
        broadcast_targets: list[str],
        timeout: float = 2.0,
    ) -> list[dict[str, Any]]:
        """Run a discovery scan through this client's own :4002 socket.

        Same request, targets and result shape as
        :func:`~.lan.async_scan_lan_devices`, but without binding a second
        :4002 socket beside the persistent one. With two of our sockets in the
        same ``SO_REUSEPORT`` group, Linux hashes each unicast reply to one of
        them, so cross-VLAN scan replies could land on the persistent socket
        and be dropped, and devStatus replies could land on the scan socket.

        An overlapping call while a scan is running returns an empty list.

        Raises:
            OSError: The client is not running (callers treat it as no data).
        """
        if not self._available or self._recv_sock is None or self._recv_transport is None:
            raise OSError("Govee LAN client is not running")
        if self._scan_collector is not None:
            # One scan at a time: a second would replace the running scan's
            # collector and that scan would lose its later replies.
            _LOGGER.debug("Govee LAN scan already running; skipping overlapping scan")
            return []
        collector = _ScanProtocol()
        self._scan_collector = collector
        try:
            _send_scan(self._recv_sock, self._recv_transport, list(interface_ips))
            _send_targets(self._recv_transport, list(extra_targets))
            _send_targets(self._recv_transport, list(broadcast_targets))
            await asyncio.sleep(timeout)
        finally:
            self._scan_collector = None
        return list(collector.responses.values())

    async def async_read_one(self, ip: str, timeout: float = LAN_WRITE_CONFIRM_TIMEOUT) -> LanDevStatus | None:
        """Send one devStatus query to ``ip`` and await a parsed reply from it.

        Registers a collector for ``ip`` BEFORE sending so a fast reply is never
        missed, sends the query, and waits up to ``timeout`` for the dispatch to
        resolve the collector with a parsed devStatus from that same IP. Used by
        the coordinator's verify-by-read write confirm (a later wave).

        Args:
            ip: The device's LAN IP address.
            timeout: Seconds to wait for the reply before giving up.

        Returns:
            The parsed :class:`LanDevStatus` from ``ip``, or ``None`` on timeout,
            send failure, or when the client is unavailable.
        """
        if not self._available:
            return None
        loop = asyncio.get_running_loop()
        collector = _ReadCollector(loop)
        self._register_read(ip, collector)
        try:
            if not await self.async_send_command(ip, "devStatus", {}):
                return None
            return await asyncio.wait_for(collector.future, timeout)
        except asyncio.TimeoutError:
            return None
        finally:
            self._unregister_read(ip, collector)

    async def async_read_batch(self, ips: list[str], window: float = LAN_READ_WINDOW) -> dict[str, LanDevStatus]:
        """Query every IP once and collect replies over ONE shared window.

        Sends a devStatus query to each unique IP, then collects replies for a
        single bounded ``window`` shared across all of them, so total wall time is
        bounded by ``window`` regardless of ``len(ips)``. Each reply is routed by
        source IP to its collector by the persistent dispatch; only IPs that
        answered with a valid devStatus appear in the result.

        Args:
            ips: The LAN IPs to query (duplicates are de-duplicated).
            window: Seconds to collect replies (shared across all IPs).

        Returns:
            ``{source_ip: LanDevStatus}`` for the IPs that answered within the
            window. Empty when the client is unavailable or ``ips`` is empty.
        """
        result: dict[str, LanDevStatus] = {}
        if not self._available or not ips:
            return result

        loop = asyncio.get_running_loop()
        collectors: dict[str, _ReadCollector] = {}
        for ip in ips:
            if ip in collectors:
                continue  # de-dupe: one collector/registration per IP
            collector = _ReadCollector(loop)
            collectors[ip] = collector
            self._register_read(ip, collector)

        try:
            for ip in collectors:
                await self.async_send_command(ip, "devStatus", {})
            # One shared window: wait the full duration so the wall time is bounded
            # by ``window`` and independent of how many IPs reply (or don't).
            await asyncio.sleep(window)
            for ip, collector in collectors.items():
                if collector.future.done():
                    result[ip] = collector.future.result()
        finally:
            for ip, collector in collectors.items():
                self._unregister_read(ip, collector)
        return result
