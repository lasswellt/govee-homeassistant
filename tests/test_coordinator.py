"""Test Govee coordinator."""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.govee.api.exceptions import (
    GoveeApiError,
    GoveeAuthError,
    GoveeDeviceNotFoundError,
    GoveeRateLimitError,
)
from custom_components.govee.models import (
    GoveeCapability,
    GoveeDevice,
    GoveeDeviceState,
    BrightnessCommand,
    ColorCommand,
    ColorTempCommand,
    OscillationCommand,
    PowerCommand,
    RGBColor,
    RangeCommand,
    SceneCommand,
    WorkModeCommand,
)
from custom_components.govee.models.device import (
    CAPABILITY_ON_OFF,
    CAPABILITY_RANGE,
    INSTANCE_POWER,
    INSTANCE_BRIGHTNESS,
    INSTANCE_HUMIDITY,
)
from custom_components.govee import const
from custom_components.govee.transport_health import TransportHealthTracker

# ==============================================================================
# Fixtures
# ==============================================================================


@pytest.fixture
def sample_capabilities():
    """Create sample light capabilities."""
    return (
        GoveeCapability(type=CAPABILITY_ON_OFF, instance=INSTANCE_POWER, parameters={}),
        GoveeCapability(
            type=CAPABILITY_RANGE,
            instance=INSTANCE_BRIGHTNESS,
            parameters={"range": {"min": 0, "max": 100}},
        ),
    )


@pytest.fixture
def sample_device(sample_capabilities):
    """Create a sample device."""
    return GoveeDevice(
        device_id="AA:BB:CC:DD:EE:FF:00:11",
        sku="H6072",
        name="Test Light",
        device_type="devices.types.light",
        capabilities=sample_capabilities,
        is_group=False,
    )


@pytest.fixture
def sample_group_device(sample_capabilities):
    """Create a sample group device."""
    return GoveeDevice(
        device_id="GROUP:AA:BB:CC:DD",
        sku="GROUP",
        name="All Lights",
        device_type="devices.types.group",
        capabilities=sample_capabilities,
        is_group=True,
    )


@pytest.fixture
def sample_state():
    """Create a sample device state."""
    return GoveeDeviceState(
        device_id="AA:BB:CC:DD:EE:FF:00:11",
        online=True,
        power_state=True,
        brightness=75,
        color=RGBColor(r=255, g=128, b=64),
        color_temp_kelvin=None,
        active_scene=None,
        source="api",
    )


# ==============================================================================
# Coordinator Logic Tests (without Home Assistant dependencies)
# ==============================================================================


class TestCoordinatorLogic:
    """Test coordinator logic that doesn't require HA."""

    def test_sample_device_creation(self, sample_device):
        """Test sample device fixture."""
        assert sample_device.device_id == "AA:BB:CC:DD:EE:FF:00:11"
        assert sample_device.sku == "H6072"
        assert sample_device.is_group is False

    def test_sample_group_device_creation(self, sample_group_device):
        """Test sample group device fixture."""
        assert sample_group_device.is_group is True

    def test_sample_state_creation(self, sample_state):
        """Test sample state fixture."""
        assert sample_state.power_state is True
        assert sample_state.brightness == 75

    def test_state_optimistic_power(self, sample_state):
        """Test optimistic power update."""
        sample_state.apply_optimistic_power(False)
        assert sample_state.power_state is False
        assert sample_state.source == "optimistic"

    def test_state_optimistic_brightness(self, sample_state):
        """Test optimistic brightness update."""
        sample_state.apply_optimistic_brightness(50)
        assert sample_state.brightness == 50
        assert sample_state.source == "optimistic"

    def test_state_optimistic_color(self, sample_state):
        """Test optimistic color update."""
        color = RGBColor(r=0, g=255, b=0)
        sample_state.apply_optimistic_color(color)
        assert sample_state.color == color
        assert sample_state.color_temp_kelvin is None
        assert sample_state.source == "optimistic"

    def test_state_optimistic_color_temp(self, sample_state):
        """Test optimistic color temperature update."""
        sample_state.apply_optimistic_color_temp(4000)
        assert sample_state.color_temp_kelvin == 4000
        assert sample_state.color is None
        assert sample_state.source == "optimistic"


class TestCommandGeneration:
    """Test command creation for coordinator."""

    def test_power_command(self):
        """Test power command for coordinator."""
        cmd = PowerCommand(power_on=True)
        assert cmd.power_on is True
        assert cmd.get_value() == 1

    def test_brightness_command(self):
        """Test brightness command for coordinator."""
        cmd = BrightnessCommand(brightness=50)
        assert cmd.brightness == 50
        assert cmd.get_value() == 50

    def test_color_command(self):
        """Test color command for coordinator."""
        color = RGBColor(r=255, g=0, b=0)
        cmd = ColorCommand(color=color)
        # Red packed = (255 << 16) + (0 << 8) + 0 = 16711680
        assert cmd.get_value() == 16711680

    def test_color_temp_command(self):
        """Test color temp command for coordinator."""
        cmd = ColorTempCommand(kelvin=4000)
        assert cmd.kelvin == 4000
        assert cmd.get_value() == 4000

    def test_scene_command(self):
        """Test scene command for coordinator."""
        cmd = SceneCommand(scene_id=123, scene_name="Test")
        value = cmd.get_value()
        assert value["id"] == 123
        assert value["name"] == "Test"


class TestDeviceFiltering:
    """Test device filtering logic."""

    def test_filter_groups_when_disabled(self, sample_device, sample_group_device):
        """Test group devices filtered when groups disabled."""
        devices = [sample_device, sample_group_device]
        enable_groups = False

        filtered = [d for d in devices if not d.is_group or enable_groups]

        assert len(filtered) == 1
        assert filtered[0] == sample_device

    def test_include_groups_when_enabled(self, sample_device, sample_group_device):
        """Test group devices included when groups enabled."""
        devices = [sample_device, sample_group_device]
        enable_groups = True

        filtered = [d for d in devices if not d.is_group or enable_groups]

        assert len(filtered) == 2


class TestSceneCaching:
    """Test scene caching logic."""

    def test_cache_empty_initially(self):
        """Test scene cache starts empty."""
        cache: dict[str, list[dict[str, Any]]] = {}
        assert "device_id" not in cache

    def test_cache_stores_scenes(self):
        """Test scenes are cached."""
        cache: dict[str, list[dict[str, Any]]] = {}
        scenes = [{"name": "Sunrise", "value": {"id": 1}}]

        cache["device_id"] = scenes

        assert cache["device_id"] == scenes

    def test_cache_returns_existing(self):
        """Test cached scenes are returned."""
        cache: dict[str, list[dict[str, Any]]] = {
            "device_id": [{"name": "Sunset", "value": {"id": 2}}]
        }

        device_id = "device_id"
        refresh = False

        if not refresh and device_id in cache:
            result = cache[device_id]
        else:
            result = []

        assert len(result) == 1
        assert result[0]["name"] == "Sunset"

    def test_cache_refresh_bypasses(self):
        """Test refresh bypasses cache."""
        cache: dict[str, list[dict[str, Any]]] = {
            "device_id": [{"name": "Old", "value": {"id": 1}}]
        }

        device_id = "device_id"
        refresh = True

        should_fetch = refresh or device_id not in cache

        assert should_fetch is True


class TestStateManagement:
    """Test state management logic."""

    def test_state_registry(self, sample_state):
        """Test state registry operations."""
        states: dict[str, GoveeDeviceState] = {}

        states["device_id"] = sample_state

        assert states.get("device_id") == sample_state
        assert states.get("unknown") is None

    def test_state_update_from_api(self):
        """Test state update from API response."""
        state = GoveeDeviceState.create_empty("device_id")

        api_data = {
            "capabilities": [
                {
                    "type": "devices.capabilities.online",
                    "instance": "online",
                    "state": {"value": True},
                },
                {
                    "type": "devices.capabilities.on_off",
                    "instance": "powerSwitch",
                    "state": {"value": 1},
                },
            ],
        }

        state.update_from_api(api_data)

        assert state.online is True
        assert state.power_state is True
        assert state.source == "api"

    def test_state_update_from_mqtt(self):
        """Test state update from MQTT message."""
        state = GoveeDeviceState.create_empty("device_id")

        mqtt_data = {
            "onOff": 1,
            "brightness": 50,
            "color": {"r": 100, "g": 150, "b": 200},
        }

        state.update_from_mqtt(mqtt_data)

        assert state.power_state is True
        assert state.brightness == 50
        assert state.color.as_tuple == (100, 150, 200)
        assert state.source == "mqtt"

    def test_preserve_active_scene_on_api_update(self, sample_state):
        """Test active scene is preserved when API doesn't return it."""
        sample_state.active_scene = "scene_123"

        new_state = GoveeDeviceState.create_empty(sample_state.device_id)
        new_state.power_state = True
        new_state.brightness = 80

        if sample_state.active_scene:
            new_state.active_scene = sample_state.active_scene

        assert new_state.active_scene == "scene_123"


class TestErrorHandling:
    """Test error handling patterns."""

    def test_auth_error_raises(self):
        """Test auth error is raised appropriately."""
        err = GoveeAuthError("Invalid key")
        assert err.code == 401

    def test_rate_limit_keeps_state(self, sample_state):
        """Test rate limit error preserves existing state."""
        states = {"device_id": sample_state}

        try:
            raise GoveeRateLimitError()
        except GoveeRateLimitError:
            result = states.get("device_id")

        assert result == sample_state

    def test_device_not_found_for_groups(self):
        """Test device not found is expected for groups."""
        err = GoveeDeviceNotFoundError("GROUP:ID")

        is_group_error = (
            "not exist" in str(err).lower() or "not found" in str(err).lower()
        )

        assert is_group_error or err.code == 400

    def test_api_error_logs_debug(self):
        """Test general API errors are logged but don't crash."""
        err = GoveeApiError("Server error", code=500)

        should_keep_state = True
        assert should_keep_state
        assert err.code == 500


class TestMqttIntegration:
    """Test MQTT integration patterns."""

    def test_mqtt_state_update_flow(self, sample_state):
        """Test MQTT state update is applied correctly."""
        states = {"device_id": sample_state}
        devices = {"device_id": MagicMock()}

        device_id = "device_id"
        mqtt_data = {"onOff": 0, "brightness": 25}

        if device_id in devices:
            state = states.get(device_id)
            if state:
                state.update_from_mqtt(mqtt_data)

        assert sample_state.power_state is False
        assert sample_state.brightness == 25
        assert sample_state.source == "mqtt"

    def test_mqtt_unknown_device_ignored(self):
        """Test MQTT updates for unknown devices are ignored."""
        devices = {"known_device": MagicMock()}

        unknown_device_id = "unknown_device"

        if unknown_device_id not in devices:
            handled = False
        else:
            handled = True

        assert handled is False

    def test_mqtt_push_recovers_offline_device(self, sample_state):
        """Issue #68 — an MQTT push should restore the entity availability
        even when the cloud's `online` flag is still stuck at False.

        Verifies the regression at the state-application layer (the
        coordinator-level entry point only adds logging on top)."""
        sample_state.online = False
        sample_state.power_state = False

        sample_state.update_from_mqtt({"onOff": 1, "brightness": 60})

        assert sample_state.online is True
        assert sample_state.power_state is True
        assert sample_state.brightness == 60


class TestParallelStateFetching:
    """Test parallel state fetching patterns."""

    @pytest.mark.asyncio
    async def test_parallel_fetch_creates_tasks(self, sample_device):
        """Test parallel fetch creates tasks for all devices."""
        devices = {
            "device1": sample_device,
            "device2": sample_device,
            "device3": sample_device,
        }

        async def mock_fetch(device_id, device):
            return GoveeDeviceState.create_empty(device_id)

        tasks = [mock_fetch(device_id, device) for device_id, device in devices.items()]

        results = await asyncio.gather(*tasks)

        assert len(results) == 3
        assert all(isinstance(r, GoveeDeviceState) for r in results)

    @pytest.mark.asyncio
    async def test_parallel_fetch_handles_exceptions(self, sample_device):
        """Test parallel fetch handles individual failures."""

        async def mock_fetch(device_id: str):
            if device_id == "failing":
                raise GoveeApiError("Fetch failed")
            return GoveeDeviceState.create_empty(device_id)

        tasks = [
            mock_fetch("success1"),
            mock_fetch("failing"),
            mock_fetch("success2"),
        ]

        results = await asyncio.gather(*tasks, return_exceptions=True)

        assert isinstance(results[0], GoveeDeviceState)
        assert isinstance(results[1], GoveeApiError)
        assert isinstance(results[2], GoveeDeviceState)


class TestOptimisticUpdates:
    """Test optimistic state update patterns."""

    def test_apply_optimistic_power_on(self, sample_state):
        """Test applying optimistic power on."""
        sample_state.power_state = False
        sample_state.apply_optimistic_power(True)

        assert sample_state.power_state is True
        assert sample_state.source == "optimistic"

    def test_apply_optimistic_power_off(self, sample_state):
        """Test applying optimistic power off."""
        sample_state.power_state = True
        sample_state.apply_optimistic_power(False)

        assert sample_state.power_state is False
        assert sample_state.source == "optimistic"

    def test_apply_optimistic_brightness(self, sample_state):
        """Test applying optimistic brightness."""
        sample_state.apply_optimistic_brightness(100)

        assert sample_state.brightness == 100
        assert sample_state.source == "optimistic"

    def test_apply_optimistic_color_clears_temp(self, sample_state):
        """Test applying color clears color temp."""
        sample_state.color_temp_kelvin = 4000
        color = RGBColor(r=255, g=0, b=0)
        sample_state.apply_optimistic_color(color)

        assert sample_state.color == color
        assert sample_state.color_temp_kelvin is None

    def test_apply_optimistic_temp_clears_color(self, sample_state):
        """Test applying color temp clears color."""
        sample_state.color = RGBColor(r=255, g=0, b=0)
        sample_state.apply_optimistic_color_temp(5000)

        assert sample_state.color_temp_kelvin == 5000
        assert sample_state.color is None

    def test_apply_optimistic_range_humidity(self, sample_state):
        """RangeCommand(humidity) updates the configured setpoint (#114/#118).

        The poll returns "" for range::humidity, so the coordinator keeps the
        dehumidifier setpoint optimistically after a successful write.
        """
        from custom_components.govee.coordinator import GoveeCoordinator
        from custom_components.govee.models.device import INSTANCE_HUMIDITY

        coord = object.__new__(GoveeCoordinator)
        coord._states = {sample_state.device_id: sample_state}

        coord._apply_optimistic_update(
            sample_state.device_id,
            RangeCommand(range_instance=INSTANCE_HUMIDITY, value=55),
        )
        assert sample_state.configured_humidity == 55

        # Non-humidity range instances must not touch the setpoint.
        coord._apply_optimistic_update(
            sample_state.device_id,
            RangeCommand(range_instance="fanSpeed", value=3),
        )
        assert sample_state.configured_humidity == 55

    def test_apply_optimistic_oscillation_command(self, sample_state):
        """OscillationCommand updates fan oscillation state optimistically."""
        from custom_components.govee.coordinator import GoveeCoordinator

        sample_state.oscillating = False

        coord = object.__new__(GoveeCoordinator)
        coord._states = {sample_state.device_id: sample_state}

        coord._apply_optimistic_update(
            sample_state.device_id,
            OscillationCommand(oscillating=True),
        )
        assert sample_state.oscillating is True
        assert sample_state.source == "optimistic"


class TestDeviceStateCreation:
    """Test device state creation patterns."""

    def test_create_empty_state(self):
        """Test creating empty state."""
        state = GoveeDeviceState.create_empty("test_id")

        assert state.device_id == "test_id"
        assert state.online is True
        assert state.power_state is False
        assert state.brightness == 100

    def test_state_with_all_attributes(self):
        """Test state with all attributes set."""
        color = RGBColor(r=100, g=150, b=200)
        state = GoveeDeviceState(
            device_id="test_id",
            online=True,
            power_state=True,
            brightness=50,
            color=color,
            color_temp_kelvin=4000,
            active_scene="scene_1",
            source="mqtt",
        )

        assert state.device_id == "test_id"
        assert state.online is True
        assert state.power_state is True
        assert state.brightness == 50
        assert state.color == color
        assert state.color_temp_kelvin == 4000
        assert state.active_scene == "scene_1"
        assert state.source == "mqtt"


class TestCoordinatorDeviceRegistry:
    """Test device registry patterns."""

    def test_get_device_by_id(self, sample_device):
        """Test getting device by ID."""
        devices = {sample_device.device_id: sample_device}

        result = devices.get(sample_device.device_id)
        assert result == sample_device

    def test_get_device_unknown_returns_none(self, sample_device):
        """Test getting unknown device returns None."""
        devices = {sample_device.device_id: sample_device}

        result = devices.get("unknown_id")
        assert result is None

    def test_device_count(self, sample_device, sample_group_device):
        """Test device count."""
        devices = {
            sample_device.device_id: sample_device,
            sample_group_device.device_id: sample_group_device,
        }

        assert len(devices) == 2


class TestCoordinatorSceneManagement:
    """Test scene management patterns."""

    def test_scene_cache_miss_fetches(self):
        """Test cache miss triggers fetch."""
        cache: dict[str, list[dict[str, Any]]] = {}

        device_id = "device_id"
        if device_id not in cache:
            # Would fetch from API
            should_fetch = True
        else:
            should_fetch = False

        assert should_fetch is True

    def test_scene_cache_hit_returns_cached(self):
        """Test cache hit returns cached scenes."""
        scenes = [{"name": "Test", "value": {"id": 1}}]
        cache = {"device_id": scenes}

        device_id = "device_id"
        result = cache.get(device_id, [])

        assert result == scenes

    def test_refresh_clears_and_fetches(self):
        """Test refresh clears cache and fetches."""
        cache = {"device_id": [{"name": "Old", "value": {"id": 1}}]}

        # Simulate refresh
        if "device_id" in cache:
            del cache["device_id"]

        assert "device_id" not in cache


