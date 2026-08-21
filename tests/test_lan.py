"""Tests for the read-only Govee LAN discovery helper (issue #57)."""

from __future__ import annotations

import json
from ipaddress import IPv4Address, IPv6Address
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.govee.api import lan


def _install_fake_endpoint(monkeypatch, responses, *, bind_error=None):
    """Patch socket + event loop so async_scan_lan_devices runs offline.

    Returns the MagicMock transport so tests can assert on sendto. ``responses``
    are fed to the real _ScanProtocol during the (stubbed) scan wait.
    """
    sock = MagicMock()
    if bind_error is not None:
        sock.bind.side_effect = bind_error
    monkeypatch.setattr(lan.socket, "socket", lambda *a, **k: sock)

    proto = lan._ScanProtocol()
    transport = MagicMock()

    async def _create_endpoint(_factory, sock=None):
        return transport, proto

    mock_loop = MagicMock()
    mock_loop.create_datagram_endpoint = _create_endpoint
    monkeypatch.setattr(lan.asyncio, "get_running_loop", lambda: mock_loop)

    async def _sleep(_timeout):
        for resp in responses:
            proto.datagram_received(json.dumps(resp).encode("utf-8"), ("192.168.1.50", 4002))

    monkeypatch.setattr(lan.asyncio, "sleep", _sleep)
    return transport, sock


def _scan_response(**data):
    return {"msg": {"cmd": "scan", "data": data}}


def _install_fake_probe_endpoint(monkeypatch, replies, *, bind_error=None):
    """Patch socket + loop so async_probe_lan_raw runs offline.

    ``replies`` is a list of ``(source_ip, raw_bytes)`` fed to the real
    _RawProbeProtocol during the stubbed collection window. ``raw_bytes`` is sent
    verbatim so tests can feed any cmd, malformed JSON, or garbage. Returns the
    MagicMock transport for sendto assertions.
    """
    sock = MagicMock()
    if bind_error is not None:
        sock.bind.side_effect = bind_error
    monkeypatch.setattr(lan.socket, "socket", lambda *a, **k: sock)

    proto = lan._RawProbeProtocol()
    transport = MagicMock()

    async def _create_endpoint(_factory, sock=None):
        return transport, proto

    mock_loop = MagicMock()
    mock_loop.create_datagram_endpoint = _create_endpoint
    monkeypatch.setattr(lan.asyncio, "get_running_loop", lambda: mock_loop)

    async def _sleep(_timeout):
        for source_ip, raw in replies:
            proto.datagram_received(raw, (source_ip, lan.LAN_RESPONSE_PORT))

    monkeypatch.setattr(lan.asyncio, "sleep", _sleep)
    return transport, sock


def _pkt(cmd, **data):
    """A raw datagram for ``cmd`` with ``data`` body."""
    return json.dumps({"msg": {"cmd": cmd, "data": data}}).encode("utf-8")


@pytest.mark.asyncio
async def test_sends_scan_request_to_multicast(monkeypatch):
    transport, _ = _install_fake_endpoint(monkeypatch, [])

    await lan.async_scan_lan_devices(timeout=0.01)

    transport.sendto.assert_called_once()
    payload, addr = transport.sendto.call_args[0]
    assert addr == (lan.LAN_MULTICAST_GROUP, lan.LAN_DISCOVERY_PORT)
    sent = json.loads(payload.decode())
    assert sent == {"msg": {"cmd": "scan", "data": {"account_topic": "reserve"}}}


@pytest.mark.asyncio
async def test_parses_and_returns_devices(monkeypatch):
    responses = [
        _scan_response(
            ip="192.168.1.23",
            device="1F:80:C5:32:32:36:72:4E",
            sku="H6072",
            wifiVersionSoft="1.02.03",
        )
    ]
    _install_fake_endpoint(monkeypatch, responses)

    devices = await lan.async_scan_lan_devices(timeout=0.01)

    assert len(devices) == 1
    assert devices[0]["sku"] == "H6072"
    assert devices[0]["ip"] == "192.168.1.23"
    assert devices[0]["device"] == "1F:80:C5:32:32:36:72:4E"
    assert devices[0]["wifiVersionSoft"] == "1.02.03"


