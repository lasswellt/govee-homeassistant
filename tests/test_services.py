"""Tests for the ``govee.set_segment_color`` and ``govee.refresh_scenes`` handlers.

The registered service callbacks delegate to the module-level handlers, so the
validation logic is reachable without a live service registry. The device
lookup is monkeypatched per test; ``_get_coordinator_for_device`` returns
``(coordinator, govee_device_id)`` or ``None`` for an unknown device.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.core import Context
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError, Unauthorized

from custom_components.govee.models import RGBColor, SegmentColorCommand
from custom_components.govee.services import (
    SERVICE_SEND_RAW_PTREAL,
    async_refresh_scenes_handler,
    async_send_raw_ptreal_handler,
    async_set_segment_color_handler,
    async_setup_services,
)
from custom_components.govee.const import DOMAIN

LOOKUP = "custom_components.govee.services._get_coordinator_for_device"


def _make_device(segment_count: int, device_id: str = "AA:BB:CC:DD:EE:FF:00:11") -> MagicMock:
    """Build a mock GoveeDevice-like object with the given segment_count."""
    device = MagicMock(name=f"device[{device_id}]")
    device.device_id = device_id
    device.name = "Strip"
    device.segment_count = segment_count
    device.supports_scenes = True
    return device


def _make_coordinator(device: MagicMock | None, device_id: str) -> MagicMock:
    """Build a mock coordinator whose ``devices`` dict yields ``device`` for ``device_id``."""
    coordinator = MagicMock(name="coordinator")
    coordinator.devices = {device_id: device} if device is not None else {}
    coordinator.async_control_device = AsyncMock(return_value=True)
    coordinator.async_get_scenes = AsyncMock(return_value=[])
    coordinator.async_refresh_scenes = AsyncMock()
    return coordinator


def _build_call(device_id: str, segments: list[int], rgb: tuple[int, int, int] = (255, 0, 0)):
    """Build a ServiceCall-like object with the same ``.data`` shape."""
    return SimpleNamespace(
        data={
            "device_id": device_id,
            "segments": list(segments),
            "rgb_color": rgb,
        }
    )


class TestSetSegmentColorService:
    """Validation and dispatch of ``govee.set_segment_color``."""

    async def test_out_of_range_segment_rejected(self, monkeypatch):
        """Out-of-range indices for the H7075 (3 physical segments) raise a validation error."""
        device_id = "AA:BB:CC:DD:EE:FF:00:11"
        device = _make_device(segment_count=3, device_id=device_id)
        coordinator = _make_coordinator(device, device_id)
        monkeypatch.setattr(LOOKUP, lambda hass, raw: (coordinator, device_id))

        with pytest.raises(ServiceValidationError) as excinfo:
            await async_set_segment_color_handler(MagicMock(), _build_call(device_id, segments=[5, 6, 7]))

        assert excinfo.value.translation_key == "segment_out_of_range"
        assert excinfo.value.translation_placeholders["indices"] == "5, 6, 7"
        coordinator.async_control_device.assert_not_called()

    async def test_in_range_segment_accepted(self, monkeypatch):
        """Valid indices dispatch one SegmentColorCommand with the right payload."""
        device_id = "AA:BB:CC:DD:EE:FF:00:22"
        device = _make_device(segment_count=3, device_id=device_id)
        coordinator = _make_coordinator(device, device_id)
        monkeypatch.setattr(LOOKUP, lambda hass, raw: (coordinator, device_id))

        await async_set_segment_color_handler(
            MagicMock(), _build_call(device_id, segments=[0, 1, 2], rgb=(10, 20, 30))
        )

        coordinator.async_control_device.assert_awaited_once()
        sent_device_id, sent_command = coordinator.async_control_device.await_args.args
        assert sent_device_id == device_id
        assert isinstance(sent_command, SegmentColorCommand)
        assert sent_command.segment_indices == (0, 1, 2)
        assert sent_command.color == RGBColor(r=10, g=20, b=30)

    async def test_unknown_device_raises(self, monkeypatch):
        """An unknown device_id raises a validation error naming the ID."""
        monkeypatch.setattr(LOOKUP, lambda hass, raw: None)

        with pytest.raises(ServiceValidationError) as excinfo:
            await async_set_segment_color_handler(MagicMock(), _build_call("missing-device-id", segments=[0]))

        assert excinfo.value.translation_key == "device_not_found"
        assert excinfo.value.translation_placeholders["device_id"] == "missing-device-id"

    async def test_rejected_command_raises(self, monkeypatch):
        """A command the cloud refuses surfaces as HomeAssistantError."""
        device_id = "AA:BB:CC:DD:EE:FF:00:33"
        device = _make_device(segment_count=3, device_id=device_id)
        coordinator = _make_coordinator(device, device_id)
        coordinator.async_control_device = AsyncMock(return_value=False)
        monkeypatch.setattr(LOOKUP, lambda hass, raw: (coordinator, device_id))

        with pytest.raises(HomeAssistantError) as excinfo:
            await async_set_segment_color_handler(MagicMock(), _build_call(device_id, segments=[0]))

        assert excinfo.value.translation_key == "command_failed"


class TestRefreshScenesService:
    """Validation and dispatch of ``govee.refresh_scenes``."""

    async def test_specific_device_refreshes_it(self, monkeypatch):
        device_id = "AA:BB:CC:DD:EE:FF:00:11"
        device = _make_device(segment_count=0, device_id=device_id)
        coordinator = _make_coordinator(device, device_id)
        monkeypatch.setattr(LOOKUP, lambda hass, raw: (coordinator, device_id))

        await async_refresh_scenes_handler(MagicMock(), SimpleNamespace(data={"device_id": device_id}))

        coordinator.async_refresh_scenes.assert_awaited_once_with(device_id)

    async def test_unknown_device_raises(self, monkeypatch):
        monkeypatch.setattr(LOOKUP, lambda hass, raw: None)

        with pytest.raises(ServiceValidationError):
            await async_refresh_scenes_handler(MagicMock(), SimpleNamespace(data={"device_id": "nope"}))

    async def test_no_loaded_entry_raises(self, monkeypatch):
        monkeypatch.setattr("custom_components.govee.services._loaded_coordinators", lambda hass: [])

        with pytest.raises(ServiceValidationError) as excinfo:
            await async_refresh_scenes_handler(MagicMock(), SimpleNamespace(data={}))

        assert excinfo.value.translation_key == "not_loaded"

    async def test_all_devices_refreshed(self, monkeypatch):
        device_id = "AA:BB:CC:DD:EE:FF:00:11"
        device = _make_device(segment_count=0, device_id=device_id)
        coordinator = _make_coordinator(device, device_id)
        monkeypatch.setattr("custom_components.govee.services._loaded_coordinators", lambda hass: [coordinator])

        await async_refresh_scenes_handler(MagicMock(), SimpleNamespace(data={}))

        coordinator.async_refresh_scenes.assert_awaited_once_with(device_id)

    async def test_all_devices_skips_devices_without_any_scenes(self, monkeypatch):
        device_id = "AA:BB:CC:DD:EE:FF:00:11"
        device = _make_device(segment_count=0, device_id=device_id)
        device.supports_scenes = False
        device.supports_diy_scenes = False
        coordinator = _make_coordinator(device, device_id)
        monkeypatch.setattr("custom_components.govee.services._loaded_coordinators", lambda hass: [coordinator])

        await async_refresh_scenes_handler(MagicMock(), SimpleNamespace(data={}))

        coordinator.async_refresh_scenes.assert_not_awaited()


def _build_ptreal_call(device_id: str, frame: str):
    """Build a ServiceCall-like object for ``govee.send_raw_ptreal``."""
    return SimpleNamespace(data={"device_id": device_id, "frame": frame})


class TestSendRawPtrealService:
    """Validation and dispatch of ``govee.send_raw_ptreal``."""

    async def test_short_frame_dispatches_exact_bytes(self, monkeypatch):
        """A short hex frame is parsed and forwarded to the coordinator unchanged."""
        device_id = "AA:BB:CC:DD:EE:FF:00:11"
        device = _make_device(segment_count=0, device_id=device_id)
        coordinator = _make_coordinator(device, device_id)
        coordinator.async_send_raw_ptreal = AsyncMock(return_value=True)
        monkeypatch.setattr(LOOKUP, lambda hass, raw: (coordinator, device_id))

        await async_send_raw_ptreal_handler(MagicMock(), _build_ptreal_call(device_id, "33 05 15 01 FF 00 00"))

        coordinator.async_send_raw_ptreal.assert_awaited_once_with(device_id, bytes.fromhex("33051501FF0000"))

    async def test_spaces_and_colons_accepted(self, monkeypatch):
        """Colons are stripped the same as spaces before parsing hex."""
        device_id = "AA:BB:CC:DD:EE:FF:00:22"
        device = _make_device(segment_count=0, device_id=device_id)
        coordinator = _make_coordinator(device, device_id)
        coordinator.async_send_raw_ptreal = AsyncMock(return_value=True)
        monkeypatch.setattr(LOOKUP, lambda hass, raw: (coordinator, device_id))

        await async_send_raw_ptreal_handler(MagicMock(), _build_ptreal_call(device_id, "33:05:15:01"))

        coordinator.async_send_raw_ptreal.assert_awaited_once_with(device_id, bytes.fromhex("33051501"))

    @pytest.mark.parametrize(
        "frame",
        ["", "ZZ", "330", "3305150111223344556677889900AABBCCDDEE01"],
        ids=["empty", "invalid-hex", "odd-length", "too-long"],
    )
    async def test_invalid_frame_rejected(self, monkeypatch, frame):
        """Empty, invalid hex, odd-length, or over-20-byte frames raise a validation error."""
        coordinator = MagicMock()
        coordinator.async_send_raw_ptreal = AsyncMock()
        monkeypatch.setattr(LOOKUP, lambda hass, raw: (coordinator, "dev"))

        with pytest.raises(ServiceValidationError) as excinfo:
            await async_send_raw_ptreal_handler(MagicMock(), _build_ptreal_call("dev", frame))

        assert excinfo.value.translation_key == "invalid_ptreal_frame"
        assert excinfo.value.translation_placeholders["frame"] == frame
        coordinator.async_send_raw_ptreal.assert_not_called()

    async def test_bad_20_byte_checksum_rejected(self, monkeypatch):
        """A 20-byte frame whose last byte is not the XOR checksum is a validation error."""
        coordinator = MagicMock()
        coordinator.async_send_raw_ptreal = AsyncMock()
        monkeypatch.setattr(LOOKUP, lambda hass, raw: (coordinator, "dev"))
        bad_frame = "00" * 19 + "FF"

        with pytest.raises(ServiceValidationError) as excinfo:
            await async_send_raw_ptreal_handler(MagicMock(), _build_ptreal_call("dev", bad_frame))

        assert excinfo.value.translation_key == "invalid_ptreal_frame"
        coordinator.async_send_raw_ptreal.assert_not_called()

    async def test_valid_20_byte_frame_passes_through(self, monkeypatch):
        """A 20-byte frame with a correct checksum is forwarded as-is."""
        device_id = "AA:BB:CC:DD:EE:FF:00:33"
        device = _make_device(segment_count=0, device_id=device_id)
        coordinator = _make_coordinator(device, device_id)
        coordinator.async_send_raw_ptreal = AsyncMock(return_value=True)
        monkeypatch.setattr(LOOKUP, lambda hass, raw: (coordinator, device_id))
        good_frame = "00" * 19 + "00"

        await async_send_raw_ptreal_handler(MagicMock(), _build_ptreal_call(device_id, good_frame))

        coordinator.async_send_raw_ptreal.assert_awaited_once_with(device_id, bytes.fromhex(good_frame))

    async def test_unknown_device_raises(self, monkeypatch):
        """An unknown device_id raises ``device_not_found``."""
        monkeypatch.setattr(LOOKUP, lambda hass, raw: None)

        with pytest.raises(ServiceValidationError) as excinfo:
            await async_send_raw_ptreal_handler(MagicMock(), _build_ptreal_call("missing-device-id", "33 05"))

        assert excinfo.value.translation_key == "device_not_found"

    async def test_coordinator_failure_raises_ptreal_unavailable(self, monkeypatch):
        """A coordinator False result surfaces as HomeAssistantError ptreal_unavailable."""
        device_id = "AA:BB:CC:DD:EE:FF:00:44"
        device = _make_device(segment_count=0, device_id=device_id)
        coordinator = _make_coordinator(device, device_id)
        coordinator.async_send_raw_ptreal = AsyncMock(return_value=False)
        monkeypatch.setattr(LOOKUP, lambda hass, raw: (coordinator, device_id))

        with pytest.raises(HomeAssistantError) as excinfo:
            await async_send_raw_ptreal_handler(MagicMock(), _build_ptreal_call(device_id, "33 05"))

        assert excinfo.value.translation_key == "ptreal_unavailable"

    async def test_registered_service_delegates_to_handler(self, hass, monkeypatch):
        """The registered service callback invokes the module-level handler."""
        device_id = "AA:BB:CC:DD:EE:FF:00:55"
        device = _make_device(segment_count=0, device_id=device_id)
        coordinator = _make_coordinator(device, device_id)
        coordinator.async_send_raw_ptreal = AsyncMock(return_value=True)
        monkeypatch.setattr(LOOKUP, lambda hass, raw: (coordinator, device_id))

        async_setup_services(hass)
        await hass.services.async_call(
            DOMAIN,
            SERVICE_SEND_RAW_PTREAL,
            {"device_id": device_id, "frame": "33 05"},
            blocking=True,
        )

        coordinator.async_send_raw_ptreal.assert_awaited_once_with(device_id, bytes.fromhex("3305"))

    async def test_non_admin_users_cannot_send_raw_frames(self, hass, hass_read_only_user, monkeypatch):
        """Raw frames can leave a light in an odd mode, so the service is admin-only."""
        device_id = "AA:BB:CC:DD:EE:FF:00:55"
        coordinator = _make_coordinator(_make_device(segment_count=0, device_id=device_id), device_id)
        coordinator.async_send_raw_ptreal = AsyncMock(return_value=True)
        monkeypatch.setattr(LOOKUP, lambda hass, raw: (coordinator, device_id))

        async_setup_services(hass)
        with pytest.raises(Unauthorized):
            await hass.services.async_call(
                DOMAIN,
                SERVICE_SEND_RAW_PTREAL,
                {"device_id": device_id, "frame": "33 05"},
                blocking=True,
                context=Context(user_id=hass_read_only_user.id),
            )

        coordinator.async_send_raw_ptreal.assert_not_awaited()
