"""Tests for the API rate-limit diagnostic sensor."""

from __future__ import annotations

from unittest.mock import MagicMock

from custom_components.govee.const import GOVEE_DAILY_REQUEST_LIMIT
from custom_components.govee.sensor import GoveeRateLimitSensor


def _sensor(**overrides):
    """A rate-limit sensor wired to a stub coordinator."""
    coordinator = MagicMock()
    coordinator.api_rate_limit_remaining = overrides.get("remaining", 87)
    coordinator.api_rate_limit_total = overrides.get("total", 100)
    coordinator.api_rate_limit_reset = overrides.get("reset", 1_700_000_000)
    coordinator.api_requests_today = overrides.get("today", 4210)
    coordinator.api_requests_last_24h = overrides.get("last_24h", 35112)
    coordinator.api_requests_per_hour = overrides.get("per_hour", 1463.0)

    sensor = object.__new__(GoveeRateLimitSensor)
    sensor.coordinator = coordinator
    return sensor


class TestRateLimitSensorValue:
    """The sensor's own value stays the per-minute allowance."""

    def test_native_value_is_remaining(self):
        assert _sensor(remaining=87).native_value == 87


class TestRateLimitSensorAttributes:
    """Daily spend is measured locally; Govee never reports it.

    Without these attributes an install can only infer its daily usage from
    arithmetic, which is how a house ends up ~3.5x over the documented cap
    without anything surfacing it.
    """

    def test_keeps_existing_attributes(self):
        attrs = _sensor().extra_state_attributes

        assert attrs["total_limit"] == 100
        assert attrs["reset_time"] == 1_700_000_000

    def test_exposes_daily_spend(self):
        attrs = _sensor(today=4210, last_24h=35112, per_hour=1463.0).extra_state_attributes

        assert attrs["requests_today"] == 4210
        assert attrs["requests_last_24h"] == 35112
        assert attrs["requests_per_hour"] == 1463.0

    def test_publishes_the_documented_cap_to_compare_against(self):
        """A number is only actionable next to the limit it is measured against."""
        attrs = _sensor().extra_state_attributes

        assert attrs["daily_limit"] == GOVEE_DAILY_REQUEST_LIMIT
        assert attrs["daily_limit"] == 10000

    def test_over_cap_is_visible_from_the_attributes_alone(self):
        attrs = _sensor(last_24h=35112).extra_state_attributes

        assert attrs["requests_last_24h"] > attrs["daily_limit"]
