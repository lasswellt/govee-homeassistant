"""Tests for the read-only Govee LAN discovery helper (issue #57)."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

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
            proto.datagram_received(
                json.dumps(resp).encode("utf-8"), ("192.168.1.50", 4002)
            )

    monkeypatch.setattr(lan.asyncio, "sleep", _sleep)
    return transport, sock


def _scan_response(**data):
    return {"msg": {"cmd": "scan", "data": data}}


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