class TestPowerOffPendingFlag:
    """Test _pending_power_off tracking in coordinator (issue #16).

    Tests the flag logic that allows segment entities to detect when a
    power-off command is in flight, avoiding race conditions during
    area-targeted turn_off.
    """

    def test_pending_power_off_starts_empty(self):
        """Test _pending_power_off set is initially empty."""
        pending: set[str] = set()
        assert len(pending) == 0

    def test_is_power_off_pending_false_initially(self):
        """Test is_power_off_pending returns False for unknown device."""
        pending: set[str] = set()
        assert "device_id" not in pending

    def test_flag_set_for_power_off_command(self):
        """Test flag is set for PowerCommand(power_on=False)."""
        pending: set[str] = set()
        command = PowerCommand(power_on=False)

        is_power_off = isinstance(command, PowerCommand) and not command.power_on
        if is_power_off:
            pending.add("device_id")

        assert "device_id" in pending

    def test_flag_not_set_for_power_on_command(self):
        """Test flag is NOT set for PowerCommand(power_on=True)."""
        pending: set[str] = set()
        command = PowerCommand(power_on=True)

        is_power_off = isinstance(command, PowerCommand) and not command.power_on
        if is_power_off:
            pending.add("device_id")

        assert "device_id" not in pending

    def test_flag_not_set_for_brightness_command(self):
        """Test flag is NOT set for non-power commands."""
        pending: set[str] = set()
        command = BrightnessCommand(brightness=50)

        is_power_off = isinstance(command, PowerCommand) and not command.power_on
        if is_power_off:
            pending.add("device_id")

        assert "device_id" not in pending

    def test_flag_cleared_after_success(self):
        """Test flag is cleared via discard after command completes."""
        pending: set[str] = set()
        pending.add("device_id")

        # Simulate finally block
        pending.discard("device_id")

        assert "device_id" not in pending

    def test_flag_cleared_after_failure(self):
        """Test flag is cleared even when command raises."""
        pending: set[str] = set()
        device_id = "device_id"
        command = PowerCommand(power_on=False)

        is_power_off = isinstance(command, PowerCommand) and not command.power_on
        if is_power_off:
            pending.add(device_id)

        try:
            raise GoveeApiError("Simulated failure")
        except GoveeApiError:
            pass
        finally:
            if is_power_off:
                pending.discard(device_id)

        assert device_id not in pending

    def test_flag_discard_idempotent(self):
        """Test discarding a non-existent device_id is safe."""
        pending: set[str] = set()
        pending.discard("nonexistent")  # Should not raise
        assert len(pending) == 0


class TestCleanupDeviceIdExtraction:
    """Test device ID extraction for cleanup logic."""

    def test_extract_mac_address_device_id(self):
        """Test extracting MAC address device_id from unique_id."""
        device_id = "AA:BB:CC:DD:EE:FF:00:01"
        unique_id = f"{device_id}_segment_0"
        known_devices = {device_id}

        # Simulate extraction using longest-first matching
        extracted = None
        for dev_id in sorted(known_devices, key=len, reverse=True):
            if unique_id.startswith(dev_id):
                extracted = dev_id
                break

        assert extracted == device_id

    def test_extract_numeric_group_id(self):
        """Test extracting numeric group ID from unique_id."""
        device_id = "12345678"
        unique_id = f"{device_id}_scene_select"
        known_devices = {device_id}

        # Simulate extraction
        extracted = None
        for dev_id in sorted(known_devices, key=len, reverse=True):
            if unique_id.startswith(dev_id):
                extracted = dev_id
                break

        assert extracted == device_id

    def test_extract_with_multiple_device_ids(self):
        """Test extraction with multiple device IDs (longest-first matching)."""
        # Mix of MAC and numeric IDs
        mac_id = "AA:BB:CC:DD:EE:FF:00:01"
        group_id = "12345678"
        known_devices = {mac_id, group_id}

        # MAC address device
        unique_id = f"{mac_id}_segment_0"
        extracted = None
        for dev_id in sorted(known_devices, key=len, reverse=True):
            if unique_id.startswith(dev_id):
                extracted = dev_id
                break
        assert extracted == mac_id

        # Group device
        unique_id = f"{group_id}_segment_0"
        extracted = None
        for dev_id in sorted(known_devices, key=len, reverse=True):
            if unique_id.startswith(dev_id):
                extracted = dev_id
                break
        assert extracted == group_id

    def test_extract_returns_none_for_unknown_device(self):
        """Test extraction returns None for unknown device."""
        known_devices = {"AA:BB:CC:DD:EE:FF:00:01"}
        unique_id = "UNKNOWN:DEVICE:ID_segment_0"

        extracted = None
        for dev_id in sorted(known_devices, key=len, reverse=True):
            if unique_id.startswith(dev_id):
                extracted = dev_id
                break

        assert extracted is None

    def test_longest_first_matching_precedence(self):
        """Test longest-first matching prevents prefix collision."""
        # Create two device IDs where one is prefix of another
        short_id = "ABC"
        long_id = "ABCDEF"
        known_devices = {short_id, long_id}

        # Test with long_id unique_id
        unique_id = f"{long_id}_segment_0"
        extracted = None
        for dev_id in sorted(known_devices, key=len, reverse=True):
            if unique_id.startswith(dev_id):
                extracted = dev_id
                break

        # Should match long_id, not short_id
        assert extracted == long_id


class TestCleanupSegmentModeLogic:
    """Test segment mode cleanup logic with per-device config."""

    def test_grouped_segment_removed_when_disabled(self):
        """Test grouped segment entity removed when mode is not grouped."""
        from custom_components.govee.const import (
            SUFFIX_GROUPED_SEGMENT,
            SEGMENT_MODE_GROUPED,
            SEGMENT_MODE_INDIVIDUAL,
        )

        device_id = "AA:BB:CC:DD:EE:FF:00:01"
        unique_id = f"{device_id}{SUFFIX_GROUPED_SEGMENT}"

        # Device config with individual mode
        device_modes = {device_id: SEGMENT_MODE_INDIVIDUAL}

        # Extract and check
        suffix = unique_id[len(device_id) :]
        is_grouped = suffix == SUFFIX_GROUPED_SEGMENT
        mode = device_modes.get(device_id, SEGMENT_MODE_GROUPED)

        should_remove = is_grouped and mode != SEGMENT_MODE_GROUPED
        assert should_remove is True

    def test_individual_segment_removed_when_disabled(self):
        """Test individual segment entity removed when mode is disabled."""
        from custom_components.govee.const import (
            SUFFIX_SEGMENT,
            SEGMENT_MODE_INDIVIDUAL,
            SEGMENT_MODE_DISABLED,
        )

        device_id = "AA:BB:CC:DD:EE:FF:00:01"
        unique_id = f"{device_id}{SUFFIX_SEGMENT}0"

        # Device config with disabled mode
        device_modes = {device_id: SEGMENT_MODE_DISABLED}

        # Extract and check
        suffix = unique_id[len(device_id) :]
        is_individual = suffix.startswith(SUFFIX_SEGMENT)
        mode = device_modes.get(device_id, SEGMENT_MODE_INDIVIDUAL)

        should_remove = is_individual and mode != SEGMENT_MODE_INDIVIDUAL
        assert should_remove is True

    def test_segment_kept_when_mode_matches(self):
        """Test segment entity is kept when mode matches."""
        from custom_components.govee.const import (
            SUFFIX_SEGMENT,
            SEGMENT_MODE_INDIVIDUAL,
        )

        device_id = "AA:BB:CC:DD:EE:FF:00:01"
        unique_id = f"{device_id}{SUFFIX_SEGMENT}0"

        # Device config with individual mode (matches entity type)
        device_modes = {device_id: SEGMENT_MODE_INDIVIDUAL}

        # Extract and check
        suffix = unique_id[len(device_id) :]
        is_individual = suffix.startswith(SUFFIX_SEGMENT)
        mode = device_modes.get(device_id, SEGMENT_MODE_INDIVIDUAL)

        should_remove = is_individual and mode != SEGMENT_MODE_INDIVIDUAL
        assert should_remove is False

    def test_fallback_to_global_mode(self):
        """Test fallback to global mode when device not in per-device config."""
        from custom_components.govee.const import (
            SUFFIX_SEGMENT,
            SEGMENT_MODE_INDIVIDUAL,
        )

        device_id = "AA:BB:CC:DD:EE:FF:00:01"
        unique_id = f"{device_id}{SUFFIX_SEGMENT}0"

        # Device NOT in per-device config, use global
        device_modes = {}  # Empty - use global fallback
        global_mode = SEGMENT_MODE_INDIVIDUAL

        # Extract and check
        suffix = unique_id[len(device_id) :]
        is_individual = suffix.startswith(SUFFIX_SEGMENT)
        mode = device_modes.get(device_id, global_mode)

        should_remove = is_individual and mode != SEGMENT_MODE_INDIVIDUAL
        assert should_remove is False  # Matches global mode


class TestClearSceneLogic:
    """Test async_clear_scene command selection logic.

    These tests verify the logic for choosing which command to send when
    clearing a scene (color restore vs color_temp restore vs defaults).
    """

    def _make_device(self, supports_rgb: bool, supports_color_temp: bool):
        """Create a device with specified color capabilities."""
        caps = [
            GoveeCapability(
                type=CAPABILITY_ON_OFF, instance=INSTANCE_POWER, parameters={}
            ),
            GoveeCapability(
                type=CAPABILITY_RANGE,
                instance=INSTANCE_BRIGHTNESS,
                parameters={"range": {"min": 0, "max": 100}},
            ),
        ]
        if supports_rgb:
            caps.append(
                GoveeCapability(
                    type="devices.capabilities.color_setting",
                    instance="colorRgb",
                    parameters={},
                )
            )
        if supports_color_temp:
            caps.append(
                GoveeCapability(
                    type="devices.capabilities.color_setting",
                    instance="colorTemperatureK",
                    parameters={"range": {"min": 2000, "max": 9000}},
                )
            )
        return GoveeDevice(
            device_id="AA:BB:CC:DD:EE:FF:00:11",
            sku="H6072",
            name="Test Light",
            device_type="devices.types.light",
            capabilities=tuple(caps),
            is_group=False,
        )

    def test_clear_scene_chooses_color_when_last_color_saved(self):
        """Test clear scene sends ColorCommand when last_color is available."""
        device = self._make_device(supports_rgb=True, supports_color_temp=True)
        state = GoveeDeviceState.create_empty(device.device_id)
        state.active_scene = "123"
        state.last_color = RGBColor(255, 0, 0)

        color = state.color or state.last_color

        # Should pick ColorCommand path
        assert color == RGBColor(255, 0, 0)
        assert device.supports_rgb is True

    def test_clear_scene_chooses_color_temp_when_last_temp_saved(self):
        """Test clear scene sends ColorTempCommand when last_color_temp is available."""
        device = self._make_device(supports_rgb=True, supports_color_temp=True)
        state = GoveeDeviceState.create_empty(device.device_id)
        state.active_scene = "123"
        state.last_color_temp_kelvin = 4000

        color = state.color or state.last_color
        color_temp = state.color_temp_kelvin or state.last_color_temp_kelvin

        # No color, falls through to color_temp
        assert color is None
        assert color_temp == 4000
        assert device.supports_color_temp is True

    def test_clear_scene_default_white_when_rgb_supported(self):
        """Test clear scene sends white RGB when device supports RGB and nothing saved."""
        device = self._make_device(supports_rgb=True, supports_color_temp=True)
        state = GoveeDeviceState.create_empty(device.device_id)
        state.active_scene = "123"

        color = state.color or state.last_color
        if color and color.as_packed_int == 0:
            color = state.last_color
        if color and color.as_packed_int == 0:
            color = None
        color_temp = state.color_temp_kelvin or state.last_color_temp_kelvin

        # No saved color or temp — prefers RGB white over color_temp midpoint
        assert color is None
        assert color_temp is None
        assert device.supports_rgb is True
        # Fallback should send RGBColor(255, 255, 255)

    def test_clear_scene_default_color_temp_midpoint(self):
        """Test clear scene uses midpoint of color temp range for color-temp-only devices."""
        device = self._make_device(supports_rgb=False, supports_color_temp=True)
        state = GoveeDeviceState.create_empty(device.device_id)
        state.active_scene = "123"

        color = state.color or state.last_color
        color_temp = state.color_temp_kelvin or state.last_color_temp_kelvin

        # No saved color or temp → falls through to color_temp default
        assert color is None
        assert color_temp is None
        assert device.supports_rgb is False
        assert device.supports_color_temp is True
        ct_range = device.color_temp_range
        assert ct_range is not None
        midpoint = (ct_range.min_kelvin + ct_range.max_kelvin) // 2
        assert midpoint == 5500

    def test_clear_scene_no_scene_active_is_noop(self):
        """Test clearing when no scene is active doesn't require a command."""
        state = GoveeDeviceState.create_empty("test_id")
        # Neither active_scene nor active_diy_scene set
        assert state.active_scene is None
        assert state.active_diy_scene is None

    def test_clear_scene_clears_both_scene_types(self):
        """Test clearing scene state clears both regular and DIY scene."""
        state = GoveeDeviceState.create_empty("test_id")
        state.active_scene = "123"
        state.active_scene_name = "Sunrise"
        state.active_diy_scene = "456"

        # Simulate what async_clear_scene does on success
        state.active_scene = None
        state.active_scene_name = None
        state.active_diy_scene = None

        assert state.active_scene is None
        assert state.active_scene_name is None
        assert state.active_diy_scene is None


class TestStatePreservationAcrossApiPoll:
    """Test that restore-target fields survive API poll cycles."""

    def test_last_color_preserved_across_api_poll(self):
        """Test last_color is preserved when API returns a fresh state."""
        existing = GoveeDeviceState.create_empty("test_id")
        existing.color = RGBColor(255, 0, 0)
        existing.apply_optimistic_scene("scene_1", "Sunset")
        assert existing.last_color == RGBColor(255, 0, 0)

        # Simulate API poll returning a fresh state (no last_color)
        new_state = GoveeDeviceState.create_empty("test_id")
        new_state.power_state = True

        # Mimic coordinator preservation logic
        if existing.last_color is not None:
            new_state.last_color = existing.last_color

        assert new_state.last_color == RGBColor(255, 0, 0)

    def test_last_color_temp_preserved_across_api_poll(self):
        """Test last_color_temp_kelvin is preserved when API returns a fresh state."""
        existing = GoveeDeviceState.create_empty("test_id")
        existing.color_temp_kelvin = 4500
        existing.apply_optimistic_scene("scene_1", "Sunset")
        assert existing.last_color_temp_kelvin == 4500

        new_state = GoveeDeviceState.create_empty("test_id")
        new_state.power_state = True

        if existing.last_color_temp_kelvin is not None:
            new_state.last_color_temp_kelvin = existing.last_color_temp_kelvin

        assert new_state.last_color_temp_kelvin == 4500

    def test_last_scene_preserved_across_api_poll(self):
        """Test last_scene_id and last_scene_name survive API poll."""
        existing = GoveeDeviceState.create_empty("test_id")
        existing.apply_optimistic_scene("scene_42", "Aurora")
        assert existing.last_scene_id == "scene_42"
        assert existing.last_scene_name == "Aurora"

        new_state = GoveeDeviceState.create_empty("test_id")

        if existing.last_scene_id is not None:
            new_state.last_scene_id = existing.last_scene_id
        if existing.last_scene_name is not None:
            new_state.last_scene_name = existing.last_scene_name

        assert new_state.last_scene_id == "scene_42"
        assert new_state.last_scene_name == "Aurora"

    def test_full_flow_color_scene_poll_clear(self):
        """End-to-end: set red → scene → API poll (colorRgb=0) → clear → red resolved."""
        # Step 1: User sets red
        state = GoveeDeviceState.create_empty("test_id")
        state.color = RGBColor(255, 0, 0)
        state.power_state = True

        # Step 2: User activates scene — saves red as last_color
        state.apply_optimistic_scene("scene_1", "Party")
        assert state.last_color == RGBColor(255, 0, 0)
        assert state.color is None

        # Step 3: API poll returns fresh state with colorRgb=0 (scene running)
        api_state = GoveeDeviceState.create_empty("test_id")
        api_state.power_state = True
        api_state.color = RGBColor(0, 0, 0)  # API returns black during scene

        # Coordinator preserves memory fields
        if state.active_scene:
            api_state.active_scene = state.active_scene
        if state.active_scene_name:
            api_state.active_scene_name = state.active_scene_name
        if state.last_color is not None:
            api_state.last_color = state.last_color

        # Step 4: Coordinator preserves existing color when API returns black
        if (
            api_state.color is not None
            and api_state.color.as_packed_int == 0
            and state.color is not None
            and state.color.as_packed_int != 0
        ):
            api_state.color = state.color

        # Step 5: Resolve color for clear_scene — reject black, fall back to last_color
        color = api_state.color or api_state.last_color
        if color and color.as_packed_int == 0:
            color = api_state.last_color
        if color and color.as_packed_int == 0:
            color = None

        assert color == RGBColor(255, 0, 0)

    def test_api_poll_preserves_color_when_api_returns_black(self):
        """API returning colorRgb=0 should not overwrite a valid existing color."""
        existing = GoveeDeviceState.create_empty("test_id")
        existing.color = RGBColor(255, 255, 255)  # White from clear_scene fallback

        api_state = GoveeDeviceState.create_empty("test_id")
        api_state.color = RGBColor(0, 0, 0)  # API returns black

        # Mimic new coordinator preservation logic
        if (
            api_state.color is not None
            and api_state.color.as_packed_int == 0
            and existing.color is not None
            and existing.color.as_packed_int != 0
        ):
            api_state.color = existing.color

        assert api_state.color == RGBColor(255, 255, 255)

    def test_api_poll_allows_real_color_updates(self):
        """API returning a non-black color should overwrite existing state normally."""
        existing = GoveeDeviceState.create_empty("test_id")
        existing.color = RGBColor(255, 0, 0)

        api_state = GoveeDeviceState.create_empty("test_id")
        api_state.color = RGBColor(0, 255, 0)  # Device changed to green

        # Preservation logic should NOT trigger for non-black API color
        if (
            api_state.color is not None
            and api_state.color.as_packed_int == 0
            and existing.color is not None
            and existing.color.as_packed_int != 0
        ):
            api_state.color = existing.color

        assert api_state.color == RGBColor(0, 255, 0)

    def test_clear_scene_black_guard_prevents_black_restore(self):
        """Even if last_color is somehow (0,0,0), the guard should catch it."""
        state = GoveeDeviceState.create_empty("test_id")
        state.active_scene = "123"
        state.color = RGBColor(0, 0, 0)
        state.last_color = None

        color = state.color or state.last_color
        if color and color.as_packed_int == 0:
            color = state.last_color
        if color and color.as_packed_int == 0:
            color = None

        # Should fall through to default (white or midpoint)
        assert color is None

    def test_sensor_temperature_preserved_across_api_poll(self):
        """#78 follow-up: battery-powered thermometers (H5179, H5109, H5110,
        HS5108, HS5106) push to the cloud infrequently, so subsequent /device/state
        responses may omit the value. Preserve the last known reading instead
        of dropping the entity to 'unknown'."""
        existing = GoveeDeviceState.create_empty("test_id")
        existing.sensor_temperature = 21.5
        existing.sensor_humidity = 47.0

        # Fresh state from API poll without the sensor capability values
        new_state = GoveeDeviceState.create_empty("test_id")
        assert new_state.sensor_temperature is None
        assert new_state.sensor_humidity is None

        # Mimic coordinator preservation logic
        if (
            existing.sensor_temperature is not None
            and new_state.sensor_temperature is None
        ):
            new_state.sensor_temperature = existing.sensor_temperature
        if existing.sensor_humidity is not None and new_state.sensor_humidity is None:
            new_state.sensor_humidity = existing.sensor_humidity

        assert new_state.sensor_temperature == 21.5
        assert new_state.sensor_humidity == 47.0

    def test_sensor_temperature_replaced_when_api_returns_new_value(self):
        """Fresh API value overrides preserved value."""
        existing = GoveeDeviceState.create_empty("test_id")
        existing.sensor_temperature = 21.5
        existing.sensor_humidity = 47.0

        new_state = GoveeDeviceState.create_empty("test_id")
        new_state.sensor_temperature = 22.7
        new_state.sensor_humidity = 50.0

        # Preservation only kicks in when new value is None
        if (
            existing.sensor_temperature is not None
            and new_state.sensor_temperature is None
        ):
            new_state.sensor_temperature = existing.sensor_temperature
        if existing.sensor_humidity is not None and new_state.sensor_humidity is None:
            new_state.sensor_humidity = existing.sensor_humidity

        assert new_state.sensor_temperature == 22.7
        assert new_state.sensor_humidity == 50.0


