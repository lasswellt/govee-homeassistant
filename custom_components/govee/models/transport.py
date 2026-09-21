"""Per-device transport health tracking.

Tracks connectivity status for each transport (Cloud REST API, AWS IoT
MQTT, direct BLE) so user-visible diagnostic entities can reflect which
channels are currently usable for a device.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

TransportKind = Literal["cloud_api", "mqtt", "ble", "lan"]

TRANSPORT_KINDS: tuple[TransportKind, ...] = ("cloud_api", "mqtt", "ble", "lan")


@dataclass
class TransportHealth:
    """Connectivity status for a single (device, transport) pair."""

    transport: TransportKind
    is_available: bool = False
    last_success_ts: datetime | None = None
    last_send_ts: datetime | None = None
    # A reading from this transport that was actually applied to the device's
    # state. Narrower than last_success_ts, which also moves on a write-only
    # send, a BLE command or a readback that was discarded as a mismatch.
    last_read_ts: datetime | None = None
    last_failure_ts: datetime | None = None
    last_failure_reason: str | None = None

    def mark_success(self, now: datetime) -> None:
        """Record a successful inbound use (data received) of this transport.

        ``last_success_ts`` is the receive direction — a poll read or an
        inbound MQTT push. Outbound commands stamp ``last_send_ts`` via
        ``mark_send`` so the two directions stay distinguishable.
        """
        self.is_available = True
        self.last_success_ts = now
        self.last_failure_reason = None

    def mark_read(self, now: datetime) -> None:
        """Record that a reading from this transport was applied to device state."""
        self.last_read_ts = now

    def mark_send(self, now: datetime) -> None:
        """Record a successful outbound use (command sent) of this transport.

        A successful send proves the transport is live, so availability is
        set, but the timestamp lands in ``last_send_ts`` — it does not
        overwrite the receive timestamp ``last_success_ts``.
        """
        self.is_available = True
        self.last_send_ts = now
        self.last_failure_reason = None

    def mark_failure(self, now: datetime, reason: str) -> None:
        """Record a failed use of this transport."""
        self.is_available = False
        self.last_failure_ts = now
        self.last_failure_reason = reason

    def mark_unavailable(self, reason: str | None = None) -> None:
        """Mark this transport as unavailable without stamping a failure time."""
        self.is_available = False
        if reason is not None:
            self.last_failure_reason = reason
