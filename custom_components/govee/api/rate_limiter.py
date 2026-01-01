"""Rate limiter for Govee API with dual limits and exponential backoff."""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass

from .const import (
    HEADER_API_RATE_LIMIT_REMAINING,
    HEADER_API_RATE_LIMIT_RESET,
    HEADER_RATE_LIMIT_REMAINING,
    HEADER_RATE_LIMIT_RESET,
    MAX_RATE_LIMIT_WAIT,
    RATE_LIMIT_PER_DAY,
    RATE_LIMIT_PER_MINUTE,
    SECONDS_PER_DAY,
    SECONDS_PER_MINUTE,
)

_LOGGER = logging.getLogger(__name__)

# Exponential backoff defaults
DEFAULT_BACKOFF_BASE = 1.0
DEFAULT_BACKOFF_MAX = 60.0


@dataclass(frozen=True)
class RateLimitStatus:
    """Current rate limit status snapshot."""

    remaining_minute: int
    remaining_day: int
    reset_minute: float | None
    reset_day: float | None
    is_limited: bool
    wait_time: float | None
    consecutive_failures: int


class RateLimiter:
    """Rate limiter for Govee API with dual limits (per-minute and per-day).

    Govee API Rate Limits:
    - Per-minute limit: 100 requests/minute
    - Per-day limit: 10,000 requests/day

    Rate Limiting Algorithm:
    1. Track timestamps of all recent requests (minute and day windows)
    2. Before each request, check if limits would be exceeded
    3. If limit would be exceeded, sleep until oldest request expires
    4. Update limits from API response headers (more accurate than local tracking)

    Exponential Backoff:
    - On API errors, wait exponentially longer before retrying
    - Backoff resets on successful requests
    - Formula: min(base * 2^failures, max_backoff)

    Thread Safety:
    - Uses asyncio.Lock to prevent race conditions
    - All requests must call acquire() before making API calls

    State Tracking:
    - Local tracking: List of request timestamps
    - API tracking: Updated from response headers (authoritative)
    - Returns API values when available, falls back to local estimates
    """

    def __init__(
        self,
        requests_per_minute: int = RATE_LIMIT_PER_MINUTE,
        requests_per_day: int = RATE_LIMIT_PER_DAY,
        backoff_base: float = DEFAULT_BACKOFF_BASE,
        backoff_max: float = DEFAULT_BACKOFF_MAX,
    ) -> None:
        self._per_minute = requests_per_minute
        self._per_day = requests_per_day
        self._backoff_base = backoff_base
        self._backoff_max = backoff_max

        self._minute_timestamps: list[float] = []
        self._day_timestamps: list[float] = []
        self._lock = asyncio.Lock()

        # Updated from API response headers (more accurate than local tracking)
        self._api_remaining_minute: int | None = None
        self._api_remaining_day: int | None = None
        self._api_reset_minute: float | None = None
        self._api_reset_day: float | None = None

        # Exponential backoff state
        self._consecutive_failures: int = 0

    async def acquire(self) -> None:
        """Wait until a request can be made within rate limits.

        Rate Limit Algorithm:
        1. Apply exponential backoff if there were recent failures
        2. Clean expired timestamps (>60s for minute, >24h for day)
        3. Check per-minute limit: wait if at capacity
        4. Check per-day limit: wait if at capacity (max 1 hour)
        5. Record request timestamp

        Waiting Strategy:
        - Backoff: Wait based on consecutive failures
        - Minute limit: Wait exactly until oldest request expires from window
        - Day limit: Wait up to 1 hour (prevents indefinite blocking)

        Thread Safety:
        - Acquires lock before any timestamp operations
        - Ensures atomic check-and-record operations
        """
        async with self._lock:
            # Apply exponential backoff for consecutive failures
            if self._consecutive_failures > 0:
                backoff_time = min(
                    self._backoff_base * (2**self._consecutive_failures),
                    self._backoff_max,
                )
                _LOGGER.debug(
                    "Backoff: waiting %.1fs after %d consecutive failures",
                    backoff_time,
                    self._consecutive_failures,
                )
                await asyncio.sleep(backoff_time)

            now = time.time()

            # Clean expired timestamps
            self._minute_timestamps = [
                t for t in self._minute_timestamps if now - t < SECONDS_PER_MINUTE
            ]
            self._day_timestamps = [
                t for t in self._day_timestamps if now - t < SECONDS_PER_DAY
            ]

            # Check per-minute limit
            if len(self._minute_timestamps) >= self._per_minute:
                wait_time = SECONDS_PER_MINUTE - (now - self._minute_timestamps[0])
                if wait_time > 0:
                    _LOGGER.debug("Rate limit: waiting %.1fs for minute limit", wait_time)
                    await asyncio.sleep(wait_time)
                    now = time.time()
                    self._minute_timestamps = [
                        t for t in self._minute_timestamps if now - t < SECONDS_PER_MINUTE
                    ]

            # Check per-day limit
            if len(self._day_timestamps) >= self._per_day:
                wait_time = SECONDS_PER_DAY - (now - self._day_timestamps[0])
                if wait_time > 0:
                    _LOGGER.warning(
                        "Daily rate limit reached. Waiting up to 1 hour. "
                        "Consider increasing poll interval."
                    )
                    await asyncio.sleep(min(wait_time, MAX_RATE_LIMIT_WAIT))
                    now = time.time()

            # Record this request
            self._minute_timestamps.append(now)
            self._day_timestamps.append(now)

    def update_from_headers(self, headers: dict[str, str]) -> None:
        """Update rate limit state from API response headers."""
        if HEADER_RATE_LIMIT_REMAINING in headers:
            self._api_remaining_minute = int(headers[HEADER_RATE_LIMIT_REMAINING])
        if HEADER_RATE_LIMIT_RESET in headers:
            self._api_reset_minute = float(headers[HEADER_RATE_LIMIT_RESET])
        if HEADER_API_RATE_LIMIT_REMAINING in headers:
            self._api_remaining_day = int(headers[HEADER_API_RATE_LIMIT_REMAINING])
        if HEADER_API_RATE_LIMIT_RESET in headers:
            self._api_reset_day = float(headers[HEADER_API_RATE_LIMIT_RESET])

    def record_success(self) -> None:
        """Record a successful API request, resetting backoff."""
        if self._consecutive_failures > 0:
            _LOGGER.debug(
                "Request succeeded, resetting backoff (was %d failures)",
                self._consecutive_failures,
            )
        self._consecutive_failures = 0

    def record_failure(self) -> None:
        """Record a failed API request, increasing backoff."""
        self._consecutive_failures += 1
        _LOGGER.debug(
            "Request failed, consecutive failures: %d (next backoff: %.1fs)",
            self._consecutive_failures,
            min(self._backoff_base * (2**self._consecutive_failures), self._backoff_max),
        )

    @property
    def remaining_minute(self) -> int:
        """Get remaining requests for current minute."""
        if self._api_remaining_minute is not None:
            return self._api_remaining_minute
        return self._per_minute - len(self._minute_timestamps)

    @property
    def remaining_day(self) -> int:
        """Get remaining requests for current day."""
        if self._api_remaining_day is not None:
            return self._api_remaining_day
        return self._per_day - len(self._day_timestamps)

    @property
    def reset_minute(self) -> float | None:
        """Get timestamp when minute limit resets."""
        return self._api_reset_minute

    @property
    def reset_day(self) -> float | None:
        """Get timestamp when day limit resets."""
        return self._api_reset_day

    @property
    def consecutive_failures(self) -> int:
        """Get current consecutive failure count."""
        return self._consecutive_failures

    @property
    def status(self) -> RateLimitStatus:
        """Get current rate limit status as a snapshot."""
        remaining_min = self.remaining_minute
        remaining_day = self.remaining_day

        # Calculate if we're in a limited state
        is_limited = remaining_min < 5 or remaining_day < 100

        # Calculate wait time if limited
        wait_time: float | None = None
        if remaining_min <= 0 and self._minute_timestamps:
            now = time.time()
            wait_time = SECONDS_PER_MINUTE - (now - self._minute_timestamps[0])
            if wait_time < 0:
                wait_time = None
        elif remaining_day <= 0 and self._day_timestamps:
            now = time.time()
            wait_time = SECONDS_PER_DAY - (now - self._day_timestamps[0])
            if wait_time < 0:
                wait_time = None

        return RateLimitStatus(
            remaining_minute=remaining_min,
            remaining_day=remaining_day,
            reset_minute=self._api_reset_minute,
            reset_day=self._api_reset_day,
            is_limited=is_limited,
            wait_time=wait_time,
            consecutive_failures=self._consecutive_failures,
        )
