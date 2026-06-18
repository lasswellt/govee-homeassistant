"""Test Govee multi-zone light switches (H60B2 light{N}Toggle, issue #104)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.govee.models import (
    GoveeCapability,
    GoveeDevice,
    ToggleCommand,
)
from custom_components.govee.models.device import (
    CAPABILITY_COLOR_SETTING,
    CAPABILITY_ON_OFF,
    CAPABILITY_SEGMENT_COLOR,
    CAPABILITY_TOGGLE,
    DEVICE_TYPE_LIGHT,
    INSTANCE_COLOR_RGB,
    INSTANCE_DREAMVIEW,
    INSTANCE_POWER,
    INSTANCE_SEGMENT_COLOR,
)


def _h60b2_device() -> GoveeDevice:
    """Build an H60B2-shaped 3-zone lamp (matches issue-#104 diagnostics).

    Has RGBIC color segments (segmentedColorRgb) AND three independently
    switchable light zones (light{1,2,3}Toggle) plus a dreamViewToggle.
    """

    def toggle(instance: str) -> GoveeCapability:
        return GoveeCapability(type=CAPABILITY_TOGGLE, instance=instance, parameters={})

    return GoveeDevice(
        device_id="AA:BB:CC:DD:EE:FF:60:B2",
        sku="H60B2",
        name="3-Segment Lamp",
        device_type=DEVICE_TYPE_LIGHT,
        capabilities=(
            GoveeCapability(
                type=CAPABILITY_ON_OFF, instance=INSTANCE_POWER, parameters={}
            ),
            GoveeCapability(
                type=CAPABILITY_COLOR_SETTING,
                instance=INSTANCE_COLOR_RGB,
                parameters={},
            ),
            GoveeCapability(
                type=CAPABILITY_SEGMENT_COLOR,
                instance=INSTANCE_SEGMENT_COLOR,
                parameters={
                    "fields": [
                        {"fieldName": "segment", "elementRange": {"min": 0, "max": 2}}
                    ]
                },
            ),
            # Order intentionally shuffled to prove sorting by zone number.
            toggle("light2Toggle"),
            toggle("light3Toggle"),
            toggle("light1Toggle"),
            toggle(INSTANCE_DREAMVIEW),
        ),
        is_group=False,
    )


# ==============================================================================
# light_toggle_instances detection
# ==============================================================================


class TestLightToggleInstances:
    """GoveeDevice.light_toggle_instances regex detection."""

    def test_returns_sorted_zone_toggles(self):
        device = _h60b2_device()
        assert device.light_toggle_instances == [
            "light1Toggle",
            "light2Toggle",
            "light3Toggle",
        ]

    def test_excludes_dreamview_and_other_toggles(self):
        # dreamViewToggle is a CAPABILITY_TOGGLE but must NOT be a light zone.
        device = _h60b2_device()
        assert INSTANCE_DREAMVIEW not in device.light_toggle_instances

    def test_empty_for_plain_light(self, mock_light_device):
        assert mock_light_device.light_toggle_instances == []


# ==============================================================================
# GoveeLightZoneSwitchEntity
# ==============================================================================


class TestLightZoneSwitchEntity:
    """Per-zone optimistic on/off switch."""

    @pytest.fixture
    def device(self):
        return _h60b2_device()

    @pytest.fixture
    def mock_coordinator(self, device):
        coordinator = MagicMock()
        coordinator.devices = {device.device_id: device}
        coordinator.get_state = MagicMock(return_value=MagicMock(online=True))
        coordinator.async_control_device = AsyncMock(return_value=True)
        return coordinator

    @pytest.fixture
    def zone_entity(self, mock_coordinator, device):
        from custom_components.govee.switch import GoveeLightZoneSwitchEntity

        entity = GoveeLightZoneSwitchEntity(
            mock_coordinator, device, "light2Toggle", 1
        )
        entity.async_write_ha_state = MagicMock()
        return entity

    def test_unique_id(self, zone_entity, device):
        from custom_components.govee.const import SUFFIX_LIGHT_ZONE

        assert zone_entity.unique_id == f"{device.device_id}{SUFFIX_LIGHT_ZONE}1"

    def test_name_placeholder(self, zone_entity):
        # Zone index 1 -> "Light Zone 2" (1-based display).
        assert zone_entity._attr_translation_placeholders == {"zone": "2"}

    def test_starts_off(self, zone_entity):
        assert zone_entity.is_on is False

    @pytest.mark.asyncio
    async def test_turn_on_sends_toggle(self, zone_entity, mock_coordinator):
        await zone_entity.async_turn_on()

        device_id, cmd = mock_coordinator.async_control_device.call_args[0]
        assert device_id == zone_entity._device_id
        assert isinstance(cmd, ToggleCommand)
        assert cmd.toggle_instance == "light2Toggle"
        assert cmd.enabled is True
        assert zone_entity.is_on is True

    @pytest.mark.asyncio
    async def test_turn_off_sends_toggle(self, zone_entity, mock_coordinator):
        zone_entity._is_on = True
        await zone_entity.async_turn_off()

        cmd = mock_coordinator.async_control_device.call_args[0][1]
        assert isinstance(cmd, ToggleCommand)
        assert cmd.enabled is False
        assert zone_entity.is_on is False

    @pytest.mark.asyncio
    async def test_no_optimistic_flip_on_failure(self, zone_entity, mock_coordinator):
        mock_coordinator.async_control_device.return_value = False
        await zone_entity.async_turn_on()
        # Command failed -> optimistic state must not flip.
        assert zone_entity.is_on is False

    @pytest.mark.asyncio
    async def test_restores_state(self, zone_entity):
        from custom_components.govee.switch import GoveeLightZoneSwitchEntity

        with (
            patch.object(
                GoveeLightZoneSwitchEntity.__bases__[0],
                "async_added_to_hass",
                new_callable=AsyncMock,
            ),
            patch.object(
                zone_entity,
                "async_get_last_state",
                new_callable=AsyncMock,
                return_value=MagicMock(state="on"),
            ),
        ):
            await zone_entity.async_added_to_hass()

        assert zone_entity.is_on is True


# ==============================================================================
# Platform wiring
# ==============================================================================


class TestLightZonePlatformWiring:
    """switch.async_setup_entry creates one zone switch per light{N}Toggle."""

    async def test_setup_creates_three_zone_switches(self):
        from custom_components.govee import switch as switch_mod

        device = _h60b2_device()
        coordinator = MagicMock()
        coordinator.devices = {device.device_id: device}
        entry = MagicMock()
        entry.runtime_data = coordinator
        added: list = []

        await switch_mod.async_setup_entry(
            MagicMock(), entry, lambda ents: added.extend(ents)
        )

        zone_switches = [
            e
            for e in added
            if type(e).__name__ == "GoveeLightZoneSwitchEntity"
        ]
        assert len(zone_switches) == 3
        instances = sorted(e._toggle_instance for e in zone_switches)
        assert instances == ["light1Toggle", "light2Toggle", "light3Toggle"]

    async def test_plain_light_gets_no_zone_switches(self, mock_light_device):
        from custom_components.govee import switch as switch_mod

        coordinator = MagicMock()
        coordinator.devices = {mock_light_device.device_id: mock_light_device}
        entry = MagicMock()
        entry.runtime_data = coordinator
        added: list = []

        await switch_mod.async_setup_entry(
            MagicMock(), entry, lambda ents: added.extend(ents)
        )

        names = [type(e).__name__ for e in added]
        assert "GoveeLightZoneSwitchEntity" not in names
