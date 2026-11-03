"""End-to-end LAN transport integration tests (issue #57, story LAN-016).

A loopback-style integration proof that drives a *real* :class:`GoveeCoordinator`
and a *real* :class:`GoveeLanClient` against an IN-PROCESS fake Govee device that
answers ``devStatus`` over a FAKE datagram transport. There is NO real socket and
NO real network: the same fake ``_build_socket`` / ``_build_send_socket`` and
``create_datagram_endpoint`` injection used by ``tests/test_lan_client.py`` and the
LAN ``tests/test_coordinator.py`` suites is reused, plus a fake device "responder"
wired onto the send transport so a ``devStatus`` query is answered synchronously by
feeding the reply straight back through the client's own dispatch.

Because the client and coordinator are the real implementations, these tests
exercise the whole LAN stack end-to-end — parse, source-IP dispatch, the
mode-aware overlay, the verify-by-read write tier, the write-health gate, the
optimistic grace window, DHCP re-correlation, and the BLE>LAN>MQTT>REST
fall-through — rather than any single unit. Each numbered class maps to one of the
behaviours LAN-016 must prove (and, where noted, to a critic blocking-issue
regression):

1. Full poll overlays EXACTLY the four LAN fields onto fresh cloud state and
   preserves active_scene / segments / sensors.
2. A scene-active read does NOT churn color / color_temp (blocking #1).
3. Power and Brightness LAN writes confirm via verify-by-read and record success.
4. Never-stranded: when the device stops answering, staleness marks LAN
   unavailable and a later write falls through LAN -> MQTT -> REST (not lost).
5. A DHCP IP that re-maps to a DIFFERENT device's MAC re-correlates and never
   clobbers the wrong device (blocking #3).
6. Color / ColorTemp commands go over LAN when the readback confirms them, and
   fall back to REST when the device ignores the write (#149, #158).
7. Bootstrap: the FIRST control after startup falls through because LAN health
   defaults unavailable until the first successful read.
"""

from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.govee.api import lan_client
from custom_components.govee.api.lan_client import GoveeLanClient, LanDeviceInfo
from custom_components.govee.const import LAN_STALE_SECONDS
from custom_components.govee.models import (
    BrightnessCommand,
    ColorCommand,
    ColorTempCommand,
    GoveeCapability,
    GoveeDevice,
    GoveeDeviceState,
    PowerCommand,
    RGBColor,
)
from custom_components.govee.models.device import (
    CAPABILITY_ON_OFF,
    CAPABILITY_RANGE,
    INSTANCE_BRIGHTNESS,
    INSTANCE_POWER,
)
from custom_components.govee.models.state import SegmentState

# ==============================================================================
# Fake datagram transport (no real sockets / no real network).
#
# Mirrors the injection helpers in tests/test_lan_client.py: _build_socket and
# _build_send_socket are monkeypatched to hand back fakes, and the running loop's
# create_datagram_endpoint is replaced so no real endpoint is ever created.
# ==============================================================================


class _FakeSocket:
    """A no-op stand-in for ``socket.socket`` that records its operations."""

    def __init__(self, family: Any = None, sock_type: Any = None) -> None:
        """Record nothing yet; capture setsockopt/bind/close as they happen."""
        self.family = family
        self.sock_type = sock_type
        self.setsockopt_calls: list[tuple[int, int, object]] = []
        self.bind_args: object = None
        self.closed = False

    def setsockopt(self, level: int, optname: int, value: object) -> None:
        """Record a setsockopt call (e.g. multicast group membership)."""
        self.setsockopt_calls.append((level, optname, value))

    def setblocking(self, flag: bool) -> None:
        """No-op: the fake socket has no real blocking mode."""

    def bind(self, addr: object) -> None:
        """Record the bind address; never touches the OS."""
        self.bind_args = addr

    def close(self) -> None:
        """Mark the fake socket closed."""
        self.closed = True


class _FakeTransport:
    """A stand-in for ``asyncio.DatagramTransport`` recording every ``sendto``."""

    def __init__(self, sock: _FakeSocket | None) -> None:
        """Wrap the fake socket and start an empty sent-datagram log."""
        self._sock = sock
        self.closed = False
        self.sent: list[tuple[bytes, object]] = []

    def sendto(self, data: bytes, addr: object = None) -> None:
        """Record an outbound datagram (the responder may wrap this)."""
        self.sent.append((data, addr))

    def close(self) -> None:
        """Close the transport and its underlying fake socket."""
        self.closed = True
        if self._sock is not None:
            self._sock.close()

    def get_extra_info(self, name: str, default: object = None) -> object:
        """Return the underlying fake socket for ``"socket"`` lookups."""
        if name == "socket":
            return self._sock
        return default


