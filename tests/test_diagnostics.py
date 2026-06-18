"""Tests for diagnostics module — verifies PII redaction of device IDs."""

from __future__ import annotations

import json
import re
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.govee.diagnostics import (
    TO_REDACT,
    _anonymize_device_id,
    _anonymize_device_keys,
    _looks_like_mac,
    async_get_config_entry_diagnostics,
    async_get_device_diagnostics,
)
from custom_components.govee.models import GoveeDeviceState
from custom_components.govee.models.device import (
    GoveeLeakSensor,
    GoveeLeakSensorState,
)
from custom_components.govee.models.transport import TransportHealth


@pytest.fixture(autouse=True)
def _stub_lan_scan(monkeypatch):
    """Stub the LAN scan so entry-diagnostics tests stay fast + offline (#57).

    async_get_config_entry_diagnostics now runs a real UDP discovery scan;
    default it to "no devices" so the existing tests don't bind a socket or
    wait on the scan timeout. Tests that exercise the LAN block override this.
    """
    monkeypatch.setattr(
        "custom_components.govee.diagnostics.async_scan_lan_devices",
        AsyncMock(return_value=[]),
    )


def _coordinator_stub(**overrides):
    """A coordinator MagicMock with all diagnostics accessors defaulted sanely."""
    coordinator = MagicMock()
    coordinator.devices = {}
    coordinator.get_state = lambda _did: None
    coordinator.mqtt_connected = False
    coordinator.is_ble_available = lambda _did: False
    coordinator.mqtt_client = None
    coordinator.api_client.last_raw_state = {}
    coordinator.api_client.last_raw_devices = []
    coordinator.leak_sensors = {}
    coordinator.leak_states = {}
    coordinator.get_transport_health = lambda _did, _kind: None
    coordinator.has_iot_credentials = False
    coordinator.device_topic_count = 0
    coordinator.api_rate_limit_remaining = 100
    coordinator.api_rate_limit_total = 100
    coordinator.api_rate_limit_reset = 0
    coordinator.scene_cache_count = 0
    coordinator.diy_scene_cache_count = 0
    for key, value in overrides.items():
        setattr(coordinator, key, value)
    return coordinator


def _entry_stub(coordinator):
    entry = MagicMock()
    entry.entry_id = "e"
    entry.version = 1
    entry.data = {}
    entry.options = {}
    entry.runtime_data = coordinator
    return entry


# Govee device-id MAC pattern: 6-8 colon-separated hex octets
_MAC_RE = re.compile(r"\b[0-9A-Fa-f]{2}(?::[0-9A-Fa-f]{2}){5,7}\b")


class TestMacDetection:
    def test_8_octet_mac_matches(self) -> None:
        assert _looks_like_mac("03:9C:DC:06:75:4B:10:7C")

    def test_6_octet_mac_matches(self) -> None:
        assert _looks_like_mac("AA:BB:CC:DD:EE:FF")

    def test_lowercase_mac_matches(self) -> None:
        assert _looks_like_mac("aa:bb:cc:dd:ee:ff")

    def test_numeric_group_id_does_not_match(self) -> None:
        assert not _looks_like_mac("11825917")

    def test_random_string_does_not_match(self) -> None:
        assert not _looks_like_mac("device_001")

    def test_too_few_octets_does_not_match(self) -> None:
        assert not _looks_like_mac("AA:BB:CC")

    def test_too_many_octets_does_not_match(self) -> None:
        assert not _looks_like_mac("00:11:22:33:44:55:66:77:88")


class TestAnonymizeDeviceId:
    def test_returns_stable_short_hash(self) -> None:
        a = _anonymize_device_id("03:9C:DC:06:75:4B:10:7C")
        b = _anonymize_device_id("03:9C:DC:06:75:4B:10:7C")
        assert a == b
        assert a.startswith("device_")
        assert len(a) == len("device_") + 8

    def test_different_macs_yield_different_hashes(self) -> None:
        a = _anonymize_device_id("03:9C:DC:06:75:4B:10:7C")
        b = _anonymize_device_id("03:9C:DC:06:75:4B:10:7D")
        assert a != b


class TestAnonymizeDeviceKeys:
    def test_replaces_mac_keys(self) -> None:
        out = _anonymize_device_keys({"03:9C:DC:06:75:4B:10:7C": {"sku": "H6601"}})
        keys = list(out.keys())
        assert len(keys) == 1
        assert keys[0].startswith("device_")
        assert "03:9C:DC" not in keys[0]

    def test_preserves_non_mac_keys(self) -> None:
        out = _anonymize_device_keys({"11825917": {"sku": "H6004"}, "summary": "x"})
        assert "11825917" in out
        assert "summary" in out

    def test_preserves_values_verbatim(self) -> None:
        payload = {"sku": "H6601", "name": "Living Room"}
        out = _anonymize_device_keys({"03:9C:DC:06:75:4B:10:7C": payload})
        assert next(iter(out.values())) == payload