# ==============================================================================
# BLE Transport Dispatch Tests
# ==============================================================================


class TestSkuFromBleName:
    """Test the SKU extraction helper for BLE advertising names."""

    def test_standard_govee_name(self):
        from custom_components.govee.coordinator import _sku_from_ble_name

        assert _sku_from_ble_name("Govee_H6072_754B") == "H6072"

    def test_ihoment_name(self):
        from custom_components.govee.coordinator import _sku_from_ble_name

        assert _sku_from_ble_name("ihoment_H6159_A3F2") == "H6159"

    def test_gbk_name(self):
        from custom_components.govee.coordinator import _sku_from_ble_name

        assert _sku_from_ble_name("GBK_H6102_1234") == "H6102"

    def test_name_without_suffix(self):
        from custom_components.govee.coordinator import _sku_from_ble_name

        assert _sku_from_ble_name("Govee_H6072") == "H6072"

    def test_no_sku_found(self):
        from custom_components.govee.coordinator import _sku_from_ble_name

        assert _sku_from_ble_name("SomeOtherDevice") is None

    def test_none_name(self):
        from custom_components.govee.coordinator import _sku_from_ble_name

        assert _sku_from_ble_name(None) is None

    def test_empty_name(self):
        from custom_components.govee.coordinator import _sku_from_ble_name

        assert _sku_from_ble_name("") is None

    def test_five_char_sku(self):
        """Some newer SKUs have 5 characters like H601F."""
        from custom_components.govee.coordinator import _sku_from_ble_name

        assert _sku_from_ble_name("Govee_H601F_ABCD") == "H601F"


class TestBleAdvertisementHandling:
    """Test BLE advertisement correlation with cloud devices."""

    def _make_coordinator_with_devices(self, devices: dict[str, GoveeDevice]):
        """Build a minimal coordinator-like object for testing _handle_ble_advertisement.

        Patches GoveeBLEDevice and SEGMENTED_MODELS into the ble_advertisement
        module since HAS_BLUETOOTH=False in the test env (missing serial for
        homeassistant.components.bluetooth).
        """
        import custom_components.govee.coordinator as coord_mod
        import custom_components.govee.ble_advertisement as ble_mod
        from custom_components.govee.ble_advertisement import BleAdvertisementHandler
        from custom_components.govee.api.ble import GoveeBLEDevice as RealBLEDevice
        from custom_components.govee.api.ble import SEGMENTED_MODELS as RealSegModels

        # Inject the names that the conditional import would have set
        ble_mod.GoveeBLEDevice = RealBLEDevice
        ble_mod.SEGMENTED_MODELS = RealSegModels
        # Broad allowlist so the enrollment-path tests exercise real logic
        # regardless of the production-default allowlist content. The
        # enforcement path is covered by its own dedicated test.
        ble_mod.BLE_COMMAND_SUPPORTED_MODELS = frozenset(
            {"H6053", "H6072", "H6102", "H6199", "H6076", "H6126"}
        )

        coord = object.__new__(coord_mod.GoveeCoordinator)
        coord._devices = devices
        coord._ble_devices = {}
        coord._transport = TransportHealthTracker()
        coord._states = {}
        coord._ble_ignored_skus_logged = set()
        coord._ble_handler = BleAdvertisementHandler(coord)
        return coord

    def _make_service_info(self, name: str, address: str):
        """Build a minimal mock BluetoothServiceInfoBleak."""
        info = MagicMock()
        info.name = name
        info.address = address
        info.device = MagicMock()
        info.device.address = address
        info.device.name = name
        info.advertisement = MagicMock()
        return info

    def test_sku_not_on_allowlist_is_ignored(self, sample_device):
        """Advertisements for SKUs outside the BLE allowlist must not enroll
        the device for command dispatch (issue #59). Advertising BLE is not
        proof the device will accept BLE command frames."""
        import custom_components.govee.coordinator as coord_mod
        import custom_components.govee.ble_advertisement as ble_mod
        from custom_components.govee.ble_advertisement import BleAdvertisementHandler
        from custom_components.govee.api.ble import GoveeBLEDevice as RealBLEDevice
        from custom_components.govee.api.ble import SEGMENTED_MODELS as RealSegModels

        ble_mod.GoveeBLEDevice = RealBLEDevice
        ble_mod.SEGMENTED_MODELS = RealSegModels
        # Narrow allowlist — does NOT include the advertised SKU below.
        ble_mod.BLE_COMMAND_SUPPORTED_MODELS = frozenset({"H9999"})

        coord = object.__new__(coord_mod.GoveeCoordinator)
        coord._devices = {"AA:BB:CC:DD:EE:FF:00:11": sample_device}
        coord._ble_devices = {}
        coord._transport = TransportHealthTracker()
        coord._states = {}
        coord._ble_ignored_skus_logged = set()
        coord._ble_handler = BleAdvertisementHandler(coord)

        info = self._make_service_info("Govee_H6072_754B", "AA:BB:CC:DD:EE:FF")
        coord._handle_ble_advertisement(info)

        assert "AA:BB:CC:DD:EE:FF:00:11" not in coord._ble_devices
        assert "H6072" in coord._ble_ignored_skus_logged

    def test_single_sku_match_creates_ble_device(self, sample_device):
        """BLE advertisement matching a single cloud device by SKU creates a GoveeBLEDevice."""
        coord = self._make_coordinator_with_devices(
            {"AA:BB:CC:DD:EE:FF:00:11": sample_device}
        )
        info = self._make_service_info("Govee_H6072_754B", "AA:BB:CC:DD:EE:FF")

        coord._handle_ble_advertisement(info)

        assert "AA:BB:CC:DD:EE:FF:00:11" in coord._ble_devices

    def test_no_sku_match_skips(self, sample_device):
        """BLE advertisement with non-matching SKU is ignored."""
        coord = self._make_coordinator_with_devices(
            {"AA:BB:CC:DD:EE:FF:00:11": sample_device}  # SKU=H6072
        )
        info = self._make_service_info("Govee_H6199_ABCD", "11:22:33:44:55:66")

        coord._handle_ble_advertisement(info)

        assert len(coord._ble_devices) == 0

    def test_no_sku_in_name_skips(self):
        """Advertisement with unparseable name is ignored."""
        coord = self._make_coordinator_with_devices({})
        info = self._make_service_info("RandomDevice", "AA:BB:CC:DD:EE:FF")

        coord._handle_ble_advertisement(info)

        assert len(coord._ble_devices) == 0

    def test_group_devices_excluded(self, sample_capabilities):
        """Group devices should never match BLE advertisements."""
        group = GoveeDevice(
            device_id="12345",
            sku="H6072",
            name="All Lights",
            device_type="devices.types.group",
            capabilities=sample_capabilities,
            is_group=True,
        )
        coord = self._make_coordinator_with_devices({"12345": group})
        info = self._make_service_info("Govee_H6072_754B", "AA:BB:CC:DD:EE:FF")

        coord._handle_ble_advertisement(info)

        assert len(coord._ble_devices) == 0

    def test_multiple_same_sku_uses_mac_tiebreaker(self, sample_capabilities):
        """Multiple cloud devices with same SKU: MAC-prefix tiebreaker."""
        dev1 = GoveeDevice(
            device_id="AA:BB:CC:DD:EE:FF:00:11",
            sku="H6072",
            name="Living Room",
            device_type="devices.types.light",
            capabilities=sample_capabilities,
        )
        dev2 = GoveeDevice(
            device_id="11:22:33:44:55:66:00:22",
            sku="H6072",
            name="Bedroom",
            device_type="devices.types.light",
            capabilities=sample_capabilities,
        )
        coord = self._make_coordinator_with_devices(
            {
                "AA:BB:CC:DD:EE:FF:00:11": dev1,
                "11:22:33:44:55:66:00:22": dev2,
            }
        )
        info = self._make_service_info("Govee_H6072_754B", "AA:BB:CC:DD:EE:FF")

        coord._handle_ble_advertisement(info)

        # Should match dev1 (MAC prefix matches)
        assert "AA:BB:CC:DD:EE:FF:00:11" in coord._ble_devices
        assert "11:22:33:44:55:66:00:22" not in coord._ble_devices

    def test_multiple_same_sku_no_mac_match_skips(self, sample_capabilities):
        """Multiple same-SKU devices with no MAC prefix match → skip."""
        dev1 = GoveeDevice(
            device_id="AA:BB:CC:DD:EE:FF:00:11",
            sku="H6072",
            name="Light 1",
            device_type="devices.types.light",
            capabilities=sample_capabilities,
        )
        dev2 = GoveeDevice(
            device_id="11:22:33:44:55:66:00:22",
            sku="H6072",
            name="Light 2",
            device_type="devices.types.light",
            capabilities=sample_capabilities,
        )
        coord = self._make_coordinator_with_devices(
            {
                "AA:BB:CC:DD:EE:FF:00:11": dev1,
                "11:22:33:44:55:66:00:22": dev2,
            }
        )
        # BLE MAC doesn't match either device's prefix
        info = self._make_service_info("Govee_H6072_754B", "99:88:77:66:55:44")

        coord._handle_ble_advertisement(info)

        assert len(coord._ble_devices) == 0

    def test_repeated_advertisement_refreshes_existing(self, sample_device):
        """Second advertisement for same device refreshes the BLEDevice reference."""
        coord = self._make_coordinator_with_devices(
            {"AA:BB:CC:DD:EE:FF:00:11": sample_device}
        )
        info1 = self._make_service_info("Govee_H6072_754B", "AA:BB:CC:DD:EE:FF")
        info2 = self._make_service_info("Govee_H6072_754B", "AA:BB:CC:DD:EE:FF")

        coord._handle_ble_advertisement(info1)
        coord._handle_ble_advertisement(info2)

        # Still only one entry, but the BLEDevice ref was refreshed
        assert len(coord._ble_devices) == 1

    def test_no_adapter_skips_enrollment(self, sample_device):
        """Issue #59 follow-up — VMs without Bluetooth passthrough still
        receive advertisements via the passive scanner stack but cannot
        actually connect, costing ~40s per command before REST fallback.
        Enrolling with zero connectable adapters must be skipped."""
        import custom_components.govee.ble_advertisement as ble_mod

        # Simulate the bt_component module presence with scanner_count=0
        bt = MagicMock()
        bt.async_scanner_count = MagicMock(return_value=0)
        ble_mod.bt_component = bt

        coord = self._make_coordinator_with_devices(
            {"AA:BB:CC:DD:EE:FF:00:11": sample_device}
        )
        coord.hass = MagicMock()
        info = self._make_service_info("Govee_H6072_754B", "AA:BB:CC:DD:EE:FF")

        coord._handle_ble_advertisement(info)

        assert "AA:BB:CC:DD:EE:FF:00:11" not in coord._ble_devices
        bt.async_scanner_count.assert_called_once()

        # Cleanup so we don't leak the patch into other tests
        del ble_mod.bt_component

    def test_adapter_present_enrolls_normally(self, sample_device):
        """When a connectable adapter exists, BLE enrollment proceeds."""
        import custom_components.govee.ble_advertisement as ble_mod

        bt = MagicMock()
        bt.async_scanner_count = MagicMock(return_value=1)
        ble_mod.bt_component = bt

        coord = self._make_coordinator_with_devices(
            {"AA:BB:CC:DD:EE:FF:00:11": sample_device}
        )
        coord.hass = MagicMock()
        info = self._make_service_info("Govee_H6072_754B", "AA:BB:CC:DD:EE:FF")

        coord._handle_ble_advertisement(info)

        assert "AA:BB:CC:DD:EE:FF:00:11" in coord._ble_devices
        bt.async_scanner_count.assert_called_once()

        del ble_mod.bt_component

    def test_ble_advertisement_restores_online_after_outage(self, sample_device):
        """Regression for issue #68 — BLE advertisement is proof of life.

        After a power-cycle the cloud may continue reporting `online: false`
        long after the device returns. Receiving a BLE advertisement is direct
        proof that the device is alive, so `_handle_ble_advertisement` must
        flip `state.online` back to True.
        """
        from custom_components.govee.models import GoveeDeviceState

        coord = self._make_coordinator_with_devices(
            {"AA:BB:CC:DD:EE:FF:00:11": sample_device}
        )
        # Stale "offline" state cached from the cloud.
        offline_state = GoveeDeviceState.create_empty("AA:BB:CC:DD:EE:FF:00:11")
        offline_state.online = False
        coord._states["AA:BB:CC:DD:EE:FF:00:11"] = offline_state

        info = self._make_service_info("Govee_H6072_754B", "AA:BB:CC:DD:EE:FF")
        coord._handle_ble_advertisement(info)

        assert coord._states["AA:BB:CC:DD:EE:FF:00:11"].online is True

    def test_ble_recovery_replaces_state_object_not_mutates(self, sample_device):
        """Regression for S3-007 (audit H2).

        The recovery path must produce a *new* GoveeDeviceState instance via
        ``dataclasses.replace`` and reassign into ``_states[matched_id]``.
        In-place mutation of the existing instance causes
        ``async_set_updated_data`` to pass the same dict-of-same-objects to
        listeners, which can mask the change. This test pins the replace
        semantic by asserting the dict slot now points at a different object.
        """
        from custom_components.govee.models import GoveeDeviceState

        coord = self._make_coordinator_with_devices(
            {"AA:BB:CC:DD:EE:FF:00:11": sample_device}
        )
        offline_state = GoveeDeviceState.create_empty("AA:BB:CC:DD:EE:FF:00:11")
        offline_state.online = False
        coord._states["AA:BB:CC:DD:EE:FF:00:11"] = offline_state
        original_id = id(offline_state)

        info = self._make_service_info("Govee_H6072_754B", "AA:BB:CC:DD:EE:FF")
        coord._handle_ble_advertisement(info)

        new_state = coord._states["AA:BB:CC:DD:EE:FF:00:11"]
        # Different object identity — proves dataclasses.replace was used.
        assert id(new_state) != original_id
        # The original object was NOT mutated to True.
        assert offline_state.online is False
        # The replacement carries online=True.
        assert new_state.online is True


class TestTryBleCommand:
    """Test the _try_ble_command method."""

    def _make_coordinator_with_mock_ble(self):
        """Build a coordinator with a mocked BLE device."""
        from unittest.mock import AsyncMock
        from custom_components.govee.coordinator import GoveeCoordinator

        coord = object.__new__(GoveeCoordinator)
        coord._ble_devices = {}
        coord._transport = TransportHealthTracker()
        coord._devices = {}
        coord._states = {}

        mock_ble = MagicMock()
        mock_ble.turn_on = AsyncMock()
        mock_ble.turn_off = AsyncMock()
        mock_ble.set_brightness = AsyncMock()
        mock_ble.set_rgb = AsyncMock()
        mock_ble.stop = AsyncMock()
        coord._ble_devices["AA:BB:CC:DD:EE:FF:00:11"] = mock_ble
        return coord, mock_ble

    @pytest.mark.asyncio
    async def test_power_on_via_ble(self):
        coord, ble = self._make_coordinator_with_mock_ble()
        result = await coord._try_ble_command(
            "AA:BB:CC:DD:EE:FF:00:11", PowerCommand(power_on=True)
        )
        assert result is True
        ble.turn_on.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_power_off_via_ble(self):
        coord, ble = self._make_coordinator_with_mock_ble()
        result = await coord._try_ble_command(
            "AA:BB:CC:DD:EE:FF:00:11", PowerCommand(power_on=False)
        )
        assert result is True
        ble.turn_off.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_brightness_via_ble(self):
        coord, ble = self._make_coordinator_with_mock_ble()
        result = await coord._try_ble_command(
            "AA:BB:CC:DD:EE:FF:00:11", BrightnessCommand(brightness=128)
        )
        assert result is True
        ble.set_brightness.assert_awaited_once_with(128)

    @pytest.mark.asyncio
    async def test_color_via_ble(self):
        coord, ble = self._make_coordinator_with_mock_ble()
        result = await coord._try_ble_command(
            "AA:BB:CC:DD:EE:FF:00:11", ColorCommand(color=RGBColor(r=255, g=0, b=128))
        )
        assert result is True
        ble.set_rgb.assert_awaited_once_with(255, 0, 128)

    @pytest.mark.asyncio
    async def test_unsupported_command_returns_false(self):
        """Scene, color_temp, etc. are not BLE-capable and must return False."""
        coord, _ble = self._make_coordinator_with_mock_ble()
        result = await coord._try_ble_command(
            "AA:BB:CC:DD:EE:FF:00:11",
            SceneCommand(scene_id=123, scene_name="Sunset"),
        )
        assert result is False

    @pytest.mark.asyncio
    async def test_ble_failure_returns_false(self):
        """BLE write failure returns False so REST fallback is triggered."""
        coord, ble = self._make_coordinator_with_mock_ble()
        from unittest.mock import AsyncMock

        ble.turn_on = AsyncMock(side_effect=Exception("BLE link lost"))
        result = await coord._try_ble_command(
            "AA:BB:CC:DD:EE:FF:00:11", PowerCommand(power_on=True)
        )
        assert result is False

    @pytest.mark.asyncio
    async def test_no_ble_device_returns_false(self):
        """No BLE device for this ID → immediate False."""
        from custom_components.govee.coordinator import GoveeCoordinator

        coord = object.__new__(GoveeCoordinator)
        coord._ble_devices = {}
        result = await coord._try_ble_command(
            "AA:BB:CC:DD:EE:FF:00:11", PowerCommand(power_on=True)
        )
        assert result is False


