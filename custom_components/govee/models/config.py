"""Runtime data and configuration types for Govee integration.

This module defines the runtime data structure and typed ConfigEntry used
throughout the integration. Using a typed ConfigEntry (PEP 695) provides
strict type checking for runtime_data access.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry

    from ..api import GoveeApiClient
    from ..coordinator import GoveeDataUpdateCoordinator
    from .device import GoveeDevice


@dataclass
class GoveeRuntimeData:
    """Runtime data for Govee integration stored in config entry.

    This data is attached to the config entry at runtime and provides
    centralized access to the API client, coordinator, and discovered devices.

    Attributes:
        client: Govee API client instance
        coordinator: Data update coordinator managing device state
        devices: Dictionary of discovered devices (device_id -> GoveeDevice)
    """

    client: GoveeApiClient
    coordinator: GoveeDataUpdateCoordinator
    devices: dict[str, GoveeDevice]


# Type alias for ConfigEntry with GoveeRuntimeData
# This enables strict typing: entry.runtime_data.coordinator is fully typed
type GoveeConfigEntry = ConfigEntry[GoveeRuntimeData]