@pytest.mark.asyncio
async def test_dedupes_by_device_id(monkeypatch):
    dup = _scan_response(ip="192.168.1.23", device="AA:BB:CC:DD:EE:FF:00:11", sku="H6072")
    _install_fake_endpoint(monkeypatch, [dup, dup])

    devices = await lan.async_scan_lan_devices(timeout=0.01)

    assert len(devices) == 1


@pytest.mark.asyncio
async def test_no_responses_returns_empty(monkeypatch):
    _install_fake_endpoint(monkeypatch, [])

    devices = await lan.async_scan_lan_devices(timeout=0.01)

    assert devices == []


@pytest.mark.asyncio
async def test_ignores_malformed_and_non_scan(monkeypatch):
    proto = lan._ScanProtocol()
    # Garbage bytes.
    proto.datagram_received(b"not-json", ("192.168.1.9", 4002))
    # Valid JSON but a different command.
    proto.datagram_received(
        json.dumps({"msg": {"cmd": "devStatus", "data": {"onOff": 1}}}).encode(),
        ("192.168.1.9", 4002),
    )
    # scan but empty data -> no usable record.
    proto.datagram_received(json.dumps(_scan_response()).encode(), ("192.168.1.9", 4002))

    assert proto.responses == {}


@pytest.mark.asyncio
async def test_bind_failure_raises_oserror(monkeypatch):
    _install_fake_endpoint(monkeypatch, [], bind_error=OSError("port in use"))

    with pytest.raises(OSError):
        await lan.async_scan_lan_devices(timeout=0.01)


def _setsockopt_args(sock):
    """All (level, optname, value) tuples passed to the mock socket."""
    return [call.args for call in sock.setsockopt.call_args_list]


@pytest.mark.asyncio
async def test_joins_multicast_group(monkeypatch):
    # The fix for #57: device replies are multicast to 239.255.255.250:4002, so
    # the receive socket MUST join the group or the kernel drops every reply.
    _, sock = _install_fake_endpoint(monkeypatch, [])

    await lan.async_scan_lan_devices(timeout=0.01)

    joins = [
        value
        for level, name, value in _setsockopt_args(sock)
        if level == lan.socket.IPPROTO_IP and name == lan.socket.IP_ADD_MEMBERSHIP
    ]
    assert joins, "scan socket never joined the multicast group"
    # mreq = group bytes + default-interface (INADDR_ANY) bytes.
    assert joins[0] == lan._GROUP_BYTES + lan.socket.inet_aton("0.0.0.0")


@pytest.mark.asyncio
async def test_sets_reuse_and_ttl_options(monkeypatch):
    _, sock = _install_fake_endpoint(monkeypatch, [])

    await lan.async_scan_lan_devices(timeout=0.01)

    names = [name for _level, name, _value in _setsockopt_args(sock)]
    assert lan.socket.SO_REUSEADDR in names
    assert lan.socket.IP_MULTICAST_TTL in names


@pytest.mark.asyncio
async def test_multi_interface_joins_and_sends_per_nic(monkeypatch):
    transport, sock = _install_fake_endpoint(monkeypatch, [])

    await lan.async_scan_lan_devices(timeout=0.01, interface_ips=["192.168.1.50", "10.0.0.5"])

    # One scan emitted per interface (no extra default send when NICs given).
    assert transport.sendto.call_count == 2
    addrs = {call.args[1] for call in transport.sendto.call_args_list}
    assert addrs == {(lan.LAN_MULTICAST_GROUP, lan.LAN_DISCOVERY_PORT)}

    # Group joined on each interface plus the default-route catch-all.
    joins = {value for _level, name, value in _setsockopt_args(sock) if name == lan.socket.IP_ADD_MEMBERSHIP}
    assert joins == {lan._GROUP_BYTES + lan.socket.inet_aton(ip) for ip in ("192.168.1.50", "10.0.0.5", "0.0.0.0")}


@pytest.mark.asyncio
async def test_fills_ip_from_source_when_missing(monkeypatch):
    # A response that omits "ip" gets the datagram source address (192.168.1.50).
    resp = _scan_response(device="AA:BB:CC:DD:EE:FF:00:11", sku="H6072")
    _install_fake_endpoint(monkeypatch, [resp])

    devices = await lan.async_scan_lan_devices(timeout=0.01)

    assert devices[0]["ip"] == "192.168.1.50"