def _patch_create_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    """Replace the running loop's ``create_datagram_endpoint`` with a fake."""

    async def fake_create(protocol_factory: Any, sock: Any = None) -> Any:
        protocol = protocol_factory()
        transport = _FakeTransport(sock)
        return transport, protocol

    loop = asyncio.get_running_loop()
    monkeypatch.setattr(loop, "create_datagram_endpoint", fake_create)


def _encode(
    *,
    on: bool = True,
    brightness: int = 100,
    color: tuple[int, int, int] = (255, 0, 0),
    color_temp: int = 0,
) -> bytes:
    """Build a Govee ``devStatus`` reply datagram from the four ceiling fields."""
    r, g, b = color
    data = {
        "onOff": 1 if on else 0,
        "brightness": brightness,
        "color": {"r": r, "g": g, "b": b},
        "colorTemInKelvin": color_temp,
    }
    return json.dumps({"msg": {"cmd": "devStatus", "data": data}}).encode("utf-8")


class _FakeDevice:
    """In-process fake Govee device(s) answering LAN over the fake send socket.

    Each registered IP models one device's reported state. The responder wraps the
    real client's send transport: a ``turn`` / ``brightness`` / ``colorwc`` write
    is APPLIED to the device (so a later ``devStatus`` confirm reports the new
    value, exactly as real firmware would) but elicits no reply; a ``devStatus``
    query is answered synchronously by feeding the encoded reply back through the
    client's own ``_handle_datagram`` — unless the device has gone silent.

    ``ignores_color`` models the firmware behind issues #149/#158: it answers
    reads but silently drops colour writes.
    """

    def __init__(self) -> None:
        """Start with no devices and answering enabled."""
        self.devices: dict[str, dict[str, Any]] = {}
        self.silent = False
        self.ignores_color = False

    def add(
        self,
        ip: str,
        *,
        on: bool = True,
        brightness: int = 100,
        color: tuple[int, int, int] = (255, 0, 0),
        color_temp: int = 0,
    ) -> None:
        """Register a device at ``ip`` with an initial reported state."""
        self.devices[ip] = {
            "on": on,
            "brightness": brightness,
            "color": color,
            "color_temp": color_temp,
        }

    def go_silent(self) -> None:
        """Stop answering ``devStatus`` reads (the device drops off the LAN)."""
        self.silent = True

    def attach(self, client: GoveeLanClient) -> None:
        """Wrap the client's send transport so this fake answers its queries."""
        transport = client._send_transport
        base = transport.sendto  # type: ignore[union-attr]

        def sendto(data: bytes, addr: object = None) -> None:
            base(data, addr)
            if not isinstance(addr, tuple):
                return
            ip = addr[0]
            dev = self.devices.get(ip)
            if dev is None:
                return  # nothing answers at this IP
            try:
                payload = json.loads(data.decode("utf-8"))
            except ValueError:  # pragma: no cover - defensive
                return
            msg = payload.get("msg", {})
            cmd = msg.get("cmd")
            body = msg.get("data", {})
            if cmd == "turn":
                dev["on"] = bool(body.get("value"))
                return  # control write: applied, no devStatus reply
            if cmd == "brightness":
                dev["brightness"] = int(body.get("value"))
                return
            if cmd == "colorwc":
                if self.ignores_color:
                    return  # firmware that swallows colour writes (#149)
                kelvin = int(body.get("colorTemInKelvin") or 0)
                if kelvin:
                    dev["color_temp"] = kelvin
                else:
                    rgb = body.get("color") or {}
                    dev["color"] = (
                        int(rgb.get("r", 0)),
                        int(rgb.get("g", 0)),
                        int(rgb.get("b", 0)),
                    )
                    dev["color_temp"] = 0
                return
            if cmd == "devStatus" and not self.silent:
                client._handle_datagram(ip, _encode(**dev))

        transport.sendto = sendto  # type: ignore[union-attr]


# ==============================================================================
# Coordinator + real LAN client wiring helpers
# ==============================================================================


