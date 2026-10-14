"""Govee OpenAPI event-subscription MQTT client.

Subscribes to Govee's official device-event push channel
(https://developer.govee.com/reference/subscribe-device-event):

    Host:     mqtts://mqtt.openapi.govee.com:8883 (TLS required)
    Username: <Developer API key>
    Password: <Developer API key>
    Topic:    GA/<Developer API key>

Unlike the AWS IoT account channel (``mqtt.py``), this needs only the API
key — no email/password login or 2FA. It carries ONLY edge-triggered
``devices.capabilities.event`` pushes (waterFullEvent, lackWaterEvent,
bodyAppearedEvent, ...); there is no state chatter and no documented
"cleared" event, so consumers must decide their own clearing semantics.

Every inbound event is kept in a ring buffer surfaced via the diagnostics
download — the same reverse-engineering pattern as the multiSync buffer in
``mqtt.py`` (#87) — so event shapes for new SKUs can be captured from a
reporter's diagnostics alone (issues #114, #118).
"""

from __future__ import annotations

import asyncio
import json
import logging
import ssl
import time
from collections import deque
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

from .mqtt import STABLE_SESSION_SECONDS, _subscription_refused

try:
    import aiomqtt

    HAS_AIOMQTT = True
except ImportError:
    HAS_AIOMQTT = False

_LOGGER = logging.getLogger(__name__)

OPENAPI_MQTT_HOST = "mqtt.openapi.govee.com"
OPENAPI_MQTT_PORT = 8883
OPENAPI_KEEPALIVE = 60
CONNECTION_TIMEOUT = 30
RECONNECT_BASE = 5
RECONNECT_MAX = 300

# Ring buffer size for the diagnostics event capture.
EVENT_BUFFER_SIZE = 64

# Callback signature: (device_id, sku, instance, state_list) — one call per
# devices.capabilities.event entry in a push.
EventCallback = Callable[[str, str, str, list[dict[str, Any]]], None]


