"""Tests for the LAN-transport models, helpers, and client (issue #57).

Covers the pure helpers ``parse_dev_status`` (the four-field LAN data ceiling,
with strict rejection of partial/garbage/wrong-command datagrams) and
``correlate_scan`` (exact + hex-normalized device-id correlation, group skipping,
no IP guessing, and unmatched accounting), plus :class:`GoveeLanClient` —
lifecycle/dual-socket design (LAN-008) and the read/send API + source-IP dispatch
(LAN-009). The client tests use fake sockets/transports and drive the dispatch
directly; they NEVER open a real socket or touch the network.
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import socket

import pytest

from custom_components.govee.api import lan, lan_client
from custom_components.govee.api.lan_client import (
    GoveeLanClient,
    LanDevStatus,
    LanDeviceInfo,
    correlate_scan,
    parse_dev_status,
)
from custom_components.govee.models.state import RGBColor


def _dev_status(**data):
    """Wrap ``data`` in the Govee ``devStatus`` reply envelope."""
    return {"msg": {"cmd": "devStatus", "data": data}}


def _dev_status_bytes(**overrides):
    """A complete devStatus reply envelope, JSON-encoded as an on-wire datagram."""
    return json.dumps(_dev_status(**_full_data(**overrides))).encode("utf-8")


def _full_data(**overrides):
    """A complete four-field devStatus ``data`` block, overridable per test."""
    data = {
        "onOff": 1,
        "brightness": 100,
        "color": {"r": 255, "g": 0, "b": 0},
        "colorTemInKelvin": 0,
    }
    data.update(overrides)
    return data


# ==============================================================================
# parse_dev_status — happy path / exactly-four-fields
# ==============================================================================


class TestParseDevStatusHappyPath:
    """Faithful extraction of exactly the four readable ceiling fields."""

    def test_full_reply_extracts_all_four_fields(self):
        # Arrange
        payload = _dev_status(**_full_data(brightness=73))

        # Act
        status = parse_dev_status(payload)

        # Assert
        assert status == LanDevStatus(
            on=True,
            brightness_0_100=73,
            color=RGBColor(255, 0, 0),
            color_temp_kelvin=None,
        )

    def test_power_off_maps_to_false(self):
        status = parse_dev_status(_dev_status(**_full_data(onOff=0)))
        assert status is not None
        assert status.on is False

    def test_power_on_maps_to_true(self):
        status = parse_dev_status(_dev_status(**_full_data(onOff=1)))
        assert status is not None
        assert status.on is True

    def test_color_temp_zero_is_none_not_zero(self):
        # 0 is the firmware "not in CT mode" sentinel, not a real 0 K reading.
        status = parse_dev_status(_dev_status(**_full_data(colorTemInKelvin=0)))
        assert status is not None
        assert status.color_temp_kelvin is None

    def test_color_temp_positive_is_kept(self):
        status = parse_dev_status(_dev_status(**_full_data(colorTemInKelvin=4000)))
        assert status is not None
        assert status.color_temp_kelvin == 4000

    def test_color_temp_negative_is_treated_as_none(self):
        status = parse_dev_status(_dev_status(**_full_data(colorTemInKelvin=-5)))
        assert status is not None
        assert status.color_temp_kelvin is None

    def test_color_dict_becomes_rgbcolor(self):
        status = parse_dev_status(_dev_status(**_full_data(color={"r": 10, "g": 20, "b": 30})))
        assert status is not None
        assert status.color == RGBColor(10, 20, 30)

    def test_color_values_are_clamped_by_rgbcolor(self):
        status = parse_dev_status(_dev_status(**_full_data(color={"r": 999, "g": -1, "b": 30})))
        assert status is not None
        assert status.color == RGBColor(255, 0, 30)

    def test_brightness_zero_is_preserved(self):
        status = parse_dev_status(_dev_status(**_full_data(brightness=0)))
        assert status is not None
        assert status.brightness_0_100 == 0

    def test_extra_fields_are_ignored_not_rejected(self):
        # Anything beyond the four ceiling fields is dropped, never surfaced.
        payload = _dev_status(
            **_full_data(),
            sku="H6159",
            pt="bleHexBlob",
            sceneId=42,
        )

        status = parse_dev_status(payload)

        assert status is not None
        assert status == LanDevStatus(
            on=True,
            brightness_0_100=100,
            color=RGBColor(255, 0, 0),
            color_temp_kelvin=None,
        )
        # No fabricated attributes leaked in.
        assert not hasattr(status, "sceneId")

    def test_string_numbers_are_coerced(self):
        status = parse_dev_status(
            _dev_status(onOff="1", brightness="50", color={"r": 1, "g": 2, "b": 3}, colorTemInKelvin="0")
        )
        assert status is not None
        assert status.on is True
        assert status.brightness_0_100 == 50


# ==============================================================================
# parse_dev_status — rejection (None) on wrong-cmd / partial / garbage
# ==============================================================================


class TestParseDevStatusRejection:
    """A malformed reply yields ``None`` and never fabricates state."""

    @pytest.mark.parametrize("cmd", ["scan", "status", "turn", "", "DEVSTATUS"])
    def test_wrong_command_returns_none(self, cmd):
        payload = {"msg": {"cmd": cmd, "data": _full_data()}}
        assert parse_dev_status(payload) is None

    def test_missing_cmd_returns_none(self):
        payload = {"msg": {"data": _full_data()}}
        assert parse_dev_status(payload) is None

    @pytest.mark.parametrize("missing", ["onOff", "brightness", "color", "colorTemInKelvin"])
    def test_partial_reply_missing_any_field_returns_none(self, missing):
        data = _full_data()
        del data[missing]
        assert parse_dev_status(_dev_status(**data)) is None

    def test_empty_data_query_echo_returns_none(self):
        # The status *query* is {"msg": {"cmd": "devStatus", "data": {}}}.
        assert parse_dev_status(_dev_status()) is None

    @pytest.mark.parametrize("payload", [None, [], "string", 42, 3.14, True])
    def test_non_dict_payload_returns_none(self, payload):
        assert parse_dev_status(payload) is None

    @pytest.mark.parametrize("msg", [None, [], "x", 5])
    def test_non_dict_msg_returns_none(self, msg):
        assert parse_dev_status({"msg": msg}) is None

    def test_missing_msg_returns_none(self):
        assert parse_dev_status({"cmd": "devStatus"}) is None

    @pytest.mark.parametrize("data", [None, [], "x", 5])
    def test_non_dict_data_returns_none(self, data):
        assert parse_dev_status({"msg": {"cmd": "devStatus", "data": data}}) is None

    @pytest.mark.parametrize("bad", ["abc", "", None, [1, 2]])
    def test_undecodable_brightness_returns_none(self, bad):
        assert parse_dev_status(_dev_status(**_full_data(brightness=bad))) is None

    @pytest.mark.parametrize("bad", ["abc", "", None])
    def test_undecodable_onoff_returns_none(self, bad):
        assert parse_dev_status(_dev_status(**_full_data(onOff=bad))) is None

    @pytest.mark.parametrize("bad", ["abc", "", None])
    def test_undecodable_color_temp_returns_none(self, bad):
        assert parse_dev_status(_dev_status(**_full_data(colorTemInKelvin=bad))) is None

    @pytest.mark.parametrize("bad", ["not-a-dict", 123, [255, 0, 0], None])
    def test_non_dict_color_returns_none(self, bad):
        assert parse_dev_status(_dev_status(**_full_data(color=bad))) is None

    def test_color_with_non_numeric_channel_returns_none(self):
        payload = _dev_status(**_full_data(color={"r": "red", "g": 0, "b": 0}))
        assert parse_dev_status(payload) is None

    def test_json_boolean_onoff_rejected(self):
        # Firmware sends 0/1; a JSON boolean is non-conforming and rejected
        # rather than silently coerced.
        assert parse_dev_status(_dev_status(**_full_data(onOff=True))) is None
        assert parse_dev_status(_dev_status(**_full_data(onOff=False))) is None


# ==============================================================================
# LanDevStatus — immutability
# ==============================================================================


class TestLanDevStatusImmutability:
    """The status snapshot is a frozen value object."""

    def test_cannot_mutate_field(self):
        status = LanDevStatus(
            on=True,
            brightness_0_100=50,
            color=RGBColor(1, 2, 3),
            color_temp_kelvin=None,
        )
        with pytest.raises(dataclasses.FrozenInstanceError):
            status.on = False  # type: ignore[misc]

    def test_equality_by_value(self):
        a = LanDevStatus(True, 50, RGBColor(1, 2, 3), None)
        b = LanDevStatus(True, 50, RGBColor(1, 2, 3), None)
        assert a == b


# ==============================================================================
# correlate_scan — matching, normalization, group skip, unmatched, ts stamp
# ==============================================================================

_MAC = "03:9C:DC:06:75:4B:10:7C"
_GROUP_ID = "11825917"


def _scan_record(device, *, ip="10.0.0.5", sku="H6159", **extra):
    record = {"device": device, "ip": ip, "sku": sku}
    record.update(extra)
    return record


class TestCorrelateScanMatching:
    """Exact + hex-normalized correlation onto coordinator device ids."""

    def test_exact_match(self):
        # Arrange
        records = [_scan_record(_MAC)]

        # Act
        matched, unmatched = correlate_scan(records, [_MAC], now=100.0)

        # Assert
        assert unmatched == []
        assert set(matched) == {_MAC}
        info = matched[_MAC]
        assert info.device_id == _MAC
        assert info.ip == "10.0.0.5"
        assert info.sku == "H6159"

    def test_hex_normalized_match_lowercase_no_separators(self):
        # Scan reports the MAC without colons and lower-cased; the coordinator
        # holds the canonical colon-delimited upper-case form.
        records = [_scan_record("039cdc06754b107c")]

        matched, unmatched = correlate_scan(records, [_MAC], now=1.0)

        assert unmatched == []
        assert set(matched) == {_MAC}
        # device_id is the coordinator id; mac is the raw scan identifier.
        assert matched[_MAC].device_id == _MAC
        assert matched[_MAC].mac == "039cdc06754b107c"

    def test_hex_normalized_match_dash_separated(self):
        records = [_scan_record("03-9C-DC-06-75-4B-10-7C")]
        matched, _ = correlate_scan(records, [_MAC], now=1.0)
        assert set(matched) == {_MAC}

    def test_exact_match_preferred_over_normalization(self):
        records = [_scan_record(_MAC)]
        matched, _ = correlate_scan(records, [_MAC], now=1.0)
        assert matched[_MAC].mac == _MAC

    def test_timestamp_stamped_per_match(self):
        records = [_scan_record(_MAC)]
        matched, _ = correlate_scan(records, [_MAC], now=1234.5)
        assert matched[_MAC].last_correlated_ts == 1234.5

    def test_firmware_prefers_wifi_soft(self):
        records = [
            _scan_record(
                _MAC,
                wifiVersionSoft="1.02.05",
                bleVersionSoft="2.00.01",
            )
        ]
        matched, _ = correlate_scan(records, [_MAC], now=1.0)
        assert matched[_MAC].firmware == "1.02.05"

    def test_firmware_falls_back_to_ble_soft(self):
        records = [_scan_record(_MAC, bleVersionSoft="2.00.01")]
        matched, _ = correlate_scan(records, [_MAC], now=1.0)
        assert matched[_MAC].firmware == "2.00.01"

    def test_firmware_empty_when_absent(self):
        records = [_scan_record(_MAC)]
        matched, _ = correlate_scan(records, [_MAC], now=1.0)
        assert matched[_MAC].firmware == ""

    def test_missing_ip_yields_empty_string(self):
        records = [{"device": _MAC, "sku": "H6159"}]
        matched, _ = correlate_scan(records, [_MAC], now=1.0)
        assert matched[_MAC].ip == ""


class TestCorrelateScanGroupSkip:
    """Group ids (numeric) are never correlated to the LAN transport."""

    def test_numeric_group_id_skipped(self):
        records = [_scan_record(_GROUP_ID)]
        matched, unmatched = correlate_scan(records, [_GROUP_ID], now=1.0)
        assert matched == {}
        assert len(unmatched) == 1

    def test_group_excluded_but_real_device_still_matches(self):
        records = [_scan_record(_MAC)]
        matched, unmatched = correlate_scan(records, [_GROUP_ID, _MAC], now=1.0)
        assert set(matched) == {_MAC}
        assert unmatched == []


class TestCorrelateScanUnmatched:
    """Unmatched scan records are returned/counted, never dropped silently."""

    def test_unknown_device_is_unmatched(self):
        records = [_scan_record("AA:BB:CC:DD:EE:FF:00:11")]
        matched, unmatched = correlate_scan(records, [_MAC], now=1.0)
        assert matched == {}
        assert unmatched == records

    def test_unmatched_count(self):
        records = [
            _scan_record(_MAC),
            _scan_record("AA:BB:CC:DD:EE:FF:00:11"),
            _scan_record("11:22:33:44:55:66:77:88"),
        ]
        matched, unmatched = correlate_scan(records, [_MAC], now=1.0)
        assert len(matched) == 1
        assert len(unmatched) == 2

    def test_ip_is_never_used_to_guess_identity(self):
        # The record's IP matches a known device's expected address, but its
        # device id does not — correlation must NOT fall back to IP.
        records = [_scan_record("FF:FF:FF:FF:FF:FF:FF:FF", ip="10.0.0.5")]
        matched, unmatched = correlate_scan(records, [_MAC], now=1.0)
        assert matched == {}
        assert len(unmatched) == 1

    def test_record_missing_device_is_unmatched(self):
        records = [{"ip": "10.0.0.5", "sku": "H6159"}]
        matched, unmatched = correlate_scan(records, [_MAC], now=1.0)
        assert matched == {}
        assert len(unmatched) == 1

    def test_record_with_empty_device_is_unmatched(self):
        records = [_scan_record("")]
        matched, unmatched = correlate_scan(records, [_MAC], now=1.0)
        assert matched == {}
        assert len(unmatched) == 1


class TestCorrelateScanEdgeCases:
    """Empty inputs and mixed batches behave predictably."""

    def test_empty_scan_records(self):
        matched, unmatched = correlate_scan([], [_MAC], now=1.0)
        assert matched == {}
        assert unmatched == []

    def test_empty_device_ids(self):
        records = [_scan_record(_MAC)]
        matched, unmatched = correlate_scan(records, [], now=1.0)
        assert matched == {}
        assert unmatched == records

    def test_mixed_batch(self):
        records = [
            _scan_record(_MAC),
            _scan_record(_GROUP_ID),
            _scan_record("AA:BB:CC:DD:EE:FF:00:11"),
        ]
        matched, unmatched = correlate_scan(records, [_MAC, _GROUP_ID], now=7.0)
        assert set(matched) == {_MAC}
        assert len(unmatched) == 2


class TestLanDeviceInfoImmutability:
    """LanDeviceInfo is a frozen value object."""

    def test_cannot_mutate(self):
        info = LanDeviceInfo(
            device_id=_MAC,
            ip="10.0.0.5",
            mac=_MAC,
            sku="H6159",
            firmware="1.0",
            last_correlated_ts=1.0,
        )
        with pytest.raises(dataclasses.FrozenInstanceError):
            info.ip = "10.0.0.6"  # type: ignore[misc]


# ==============================================================================
# GoveeLanClient lifecycle + dual-socket design (story LAN-008).
#
# All sockets are fakes — these tests NEVER open a real socket or touch the
# network. _build_socket / _build_send_socket are monkeypatched to hand back the
# fakes, and the event loop's create_datagram_endpoint is replaced so no real
# datagram endpoint is created.
# ==============================================================================


class _FakeSocket:
    """A no-op stand-in for ``socket.socket`` that records every operation."""

    def __init__(self, family=None, sock_type=None):
        self.family = family
        self.sock_type = sock_type
        self.setsockopt_calls: list[tuple[int, int, object]] = []
        self.bind_args: object = None
        self.blocking = True
        self.closed = False
        # When set, ``bind`` raises this instead of recording the address.
        self.bind_error: OSError | None = None

    def setsockopt(self, level, optname, value):
        self.setsockopt_calls.append((level, optname, value))

    def setblocking(self, flag):
        self.blocking = flag

    def bind(self, addr):
        if self.bind_error is not None:
            raise self.bind_error
        self.bind_args = addr

    def close(self):
        self.closed = True

    def opt_names(self) -> list[int]:
        """The ``optname`` of every recorded setsockopt call."""
        return [optname for _, optname, _ in self.setsockopt_calls]


class _FakeTransport:
    """A stand-in for ``asyncio.DatagramTransport`` that closes its fake socket."""

    def __init__(self, sock):
        self._sock = sock
        self.closed = False
        self.sent: list[tuple[bytes, object]] = []

    def sendto(self, data, addr=None):
        self.sent.append((data, addr))

    def close(self):
        self.closed = True
        if self._sock is not None:
            self._sock.close()

    def get_extra_info(self, name, default=None):
        if name == "socket":
            return self._sock
        return default


def _patch_create_endpoint(monkeypatch):
    """Replace the running loop's ``create_datagram_endpoint`` with a fake.

    Returns a list that accumulates ``(transport, protocol, sock)`` for every
    endpoint the client creates, so a test can assert exactly which sockets were
    wired and inspect the forwarding protocol.
    """
    endpoints: list[tuple[_FakeTransport, object, object]] = []

    async def fake_create(protocol_factory, sock=None):
        protocol = protocol_factory()
        transport = _FakeTransport(sock)
        endpoints.append((transport, protocol, sock))
        return transport, protocol

    loop = asyncio.get_running_loop()
    monkeypatch.setattr(loop, "create_datagram_endpoint", fake_create)
    return endpoints


def _patch_sockets(monkeypatch, recv_sock, send_sock):
    """Make ``_build_socket`` / ``_build_send_socket`` hand back the given fakes."""
    monkeypatch.setattr(lan_client, "_build_socket", lambda: recv_sock)
    monkeypatch.setattr(lan_client, "_build_send_socket", lambda: send_sock)


# ------------------------------------------------------------------------------
# _build_send_socket — the ephemeral, integration-owned send socket
# ------------------------------------------------------------------------------


class TestBuildSendSocket:
    """The dedicated send socket binds an ephemeral port, never :4002."""

    def test_binds_ephemeral_port_not_4002(self, monkeypatch):
        created: list[_FakeSocket] = []

        def fake_factory(family, sock_type):
            sock = _FakeSocket(family, sock_type)
            created.append(sock)
            return sock

        monkeypatch.setattr(lan_client.socket, "socket", fake_factory)

        sock = lan_client._build_send_socket()

        # Port 0 -> the kernel assigns a free ephemeral port we solely own; it is
        # explicitly NOT the shared :4002 response port.
        assert sock.bind_args == ("", 0)
        assert sock.bind_args != ("", lan_client.LAN_RESPONSE_PORT)
        assert created == [sock]

    def test_sets_broadcast_for_x_x_x_255_targets(self, monkeypatch):
        monkeypatch.setattr(lan_client.socket, "socket", lambda f, t: _FakeSocket(f, t))
        sock = lan_client._build_send_socket()
        assert socket.SO_BROADCAST in sock.opt_names()

    def test_never_joins_the_multicast_group(self, monkeypatch):
        # The send socket must never IP_ADD_MEMBERSHIP — only the receive socket
        # joins the group. Joining here would re-introduce the co-bind hazard.
        monkeypatch.setattr(lan_client.socket, "socket", lambda f, t: _FakeSocket(f, t))
        sock = lan_client._build_send_socket()
        assert socket.IP_ADD_MEMBERSHIP not in sock.opt_names()

    def test_bind_failure_closes_socket_and_reraises(self, monkeypatch):
        bad = _FakeSocket()
        bad.bind_error = OSError("ephemeral bind failed")
        monkeypatch.setattr(lan_client.socket, "socket", lambda f, t: bad)
        with pytest.raises(OSError):
            lan_client._build_send_socket()
        assert bad.closed is True


# ------------------------------------------------------------------------------
# _RealtimeProtocol — forwards every datagram to the injected callback
# ------------------------------------------------------------------------------


class TestRealtimeProtocol:
    """The protocol hands raw datagrams to the callback, keyed by source IP."""

    def test_forwards_datagram_keyed_by_source_ip(self):
        received: list[tuple[str, bytes]] = []
        proto = lan_client._RealtimeProtocol(lambda ip, payload: received.append((ip, payload)))

        proto.datagram_received(b'{"msg": {"cmd": "devStatus"}}', ("10.0.0.9", 4002))

        assert received == [("10.0.0.9", b'{"msg": {"cmd": "devStatus"}}')]

    def test_each_source_ip_forwarded_separately(self):
        received: list[tuple[str, bytes]] = []
        proto = lan_client._RealtimeProtocol(lambda ip, payload: received.append((ip, payload)))

        # A multicast push from one device and a unicast reply from another.
        proto.datagram_received(b"push", ("10.0.0.1", 4002))
        proto.datagram_received(b"reply", ("10.0.0.2", 54321))

        assert received == [("10.0.0.1", b"push"), ("10.0.0.2", b"reply")]

    def test_payload_forwarded_raw_no_parsing(self):
        # Parse/dispatch is LAN-009; the protocol must not decode or filter.
        received: list[tuple[str, bytes]] = []
        proto = lan_client._RealtimeProtocol(lambda ip, payload: received.append((ip, payload)))

        proto.datagram_received(b"not-json-garbage", ("10.0.0.3", 4002))

        assert received == [("10.0.0.3", b"not-json-garbage")]


# ------------------------------------------------------------------------------
# GoveeLanClient.async_start / async_stop — dual sockets, degrade, teardown
# ------------------------------------------------------------------------------


class TestGoveeLanClientStart:
    """async_start builds both sockets, joins the group, and forwards datagrams."""

    async def test_builds_both_sockets_and_joins_group(self, monkeypatch):
        recv_sock = _FakeSocket()
        send_sock = _FakeSocket()
        _patch_sockets(monkeypatch, recv_sock, send_sock)
        endpoints = _patch_create_endpoint(monkeypatch)

        client = GoveeLanClient(lambda ip, payload: None)
        await client.async_start(["10.0.0.2"])

        assert client.available is True
        # Exactly two endpoints — one per socket.
        wired_socks = [sock for _, _, sock in endpoints]
        assert wired_socks == [recv_sock, send_sock]
        # The group was joined on the RECEIVE socket only (real _join_group runs
        # against the fake, recording IP_ADD_MEMBERSHIP setsockopt calls).
        assert socket.IP_ADD_MEMBERSHIP in recv_sock.opt_names()
        assert socket.IP_ADD_MEMBERSHIP not in send_sock.opt_names()

    async def test_send_socket_is_distinct_from_receive_socket(self, monkeypatch):
        recv_sock = _FakeSocket()
        send_sock = _FakeSocket()
        _patch_sockets(monkeypatch, recv_sock, send_sock)
        _patch_create_endpoint(monkeypatch)

        client = GoveeLanClient(lambda ip, payload: None)
        await client.async_start([])

        assert client._recv_sock is recv_sock
        assert client._send_sock is send_sock
        assert client._recv_sock is not client._send_sock

    async def test_datagram_on_either_socket_reaches_the_callback(self, monkeypatch):
        recv_sock = _FakeSocket()
        send_sock = _FakeSocket()
        _patch_sockets(monkeypatch, recv_sock, send_sock)
        endpoints = _patch_create_endpoint(monkeypatch)

        # LAN-009: the client parses internally and only delivers PARSED,
        # unsolicited devStatus pushes (no read in flight) to on_dev_status.
        pushes: list[tuple[str, LanDevStatus]] = []
        client = GoveeLanClient(lambda ip, status: pushes.append((ip, status)))
        await client.async_start([])

        # endpoints[0] is the receive (:4002 multicast) protocol, endpoints[1]
        # the ephemeral send-socket (unicast reply) protocol — a valid devStatus
        # on EITHER reaches the dispatch and is delivered as a parsed push.
        recv_proto = endpoints[0][1]
        send_proto = endpoints[1][1]
        recv_proto.datagram_received(_dev_status_bytes(brightness=40), ("10.0.0.5", 4002))
        send_proto.datagram_received(_dev_status_bytes(brightness=60), ("10.0.0.6", 4003))

        assert pushes == [
            ("10.0.0.5", LanDevStatus(True, 40, RGBColor(255, 0, 0), None)),
            ("10.0.0.6", LanDevStatus(True, 60, RGBColor(255, 0, 0), None)),
        ]

    async def test_idempotent_start_is_a_noop(self, monkeypatch):
        recv_sock = _FakeSocket()
        send_sock = _FakeSocket()
        _patch_sockets(monkeypatch, recv_sock, send_sock)
        endpoints = _patch_create_endpoint(monkeypatch)

        client = GoveeLanClient(lambda ip, payload: None)
        await client.async_start([])
        await client.async_start([])  # second call must not re-bind

        assert len(endpoints) == 2


class TestGoveeLanClientDegrade:
    """A failed :4002 bind disables the client without raising (critic #5)."""

    async def test_oserror_on_4002_bind_disables_without_raise(self, monkeypatch):
        def boom():
            raise OSError("port 4002 held without SO_REUSEPORT")

        monkeypatch.setattr(lan_client, "_build_socket", boom)
        # The send-socket builder must never be reached when :4002 fails.
        send_sock = _FakeSocket()
        monkeypatch.setattr(lan_client, "_build_send_socket", lambda: send_sock)
        _patch_create_endpoint(monkeypatch)

        client = GoveeLanClient(lambda ip, payload: None)
        await client.async_start(["10.0.0.2"])  # must NOT raise

        assert client.available is False
        assert client._recv_sock is None
        assert client._send_sock is None
        assert send_sock.closed is False  # never built

    async def test_send_socket_failure_closes_recv_and_disables(self, monkeypatch):
        recv_sock = _FakeSocket()
        monkeypatch.setattr(lan_client, "_build_socket", lambda: recv_sock)

        def boom():
            raise OSError("ephemeral bind failed")

        monkeypatch.setattr(lan_client, "_build_send_socket", boom)
        _patch_create_endpoint(monkeypatch)

        client = GoveeLanClient(lambda ip, payload: None)
        await client.async_start([])  # must NOT raise

        assert client.available is False
        # The already-built receive socket must be closed, not leaked.
        assert recv_sock.closed is True

    async def test_endpoint_failure_drops_group_and_closes_both(self, monkeypatch):
        recv_sock = _FakeSocket()
        send_sock = _FakeSocket()
        _patch_sockets(monkeypatch, recv_sock, send_sock)

        async def boom(protocol_factory, sock=None):
            raise OSError("cannot create datagram endpoint")

        loop = asyncio.get_running_loop()
        monkeypatch.setattr(loop, "create_datagram_endpoint", boom)

        client = GoveeLanClient(lambda ip, payload: None)
        await client.async_start(["10.0.0.2"])  # must NOT raise

        assert client.available is False
        # Both sockets closed; the group join was dropped on the receive socket.
        assert recv_sock.closed is True
        assert send_sock.closed is True
        assert socket.IP_DROP_MEMBERSHIP in recv_sock.opt_names()

    async def test_second_endpoint_failure_closes_the_first_transport(self, monkeypatch):
        recv_sock = _FakeSocket()
        send_sock = _FakeSocket()
        _patch_sockets(monkeypatch, recv_sock, send_sock)
        created: list[_FakeTransport] = []

        async def first_only(protocol_factory, sock=None):
            if created:
                raise OSError("cannot attach the send socket")
            transport = _FakeTransport(sock)
            created.append(transport)
            return transport, protocol_factory()

        loop = asyncio.get_running_loop()
        monkeypatch.setattr(loop, "create_datagram_endpoint", first_only)

        client = GoveeLanClient(lambda ip, payload: None)
        await client.async_start(["10.0.0.2"])  # must NOT raise

        assert client.available is False
        # The receive transport is not left registered on the loop.
        assert created[0].closed is True
        assert send_sock.closed is True


class TestGoveeLanClientStop:
    """async_stop drops the group, closes BOTH sockets, and is idempotent."""

    async def test_drops_group_and_closes_both_sockets(self, monkeypatch):
        recv_sock = _FakeSocket()
        send_sock = _FakeSocket()
        _patch_sockets(monkeypatch, recv_sock, send_sock)
        _patch_create_endpoint(monkeypatch)

        client = GoveeLanClient(lambda ip, payload: None)
        await client.async_start(["10.0.0.2"])
        assert client.available is True

        await client.async_stop()

        assert client.available is False
        # Closing each transport closes its underlying socket.
        assert recv_sock.closed is True
        assert send_sock.closed is True
        # The multicast group was dropped on the receive socket.
        assert socket.IP_DROP_MEMBERSHIP in recv_sock.opt_names()
        assert client._recv_sock is None
        assert client._send_sock is None

    async def test_stop_is_idempotent(self, monkeypatch):
        recv_sock = _FakeSocket()
        send_sock = _FakeSocket()
        _patch_sockets(monkeypatch, recv_sock, send_sock)
        _patch_create_endpoint(monkeypatch)

        client = GoveeLanClient(lambda ip, payload: None)
        await client.async_start([])
        await client.async_stop()
        await client.async_stop()  # second stop must be a clean no-op

        assert client.available is False

    async def test_stop_before_start_is_a_noop(self):
        # Never-started client: async_stop must not raise or touch anything.
        client = GoveeLanClient(lambda ip, payload: None)
        await client.async_stop()
        assert client.available is False


# ==============================================================================
# GoveeLanClient read/send API + source-IP dispatch (story LAN-009).
#
# Same fakes as the lifecycle tests. A "responder" is wired onto the fake SEND
# transport so a simulated device answers a devStatus query INSTANTLY (the reply
# is fed straight back through the client's own dispatch), which lets the async
# read methods resolve without any real socket or timing dependence.
# ==============================================================================


def _raise_oserror(*_args, **_kwargs):
    """A ``sendto`` stand-in that always raises ``OSError`` (unreachable host)."""
    raise OSError("simulated send failure")


def _wire_responder(client, replies):
    """Make the fake send socket inject a devStatus reply when ``sendto`` fires.

    ``replies`` maps a target IP to the raw reply bytes the "device" answers with
    (or ``None`` for a silent device). When the client sends to an IP with a
    non-``None`` reply, that reply is dispatched synchronously through the
    client's own :meth:`_handle_datagram`, simulating an instant answer.
    """
    transport = client._send_transport
    base = transport.sendto

    def sendto(data, addr=None):
        base(data, addr)
        if addr is not None:
            reply = replies.get(addr[0])
            if reply is not None:
                client._handle_datagram(addr[0], reply)

    transport.sendto = sendto


async def _start_client(monkeypatch, on_dev_status=None, replies=None):
    """Build + start a client over fakes; return ``(client, pushes)``.

    ``pushes`` is the list the default ``on_dev_status`` appends unsolicited
    ``(ip, status)`` pushes to (unless ``on_dev_status`` is supplied). ``replies``,
    when given, wires an instant-answering responder onto the send transport.
    """
    recv_sock = _FakeSocket()
    send_sock = _FakeSocket()
    _patch_sockets(monkeypatch, recv_sock, send_sock)
    _patch_create_endpoint(monkeypatch)

    pushes: list[tuple[str, LanDevStatus]] = []
    callback = on_dev_status if on_dev_status is not None else (lambda ip, status: pushes.append((ip, status)))
    client = GoveeLanClient(callback)
    await client.async_start([])
    if replies is not None:
        _wire_responder(client, replies)
    return client, pushes


# ------------------------------------------------------------------------------
# async_send_command — fire-and-forget unicast to <ip>:4003 from the send socket
# ------------------------------------------------------------------------------


class TestAsyncSendCommand:
    """Build the envelope, send from the dedicated socket, True/False contract."""

    async def test_builds_envelope_and_targets_command_port(self, monkeypatch):
        client, _ = await _start_client(monkeypatch)

        ok = await client.async_send_command("10.0.0.5", "turn", {"value": 1})

        assert ok is True
        data, addr = client._send_transport.sent[-1]
        assert addr == ("10.0.0.5", lan_client.LAN_COMMAND_PORT)
        assert json.loads(data.decode("utf-8")) == {"msg": {"cmd": "turn", "data": {"value": 1}}}

    async def test_sends_from_the_dedicated_send_socket_only(self, monkeypatch):
        # The unicast write must leave the ephemeral send socket, never the shared
        # :4002 receive socket (critic blocking #4).
        client, _ = await _start_client(monkeypatch)

        await client.async_send_command("10.0.0.5", "devStatus", {})

        assert len(client._send_transport.sent) == 1
        assert client._recv_transport.sent == []

    async def test_returns_false_on_sendto_oserror(self, monkeypatch):
        client, _ = await _start_client(monkeypatch)
        client._send_transport.sendto = _raise_oserror

        assert await client.async_send_command("10.0.0.5", "turn", {"value": 0}) is False

    async def test_returns_false_when_the_transport_reports_the_error(self, monkeypatch):
        # CPython's datagram transport never raises from sendto: it hands the
        # OSError to protocol.error_received and returns. That must still count
        # as a failed send.
        _patch_sockets(monkeypatch, _FakeSocket(), _FakeSocket())
        endpoints = _patch_create_endpoint(monkeypatch)
        client = GoveeLanClient(lambda ip, status: None)
        await client.async_start([])
        send_transport, send_protocol, _ = endpoints[1]

        def _cpython_sendto(data, addr=None):
            send_protocol.error_received(OSError(101, "Network is unreachable"))

        send_transport.sendto = _cpython_sendto

        assert await client.async_send_command("10.0.0.5", "turn", {"value": 0}) is False

        # The error belongs to that send only; the next good send succeeds.
        send_transport.sendto = lambda data, addr=None: None
        assert await client.async_send_command("10.0.0.5", "turn", {"value": 0}) is True

    async def test_returns_false_when_unavailable(self):
        # A never-started client has no send transport — send is a no-op False.
        client = GoveeLanClient(lambda ip, status: None)
        assert await client.async_send_command("10.0.0.5", "turn", {}) is False


# ------------------------------------------------------------------------------
# async_read_one — one query, await one parsed reply from THAT ip
# ------------------------------------------------------------------------------


class TestAsyncReadOne:
    """Returns the parsed reply, or None on timeout / send failure / unavailable."""

    async def test_returns_parsed_reply_from_queried_ip(self, monkeypatch):
        client, pushes = await _start_client(monkeypatch, replies={"10.0.0.7": _dev_status_bytes(brightness=55)})

        status = await client.async_read_one("10.0.0.7", timeout=0.5)

        assert status == LanDevStatus(True, 55, RGBColor(255, 0, 0), None)
        # A solicited reply must NOT also fire the unsolicited push callback.
        assert pushes == []
        # The in-flight read was cleaned up.
        assert client._pending_reads == {}

    async def test_returns_none_on_timeout(self, monkeypatch):
        # No responder wired -> the queried device never answers.
        client, _ = await _start_client(monkeypatch)

        status = await client.async_read_one("10.0.0.5", timeout=0.02)

        assert status is None
        assert client._pending_reads == {}

    async def test_returns_none_on_send_failure(self, monkeypatch):
        client, _ = await _start_client(monkeypatch)
        client._send_transport.sendto = _raise_oserror

        status = await client.async_read_one("10.0.0.5", timeout=0.5)

        assert status is None
        assert client._pending_reads == {}

    async def test_returns_none_when_unavailable(self):
        client = GoveeLanClient(lambda ip, status: None)
        assert await client.async_read_one("10.0.0.5", timeout=0.01) is None


# ------------------------------------------------------------------------------
# async_read_batch — one shared bounded window, only answering ips returned
# ------------------------------------------------------------------------------


class TestAsyncReadBatch:
    """Collect replies for one shared window; return only the ips that answered."""

    async def test_returns_only_ips_that_answered(self, monkeypatch):
        client, pushes = await _start_client(
            monkeypatch,
            replies={
                "10.0.0.1": _dev_status_bytes(brightness=10),
                "10.0.0.3": _dev_status_bytes(brightness=30),
            },
        )

        result = await client.async_read_batch(["10.0.0.1", "10.0.0.2", "10.0.0.3"], window=0.05)

        assert set(result) == {"10.0.0.1", "10.0.0.3"}
        assert result["10.0.0.1"].brightness_0_100 == 10
        assert result["10.0.0.3"].brightness_0_100 == 30
        # Solicited replies never double-dispatch to the push callback.
        assert pushes == []
        assert client._pending_reads == {}

    async def test_total_wall_time_bounded_by_one_shared_window(self, monkeypatch):
        # Eight silent ips share ONE window: total time ~= window, NOT 8 * window.
        client, _ = await _start_client(monkeypatch)
        ips = [f"10.0.0.{i}" for i in range(8)]

        loop = asyncio.get_running_loop()
        start = loop.time()
        result = await client.async_read_batch(ips, window=0.1)
        elapsed = loop.time() - start

        assert result == {}
        assert elapsed >= 0.09  # it did wait the shared window once
        assert elapsed < 0.5  # but not once per ip (which would be ~0.8s)
        assert client._pending_reads == {}

    async def test_duplicate_ips_are_de_duplicated(self, monkeypatch):
        client, _ = await _start_client(monkeypatch, replies={"10.0.0.1": _dev_status_bytes(brightness=10)})

        result = await client.async_read_batch(["10.0.0.1", "10.0.0.1"], window=0.05)

        assert set(result) == {"10.0.0.1"}
        # One query per unique ip, despite the duplicate in the input.
        assert len(client._send_transport.sent) == 1
        assert client._pending_reads == {}

    async def test_empty_ips_returns_empty(self, monkeypatch):
        client, _ = await _start_client(monkeypatch)
        assert await client.async_read_batch([], window=0.01) == {}

    async def test_returns_empty_when_unavailable(self):
        client = GoveeLanClient(lambda ip, status: None)
        assert await client.async_read_batch(["10.0.0.5"], window=0.01) == {}


# ------------------------------------------------------------------------------
# _handle_datagram — source-IP dispatch, solicited/unsolicited split, ignore
# ------------------------------------------------------------------------------


class TestAsyncScan:
    """Rescans run on the client's own :4002 socket instead of a second one."""

    @staticmethod
    def _scan_reply(device: str, ip: str) -> bytes:
        return json.dumps({"msg": {"cmd": "scan", "data": {"device": device, "ip": ip, "sku": "H6072"}}}).encode()

    async def test_sends_from_the_4002_socket_and_collects_replies(self, monkeypatch):
        client, pushes = await _start_client(monkeypatch)

        async def _window(_timeout):
            client._handle_datagram("10.0.0.5", self._scan_reply("AA:BB", "10.0.0.5"))
            client._handle_datagram("10.0.0.6", b"garbage")
            client._handle_datagram("10.0.0.7", _dev_status_bytes(brightness=40))

        monkeypatch.setattr(lan_client.asyncio, "sleep", _window)

        records = await client.async_scan(
            interface_ips=["10.0.0.2"], extra_targets=["192.0.2.10"], broadcast_targets=["10.0.0.255"]
        )

        assert records == [{"device": "AA:BB", "ip": "10.0.0.5", "sku": "H6072"}]
        sent = {addr for _data, addr in client._recv_transport.sent}
        assert sent == {
            (lan.LAN_MULTICAST_GROUP, lan.LAN_DISCOVERY_PORT),
            ("192.0.2.10", lan.LAN_DISCOVERY_PORT),
            ("10.0.0.255", lan.LAN_DISCOVERY_PORT),
        }
        assert client._send_transport.sent == []
        # devStatus pushes keep flowing during the scan window.
        assert [ip for ip, _ in pushes] == ["10.0.0.7"]
        assert client._scan_collector is None

    async def test_scan_replies_outside_a_scan_are_ignored(self, monkeypatch):
        client, pushes = await _start_client(monkeypatch)

        client._handle_datagram("10.0.0.5", self._scan_reply("AA:BB", "10.0.0.5"))

        assert pushes == []

    async def test_overlapping_scan_does_not_steal_the_running_scans_replies(self, monkeypatch):
        client, _ = await _start_client(monkeypatch)
        inner: dict[str, object] = {}

        async def _window(_timeout):
            if "result" not in inner:
                inner["result"] = await client.async_scan(interface_ips=[], extra_targets=[], broadcast_targets=[])
            client._handle_datagram("10.0.0.5", self._scan_reply("AA:BB", "10.0.0.5"))

        monkeypatch.setattr(lan_client.asyncio, "sleep", _window)

        records = await client.async_scan(interface_ips=[], extra_targets=[], broadcast_targets=[])

        assert inner["result"] == []
        assert records == [{"device": "AA:BB", "ip": "10.0.0.5", "sku": "H6072"}]

    async def test_raises_when_not_running(self):
        client = GoveeLanClient(lambda ip, status: None)

        with pytest.raises(OSError):
            await client.async_scan(interface_ips=[], extra_targets=[], broadcast_targets=[])


class TestSourceIpDispatch:
    """Valid devStatus dispatched by source IP; garbage ignored; no double-fire."""

    async def test_unsolicited_devstatus_goes_to_push_callback(self, monkeypatch):
        # No read in flight for the source IP -> it is an external-change push.
        client, pushes = await _start_client(monkeypatch)

        client._handle_datagram("10.0.0.8", _dev_status_bytes(brightness=77))

        assert pushes == [("10.0.0.8", LanDevStatus(True, 77, RGBColor(255, 0, 0), None))]

    async def test_solicited_reply_routes_to_collector_not_push(self, monkeypatch):
        # A read in flight for the IP claims its reply; the push must NOT also fire.
        client, pushes = await _start_client(monkeypatch)
        collector = lan_client._ReadCollector(asyncio.get_running_loop())
        client._register_read("10.0.0.1", collector)

        client._handle_datagram("10.0.0.1", _dev_status_bytes(brightness=11))

        assert collector.future.done()
        assert collector.future.result().brightness_0_100 == 11
        assert pushes == []  # no double-dispatch
        client._unregister_read("10.0.0.1", collector)

    async def test_in_flight_read_isolates_other_ips_to_push(self, monkeypatch):
        # While a read is in flight for IP A, an unsolicited reply from IP B (no
        # read in flight) still goes to the push callback — correlation is by IP.
        client, pushes = await _start_client(monkeypatch)
        collector = lan_client._ReadCollector(asyncio.get_running_loop())
        client._register_read("10.0.0.1", collector)

        client._handle_datagram("10.0.0.1", _dev_status_bytes(brightness=11))
        client._handle_datagram("10.0.0.2", _dev_status_bytes(brightness=22))

        assert collector.future.result().brightness_0_100 == 11
        assert pushes == [("10.0.0.2", LanDevStatus(True, 22, RGBColor(255, 0, 0), None))]
        client._unregister_read("10.0.0.1", collector)

    async def test_garbage_and_non_devstatus_are_ignored(self, monkeypatch):
        client, pushes = await _start_client(monkeypatch)

        client._handle_datagram("10.0.0.9", b"not-json-garbage{{{")
        client._handle_datagram("10.0.0.9", json.dumps({"msg": {"cmd": "scan", "data": {}}}).encode("utf-8"))
        client._handle_datagram("10.0.0.9", json.dumps({"msg": {"cmd": "devStatus", "data": {}}}).encode("utf-8"))

        # None of these is a well-formed devStatus reply -> nothing dispatched.
        assert pushes == []

    async def test_hostile_datagrams_are_ignored_without_raising(self, monkeypatch):
        # A datagram from any LAN host must never raise out of the socket
        # callback (asyncio would log a traceback per packet).
        client, pushes = await _start_client(monkeypatch)

        client._handle_datagram("10.0.0.9", b"[" * 100000)  # RecursionError in json
        client._handle_datagram(
            "10.0.0.9",
            b'{"msg":{"cmd":"devStatus","data":{"onOff":1,"brightness":1e400,'
            b'"color":{"r":0,"g":0,"b":0},"colorTemInKelvin":0}}}',
        )  # int(inf)
        client._handle_datagram("10.0.0.9", _dev_status_bytes(color={"r": 1e400, "g": 0, "b": 0}))

        assert pushes == []

    async def test_unregister_unknown_ip_is_a_noop(self, monkeypatch):
        # Defensive: unregistering an ip with no in-flight read must not raise.
        client, _ = await _start_client(monkeypatch)
        collector = lan_client._ReadCollector(asyncio.get_running_loop())
        client._unregister_read("10.0.0.123", collector)
        assert client._pending_reads == {}