def _light_device(
    device_id: str,
    *,
    brightness_max: int = 100,
    sku: str = "H6072",
    is_group: bool = False,
) -> GoveeDevice:
    """Build a minimal on/off + brightness light device."""
    caps = (
        GoveeCapability(type=CAPABILITY_ON_OFF, instance=INSTANCE_POWER, parameters={}),
        GoveeCapability(
            type=CAPABILITY_RANGE,
            instance=INSTANCE_BRIGHTNESS,
            parameters={"range": {"min": 0, "max": brightness_max}},
        ),
    )
    return GoveeDevice(
        device_id=device_id,
        sku=sku,
        name="Test Light",
        device_type="devices.types.light",
        capabilities=caps,
        is_group=is_group,
    )


def _build_coordinator(devices: dict[str, GoveeDevice]) -> tuple[Any, Any]:
    """Construct a real coordinator pre-populated with ``devices`` (no I/O)."""
    import custom_components.govee.coordinator as coord_mod

    hass = MagicMock()
    config_entry = MagicMock()
    config_entry.entry_id = "integration_entry"
    config_entry.options = {}
    coord = coord_mod.GoveeCoordinator(
        hass=hass,
        config_entry=config_entry,
        api_client=MagicMock(),
        iot_credentials=None,
        poll_interval=60,
    )
    for dev_id, device in devices.items():
        coord._devices[dev_id] = device
        coord._states[dev_id] = GoveeDeviceState.create_empty(dev_id)
        coord._ensure_transport_health(dev_id)
    # The REST control path is the fall-through target; stub it as an awaitable.
    coord._api_client.control_device = AsyncMock(return_value=True)
    # async_set_updated_data needs HA internals we don't have here.
    coord.async_set_updated_data = MagicMock()
    return coord, coord_mod


async def _wire_lan(monkeypatch: pytest.MonkeyPatch, coord: Any) -> tuple[GoveeLanClient, _FakeDevice]:
    """Start a real ``GoveeLanClient`` over fakes and attach a fake device."""
    recv_sock = _FakeSocket()
    send_sock = _FakeSocket()
    monkeypatch.setattr(lan_client, "_build_socket", lambda: recv_sock)
    monkeypatch.setattr(lan_client, "_build_send_socket", lambda: send_sock)
    _patch_create_endpoint(monkeypatch)

    client = GoveeLanClient(coord._on_lan_dev_status)
    await client.async_start([])

    # Shrink the solicited-read collection window: the fake device answers
    # synchronously, so a long real window would only add dead wall-clock time.
    # The underlying real read path (queries + source-IP dispatch) is unchanged.
    real_read_batch = client.async_read_batch

    async def _fast_read_batch(ips: list[str], window: float = 0.01) -> Any:
        return await real_read_batch(ips, window=window)

    client.async_read_batch = _fast_read_batch  # type: ignore[method-assign]

    responder = _FakeDevice()
    responder.attach(client)
    coord._lan_client = client
    return client, responder


def _correlate(coord: Any, device_id: str, ip: str) -> None:
    """Register ``device_id`` as a LAN-correlated device at ``ip``."""
    coord._lan_devices[device_id] = LanDeviceInfo(
        device_id=device_id,
        ip=ip,
        mac=device_id,
        sku="H6072",
        firmware="1.0.0",
        last_correlated_ts=time.monotonic(),
    )


# ==============================================================================
# 1. Full poll overlays EXACTLY the four fields and preserves the rest
# ==============================================================================


