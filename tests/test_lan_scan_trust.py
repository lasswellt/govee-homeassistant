"""LAN scan trusts the datagram source address and caps responders."""

from __future__ import annotations

import json
import logging

from custom_components.govee.api import lan


def _scan(ip: str | None, device: str = "AA:BB:CC:DD:EE:FF:00:01") -> bytes:
    data: dict[str, str] = {"device": device, "sku": "H6008"}
    if ip is not None:
        data["ip"] = ip
    return json.dumps({"msg": {"cmd": "scan", "data": data}}).encode()


def test_source_address_wins_and_reported_ip_kept() -> None:
    proto = lan._ScanProtocol()
    proto.datagram_received(_scan("10.9.9.9"), ("192.168.1.50", 4002))
    record = next(iter(proto.responses.values()))
    assert record["ip"] == "192.168.1.50"
    assert record["reported_ip"] == "10.9.9.9"


def test_missing_body_ip_uses_source() -> None:
    proto = lan._ScanProtocol()
    proto.datagram_received(_scan(None), ("192.168.1.50", 4002))
    record = next(iter(proto.responses.values()))
    assert record["ip"] == "192.168.1.50"
    assert "reported_ip" not in record


def test_gateway_source_keeps_body_ip_and_logs_once(caplog) -> None:
    proto = lan._ScanProtocol(["192.168.1.20"])
    with caplog.at_level(logging.DEBUG, logger=lan.__name__):
        proto.datagram_received(_scan("192.168.1.77"), ("192.168.1.1", 4002))
        proto.datagram_received(_scan("192.168.1.77"), ("192.168.1.1", 4002))
    record = next(iter(proto.responses.values()))
    assert record["ip"] == "192.168.1.77"
    assert "reported_ip" not in record
    assert sum("local gateway" in r.message for r in caplog.records) == 1


def test_non_gateway_mismatch_uses_source_even_with_interfaces() -> None:
    proto = lan._ScanProtocol(["192.168.1.20"])
    proto.datagram_received(_scan("192.168.1.77"), ("192.168.1.99", 4002))
    assert next(iter(proto.responses.values()))["ip"] == "192.168.1.99"


def test_no_known_interfaces_skips_gateway_exception() -> None:
    proto = lan._ScanProtocol()
    proto.datagram_received(_scan("192.168.1.77"), ("192.168.1.1", 4002))
    assert next(iter(proto.responses.values()))["ip"] == "192.168.1.1"


def test_bad_interface_ip_ignored() -> None:
    assert lan._gateway_addresses(["not-an-ip", "10.0.0.5"]) == {"10.0.0.1"}


def test_cap_stops_new_devices_but_updates_known() -> None:
    proto = lan._ScanProtocol()
    for i in range(lan.LAN_SCAN_MAX_RESPONSES + 10):
        proto.datagram_received(_scan("10.0.0.1", device=f"dev{i}"), ("10.0.0.1", 4002))
    assert len(proto.responses) == lan.LAN_SCAN_MAX_RESPONSES
    proto.datagram_received(_scan("10.0.0.1", device="dev0"), ("10.0.0.2", 4002))
    assert proto.responses["dev0"]["ip"] == "10.0.0.2"
