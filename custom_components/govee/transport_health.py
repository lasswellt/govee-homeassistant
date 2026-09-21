"""Per-device transport-health tracking.

Extracted from coordinator.py for testability + cohesion (audit H1).
Tracks last-success / last-failure timestamps and availability per
(device, transport) where transport ∈ {cloud_api, mqtt, ble}.

The tracker is callback-free and side-effect-free — it owns the
transport-health dict and the policy for staleness decisions, but does
not know about the coordinator, devices, or BLE/MQTT clients. All
inputs are passed in explicitly.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Iterable

from .const import LAN_STALE_SECONDS
from .models.transport import (
    TRANSPORT_KINDS,
    TransportHealth,
    TransportKind,
)

# Time after which a BLE advertisement is considered stale.
BLE_STALE_SECONDS = 120


class TransportHealthTracker:
    """Owns per-device transport health entries.

    Keys: device_id -> kind -> TransportHealth.
    """

    def __init__(self) -> None:
        self._health: dict[str, dict[TransportKind, TransportHealth]] = {}

    @property
    def health(self) -> dict[str, dict[TransportKind, TransportHealth]]:
        """Read-only view of the underlying dict (mutated in place)."""
        return self._health

    def ensure(self, device_id: str) -> None:
        """Initialize transport-health entries for a device if missing."""
        if device_id in self._health:
            return
        self._health[device_id] = {kind: TransportHealth(transport=kind) for kind in TRANSPORT_KINDS}

    def get(self, device_id: str, transport: TransportKind) -> TransportHealth | None:
        """Return health for (device, transport), or None if untracked."""
        per_device = self._health.get(device_id)
        if per_device is None:
            return None
        return per_device.get(transport)

    def record_success(self, device_id: str, transport: TransportKind) -> None:
        """Stamp a successful inbound transport use (data received)."""
        self.ensure(device_id)
        self._health[device_id][transport].mark_success(datetime.now(timezone.utc))

    def record_read(self, device_id: str, transport: TransportKind) -> None:
        """Stamp a reading from this transport that was applied to device state."""
        self.ensure(device_id)
        self._health[device_id][transport].mark_read(datetime.now(timezone.utc))

    def record_send(self, device_id: str, transport: TransportKind) -> None:
        """Stamp a successful outbound transport use (command sent)."""
        self.ensure(device_id)
        self._health[device_id][transport].mark_send(datetime.now(timezone.utc))

    def record_failure(self, device_id: str, transport: TransportKind, reason: str) -> None:
        """Stamp a failed transport use."""
        self.ensure(device_id)
        self._health[device_id][transport].mark_failure(datetime.now(timezone.utc), reason)

    def refresh_mqtt_for_devices(
        self,
        device_ids: Iterable[str],
        *,
        connected: bool,
        client_configured: bool,
    ) -> None:
        """Propagate MQTT client connection state to per-device entries.

        Doesn't backdate ``last_success_ts``; only real pushes do that.
        """
        for device_id in device_ids:
            self.ensure(device_id)
            mqtt = self._health[device_id]["mqtt"]
            if connected:
                mqtt.is_available = True
                mqtt.last_failure_reason = None
            else:
                mqtt.is_available = False
                if not client_configured:
                    mqtt.last_failure_reason = "not_configured"
                else:
                    mqtt.last_failure_reason = "disconnected"

    def refresh_ble_staleness(
        self,
        device_ids: Iterable[str],
        ble_connected_ids: set[str],
    ) -> None:
        """Mark BLE unavailable when last advertisement exceeds stale window."""
        now = datetime.now(timezone.utc)
        for device_id in device_ids:
            self.ensure(device_id)
            ble = self._health[device_id]["ble"]
            if device_id not in ble_connected_ids:
                ble.is_available = False
                continue
            last = ble.last_success_ts
            if last is None:
                continue
            if (now - last).total_seconds() > BLE_STALE_SECONDS:
                ble.is_available = False
                ble.last_failure_reason = "stale_advertisement"

    def refresh_lan_staleness(
        self,
        device_ids: Iterable[str],
        lan_active_ids: set[str],
    ) -> None:
        """Mark LAN unavailable for absent or stale devices (issue #57).

        Modelled on :meth:`refresh_ble_staleness`. The ``lan`` health entry is
        auto-provisioned for *every* device (groups, sensors, BLE-only devices
        included), but only devices that answered the LAN discovery scan and
        correlated to a ``device_id`` are LAN-active. This gives the otherwise
        blank ``lan`` entry a meaningful failure reason:

        * A device not in ``lan_active_ids`` is marked unavailable with reason
          ``no_lan_presence`` — it never participated in LAN at all (the common
          case for groups, sensors and BLE-only devices).
        * A LAN-active device whose last successful exchange is older than
          :data:`~custom_components.govee.const.LAN_STALE_SECONDS` is marked
          unavailable with reason ``stale_lan`` — it was reachable but has gone
          quiet, so the write-health gate must stop routing writes to it.

        Recording asymmetry vs MQTT (intentional — the LAN control tier relies
        on it): for LAN a confirmed inbound devStatus *read* records success and
        a confirmed *write* records send + success. An UNCONFIRMED write (no
        devStatus reply within ``LAN_WRITE_CONFIRM_TIMEOUT``, or a value
        mismatch) records NO transport-health change — it drives write
        suppression only (``coordinator._note_lan_write_miss``, #57); flipping
        the ``lan`` transport unavailable on confirm misses was the reported flap
        (the sensor dropping to Disconnected the instant a device was turned on),
        so transport health is read-driven. Only a hard ``send_failed`` OSError
        records a LAN transport FAILURE. MQTT, which cannot verify by read,
        stamps a bare send-only success on publish. Do **not** add a send-only
        success for LAN: a verify-by-read transport must never report a write the
        device silently dropped as healthy, or a stranded device would look
        available.

        Args:
            device_ids: Every known device (LAN-active or not).
            lan_active_ids: The subset currently correlated in the LAN map.
        """
        now = datetime.now(timezone.utc)
        for device_id in device_ids:
            self.ensure(device_id)
            lan = self._health[device_id]["lan"]
            if device_id not in lan_active_ids:
                lan.is_available = False
                lan.last_failure_reason = "no_lan_presence"
                continue
            last = lan.last_success_ts
            if last is None:
                continue
            if (now - last).total_seconds() > LAN_STALE_SECONDS:
                lan.is_available = False
                lan.last_failure_reason = "stale_lan"
