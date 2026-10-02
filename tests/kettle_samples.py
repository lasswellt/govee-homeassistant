"""Captured H7175 kettle samples shared by the kettle tests.

Source: one H7175 on an account that displays Fahrenheit, idle, custom slot
4 selected in the Govee app (target 176 °F). The ``/user/devices`` capability
block, the ``/device/state`` capabilities and the MQTT push are verbatim
(device id and name replaced). See docs/_research/2026-09-30_h7175-kettle.md.
"""

from __future__ import annotations

from typing import Any

DEVICE_ID = "AA:BB:CC:DD:71:75:00:01"

# /user/devices capability block, verbatim from the API (the property
# capability has no "parameters" key there; Home Assistant diagnostics show
# "parameters": {} after parsing).
H7175_CAPABILITIES: list[dict[str, Any]] = [
    {
        "type": "devices.capabilities.on_off",
        "instance": "powerSwitch",
        "parameters": {"dataType": "ENUM", "options": [{"name": "on", "value": 1}, {"name": "off", "value": 0}]},
    },
    {
        "type": "devices.capabilities.temperature_setting",
        "instance": "sliderTemperature",
        "parameters": {
            "dataType": "STRUCT",
            "fields": [
                {
                    "fieldName": "temperature",
                    "dataType": "INTEGER",
                    "range": {"min": 40, "max": 100, "precision": 1},
                    "required": True,
                },
                {
                    "fieldName": "unit",
                    "defaultValue": "Celsius",
                    "dataType": "ENUM",
                    "options": [
                        {"name": "Celsius", "value": "Celsius"},
                        {"name": "Fahrenheit", "value": "Fahrenheit"},
                    ],
                    "required": True,
                },
            ],
        },
    },
    {"type": "devices.capabilities.property", "instance": "sensorTemperature"},
    {
        "type": "devices.capabilities.work_mode",
        "instance": "workMode",
        "parameters": {
            "dataType": "STRUCT",
            "fields": [
                {
                    "fieldName": "workMode",
                    "dataType": "ENUM",
                    "options": [
                        {"name": "Custom", "value": 1},
                        {"name": "Green Tea", "value": 2},
                        {"name": "Oolong Tea", "value": 3},
                        {"name": "Coffee", "value": 4},
                        {"name": "Black Tea/Boil", "value": 5},
                    ],
                    "required": True,
                },
                {
                    "fieldName": "modeValue",
                    "dataType": "ENUM",
                    "options": [
                        {
                            "dataType": "ENUM",
                            "name": "Custom",
                            "options": [{"value": 1}, {"value": 2}, {"value": 3}, {"value": 4}],
                        },
                        {"defaultValue": 0, "name": "Green Tea"},
                        {"defaultValue": 0, "name": "Oolong Tea"},
                        {"defaultValue": 0, "name": "Coffee"},
                        {"defaultValue": 0, "name": "Black Tea/Boil"},
                    ],
                    "required": False,
                },
            ],
        },
    },
]

H7175_DEVICE: dict[str, Any] = {
    "sku": "H7175",
    "device": DEVICE_ID,
    "deviceName": "Kettle",
    "type": "devices.types.kettle",
    "capabilities": H7175_CAPABILITIES,
}

# /device/state "capabilities" (verbatim values).
H7175_STATE: dict[str, Any] = {
    "sku": "H7175",
    "device": DEVICE_ID,
    "capabilities": [
        {"type": "devices.capabilities.online", "instance": "online", "state": {"value": True}},
        {"type": "devices.capabilities.on_off", "instance": "powerSwitch", "state": {"value": 0}},
        {
            "type": "devices.capabilities.temperature_setting",
            "instance": "sliderTemperature",
            "state": {"value": {"unit": "Fahrenheit", "targetTemperature": 176}},
        },
        {"type": "devices.capabilities.property", "instance": "sensorTemperature", "state": {"value": 91.0}},
        {"type": "devices.capabilities.work_mode", "instance": "workMode", "state": {"value": {"workMode": 1}}},
    ],
}

# The integration's last-MQTT-message diagnostics entry (verbatim).
H7175_MQTT: dict[str, Any] = {
    "onOff": 0,
    "sta": {"setTem": 17600, "curTem": 9000},
    "result": 1,
    "_op_frames": [
        "aa1f0801000000000000000000000000000000bc",
        "aa1f0600000000000000000000000000000000b3",
        "aa050001040000000000000000000000000000aa",
        "aa050246500000000000000000000000000000bb",
        "aa05034c2c0000000000000000000000000000cc",
        "aa050450140000000000000000000000000000ef",
        "aa050552d0000000000000000000000000000028",
        "aa050100c45c0100aaf802000000000000000067",
        "aa050101c074030044c004000000000000000098",
        "aa100123280100000000000000000000000000b1",
        "aa170000000000000000000000000000000000bd",
        "aa190000000000000000000000000000000000b3",
        "aa22010078780000000000000000000000000089",
        "aa190000000000000000000000000000000000b3",
        "aa2300016366c9d5e50000000000000000000074",
    ],
}


def mqtt_frames() -> list[bytes]:
    """The sample push's op.command frames as bytes."""
    return [bytes.fromhex(frame) for frame in H7175_MQTT["_op_frames"]]


# Custom-slot page 0 with slot 1 as the app's DIY slot (verbatim): slot 1
# (0x445c) has the flag bit clear, slot 2 (0xaaf8) set. In the sample push
# above, slot 4 (0x44c0) is the clear one.
SLOT_PAGE0_DIY_1 = "aa050100445c0100aaf8020000000000000000e7"

# Selected-mode frame after the target was set directly (verbatim): workMode
# 6 ("manual"), target 0x44c0 = 176.00 °F.
MODE_MANUAL_176 = "aa05000644c0000000000000000000000000002d"

# /device/state after the same change: the poll reported workMode 6.
H7175_STATE_MANUAL: dict[str, Any] = {
    **H7175_STATE,
    "capabilities": [
        *H7175_STATE["capabilities"][:-1],
        {"type": "devices.capabilities.work_mode", "instance": "workMode", "state": {"value": {"workMode": 6}}},
    ],
}

# Keep warm (verbatim): status frames, then command echoes seen on the
# device topic after changing keep warm in the app.
KEEP_WARM_STATUS_OFF_2H = "aa22000078780000000000000000000000000088"
KEEP_WARM_STATUS_ON_2H = "aa22010078780000000000000000000000000089"
KEEP_WARM_ECHO_ON_30M = "3a2201001e1e0000000000000000000000000019"
KEEP_WARM_ECHO_ON_1H = "3a2201003c3c0000000000000000000000000019"
KEEP_WARM_ECHO_ON_90M = "3a2201005a5a0000000000000000000000000019"
KEEP_WARM_ECHO_ON_2H = "3a22010078780000000000000000000000000019"

# Keep warm counting down (verbatim, diagnostics): 120 min set, 116 left,
# pushed with "reached target"; the next push showed 120 left again.
KEEP_WARM_STATUS_ON_2H_116_LEFT = "aa22010078740000000000000000000000000085"
HEATING_REACHED_TARGET = "aa190400000000000000000000000000000000b7"
HEATING_KEEPING_WARM = "aa190200000000000000000000000000000000b1"

HEATING_IDLE = "aa190000000000000000000000000000000000b3"
