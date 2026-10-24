"""Test SceneCacheManager in-flight deduplication and caching."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from custom_components.govee.api.exceptions import GoveeApiError
from custom_components.govee.models.device import GoveeDevice
from custom_components.govee.scene_cache import SceneCacheManager, _forget_when_done


@pytest.fixture
def mock_api() -> AsyncMock:
    """Create a mock API client with scene methods."""
    api = AsyncMock()
    api.get_dynamic_scenes = AsyncMock(return_value=[{"name": "Sunrise", "value": {"id": 1}}])
    api.get_diy_scenes = AsyncMock(return_value=[{"name": "DIY Rainbow", "value": {"id": 100}}])
    return api


@pytest.fixture
def device() -> GoveeDevice:
    """Create a minimal device for scene tests."""
    return GoveeDevice(
        device_id="AA:BB:CC:DD:EE:FF:00:11",
        sku="H6072",
        name="Test Light",
        device_type="devices.types.light",
        capabilities=(),
        is_group=False,
    )


@pytest.fixture
def device_b() -> GoveeDevice:
    """Create a second device for multi-device tests."""
    return GoveeDevice(
        device_id="BB:CC:DD:EE:FF:00:11:22",
        sku="H6072",
        name="Test Light B",
        device_type="devices.types.light",
        capabilities=(),
        is_group=False,
    )


# ==============================================================================
# Scene cache hit tests
# ==============================================================================


class TestSceneCacheHit:
    """Test that cache hits return cached data without API call."""

    @pytest.mark.asyncio
    async def test_cache_hit_returns_cached_scenes(self, mock_api: AsyncMock, device: GoveeDevice) -> None:
        """Cache hit returns cached data without making an API call."""
        manager = SceneCacheManager(mock_api)

        # First call populates cache
        result1 = await manager.async_get_scenes(device.device_id, device)
        assert len(result1) == 1
        assert mock_api.get_dynamic_scenes.call_count == 1

        # Second call hits cache
        result2 = await manager.async_get_scenes(device.device_id, device)
        assert result2 == result1
        assert mock_api.get_dynamic_scenes.call_count == 1  # No additional call

    @pytest.mark.asyncio
    async def test_cache_hit_returns_cached_diy_scenes(self, mock_api: AsyncMock, device: GoveeDevice) -> None:
        """DIY scene cache hit returns cached data without making an API call."""
        manager = SceneCacheManager(mock_api)

        result1 = await manager.async_get_diy_scenes(device.device_id, device)
        assert len(result1) == 1
        assert mock_api.get_diy_scenes.call_count == 1

        result2 = await manager.async_get_diy_scenes(device.device_id, device)
        assert result2 == result1
        assert mock_api.get_diy_scenes.call_count == 1

    @pytest.mark.asyncio
    async def test_refresh_bypasses_cache(self, mock_api: AsyncMock, device: GoveeDevice) -> None:
        """Refresh=True bypasses cache and makes a new API call."""
        manager = SceneCacheManager(mock_api)

        await manager.async_get_scenes(device.device_id, device)
        assert mock_api.get_dynamic_scenes.call_count == 1

        await manager.async_get_scenes(device.device_id, device, refresh=True)
        assert mock_api.get_dynamic_scenes.call_count == 2

    @pytest.mark.asyncio
    async def test_diy_refresh_bypasses_cache(self, mock_api: AsyncMock, device: GoveeDevice) -> None:
        """Refresh=True bypasses DIY scene cache and makes a new API call."""
        manager = SceneCacheManager(mock_api)

        await manager.async_get_diy_scenes(device.device_id, device)
        assert mock_api.get_diy_scenes.call_count == 1

        await manager.async_get_diy_scenes(device.device_id, device, refresh=True)
        assert mock_api.get_diy_scenes.call_count == 2


# ==============================================================================
# In-flight deduplication tests
# ==============================================================================


class TestInflightDedup:
    """Test that concurrent requests for the same device share one API call."""

    @pytest.mark.asyncio
    async def test_concurrent_scene_requests_deduplicated(self, mock_api: AsyncMock, device: GoveeDevice) -> None:
        """Two concurrent scene calls for the same device make only one API call."""

        # Make the API call take some time so both callers overlap
        async def slow_fetch(*args, **kwargs):
            await asyncio.sleep(0.05)
            return [{"name": "Sunrise", "value": {"id": 1}}]

        mock_api.get_dynamic_scenes = AsyncMock(side_effect=slow_fetch)
        manager = SceneCacheManager(mock_api)

        results = await asyncio.gather(
            manager.async_get_scenes(device.device_id, device),
            manager.async_get_scenes(device.device_id, device),
        )

        # Both get the same result
        assert results[0] == results[1]
        assert len(results[0]) == 1
        # Only one API call was made
        assert mock_api.get_dynamic_scenes.call_count == 1

    @pytest.mark.asyncio
    async def test_concurrent_diy_scene_requests_deduplicated(self, mock_api: AsyncMock, device: GoveeDevice) -> None:
        """Two concurrent DIY scene calls for the same device make only one API call."""

        async def slow_fetch(*args, **kwargs):
            await asyncio.sleep(0.05)
            return [{"name": "DIY Rainbow", "value": {"id": 100}}]

        mock_api.get_diy_scenes = AsyncMock(side_effect=slow_fetch)
        manager = SceneCacheManager(mock_api)

        results = await asyncio.gather(
            manager.async_get_diy_scenes(device.device_id, device),
            manager.async_get_diy_scenes(device.device_id, device),
        )

        assert results[0] == results[1]
        assert mock_api.get_diy_scenes.call_count == 1

    @pytest.mark.asyncio
    async def test_different_devices_make_separate_calls(
        self,
        mock_api: AsyncMock,
        device: GoveeDevice,
        device_b: GoveeDevice,
    ) -> None:
        """Concurrent calls for different devices make separate API calls."""

        async def slow_fetch(*args, **kwargs):
            await asyncio.sleep(0.05)
            return [{"name": "Sunrise", "value": {"id": 1}}]

        mock_api.get_dynamic_scenes = AsyncMock(side_effect=slow_fetch)
        manager = SceneCacheManager(mock_api)

        await asyncio.gather(
            manager.async_get_scenes(device.device_id, device),
            manager.async_get_scenes(device_b.device_id, device_b),
        )

        assert mock_api.get_dynamic_scenes.call_count == 2

    @pytest.mark.asyncio
    async def test_inflight_cleaned_up_after_completion(self, mock_api: AsyncMock, device: GoveeDevice) -> None:
        """In-flight task is removed after the request completes."""
        manager = SceneCacheManager(mock_api)

        await manager.async_get_scenes(device.device_id, device)
        assert device.device_id not in manager._scene_inflight

    @pytest.mark.asyncio
    async def test_diy_inflight_cleaned_up_after_completion(self, mock_api: AsyncMock, device: GoveeDevice) -> None:
        """DIY in-flight task is removed after the request completes."""
        manager = SceneCacheManager(mock_api)

        await manager.async_get_diy_scenes(device.device_id, device)
        assert device.device_id not in manager._diy_scene_inflight


# ==============================================================================
# Error handling tests
# ==============================================================================


class TestErrorHandling:
    """Test that API errors propagate correctly and fall back to cached data."""

    @pytest.mark.asyncio
    async def test_api_error_propagates_to_all_waiters(self, mock_api: AsyncMock, device: GoveeDevice) -> None:
        """API error returns fallback (empty list) to all concurrent waiters."""

        async def failing_fetch(*args, **kwargs):
            await asyncio.sleep(0.05)
            raise GoveeApiError("rate limited")

        mock_api.get_dynamic_scenes = AsyncMock(side_effect=failing_fetch)
        manager = SceneCacheManager(mock_api)

        results = await asyncio.gather(
            manager.async_get_scenes(device.device_id, device),
            manager.async_get_scenes(device.device_id, device),
        )

        # Both get empty list (no cached data)
        assert results[0] == []
        assert results[1] == []
        # Only one API call was attempted
        assert mock_api.get_dynamic_scenes.call_count == 1

    @pytest.mark.asyncio
    async def test_timeout_returns_fallback_not_raises(self, mock_api: AsyncMock, device: GoveeDevice) -> None:
        """A raw read TimeoutError degrades to empty, not an exception (#146).

        Scenes are fetched from light/select ``async_added_to_hass``; a
        propagating timeout there would abort adding the entity entirely.
        """
        mock_api.get_dynamic_scenes = AsyncMock(side_effect=TimeoutError())
        manager = SceneCacheManager(mock_api)

        result = await manager.async_get_scenes(device.device_id, device)
        assert result == []

    @pytest.mark.asyncio
    async def test_api_error_falls_back_to_cached_data(self, mock_api: AsyncMock, device: GoveeDevice) -> None:
        """API error returns previously cached data."""
        manager = SceneCacheManager(mock_api)

        # Populate cache
        cached = await manager.async_get_scenes(device.device_id, device)
        assert len(cached) == 1

        # Now fail on refresh
        mock_api.get_dynamic_scenes = AsyncMock(side_effect=GoveeApiError("rate limited"))
        result = await manager.async_get_scenes(device.device_id, device, refresh=True)
        assert result == cached

    @pytest.mark.asyncio
    async def test_diy_api_error_falls_back_to_cached_data(self, mock_api: AsyncMock, device: GoveeDevice) -> None:
        """DIY scene API error returns previously cached data."""
        manager = SceneCacheManager(mock_api)

        cached = await manager.async_get_diy_scenes(device.device_id, device)
        assert len(cached) == 1

        mock_api.get_diy_scenes = AsyncMock(side_effect=GoveeApiError("rate limited"))
        result = await manager.async_get_diy_scenes(device.device_id, device, refresh=True)
        assert result == cached

    @pytest.mark.asyncio
    async def test_inflight_cleaned_up_after_error(self, mock_api: AsyncMock, device: GoveeDevice) -> None:
        """In-flight task is removed even when the API call fails."""
        mock_api.get_dynamic_scenes = AsyncMock(side_effect=GoveeApiError("rate limited"))
        manager = SceneCacheManager(mock_api)

        await manager.async_get_scenes(device.device_id, device)
        assert device.device_id not in manager._scene_inflight


# ==============================================================================
# Cancelled first caller
# ==============================================================================


async def _fail_slowly(*_args: object) -> list:
    await asyncio.sleep(0.05)
    raise GoveeApiError("boom")


async def _cancel_first_caller(fetch) -> None:
    caller = asyncio.ensure_future(fetch())
    await asyncio.sleep(0.01)
    caller.cancel()
    with pytest.raises(asyncio.CancelledError):
        await caller
    # Let the shielded fetch finish on its own.
    await asyncio.sleep(0.1)


class TestCancelledFirstCaller:
    """A cancelled first caller must not leave its fetch in the in-flight map."""

    @pytest.mark.asyncio
    async def test_refresh_after_cancelled_first_caller_fetches_again(
        self, mock_api: AsyncMock, device: GoveeDevice
    ) -> None:
        """Refresh hits the API again instead of rejoining the finished, failed fetch."""
        mock_api.get_dynamic_scenes = AsyncMock(side_effect=_fail_slowly)
        manager = SceneCacheManager(mock_api)

        await _cancel_first_caller(lambda: manager.async_get_scenes(device.device_id, device))
        assert device.device_id not in manager._scene_inflight

        fresh = [{"name": "Fresh", "value": {"id": 2}}]
        mock_api.get_dynamic_scenes = AsyncMock(return_value=fresh)
        result = await manager.async_get_scenes(device.device_id, device, refresh=True)

        assert result == fresh
        assert mock_api.get_dynamic_scenes.call_count == 1

    @pytest.mark.asyncio
    async def test_diy_refresh_after_cancelled_first_caller_fetches_again(
        self, mock_api: AsyncMock, device: GoveeDevice
    ) -> None:
        """Same for DIY scenes."""
        mock_api.get_diy_scenes = AsyncMock(side_effect=_fail_slowly)
        manager = SceneCacheManager(mock_api)

        await _cancel_first_caller(lambda: manager.async_get_diy_scenes(device.device_id, device))
        assert device.device_id not in manager._diy_scene_inflight

        fresh = [{"name": "Fresh DIY", "value": {"id": 200}}]
        mock_api.get_diy_scenes = AsyncMock(return_value=fresh)
        result = await manager.async_get_diy_scenes(device.device_id, device, refresh=True)

        assert result == fresh
        assert mock_api.get_diy_scenes.call_count == 1

    @pytest.mark.asyncio
    async def test_finished_task_does_not_evict_a_newer_one(self) -> None:
        """The done callback removes only the task it was registered for."""
        loop = asyncio.get_running_loop()
        old: asyncio.Future = loop.create_future()
        newer: asyncio.Future = loop.create_future()
        inflight = {"dev": newer}
        _forget_when_done(inflight, "dev", old)  # type: ignore[arg-type]

        old.set_result([])
        await asyncio.sleep(0)

        assert inflight == {"dev": newer}