class GoveeOpenApiEventClient:
    """Maintains the OpenAPI event subscription and fans out event pushes."""

    def __init__(self, api_key: str, on_event: EventCallback) -> None:
        """Initialize the client.

        Args:
            api_key: Govee Developer API key (doubles as MQTT username,
                password, and topic suffix).
            on_event: Called on the event loop for each event capability in
                an inbound push.
        """
        self._api_key = api_key
        self._on_event = on_event
        self._running = False
        self._connected = False
        # Warn once per failure streak rather than on every retry.
        self._failure_logged = False
        self._task: asyncio.Task[None] | None = None
        self._recent_events: deque[dict[str, Any]] = deque(maxlen=EVENT_BUFFER_SIZE)

    @property
    def available(self) -> bool:
        """Whether the aiomqtt dependency is importable."""
        return HAS_AIOMQTT

    @property
    def connected(self) -> bool:
        """Whether the subscription is currently established."""
        return self._connected

    @property
    def recent_events(self) -> list[dict[str, Any]]:
        """Recent event pushes for the diagnostics download (oldest first)."""
        return list(self._recent_events)

    async def async_start(self) -> None:
        """Start the connection loop as a background task."""
        if not HAS_AIOMQTT:
            _LOGGER.warning("aiomqtt not available - OpenAPI event channel disabled")
            return
        if self._running:
            return
        self._running = True
        self._task = asyncio.get_running_loop().create_task(self._connection_loop())

    async def async_stop(self) -> None:
        """Stop the connection loop and drop the subscription."""
        self._running = False
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        self._connected = False

    def _create_ssl_context_sync(self) -> ssl.SSLContext:
        """Standard CA-verified TLS context (blocking — run in executor)."""
        return ssl.create_default_context()

    async def _connection_loop(self) -> None:
        """Maintain the OpenAPI MQTT connection with exponential backoff.

        Retries forever (capped at RECONNECT_MAX) rather than giving up: the
        channel is a passive listener, so a long outage should self-heal
        without a repair issue.
        """
        reconnect_interval = RECONNECT_BASE

        while self._running:
            session_started: float | None = None
            try:
                loop = asyncio.get_running_loop()
                ssl_context = await loop.run_in_executor(None, self._create_ssl_context_sync)

                _LOGGER.debug(
                    "Connecting to Govee OpenAPI event broker %s:%d",
                    OPENAPI_MQTT_HOST,
                    OPENAPI_MQTT_PORT,
                )

                async with aiomqtt.Client(
                    hostname=OPENAPI_MQTT_HOST,
                    port=OPENAPI_MQTT_PORT,
                    username=self._api_key,
                    password=self._api_key,
                    tls_context=ssl_context,
                    keepalive=OPENAPI_KEEPALIVE,
                    timeout=CONNECTION_TIMEOUT,
                ) as client:
                    # Only a confirmed subscription counts as connected; a
                    # refused topic would otherwise leave the client looking
                    # healthy while it receives nothing.
                    topic = f"GA/{self._api_key}"
                    granted = await client.subscribe(topic, qos=1)
                    if _subscription_refused(granted):
                        raise aiomqtt.MqttError(f"event topic subscription refused (SUBACK {granted!r})")
                    self._connected = True
                    session_started = time.monotonic()
                    _LOGGER.debug("Subscribed to Govee OpenAPI event channel")
                    # Backoff and the failure streak reset only once the
                    # session has proven stable: a broker that accepts and
                    # then drops at once must keep backing off, not be
                    # hammered every RECONNECT_BASE seconds.

                    async for message in client.messages:
                        if not self._running:
                            break  # type: ignore[unreachable]
                        if reconnect_interval != RECONNECT_BASE or self._failure_logged:
                            reconnect_interval = self._after_stable_session(session_started, reconnect_interval)
                        self._handle_message(message)

            except asyncio.CancelledError:
                _LOGGER.debug("OpenAPI event loop cancelled")
                raise

            except Exception as err:  # noqa: BLE001 - keep the listener alive
                self._connected = False
                if session_started is not None:
                    # A healthy session that was lost is not part of a streak.
                    reconnect_interval = self._after_stable_session(session_started, reconnect_interval)
                if self._running:
                    log = _LOGGER.debug if self._failure_logged else _LOGGER.warning
                    self._failure_logged = True
                    log(
                        "OpenAPI event connection error (%s): %s; retrying in %ds",
                        type(err).__name__,
                        err,
                        reconnect_interval,
                    )
                    await asyncio.sleep(reconnect_interval)
                    reconnect_interval = min(reconnect_interval * 2, RECONNECT_MAX)

        self._connected = False

    def _after_stable_session(self, session_started: float, reconnect_interval: int) -> int:
        """Reset the failure streak once a session has lived STABLE_SESSION_SECONDS.

        Returns the reconnect interval to use from here on.
        """
        if time.monotonic() - session_started < STABLE_SESSION_SECONDS:
            return reconnect_interval
        if self._failure_logged:
            _LOGGER.info("Govee OpenAPI event channel reconnected")
            self._failure_logged = False
        return RECONNECT_BASE

    def _handle_message(self, message: Any) -> None:
        """Record an inbound event push and fan out its event capabilities.

        Documented payload shape (Subscribe Device Event):
        {
            "sku": "H7151",
            "device": "XX:XX:...",
            "deviceName": "...",
            "capabilities": [
                {"type": "devices.capabilities.event",
                 "instance": "waterFullEvent",
                 "state": [{"name": "waterFull", "value": 1,
                            "message": "Water bucket is full or has been pulled out"}]}
            ]
        }
        """
        try:
            payload = message.payload
            if isinstance(payload, (bytes, bytearray)):
                payload = payload.decode("utf-8", "replace")
            data = json.loads(payload)
        except (ValueError, TypeError) as err:
            _LOGGER.debug("Undecodable OpenAPI event payload: %s", err)
            return

        if not isinstance(data, dict):
            return

        # Capture EVERYTHING (even unknown shapes) for the diagnostics ring
        # buffer, timestamped — new-SKU event shapes get reverse-engineered
        # from a diagnostics download alone.
        self._recent_events.append(
            {
                "received_at": datetime.now(timezone.utc).isoformat(),
                "payload": data,
            }
        )

        device_id = data.get("device")
        sku = data.get("sku", "")
        capabilities = data.get("capabilities")
        if not device_id or not isinstance(capabilities, list):
            _LOGGER.debug("OpenAPI event push without device/capabilities: %s", data)
            return

        for cap in capabilities:
            if not isinstance(cap, dict):
                continue
            if cap.get("type") != "devices.capabilities.event":
                continue
            instance = cap.get("instance", "")
            state = cap.get("state")
            state_list = state if isinstance(state, list) else []
            _LOGGER.debug(
                "Govee event push: %s (%s) %s -> %s",
                device_id,
                sku,
                instance,
                state_list,
            )
            try:
                self._on_event(device_id, sku, instance, state_list)
            except Exception as err:  # pragma: no cover - defensive
                _LOGGER.error("OpenAPI event callback failed: %s", err)