@pytest.mark.asyncio
async def test_leaves_group_on_teardown(monkeypatch):
    _, sock = _install_fake_endpoint(monkeypatch, [])

    await lan.async_scan_lan_devices(timeout=0.01)

    drops = [name for _level, name, _value in _setsockopt_args(sock) if name == lan.socket.IP_DROP_MEMBERSHIP]
    assert drops, "scan socket never left the multicast group on teardown"


@pytest.mark.asyncio
async def test_sets_broadcast_option(monkeypatch):
    # SO_BROADCAST lets a configured x.x.x.255 LAN target be reached (#57).
    _, sock = _install_fake_endpoint(monkeypatch, [])

    await lan.async_scan_lan_devices(timeout=0.01)

    names = [name for _level, name, _value in _setsockopt_args(sock)]
    assert lan.socket.SO_BROADCAST in names


@pytest.mark.asyncio
async def test_sends_scan_to_extra_targets(monkeypatch):
    transport, _ = _install_fake_endpoint(monkeypatch, [])

    await lan.async_scan_lan_devices(timeout=0.01, extra_targets=["10.20.0.51", "10.20.0.255"])

    # One multicast (default interface) plus one send per explicit target.
    addrs = [call.args[1] for call in transport.sendto.call_args_list]
    assert (lan.LAN_MULTICAST_GROUP, lan.LAN_DISCOVERY_PORT) in addrs
    assert ("10.20.0.51", lan.LAN_DISCOVERY_PORT) in addrs
    assert ("10.20.0.255", lan.LAN_DISCOVERY_PORT) in addrs
    assert len(addrs) == 3


@pytest.mark.asyncio
async def test_sends_scan_to_broadcast_targets(monkeypatch):
    # Newer devices ignore multicast but answer a directed broadcast (#57): the
    # scan must also be emitted to each auto-derived subnet broadcast address.
    transport, _ = _install_fake_endpoint(monkeypatch, [])

    await lan.async_scan_lan_devices(timeout=0.01, broadcast_targets=["192.168.0.255"])

    addrs = [call.args[1] for call in transport.sendto.call_args_list]
    assert (lan.LAN_MULTICAST_GROUP, lan.LAN_DISCOVERY_PORT) in addrs
    assert ("192.168.0.255", lan.LAN_DISCOVERY_PORT) in addrs
    assert len(addrs) == 2


# --- reality probe: _RawProbeProtocol capture ------------------------------


def test_rawprobe_captures_all_payloads_keyed_by_source_ip():
    # Every datagram is kept verbatim, as a list, keyed by source IP.
    proto = lan._RawProbeProtocol()
    proto.datagram_received(_pkt("devStatus", onOff=1, brightness=80), ("192.168.1.23", 4002))
    proto.datagram_received(_pkt("status", pt="MwUE..."), ("192.168.1.23", 4002))
    proto.datagram_received(_pkt("devStatus", onOff=0), ("192.168.1.24", 4002))

    assert set(proto.replies) == {"192.168.1.23", "192.168.1.24"}
    assert len(proto.replies["192.168.1.23"]) == 2
    cmds = {r["msg"]["cmd"] for r in proto.replies["192.168.1.23"]}
    assert cmds == {"devStatus", "status"}


def test_rawprobe_keeps_unknown_cmds_and_fields():
    # The whole point: nothing is filtered by cmd or field — reality, not a lib's
    # idea of reality.
    proto = lan._RawProbeProtocol()
    proto.datagram_received(_pkt("mysteryCmd", undocumented={"a": 1}, sensor=42), ("10.0.0.5", 4002))

    body = proto.replies["10.0.0.5"][0]["msg"]
    assert body["cmd"] == "mysteryCmd"
    assert body["data"]["undocumented"] == {"a": 1}
    assert body["data"]["sensor"] == 42


def test_rawprobe_captures_undecodable_as_unparsed():
    # Even garbage is signal — captured, not dropped.
    proto = lan._RawProbeProtocol()
    proto.datagram_received(b"\xff\xfenot-json", ("10.0.0.5", 4002))

    captured = proto.replies["10.0.0.5"][0]
    assert "_unparsed" in captured


def test_rawprobe_caps_replies_per_ip():
    proto = lan._RawProbeProtocol()
    for i in range(lan.LAN_PROBE_MAX_REPLIES_PER_IP + 5):
        proto.datagram_received(_pkt("devStatus", n=i), ("10.0.0.5", 4002))

    assert len(proto.replies["10.0.0.5"]) == lan.LAN_PROBE_MAX_REPLIES_PER_IP