class TestFullPollOverlay:
    """A devStatus reply overlays the 4 fields onto fresh cloud state."""

    DEVICE_ID = "AA:BB:CC:DD:EE:FF:00:11"
    IP = "10.0.0.5"

    @pytest.mark.asyncio
    async def test_overlay_four_fields_preserve_scene_segments_sensors(self, monkeypatch: pytest.MonkeyPatch) -> None:
        coord, _ = _build_coordinator({self.DEVICE_ID: _light_device(self.DEVICE_ID)})
        client, responder = await _wire_lan(monkeypatch, coord)
        _correlate(coord, self.DEVICE_ID, self.IP)

        # The cloud poll returns this fresh object; it also carries data that lives
        # OUTSIDE the four-field LAN ceiling (segments + sensors), which the overlay
        # must never touch.
        cloud = GoveeDeviceState.create_empty(self.DEVICE_ID)
        cloud.online = False
        cloud.power_state = False
        cloud.brightness = 30
        cloud.color = RGBColor(255, 0, 0)
        cloud.color_temp_kelvin = None
        cloud.segments = [SegmentState(index=0, color=RGBColor(1, 2, 3))]
        cloud.sensor_temperature = 21.5
        cloud.sensor_humidity = 44
        cloud.battery = 88
        cloud.source = "api"
        coord._api_client.get_device_state = AsyncMock(return_value=cloud)

        # The fake device reports a different live state on all four fields.
        responder.add(self.IP, on=True, brightness=60, color=(0, 255, 0), color_temp=0)

        # Suppress the throttled rescan so this poll only does cloud fan-in + LAN
        # overlay (rescan/correlation is covered by TestDhcpReassignment).
        coord._last_lan_rescan = time.monotonic()

        # Drive a REAL full poll: cloud fan-in THEN LAN overlay (overlay must run
        # on the fresh cloud object, never the reverse).
        result = await coord._async_update_data()

        state = result[self.DEVICE_ID]
        # The four LAN fields were overlaid from the device reply.
        assert state.power_state is True
        assert state.brightness == 60
        assert state.color == RGBColor(0, 255, 0)
        assert state.color_temp_kelvin is None
        assert state.online is True
        assert state.source == "lan"
        # Everything outside the four-field ceiling is preserved untouched.
        assert state.segments == [SegmentState(index=0, color=RGBColor(1, 2, 3))]
        assert state.sensor_temperature == 21.5
        assert state.sensor_humidity == 44
        assert state.battery == 88
        assert state.active_scene is None
        # The inbound read marked LAN healthy.
        health = coord._transport.get(self.DEVICE_ID, "lan")
        assert health is not None and health.is_available is True
        # No real network was touched: the only datagrams were our own queries.
        assert client._send_transport.sent  # devStatus query went out


# ==============================================================================
# 2. Scene-active read does not churn color / color_temp (blocking #1)
# ==============================================================================


class TestSceneActiveNoChurn:
    """A LAN read during an active scene must not rewrite color/color_temp."""

    DEVICE_ID = "AA:BB:CC:DD:EE:FF:00:11"
    IP = "10.0.0.5"

    @pytest.mark.asyncio
    async def test_scene_frame_color_does_not_overwrite_preserved_color(self, monkeypatch: pytest.MonkeyPatch) -> None:
        coord, _ = _build_coordinator({self.DEVICE_ID: _light_device(self.DEVICE_ID)})
        client, responder = await _wire_lan(monkeypatch, coord)
        _correlate(coord, self.DEVICE_ID, self.IP)

        # Device is running a scene; the preserved color/brightness are the real,
        # static last-known values the UI should keep showing.
        state = coord._states[self.DEVICE_ID]
        state.active_scene = "scene-7"
        state.active_scene_name = "Sunset"
        state.power_state = True
        state.brightness = 40
        state.color = RGBColor(255, 0, 0)
        state.color_temp_kelvin = None
        state.source = "api"

        # The fake device reports the live per-frame RGB of the running scene
        # (non-zero, so the {0,0,0} sentinel guard never fires) plus a different
        # brightness — exactly the churn hazard blocking #1 describes.
        responder.add(self.IP, on=True, brightness=90, color=(0, 128, 255))

        await coord._refresh_lan_reads()

        state = coord._states[self.DEVICE_ID]
        # Color / color_temp / brightness are NOT churned by the scene frame.
        assert state.color == RGBColor(255, 0, 0)
        assert state.color_temp_kelvin is None
        assert state.brightness == 40
        # The scene itself is preserved, and only power + liveness updated.
        assert state.active_scene == "scene-7"
        assert state.active_scene_name == "Sunset"
        assert state.power_state is True
        assert state.online is True


# ==============================================================================
# 3. Power + Brightness writes confirm via verify-by-read
# ==============================================================================


