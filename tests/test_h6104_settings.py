"""H6104 desired TV settings, restoration, and mode transitions."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.govee import number, select
from custom_components.govee.const import CONF_ENABLE_H6104_BLE
from custom_components.govee.coordinator import GoveeCoordinator
from custom_components.govee.entity import GoveeEntity
from custom_components.govee.h6104 import GoveeTVAreaSelect, GoveeTVSensitivityNumber
from custom_components.govee.models import ColorCommand, ColorTempCommand, GoveeDevice, GoveeDeviceState, RGBColor
from custom_components.govee.models import create_dreamview_command


@pytest.fixture
def tv():
    entry = MagicMock()
    entry.options = {CONF_ENABLE_H6104_BLE: True}
    entry.data = {}
    coord = GoveeCoordinator(
        hass=MagicMock(), config_entry=entry, api_client=MagicMock(), iot_credentials=None, poll_interval=60
    )
    device = GoveeDevice(device_id="tv", sku="H6104", name="TV", device_type="devices.types.light", capabilities=())
    coord._devices = {"tv": device}
    coord._states = {"tv": GoveeDeviceState.create_empty("tv")}
    coord.async_send_dreamview = AsyncMock(return_value=True)
    entry.runtime_data = coord
    return coord, device, entry


@pytest.mark.asyncio
@pytest.mark.parametrize("active", [False, True])
async def test_live_settings_preserve_the_other_field_and_only_send_in_tv_mode(tv, active):
    coord, device, _entry = tv
    coord._states["tv"].dreamview_enabled = active
    sensitivity = GoveeTVSensitivityNumber(coord, device)
    area = GoveeTVAreaSelect(coord, device)
    sensitivity.async_write_ha_state = MagicMock()
    area.async_write_ha_state = MagicMock()
    await sensitivity.async_set_native_value(90)
    await area.async_select_option("Part of screen")
    assert coord.h6104_tv_settings("tv") == (0, 90)
    assert sensitivity.native_value == 90
    assert area.current_option == "Part of screen"
    assert coord.h6104_tv_settings("another_tv") == (1, 50)
    assert coord.async_send_dreamview.await_count == (2 if active else 0)


@pytest.mark.asyncio
@pytest.mark.parametrize("raise_error", [False, True])
async def test_failed_active_setting_write_rolls_back(tv, raise_error):
    coord, _device, _entry = tv
    coord._states["tv"].dreamview_enabled = True
    if raise_error:
        coord.async_send_dreamview.side_effect = RuntimeError("failed")
        with pytest.raises(RuntimeError):
            await coord.async_update_h6104_tv_settings("tv", sensitivity=80)
    else:
        coord.async_send_dreamview.return_value = False
        assert await coord.async_update_h6104_tv_settings("tv", sensitivity=80) is False
    assert coord.h6104_tv_settings("tv") == (1, 50)


@pytest.mark.asyncio
@pytest.mark.parametrize("value", [-1, 101, 12.5, float("nan")])
async def test_invalid_sensitivity_never_sends(tv, value):
    coord, device, _entry = tv
    with pytest.raises(ValueError):
        await GoveeTVSensitivityNumber(coord, device).async_set_native_value(value)
    coord.async_send_dreamview.assert_not_awaited()


@pytest.mark.asyncio
async def test_settings_restore_without_selecting_a_mode_or_sending(tv):
    coord, device, _entry = tv
    with patch.object(GoveeEntity, "async_added_to_hass", AsyncMock()):
        for entity, value in [
            (GoveeTVSensitivityNumber(coord, device), "85"),
            (GoveeTVAreaSelect(coord, device), "Part of screen"),
        ]:
            entity.async_get_last_state = AsyncMock(return_value=MagicMock(state=value))
            await entity.async_added_to_hass()
    assert coord.h6104_tv_settings("tv") == (0, 85)
    assert coord._states["tv"].dreamview_enabled is None
    coord.async_send_dreamview.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("value", ["unknown", "unavailable", "nan", "105", "12.5"])
async def test_invalid_restore_keeps_defaults(tv, value):
    coord, device, _entry = tv
    with patch.object(GoveeEntity, "async_added_to_hass", AsyncMock()):
        for entity in [GoveeTVSensitivityNumber(coord, device), GoveeTVAreaSelect(coord, device)]:
            entity.async_get_last_state = AsyncMock(return_value=MagicMock(state=value))
            await entity.async_added_to_hass()
    assert coord.h6104_tv_settings("tv") == (1, 50)


@pytest.mark.asyncio
@pytest.mark.parametrize("enabled", [False, True])
async def test_setting_entities_only_created_when_opted_in(tv, enabled):
    coord, _device, entry = tv
    entry.options = {CONF_ENABLE_H6104_BLE: enabled}
    added = []
    await number.async_setup_entry(coord.hass, entry, added.extend)
    await select.async_setup_entry(coord.hass, entry, added.extend)
    assert sum(isinstance(e, (GoveeTVSensitivityNumber, GoveeTVAreaSelect)) for e in added) == (2 if enabled else 0)


@pytest.mark.parametrize("command", [ColorCommand(color=RGBColor(255, 0, 0)), ColorTempCommand(kelvin=4000)])
def test_selecting_solid_color_clears_dreamview(tv, command):
    coord, _device, _entry = tv
    coord._states["tv"].dreamview_enabled = True
    coord._apply_optimistic_update("tv", command)
    assert coord._states["tv"].dreamview_enabled is False


@pytest.mark.asyncio
async def test_ble_tv_write_uses_selected_settings_and_records_them(tv):
    coord, _device, _entry = tv
    coord.restore_h6104_tv_settings("tv", area=0, sensitivity=95)
    ble = MagicMock()
    ble.set_tv_mode = AsyncMock()
    coord._ble_devices["tv"] = ble
    assert await coord._try_ble_command("tv", create_dreamview_command(True)) is True
    ble.set_tv_mode.assert_awaited_once_with(area=0, sensitivity=95)
    assert "area=0 sensitivity=95" in coord._api_client.record_local_command.call_args.kwargs["detail"]


@pytest.mark.asyncio
@pytest.mark.parametrize("area,sensitivity", [(0, 0), (0, 100), (1, 90)])
async def test_tv_settings_are_encoded_in_the_frame(area, sensitivity):
    from custom_components.govee.api.ble import GoveeBLEDevice

    ble = GoveeBLEDevice(MagicMock(), legacy_uart=True)
    with patch.object(ble, "_write", AsyncMock()) as write:
        await ble.set_tv_mode(area=area, sensitivity=sensitivity)
    assert write.call_args.args[0][:6] == bytes([0x33, 0x05, 0, 0, area, sensitivity])