# --- reality probe: async_probe_lan_raw ------------------------------------


@pytest.mark.asyncio
async def test_rawprobe_sends_full_command_battery_to_each_ip(monkeypatch):
    transport, _ = _install_fake_probe_endpoint(monkeypatch, [])

    await lan.async_probe_lan_raw(["10.0.0.5", "10.0.0.6"], timeout=0.01)

    sent = [(json.loads(c.args[0].decode())["msg"]["cmd"], c.args[1]) for c in transport.sendto.call_args_list]
    # Every read-only command, to its port, for each IP — and NO write verbs.
    assert ("devStatus", ("10.0.0.5", lan.LAN_COMMAND_PORT)) in sent
    assert ("status", ("10.0.0.5", lan.LAN_COMMAND_PORT)) in sent
    assert ("scan", ("10.0.0.5", lan.LAN_DISCOVERY_PORT)) in sent
    assert len(sent) == len(lan.LAN_PROBE_COMMANDS) * 2
    cmds_sent = {cmd for cmd, _addr in sent}
    assert cmds_sent.isdisjoint({"turn", "brightness", "colorwc", "ptReal"})


@pytest.mark.asyncio
async def test_rawprobe_returns_replies_keyed_by_ip(monkeypatch):
    _install_fake_probe_endpoint(
        monkeypatch,
        [
            ("10.0.0.5", _pkt("devStatus", onOff=1, brightness=42)),
            ("10.0.0.5", _pkt("status", pt="MwUE...")),
        ],
    )

    result = await lan.async_probe_lan_raw(["10.0.0.5"], timeout=0.01)

    assert set(result) == {"10.0.0.5"}
    assert len(result["10.0.0.5"]) == 2


@pytest.mark.asyncio
async def test_rawprobe_empty_ips_returns_empty_no_socket(monkeypatch):
    called = MagicMock()
    monkeypatch.setattr(lan.socket, "socket", called)

    result = await lan.async_probe_lan_raw([], timeout=0.01)

    assert result == {}
    called.assert_not_called()


@pytest.mark.asyncio
async def test_rawprobe_caps_device_count(monkeypatch):
    transport, _ = _install_fake_probe_endpoint(monkeypatch, [])
    ips = [f"10.0.0.{i}" for i in range(lan.LAN_PROBE_MAX_DEVICES + 10)]

    await lan.async_probe_lan_raw(ips, timeout=0.01)

    # One send per command per (capped) IP.
    assert transport.sendto.call_count == lan.LAN_PROBE_MAX_DEVICES * len(lan.LAN_PROBE_COMMANDS)


@pytest.mark.asyncio
async def test_rawprobe_bind_failure_raises_oserror(monkeypatch):
    _install_fake_probe_endpoint(monkeypatch, [], bind_error=OSError("port in use"))

    with pytest.raises(OSError):
        await lan.async_probe_lan_raw(["10.0.0.5"], timeout=0.01)


@pytest.mark.asyncio
async def test_rawprobe_one_send_failure_does_not_abort_batch(monkeypatch):
    transport, _ = _install_fake_probe_endpoint(monkeypatch, [("10.0.0.6", _pkt("devStatus", onOff=1))])
    # First send raises; every subsequent send still attempted.
    transport.sendto.side_effect = [OSError("unreachable")] + [None] * 10

    result = await lan.async_probe_lan_raw(["10.0.0.5", "10.0.0.6"], timeout=0.01)

    assert transport.sendto.call_count == len(lan.LAN_PROBE_COMMANDS) * 2
    assert result == {"10.0.0.6": [{"msg": {"cmd": "devStatus", "data": {"onOff": 1}}}]}


@pytest.mark.asyncio
async def test_rawprobe_custom_commands_are_honored(monkeypatch):
    # The battery is overridable (e.g. to add a future read command).
    transport, _ = _install_fake_probe_endpoint(monkeypatch, [])

    await lan.async_probe_lan_raw(["10.0.0.5"], timeout=0.01, commands=(("devInfo", 4001, {}),))

    sent = [(json.loads(c.args[0].decode())["msg"]["cmd"], c.args[1]) for c in transport.sendto.call_args_list]
    assert sent == [("devInfo", ("10.0.0.5", 4001))]


