"""Short bursts of AWS IoT status queries for one device.

Devices answer a status query on their own MQTT topic with a push, which the
coordinator applies like any other. A burst repeats that query every
``interval`` seconds for ``duration`` seconds, to follow a device closely for
a while, e.g. a kettle heating, on request (``govee.request_status``). Status queries go over MQTT, not the cloud API,
so they cost nothing against the request budget; they skip devices whose
queries are quarantined (issue #195), and a session drop right after one
counts as that device's strike (see ``GoveeCoordinator.async_request_status``).
"""

from __future__ import annotations

import time
from collections.abc import Callable
from datetime import datetime
from typing import TYPE_CHECKING

from homeassistant.helpers.event import async_call_later

if TYPE_CHECKING:
    from .coordinator import GoveeCoordinator

# Limits for a burst, in seconds.
STATUS_BURST_MAX_DURATION = 120
STATUS_BURST_MIN_INTERVAL = 3


class StatusBurst:
    """Repeated status queries per device; a new burst replaces a running one."""

    def __init__(self, coordinator: GoveeCoordinator) -> None:
        """Initialize for a coordinator."""
        self._coordinator = coordinator
        self._timers: dict[str, Callable[[], None]] = {}
        self._generation: dict[str, int] = {}
        self._closed = False

    def running(self, device_id: str) -> bool:
        """Whether a burst is running for the device."""
        return device_id in self._timers

    def start(self, device_id: str, duration: float, interval: float) -> None:
        """Query now, then every ``interval`` seconds until ``duration`` has passed."""
        if self._closed:
            return
        duration = min(duration, STATUS_BURST_MAX_DURATION)
        interval = max(interval, STATUS_BURST_MIN_INTERVAL)
        self.stop(device_id)
        generation = self._generation.get(device_id, 0) + 1
        self._generation[device_id] = generation
        self._arm(device_id, generation, time.monotonic() + duration, interval, 0)

    def _arm(self, device_id: str, generation: int, end: float, interval: float, delay: float) -> None:
        async def _tick(_now: datetime) -> None:
            await self._coordinator.async_request_status(device_id)
            # Stopped, replaced or unloaded while the query was awaited.
            if self._closed or self._generation.get(device_id) != generation:
                return
            if time.monotonic() + interval >= end:
                self._timers.pop(device_id, None)
                return
            self._arm(device_id, generation, end, interval, interval)

        self._timers[device_id] = async_call_later(self._coordinator.hass, delay, _tick)

    def stop(self, device_id: str) -> None:
        """Stop the device's burst, if any."""
        self._generation[device_id] = self._generation.get(device_id, 0) + 1
        unsub = self._timers.pop(device_id, None)
        if unsub is not None:
            unsub()

    def async_shutdown(self) -> None:
        """Stop every burst (entry unload)."""
        self._closed = True
        for device_id in list(self._timers):
            self.stop(device_id)
