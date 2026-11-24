"""Tests for native LAN control: command mapper + brightness scalers (LAN-004).

The LAN mapper is a pure, I/O-free sibling of ``command_to_mqtt``. Power and
brightness are the only commands routed over LAN; everything else (colour,
colour temperature, scenes, segments, music, DIY, work modes, toggles) and the
H5080/H5083 power-quirk SKUs fall back to REST by returning ``None``.
"""

import pytest

from custom_components.govee.api.lan_control import (
    LAN_BRIGHTNESS_MAX,
    command_to_lan,
    device_brightness_to_lan,
    lan_brightness_to_device,
)
from custom_components.govee.api.mqtt_control import POWER_QUIRK_SKUS
from custom_components.govee.models.commands import (
    BrightnessCommand,
    ColorCommand,
    ColorTempCommand,
    DIYSceneCommand,
    ModeCommand,
    MusicModeCommand,
    OscillationCommand,
    PowerCommand,
    SceneCommand,
    SegmentColorCommand,
    SnapshotCommand,
    ToggleCommand,
    WorkModeCommand,
)
from custom_components.govee.models.state import RGBColor

# A device whose native brightness range already matches the LAN 0-100 scale.
RANGE_0_100 = (0, 100)
# A wide native range (e.g. H6072-class) that requires rescaling.
RANGE_0_254 = (0, 254)


# --------------------------------------------------------------------------- #
# command_to_lan — power
# --------------------------------------------------------------------------- #
class TestCommandToLanPower:
    def test_power_on(self):
        # Arrange / Act
        result = command_to_lan(PowerCommand(power_on=True), "H601F", RANGE_0_100)
        # Assert — LAN uses key "value" with 1/0 (NOT MQTT's "val"/17-16).
        assert result == ("turn", {"value": 1})

    def test_power_off(self):
        result = command_to_lan(PowerCommand(power_on=False), "H601F", RANGE_0_100)
        assert result == ("turn", {"value": 0})

    @pytest.mark.parametrize("sku", sorted(POWER_QUIRK_SKUS))
    def test_power_quirk_sku_falls_back_to_rest(self, sku):
        # The 17/16 power quirk is honoured only by the REST/MQTT path, so the
        # quirk SKUs must NOT be routed over LAN.
        assert command_to_lan(PowerCommand(power_on=True), sku, RANGE_0_100) is None
        assert command_to_lan(PowerCommand(power_on=False), sku, RANGE_0_100) is None

    def test_h60a6_power_off_falls_back_to_cloud(self):
        # H60A6 reboots and comes back on after a LAN turn 0 (#308).
        assert command_to_lan(PowerCommand(power_on=False), "H60A6", RANGE_0_100) is None

    def test_h60a6_power_off_case_insensitive(self):
        assert command_to_lan(PowerCommand(power_on=False), "h60a6", RANGE_0_100) is None

    def test_h60a6_power_on_stays_on_lan(self):
        assert command_to_lan(PowerCommand(power_on=True), "H60A6", RANGE_0_100) == ("turn", {"value": 1})

    def test_other_sku_power_off_unchanged(self):
        assert command_to_lan(PowerCommand(power_on=False), "H6159", RANGE_0_100) == ("turn", {"value": 0})

    def test_quirk_skus_match_mqtt_control(self):
        # Mirror exactly the SKUs mqtt_control special-cases — no drift.
        assert POWER_QUIRK_SKUS == frozenset({"H5080", "H5083"})


# --------------------------------------------------------------------------- #
# command_to_lan — brightness
# --------------------------------------------------------------------------- #
class TestCommandToLanBrightness:
    def test_brightness_identity_range(self):
        result = command_to_lan(BrightnessCommand(brightness=50), "H601F", RANGE_0_100)
        assert result == ("brightness", {"value": 50})

    def test_brightness_rescaled_range(self):
        # 127/254 ~= 50% -> LAN 50.
        result = command_to_lan(BrightnessCommand(brightness=127), "H601F", RANGE_0_254)
        assert result == ("brightness", {"value": 50})

    def test_brightness_full_on_rescaled(self):
        result = command_to_lan(BrightnessCommand(brightness=254), "H601F", RANGE_0_254)
        assert result == ("brightness", {"value": 100})

    def test_brightness_zero(self):
        result = command_to_lan(BrightnessCommand(brightness=0), "H601F", RANGE_0_254)
        assert result == ("brightness", {"value": 0})