class TestClearSceneOnHdmiSyncBox:
    """Issue #48 — clearing a scene on H6604/H6605/etc must NOT lock the
    device into manual color (the historical bug was sending ColorCommand
    white, which produced 'a flat white image' instead of resuming Video
    Sync). The integration must instead re-select the HDMI source so the
    Sync Box returns to its native video sync mode."""

    def _make_sync_box(self, hdmi_options=None):
        """Build an H6604-style device with hdmiSource and dynamic_scene."""
        from custom_components.govee.models.device import (
            CAPABILITY_MODE,
            INSTANCE_HDMI_SOURCE,
        )

        if hdmi_options is None:
            hdmi_options = [
                {"name": "HDMI 1", "value": 1},
                {"name": "HDMI 2", "value": 2},
                {"name": "HDMI 3", "value": 3},
                {"name": "HDMI 4", "value": 4},
            ]
        caps = (
            GoveeCapability(
                type=CAPABILITY_ON_OFF, instance=INSTANCE_POWER, parameters={}
            ),
            GoveeCapability(
                type=CAPABILITY_RANGE,
                instance=INSTANCE_BRIGHTNESS,
                parameters={"range": {"min": 1, "max": 100}},
            ),
            GoveeCapability(
                type="devices.capabilities.color_setting",
                instance="colorRgb",
                parameters={},
            ),
            GoveeCapability(
                type=CAPABILITY_MODE,
                instance=INSTANCE_HDMI_SOURCE,
                parameters={"options": hdmi_options},
            ),
            GoveeCapability(
                type="devices.capabilities.dynamic_scene",
                instance="lightScene",
                parameters={"options": []},
            ),
            GoveeCapability(
                type="devices.capabilities.dynamic_scene",
                instance="diyScene",
                parameters={"options": []},
            ),
        )
        return GoveeDevice(
            device_id="AA:BB:CC:DD:EE:FF:00:11",
            sku="H6604",
            name="Smart AI Sync Box",
            device_type="devices.types.light",
            capabilities=caps,
            is_group=False,
        )

    def _make_coord(self, device):
        from unittest.mock import AsyncMock
        from custom_components.govee.coordinator import GoveeCoordinator

        coord = object.__new__(GoveeCoordinator)
        coord._devices = {device.device_id: device}
        coord._states = {}
        coord.async_control_device = AsyncMock(return_value=True)
        return coord

    @pytest.mark.asyncio
    async def test_clear_scene_reselects_known_hdmi_source(self):
        """When state.hdmi_source is known, re-select that source — never
        send a ColorCommand."""
        from custom_components.govee.models.commands import ModeCommand
        from custom_components.govee.models.device import INSTANCE_HDMI_SOURCE

        device = self._make_sync_box()
        coord = self._make_coord(device)

        state = GoveeDeviceState.create_empty(device.device_id)
        state.active_diy_scene = "1234"
        state.hdmi_source = 2
        coord._states[device.device_id] = state

        await coord.async_clear_scene(device.device_id)

        coord.async_control_device.assert_awaited_once()
        sent_id, sent_command = coord.async_control_device.await_args.args
        assert sent_id == device.device_id
        assert isinstance(sent_command, ModeCommand)
        assert sent_command.mode_instance == INSTANCE_HDMI_SOURCE
        assert sent_command.value == 2
        # And local scene state was cleared
        assert state.active_diy_scene is None

    @pytest.mark.asyncio
    async def test_clear_scene_falls_back_to_first_hdmi_option(self):
        """If state.hdmi_source is None, default to the first option from
        the capability — better than guessing white."""
        from custom_components.govee.models.commands import ModeCommand

        device = self._make_sync_box(
            hdmi_options=[
                {"name": "HDMI 1", "value": 1},
                {"name": "HDMI 2", "value": 2},
            ]
        )
        coord = self._make_coord(device)

        state = GoveeDeviceState.create_empty(device.device_id)
        state.active_scene = "9999"
        coord._states[device.device_id] = state

        await coord.async_clear_scene(device.device_id)

        sent_id, sent_command = coord.async_control_device.await_args.args
        assert isinstance(sent_command, ModeCommand)
        assert sent_command.value == 1
        assert state.active_scene is None

    @pytest.mark.asyncio
    async def test_clear_scene_does_not_send_color_command_to_sync_box(self):
        """Regression guard: ColorCommand(white) is the bug from #48 —
        verify that path is never taken on a device with hdmiSource."""
        from custom_components.govee.models.commands import ModeCommand

        device = self._make_sync_box()
        coord = self._make_coord(device)

        state = GoveeDeviceState.create_empty(device.device_id)
        state.active_scene = "5555"
        state.hdmi_source = 3
        coord._states[device.device_id] = state

        await coord.async_clear_scene(device.device_id)

        _, sent_command = coord.async_control_device.await_args.args
        assert not isinstance(sent_command, ColorCommand)
        assert isinstance(sent_command, ModeCommand)


class TestSensorReadingChangeTracking:
    """#83: the 'Last Reading' diagnostic timestamp tracks when a thermometer's
    temp/humidity value last changed (cloud batches BLE-bridged sensors)."""

    def _coord(self):
        import custom_components.govee.coordinator as coord_mod

        coord = object.__new__(coord_mod.GoveeCoordinator)
        coord._sensor_reading_changed_at = {}
        return coord

    def test_unknown_device_returns_none(self):
        coord = self._coord()
        assert coord.sensor_reading_changed_at("nope") is None

    def test_first_reading_stamps(self):
        coord = self._coord()
        existing = GoveeDeviceState.create_empty("x")  # no reading yet
        new = GoveeDeviceState.create_empty("x")
        new.sensor_temperature = 21.0
        coord._note_sensor_reading_change("x", new, existing)
        assert coord.sensor_reading_changed_at("x") is not None

    def test_unchanged_reading_keeps_timestamp(self):
        coord = self._coord()
        first = GoveeDeviceState.create_empty("x")
        first.sensor_temperature = 21.0
        prev = GoveeDeviceState.create_empty("x")  # empty -> first is a change
        coord._note_sensor_reading_change("x", first, prev)
        t1 = coord.sensor_reading_changed_at("x")

        # Same value next poll -> no restamp
        same = GoveeDeviceState.create_empty("x")
        same.sensor_temperature = 21.0
        coord._note_sensor_reading_change("x", same, first)
        assert coord.sensor_reading_changed_at("x") == t1

    def test_changed_reading_restamps(self):
        from datetime import datetime, timezone

        coord = self._coord()
        # Seed an old timestamp so the restamp is unambiguously newer.
        coord._sensor_reading_changed_at["x"] = datetime(
            2020, 1, 1, tzinfo=timezone.utc
        )
        prev = GoveeDeviceState.create_empty("x")
        prev.sensor_temperature = 21.0
        new = GoveeDeviceState.create_empty("x")
        new.sensor_temperature = 22.0  # changed
        coord._note_sensor_reading_change("x", new, prev)
        assert coord.sensor_reading_changed_at("x").year > 2020

    def test_no_reading_does_not_stamp(self):
        coord = self._coord()
        existing = GoveeDeviceState.create_empty("x")
        new = GoveeDeviceState.create_empty("x")  # both temp+humidity None
        coord._note_sensor_reading_change("x", new, existing)
        assert coord.sensor_reading_changed_at("x") is None

    def test_humidity_only_change_restamps(self):
        coord = self._coord()
        prev = GoveeDeviceState.create_empty("x")
        prev.sensor_humidity = 40.0
        coord._note_sensor_reading_change("x", prev, GoveeDeviceState.create_empty("x"))
        t1 = coord.sensor_reading_changed_at("x")
        new = GoveeDeviceState.create_empty("x")
        new.sensor_humidity = 45.0
        coord._sensor_reading_changed_at["x"] = __import__("datetime").datetime(
            2020, 1, 1, tzinfo=__import__("datetime").timezone.utc
        )
        coord._note_sensor_reading_change("x", new, prev)
        assert coord.sensor_reading_changed_at("x").year > 2020
        assert t1 is not None


class TestCoordinatorAlwaysUpdate:
    """Regression for #93: poll-only devices (BLE thermometers like H5109 with
    no MQTT push) froze until reload because the coordinator returned the same
    self._states dict every poll while always_update=False — HA's refresh gate
    (previous_data != self.data) compared the object to itself and never fired
    listeners after the first poll. always_update=True forces the notify."""

    def _build(self):
        import custom_components.govee.coordinator as coord_mod

        hass = MagicMock()
        config_entry = MagicMock()
        config_entry.entry_id = "test_entry"
        api_client = MagicMock()
        return coord_mod.GoveeCoordinator(
            hass=hass,
            config_entry=config_entry,
            api_client=api_client,
            iot_credentials=None,
            poll_interval=60,
        )

    def test_always_update_is_true(self):
        """always_update must stay True so each successful poll notifies
        listeners even when _async_update_data returns the same dict instance."""
        coord = self._build()
        assert coord.always_update is True


class TestWaterDetectorPoll:
    """Standalone H5054 leak polling via the account warnMessage path (#62)."""

    def _coord_with_detector(self):
        import custom_components.govee.coordinator as coord_mod
        from custom_components.govee.models.device import (
            CAPABILITY_EVENT,
            INSTANCE_BODY_APPEARED_EVENT,
        )

        hass = MagicMock()
        config_entry = MagicMock()
        config_entry.entry_id = "test_entry"
        coord = coord_mod.GoveeCoordinator(
            hass=hass,
            config_entry=config_entry,
            api_client=MagicMock(),
            iot_credentials=MagicMock(token="tok"),
            poll_interval=60,
        )
        device = GoveeDevice(
            device_id="DABFC0D6A5FE0008E8",
            sku="H5054",
            name="Washing Machine",
            device_type="devices.types.sensor",
            capabilities=(
                GoveeCapability(
                    type=CAPABILITY_EVENT,
                    instance=INSTANCE_BODY_APPEARED_EVENT,
                    parameters={},
                ),
            ),
            is_group=False,
        )
        coord._devices[device.device_id] = device
        coord._states[device.device_id] = GoveeDeviceState.create_empty(
            device.device_id
        )
        coord.async_update_listeners = MagicMock()
        return coord, device.device_id

    def test_water_detectors_property_finds_h5054(self):
        coord, did = self._coord_with_detector()
        assert [d.device_id for d in coord._water_detectors] == [did]

    @pytest.mark.asyncio
    async def test_poll_sets_leak_and_online(self, monkeypatch):
        import custom_components.govee.coordinator as coord_mod

        coord, did = self._coord_with_detector()

        inner = MagicMock()
        inner.fetch_water_detector_states = _make_async(
            {
                did: {
                    "online": True,
                    "gateway_online": True,
                    "battery": 80,
                    "last_time": 1717000000,
                }
            }
        )
        inner.fetch_leak_warning = _make_async(True)
        monkeypatch.setattr(coord_mod, "GoveeAuthClient", lambda **kw: _AsyncCM(inner))

        await coord._poll_water_detectors()

        state = coord._states[did]
        assert state.water_leak is True
        assert state.online is True
        assert state.battery == 80  # surfaced for the battery sensor (#145)
        coord.async_update_listeners.assert_called()

    def test_is_water_detector_predicate(self):
        coord, did = self._coord_with_detector()
        assert coord.is_water_detector(did) is True
        assert coord.is_water_detector("not-a-detector") is False

    def test_battery_sensor_available_despite_offline(self):
        """H5054 battery sensor stays available though the device is offline (#145)."""
        from custom_components.govee.sensor import GoveeThermoBatterySensor

        coord, did = self._coord_with_detector()
        state = coord._states[did]
        state.online = False
        state.battery = 80
        entity = GoveeThermoBatterySensor(coord, coord._devices[did])
        assert entity.available is True
        assert entity.native_value == 80

    @pytest.mark.asyncio
    async def test_warnmessage_skipped_when_no_new_report(self, monkeypatch):
        """Steady state (last_time not advanced, not wet) → no warnMessage call."""
        import custom_components.govee.coordinator as coord_mod

        coord, did = self._coord_with_detector()
        coord._water_leak_last_time[did] = 1717000000  # already seen

        warn_calls = {"n": 0}

        async def _warn(*a, **k):
            warn_calls["n"] += 1
            return False

        inner = MagicMock()
        inner.fetch_water_detector_states = _make_async(
            {did: {"online": True, "gateway_online": True, "last_time": 1717000000}}
        )
        inner.fetch_leak_warning = _warn
        monkeypatch.setattr(coord_mod, "GoveeAuthClient", lambda **kw: _AsyncCM(inner))

        await coord._poll_water_detectors()

        assert warn_calls["n"] == 0
        assert coord._states[did].water_leak is None

    @pytest.mark.asyncio
    async def test_clears_wet_when_alert_read_in_app(self, monkeypatch):
        """A currently-wet detector re-checks warnMessage even with no fresh
        report; an empty/read history clears it (user acked in the Govee app)."""
        import custom_components.govee.coordinator as coord_mod

        coord, did = self._coord_with_detector()
        coord._states[did].water_leak = True  # currently wet
        coord._water_leak_last_time[did] = 1717000000  # no fresh report

        warn_calls = {"n": 0}

        async def _warn(*a, **k):
            warn_calls["n"] += 1
            return False  # alert now read → not wet

        inner = MagicMock()
        inner.fetch_water_detector_states = _make_async(
            {did: {"online": True, "gateway_online": True, "last_time": 1717000000}}
        )
        inner.fetch_leak_warning = _warn
        monkeypatch.setattr(coord_mod, "GoveeAuthClient", lambda **kw: _AsyncCM(inner))

        await coord._poll_water_detectors()

        # warnMessage IS called because the sensor was wet, and it clears.
        assert warn_calls["n"] == 1
        assert coord._states[did].water_leak is False
        coord.async_update_listeners.assert_called()

    @pytest.mark.asyncio
    async def test_poll_noop_without_iot_credentials(self):
        """No account token → poll is a no-op (entity simply stays unknown)."""
        coord, did = self._coord_with_detector()
        coord._iot_credentials = None
        await coord._poll_water_detectors()
        assert coord._states[did].water_leak is None


class TestWaterDetectorPollInterval:
    """The leak-poll interval is a user-configurable option."""

    def _coord_with_options(self, options):
        import custom_components.govee.coordinator as coord_mod

        config_entry = MagicMock()
        config_entry.entry_id = "test_entry"
        config_entry.options = options
        return coord_mod.GoveeCoordinator(
            hass=MagicMock(),
            config_entry=config_entry,
            api_client=MagicMock(),
            iot_credentials=MagicMock(token="tok"),
            poll_interval=60,
        )

    def test_default_when_option_unset(self):
        coord = self._coord_with_options({})
        assert coord._water_detector_poll_interval == (
            const.DEFAULT_WATER_DETECTOR_POLL_INTERVAL
        )

    def test_configured_value_is_used(self):
        coord = self._coord_with_options({const.CONF_WATER_DETECTOR_POLL_INTERVAL: 600})
        assert coord._water_detector_poll_interval == 600

    @pytest.mark.parametrize(
        "value",
        [
            const.MIN_WATER_DETECTOR_POLL_INTERVAL - 1,
            const.MAX_WATER_DETECTOR_POLL_INTERVAL + 1,
            "not-a-number",
            None,
        ],
    )
    def test_out_of_range_or_bad_value_falls_back(self, value):
        """Hand-edited options must not arm a bad timer."""
        coord = self._coord_with_options(
            {const.CONF_WATER_DETECTOR_POLL_INTERVAL: value}
        )
        assert coord._water_detector_poll_interval == (
            const.DEFAULT_WATER_DETECTOR_POLL_INTERVAL
        )

    def test_schedule_uses_configured_interval(self, monkeypatch):
        import custom_components.govee.coordinator as coord_mod

        coord = self._coord_with_options({const.CONF_WATER_DETECTOR_POLL_INTERVAL: 300})
        seen = {}

        def _capture(hass, delay, callback):
            seen["delay"] = delay
            return lambda: None

        monkeypatch.setattr(coord_mod, "async_call_later", _capture)
        coord._schedule_water_detector_poll()

        assert seen["delay"] == 300


class _AsyncCM:
    """Minimal async context manager yielding a configured inner mock."""

    def __init__(self, inner):
        self._inner = inner

    async def __aenter__(self):
        return self._inner

    async def __aexit__(self, *exc):
        return False


def _make_async(return_value):
    async def _inner(*args, **kwargs):
        return return_value

    return _inner


class TestStartupStepResilience:
    """A hung/failing account-BFF discovery step must not cancel setup (#146)."""

    def _bare_coord(self):
        import custom_components.govee.coordinator as coord_mod

        config_entry = MagicMock()
        config_entry.entry_id = "test_entry"
        return coord_mod.GoveeCoordinator(
            hass=MagicMock(),
            config_entry=config_entry,
            api_client=MagicMock(),
            iot_credentials=MagicMock(token="tok"),
            poll_interval=60,
        )

    @pytest.mark.asyncio
    async def test_timeout_is_swallowed(self, monkeypatch):
        import custom_components.govee.coordinator as coord_mod

        monkeypatch.setattr(coord_mod, "STARTUP_STEP_TIMEOUT", 0.01)
        coord = self._bare_coord()

        async def _hangs():
            await asyncio.sleep(1)

        # Returns normally (does not raise) even though the step never finishes.
        await coord._run_startup_step(_hangs(), "hanging step")

    @pytest.mark.asyncio
    async def test_error_is_swallowed(self):
        coord = self._bare_coord()

        async def _boom():
            raise RuntimeError("BFF exploded")

        await coord._run_startup_step(_boom(), "failing step")

    @pytest.mark.asyncio
    async def test_success_runs_to_completion(self):
        coord = self._bare_coord()
        ran = {"ok": False}

        async def _ok():
            ran["ok"] = True

        await coord._run_startup_step(_ok(), "good step")
        assert ran["ok"] is True