class TestRedactionSet:
    def test_includes_device_id(self) -> None:
        assert "device_id" in TO_REDACT

    def test_includes_mac(self) -> None:
        assert "mac" in TO_REDACT


class TestDiagnosticsOutput:
    """Regression test for H4: MAC-format device IDs must not leak."""

    @pytest.mark.asyncio
    async def test_no_mac_in_diagnostics_output(self) -> None:
        """A full diagnostics payload must contain no MAC-format substrings.

        Govee uses MAC-derived device IDs as dict keys. Without anonymization,
        every diagnostics dump leaks user device hardware addresses. This test
        renders a representative diagnostics payload and asserts it is clean.
        """
        # Build a coordinator stub with one MAC-keyed device + one group device
        mac_id = "03:9C:DC:06:75:4B:10:7C"
        group_id = "11825917"

        device_mac = MagicMock()
        device_mac.sku = "H6601"
        device_mac.name = "Living Room Lamp"
        device_mac.device_type = "devices.types.light"
        device_mac.is_group = False
        device_mac.capabilities = []

        device_group = MagicMock()
        device_group.sku = "H6004"
        device_group.name = "Bedroom Group"
        device_group.device_type = "devices.types.light"
        device_group.is_group = True
        device_group.capabilities = []

        # Real state object so the full asdict dump path runs (incl. the
        # device_id MAC field, which must be redacted).
        state = GoveeDeviceState.create_empty(mac_id)
        state.sensor_temperature = 23.4
        state.sensor_humidity = 48.0

        # Realistic raw API/MQTT captures whose "device"/"deviceName" carry the
        # MAC + user name — must be redacted out of the dump.
        raw_state_payload = {
            "device": mac_id,
            "sku": "H6601",
            "capabilities": [
                {
                    "type": "devices.capabilities.property",
                    "instance": "sensorTemperature",
                    "state": {"value": 23.4},
                }
            ],
        }
        api_client = MagicMock()
        api_client.last_raw_state = {mac_id: raw_state_payload}
        api_client.last_raw_devices = [
            {"device": mac_id, "sku": "H6601", "deviceName": "Living Room Lamp"}
        ]

        mqtt_client = MagicMock()
        mqtt_client.available = True
        mqtt_client.connected = True
        mqtt_client.last_messages = {mac_id: {"onOff": 1, "sensorTemperature": 2340}}

        coordinator = MagicMock()
        coordinator.devices = {mac_id: device_mac, group_id: device_group}
        coordinator.get_state = lambda did: state if did == mac_id else None
        coordinator.mqtt_connected = True
        coordinator.is_ble_available = lambda _did: False
        coordinator.mqtt_client = mqtt_client
        coordinator.api_client = api_client
        coordinator.api_rate_limit_remaining = 100
        coordinator.api_rate_limit_total = 100
        coordinator.api_rate_limit_reset = 0
        coordinator.scene_cache_count = 0

        entry = MagicMock()
        entry.entry_id = "test_entry"
        entry.version = 1
        entry.data = {"api_key": "secret", "email": "user@example.com"}
        entry.options = {}
        entry.runtime_data = coordinator

        hass = MagicMock()

        out = await async_get_config_entry_diagnostics(hass, entry)
        rendered = json.dumps(out, default=str)

        # The MAC must not appear anywhere — keys, values, or nested strings.
        assert (
            mac_id not in rendered
        ), f"MAC-format device id leaked into diagnostics: {mac_id} found in {rendered}"
        # No 6-or-more-octet MAC pattern anywhere.
        match = _MAC_RE.search(rendered)
        assert match is None, f"MAC-format substring leaked: {match.group(0)!r}"

        # Numeric group IDs are not PII; preserve them.
        assert group_id in rendered

        # API key and email must also be redacted.
        assert "secret" not in rendered
        assert "user@example.com" not in rendered

    @pytest.mark.asyncio
    async def test_raw_captures_present_and_redacted(self) -> None:
        """Raw API/MQTT payloads are included for debugging but redacted.

        The full parsed state (incl. sensor readings) and the verbatim
        /device/state + MQTT payloads must appear, while the MAC inside their
        "device" field is redacted.
        """
        mac_id = "03:9C:DC:06:75:4B:10:7C"

        device = MagicMock()
        device.sku = "H5075"
        device.name = "Office Thermo"
        device.device_type = "devices.types.thermometer"
        device.is_group = False
        device.capabilities = []

        state = GoveeDeviceState.create_empty(mac_id)
        state.sensor_temperature = 23.4
        state.sensor_humidity = 48.0

        api_client = MagicMock()
        api_client.last_raw_state = {
            mac_id: {"device": mac_id, "sku": "H5075", "capabilities": []}
        }
        api_client.last_raw_devices = [{"device": mac_id, "sku": "H5075"}]

        mqtt_client = MagicMock()
        mqtt_client.available = True
        mqtt_client.connected = True
        mqtt_client.last_messages = {mac_id: {"onOff": 1}}

        coordinator = MagicMock()
        coordinator.devices = {mac_id: device}
        coordinator.get_state = lambda _did: state
        coordinator.mqtt_connected = True
        coordinator.is_ble_available = lambda _did: False
        coordinator.mqtt_client = mqtt_client
        coordinator.api_client = api_client
        coordinator.api_rate_limit_remaining = 100
        coordinator.api_rate_limit_total = 100
        coordinator.api_rate_limit_reset = 0
        coordinator.scene_cache_count = 0

        entry = MagicMock()
        entry.entry_id = "e"
        entry.version = 1
        entry.data = {}
        entry.options = {}
        entry.runtime_data = coordinator

        out = await async_get_config_entry_diagnostics(MagicMock(), entry)

        dev = next(iter(out["devices"].values()))
        # Full parsed state carries the sensor readings (the #83 debug signal).
        assert dev["state"]["sensor_temperature"] == 23.4
        assert dev["state"]["sensor_humidity"] == 48.0
        # Raw captures are attached per-device + the device-list at top level.
        assert dev["raw_api_state"] is not None
        assert dev["last_mqtt_message"] == {"onOff": 1}
        assert out["raw_api_devices"] is not None
        assert out["mqtt"]["tracked_devices"] == 1

        # But the MAC in the raw payloads' "device" field is redacted, and the
        # parsed-state device_id MAC is gone too.
        rendered = json.dumps(out, default=str)
        assert mac_id not in rendered
        assert _MAC_RE.search(rendered) is None


