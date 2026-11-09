"""#240: H7073 colour control the Developer API omits, sent over AWS IoT."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.components.light import ColorMode

from custom_components.govee.const import CONF_ENABLE_MQTT_CONTROL
from custom_components.govee.light import GoveeLightEntity
from custom_components.govee.models import ColorCommand, GoveeDevice, RGBColor
from tests.test_cov_coordinator_control import CREDS, DEV, _add, _coordinator, _device, _mqtt

H7073_API = {
    "device": DEV,
    "sku": "H7073",
    "deviceName": "Projektor",
    "type": "devices.types.light",
    "capabilities": [
        {"type": "devices.capabilities.on_off", "instance": "powerSwitch", "parameters": {}},
        {
            "type": "devices.capabilities.range",
            "instance": "brightness",
            "parameters": {"dataType": "INTEGER", "range": {"min": 1, "max": 100}},
        },
    ],
}


async def _discover(iot):
    coord = _coordinator(iot=iot)
    coord._api_client.get_devices = AsyncMock(return_value=[GoveeDevice.from_api_response(H7073_API)])
    await coord._discover_devices()
    return coord


def _ready_mqtt(coord, device):
    _add(coord, device)
    coord._mqtt_client = _mqtt()
    coord._device_topics[device.device_id] = "GD/dev"
    coord._transport.record_success(device.device_id, "mqtt")


@pytest.mark.asyncio
async def test_h7073_gets_colour_with_iot_credentials():
    coord = await _discover(CREDS)

    device = coord.devices[DEV]
    assert device.supports_rgb
    assert not device.supports_color_temp
    cap = device.get_capability("devices.capabilities.color_setting", "colorRgb")
    assert cap is not None and cap.parameters["range"] == {"min": 0, "max": 16777215}


@pytest.mark.asyncio
async def test_h7073_no_colour_api_key_only():
    coord = await _discover(None)

    assert not coord.devices[DEV].supports_rgb


@pytest.mark.asyncio
async def test_h7073_light_entity_offers_rgb():
    coord = await _discover(CREDS)
    entity = GoveeLightEntity(MagicMock(), coord.devices[DEV], enable_scenes=False)

    assert ColorMode.RGB in entity._determine_color_modes()


@pytest.mark.asyncio
async def test_h7073_with_api_colour_is_not_given_a_second_one():
    data = {**H7073_API, "capabilities": [*H7073_API["capabilities"]]}
    data["capabilities"].append(
        {"type": "devices.capabilities.color_setting", "instance": "colorRgb", "parameters": {}}
    )
    coord = _coordinator(iot=CREDS)
    coord._api_client.get_devices = AsyncMock(return_value=[GoveeDevice.from_api_response(data)])
    await coord._discover_devices()

    caps = [c for c in coord.devices[DEV].capabilities if c.instance == "colorRgb"]
    assert len(caps) == 1


@pytest.mark.asyncio
async def test_other_sku_gets_no_synthetic_colour():
    data = {**H7073_API, "sku": "H6072"}
    coord = _coordinator(iot=CREDS)
    coord._api_client.get_devices = AsyncMock(return_value=[GoveeDevice.from_api_response(data)])
    await coord._discover_devices()

    assert not coord.devices[DEV].supports_rgb


@pytest.mark.asyncio
async def test_h7073_colour_goes_over_mqtt_with_control_option_off():
    coord = await _discover(CREDS)
    _ready_mqtt(coord, coord.devices[DEV])

    assert await coord.async_control_device(DEV, ColorCommand(color=RGBColor(255, 0, 0))) is True

    coord._mqtt_client.async_publish_command.assert_awaited()
    coord._api_client.control_device.assert_not_awaited()


@pytest.mark.asyncio
async def test_h7073_colour_falls_back_to_rest_when_mqtt_down():
    coord = await _discover(CREDS)
    _ready_mqtt(coord, coord.devices[DEV])
    coord._mqtt_client.connected = False

    assert await coord.async_control_device(DEV, ColorCommand(color=RGBColor(255, 0, 0))) is True

    coord._mqtt_client.async_publish_command.assert_not_awaited()
    coord._api_client.control_device.assert_awaited_once()


@pytest.mark.asyncio
async def test_h7073_colour_falls_back_to_rest_when_never_heard():
    coord = await _discover(CREDS)
    _add(coord, coord.devices[DEV])
    coord._mqtt_client = _mqtt()
    coord._device_topics[DEV] = "GD/dev"

    assert await coord.async_control_device(DEV, ColorCommand(color=RGBColor(255, 0, 0))) is True

    coord._mqtt_client.async_publish_command.assert_not_awaited()
    coord._api_client.control_device.assert_awaited_once()


@pytest.mark.asyncio
async def test_other_sku_colour_still_needs_the_control_option():
    coord = _coordinator(iot=CREDS)
    _ready_mqtt(coord, _device())

    assert await coord.async_control_device(DEV, ColorCommand(color=RGBColor(255, 0, 0))) is True
    coord._mqtt_client.async_publish_command.assert_not_awaited()

    coord = _coordinator(iot=CREDS, options={CONF_ENABLE_MQTT_CONTROL: True})
    _ready_mqtt(coord, _device())
    assert await coord.async_control_device(DEV, ColorCommand(color=RGBColor(255, 0, 0))) is True
    coord._mqtt_client.async_publish_command.assert_awaited()