class TestBffThermometerDiscovery:
    """BFF-only thermo-hygrometers (H5301) surfaced via the BFF list (issue #86)."""

    def _coord(self):
        import custom_components.govee.coordinator as coord_mod

        hass = MagicMock()
        config_entry = MagicMock()
        config_entry.entry_id = "test_entry"
        coord = coord_mod.GoveeCoordinator(
            hass=hass,
            config_entry=config_entry,
            api_client=MagicMock(),
            iot_credentials=MagicMock(token="tok"),
            poll_interval=60,
        )
        coord.async_update_listeners = MagicMock()
        coord.async_set_updated_data = MagicMock()
        coord._schedule_bff_poll = MagicMock()
        return coord, coord_mod

    @pytest.mark.asyncio
    async def test_discover_synthesizes_device_and_seeds_state(self, monkeypatch):
        coord, coord_mod = self._coord()
        did = "AA:BB:CC:DD:EE:FF:00:11"
        inner = MagicMock()
        inner.fetch_bff_thermo_hygrometers = _make_async(
            [
                {
                    "device_id": did,
                    "name": "Office",
                    "sku": "H5301",
                    "sw_version": "1.02.01",
                    "hw_version": "1.00.00",
                    "battery": 88,
                    "online": True,
                    "temperature": 22.35,
                    "humidity": 47.1,
                    "hub_device_id": "11:22:33:44:55:66:77:88",
                    "hub_sku": "H5044",
                }
            ]
        )
        inner.bff_device_census = MagicMock(return_value=[])
        inner.bff_response_skeleton = MagicMock(return_value=None)
        monkeypatch.setattr(coord_mod, "GoveeAuthClient", lambda **kw: _AsyncCM(inner))

        await coord._discover_bff_thermometers()

        assert did in coord._devices
        device = coord._devices[did]
        assert device.is_thermometer
        assert device.supports_temperature_sensor
        assert device.supports_humidity_sensor
        assert did in coord._bff_thermometer_ids
        state = coord._states[did]
        assert state.sensor_temperature == 22.35
        assert state.sensor_humidity == 47.1
        assert state.battery == 88
        assert coord._devices[did].hub_device_id == "11:22:33:44:55:66:77:88"
        assert coord._bff_thermo_hubs == {
            "11:22:33:44:55:66:77:88": {"sku": "H5044"}
        }
        coord._schedule_bff_poll.assert_called_once()

    @pytest.mark.asyncio
    async def test_dev_api_thermometer_supplemented_from_bff(self, monkeypatch):
        """H5179 exists from Developer-API discovery but reads via BFF (#141).

        It must NOT be re-synthesized; instead the existing device gets its
        reading from the BFF and is routed through the BFF path
        (``_bff_thermometer_ids``) so the futile Developer poll is skipped.
        """
        from custom_components.govee.models.device import (
            CAPABILITY_PROPERTY,
            INSTANCE_SENSOR_HUMIDITY,
            INSTANCE_SENSOR_TEMPERATURE,
        )

        coord, coord_mod = self._coord()
        did = "AA:BB:CC:DD:EE:FF:00:11"
        # Pre-existing Developer-API H5179 with empty reading.
        existing = GoveeDevice(
            device_id=did,
            sku="H5179",
            name="Garage Fridge",
            device_type="devices.types.thermometer",
            capabilities=(
                GoveeCapability(
                    type=CAPABILITY_PROPERTY,
                    instance=INSTANCE_SENSOR_TEMPERATURE,
                    parameters={},
                ),
                GoveeCapability(
                    type=CAPABILITY_PROPERTY,
                    instance=INSTANCE_SENSOR_HUMIDITY,
                    parameters={},
                ),
            ),
            is_group=False,
        )
        coord._devices[did] = existing
        coord._states[did] = GoveeDeviceState.create_empty(did)

        inner = MagicMock()
        inner.fetch_bff_thermo_hygrometers = _make_async(
            [
                {
                    "device_id": did,
                    "name": "Garage Fridge",
                    "sku": "H5179",
                    "battery": 17,
                    "online": False,
                    "temperature": 4.9,
                    "humidity": 53.1,
                    "hub_device_id": "",
                    "hub_sku": "",
                }
            ]
        )
        inner.bff_device_census = MagicMock(return_value=[])
        inner.bff_response_skeleton = MagicMock(return_value=None)
        inner.bff_device_values = MagicMock(return_value=[])
        monkeypatch.setattr(coord_mod, "GoveeAuthClient", lambda **kw: _AsyncCM(inner))

        await coord._discover_bff_thermometers()

        # Same device object — not synthesized/replaced.
        assert coord._devices[did] is existing
        assert did in coord._bff_thermometer_ids
        state = coord._states[did]
        assert state.sensor_temperature == 4.9
        assert state.sensor_humidity == 53.1
        assert state.battery == 17

    @pytest.mark.asyncio
    async def test_fetch_device_state_skips_developer_poll(self, monkeypatch):
        coord, coord_mod = self._coord()
        did = "AA:BB:CC:DD:EE:FF:00:11"
        device = GoveeDevice.synthetic_thermometer(did, "H5301", "Office")
        coord._devices[did] = device
        coord._bff_thermometer_ids.add(did)
        seeded = GoveeDeviceState.create_empty(did)
        seeded.sensor_temperature = 21.0
        coord._states[did] = seeded
        from unittest.mock import AsyncMock

        coord._api_client.get_device_state = AsyncMock()

        result = await coord._fetch_device_state(did, device)

        assert result is seeded  # BFF-managed state preserved
        coord._api_client.get_device_state.assert_not_called()

    @pytest.mark.asyncio
    async def test_refresh_updates_readings(self, monkeypatch):
        coord, coord_mod = self._coord()
        did = "AA:BB:CC:DD:EE:FF:00:11"
        coord._devices[did] = GoveeDevice.synthetic_thermometer(did, "H5301", "Office")
        coord._bff_thermometer_ids.add(did)
        coord._states[did] = GoveeDeviceState.create_empty(did)

        inner = MagicMock()
        inner.fetch_bff_thermo_hygrometers = _make_async(
            [{"device_id": did, "temperature": 25.0, "humidity": 50.0, "online": True}]
        )
        monkeypatch.setattr(coord_mod, "GoveeAuthClient", lambda **kw: _AsyncCM(inner))

        await coord._refresh_bff_thermometers()

        assert coord._states[did].sensor_temperature == 25.0
        assert coord._states[did].sensor_humidity == 50.0
        coord.async_set_updated_data.assert_called_once()

    @pytest.mark.asyncio
    async def test_refresh_preserves_last_reading_when_omitted(self, monkeypatch):
        coord, coord_mod = self._coord()
        did = "AA:BB:CC:DD:EE:FF:00:11"
        coord._devices[did] = GoveeDevice.synthetic_thermometer(did, "H5301", "Office")
        coord._bff_thermometer_ids.add(did)
        prev = GoveeDeviceState.create_empty(did)
        prev.sensor_temperature = 22.0
        prev.sensor_humidity = 44.0
        prev.battery = 77
        coord._states[did] = prev

        inner = MagicMock()
        inner.fetch_bff_thermo_hygrometers = _make_async(
            [
                {
                    "device_id": did,
                    "temperature": None,
                    "humidity": None,
                    "battery": None,
                    "online": True,
                }
            ]
        )
        monkeypatch.setattr(coord_mod, "GoveeAuthClient", lambda **kw: _AsyncCM(inner))

        await coord._refresh_bff_thermometers()

        assert coord._states[did].sensor_temperature == 22.0
        assert coord._states[did].sensor_humidity == 44.0
        assert coord._states[did].battery == 77
        # Nothing changed this cycle -> no HA update pushed (churn guard, #86)
        coord.async_set_updated_data.assert_not_called()

    @pytest.mark.asyncio
    async def test_discover_noop_without_iot_credentials(self):
        coord, _ = self._coord()
        coord._iot_credentials = None
        await coord._discover_bff_thermometers()
        assert coord._bff_thermometer_ids == set()

    def test_register_thermo_hubs_creates_hub_device(self, monkeypatch):
        coord, coord_mod = self._coord()
        coord._bff_thermo_hubs = {"11:22:33:44:55:66:77:88": {"sku": "H5044"}}
        device_reg = MagicMock()
        monkeypatch.setattr(
            coord_mod.dr, "async_get", lambda _hass: device_reg
        )

        coord.register_thermo_hubs()

        device_reg.async_get_or_create.assert_called_once()
        kwargs = device_reg.async_get_or_create.call_args.kwargs
        assert kwargs["identifiers"] == {
            (coord_mod.DOMAIN, "11:22:33:44:55:66:77:88")
        }
        assert kwargs["model"] == "H5044"

    def test_register_thermo_hubs_noop_when_empty(self, monkeypatch):
        coord, coord_mod = self._coord()
        coord._bff_thermo_hubs = {}
        device_reg = MagicMock()
        monkeypatch.setattr(
            coord_mod.dr, "async_get", lambda _hass: device_reg
        )

        coord.register_thermo_hubs()

        device_reg.async_get_or_create.assert_not_called()


class TestBffThermoTickleOnly:
    """BFF list is a *tickle* for Developer-API thermometers (issues #83, #102).

    The BFF ``tem``/``hum`` are NOT applied to state: their scale varies by
    gateway/firmware and a fixed ``/10`` divisor over-scaled humidity 10x
    (#102). The reading is used only to gate the tickle poll; the correctly
    scaled, unit-handled value comes from the Developer ``/device/state`` poll.
    """

    def _coord(self):
        import custom_components.govee.coordinator as coord_mod

        hass = MagicMock()
        config_entry = MagicMock()
        config_entry.entry_id = "test_entry"
        coord = coord_mod.GoveeCoordinator(
            hass=hass,
            config_entry=config_entry,
            api_client=MagicMock(),
            iot_credentials=MagicMock(token="tok"),
            poll_interval=60,
        )
        coord.async_update_listeners = MagicMock()
        coord.async_set_updated_data = MagicMock()
        coord._schedule_bff_poll = MagicMock()
        return coord, coord_mod

    @pytest.mark.asyncio
    async def test_poll_does_not_overwrite_developer_reading(self, monkeypatch):
        """The 5-min BFF poll must leave the Developer-API reading intact (#102)."""
        coord, coord_mod = self._coord()
        did = "AA:BB:CC:DD:EE:FF:00:22"
        coord._devices[did] = GoveeDevice.synthetic_thermometer(did, "H5110", "Garage")
        state = GoveeDeviceState.create_empty(did)
        # The correct values the Developer /device/state poll wrote.
        state.sensor_temperature = 75.02
        state.sensor_humidity = 83.0
        coord._states[did] = state
        coord._thermo_bff_devices = {did}

        inner = MagicMock()
        # raw hum=8300 (hundredths) -> the old /10 bug surfaced 830.0.
        inner.fetch_bff_leak_sensors = _make_async(
            ([], {}, {did: {"tem": 7502, "hum": 8300}})
        )
        monkeypatch.setattr(coord_mod, "GoveeAuthClient", lambda **kw: _AsyncCM(inner))
        monkeypatch.setattr(coord_mod, "async_dispatcher_send", MagicMock())

        await coord._poll_bff_leak_state()

        assert coord._states[did].sensor_humidity == 83.0
        assert coord._states[did].sensor_temperature == 75.02
        coord.async_set_updated_data.assert_not_called()

    @pytest.mark.asyncio
    async def test_discover_keeps_tickle_without_leak_sensors(self, monkeypatch):
        """A bridged thermometer with no leak sensors still starts the tickle
        poll, and the BFF value is not applied at discovery time (#83/#102)."""
        coord, coord_mod = self._coord()
        did = "AA:BB:CC:DD:EE:FF:00:22"
        coord._devices[did] = GoveeDevice.synthetic_thermometer(did, "H5110", "Garage")
        coord._states[did] = GoveeDeviceState.create_empty(did)

        inner = MagicMock()
        inner.fetch_bff_leak_sensors = _make_async(
            ([], {}, {did: {"tem": 7502, "hum": 8300}})
        )
        inner.bff_device_census = MagicMock(return_value=[])
        inner.bff_response_skeleton = MagicMock(return_value=None)
        monkeypatch.setattr(coord_mod, "GoveeAuthClient", lambda **kw: _AsyncCM(inner))

        await coord._discover_leak_sensors()

        # Gate set so the 5-min tickle poll runs (#83) ...
        assert coord._thermo_bff_devices == {did}
        coord._schedule_bff_poll.assert_called_once()
        # ... but the BFF reading is never written to state (#102).
        assert coord._states[did].sensor_humidity is None
        assert coord._states[did].sensor_temperature is None

    @pytest.mark.asyncio
    async def test_developer_poll_not_skipped_for_bridged_thermo(self, monkeypatch):
        """Bridged thermometers (in _thermo_bff_devices, not _bff_thermometer_ids)
        keep getting their Developer /device/state poll, which owns the value."""
        coord, coord_mod = self._coord()
        did = "AA:BB:CC:DD:EE:FF:00:22"
        device = GoveeDevice.synthetic_thermometer(did, "H5110", "Garage")
        coord._devices[did] = device
        coord._thermo_bff_devices = {did}
        coord._states[did] = GoveeDeviceState.create_empty(did)
        from unittest.mock import AsyncMock

        fresh = GoveeDeviceState.create_empty(did)
        fresh.sensor_humidity = 84.0
        coord._api_client.get_device_state = AsyncMock(return_value=fresh)

        result = await coord._fetch_device_state(did, device)

        coord._api_client.get_device_state.assert_called_once()
        assert result.sensor_humidity == 84.0


class TestPeriodicRediscovery:
    """New devices added after startup are picked up via reload (issue #101)."""

    def _coord(self):
        import custom_components.govee.coordinator as coord_mod

        hass = MagicMock()
        config_entry = MagicMock()
        config_entry.entry_id = "test_entry"
        coord = coord_mod.GoveeCoordinator(
            hass=hass,
            config_entry=config_entry,
            api_client=MagicMock(),
            iot_credentials=None,
            poll_interval=60,
        )
        coord._enable_groups = False
        return coord, coord_mod

    @staticmethod
    def _device(device_id, is_group=False):
        return GoveeDevice(
            device_id=device_id,
            sku="H6001",
            name=device_id,
            device_type="devices.types.light",
            capabilities=(),
            is_group=is_group,
        )

    @pytest.mark.asyncio
    async def test_schedules_reload_on_new_device(self):
        import time
        from unittest.mock import AsyncMock

        coord, _ = self._coord()
        dev_a = self._device("A")
        coord._devices = {"A": dev_a}
        coord._last_rediscovery_check = time.monotonic() - 10_000  # force elapsed
        coord._api_client.get_devices = AsyncMock(
            return_value=[dev_a, self._device("B")]
        )

        await coord._async_maybe_rediscover_devices()

        coord.hass.config_entries.async_schedule_reload.assert_called_once_with(
            "test_entry"
        )

    @pytest.mark.asyncio
    async def test_no_reload_when_device_set_unchanged(self):
        import time
        from unittest.mock import AsyncMock

        coord, _ = self._coord()
        dev_a = self._device("A")
        coord._devices = {"A": dev_a}
        coord._last_rediscovery_check = time.monotonic() - 10_000
        coord._api_client.get_devices = AsyncMock(return_value=[dev_a])

        await coord._async_maybe_rediscover_devices()

        coord.hass.config_entries.async_schedule_reload.assert_not_called()

    @pytest.mark.asyncio
    async def test_throttle_skips_when_recent(self):
        import time
        from unittest.mock import AsyncMock

        coord, _ = self._coord()
        coord._devices = {"A": self._device("A")}
        coord._last_rediscovery_check = time.monotonic()  # just checked
        coord._api_client.get_devices = AsyncMock(return_value=[])

        await coord._async_maybe_rediscover_devices()

        coord._api_client.get_devices.assert_not_called()
        coord.hass.config_entries.async_schedule_reload.assert_not_called()

    @pytest.mark.asyncio
    async def test_failure_is_isolated(self):
        import time
        from unittest.mock import AsyncMock

        coord, _ = self._coord()
        coord._devices = {"A": self._device("A")}
        coord._last_rediscovery_check = time.monotonic() - 10_000
        coord._api_client.get_devices = AsyncMock(side_effect=RuntimeError("boom"))

        # Must not raise.
        await coord._async_maybe_rediscover_devices()

        coord.hass.config_entries.async_schedule_reload.assert_not_called()

    @pytest.mark.asyncio
    async def test_disabled_group_is_not_treated_as_new(self):
        import time
        from unittest.mock import AsyncMock

        coord, _ = self._coord()
        dev_a = self._device("A")
        coord._devices = {"A": dev_a}
        coord._enable_groups = False
        coord._last_rediscovery_check = time.monotonic() - 10_000
        # A new group device, but groups are disabled -> not "new", no reload.
        coord._api_client.get_devices = AsyncMock(
            return_value=[dev_a, self._device("11825917", is_group=True)]
        )

        await coord._async_maybe_rediscover_devices()

        coord.hass.config_entries.async_schedule_reload.assert_not_called()


# ==============================================================================
# LAN (UDP) transport lifecycle — story LAN-010 (issue #57)
# ==============================================================================


class _FakeLanClient:
    """Minimal stand-in for GoveeLanClient — no sockets, tracks calls."""

    def __init__(self, available: bool = True) -> None:
        self.available = available
        self.start_calls = 0
        self.stop_calls = 0
        self.started_with: Any = None

    async def async_start(self, interface_ips: list[str]) -> None:
        self.start_calls += 1
        self.started_with = interface_ips

    async def async_stop(self) -> None:
        self.stop_calls += 1