class TestVerifiedWrites:
    """Power/Brightness LAN writes confirm by reading the device back."""

    DEVICE_ID = "AA:BB:CC:DD:EE:FF:00:11"
    IP = "10.0.0.5"

    async def _ready(self, monkeypatch: pytest.MonkeyPatch) -> tuple[Any, Any, Any]:
        coord, _ = _build_coordinator({self.DEVICE_ID: _light_device(self.DEVICE_ID)})
        client, responder = await _wire_lan(monkeypatch, coord)
        _correlate(coord, self.DEVICE_ID, self.IP)
        responder.add(self.IP, on=False, brightness=10)
        # One real solicited read marks LAN available (write-health gate) and
        # syncs initial state — the bootstrap fall-through is covered separately.
        await coord._refresh_lan_reads()
        return coord, client, responder

    @pytest.mark.asyncio
    async def test_power_write_confirms_and_skips_rest(self, monkeypatch: pytest.MonkeyPatch) -> None:
        coord, client, _ = await self._ready(monkeypatch)

        result = await coord.async_control_device(self.DEVICE_ID, PowerCommand(power_on=True))

        assert result is True
        # LAN handled it — REST was never reached.
        coord._api_client.control_device.assert_not_called()
        assert coord._states[self.DEVICE_ID].power_state is True
        # The verify-by-read write recorded send + success.
        health = coord._transport.get(self.DEVICE_ID, "lan")
        assert health is not None
        assert health.is_available is True
        assert health.last_send_ts is not None
        assert health.last_failure_reason is None
        # A LAN turn datagram was actually emitted to the device's IP.
        sent_cmds = [json.loads(d.decode())["msg"]["cmd"] for d, _ in client._send_transport.sent]
        assert "turn" in sent_cmds

    @pytest.mark.asyncio
    async def test_brightness_write_confirms_within_tolerance(self, monkeypatch: pytest.MonkeyPatch) -> None:
        coord, client, _ = await self._ready(monkeypatch)

        result = await coord.async_control_device(self.DEVICE_ID, BrightnessCommand(brightness=55))

        assert result is True
        coord._api_client.control_device.assert_not_called()
        assert coord._states[self.DEVICE_ID].brightness == 55
        sent = [json.loads(d.decode())["msg"] for d, _ in client._send_transport.sent]
        assert {"cmd": "brightness", "data": {"value": 55}} in sent


# ==============================================================================
# 4. Never-stranded: stale LAN -> write falls through LAN -> MQTT -> REST
# ==============================================================================


class TestNeverStranded:
    """When the device stops answering, writes still reach REST (not lost)."""

    DEVICE_ID = "AA:BB:CC:DD:EE:FF:00:11"
    IP = "10.0.0.5"

    @pytest.mark.asyncio
    async def test_stale_lan_write_falls_through_lan_mqtt_rest(self, monkeypatch: pytest.MonkeyPatch) -> None:
        coord, _ = _build_coordinator({self.DEVICE_ID: _light_device(self.DEVICE_ID)})
        client, responder = await _wire_lan(monkeypatch, coord)
        _correlate(coord, self.DEVICE_ID, self.IP)
        responder.add(self.IP, on=True, brightness=80)

        # Healthy at first: one real read makes LAN available.
        await coord._refresh_lan_reads()
        assert coord._transport.get(self.DEVICE_ID, "lan").is_available is True

        # Mid-session the device drops off the LAN and time passes without a
        # successful read; staleness then marks LAN unavailable.
        responder.go_silent()
        health = coord._transport.get(self.DEVICE_ID, "lan")
        health.last_success_ts = datetime.now(timezone.utc) - timedelta(seconds=LAN_STALE_SECONDS + 10)
        coord._refresh_lan_staleness()
        assert health.is_available is False
        assert health.last_failure_reason == "stale_lan"

        # Configure the MQTT tier as the middle fall-through step: enabled and
        # connected, but its publish fails — so the write must traverse
        # LAN -> MQTT -> REST and land on REST.
        coord._enable_mqtt_control = True
        mqtt = MagicMock()
        mqtt.connected = True
        mqtt.async_publish_command = AsyncMock(return_value=False)
        coord._mqtt_client = mqtt
        coord._ensure_device_topic = AsyncMock(return_value="GA/topic/x")
        coord._transport.record_success(self.DEVICE_ID, "mqtt")

        client._send_transport.sent.clear()
        command = PowerCommand(power_on=False)
        result = await coord.async_control_device(self.DEVICE_ID, command)

        assert result is True
        # The write-health gate blocked the LAN tier BEFORE any datagram was sent.
        assert client._send_transport.sent == []
        # MQTT was attempted (the middle tier) and fell through...
        mqtt.async_publish_command.assert_awaited()
        # ...to REST, which actually delivered the command — it was NOT lost.
        coord._api_client.control_device.assert_awaited_once()
        rest_args = coord._api_client.control_device.call_args.args
        assert rest_args[0] == self.DEVICE_ID
        assert rest_args[2] is command