@pytest.mark.asyncio
async def test_rawprobe_joins_multicast_group(monkeypatch):
    # Replies can arrive multicast, so the probe socket must join the group too.
    _, sock = _install_fake_probe_endpoint(monkeypatch, [])

    await lan.async_probe_lan_raw(["10.0.0.5"], timeout=0.01, interface_ips=["192.168.1.50"])

    joins = [
        value
        for level, name, value in _setsockopt_args(sock)
        if level == lan.socket.IPPROTO_IP and name == lan.socket.IP_ADD_MEMBERSHIP
    ]
    assert joins, "probe socket never joined the multicast group"


# --- expand_lan_targets ----------------------------------------------------


@pytest.mark.parametrize("raw", ["", None, "   ", "\n,\n"])
def test_expand_lan_targets_empty(raw):
    assert lan.expand_lan_targets(raw) == []


def test_expand_lan_targets_single_ip():
    assert lan.expand_lan_targets("10.20.0.51") == ["10.20.0.51"]


def test_expand_lan_targets_mixed_separators_and_dedupe():
    raw = "10.20.0.5,10.20.0.6 10.20.0.7\n10.20.0.5"
    assert lan.expand_lan_targets(raw) == [
        "10.20.0.5",
        "10.20.0.6",
        "10.20.0.7",
    ]


def test_expand_lan_targets_small_cidr_sweeps_hosts_plus_broadcast():
    # /30 -> usable hosts .1/.2 plus the .3 broadcast.
    assert lan.expand_lan_targets("10.20.0.0/30") == [
        "10.20.0.1",
        "10.20.0.2",
        "10.20.0.3",
    ]


def test_expand_lan_targets_24_is_allowed():
    targets = lan.expand_lan_targets("10.20.0.0/24")
    assert len(targets) == 255  # 254 hosts + broadcast
    assert targets[0] == "10.20.0.1"
    assert targets[-1] == "10.20.0.255"


def test_expand_lan_targets_rejects_large_subnet():
    with pytest.raises(lan.LanTargetError):
        lan.expand_lan_targets("10.20.0.0/16")


def test_expand_lan_targets_rejects_garbage():
    with pytest.raises(lan.LanTargetError):
        lan.expand_lan_targets("not-an-ip")


def test_expand_lan_targets_skips_device_overrides():
    # A device_id=ip[!] override (#164) is not a scan target — expand_lan_targets
    # must not try to parse it as an IP/subnet and must not raise on it.
    raw = "10.20.0.5 11:66:C0:EB:32:C1:19:FC=192.168.3.210!"
    assert lan.expand_lan_targets(raw) == ["10.20.0.5"]


# --- parse_lan_device_overrides ---------------------------------------------


@pytest.mark.parametrize("raw", ["", None, "   "])
def test_parse_lan_device_overrides_empty(raw):
    assert lan.parse_lan_device_overrides(raw) == {}


def test_parse_lan_device_overrides_plain_ip():
    # No trailing "!" -> normal read/write/health treatment, not write-only.
    raw = "11:66:C0:EB:32:C1:19:FC=192.168.3.210"
    assert lan.parse_lan_device_overrides(raw) == {
        "11:66:C0:EB:32:C1:19:FC": ("192.168.3.210", False),
    }


def test_parse_lan_device_overrides_write_only_marker():
    raw = "11:66:C0:EB:32:C1:19:FC=192.168.3.210!"
    assert lan.parse_lan_device_overrides(raw) == {
        "11:66:C0:EB:32:C1:19:FC": ("192.168.3.210", True),
    }


def test_parse_lan_device_overrides_multiple_and_mixed_with_plain_targets():
    raw = "10.20.0.5, AA:BB=10.20.0.6!, CC:DD=10.20.0.7"
    assert lan.parse_lan_device_overrides(raw) == {
        "AA:BB": ("10.20.0.6", True),
        "CC:DD": ("10.20.0.7", False),
    }
    # And the plain IP is still picked up by expand_lan_targets, unaffected.
    assert lan.expand_lan_targets(raw) == ["10.20.0.5"]


def test_parse_lan_device_overrides_later_duplicate_wins():
    raw = "AA:BB=10.20.0.6 AA:BB=10.20.0.9!"
    assert lan.parse_lan_device_overrides(raw) == {"AA:BB": ("10.20.0.9", True)}


