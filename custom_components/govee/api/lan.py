"""Read-only Govee LAN (UDP) discovery helper.

Govee exposes a local UDP control API (must be toggled on per device in the
Govee app) on a subset of mostly-light SKUs. This module performs ONLY the
discovery half — a single bounded multicast ``scan`` — so a user can capture
which of their devices answer on the LAN and what they report, and attach it to
a diagnostics download. That community data is the prerequisite for the full
LAN transport requested in issue #57 (the maintainer has no LAN hardware to
test against).

Deliberately scoped: no control commands, no entities, no persistent socket —
one scan, collect responses for a short timeout, return them. Protocol per
``docs/govee-protocol-reference.md`` §6:

- Scan request  -> 239.255.255.250:4001  ``{"msg":{"cmd":"scan",...}}``
- Scan response -> client:4002            ``{"msg":{"cmd":"scan","data":{...}}}``
"""

from __future__ import annotations

import asyncio
import json
import logging
import socket
from typing import Any

_LOGGER = logging.getLogger(__name__)

# Govee LAN API network parameters (docs/govee-protocol-reference.md §6).
LAN_MULTICAST_GROUP = "239.255.255.250"
LAN_DISCOVERY_PORT = 4001  # devices listen here for the scan request
LAN_RESPONSE_PORT = 4002  # we listen here for scan responses

_SCAN_REQUEST = json.dumps(
    {"msg": {"cmd": "scan", "data": {"account_topic": "reserve"}}}
).encode("utf-8")

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
        # Dedupe by the device id (MAC) when present, else by source IP.
        key = str(record.get("device") or addr[0])
        self.responses[key] = record

    def error_received(self, exc: Exception) -> None:  # pragma: no cover - rare
        _LOGGER.debug("LAN scan socket error: %s", exc)


async def async_scan_lan_devices(timeout: float = 2.0) -> list[dict[str, Any]]:
    """Send one multicast ``scan`` and collect responses for ``timeout`` seconds.

    Returns a list of per-device dicts (deduped) limited to the discovery fields
    above — no control surface is touched. Uses asyncio datagram endpoints so it
    is safe to run on the Home Assistant event loop.

    Raises ``OSError`` if the response socket cannot be bound (e.g. port 4002 in
    use by another local-control app); callers should treat that as "no data".
    """
    loop = asyncio.get_running_loop()

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    # Multicast send TTL of 2 so a scan can cross one router hop if present.
    sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
    sock.setblocking(False)
    try:
        sock.bind(("", LAN_RESPONSE_PORT))
    except OSError:
        sock.close()
        raise

    transport, protocol = await loop.create_datagram_endpoint(
        _ScanProtocol, sock=sock
    )
    assert isinstance(protocol, _ScanProtocol)
    try:
        transport.sendto(_SCAN_REQUEST, (LAN_MULTICAST_GROUP, LAN_DISCOVERY_PORT))
        await asyncio.sleep(timeout)
    finally:
        transport.close()

    return list(protocol.responses.values())