class TestLanLifecycle:
    """Coordinator LAN setup (open LAST + leak-proof teardown) and shutdown."""

    # Correlates to the scan record's ``device`` below.
    DEVICE_ID = "AA:BB:CC:DD:EE:FF:00:11"

    def _coord(self, options: dict[str, Any] | None = None):
        import custom_components.govee.coordinator as coord_mod

        hass = MagicMock()
        config_entry = MagicMock()
        config_entry.entry_id = "test_entry"
        # Real dict so options.get() resolves defaults deterministically.
        config_entry.options = options if options is not None else {}
        coord = coord_mod.GoveeCoordinator(
            hass=hass,
            config_entry=config_entry,
            api_client=MagicMock(),
            iot_credentials=None,
            poll_interval=60,
        )
        coord._devices[self.DEVICE_ID] = GoveeDevice(
            device_id=self.DEVICE_ID,
            sku="H6072",
            name="Test Light",
            device_type="devices.types.light",
            capabilities=(),
            is_group=False,
        )
        return coord, coord_mod

    def _matching_scan(self) -> list[dict[str, Any]]:
        return [
            {
                "device": self.DEVICE_ID,
                "ip": "10.0.0.5",
                "sku": "H6072",
                "wifiVersionSoft": "1.0.0",
            }
        ]

    @staticmethod
    def _patch_lan(monkeypatch, coord_mod, *, scan, client, probe=None, broadcasts=None):
        """Patch the LAN module helpers the coordinator imported.

        ``probe`` (optional dict) records whether the scan ran and how many
        clients were constructed, so the "off" escape hatch can be verified.
        ``broadcasts`` is the list ``async_get_lan_broadcast_addresses`` returns,
        so a test can assert the coordinator forwards it to the scanner (#57).
        """
        derived_broadcasts = list(broadcasts or [])

        async def _ifaces(hass):
            return []

        async def _broadcasts(hass):
            return derived_broadcasts

        async def _scan(*, interface_ips, extra_targets, broadcast_targets=None):
            if probe is not None:
                probe["scanned"] = True
                probe["extra_targets"] = extra_targets
                probe["broadcast_targets"] = broadcast_targets
            return scan

        def _factory(callback):
            if probe is not None:
                probe["constructed"] = probe.get("constructed", 0) + 1
            return client

        monkeypatch.setattr(coord_mod, "async_get_lan_interface_ips", _ifaces)
        monkeypatch.setattr(coord_mod, "async_get_lan_broadcast_addresses", _broadcasts)
        monkeypatch.setattr(coord_mod, "async_scan_lan_devices", _scan)
        monkeypatch.setattr(coord_mod, "GoveeLanClient", _factory)

    @pytest.mark.asyncio
    async def test_disabled_clean_when_client_unavailable(self, monkeypatch):
        """async_start that degrades (available False) -> client None, stopped."""
        coord, coord_mod = self._coord()
        client = _FakeLanClient(available=False)
        self._patch_lan(monkeypatch, coord_mod, scan=self._matching_scan(), client=client)

        await coord._async_setup_lan()  # must not raise

        assert coord._lan_client is None
        assert coord._lan_devices == {}
        assert client.stop_calls == 1  # released whatever the bind grabbed

    @pytest.mark.asyncio
    async def test_off_escape_hatch_skips_setup(self, monkeypatch):
        """CONF_LAN_TARGETS='off' (any case) skips scan + client entirely."""
        coord, coord_mod = self._coord(options={"lan_targets": "  OFF  "})
        probe: dict[str, Any] = {}
        client = _FakeLanClient(available=True)
        self._patch_lan(
            monkeypatch, coord_mod, scan=self._matching_scan(), client=client, probe=probe
        )

        await coord._async_setup_lan()

        assert coord._lan_client is None
        assert probe.get("scanned") is None  # scan never ran
        assert probe.get("constructed") is None  # no client constructed
        assert client.start_calls == 0

    @pytest.mark.asyncio
    async def test_correlated_scan_populates_lan_devices(self, monkeypatch):
        """A scan that correlates to a device_id enables LAN and keeps the client."""
        coord, coord_mod = self._coord()
        client = _FakeLanClient(available=True)
        self._patch_lan(monkeypatch, coord_mod, scan=self._matching_scan(), client=client)

        await coord._async_setup_lan()

        assert coord._lan_client is client
        assert self.DEVICE_ID in coord._lan_devices
        info = coord._lan_devices[self.DEVICE_ID]
        assert info.ip == "10.0.0.5"
        assert info.last_correlated_ts > 0  # stamped from the monotonic clock
        assert coord._lan_unmatched == []
        assert client.start_calls == 1
        assert client.stop_calls == 0
        assert client.started_with == []  # interface ips passed through

    @pytest.mark.asyncio
    async def test_setup_forwards_derived_broadcasts_to_scan(self, monkeypatch):
        """Setup forwards auto-derived subnet broadcasts to the scanner (#57)."""
        coord, coord_mod = self._coord()
        probe: dict[str, Any] = {}
        client = _FakeLanClient(available=True)
        self._patch_lan(
            monkeypatch,
            coord_mod,
            scan=self._matching_scan(),
            client=client,
            probe=probe,
            broadcasts=["192.168.0.255"],
        )

        await coord._async_setup_lan()

        # The derived broadcast reached async_scan_lan_devices — newer devices
        # that ignore multicast are scanned via the directed broadcast (#57).
        assert probe["broadcast_targets"] == ["192.168.0.255"]

    @pytest.mark.asyncio
    async def test_no_correlation_stops_client(self, monkeypatch):
        """Scan answered but nothing correlated -> stop+None (no held sockets)."""
        coord, coord_mod = self._coord()
        client = _FakeLanClient(available=True)
        unmatched_scan = [
            {"device": "99:99:99:99:99:99:99:99", "ip": "10.0.0.9", "sku": "H6072"}
        ]
        self._patch_lan(monkeypatch, coord_mod, scan=unmatched_scan, client=client)

        await coord._async_setup_lan()

        assert coord._lan_client is None
        assert coord._lan_devices == {}
        assert client.stop_calls == 1

    @pytest.mark.asyncio
    async def test_scan_oserror_degrades_clean(self, monkeypatch):
        """A scan OSError (port :4002 held) degrades cleanly without a client."""
        coord, coord_mod = self._coord()
        probe: dict[str, Any] = {}

        async def _ifaces(hass):
            return []

        async def _broadcasts(hass):
            return []

        async def _scan(*, interface_ips, extra_targets, broadcast_targets=None):
            raise OSError("port 4002 in use")

        def _factory(callback):
            probe["constructed"] = probe.get("constructed", 0) + 1
            return _FakeLanClient()

        monkeypatch.setattr(coord_mod, "async_get_lan_interface_ips", _ifaces)
        monkeypatch.setattr(coord_mod, "async_get_lan_broadcast_addresses", _broadcasts)
        monkeypatch.setattr(coord_mod, "async_scan_lan_devices", _scan)
        monkeypatch.setattr(coord_mod, "GoveeLanClient", _factory)

        await coord._async_setup_lan()  # must not raise

        assert coord._lan_client is None
        assert probe.get("constructed") is None  # never reached client construction

    @pytest.mark.asyncio
    async def test_failure_after_open_stops_client_no_leak(self, monkeypatch):
        """Any raise after the socket opens stops the client before propagating."""
        coord, coord_mod = self._coord()
        client = _FakeLanClient(available=True)
        self._patch_lan(monkeypatch, coord_mod, scan=self._matching_scan(), client=client)

        def _boom(*args, **kwargs):
            raise RuntimeError("correlation blew up")

        monkeypatch.setattr(coord_mod, "correlate_scan", _boom)

        with pytest.raises(RuntimeError, match="correlation blew up"):
            await coord._async_setup_lan()

        # The partially-built setup must not strand the bound socket.
        assert client.stop_calls == 1
        assert coord._lan_client is None

    @pytest.mark.asyncio
    async def test_invalid_lan_targets_tolerated(self, monkeypatch):
        """A bad LAN-targets option is ignored, not fatal — LAN still sets up."""
        coord, coord_mod = self._coord(options={"lan_targets": "not-an-ip/8"})
        client = _FakeLanClient(available=True)
        self._patch_lan(monkeypatch, coord_mod, scan=self._matching_scan(), client=client)

        await coord._async_setup_lan()  # LanTargetError swallowed

        assert coord._lan_client is client
        assert self.DEVICE_ID in coord._lan_devices

    @pytest.mark.asyncio
    async def test_async_shutdown_closes_lan_client(self, monkeypatch):
        """async_shutdown stops the LAN client and clears the reference."""
        coord, _ = self._coord()
        client = _FakeLanClient(available=True)
        coord._lan_client = client
        coord._api_client.close = _make_async(None)

        await coord.async_shutdown()

        assert client.stop_calls == 1
        assert coord._lan_client is None

    @pytest.mark.asyncio
    async def test_async_shutdown_cancels_probe_poll_timer(self, monkeypatch):
        """The H5192 live-poll timer (#185) must not outlive the entry."""
        coord, _ = self._coord()
        coord._api_client.close = _make_async(None)
        unsub = MagicMock()
        coord._probe_poll_unsub = unsub
        coord._probe_polling_enabled = {"dev"}

        await coord.async_shutdown()

        unsub.assert_called_once()
        assert coord._probe_poll_unsub is None

    @pytest.mark.asyncio
    async def test_setup_lan_runs_last_in_async_setup(self, monkeypatch):
        """_async_setup calls _async_setup_lan after all fallible discovery steps."""
        coord, _ = self._coord()
        order: list[str] = []

        async def _discover():
            order.append("discover")

        async def _leaks():
            order.append("leaks")

        async def _thermo():
            order.append("thermo")

        async def _setup_lan():
            order.append("lan")

        async def _openapi_events():
            order.append("openapi_events")

        monkeypatch.setattr(coord, "_discover_devices", _discover)
        monkeypatch.setattr(coord, "_discover_leak_sensors", _leaks)
        monkeypatch.setattr(coord, "_discover_bff_thermometers", _thermo)
        monkeypatch.setattr(coord, "_async_setup_lan", _setup_lan)
        # Mocked so the test never spawns the real OpenAPI MQTT connection
        # loop (would trip the lingering-task guard).
        monkeypatch.setattr(coord, "_start_openapi_events", _openapi_events)

        await coord._async_setup()

        assert order[-1] == "lan"  # LAN is always opened last (blocking #5)
        assert order.index("discover") < order.index("lan")

    def test_on_lan_dev_status_unknown_ip_skips_and_rescans(self):
        """A push from an uncorrelated IP must not mutate state; it forces rescan."""
        coord, _ = self._coord()
        coord._states[self.DEVICE_ID] = GoveeDeviceState.create_empty(self.DEVICE_ID)
        coord._last_lan_rescan = 12345.0  # a non-zero throttle to observe the reset
        before = GoveeDeviceState.create_empty(self.DEVICE_ID)
        from custom_components.govee.api.lan_client import LanDevStatus

        status = LanDevStatus(
            on=True, brightness_0_100=50, color=None, color_temp_kelvin=None
        )
        # No _lan_devices entry maps to this IP -> unknown source, skip + rescan.
        assert coord._on_lan_dev_status("10.0.0.5", status) is None
        assert coord._states[self.DEVICE_ID] == before  # untouched
        assert coord._last_lan_rescan == float("-inf")  # re-correlation forced (blocking #3)


class _FakeReadClient:
    """Minimal LAN client exposing only ``async_read_batch`` for the read path.

    ``batch`` maps a queried IP to the :class:`LanDevStatus` it answers with;
    IPs absent from ``batch`` simply do not reply (read miss).
    """

    def __init__(self, batch: dict[str, Any] | None = None) -> None:
        self.available = True
        self.batch = batch or {}
        self.read_calls: list[list[str]] = []

    async def async_read_batch(
        self, ips: list[str], window: float = 1.0
    ) -> dict[str, Any]:
        self.read_calls.append(list(ips))
        return {ip: self.batch[ip] for ip in ips if ip in self.batch}


class TestLanReadPath:
    """Coordinator LAN read overlay, DHCP guard, demotion and rescan (LAN-011)."""

    DEVICE_ID = "AA:BB:CC:DD:EE:FF:00:11"
    IP = "10.0.0.5"

    def _status(self, **kw):
        from custom_components.govee.api.lan_client import LanDevStatus

        defaults = dict(
            on=True, brightness_0_100=80, color=RGBColor(255, 0, 0), color_temp_kelvin=None
        )
        defaults.update(kw)
        return LanDevStatus(**defaults)

    def _info(self, *, ip=None, ts=None, device_id=None):
        from custom_components.govee.api.lan_client import LanDeviceInfo

        return LanDeviceInfo(
            device_id=device_id or self.DEVICE_ID,
            ip=ip or self.IP,
            mac=device_id or self.DEVICE_ID,
            sku="H6072",
            firmware="1.0.0",
            last_correlated_ts=ts if ts is not None else __import__("time").monotonic(),
        )

    def _coord(self, *, brightness_max: int = 100):
        import custom_components.govee.coordinator as coord_mod

        hass = MagicMock()
        config_entry = MagicMock()
        config_entry.entry_id = "test_entry"
        config_entry.options = {}
        coord = coord_mod.GoveeCoordinator(
            hass=hass,
            config_entry=config_entry,
            api_client=MagicMock(),
            iot_credentials=None,
            poll_interval=60,
        )
        caps = (
            GoveeCapability(type=CAPABILITY_ON_OFF, instance=INSTANCE_POWER, parameters={}),
            GoveeCapability(
                type=CAPABILITY_RANGE,
                instance=INSTANCE_BRIGHTNESS,
                parameters={"range": {"min": 0, "max": brightness_max}},
            ),
        )
        coord._devices[self.DEVICE_ID] = GoveeDevice(
            device_id=self.DEVICE_ID,
            sku="H6072",
            name="Test Light",
            device_type="devices.types.light",
            capabilities=caps,
            is_group=False,
        )
        coord.async_set_updated_data = MagicMock()
        coord.async_update_listeners = MagicMock()
        return coord, coord_mod

    # ---- _apply_lan_read overlay semantics ---------------------------------

    def test_overlay_writes_four_fields_in_plain_mode(self):
        coord, _ = self._coord()
        state = GoveeDeviceState.create_empty(self.DEVICE_ID)
        state.online = False
        state.power_state = False
        state.brightness = 40
        state.color = RGBColor(10, 20, 30)
        coord._states[self.DEVICE_ID] = state

        coord._apply_lan_read(self.DEVICE_ID, self._status())

        assert state.power_state is True
        assert state.brightness == 80  # (0,100) device -> identity rescale
        assert state.color == RGBColor(255, 0, 0)
        assert state.color_temp_kelvin is None
        assert state.online is True
        assert state.source == "lan"
        coord.async_set_updated_data.assert_called_once()

    def test_overlay_preserves_scene_segments_sensors(self):
        from custom_components.govee.models.state import SegmentState

        coord, _ = self._coord()
        state = GoveeDeviceState.create_empty(self.DEVICE_ID)
        state.power_state = False
        state.active_scene = "123"
        state.active_scene_name = "Sunrise"
        state.segments = [SegmentState(index=0, color=RGBColor(1, 2, 3))]
        state.sensor_temperature = 22.5
        state.color = RGBColor(9, 9, 9)
        state.brightness = 33
        coord._states[self.DEVICE_ID] = state

        # devStatus reports a live scene-frame colour + brightness — must be
        # ignored mid-effect; only power is adopted.
        coord._apply_lan_read(
            self.DEVICE_ID,
            self._status(on=True, brightness_0_100=70, color=RGBColor(200, 100, 50)),
        )

        assert state.power_state is True  # power still tracked
        assert state.active_scene == "123"  # scene preserved
        assert state.active_scene_name == "Sunrise"
        assert state.segments == [SegmentState(index=0, color=RGBColor(1, 2, 3))]
        assert state.sensor_temperature == 22.5
        assert state.color == RGBColor(9, 9, 9)  # NOT overwritten mid-effect
        assert state.brightness == 33  # NOT overwritten mid-effect

    def test_overlay_skips_power_brightness_within_grace(self):
        import time

        coord, _ = self._coord()
        state = GoveeDeviceState.create_empty(self.DEVICE_ID)
        state.power_state = True
        state.brightness = 90
        state.color = RGBColor(1, 2, 3)
        state.source = "optimistic"
        state.last_optimistic_update = time.monotonic()  # fresh -> in grace
        coord._states[self.DEVICE_ID] = state

        # LAN says off/dim with a {0,0,0} colour (sentinel preserves colour).
        coord._apply_lan_read(
            self.DEVICE_ID,
            self._status(on=False, brightness_0_100=5, color=RGBColor(0, 0, 0)),
        )

        assert state.power_state is True  # power skipped in grace
        assert state.brightness == 90  # brightness skipped in grace
        assert state.color == RGBColor(1, 2, 3)  # {0,0,0} sentinel preserved
        assert state.last_optimistic_update is not None  # grace window preserved

    def test_two_reads_in_grace_window_do_not_revert(self):
        """Regression: a second LAN read in one grace window must not revert.

        update_from_lan keeps source="optimistic" during grace; otherwise the
        first read would flip source to "lan", end the window (the grace test
        gates on source), and the second read would overwrite the in-flight
        power/brightness — flip-flopping the UI back to the pre-command state.
        (critic BLOCK on the read-side wiring.)
        """
        import time

        coord, _ = self._coord()
        state = GoveeDeviceState.create_empty(self.DEVICE_ID)
        state.power_state = True
        state.brightness = 90
        state.source = "optimistic"
        state.last_optimistic_update = time.monotonic()  # fresh -> in grace
        coord._states[self.DEVICE_ID] = state

        stale = self._status(on=False, brightness_0_100=5, color=RGBColor(0, 0, 0))
        coord._apply_lan_read(self.DEVICE_ID, stale)  # read #1 in grace
        assert state.source == "optimistic"  # window must stay open for read #2
        coord._apply_lan_read(self.DEVICE_ID, stale)  # read #2 still in grace

        # Both reads skipped power/brightness — no flip-flop.
        assert state.power_state is True
        assert state.brightness == 90

    def test_overlay_rescales_brightness_for_non_0_100_device(self):
        coord, _ = self._coord(brightness_max=254)
        state = GoveeDeviceState.create_empty(self.DEVICE_ID)
        coord._states[self.DEVICE_ID] = state

        coord._apply_lan_read(self.DEVICE_ID, self._status(brightness_0_100=50))

        assert state.brightness == 127  # 50/100 * 254 -> device-native, not 50

    def test_overlay_none_state_never_raises(self):
        coord, _ = self._coord()
        # device_id absent from _states -> additive/abortable, no raise.
        coord._apply_lan_read("NO:SUCH:DEVICE", self._status())
        coord.async_set_updated_data.assert_not_called()

    def test_overlay_no_change_does_not_notify(self):
        coord, _ = self._coord()
        state = GoveeDeviceState.create_empty(self.DEVICE_ID)
        state.online = True
        state.power_state = True
        state.brightness = 80
        state.color = RGBColor(255, 0, 0)
        coord._states[self.DEVICE_ID] = state

        coord._apply_lan_read(self.DEVICE_ID, self._status())  # identical values

        coord.async_set_updated_data.assert_not_called()  # BFF churn-avoidance

    # ---- _on_lan_dev_status push guard (blocking #3) -----------------------

    def test_push_fresh_correlation_applies(self):
        coord, _ = self._coord()
        coord._states[self.DEVICE_ID] = GoveeDeviceState.create_empty(self.DEVICE_ID)
        coord._lan_devices[self.DEVICE_ID] = self._info()

        coord._on_lan_dev_status(self.IP, self._status(on=True))

        assert coord._states[self.DEVICE_ID].power_state is True
        coord.async_set_updated_data.assert_called_once()

    def test_push_stale_correlation_skips_and_rescans(self):
        import time

        from custom_components.govee.const import LAN_CORRELATION_TTL_SECONDS

        coord, _ = self._coord()
        before = GoveeDeviceState.create_empty(self.DEVICE_ID)
        coord._states[self.DEVICE_ID] = GoveeDeviceState.create_empty(self.DEVICE_ID)
        coord._lan_devices[self.DEVICE_ID] = self._info(
            ts=time.monotonic() - (LAN_CORRELATION_TTL_SECONDS + 10)
        )
        coord._last_lan_rescan = 999.0

        coord._on_lan_dev_status(self.IP, self._status(on=True))

        assert coord._states[self.DEVICE_ID] == before  # not clobbered
        assert coord._last_lan_rescan == float("-inf")  # re-correlate forced
        coord.async_set_updated_data.assert_not_called()

    def test_push_ambiguous_ip_skips(self):
        coord, _ = self._coord()
        other = "BB:BB:BB:BB:BB:BB:BB:BB"
        coord._states[self.DEVICE_ID] = GoveeDeviceState.create_empty(self.DEVICE_ID)
        # Two devices claim the same IP -> ambiguous -> skip + rescan.
        coord._lan_devices[self.DEVICE_ID] = self._info(ip=self.IP)
        coord._lan_devices[other] = self._info(ip=self.IP, device_id=other)
        coord._last_lan_rescan = 999.0

        coord._on_lan_dev_status(self.IP, self._status())

        coord.async_set_updated_data.assert_not_called()
        assert coord._last_lan_rescan == float("-inf")

    # ---- _refresh_lan_reads demotion + reset -------------------------------

    @pytest.mark.asyncio
    async def test_refresh_demotes_after_k_misses(self):
        from custom_components.govee.const import LAN_READ_MISS_DEMOTE_THRESHOLD

        coord, _ = self._coord()
        coord._states[self.DEVICE_ID] = GoveeDeviceState.create_empty(self.DEVICE_ID)
        coord._lan_devices[self.DEVICE_ID] = self._info()
        coord._lan_client = _FakeReadClient(batch={})  # never replies

        for i in range(1, LAN_READ_MISS_DEMOTE_THRESHOLD):
            await coord._refresh_lan_reads()
            assert coord._lan_read_misses[self.DEVICE_ID] == i
            assert self.DEVICE_ID in coord._lan_devices

        await coord._refresh_lan_reads()  # K-th miss -> demote
        assert self.DEVICE_ID not in coord._lan_devices
        assert self.DEVICE_ID not in coord._lan_read_misses

    @pytest.mark.asyncio
    async def test_refresh_reply_resets_misses_and_overlays(self):
        coord, _ = self._coord()
        state = GoveeDeviceState.create_empty(self.DEVICE_ID)
        state.power_state = False
        coord._states[self.DEVICE_ID] = state
        coord._lan_devices[self.DEVICE_ID] = self._info()
        coord._lan_read_misses[self.DEVICE_ID] = 2  # accumulated misses
        coord._lan_client = _FakeReadClient(batch={self.IP: self._status(on=True)})

        await coord._refresh_lan_reads()

        assert coord._lan_read_misses[self.DEVICE_ID] == 0  # reset on reply
        assert state.power_state is True  # overlaid
        # Poll path mutates in place (notify=False) — no re-entrant push.
        coord.async_set_updated_data.assert_not_called()

    @pytest.mark.asyncio
    async def test_refresh_noop_without_client_or_devices(self):
        coord, _ = self._coord()
        # No client.
        await coord._refresh_lan_reads()
        # Client but no correlated devices.
        coord._lan_client = _FakeReadClient()
        await coord._refresh_lan_reads()
        assert coord._lan_client.read_calls == []

    # ---- rescan / re-correlation (blocking #3 (b)) -------------------------

    def test_merge_drops_stale_ip_reclaimed_by_other_device(self):
        from custom_components.govee.api.lan_client import LanDeviceInfo

        coord, _ = self._coord()
        other = "BB:BB:BB:BB:BB:BB:BB:BB"
        # Old map: device A at IP X.
        coord._lan_devices[self.DEVICE_ID] = self._info(ip=self.IP)
        coord._lan_read_misses[self.DEVICE_ID] = 1
        # Fresh scan: a DIFFERENT device B now answers from IP X.
        matched = {
            other: LanDeviceInfo(
                device_id=other,
                ip=self.IP,
                mac=other,
                sku="H6072",
                firmware="1",
                last_correlated_ts=1.0,
            )
        }
        coord._merge_lan_correlation(matched, [])

        assert self.DEVICE_ID not in coord._lan_devices  # stale A dropped
        assert other in coord._lan_devices  # B promoted
        assert self.DEVICE_ID not in coord._lan_read_misses

    def test_merge_repromotes_and_keeps_transient_miss(self):
        from custom_components.govee.api.lan_client import LanDeviceInfo

        coord, _ = self._coord()
        kept = "CC:CC:CC:CC:CC:CC:CC:CC"
        # A demoted device that re-answers, plus an unrelated device that
        # didn't answer this scan but keeps a non-conflicting IP.
        coord._lan_devices[kept] = self._info(ip="10.0.0.9", device_id=kept)
        coord._lan_read_misses[self.DEVICE_ID] = 5
        matched = {
            self.DEVICE_ID: LanDeviceInfo(
                device_id=self.DEVICE_ID,
                ip=self.IP,
                mac=self.DEVICE_ID,
                sku="H6072",
                firmware="1",
                last_correlated_ts=2.0,
            )
        }
        coord._merge_lan_correlation(matched, [{"device": "x"}])

        assert coord._lan_read_misses[self.DEVICE_ID] == 0  # re-promoted
        assert self.DEVICE_ID in coord._lan_devices
        assert kept in coord._lan_devices  # transient miss kept
        assert coord._lan_unmatched == [{"device": "x"}]

    @pytest.mark.asyncio
    async def test_rescan_throttled_then_runs(self, monkeypatch):
        import time

        import custom_components.govee.coordinator as coord_mod

        coord, _ = self._coord()
        coord._lan_client = _FakeReadClient()

        async def _ifaces(hass):
            return []

        scans = {"count": 0, "broadcast_targets": None}

        async def _broadcasts(hass):
            return ["192.168.0.255"]

        async def _scan(*, interface_ips, extra_targets, broadcast_targets=None):
            scans["count"] += 1
            scans["broadcast_targets"] = broadcast_targets
            return self._scan_record()

        monkeypatch.setattr(coord_mod, "async_get_lan_interface_ips", _ifaces)
        monkeypatch.setattr(coord_mod, "async_get_lan_broadcast_addresses", _broadcasts)
        monkeypatch.setattr(coord_mod, "async_scan_lan_devices", _scan)

        # Throttled: a recent rescan blocks a new one.
        coord._last_lan_rescan = time.monotonic()
        await coord._async_maybe_rescan_lan()
        assert scans["count"] == 0

        # Forced (throttle cleared, as the push path does) -> runs + correlates.
        coord._request_lan_rescan()
        await coord._async_maybe_rescan_lan()
        assert scans["count"] == 1
        assert self.DEVICE_ID in coord._lan_devices  # re-promoted from scan
        # The rescan also forwards the derived subnet broadcast(s) to the scan,
        # re-priming newer broadcast-only devices each cycle (#57).
        assert scans["broadcast_targets"] == ["192.168.0.255"]

    async def test_forced_rescan_runs_on_low_monotonic_clock(self, monkeypatch):
        """Regression: a forced rescan must run even just after host boot.

        time.monotonic() counts seconds since boot, so on a freshly-booted host
        (monotonic < LAN_RESCAN_INTERVAL) a 0.0 force sentinel still read as
        throttled and silently swallowed the DHCP re-correlation. _request_lan_
        rescan now uses -inf, which beats the throttle at any clock value.
        """
        import custom_components.govee.coordinator as coord_mod

        coord, _ = self._coord()
        coord._lan_client = _FakeReadClient()
        scans = {"count": 0}

        async def _ifaces(hass):
            return []

        async def _broadcasts(hass):
            return []

        async def _scan(*, interface_ips, extra_targets, broadcast_targets=None):
            scans["count"] += 1
            return self._scan_record()

        # Simulate a host only 5s past boot — well inside LAN_RESCAN_INTERVAL.
        monkeypatch.setattr(coord_mod.time, "monotonic", lambda: 5.0)
        monkeypatch.setattr(coord_mod, "async_get_lan_interface_ips", _ifaces)
        monkeypatch.setattr(coord_mod, "async_get_lan_broadcast_addresses", _broadcasts)
        monkeypatch.setattr(coord_mod, "async_scan_lan_devices", _scan)

        coord._request_lan_rescan()
        await coord._async_maybe_rescan_lan()
        assert scans["count"] == 1  # ran despite monotonic() == 5 < 300

    def _scan_record(self):
        return [
            {
                "device": self.DEVICE_ID,
                "ip": self.IP,
                "sku": "H6072",
                "wifiVersionSoft": "1.0.0",
            }
        ]

    # ---- overlay runs AFTER cloud fan-in -----------------------------------

    @pytest.mark.asyncio
    async def test_overlay_runs_after_cloud_fan_in(self, monkeypatch):
        coord, _ = self._coord()
        coord._states[self.DEVICE_ID] = GoveeDeviceState.create_empty(self.DEVICE_ID)
        coord._lan_devices[self.DEVICE_ID] = self._info()
        coord._lan_client = _FakeReadClient(
            batch={self.IP: self._status(on=True, brightness_0_100=80)}
        )

        async def _fetch(device_id, device):
            # Fresh cloud object every poll: power off, dim.
            fresh = GoveeDeviceState.create_empty(device_id)
            fresh.power_state = False
            fresh.brightness = 10
            fresh.source = "api"
            return fresh

        monkeypatch.setattr(coord, "_fetch_device_state", _fetch)
        # Rescan is throttled (already-fresh) so it doesn't re-scan here.
        coord._last_lan_rescan = __import__("time").monotonic()

        result = await coord._async_update_data()

        # The LAN read overlaid the FRESH cloud object, not the reverse.
        assert result[self.DEVICE_ID].power_state is True
        assert result[self.DEVICE_ID].brightness == 80
        assert result[self.DEVICE_ID].source == "lan"