@pytest.mark.parametrize(
    "raw",
    [
        "=192.168.3.210",  # empty device_id
        "AA:BB=",  # empty ip
        "AA:BB=!",  # empty ip, just the marker
        "AA:BB=not-an-ip",  # invalid ip
        "AA:BB=not-an-ip!",  # invalid ip, write-only marker
    ],
)
def test_parse_lan_device_overrides_skips_malformed_entries(raw):
    # Malformed entries are skipped silently, never raised — a bad token must
    # never abort the rest of parsing (matches expand_lan_targets' contract
    # for its own errors, but overrides never raise at all).
    assert lan.parse_lan_device_overrides(raw) == {}


# --- validate_lan_device_overrides ------------------------------------------
#
# The strict counterpart used by the options form. Runtime parsing stays
# lenient so one bad token can't stop the others binding; the form must not be,
# or a typo saves cleanly and then silently does nothing (#164).


@pytest.mark.parametrize("raw", ["", None, "   "])
def test_validate_lan_device_overrides_empty(raw):
    assert lan.validate_lan_device_overrides(raw) == {}


def test_validate_lan_device_overrides_returns_parsed_entries():
    raw = "10.20.0.5, AA:BB=10.20.0.6!, CC:DD=10.20.0.7"
    assert lan.validate_lan_device_overrides(raw) == {
        "AA:BB": ("10.20.0.6", True),
        "CC:DD": ("10.20.0.7", False),
    }


def test_validate_lan_device_overrides_ignores_plain_targets():
    # Plain IPs/subnets are expand_lan_targets' business, not an override.
    assert lan.validate_lan_device_overrides("10.20.0.5 10.20.0.0/24") == {}


@pytest.mark.parametrize(
    "raw",
    [
        "=192.168.3.210",  # empty device_id
        "AA:BB=",  # empty ip
        "AA:BB=!",  # empty ip, just the marker
        "AA:BB=not-an-ip",  # invalid ip
        "AA:BB=not-an-ip!",  # invalid ip, write-only marker
        "AA:BB=192.168.1.999",  # out-of-range octet
    ],
)
def test_validate_lan_device_overrides_raises_on_malformed(raw):
    # The exact tokens parse_lan_device_overrides skips silently must raise
    # here — this is the difference between the two functions.
    with pytest.raises(lan.LanTargetError):
        lan.validate_lan_device_overrides(raw)


def test_validate_and_parse_disagree_only_on_malformed_input():
    """The lenient/strict split is the point: same good input, same result."""
    good = "AA:BB=10.20.0.6! CC:DD=10.20.0.7"
    assert lan.validate_lan_device_overrides(good) == lan.parse_lan_device_overrides(
        good
    )

    bad = "AA:BB=not-an-ip"
    assert lan.parse_lan_device_overrides(bad) == {}
    with pytest.raises(lan.LanTargetError):
        lan.validate_lan_device_overrides(bad)


# --- async_get_lan_interface_ips -------------------------------------------
#
# The single shared interface-IP enumeration hoisted out of diagnostics (#57) so
# both the diagnostics scan and the future persistent LAN client use one
# implementation. Patched on the shared ``network`` module object so the call
# inside the helper picks up the mock.


@pytest.mark.asyncio
async def test_interface_ips_filters_loopback_and_non_ipv4(monkeypatch):
    # Only enabled, non-loopback IPv4 addresses survive — IPv6 and loopback drop.
    monkeypatch.setattr(
        lan.network,
        "async_get_enabled_source_ips",
        AsyncMock(
            return_value=[
                IPv4Address("192.168.1.50"),
                IPv4Address("10.0.0.5"),
                IPv4Address("127.0.0.1"),  # loopback -> dropped
                IPv6Address("fe80::1"),  # non-IPv4 -> dropped
            ]
        ),
    )

    ips = await lan.async_get_lan_interface_ips(MagicMock())

    assert ips == ["192.168.1.50", "10.0.0.5"]


@pytest.mark.asyncio
async def test_interface_ips_returns_plain_strings(monkeypatch):
    # Returns str (not IPv4Address) so it can flow straight into the scan helpers.
    monkeypatch.setattr(
        lan.network,
        "async_get_enabled_source_ips",
        AsyncMock(return_value=[IPv4Address("192.168.1.50")]),
    )

    ips = await lan.async_get_lan_interface_ips(MagicMock())

    assert ips == ["192.168.1.50"]
    assert all(isinstance(ip, str) for ip in ips)


