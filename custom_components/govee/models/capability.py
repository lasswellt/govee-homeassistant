"""Device capability models for Govee devices.

This module defines the data structures for device capabilities as reported
by the Govee API. Capabilities define what a device can do (on/off, brightness,
color, scenes, etc.) and their valid parameter ranges.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class CapabilityParameter:
    """Parameter definition for a capability.

    Defines the data type and constraints for a capability's values.

    Attributes:
        data_type: Parameter type ("INTEGER", "ENUM", "STRUCT", etc.)
        range: Value range for INTEGER types (min, max, precision)
        options: Valid options for ENUM types
        fields: Field definitions for STRUCT types
    """

    data_type: str  # ENUM, INTEGER, STRUCT, etc.
    range: dict[str, int] | None = None  # min, max, precision
    options: list[dict[str, Any]] | None = None  # For ENUM types
    fields: list[dict[str, Any]] | None = None  # For STRUCT types

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> CapabilityParameter:
        """Create from API response.

        Args:
            data: Parameters dictionary from API capability

        Returns:
            CapabilityParameter instance
        """
        return cls(
            data_type=data.get("dataType", ""),
            range=data.get("range"),
            options=data.get("options"),
            fields=data.get("fields"),
        )


@dataclass
class DeviceCapability:
    """A device capability from the API.

    Represents a single capability that a device supports, such as power on/off,
    brightness control, or color setting.

    Attributes:
        type: Capability type identifier (e.g., "devices.capabilities.on_off")
        instance: Capability instance name (e.g., "powerSwitch")
        parameters: Optional parameter definition
        min_value: Extracted minimum value for range capabilities
        max_value: Extracted maximum value for range capabilities
    """

    type: str  # e.g., "devices.capabilities.on_off"
    instance: str  # e.g., "powerSwitch"
    parameters: CapabilityParameter | None = None

    # Parsed constraints for convenience
    min_value: int | None = None
    max_value: int | None = None

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> DeviceCapability:
        """Create from API response.

        Args:
            data: Capability dictionary from API

        Returns:
            DeviceCapability instance with parsed parameters
        """
        params_data = data.get("parameters", {})
        parameters = CapabilityParameter.from_api(params_data) if params_data else None

        # Extract range constraints
        min_value = None
        max_value = None
        if parameters and parameters.range:
            min_value = parameters.range.get("min")
            max_value = parameters.range.get("max")

        return cls(
            type=data.get("type", ""),
            instance=data.get("instance", ""),
            parameters=parameters,
            min_value=min_value,
            max_value=max_value,
        )


@dataclass
class CapabilityCommand:
    """Command to send to a device.

    Represents a control command with capability type, instance, and value.

    Attributes:
        type: Capability type to control
        instance: Capability instance to target
        value: Value to set (type depends on capability)
    """

    type: str  # Capability type
    instance: str  # Capability instance
    value: Any  # Value to set

    def to_api(self) -> dict[str, Any]:
        """Convert to API format.

        Returns:
            Dictionary ready for API control request
        """
        return {
            "type": self.type,
            "instance": self.instance,
            "value": self.value,
        }
