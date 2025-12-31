"""Test Govee helpers module."""

from __future__ import annotations

import pytest

from custom_components.govee.helpers.color import (
    rgb_to_int,
    int_to_rgb,
    kelvin_to_rgb,
)
from custom_components.govee.helpers.brightness import (
    brightness_to_api,
    brightness_from_api,
)


class TestColorHelpers:
    """Test color helper functions."""

    def test_rgb_to_int(self):
        """Test RGB to integer conversion."""
        assert rgb_to_int(255, 0, 0) == 0xFF0000  # Red
        assert rgb_to_int(0, 255, 0) == 0x00FF00  # Green
        assert rgb_to_int(0, 0, 255) == 0x0000FF  # Blue
        assert rgb_to_int(255, 255, 255) == 0xFFFFFF  # White
        assert rgb_to_int(0, 0, 0) == 0  # Black
        assert rgb_to_int(255, 128, 64) == 16744512  # Orange

    def test_int_to_rgb(self):
        """Test integer to RGB conversion."""
        assert int_to_rgb(0xFF0000) == (255, 0, 0)  # Red
        assert int_to_rgb(0x00FF00) == (0, 255, 0)  # Green
        assert int_to_rgb(0x0000FF) == (0, 0, 255)  # Blue
        assert int_to_rgb(0xFFFFFF) == (255, 255, 255)  # White
        assert int_to_rgb(0) == (0, 0, 0)  # Black

    def test_rgb_roundtrip(self):
        """Test RGB to int and back."""
        for r, g, b in [(255, 0, 0), (0, 255, 0), (0, 0, 255), (128, 64, 32)]:
            result = int_to_rgb(rgb_to_int(r, g, b))
            assert result == (r, g, b)

    def test_kelvin_to_rgb_warm(self):
        """Test Kelvin conversion for warm white."""
        r, g, b = kelvin_to_rgb(2700)
        assert r == 255  # Red should be maxed for warm
        assert g < r  # Green should be less

    def test_kelvin_to_rgb_daylight(self):
        """Test Kelvin conversion for daylight."""
        r, g, b = kelvin_to_rgb(6500)
        # All channels should be high (close to white)
        assert r >= 200
        assert g >= 200
        assert b >= 200

    def test_kelvin_to_rgb_cool(self):
        """Test Kelvin conversion for cool white."""
        r, g, b = kelvin_to_rgb(9000)
        assert b == 255  # Blue should be maxed for cool
        assert r < b  # Red should be less than blue for cool

    def test_kelvin_to_rgb_clamping(self):
        """Test Kelvin conversion clamps to valid range."""
        # Should not raise for extreme values
        kelvin_to_rgb(500)  # Below minimum
        kelvin_to_rgb(50000)  # Above maximum


class TestBrightnessHelpers:
    """Test brightness helper functions."""

    def test_brightness_to_api_default_range(self):
        """Test brightness conversion to API with default range."""
        assert brightness_to_api(0) == 0
        assert brightness_to_api(255) == 100
        assert brightness_to_api(127) == 50  # Approximately half
        assert brightness_to_api(128) == 50  # Approximately half

    def test_brightness_to_api_custom_range(self):
        """Test brightness conversion to API with custom range."""
        assert brightness_to_api(255, (0, 254)) == 254
        assert brightness_to_api(127, (0, 254)) == 127
        assert brightness_to_api(0, (0, 254)) == 0

    def test_brightness_to_api_clamping(self):
        """Test brightness conversion clamps values."""
        assert brightness_to_api(-10) == 0
        assert brightness_to_api(300) == 100

    def test_brightness_from_api_default_range(self):
        """Test brightness conversion from API with default range."""
        assert brightness_from_api(0) == 0
        assert brightness_from_api(100) == 255
        assert brightness_from_api(50) == 128  # Approximately half

    def test_brightness_from_api_custom_range(self):
        """Test brightness conversion from API with custom range."""
        assert brightness_from_api(254, (0, 254)) == 255
        assert brightness_from_api(127, (0, 254)) == 128  # Approximately half
        assert brightness_from_api(0, (0, 254)) == 0

    def test_brightness_from_api_clamping(self):
        """Test brightness conversion from API clamps values."""
        assert brightness_from_api(-10) == 0
        assert brightness_from_api(200) == 255  # Over max

    def test_brightness_roundtrip(self):
        """Test brightness conversion roundtrip."""
        for brightness in [0, 64, 127, 192, 255]:
            api_value = brightness_to_api(brightness)
            result = brightness_from_api(api_value)
            # Allow for rounding differences
            assert abs(result - brightness) <= 3