# ==============================================================================
# 5. DHCP reassignment re-correlates and never clobbers the wrong device (#3)
# ==============================================================================


class TestDhcpReassignment:
    """An IP that re-maps to a different MAC invalidates the stale mapping."""

    DEVICE_A = "AA:AA:AA:AA:AA:AA:AA:AA"
    DEVICE_B = "BB:BB:BB:BB:BB:BB:BB:BB"
    IP = "10.0.0.5"

    @pytest.mark.asyncio
    async def test_rescan_invalidates_stale_ip_and_preserves_device_a(self, monkeypatch: pytest.MonkeyPatch) -> None:
        coord, coord_mod = _build_coordinator(
            {
                self.DEVICE_A: _light_device(self.DEVICE_A),
                self.DEVICE_B: _light_device(self.DEVICE_B),
            }
        )
        client, responder = await _wire_lan(monkeypatch, coord)

        # Device A currently owns the IP; its state is known-good.
        _correlate(coord, self.DEVICE_A, self.IP)
        state_a = coord._states[self.DEVICE_A]
        state_a.power_state = True
        state_a.brightness = 80
        state_a.color = RGBColor(255, 0, 0)
        state_a.source = "api"

        # DHCP reassigned the IP to device B; the next scan reports B at that IP.
        async def _ifaces(_hass: Any) -> list[str]:
            return []

        async def _broadcasts(_hass: Any) -> list[str]:
            return []

        async def _scan(
            *, interface_ips: Any, extra_targets: Any, broadcast_targets: Any = None
        ) -> list[dict[str, Any]]:
            return [
                {
                    "device": self.DEVICE_B,
                    "ip": self.IP,
                    "sku": "H6072",
                    "wifiVersionSoft": "1.0.0",
                }
            ]

        monkeypatch.setattr(coord_mod, "async_get_lan_interface_ips", _ifaces)
        monkeypatch.setattr(coord_mod, "async_get_lan_broadcast_addresses", _broadcasts)
        # Rescans go through the running client's own socket (no second :4002 bind).
        monkeypatch.setattr(client, "async_scan", _scan)

        coord._request_lan_rescan()  # force the throttled rescan to run
        await coord._async_maybe_rescan_lan()

        # The stale A->IP mapping is invalidated; the IP now belongs to B only.
        assert self.DEVICE_A not in coord._lan_devices
        assert coord._lan_devices[self.DEVICE_B].ip == self.IP

        # A real read from that IP is now applied to B — and must NOT touch A.
        responder.add(self.IP, on=False, brightness=20, color=(0, 0, 255))
        await coord._refresh_lan_reads()

        # Device A was NOT clobbered by the reassigned IP's reply (blocking #3).
        assert coord._states[self.DEVICE_A].power_state is True
        assert coord._states[self.DEVICE_A].brightness == 80
        assert coord._states[self.DEVICE_A].color == RGBColor(255, 0, 0)
        # Device B received the overlay.
        assert coord._states[self.DEVICE_B].power_state is False
        assert coord._states[self.DEVICE_B].brightness == 20
        assert coord._states[self.DEVICE_B].color == RGBColor(0, 0, 255)


# ==============================================================================
# 6. Color / ColorTemp commands never go over LAN (blocking #2)
# ==============================================================================


