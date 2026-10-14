"""Scene cache manager for Govee integration.

Manages scene and DIY scene caches with TTL, extracted from the coordinator
to reduce its responsibility surface.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from .api.client import GoveeApiClient
from .api.exceptions import GoveeApiError
from .const import DOMAIN
from .models.device import GoveeDevice

_LOGGER = logging.getLogger(__name__)

# Scene cache time-to-live (24 hours)
SCENE_CACHE_TTL = 86400


def scenes_refreshed_signal(device_id: str) -> str:
    """Per-device dispatcher signal sent after a forced scene refresh.

    Carries ``(scenes, diy_scenes)``. The scene / DIY scene selects and the
    light's effect list build their options once at setup, so without this a
    "Refresh scenes" press or ``govee.refresh_scenes`` call would only update
    the cache, never what the user sees.
    """
    return f"{DOMAIN}_scenes_refreshed_{device_id}"


class SceneCacheManager:
    """Manages scene and DIY scene caches with TTL.

    Provides lazy-loading scene data from the Govee API with a 24-hour
    cache to avoid rate limit pressure. Concurrent requests for the same
    device are deduplicated so only one API call is made. Stale entries
    for removed devices are cleaned up when requested.
    """

    def __init__(self, api_client: GoveeApiClient, cache_ttl: int = SCENE_CACHE_TTL) -> None:
        """Initialize the scene cache manager.

        Args:
            api_client: Govee REST API client.
            cache_ttl: Cache time-to-live in seconds (default 24 hours).
        """
        self._api_client = api_client
        self._cache_ttl = cache_ttl

        # Scene cache {device_id: (timestamp, [scenes])}
        self._scene_cache: dict[str, tuple[float, list[dict[str, Any]]]] = {}

        # DIY scene cache {device_id: (timestamp, [scenes])}
        self._diy_scene_cache: dict[str, tuple[float, list[dict[str, Any]]]] = {}

        # In-flight request deduplication
        self._scene_inflight: dict[str, asyncio.Task[list[dict[str, Any]]]] = {}
        self._diy_scene_inflight: dict[str, asyncio.Task[list[dict[str, Any]]]] = {}

    @property
    def scene_cache_count(self) -> int:
        """Return number of devices with cached scenes."""
        return len(self._scene_cache)

    @property
    def diy_scene_cache_count(self) -> int:
        """Return number of devices with cached DIY scenes."""
        return len(self._diy_scene_cache)

    def cleanup_stale(self, active_device_ids: set[str]) -> None:
        """Remove cache entries for devices no longer discovered.

        Args:
            active_device_ids: Set of currently active device IDs.
        """
        stale_ids = set(self._scene_cache) - active_device_ids
        stale_ids |= set(self._diy_scene_cache) - active_device_ids
        for stale_id in stale_ids:
            self._scene_cache.pop(stale_id, None)
            self._diy_scene_cache.pop(stale_id, None)
        if stale_ids:
            _LOGGER.debug("Cleaned scene cache for %d removed devices", len(stale_ids))

    async def async_get_scenes(
        self,
        device_id: str,
        device: GoveeDevice | None,
        refresh: bool = False,
    ) -> list[dict[str, Any]]:
        """Get available scenes for a device.

        Concurrent requests for the same device are deduplicated so only
        one API call is made; additional callers share the result.

        Args:
            device_id: Device identifier.
            device: Device instance (needed for sku on cache miss).
            refresh: Force refresh from API.

        Returns:
            List of scene definitions.
        """
        if not refresh and device_id in self._scene_cache:
            cached_ts, cached_scenes = self._scene_cache[device_id]
            cache_age = time.monotonic() - cached_ts
            if cache_age < self._cache_ttl:
                _LOGGER.debug(
                    "Returning %d cached scenes for %s (age: %ds)",
                    len(cached_scenes),
                    device_id,
                    int(cache_age),
                )
                return cached_scenes
            _LOGGER.debug(
                "Scene cache expired for %s (age: %ds), refreshing",
                device_id,
                int(cache_age),
            )

        if not device:
            _LOGGER.warning("Device %s not found for scene fetch", device_id)
            return []

        # Deduplicate concurrent requests for the same device
        # Shielded so a cancelled caller (an entity being removed mid-add)
        # does not cancel the fetch every other waiter shares.
        if device_id in self._scene_inflight:
            _LOGGER.debug("Joining in-flight scene request for %s", device.name)
            return await asyncio.shield(self._scene_inflight[device_id])

        task = asyncio.ensure_future(self._fetch_and_cache_scenes(device_id, device))
        self._scene_inflight[device_id] = task
        try:
            return await asyncio.shield(task)
        finally:
            if task.done():
                self._scene_inflight.pop(device_id, None)

    async def _fetch_and_cache_scenes(self, device_id: str, device: GoveeDevice) -> list[dict[str, Any]]:
        """Fetch scenes from API and update cache.

        Args:
            device_id: Device identifier.
            device: Device instance.

        Returns:
            List of scene definitions.
        """
        _LOGGER.debug(
            "Fetching scenes from API for %s (sku=%s)",
            device.name,
            device.sku,
        )

        try:
            scenes = await self._api_client.get_dynamic_scenes(device_id, device.sku)
            self._scene_cache[device_id] = (time.monotonic(), scenes)
            _LOGGER.debug(
                "Fetched and cached %d scenes for %s",
                len(scenes),
                device.name,
            )
            return scenes
        except (GoveeApiError, TimeoutError) as err:
            # TimeoutError (a raw aiohttp read timeout, not wrapped in
            # GoveeApiError) must be caught here too: scenes are fetched from
            # light/select ``async_added_to_hass``, so an uncaught timeout there
            # aborts adding the entity entirely (issue #146). Scenes are optional
            # effect support — degrade to cached/empty and let the entity load.
            _LOGGER.error(
                "%s fetching scenes for %s: %s",
                type(err).__name__,
                device.name,
                err,
            )
            # Return cached scenes if available, otherwise empty list
            cached_entry = self._scene_cache.get(device_id)
            cached = cached_entry[1] if cached_entry else []
            _LOGGER.debug("Returning %d cached scenes after error", len(cached))
            return cached

    async def async_get_diy_scenes(
        self,
        device_id: str,
        device: GoveeDevice | None,
        refresh: bool = False,
    ) -> list[dict[str, Any]]:
        """Get available DIY scenes for a device.

        Concurrent requests for the same device are deduplicated so only
        one API call is made; additional callers share the result.

        Args:
            device_id: Device identifier.
            device: Device instance (needed for sku on cache miss).
            refresh: Force refresh from API.

        Returns:
            List of DIY scene definitions.
        """
        if not refresh and device_id in self._diy_scene_cache:
            cached_ts, cached_scenes = self._diy_scene_cache[device_id]
            cache_age = time.monotonic() - cached_ts
            if cache_age < self._cache_ttl:
                _LOGGER.debug(
                    "Returning %d cached DIY scenes for %s (age: %ds)",
                    len(cached_scenes),
                    device_id,
                    int(cache_age),
                )
                return cached_scenes
            _LOGGER.debug(
                "DIY scene cache expired for %s (age: %ds), refreshing",
                device_id,
                int(cache_age),
            )

        if not device:
            _LOGGER.warning("Device %s not found for DIY scene fetch", device_id)
            return []

        # Deduplicate concurrent requests for the same device
        # Shielded for the same reason as the scene fetch above.
        if device_id in self._diy_scene_inflight:
            _LOGGER.debug("Joining in-flight DIY scene request for %s", device.name)
            return await asyncio.shield(self._diy_scene_inflight[device_id])

        task = asyncio.ensure_future(self._fetch_and_cache_diy_scenes(device_id, device))
        self._diy_scene_inflight[device_id] = task
        try:
            return await asyncio.shield(task)
        finally:
            if task.done():
                self._diy_scene_inflight.pop(device_id, None)

    async def _fetch_and_cache_diy_scenes(self, device_id: str, device: GoveeDevice) -> list[dict[str, Any]]:
        """Fetch DIY scenes from API and update cache.

        Args:
            device_id: Device identifier.
            device: Device instance.

        Returns:
            List of DIY scene definitions.
        """
        _LOGGER.debug(
            "Fetching DIY scenes from API for %s (sku=%s)",
            device.name,
            device.sku,
        )

        try:
            scenes = await self._api_client.get_diy_scenes(device_id, device.sku)
            self._diy_scene_cache[device_id] = (time.monotonic(), scenes)
            _LOGGER.debug(
                "Fetched and cached %d DIY scenes for %s",
                len(scenes),
                device.name,
            )
            return scenes
        except (GoveeApiError, TimeoutError) as err:
            # See _fetch_and_cache_scenes: a raw read TimeoutError here would
            # otherwise abort adding the entity (issue #146).
            _LOGGER.error(
                "%s fetching DIY scenes for %s: %s",
                type(err).__name__,
                device.name,
                err,
            )
            # Return cached scenes if available, otherwise empty list
            cached_entry = self._diy_scene_cache.get(device_id)
            cached = cached_entry[1] if cached_entry else []
            _LOGGER.debug("Returning %d cached DIY scenes after error", len(cached))
            return cached
