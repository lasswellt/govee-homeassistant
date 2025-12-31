"""Switch entity descriptions for Govee integration."""

from __future__ import annotations

from dataclasses import dataclass

from homeassistant.components.switch import SwitchDeviceClass, SwitchEntityDescription


@dataclass(frozen=True, kw_only=True)
class GoveeSwitchEntityDescription(SwitchEntityDescription):
    """Describes a Govee switch entity.

    Extends SwitchEntityDescription with Govee-specific configuration.
    This allows centralized entity configuration separate from entity logic.
    """


# Switch entity descriptions for Govee devices
SWITCH_DESCRIPTIONS: dict[str, GoveeSwitchEntityDescription] = {
    "outlet": GoveeSwitchEntityDescription(
        key="outlet",
        translation_key="outlet",
        device_class=SwitchDeviceClass.OUTLET,
        entity_registry_enabled_default=True,
    ),
    "nightlight": GoveeSwitchEntityDescription(
        key="nightlight",
        translation_key="nightlight",
        device_class=SwitchDeviceClass.SWITCH,
        entity_registry_enabled_default=False,  # Disabled by default (opt-in feature)
    ),
}
