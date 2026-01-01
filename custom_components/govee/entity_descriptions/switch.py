from __future__ import annotations

from dataclasses import dataclass

from homeassistant.components.switch import SwitchDeviceClass, SwitchEntityDescription


@dataclass(frozen=True, kw_only=True)
class GoveeSwitchEntityDescription(SwitchEntityDescription):
    """Describes a Govee switch entity."""


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
        entity_registry_enabled_default=False,
    ),
    "oscillation": GoveeSwitchEntityDescription(
        key="oscillation",
        translation_key="oscillation",
        device_class=SwitchDeviceClass.SWITCH,
        entity_registry_enabled_default=True,
    ),
    "thermostat": GoveeSwitchEntityDescription(
        key="thermostat",
        translation_key="thermostat",
        device_class=SwitchDeviceClass.SWITCH,
        entity_registry_enabled_default=True,
    ),
    "gradient": GoveeSwitchEntityDescription(
        key="gradient",
        translation_key="gradient",
        device_class=SwitchDeviceClass.SWITCH,
        entity_registry_enabled_default=False,
    ),
    "warm_mist": GoveeSwitchEntityDescription(
        key="warm_mist",
        translation_key="warm_mist",
        device_class=SwitchDeviceClass.SWITCH,
        entity_registry_enabled_default=True,
    ),
    "air_deflector": GoveeSwitchEntityDescription(
        key="air_deflector",
        translation_key="air_deflector",
        device_class=SwitchDeviceClass.SWITCH,
        entity_registry_enabled_default=True,
    ),
}
