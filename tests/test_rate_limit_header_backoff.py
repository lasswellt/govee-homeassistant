"""Back off on the API's reported allowance, not only on a hard 429.

X-RateLimit-Remaining/-Reset were parsed and shown on a sensor but throttled
nothing, so the only way to learn the window was exhausted was to exhaust it.
"""

from __future__ import annotations

import time
from datetime import timedelta
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

from custom_components.govee.api.client import GoveeApiClient
from custom_components.govee.const import MAX_BUDGET_PACED_INTERVAL
from custom_components.govee.coordinator import GoveeCoordinator
from custom_components.govee.request_budget import header_backoff_interval


def _backoff(**overrides: Any) -> int | None:
    kwargs: dict[str, Any] = {
        "remaining": 5,
        "reset_in": 40,
        "requests_per_cycle": 19,
        "base_interval": 60,
        "max_interval": MAX_BUDGET_PACED_INTERVAL,
    }
    kwargs.update(overrides)
    return header_backoff_interval(**kwargs)


def test_backs_off_when_the_allowance_will_not_cover_a_cycle() -> None:
    """5 left, 19 needed: this cycle is the one that would earn the 429."""
    assert _backoff() == 60


def test_waits_for_the_reset_when_that_is_the_longer_wait() -> None:
    assert _backoff(reset_in=200) == 200


def test_backoff_is_capped() -> None:
    assert _backoff(reset_in=100000) == MAX_BUDGET_PACED_INTERVAL


def test_affordable_cycle_proceeds() -> None:
    assert _backoff(remaining=19) is None
    assert _backoff(remaining=100) is None


def test_no_reset_information_means_no_backoff() -> None:
    """Never stall the poll on a header that said nothing."""
    assert _backoff(reset_in=0) is None


def test_nothing_to_poll_means_no_backoff() -> None:
    assert _backoff(requests_per_cycle=0) is None


def _client() -> GoveeApiClient:
    return GoveeApiClient(api_key="k", session=MagicMock())


def test_reset_header_read_as_an_epoch_stamp() -> None:
    client = _client()
    with patch.object(time, "time", return_value=1_000_000.0):
        client.rate_limit_reset = 1_000_045
        assert client.rate_limit_reset_in == 45


def test_reset_header_read_as_a_plain_duration() -> None:
    client = _client()
    with patch.object(time, "time", return_value=1_000_000.0):
        client.rate_limit_reset = 45
        assert client.rate_limit_reset_in == 45


def test_stale_epoch_stamp_reads_as_no_wait() -> None:
    client = _client()
    with patch.object(time, "time", return_value=1_000_000.0):
        client.rate_limit_reset = 999_000
        assert client.rate_limit_reset_in == 0


def test_unset_reset_header_reads_as_no_wait() -> None:
    client = _client()
    assert client.rate_limit_reset == 0
    assert client.rate_limit_reset_in == 0


def _coordinator(*, remaining: Any, reset_in: Any) -> Any:
    return SimpleNamespace(
        _api_client=SimpleNamespace(rate_limit_remaining=remaining, rate_limit_reset_in=reset_in),
        _original_update_interval=timedelta(seconds=60),
        update_interval=timedelta(seconds=60),
    )


def test_coordinator_defers_the_cycle_and_sets_the_interval() -> None:
    coordinator = _coordinator(remaining=3, reset_in=200)
    assert GoveeCoordinator._defer_for_rate_limit_headers(coordinator, 19) is True
    assert coordinator.update_interval == timedelta(seconds=200)


def test_coordinator_proceeds_when_the_allowance_is_ample() -> None:
    coordinator = _coordinator(remaining=90, reset_in=30)
    assert GoveeCoordinator._defer_for_rate_limit_headers(coordinator, 19) is False
    assert coordinator.update_interval == timedelta(seconds=60)


def test_unparseable_headers_never_stall_the_poll() -> None:
    """A malformed header deferring forever would look like a dead poll."""
    coordinator = _coordinator(remaining="not-a-number", reset_in=200)
    assert GoveeCoordinator._defer_for_rate_limit_headers(coordinator, 19) is False
    assert coordinator.update_interval == timedelta(seconds=60)
