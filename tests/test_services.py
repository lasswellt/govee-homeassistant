"""Tests for custom_components.govee.services module-level handlers.

These tests target ``async_set_segment_color_handler`` (the module-level
extraction of the original ``govee.set_segment_color`` closure handler).
The closure inside ``async_setup_services`` just delegates to this
function with the registered ``hass`` instance, so the validation logic
is reachable without a live service registry.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from custom_components.govee.models import RGBColor, SegmentColorCommand
from custom_components.govee.services import async_set_segment_color_handler


def _make_hass_with_coordinator(
    coordinator: MagicMock | None,
) -> MagicMock:
    """Build a mock hass whose ``_get_coordinator_for_device`` returns ``coordinator``.

    The handler uses ``_get_coordinator_for_device(hass, device_id)`` which
    is module-private; we monkeypatch the function on the services module
    instead, so the mock here only needs to be a sentinel for ``hass``
    identity. The actual lookup is overridden per-test via ``monkeypatch``.
    """
    return MagicMock(name="hass")


def _make_device(segment_count: int, device_id: str = "AA:BB:CC:DD:EE:FF:00:11") -> MagicMock:
    """Build a mock GoveeDevice-like object with the given segment_count."""
    device = MagicMock(name=f"device[{device_id}]")
    device.device_id = device_id
    device.segment_count = segment_count
    return device


def _make_coordinator(device: MagicMock | None, device_id: str) -> MagicMock:
    """Build a mock coordinator whose ``devices`` dict yields ``device`` for ``device_id``."""
    coordinator = MagicMock(name="coordinator")
    coordinator.devices = {device_id: device} if device is not None else {}
    coordinator.async_control_device = AsyncMock(return_value=True)
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
    """Tests for ``async_set_segment_color_handler``.

    Behaviour (per REQ-006 + user prompt T-003):

    * If the device is unknown: log error, do not call
      ``async_control_device``.
    * If any segment index is ``>= device.segment_count``: log warning,
      early-return without dispatching.
    * Otherwise: dispatch exactly one ``SegmentColorCommand`` with the
      supplied indices and RGB tuple.
    """

    async def test_out_of_range_segment_rejected(self, monkeypatch, caplog):
        """Out-of-range indices for the H7075 (3 physical segments) are rejected."""
        device_id = "AA:BB:CC:DD:EE:FF:00:11"
        device = _make_device(segment_count=3, device_id=device_id)
        coordinator = _make_coordinator(device, device_id)
        hass = _make_hass_with_coordinator(coordinator)

        def fake_lookup(hass_arg, lookup_id):
            return coordinator if lookup_id == device_id else None

        monkeypatch.setattr(
            "custom_components.govee.services._get_coordinator_for_device",
            fake_lookup,
        )

        call = _build_call(device_id, segments=[5, 6, 7])
        with caplog.at_level("WARNING", logger="custom_components.govee.services"):
            await async_set_segment_color_handler(hass, call)

        coordinator.async_control_device.assert_not_called()
        assert any(
            "out of range" in record.getMessage().lower()
            or "segment" in record.getMessage().lower()
            for record in caplog.records
        ), f"expected a warning about out-of-range segments, got: {[r.getMessage() for r in caplog.records]}"

    async def test_in_range_segment_accepted(self, monkeypatch):
        """Valid indices dispatch one SegmentColorCommand with the right payload."""
        device_id = "AA:BB:CC:DD:EE:FF:00:22"
        device = _make_device(segment_count=3, device_id=device_id)
        coordinator = _make_coordinator(device, device_id)
        hass = _make_hass_with_coordinator(coordinator)

        def fake_lookup(hass_arg, lookup_id):
            return coordinator if lookup_id == device_id else None

        monkeypatch.setattr(
            "custom_components.govee.services._get_coordinator_for_device",
            fake_lookup,
        )

        call = _build_call(device_id, segments=[0, 1, 2], rgb=(10, 20, 30))
        await async_set_segment_color_handler(hass, call)

        coordinator.async_control_device.assert_awaited_once()
        sent_device_id, sent_command = coordinator.async_control_device.await_args.args
        assert sent_device_id == device_id
        assert isinstance(sent_command, SegmentColorCommand)
        assert sent_command.segment_indices == (0, 1, 2)
        assert sent_command.color == RGBColor(r=10, g=20, b=30)

    async def test_unknown_device_logs_error_and_returns(self, monkeypatch, caplog):
        """Unknown device_id: error logged, no command dispatched, no exception raised."""
        hass = _make_hass_with_coordinator(None)

        def fake_lookup(hass_arg, lookup_id):
            return None

        monkeypatch.setattr(
            "custom_components.govee.services._get_coordinator_for_device",
            fake_lookup,
        )

        call = _build_call("missing-device-id", segments=[0])
        with caplog.at_level("ERROR", logger="custom_components.govee.services"):
            await async_set_segment_color_handler(hass, call)

        assert any(
            "missing-device-id" in record.getMessage()
            for record in caplog.records
        ), f"expected an error mentioning the device_id, got: {[r.getMessage() for r in caplog.records]}"
