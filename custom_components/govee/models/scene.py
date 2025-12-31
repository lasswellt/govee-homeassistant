"""Scene option model for Govee devices."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class SceneOption:
    """A scene option from the API.

    Represents either a dynamic scene (built-in lighting effects) or
    a DIY scene (user-created custom effects) available on a device.

    Attributes:
        name: Display name of the scene
        value: Scene identifier (int for DIY, dict for dynamic)
        category: Optional scene category/group
    """

    name: str
    value: Any  # int or dict depending on scene type
    category: str | None = None

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> SceneOption:
        """Create from API response.

        Args:
            data: Scene option dictionary from API

        Returns:
            SceneOption instance
        """
        return cls(
            name=data.get("name", ""),
            value=data.get("value"),
            category=data.get("category"),
        )

    def to_command_value(self) -> Any:
        """Get the value format for sending commands.

        Returns:
            Value to use in control command payloads
        """
        return self.value
