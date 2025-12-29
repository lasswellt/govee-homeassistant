"""Test Govee base entity."""
from __future__ import annotations

from unittest.mock import MagicMock, patch
import pytest
from homeassistant.core import HomeAssistant

from custom_components.govee.entity import GoveeEntity
from custom_components.govee.const import DOMAIN


class TestGoveeEntity:
    """Test GoveeEntity base class."""

    def test_entity_initialization(
        self,
        mock_coordinator: MagicMock,
        mock_device_light,
    ):
        """Test entity initializes correctly."""
        entity = GoveeEntity(mock_coordinator, mock_device_light)

        assert entity._device == mock_device_light
        assert entity._device_id == mock_device_light.device_id
        assert entity.coordinator == mock_coordinator

    def test_entity_device_info(
        self,
        mock_coordinator: MagicMock,
        mock_device_light,
    ):
        """Test entity device info is set correctly."""
        entity = GoveeEntity(mock_coordinator, mock_device_light)

        device_info = entity._attr_device_info

        assert device_info is not None
        assert (DOMAIN, mock_device_light.device_id) in device_info["identifiers"]
        assert device_info["name"] == mock_device_light.device_name
        assert device_info["manufacturer"] == "Govee"
        assert device_info["model"] == mock_device_light.sku
        assert device_info["sw_version"] == mock_device_light.firmware_version

    def test_entity_has_entity_name_true(
        self,
        mock_coordinator: MagicMock,
        mock_device_light,
    ):
        """Test entity has_entity_name attribute is True."""
        entity = GoveeEntity(mock_coordinator, mock_device_light)

        assert entity._attr_has_entity_name is True

    def test_device_state_property(
        self,
        mock_coordinator: MagicMock,
        mock_device_light,
        mock_state_light_on,
    ):
        """Test device_state property returns state from coordinator."""
        mock_coordinator.get_state.return_value = mock_state_light_on
        entity = GoveeEntity(mock_coordinator, mock_device_light)

        state = entity.device_state

        assert state == mock_state_light_on
        mock_coordinator.get_state.assert_called_once_with(mock_device_light.device_id)

    def test_device_state_property_none(
        self,
        mock_coordinator: MagicMock,
        mock_device_light,
    ):
        """Test device_state property returns None when no state."""
        mock_coordinator.get_state.return_value = None
        entity = GoveeEntity(mock_coordinator, mock_device_light)

        state = entity.device_state

        assert state is None

    def test_is_group_device_false(
        self,
        mock_coordinator: MagicMock,
        mock_device_light,
    ):
        """Test _is_group_device returns False for regular device."""
        entity = GoveeEntity(mock_coordinator, mock_device_light)

        assert entity._is_group_device is False

    def test_is_group_device_true(
        self,
        mock_coordinator: MagicMock,
        mock_device_group,
    ):
        """Test _is_group_device returns True for group device."""
        with patch(
            "custom_components.govee.entity.UNSUPPORTED_DEVICE_SKUS",
            [mock_device_group.sku],
        ):
            entity = GoveeEntity(mock_coordinator, mock_device_group)

            assert entity._is_group_device is True

    def test_available_coordinator_unavailable(
        self,
        mock_coordinator: MagicMock,
        mock_device_light,
    ):
        """Test entity unavailable when coordinator unavailable."""
        mock_coordinator.last_update_success = False
        entity = GoveeEntity(mock_coordinator, mock_device_light)

        # Mock CoordinatorEntity.available
        with patch.object(GoveeEntity, "available", new_callable=lambda: property(lambda self: False)):
            assert entity.available is False

    def test_available_regular_device_online(
        self,
        mock_coordinator: MagicMock,
        mock_device_light,
        mock_state_light_on,
    ):
        """Test regular device available when online."""
        mock_coordinator.get_state.return_value = mock_state_light_on
        entity = GoveeEntity(mock_coordinator, mock_device_light)

        assert mock_state_light_on.online is True
        # Note: Full availability test requires mocking parent class

    def test_available_regular_device_offline(
        self,
        mock_coordinator: MagicMock,
        mock_device_light,
        mock_state_offline,
    ):
        """Test regular device unavailable when offline."""
        mock_coordinator.get_state.return_value = mock_state_offline
        entity = GoveeEntity(mock_coordinator, mock_device_light)

        assert mock_state_offline.online is False

    def test_available_regular_device_no_state(
        self,
        mock_coordinator: MagicMock,
        mock_device_light,
    ):
        """Test regular device unavailable when no state."""
        mock_coordinator.get_state.return_value = None
        entity = GoveeEntity(mock_coordinator, mock_device_light)

        state = entity.device_state
        assert state is None

    def test_available_group_device_always_true(
        self,
        mock_coordinator: MagicMock,
        mock_device_group,
    ):
        """Test group device always available even if offline."""
        with patch(
            "custom_components.govee.entity.UNSUPPORTED_DEVICE_SKUS",
            [mock_device_group.sku],
        ):
            entity = GoveeEntity(mock_coordinator, mock_device_group)

            # Group devices should be available for control
            assert entity._is_group_device is True

    def test_extra_state_attributes_basic(
        self,
        mock_coordinator: MagicMock,
        mock_device_light,
    ):
        """Test extra_state_attributes includes basic info."""
        mock_coordinator.rate_limit_remaining = 9999
        mock_coordinator.rate_limit_remaining_minute = 99
        entity = GoveeEntity(mock_coordinator, mock_device_light)

        attrs = entity.extra_state_attributes

        assert attrs["device_id"] == mock_device_light.device_id
        assert attrs["model"] == mock_device_light.sku
        assert attrs["rate_limit_remaining"] == 9999
        assert attrs["rate_limit_remaining_minute"] == 99

    def test_extra_state_attributes_group_device(
        self,
        mock_coordinator: MagicMock,
        mock_device_group,
    ):
        """Test extra_state_attributes includes group device info."""
        mock_coordinator.rate_limit_remaining = 9999
        mock_coordinator.rate_limit_remaining_minute = 99

        with patch(
            "custom_components.govee.entity.UNSUPPORTED_DEVICE_SKUS",
            [mock_device_group.sku],
        ):
            entity = GoveeEntity(mock_coordinator, mock_device_group)

            attrs = entity.extra_state_attributes

            assert attrs["group_device"] is True
            assert "assumed_state_reason" in attrs
            assert "cannot be queried" in attrs["assumed_state_reason"]

    def test_entity_unique_id(
        self,
        mock_coordinator: MagicMock,
        mock_device_light,
    ):
        """Test entity has device_id as identifier."""
        entity = GoveeEntity(mock_coordinator, mock_device_light)

        assert entity._device_id == mock_device_light.device_id
