"""BLE passthrough manager for Govee integration.

Encapsulates MQTT topic management and ptReal publishing for BLE
passthrough commands, extracted from the coordinator to reduce its
responsibility surface.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from .api.ble_packet import (
    FAN_OSC_MULTISYNC_PREFIX,
    build_diy_scene_packet,
    build_dreamview_packet,
    build_fan_oscillation_packet,
    build_music_mode_packet,
    encode_packet_base64,
)

_LOGGER = logging.getLogger(__name__)


class BlePassthroughManager:
    """Manages BLE passthrough commands via MQTT.

    Encapsulates MQTT topic management and ptReal publishing for
    controlling device features not exposed via REST API.
    """

    def __init__(
        self,
        get_mqtt_client: Callable[[], Any],
        device_topics: dict[str, str],
        ensure_device_topic: Callable[[str], Awaitable[str | None]],
    ) -> None:
        """Initialize the BLE passthrough manager.

        Args:
            get_mqtt_client: Callable returning current MQTT client (or None).
            device_topics: Dict mapping device_id -> MQTT topic.
            ensure_device_topic: Async callable to refresh a device's topic.
        """
        self._get_mqtt_client = get_mqtt_client
        self._device_topics = device_topics
        self._ensure_device_topic = ensure_device_topic

    @property
    def available(self) -> bool:
        """Return True if MQTT is connected."""
        client = self._get_mqtt_client()
        return client is not None and client.connected

    async def async_send_ble_packet(
        self,
        device_id: str,
        sku: str,
        encoded_packet: str,
    ) -> bool:
        """Send a base64-encoded BLE packet via MQTT ptReal.

        Args:
            device_id: Device identifier.
            sku: Device SKU.
            encoded_packet: Base64-encoded BLE packet.

        Returns:
            True if packet was sent successfully.
        """
        client = self._get_mqtt_client()
        if client is None:
            return False

        device_topic = await self._ensure_device_topic(device_id)

        result: bool = await client.async_publish_ptreal(
            device_id,
            sku,
            encoded_packet,
            device_topic,
        )
        return result

    async def async_send_music_mode(
        self,
        device_id: str,
        sku: str,
        enabled: bool,
        sensitivity: int = 50,
    ) -> bool:
        """Send music mode command via BLE passthrough.

        Args:
            device_id: Device identifier.
            sku: Device SKU.
            enabled: True to enable, False to disable.
            sensitivity: Microphone sensitivity 0-100.

        Returns:
            True if command was sent successfully.
        """
        packet = build_music_mode_packet(enabled, sensitivity)
        encoded = encode_packet_base64(packet)
        return await self.async_send_ble_packet(device_id, sku, encoded)

    async def async_send_dreamview(
        self,
        device_id: str,
        sku: str,
        enabled: bool,
    ) -> bool:
        """Send DreamView command via BLE passthrough.

        Args:
            device_id: Device identifier.
            sku: Device SKU.
            enabled: True to enable, False to disable.

        Returns:
            True if command was sent successfully.
        """
        packet = build_dreamview_packet(enabled)
        encoded = encode_packet_base64(packet)
        return await self.async_send_ble_packet(device_id, sku, encoded)

    async def async_send_fan_oscillation(
        self,
        device_id: str,
        sku: str,
        enabled: bool,
        swing_tail: list[int] | None = None,
    ) -> bool:
        """Send a Tower-Fan oscillation on/off command via BLE passthrough.

        Sends the ptReal 0x33 0x1d frame and the multiSync 0x3a 0x1d twin
        (homebridge sends both; the fan honours one and the OFF twin is what
        makes "stop" reliable). Returns the ptReal publish result.

        Args:
            device_id: Device identifier.
            sku: Device SKU.
            enabled: True = oscillate, False = hold still.
            swing_tail: 4 swing-range bytes for the ON frame (None = bare ON).

        Returns:
            True if the primary ptReal frame was sent.
        """
        client = self._get_mqtt_client()
        if client is None:
            return False

        device_topic = await self._ensure_device_topic(device_id)

        ptreal_b64 = encode_packet_base64(
            build_fan_oscillation_packet(enabled, swing_tail)
        )
        ok: bool = await client.async_publish_ptreal(
            device_id, sku, ptreal_b64, device_topic
        )

        # multiSync twin (0x3a) — non-fatal if it fails.
        try:
            multi_b64 = encode_packet_base64(
                build_fan_oscillation_packet(
                    enabled, swing_tail, prefix=FAN_OSC_MULTISYNC_PREFIX
                )
            )
            await client.async_publish_command(
                device_topic,
                "multiSync",
                {"command": [multi_b64], "device": device_id, "sku": sku},
            )
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("multiSync oscillation twin failed (non-fatal): %s", err)

        return ok

    async def async_send_diy_scene(
        self,
        device_id: str,
        sku: str,
        scene_id: int,
    ) -> bool:
        """Send DIY scene activation via BLE passthrough.

        Args:
            device_id: Device identifier.
            sku: Device SKU.
            scene_id: DIY scene ID from the API.

        Returns:
            True if command was sent successfully.
        """
        packet = build_diy_scene_packet(scene_id)
        encoded = encode_packet_base64(packet)
        return await self.async_send_ble_packet(device_id, sku, encoded)