class TestLeakAndTransportDump:
    """Entry diagnostics include leak sensors + transport health, redacted."""

    @pytest.mark.asyncio
    async def test_leak_sensors_and_transport_health(self) -> None:
        sensor_mac = "01:32:7A:C4:06:03:0D:0C"
        hub_mac = "09:C2:60:74:F4:64:AB:FA"

        sensor = GoveeLeakSensor(
            device_id=sensor_mac,
            name="Kitchen Sink",
            sku="H5058",
            hub_device_id=hub_mac,
            sno=3,
            hw_version="1.0",
            sw_version="2.1",
        )
        state = GoveeLeakSensorState(is_wet=True, battery=88, gateway_online=True)

        from datetime import datetime, timezone

        recv = datetime(2026, 6, 5, 12, 0, tzinfo=timezone.utc)
        sent = datetime(2026, 6, 5, 12, 1, tzinfo=timezone.utc)
        health = TransportHealth(
            transport="cloud_api",
            is_available=True,
            last_success_ts=recv,
            last_send_ts=sent,
        )

        coordinator = _coordinator_stub(
            leak_sensors={sensor_mac: sensor},
            leak_states={sensor_mac: state},
            get_transport_health=lambda did, kind: (
                health if kind == "cloud_api" else None
            ),
            devices={
                sensor_mac: MagicMock(
                    sku="H5058",
                    name="Kitchen Sink",
                    device_type="devices.types.sensor",
                    is_group=False,
                    capabilities=[],
                )
            },
            get_state=lambda _did: None,
        )

        out = await async_get_config_entry_diagnostics(
            MagicMock(), _entry_stub(coordinator)
        )

        # Leak sensor data is present (battery/is_wet survive — not PII).
        leak = next(iter(out["leak_sensors"].values()))
        assert leak["state"]["is_wet"] is True
        assert leak["state"]["battery"] == 88
        # Transport health surfaced for the device.
        dev = next(iter(out["devices"].values()))
        cloud = dev["transport_health"]["cloud_api"]
        assert cloud["is_available"] is True
        # Directional timestamps surfaced + back-compat alias.
        assert cloud["last_received"] == recv.isoformat()
        assert cloud["last_sent"] == sent.isoformat()
        assert cloud["last_success"] == recv.isoformat()
        # Runtime signals present.
        assert "has_iot_credentials" in out
        assert "diy_scene_cache_count" in out

        # Both the sensor MAC and the hub MAC must be redacted everywhere.
        rendered = json.dumps(out, default=str)
        assert sensor_mac not in rendered
        assert hub_mac not in rendered
        assert _MAC_RE.search(rendered) is None