# --------------------------------------------------------------------------- #
# command_to_lan — everything else returns None
# --------------------------------------------------------------------------- #
class TestCommandToLanReturnsNone:
    @pytest.mark.parametrize(
        "command",
        [
            # Scenes / DIY / snapshots: no LAN readback.
            SceneCommand(scene_id=1, scene_name="Sunset"),
            DIYSceneCommand(scene_id=7, scene_name="Mine"),
            SnapshotCommand(snapshot_value=42),
            # Segments / music / work modes / toggles.
            SegmentColorCommand(segment_indices=(0, 1), color=RGBColor(r=1, g=2, b=3)),
            MusicModeCommand(music_mode=1, sensitivity=50),
            WorkModeCommand(work_mode=1, mode_value=2),
            OscillationCommand(oscillating=True),
            ToggleCommand(toggle_instance="nightlightToggle", enabled=True),
            ModeCommand(mode_instance="hdmiSource", value=2),
        ],
    )
    def test_unmapped_commands_return_none(self, command):
        assert command_to_lan(command, "H601F", RANGE_0_100) is None

    def test_color_maps_to_colorwc_with_kelvin_zeroed(self):
        # Colour and colour temperature share one command; the unused half must
        # be zeroed or the firmware picks arbitrarily (#149, #158).
        cmd = ColorCommand(color=RGBColor(r=10, g=20, b=30))
        assert command_to_lan(cmd, "H6072", RANGE_0_254) == (
            "colorwc",
            {"color": {"r": 10, "g": 20, "b": 30}, "colorTemInKelvin": 0},
        )

    def test_color_temp_maps_to_colorwc_with_rgb_zeroed(self):
        assert command_to_lan(ColorTempCommand(kelvin=3000), "H6072", RANGE_0_100) == (
            "colorwc",
            {"color": {"r": 0, "g": 0, "b": 0}, "colorTemInKelvin": 3000},
        )


# --------------------------------------------------------------------------- #
# Brightness scalers — identity @ (0, 100)
# --------------------------------------------------------------------------- #
class TestBrightnessIdentity:
    @pytest.mark.parametrize("value", [0, 1, 25, 50, 99, 100])
    def test_device_to_lan_identity(self, value):
        assert device_brightness_to_lan(value, RANGE_0_100) == value

    @pytest.mark.parametrize("value", [0, 1, 25, 50, 99, 100])
    def test_lan_to_device_identity(self, value):
        assert lan_brightness_to_device(value, RANGE_0_100) == value

    def test_identity_clamps_above_100(self):
        # Defensive: a stray over-range native value is clamped to the LAN max.
        assert device_brightness_to_lan(150, RANGE_0_100) == LAN_BRIGHTNESS_MAX

    def test_identity_holds_when_max_is_100_with_nonzero_min(self):
        # The identity short-circuit keys off max == 100.
        assert device_brightness_to_lan(50, (1, 100)) == 50
        assert lan_brightness_to_device(50, (1, 100)) == 50


# --------------------------------------------------------------------------- #
# Brightness scalers — rescale @ (0, 254)
# --------------------------------------------------------------------------- #
class TestBrightnessRescale:
    @pytest.mark.parametrize(
        "native,expected_lan",
        [
            (0, 0),
            (127, 50),
            (254, 100),
        ],
    )
    def test_device_to_lan_rescale(self, native, expected_lan):
        assert device_brightness_to_lan(native, RANGE_0_254) == expected_lan

    @pytest.mark.parametrize(
        "lan,expected_native",
        [
            (0, 0),
            (50, 127),
            (100, 254),
        ],
    )
    def test_lan_to_device_rescale(self, lan, expected_native):
        assert lan_brightness_to_device(lan, RANGE_0_254) == expected_native

    @pytest.mark.parametrize("lan", range(0, 101))
    def test_round_trip_stable_from_lan_side(self, lan):
        # LAN -> device -> LAN must be a fixed point for every 0-100 input
        # (the LAN domain is coarser than the device domain, so it is lossless
        # in this direction).
        native = lan_brightness_to_device(lan, RANGE_0_254)
        assert device_brightness_to_lan(native, RANGE_0_254) == lan

    def test_device_to_lan_clamps_over_range(self):
        assert device_brightness_to_lan(300, RANGE_0_254) == LAN_BRIGHTNESS_MAX

    def test_lan_to_device_clamps_over_range(self):
        assert lan_brightness_to_device(150, RANGE_0_254) == 254

    def test_lan_to_device_clamps_under_range(self):
        assert lan_brightness_to_device(-10, RANGE_0_254) == 0


# --------------------------------------------------------------------------- #
# Brightness scalers — degenerate ranges
# --------------------------------------------------------------------------- #
class TestBrightnessDegenerateRange:
    def test_device_to_lan_zero_span(self):
        # min == max (and max != 100) -> no meaningful scale, floor to LAN min.
        assert device_brightness_to_lan(5, (5, 5)) == 0

    def test_lan_to_device_zero_span(self):
        # min == max -> the only representable value is the minimum.
        assert lan_brightness_to_device(50, (5, 5)) == 5

    def test_device_to_lan_inverted_span(self):
        # max < min (and max != 100) -> span <= 0 guard returns LAN min.
        assert device_brightness_to_lan(10, (50, 10)) == 0