class TestColorOverLan:
    """Colour and colour-temperature writes prefer LAN, verified by readback.

    Issues #149/#158: Govee's cloud accepts a colorRgb write with HTTP 200 and
    "success" but never delivers it to some strips. A LAN write reaches the
    device directly, and the readback proves whether it landed.
    """

    DEVICE_ID = "AA:BB:CC:DD:EE:FF:00:11"
    IP = "10.0.0.5"

    async def _ready(self, monkeypatch: pytest.MonkeyPatch) -> tuple[Any, Any, Any]:
        coord, _ = _build_coordinator({self.DEVICE_ID: _light_device(self.DEVICE_ID)})
        client, responder = await _wire_lan(monkeypatch, coord)
        _correlate(coord, self.DEVICE_ID, self.IP)
        responder.add(self.IP, on=True, brightness=80)
        await coord._refresh_lan_reads()
        assert coord._transport.get(self.DEVICE_ID, "lan").is_available is True
        client._send_transport.sent.clear()
        return coord, client, responder

    @pytest.mark.asyncio
    async def test_color_command_goes_over_lan(self, monkeypatch: pytest.MonkeyPatch) -> None:
        coord, client, responder = await self._ready(monkeypatch)

        result = await coord.async_control_device(self.DEVICE_ID, ColorCommand(color=RGBColor(0, 0, 255)))

        assert result is True
        sent = [json.loads(d.decode())["msg"] for d, _ in client._send_transport.sent]
        assert {
            "cmd": "colorwc",
            "data": {"color": {"r": 0, "g": 0, "b": 255}, "colorTemInKelvin": 0},
        } in sent
        # The device applied it, so REST was never needed.
        coord._api_client.control_device.assert_not_awaited()
        assert responder.devices[self.IP]["color"] == (0, 0, 255)

    @pytest.mark.asyncio
    async def test_color_temp_command_goes_over_lan(self, monkeypatch: pytest.MonkeyPatch) -> None:
        coord, client, responder = await self._ready(monkeypatch)

        result = await coord.async_control_device(self.DEVICE_ID, ColorTempCommand(kelvin=4000))

        assert result is True
        sent = [json.loads(d.decode())["msg"] for d, _ in client._send_transport.sent]
        assert {
            "cmd": "colorwc",
            "data": {"color": {"r": 0, "g": 0, "b": 0}, "colorTemInKelvin": 4000},
        } in sent
        coord._api_client.control_device.assert_not_awaited()
        assert responder.devices[self.IP]["color_temp"] == 4000

    @pytest.mark.asyncio
    async def test_color_falls_back_to_rest_when_the_device_ignores_it(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # The never-stranded guarantee: firmware that swallows the LAN colour
        # write still reports its old colour, so the readback fails to confirm
        # and REST delivers the command instead.
        coord, client, responder = await self._ready(monkeypatch)
        responder.ignores_color = True

        result = await coord.async_control_device(self.DEVICE_ID, ColorCommand(color=RGBColor(0, 0, 255)))

        assert result is True
        coord._api_client.control_device.assert_awaited_once()
        assert isinstance(coord._api_client.control_device.call_args.args[2], ColorCommand)


# ==============================================================================
# 7. Bootstrap: the first control after startup falls through (documented)
# ==============================================================================


class TestBootstrapFallThrough:
    """LAN health defaults unavailable until the first successful read."""

    DEVICE_ID = "AA:BB:CC:DD:EE:FF:00:11"
    IP = "10.0.0.5"

    @pytest.mark.asyncio
    async def test_first_control_falls_through_to_rest(self, monkeypatch: pytest.MonkeyPatch) -> None:
        coord, _ = _build_coordinator({self.DEVICE_ID: _light_device(self.DEVICE_ID)})
        client, responder = await _wire_lan(monkeypatch, coord)
        # Correlated and reachable, but NO read has happened yet, so the
        # write-health gate (TransportHealth defaults is_available=False) is shut.
        _correlate(coord, self.DEVICE_ID, self.IP)
        responder.add(self.IP, on=True, brightness=80)

        health = coord._transport.get(self.DEVICE_ID, "lan")
        # Documented bootstrap behaviour — this is expected, not breakage.
        assert health is not None and health.is_available is False

        result = await coord.async_control_device(self.DEVICE_ID, PowerCommand(power_on=True))

        assert result is True
        # The first control did NOT go over LAN (gate shut before any send)...
        assert client._send_transport.sent == []
        # ...it fell through to REST, which delivered it.
        coord._api_client.control_device.assert_awaited_once()
        assert coord._api_client.control_device.call_args.args[0] == self.DEVICE_ID


class TestCloudOutage:
    """Cloud failure must not stop local reads or hide a reachable light."""

    @pytest.mark.asyncio
    async def test_outage_updates_local_state_and_availability(self, monkeypatch):
        from custom_components.govee.api.exceptions import GoveeConnectionError
        from custom_components.govee.entity import GoveeEntity
        from custom_components.govee.light import GoveeLightEntity

        local_id = "AA:BB:CC:DD:EE:FF:00:11"
        cloud_id = "AA:BB:CC:DD:EE:FF:00:22"
        ip = "10.0.0.5"
        coord, _ = _build_coordinator({local_id: _light_device(local_id), cloud_id: _light_device(cloud_id)})
        client, responder = await _wire_lan(monkeypatch, coord)
        _correlate(coord, local_id, ip)
        responder.add(ip, on=True, brightness=60)
        coord._last_lan_rescan = time.monotonic()
        coord._api_client.get_device_state = AsyncMock(side_effect=GoveeConnectionError("WAN down"))
        # Use the real refresh wrapper, with background scheduling/discovery disabled.
        coord._async_maybe_rediscover_devices = AsyncMock()
        coord._schedule_refresh = MagicMock()
        coord._states[cloud_id].online = True
        local = GoveeLightEntity(coord, coord._devices[local_id], enable_scenes=False)
        cloud = GoveeLightEntity(coord, coord._devices[cloud_id], enable_scenes=False)
        cloud_feature = GoveeEntity(coord, coord._devices[local_id])
        snapshots = []
        unsub = coord.async_add_listener(
            lambda: snapshots.append((local.available, local.brightness, cloud.available, cloud_feature.available))
        )
        try:
            await coord.async_refresh()
            assert coord.last_update_success is False
            assert snapshots[-1] == (True, 153, False, False)
            assert coord.get_transport_health(local_id, "lan").is_available

            # A second failed poll must still publish changed local state.
            responder.devices[ip]["brightness"] = 20
            await coord.async_refresh()
            assert coord.last_update_success is False
            assert len(snapshots) == 2
            assert snapshots[-1] == (True, 51, False, False)

            # Staleness must be evaluated even while the cloud stays unreachable.
            responder.go_silent()
            coord.get_transport_health(local_id, "lan").last_success_ts = datetime.now(timezone.utc) - timedelta(
                seconds=LAN_STALE_SECONDS + 1
            )
            await coord.async_refresh()
            assert len(snapshots) == 3
            assert snapshots[-1][0] is False
            assert not coord.get_transport_health(local_id, "lan").is_available

            # LAN recovery publishes availability without waiting for WAN recovery.
            responder.silent = False
            await coord.async_refresh()
            assert len(snapshots) == 4
            assert snapshots[-1] == (True, 51, False, False)
            assert coord.last_update_success is False

            # Normal entity controls reach the fake device via verified LAN writes.
            local.async_write_ha_state = MagicMock()
            await local.async_turn_off()
            assert responder.devices[ip]["on"] is False
            await local.async_turn_on(brightness=102)
            assert responder.devices[ip]["on"] is True
            assert responder.devices[ip]["brightness"] == 40
            await local.async_turn_on(rgb_color=(12, 34, 56))
            assert responder.devices[ip]["color"] == (12, 34, 56)
            await local.async_turn_on(color_temp_kelvin=3500)
            assert responder.devices[ip]["color_temp"] == 3500
            coord._api_client.control_device.assert_not_awaited()

            # Cloud recovery restores the normal cloud-dependent entities too.
            async def cloud_recovers(device_id, sku):
                state = GoveeDeviceState.create_empty(device_id)
                state.online = True
                return state

            coord._api_client.get_device_state.side_effect = cloud_recovers
            await coord.async_refresh()
            assert coord.last_update_success is True
            assert snapshots[-1][0] is True
            assert snapshots[-1][2:] == (True, True)
        finally:
            unsub()
            await client.async_stop()

    @pytest.mark.asyncio
    async def test_lan_exception_does_not_skip_staleness_or_mask_cloud_outage(self, monkeypatch):
        from homeassistant.helpers.update_coordinator import UpdateFailed
        from custom_components.govee.api.exceptions import GoveeConnectionError

        device_id = "AA:BB:CC:DD:EE:FF:00:11"
        coord, _ = _build_coordinator({device_id: _light_device(device_id)})
        _correlate(coord, device_id, "10.0.0.5")
        coord._transport.record_success(device_id, "lan")
        coord.get_transport_health(device_id, "lan").last_success_ts = datetime.now(timezone.utc) - timedelta(
            seconds=LAN_STALE_SECONDS + 1
        )
        coord._async_maybe_rescan_lan = AsyncMock(side_effect=OSError("LAN error"))
        coord._api_client.get_device_state = AsyncMock(side_effect=GoveeConnectionError("WAN down"))
        with pytest.raises(UpdateFailed, match="unreachable"):
            await coord._async_update_data()
        assert not coord.get_transport_health(device_id, "lan").is_available
