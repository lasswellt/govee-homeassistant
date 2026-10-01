"""Experimental H7175 keep-warm control via the kettle's own command frame.

Keep warm is not in the Developer API. The H7175 sets it with the
``3a 22 <on> <minutes u16> <minutes lo>`` frame the Govee app sends, which
goes out over the AWS IoT passthrough (``ptReal``, see
:meth:`GoveeCoordinator.async_send_raw_ptreal`). Behind
``CONF_KETTLE_FRAME_CONTROL``, off by default.

A write is shown at once and reconciled against the kettle's pushes:

- a frame reporting the requested setting confirms it;
- an ``aa 22`` status that disagrees within OPTIMISTIC_GRACE_CAP_SECONDS is a
  reply already in flight and is ignored; so is the echo of one of HA's own
  earlier, superseded writes;
- any other disagreement (a later status, or the echo of the Govee app's
  command) is the kettle's setting, and the write is dropped with a warning;
- with no confirmation within KETTLE_FRAME_CONFIRM_TIMEOUT (the kettle is
  asked for its status every KETTLE_FRAME_REQUERY_INTERVAL), on/off becomes
  unknown, keeping the requested minutes.

A publish that fails or times out may still have reached the kettle, so it is
treated the same way: on/off unknown, the requested minutes kept, and the
kettle asked at once.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import TYPE_CHECKING

from homeassistant.helpers.event import async_call_later

from ..api.ble_packet import build_packet
from ..const import KETTLE_FRAME_CONFIRM_TIMEOUT, KETTLE_FRAME_REQUERY_INTERVAL, OPTIMISTIC_GRACE_CAP_SECONDS
from .frames import STATUS_PREFIX, KettleFrameReport

if TYPE_CHECKING:
    from ..coordinator import GoveeCoordinator
    from ..models import GoveeDeviceState

_LOGGER = logging.getLogger(__name__)

_COMMAND_KEEP_WARM = (0x3A, 0x22)
# Keep-warm durations the Govee app offers, in minutes; and the duration sent
# when the kettle has not reported one (the app's longest).
KEEP_WARM_DURATIONS = (30, 60, 90, 120)
DEFAULT_KEEP_WARM_MINUTES = 120
# How many of HA's own recent frames per kettle are remembered, and for how
# long, to recognise their echoes.
_SENT_FRAME_MEMORY = 8
_SENT_FRAME_SECONDS = 60


def keep_warm_command(enabled: bool, minutes: int) -> list[int]:
    """``3a 22 <on> <minutes u16 BE> <minutes low byte>``, before padding and checksum."""
    return [*_COMMAND_KEEP_WARM, 0x01 if enabled else 0x00, (minutes >> 8) & 0xFF, minutes & 0xFF, minutes & 0xFF]


class WriteResult(Enum):
    """Outcome of a keep-warm write."""

    SENT = "sent"
    FAILED = "failed"  # publish failed or timed out: state unknown, kettle asked
    UNKNOWN_STATE = "unknown_state"  # "keep on/off as is", but on/off is unknown
    UNAVAILABLE = "unavailable"


@dataclass
class _Pending:
    """A write shown optimistically, awaiting confirmation."""

    enabled: bool
    minutes: int
    sent_at: float  # time.monotonic()
    unsub: Callable[[], None] | None = None
    # The publish was not acknowledged: show on/off as unknown meanwhile.
    uncertain: bool = False

    @property
    def shown(self) -> bool | None:
        """The on/off to show while the write is pending."""
        return None if self.uncertain else self.enabled


class KeepWarmControl:
    """Sends keep-warm frames and reconciles them with the kettle's pushes."""

    def __init__(self, coordinator: GoveeCoordinator) -> None:
        """Initialize for a coordinator."""
        self._coordinator = coordinator
        self._pending: dict[str, _Pending] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._sent: dict[str, deque[tuple[float, bytes]]] = {}
        self._closed = False

    def pending(self, device_id: str) -> tuple[bool, int] | None:
        """The write awaiting confirmation, as ``(enabled, minutes)``."""
        entry = self._pending.get(device_id)
        return (entry.enabled, entry.minutes) if entry is not None else None

    async def async_set(self, device_id: str, enabled: bool | None, minutes: int | None) -> WriteResult:
        """Write keep warm. ``None`` keeps that part as the kettle reports it.

        Everything is decided under the kettle's lock, after any earlier
        write has registered, from the state as it is then.
        """
        coordinator = self._coordinator
        device = coordinator.devices.get(device_id)
        if device is None or not device.decodes_kettle_frames:
            return WriteResult.UNAVAILABLE
        async with self._locks.setdefault(device_id, asyncio.Lock()):
            state = coordinator.get_state(device_id)
            if self._closed or state is None or not coordinator.mqtt_connected:
                return WriteResult.UNAVAILABLE
            if enabled is None:
                if state.kettle_keep_warm_enabled is None:
                    return WriteResult.UNKNOWN_STATE
                enabled = state.kettle_keep_warm_enabled
            if minutes is None:
                minutes = state.kettle_keep_warm_minutes or DEFAULT_KEEP_WARM_MINUTES
            packet = build_packet(keep_warm_command(enabled, minutes))
            # Registered before the send, so a push that races it is matched.
            self._remember(device_id, packet)
            self._replace_pending(device_id, _Pending(enabled, minutes, time.monotonic()))
            self._write(device_id, enabled, minutes)
            sent = await coordinator.async_send_raw_ptreal(device_id, packet)
            pending = self._pending.get(device_id)
            if not sent and pending is not None:
                # It may still have reached the kettle: unknown, and ask now.
                _LOGGER.warning("Keep-warm write to %s was not acknowledged; its state is unknown", device_id)
                pending.uncertain = True
                self._write(device_id, None, minutes)
            await self._async_check(device_id, pending)
            return WriteResult.SENT if sent else WriteResult.FAILED

    def _remember(self, device_id: str, packet: bytes) -> None:
        sent = self._sent.setdefault(device_id, deque(maxlen=_SENT_FRAME_MEMORY))
        sent.append((time.monotonic(), packet))

    def _replace_pending(self, device_id: str, pending: _Pending | None) -> None:
        previous = self._pending.pop(device_id, None)
        if previous is not None and previous.unsub is not None:
            previous.unsub()
        if pending is not None:
            self._pending[device_id] = pending

    def _write(self, device_id: str, enabled: bool | None, minutes: int) -> None:
        """Show a setting on the kettle's current state object."""
        state = self._coordinator.get_state(device_id)
        if state is not None:
            state.kettle_keep_warm_enabled, state.kettle_keep_warm_minutes = enabled, minutes
            self._coordinator.async_update_listeners()

    async def _async_check(self, device_id: str, pending: _Pending | None) -> None:
        """Ask for status and arm the next check, or give up at the deadline."""
        if pending is None or self._closed or self._pending.get(device_id) is not pending:
            return
        elapsed = time.monotonic() - pending.sent_at
        if elapsed >= KETTLE_FRAME_CONFIRM_TIMEOUT:
            self._replace_pending(device_id, None)
            _LOGGER.warning(
                "Kettle %s did not confirm keep warm %s for %s min within %ss; unknown until it reports",
                device_id,
                "on" if pending.enabled else "off",
                pending.minutes,
                int(KETTLE_FRAME_CONFIRM_TIMEOUT),
            )
            self._write(device_id, None, pending.minutes)
            return
        await self._coordinator.async_request_status(device_id)
        if self._closed or self._pending.get(device_id) is not pending:
            return

        async def _next(_now: datetime) -> None:
            await self._async_check(device_id, pending)

        delay = min(KETTLE_FRAME_REQUERY_INTERVAL, KETTLE_FRAME_CONFIRM_TIMEOUT - elapsed)
        pending.unsub = async_call_later(self._coordinator.hass, delay, _next)

    def _own_echo(self, device_id: str, frame: bytes) -> bool:
        cutoff = time.monotonic() - _SENT_FRAME_SECONDS
        return any(at >= cutoff and packet == frame for at, packet in self._sent.get(device_id, ()))

    def on_push(self, device_id: str, state: GoveeDeviceState, report: KettleFrameReport) -> None:
        """Reconcile a pending write with a push already applied to ``state``."""
        pending = self._pending.get(device_id)
        if pending is None or report.keep_warm is None or report.keep_warm_frame is None:
            return
        if report.keep_warm == (pending.enabled, pending.minutes):
            self._replace_pending(device_id, None)
            return
        frame = report.keep_warm_frame
        in_grace = time.monotonic() - pending.sent_at < OPTIMISTIC_GRACE_CAP_SECONDS
        if (frame[0] == STATUS_PREFIX and in_grace) or (
            frame[0] != STATUS_PREFIX and self._own_echo(device_id, frame)
        ):
            state.kettle_keep_warm_enabled, state.kettle_keep_warm_minutes = pending.shown, pending.minutes
            return
        self._replace_pending(device_id, None)
        _LOGGER.warning(
            "Kettle %s reports keep warm %s for %s min, not the %s for %s min requested",
            device_id,
            "on" if report.keep_warm[0] else "off",
            report.keep_warm[1],
            "on" if pending.enabled else "off",
            pending.minutes,
        )

    def reapply(self, device_id: str, state: GoveeDeviceState) -> None:
        """Show a pending write on a state object that replaces the current one (a poll)."""
        pending = self._pending.get(device_id)
        if pending is not None:
            state.kettle_keep_warm_enabled, state.kettle_keep_warm_minutes = pending.shown, pending.minutes

    def async_shutdown(self) -> None:
        """Stop every pending confirmation (entry unload)."""
        self._closed = True
        for device_id in list(self._pending):
            self._replace_pending(device_id, None)