@pytest.mark.asyncio
async def test_interface_ips_empty_when_none_enabled(monkeypatch):
    monkeypatch.setattr(
        lan.network,
        "async_get_enabled_source_ips",
        AsyncMock(return_value=[]),
    )

    assert await lan.async_get_lan_interface_ips(MagicMock()) == []


@pytest.mark.asyncio
async def test_interface_ips_degrades_to_empty_on_error(monkeypatch):
    # Network component not set up -> never raise; fall back to default-route scan.
    monkeypatch.setattr(
        lan.network,
        "async_get_enabled_source_ips",
        AsyncMock(side_effect=RuntimeError("network not set up")),
    )

    assert await lan.async_get_lan_interface_ips(MagicMock()) == []


# --- async_get_lan_broadcast_addresses (#57) -------------------------------
#
# Derives each enabled adapter's directed-broadcast (x.x.x.255) so the scan can
# reach newer devices that ignore multicast. Patched on the shared ``network``
# module object, mirroring the interface-IP tests above.


def _adapter(*, enabled=True, ipv4):
    """Minimal HA network Adapter dict with the fields the helper reads."""
    return {"name": "eth0", "enabled": enabled, "ipv4": ipv4}


@pytest.mark.asyncio
async def test_broadcast_addresses_derived_from_prefix(monkeypatch):
    monkeypatch.setattr(
        lan.network,
        "async_get_adapters",
        AsyncMock(
            return_value=[
                _adapter(ipv4=[{"address": "192.168.0.42", "network_prefix": 24}]),
                _adapter(ipv4=[{"address": "10.0.5.7", "network_prefix": 16}]),
            ]
        ),
    )

    bcasts = await lan.async_get_lan_broadcast_addresses(MagicMock())

    assert bcasts == ["192.168.0.255", "10.0.255.255"]


@pytest.mark.asyncio
async def test_broadcast_addresses_skip_disabled_loopback_linklocal_and_p2p(monkeypatch):
    monkeypatch.setattr(
        lan.network,
        "async_get_adapters",
        AsyncMock(
            return_value=[
                _adapter(enabled=False, ipv4=[{"address": "192.168.9.1", "network_prefix": 24}]),  # disabled
                _adapter(ipv4=[{"address": "127.0.0.1", "network_prefix": 8}]),  # loopback
                _adapter(ipv4=[{"address": "169.254.1.1", "network_prefix": 16}]),  # link-local
                _adapter(ipv4=[{"address": "10.0.0.2", "network_prefix": 31}]),  # /31 p2p, no bcast
                _adapter(ipv4=[{"address": "172.16.4.9", "network_prefix": 24}]),  # kept
            ]
        ),
    )

    assert await lan.async_get_lan_broadcast_addresses(MagicMock()) == ["172.16.4.255"]


@pytest.mark.asyncio
async def test_broadcast_addresses_dedupe_multi_ip_adapter(monkeypatch):
    # Two addresses on the same subnet collapse to one broadcast.
    monkeypatch.setattr(
        lan.network,
        "async_get_adapters",
        AsyncMock(
            return_value=[
                _adapter(
                    ipv4=[
                        {"address": "192.168.0.10", "network_prefix": 24},
                        {"address": "192.168.0.11", "network_prefix": 24},
                    ]
                )
            ]
        ),
    )

    assert await lan.async_get_lan_broadcast_addresses(MagicMock()) == ["192.168.0.255"]


@pytest.mark.asyncio
async def test_broadcast_addresses_tolerate_malformed_entries(monkeypatch):
    monkeypatch.setattr(
        lan.network,
        "async_get_adapters",
        AsyncMock(
            return_value=[
                _adapter(ipv4=[{"address": None, "network_prefix": 24}]),  # no address
                _adapter(ipv4=[{"address": "192.168.1.5"}]),  # no prefix
                _adapter(ipv4=[{"address": "not-an-ip", "network_prefix": 24}]),  # unparseable
                _adapter(ipv4=[{"address": "192.168.1.5", "network_prefix": 24}]),  # valid
            ]
        ),
    )

    assert await lan.async_get_lan_broadcast_addresses(MagicMock()) == ["192.168.1.255"]


@pytest.mark.asyncio
async def test_broadcast_addresses_degrade_to_empty_on_error(monkeypatch):
    monkeypatch.setattr(
        lan.network,
        "async_get_adapters",
        AsyncMock(side_effect=RuntimeError("network not set up")),
    )

    assert await lan.async_get_lan_broadcast_addresses(MagicMock()) == []