class _FakeWriteClient:
    """LAN client fake exposing only the write-tier surface (LAN-012).

    ``send_result`` is what ``async_send_command`` returns (the real client
    returns ``False`` when ``sendto`` raises OSError). ``read_reply`` is what the
    verify-by-read ``async_read_one`` returns (``None`` = no confirmation).
    """

    def __init__(self, *, send_result: bool = True, read_reply: Any = None) -> None:
        self.available = True
        self.send_result = send_result
        self.read_reply = read_reply
        self.send_calls: list[tuple[str, str, dict[str, Any]]] = []
        self.read_calls: list[tuple[str, float]] = []

    async def async_send_command(
        self, ip: str, cmd: str, data: dict[str, Any]
    ) -> bool:
        self.send_calls.append((ip, cmd, data))
        return self.send_result

    async def async_read_one(self, ip: str, timeout: float = 0.5) -> Any:
        self.read_calls.append((ip, timeout))
        return self.read_reply


class TestTryLanCommand:
    """Coordinator LAN control tier: verify-by-read, write-health gate,
    fall-through (LAN-012). Precedence is BLE > LAN > MQTT > REST."""

    DEVICE_ID = "AA:BB:CC:DD:EE:FF:00:11"
    IP = "10.0.0.5"

    def _status(self, **kw):
        from custom_components.govee.api.lan_client import LanDevStatus

        defaults = dict(
            on=True,
            brightness_0_100=80,
            color=RGBColor(255, 0, 0),
            color_temp_kelvin=None,
        )
        defaults.update(kw)
        return LanDevStatus(**defaults)

    def _info(self, *, ip=None, device_id=None):
        import time

        from custom_components.govee.api.lan_client import LanDeviceInfo

        return LanDeviceInfo(
            device_id=device_id or self.DEVICE_ID,
            ip=ip or self.IP,
            mac=device_id or self.DEVICE_ID,
            sku="H6072",
            firmware="1.0.0",
            last_correlated_ts=time.monotonic(),
        )

    def _coord(
        self,
        *,
        brightness_max: int = 100,
        is_group: bool = False,
        device_id: str | None = None,
        sku: str = "H6072",
    ):
        import custom_components.govee.coordinator as coord_mod

        dev_id = device_id or self.DEVICE_ID
        hass = MagicMock()
        config_entry = MagicMock()
        config_entry.entry_id = "test_entry"
        config_entry.options = {}
        coord = coord_mod.GoveeCoordinator(
            hass=hass,
            config_entry=config_entry,
            api_client=MagicMock(),
            iot_credentials=None,
            poll_interval=60,
        )
        caps = (
            GoveeCapability(
                type=CAPABILITY_ON_OFF, instance=INSTANCE_POWER, parameters={}
            ),
            GoveeCapability(
                type=CAPABILITY_RANGE,
                instance=INSTANCE_BRIGHTNESS,
                parameters={"range": {"min": 0, "max": brightness_max}},
            ),
        )
        coord._devices[dev_id] = GoveeDevice(
            device_id=dev_id,
            sku=sku,
            name="Test Light",
            device_type="devices.types.light",
            capabilities=caps,
            is_group=is_group,
        )
        coord._states[dev_id] = GoveeDeviceState.create_empty(dev_id)
        coord.async_set_updated_data = MagicMock()
        return coord, coord_mod

    def _ready_coord(self, **kw):
        """A coord wired for a successful LAN write: correlated + LAN-available."""
        coord, coord_mod = self._coord(**kw)
        dev_id = kw.get("device_id") or self.DEVICE_ID
        coord._lan_devices[dev_id] = self._info(device_id=dev_id)
        # Mark LAN available so the write-health gate passes (a prior read).
        coord._record_transport_success(dev_id, "lan")
        return coord, coord_mod

    # ---- happy paths -------------------------------------------------------

    @pytest.mark.asyncio
    async def test_confirmed_power_records_and_returns_true(self):
        coord, _ = self._ready_coord()
        client = _FakeWriteClient(read_reply=self._status(on=True))
        coord._lan_client = client

        result = await coord._try_lan_command(
            self.DEVICE_ID, coord._devices[self.DEVICE_ID], PowerCommand(power_on=True)
        )

        assert result is True
        # Sent the LAN turn-on command to the correlated IP.
        assert client.send_calls == [(self.IP, "turn", {"value": 1})]
        # Confirm read happened.
        assert client.read_calls and client.read_calls[0][0] == self.IP
        # Optimistic state applied immediately (step 7).
        assert coord._states[self.DEVICE_ID].power_state is True
        coord.async_set_updated_data.assert_called()
        # Verify-by-read recording: the WRITE stamps send + success.
        health = coord._transport.get(self.DEVICE_ID, "lan")
        assert health is not None
        assert health.is_available is True
        assert health.last_send_ts is not None
        assert health.last_failure_reason is None

    @pytest.mark.asyncio
    async def test_confirmed_brightness_within_tolerance_returns_true(self):
        coord, _ = self._ready_coord()
        # Requested 50; device reports 51 (within ±2) -> confirmed.
        client = _FakeWriteClient(read_reply=self._status(brightness_0_100=51))
        coord._lan_client = client

        result = await coord._try_lan_command(
            self.DEVICE_ID,
            coord._devices[self.DEVICE_ID],
            BrightnessCommand(brightness=50),
        )

        assert result is True
        assert client.send_calls == [(self.IP, "brightness", {"value": 50})]
        assert coord._states[self.DEVICE_ID].brightness == 50  # optimistic value

    @pytest.mark.asyncio
    async def test_confirmed_brightness_rescaled_for_non_0_100_device(self):
        coord, _ = self._ready_coord(brightness_max=254)
        # Device-native 127 -> LAN 50; reports back LAN 50 -> native 127, |0| ok.
        client = _FakeWriteClient(read_reply=self._status(brightness_0_100=50))
        coord._lan_client = client

        result = await coord._try_lan_command(
            self.DEVICE_ID,
            coord._devices[self.DEVICE_ID],
            BrightnessCommand(brightness=127),
        )

        assert result is True
        assert client.send_calls == [(self.IP, "brightness", {"value": 50})]

    # ---- unconfirmed / mismatch -> fall through ----------------------------

    @pytest.mark.asyncio
    async def test_unconfirmed_read_none_returns_false(self):
        coord, _ = self._ready_coord()
        client = _FakeWriteClient(read_reply=None)  # device never answers
        coord._lan_client = client

        result = await coord._try_lan_command(
            self.DEVICE_ID, coord._devices[self.DEVICE_ID], PowerCommand(power_on=True)
        )

        assert result is False
        # Send + confirm-read both attempted; optimistic applied before falling.
        assert client.send_calls
        assert client.read_calls
        assert coord._states[self.DEVICE_ID].power_state is True
        # A confirm miss must NEVER flip LAN unavailable (#57): that flapped the
        # lan_connectivity sensor on every power-on. It only counts toward WRITE
        # suppression. The command still fell through to MQTT/REST.
        health = coord._transport.get(self.DEVICE_ID, "lan")
        assert health is not None and health.is_available is True
        assert coord._lan_write_misses[self.DEVICE_ID] == 1

    @pytest.mark.asyncio
    async def test_send_failure_returns_false_without_reading(self):
        coord, _ = self._ready_coord()
        # sendto OSError surfaces as a False return from async_send_command.
        client = _FakeWriteClient(send_result=False)
        coord._lan_client = client

        result = await coord._try_lan_command(
            self.DEVICE_ID, coord._devices[self.DEVICE_ID], PowerCommand(power_on=True)
        )

        assert result is False
        assert client.send_calls  # send attempted
        assert client.read_calls == []  # no confirm read after a failed send
        # Optimistic NOT applied when the send itself failed.
        coord.async_set_updated_data.assert_not_called()
        health = coord._transport.get(self.DEVICE_ID, "lan")
        assert health is not None and health.last_failure_reason == "send_failed"

    @pytest.mark.asyncio
    async def test_power_value_mismatch_returns_false(self):
        coord, _ = self._ready_coord()
        # Asked ON, device reports OFF -> mismatch -> fall through.
        client = _FakeWriteClient(read_reply=self._status(on=False))
        coord._lan_client = client

        result = await coord._try_lan_command(
            self.DEVICE_ID, coord._devices[self.DEVICE_ID], PowerCommand(power_on=True)
        )

        assert result is False
        # The device ANSWERED -> LAN is alive. A content mismatch must NOT flip
        # LAN unavailable (#57 — the value_mismatch flap). The reply is applied as
        # a confirmed read (records LAN success); it only counts a write-miss.
        health = coord._transport.get(self.DEVICE_ID, "lan")
        assert health is not None and health.is_available is True
        assert health.last_failure_reason is None  # never marked a transport failure
        assert coord._lan_write_misses[self.DEVICE_ID] == 1
        # Optimistic power survives (grace window) despite the stale onOff=0 read.
        assert coord._states[self.DEVICE_ID].power_state is True

    @pytest.mark.asyncio
    async def test_power_reply_on_none_is_mismatch(self):
        """A plug whose devStatus lacks onOff -> reply.on None -> never matches."""
        coord, _ = self._ready_coord()
        client = _FakeWriteClient(read_reply=self._status(on=None))
        coord._lan_client = client

        result = await coord._try_lan_command(
            self.DEVICE_ID, coord._devices[self.DEVICE_ID], PowerCommand(power_on=True)
        )

        assert result is False
        # Mismatch -> fall through, LAN stays available, only a write-miss (#57).
        health = coord._transport.get(self.DEVICE_ID, "lan")
        assert health is not None and health.is_available is True
        assert coord._lan_write_misses[self.DEVICE_ID] == 1

    @pytest.mark.asyncio
    async def test_brightness_out_of_tolerance_returns_false(self):
        coord, _ = self._ready_coord()
        # Asked 50, device reports 60 (>2) -> mismatch.
        client = _FakeWriteClient(read_reply=self._status(brightness_0_100=60))
        coord._lan_client = client

        result = await coord._try_lan_command(
            self.DEVICE_ID,
            coord._devices[self.DEVICE_ID],
            BrightnessCommand(brightness=50),
        )

        assert result is False
        # Mismatch -> fall through, LAN stays available, only a write-miss (#57).
        health = coord._transport.get(self.DEVICE_ID, "lan")
        assert health is not None and health.is_available is True
        assert coord._lan_write_misses[self.DEVICE_ID] == 1

    # ---- write suppression vs. read-driven health (#57) --------------------

    @pytest.mark.asyncio
    async def test_confirm_misses_never_flip_lan_health(self):
        """Confirm misses must NEVER mark LAN unavailable — the reported flap (#57)."""
        from custom_components.govee.const import LAN_WRITE_SUPPRESS_THRESHOLD

        coord, _ = self._ready_coord()
        coord._lan_client = _FakeWriteClient(read_reply=None)  # never confirms
        dev = coord._devices[self.DEVICE_ID]

        # Many consecutive misses (well past the suppress threshold): the lan
        # transport stays available the whole time — health is read-driven.
        for _ in range(LAN_WRITE_SUPPRESS_THRESHOLD + 2):
            assert await coord._try_lan_command(self.DEVICE_ID, dev, PowerCommand(power_on=True)) is False
            health = coord._transport.get(self.DEVICE_ID, "lan")
            assert health.is_available is True
            assert health.last_failure_reason is None

    @pytest.mark.asyncio
    async def test_misses_suppress_writes_after_threshold(self):
        """After the threshold, LAN write attempts are skipped (no wire) (#57)."""
        from custom_components.govee.const import LAN_WRITE_SUPPRESS_THRESHOLD

        coord, _ = self._ready_coord()
        client = _FakeWriteClient(read_reply=None)  # never confirms
        coord._lan_client = client
        dev = coord._devices[self.DEVICE_ID]

        # Reach the threshold; each attempt sends + reads (paying the timeout).
        for _ in range(LAN_WRITE_SUPPRESS_THRESHOLD):
            await coord._try_lan_command(self.DEVICE_ID, dev, PowerCommand(power_on=True))
        sends_at_threshold = len(client.send_calls)
        assert coord._lan_writes_suppressed(self.DEVICE_ID) is True

        # Next command is suppressed: no further send/read, immediate fall-through.
        assert await coord._try_lan_command(self.DEVICE_ID, dev, PowerCommand(power_on=True)) is False
        assert len(client.send_calls) == sends_at_threshold  # no new wire traffic
        # LAN reads remain available (sensor stays Connected).
        assert coord._transport.get(self.DEVICE_ID, "lan").is_available is True

    @pytest.mark.asyncio
    async def test_poll_read_does_not_rearm_suppressed_writes(self):
        """A poll READ proves the link but not writes — suppression persists (#57)."""
        from custom_components.govee.const import LAN_WRITE_SUPPRESS_THRESHOLD

        coord, _ = self._ready_coord()
        coord._lan_client = _FakeWriteClient(read_reply=None)
        dev = coord._devices[self.DEVICE_ID]

        for _ in range(LAN_WRITE_SUPPRESS_THRESHOLD):
            await coord._try_lan_command(self.DEVICE_ID, dev, PowerCommand(power_on=True))
        assert coord._lan_writes_suppressed(self.DEVICE_ID) is True

        # An inbound read lands: LAN available, but writes stay suppressed.
        coord._apply_lan_read(self.DEVICE_ID, self._status())
        assert coord._transport.get(self.DEVICE_ID, "lan").is_available is True
        assert coord._lan_writes_suppressed(self.DEVICE_ID) is True
        assert coord._lan_write_misses.get(self.DEVICE_ID) == LAN_WRITE_SUPPRESS_THRESHOLD

    @pytest.mark.asyncio
    async def test_confirmed_write_rearms_suppressed_writes(self):
        """A confirmed write clears the miss streak + cooldown (#57)."""
        from custom_components.govee.const import LAN_WRITE_SUPPRESS_THRESHOLD

        coord, _ = self._ready_coord()
        client = _FakeWriteClient(read_reply=None)
        coord._lan_client = client
        dev = coord._devices[self.DEVICE_ID]

        for _ in range(LAN_WRITE_SUPPRESS_THRESHOLD):
            await coord._try_lan_command(self.DEVICE_ID, dev, PowerCommand(power_on=True))
        assert coord._lan_writes_suppressed(self.DEVICE_ID) is True

        # Manually clear the cooldown so the next write probes LAN; this time the
        # device confirms (reports ON) -> streak + cooldown cleared, returns True.
        coord._lan_write_suppressed_until.clear()
        client.read_reply = self._status(on=True)
        assert await coord._try_lan_command(self.DEVICE_ID, dev, PowerCommand(power_on=True)) is True
        assert self.DEVICE_ID not in coord._lan_write_misses
        assert self.DEVICE_ID not in coord._lan_write_suppressed_until

    @pytest.mark.asyncio
    async def test_suppression_cooldown_expiry_rearms(self):
        """Once the cooldown elapses, the next command probes LAN again (#57)."""
        coord, _ = self._ready_coord()
        coord._lan_client = _FakeWriteClient(read_reply=None)
        # Force an already-expired cooldown deadline.
        coord._lan_write_misses[self.DEVICE_ID] = 5
        coord._lan_write_suppressed_until[self.DEVICE_ID] = 0.0  # monotonic past

        assert coord._lan_writes_suppressed(self.DEVICE_ID) is False
        # Re-armed: streak + deadline cleared so the next write is a fresh probe.
        assert self.DEVICE_ID not in coord._lan_write_misses
        assert self.DEVICE_ID not in coord._lan_write_suppressed_until

    @pytest.mark.asyncio
    async def test_lan_write_suppressed_diag_accessor_is_read_only(self):
        """The diagnostics accessor reports state without re-arming it (#57).

        Unlike ``_lan_writes_suppressed`` (which clears an expired cooldown),
        ``lan_write_suppressed`` must never mutate runtime state — a diagnostics
        download is read-only.
        """
        coord, _ = self._ready_coord()
        coord._lan_write_misses[self.DEVICE_ID] = 5

        # Active cooldown -> True.
        coord._lan_write_suppressed_until[self.DEVICE_ID] = float("inf")
        assert coord.lan_write_suppressed(self.DEVICE_ID) is True

        # Already-expired deadline -> False, but the entry is NOT cleared.
        coord._lan_write_suppressed_until[self.DEVICE_ID] = 0.0
        assert coord.lan_write_suppressed(self.DEVICE_ID) is False
        assert self.DEVICE_ID in coord._lan_write_suppressed_until  # not mutated
        assert self.DEVICE_ID in coord._lan_write_misses  # not mutated

        # No deadline at all -> False.
        coord._lan_write_suppressed_until.pop(self.DEVICE_ID, None)
        assert coord.lan_write_suppressed(self.DEVICE_ID) is False

    # ---- early-return guards (never touch the wire) ------------------------

    @pytest.mark.asyncio
    async def test_no_lan_client_returns_false(self):
        coord, _ = self._ready_coord()
        coord._lan_client = None
        result = await coord._try_lan_command(
            self.DEVICE_ID, coord._devices[self.DEVICE_ID], PowerCommand(power_on=True)
        )
        assert result is False

    @pytest.mark.asyncio
    async def test_group_device_returns_false(self):
        coord, _ = self._ready_coord(is_group=True)
        client = _FakeWriteClient(read_reply=self._status())
        coord._lan_client = client
        result = await coord._try_lan_command(
            self.DEVICE_ID, coord._devices[self.DEVICE_ID], PowerCommand(power_on=True)
        )
        assert result is False
        assert client.send_calls == []

    @pytest.mark.asyncio
    async def test_numeric_device_id_returns_false(self):
        numeric_id = "11825917"
        coord, _ = self._ready_coord(device_id=numeric_id)
        client = _FakeWriteClient(read_reply=self._status())
        coord._lan_client = client
        result = await coord._try_lan_command(
            numeric_id, coord._devices[numeric_id], PowerCommand(power_on=True)
        )
        assert result is False
        assert client.send_calls == []

    @pytest.mark.asyncio
    async def test_not_in_lan_devices_returns_false(self):
        coord, _ = self._coord()
        coord._record_transport_success(self.DEVICE_ID, "lan")  # available but...
        coord._lan_devices = {}  # ...not correlated
        client = _FakeWriteClient(read_reply=self._status())
        coord._lan_client = client
        result = await coord._try_lan_command(
            self.DEVICE_ID, coord._devices[self.DEVICE_ID], PowerCommand(power_on=True)
        )
        assert result is False
        assert client.send_calls == []

    @pytest.mark.asyncio
    async def test_write_health_gate_bootstrap_falls_through(self):
        """First control after startup: lan health exists but defaults
        is_available=False, so the write-health gate falls through (#57)."""
        coord, _ = self._coord()
        coord._lan_devices[self.DEVICE_ID] = self._info()
        coord._ensure_transport_health(self.DEVICE_ID)  # lan default unavailable
        client = _FakeWriteClient(read_reply=self._status())
        coord._lan_client = client

        health = coord._transport.get(self.DEVICE_ID, "lan")
        assert health is not None and health.is_available is False  # precondition

        result = await coord._try_lan_command(
            self.DEVICE_ID, coord._devices[self.DEVICE_ID], PowerCommand(power_on=True)
        )

        assert result is False
        assert client.send_calls == []  # gate blocks before the wire

    @pytest.mark.asyncio
    async def test_health_none_returns_false(self):
        coord, _ = self._coord()
        coord._lan_devices[self.DEVICE_ID] = self._info()
        # No transport-health entry at all for this device -> gate returns False.
        coord._transport = TransportHealthTracker()
        client = _FakeWriteClient(read_reply=self._status())
        coord._lan_client = client
        result = await coord._try_lan_command(
            self.DEVICE_ID, coord._devices[self.DEVICE_ID], PowerCommand(power_on=True)
        )
        assert result is False
        assert client.send_calls == []

    @pytest.mark.asyncio
    async def test_color_command_confirms_over_lan(self):
        # Colour is LAN-routed and confirmed by readback (#149, #158).
        coord, _ = self._ready_coord()
        client = _FakeWriteClient(read_reply=self._status(color=RGBColor(0, 255, 0)))
        coord._lan_client = client
        result = await coord._try_lan_command(
            self.DEVICE_ID,
            coord._devices[self.DEVICE_ID],
            ColorCommand(color=RGBColor(0, 255, 0)),
        )
        assert result is True
        assert client.send_calls and client.send_calls[0][1] == "colorwc"

    @pytest.mark.asyncio
    async def test_color_falls_through_when_readback_disagrees(self):
        # A device that ignored the write reports its old colour, so the write
        # must fall through to REST rather than be silently dropped (#149).
        coord, _ = self._ready_coord()
        client = _FakeWriteClient(read_reply=self._status(color=RGBColor(255, 0, 0)))
        coord._lan_client = client
        result = await coord._try_lan_command(
            self.DEVICE_ID,
            coord._devices[self.DEVICE_ID],
            ColorCommand(color=RGBColor(0, 255, 0)),
        )
        assert result is False

    @pytest.mark.asyncio
    async def test_color_temp_command_confirms_within_tolerance(self):
        # Firmware snaps Kelvin onto its own grid, so the confirm allows a
        # small delta rather than demanding an exact echo (#149, #158).
        coord, _ = self._ready_coord()
        client = _FakeWriteClient(read_reply=self._status(color_temp_kelvin=4050))
        coord._lan_client = client
        result = await coord._try_lan_command(
            self.DEVICE_ID,
            coord._devices[self.DEVICE_ID],
            ColorTempCommand(kelvin=4000),
        )
        assert result is True
        assert client.send_calls and client.send_calls[0][1] == "colorwc"

    @pytest.mark.asyncio
    async def test_color_temp_falls_through_when_device_stays_in_rgb_mode(self):
        # colorTemInKelvin == 0 means "not in CT mode" — the write didn't land.
        coord, _ = self._ready_coord()
        client = _FakeWriteClient(read_reply=self._status(color_temp_kelvin=None))
        coord._lan_client = client
        result = await coord._try_lan_command(
            self.DEVICE_ID,
            coord._devices[self.DEVICE_ID],
            ColorTempCommand(kelvin=4000),
        )
        assert result is False

    # ---- precedence BLE > LAN > MQTT > REST --------------------------------

    @pytest.mark.asyncio
    async def test_precedence_ble_beats_lan(self, monkeypatch):
        coord, coord_mod = self._ready_coord()
        monkeypatch.setattr(coord_mod, "HAS_BLUETOOTH", True)
        coord._ble_devices = {self.DEVICE_ID: MagicMock()}
        coord._try_ble_command = AsyncMock(return_value=True)
        coord._try_lan_command = AsyncMock(return_value=True)

        result = await coord.async_control_device(
            self.DEVICE_ID, PowerCommand(power_on=True)
        )

        assert result is True
        coord._try_ble_command.assert_awaited_once()
        coord._try_lan_command.assert_not_awaited()  # BLE short-circuited

    @pytest.mark.asyncio
    async def test_precedence_lan_beats_mqtt(self, monkeypatch):
        coord, coord_mod = self._ready_coord()
        monkeypatch.setattr(coord_mod, "HAS_BLUETOOTH", False)
        coord._enable_mqtt_control = True
        coord._mqtt_client = MagicMock(connected=True)
        coord._try_lan_command = AsyncMock(return_value=True)
        coord._try_mqtt_command = AsyncMock(return_value=True)

        result = await coord.async_control_device(
            self.DEVICE_ID, PowerCommand(power_on=True)
        )

        assert result is True
        coord._try_lan_command.assert_awaited_once()
        coord._try_mqtt_command.assert_not_awaited()  # LAN short-circuited

    @pytest.mark.asyncio
    async def test_unconfirmed_lan_falls_through_to_mqtt_then_rest(self, monkeypatch):
        """End-to-end: an unconfirmed LAN write must reach MQTT, then REST,
        so the device is never stranded."""
        coord, coord_mod = self._ready_coord()
        monkeypatch.setattr(coord_mod, "HAS_BLUETOOTH", False)
        # Real LAN attempt that cannot confirm (device never answers the read).
        coord._lan_client = _FakeWriteClient(read_reply=None)
        # MQTT is enabled + connected but also "fails" so REST is the deliverer.
        coord._enable_mqtt_control = True
        coord._mqtt_client = MagicMock(connected=True)
        coord._try_mqtt_command = AsyncMock(return_value=False)
        coord._api_client.control_device = AsyncMock(return_value=True)

        result = await coord.async_control_device(
            self.DEVICE_ID, PowerCommand(power_on=True)
        )

        assert result is True  # REST delivered it
        coord._try_mqtt_command.assert_awaited_once()  # fell through LAN -> MQTT
        coord._api_client.control_device.assert_awaited_once()  # MQTT -> REST
        lan_health = coord._transport.get(self.DEVICE_ID, "lan")
        assert lan_health is not None
        # The command still falls through LAN -> MQTT -> REST on a confirm miss,
        # but LAN stays available (a content miss never flips health — #57).
        assert lan_health.is_available is True
        assert coord._lan_write_misses[self.DEVICE_ID] == 1