class TestDeviceDiagnostics:
    """Per-device diagnostics (the ⋮ menu → Download diagnostics path)."""

    @pytest.mark.asyncio
    async def test_regular_device_dump_is_mac_free(self) -> None:
        mac_id = "03:9C:DC:06:75:4B:10:7C"
        device = MagicMock(
            sku="H6601",
            name="Lamp",
            device_type="devices.types.light",
            is_group=False,
            capabilities=[],
        )
        state = GoveeDeviceState.create_empty(mac_id)
        coordinator = _coordinator_stub(
            devices={mac_id: device},
            get_state=lambda _did: state,
        )
        coordinator.api_client.last_raw_state = {
            mac_id: {"device": mac_id, "sku": "H6601"}
        }

        device_entry = MagicMock()
        device_entry.id = "ha_dev_1"
        device_entry.name = "Lamp"
        device_entry.name_by_user = None
        device_entry.identifiers = {("govee", mac_id)}
        device_entry.model = "H6601"
        device_entry.sw_version = None
        device_entry.hw_version = None

        out = await async_get_device_diagnostics(
            MagicMock(), _entry_stub(coordinator), device_entry
        )

        # The targeted device is dumped...
        assert len(out["devices"]) == 1
        # ...and the MAC is gone from keys, identifiers, and raw payloads.
        rendered = json.dumps(out, default=str)
        assert mac_id not in rendered
        assert _MAC_RE.search(rendered) is None

    @pytest.mark.asyncio
    async def test_leak_hub_device_includes_its_sensors(self) -> None:
        hub_mac = "09:C2:60:74:F4:64:AB:FA"
        sensor_mac = "01:32:7A:C4:06:03:0D:0C"
        sensor = GoveeLeakSensor(
            device_id=sensor_mac,
            name="Sink",
            sku="H5058",
            hub_device_id=hub_mac,
            sno=1,
        )
        coordinator = _coordinator_stub(
            leak_sensors={sensor_mac: sensor},
            leak_states={sensor_mac: GoveeLeakSensorState(is_wet=False, battery=50)},
        )

        device_entry = MagicMock()
        device_entry.id = "ha_hub"
        device_entry.name = "Leak Hub"
        device_entry.name_by_user = None
        device_entry.identifiers = {("govee", hub_mac)}
        device_entry.model = "H5043"
        device_entry.sw_version = None
        device_entry.hw_version = None

        out = await async_get_device_diagnostics(
            MagicMock(), _entry_stub(coordinator), device_entry
        )

        # The hub's linked leak sensor is included.
        assert len(out["leak_sensors"]) == 1
        rendered = json.dumps(out, default=str)
        assert hub_mac not in rendered
        assert sensor_mac not in rendered
        assert _MAC_RE.search(rendered) is None


class TestLanDiscoveryDiag:
    """Entry diagnostics include a read-only LAN scan, with IP redacted (#57)."""

    @pytest.mark.asyncio
    async def test_lan_block_present_and_ip_redacted(self, monkeypatch) -> None:
        monkeypatch.setattr(
            "custom_components.govee.diagnostics.async_scan_lan_devices",
            AsyncMock(
                return_value=[
                    {
                        "ip": "192.168.1.23",
                        "device": "1F:80:C5:32:32:36:72:4E",
                        "sku": "H6072",
                        "wifiVersionSoft": "1.02.03",
                    }
                ]
            ),
        )
        coordinator = _coordinator_stub()
        out = await async_get_config_entry_diagnostics(
            MagicMock(), _entry_stub(coordinator)
        )

        lan = out["lan_discovery"]
        assert lan["scan_attempted"] is True
        assert lan["device_count"] == 1
        device = lan["devices"][0]
        # SKU + firmware are kept (the useful signal); IP + MAC are redacted.
        assert device["sku"] == "H6072"
        assert device["wifiVersionSoft"] == "1.02.03"
        assert device["ip"] == "**REDACTED**"
        assert device["device"] == "**REDACTED**"
        rendered = json.dumps(out, default=str)
        assert "192.168.1.23" not in rendered
        assert "1F:80:C5:32:32:36:72:4E" not in rendered

    @pytest.mark.asyncio
    async def test_lan_scan_failure_is_isolated(self, monkeypatch) -> None:
        # A scan error must not break the diagnostics download.
        monkeypatch.setattr(
            "custom_components.govee.diagnostics.async_scan_lan_devices",
            AsyncMock(side_effect=OSError("port 4002 in use")),
        )
        coordinator = _coordinator_stub()
        out = await async_get_config_entry_diagnostics(
            MagicMock(), _entry_stub(coordinator)
        )

        lan = out["lan_discovery"]
        assert lan["scan_attempted"] is True
        assert lan["device_count"] == 0
        assert "port 4002 in use" in lan["error"]