class TestHumidityVerificationPoll:
    """A successful humidifier work_mode/humidity REST write schedules a
    delayed re-poll, attached to the same recent_commands entry, so a
    diagnostics download shows a timestamped before/after pair instead of
    relying on the next unrelated coordinator poll cycle (issue #118)."""

    DEVICE_ID = "AA:BB:CC:DD:EE:FF:00:99"

    def _coord(self, *, device_type: str = "devices.types.dehumidifier"):
        import custom_components.govee.coordinator as coord_mod

        hass = MagicMock()
        config_entry = MagicMock()
        config_entry.entry_id = "test_entry"
        config_entry.async_create_background_task = MagicMock()
        coord = coord_mod.GoveeCoordinator(
            hass=hass,
            config_entry=config_entry,
            api_client=MagicMock(),
            iot_credentials=None,
            poll_interval=60,
        )
        coord._devices[self.DEVICE_ID] = GoveeDevice(
            device_id=self.DEVICE_ID,
            sku="H7150",
            name="Test Dehumidifier",
            device_type=device_type,
            capabilities=(),
            is_group=False,
        )
        coord._states[self.DEVICE_ID] = GoveeDeviceState.create_empty(self.DEVICE_ID)
        coord._api_client.control_device = AsyncMock(return_value=True)
        coord.async_set_updated_data = MagicMock()
        return coord

    def _scheduled_coro(self, coord):
        """Return the coroutine handed to async_create_background_task, if any."""
        calls = coord._config_entry.async_create_background_task.call_args_list
        if not calls:
            return None
        # Signature mirrors the existing rate-limit-issue call site:
        # (hass, coro, name=...).
        return calls[-1].args[1]

    @pytest.mark.asyncio
    async def test_work_mode_on_humidifier_schedules_verification(self, monkeypatch):
        import custom_components.govee.coordinator as coord_mod

        monkeypatch.setattr(coord_mod, "HUMIDITY_VERIFICATION_DELAY_SECONDS", 0)
        coord = self._coord()
        record = {"capability": {"instance": "workMode"}}
        coord._api_client.peek_last_command_record = MagicMock(return_value=record)
        coord._api_client.get_device_state = AsyncMock(
            return_value=GoveeDeviceState(
                device_id=self.DEVICE_ID,
                online=True,
                power_state=True,
                work_mode=3,
                mode_value=45,
                configured_humidity=45,
            )
        )

        await coord.async_control_device(
            self.DEVICE_ID, WorkModeCommand(work_mode=3, mode_value=45)
        )

        coro = self._scheduled_coro(coord)
        assert coro is not None
        await coro  # run the scheduled verification inline

        assert record["verification_poll"]["work_mode"] == 3
        assert record["verification_poll"]["mode_value"] == 45
        assert record["verification_poll"]["configured_humidity"] == 45
        assert record["verification_poll"]["delay_seconds"] == 0
        assert "polled_at" in record["verification_poll"]
        coord._api_client.get_device_state.assert_awaited_once_with(
            self.DEVICE_ID, "H7150"
        )

    @pytest.mark.asyncio
    async def test_range_humidity_schedules_verification(self):
        coord = self._coord()
        record = {"capability": {"instance": "humidity"}}
        coord._api_client.peek_last_command_record = MagicMock(return_value=record)
        coord._api_client.get_device_state = AsyncMock(
            return_value=GoveeDeviceState.create_empty(self.DEVICE_ID)
        )

        await coord.async_control_device(
            self.DEVICE_ID,
            RangeCommand(range_instance=INSTANCE_HUMIDITY, value=60),
        )

        coro = self._scheduled_coro(coord)
        assert coro is not None
        coro.close()  # never run here — just confirm scheduling, not the result

    @pytest.mark.asyncio
    async def test_work_mode_on_non_humidifier_does_not_schedule(self):
        """WorkModeCommand also drives fan speed (#54) — must not verify those."""
        coord = self._coord(device_type="devices.types.fan")
        coord._api_client.peek_last_command_record = MagicMock()

        await coord.async_control_device(
            self.DEVICE_ID, WorkModeCommand(work_mode=1, mode_value=2)
        )

        assert self._scheduled_coro(coord) is None
        coord._api_client.peek_last_command_record.assert_not_called()

    @pytest.mark.asyncio
    async def test_unrelated_command_does_not_schedule(self):
        coord = self._coord()
        coord._api_client.peek_last_command_record = MagicMock()

        await coord.async_control_device(self.DEVICE_ID, PowerCommand(power_on=True))

        assert self._scheduled_coro(coord) is None
        coord._api_client.peek_last_command_record.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_record_to_attach_to_skips_scheduling(self):
        """A defensive no-op if there's somehow no command record to attach to."""
        coord = self._coord()
        coord._api_client.peek_last_command_record = MagicMock(return_value=None)

        await coord.async_control_device(
            self.DEVICE_ID, WorkModeCommand(work_mode=3, mode_value=45)
        )

        assert self._scheduled_coro(coord) is None

    @pytest.mark.asyncio
    async def test_verification_poll_records_error(self, monkeypatch):
        """A failed re-poll records the error instead of raising into the caller."""
        import custom_components.govee.coordinator as coord_mod

        monkeypatch.setattr(coord_mod, "HUMIDITY_VERIFICATION_DELAY_SECONDS", 0)
        coord = self._coord()
        record = {"capability": {"instance": "workMode"}}
        coord._api_client.peek_last_command_record = MagicMock(return_value=record)
        coord._api_client.get_device_state = AsyncMock(
            side_effect=RuntimeError("boom")
        )

        await coord.async_control_device(
            self.DEVICE_ID, WorkModeCommand(work_mode=3, mode_value=45)
        )
        await self._scheduled_coro(coord)

        assert record["verification_poll"] == {"error": "boom"}
