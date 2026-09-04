# Govee Protocol Reference

A comprehensive technical reference for Govee device communication protocols, compiled from official documentation, PCAP analysis of the Android app, and community reverse engineering efforts.

**Last Updated:** March 4, 2026
**Data Sources:**
- `docs/PCAPdroid_24_Jan_16_00_31.pcap` - Android app network capture
- `logs/PCAPdroid_09_Jan_19_27_26.pcap` - Reference capture
- Live AWS IoT MQTT capture sessions (January 2026)
- User-submitted API responses from GitHub issues (February–March 2026)

---

## Table of Contents

1. [Protocol Overview](#1-protocol-overview)
2. [Official Platform API v2.0](#2-official-platform-api-v20)
3. [Curl Testing Reference](#3-curl-testing-reference)
4. [AWS IoT MQTT (Undocumented)](#4-aws-iot-mqtt-undocumented)
5. [Undocumented Internal API](#5-undocumented-internal-api-app2goveecom)
6. [LAN API (UDP)](#6-lan-api-udp)
7. [BLE Protocol](#7-ble-protocol)
8. [State Management](#8-state-management)
9. [Device Capabilities](#9-device-capabilities)
10. [Scene & DIY Modes](#10-scene--diy-modes)
11. [PCAP Analysis Details](#11-pcap-analysis-details)
12. [References](#12-references)

---

## 1. Protocol Overview

Govee devices support multiple communication protocols, each with distinct characteristics:

| Protocol | Latency | Auth Method | Use Case | Rate Limits |
|----------|---------|-------------|----------|-------------|
| **Platform API v2** | 2-4s | API Key | Device control, state query | 10K/day |
| **AWS IoT MQTT** | ~50ms | Certificates | Real-time state push | None known |
| **Official MQTT** | ~100ms | API Key | Event notifications | None known |
| **LAN UDP** | <10ms | None | Local control | None |
| **BLE** | <50ms | Pairing | Direct control | None |

### Communication Flow (from PCAP)

```
┌─────────────────┐     HTTPS/443     ┌──────────────────┐
│   Govee App     │◄─────────────────►│  app2.govee.com  │
│   (Android)     │                    │  (Auth + API)    │
└────────┬────────┘                    └──────────────────┘
         │
         │  MQTT/8883 (TLS + Mutual Auth)
         ▼
┌─────────────────────────────────────────────────────────┐
│         AWS IoT Core (us-east-1)                        │
│   aqm3wd1qlc3dy-ats.iot.us-east-1.amazonaws.com        │
│                                                         │
│   Topic: GA/{account-uuid}                              │
│   - Device state push notifications                     │
│   - Bidirectional command/response                      │
└─────────────────────────────────────────────────────────┘
         │
         ▼
┌─────────────────┐     UDP 4001-4003  ┌──────────────────┐
│   Home Network  │◄─────────────────►│  Govee Devices   │
│                 │     (LAN API)      │                  │
└─────────────────┘                    └──────────────────┘
```

---

## 2. Official Platform API v2.0

### 2.1 Base Configuration

| Parameter | Value |
|-----------|-------|
| **Base URL** | `https://openapi.api.govee.com/router/api/v1` |
| **Auth Header** | `Govee-API-Key: {your-api-key}` |
| **Content-Type** | `application/json` |

### 2.2 Endpoints

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/user/devices` | GET | List all devices |
| `/device/state` | POST | Query device state |
| `/device/control` | POST | Send control command |
| `/device/scenes` | POST | Get dynamic scenes |
| `/device/diy-scenes` | POST | Get DIY scenes |

### 2.3 Request Format

All POST requests use this structure:

```json
{
  "requestId": "550e8400-e29b-41d4-a716-446655440000",
  "payload": {
    "sku": "H618E",
    "device": "8C:2E:9C:04:A0:03:82:D1",
    "capability": {
      "type": "devices.capabilities.TYPE",
      "instance": "INSTANCE_NAME",
      "value": "VALUE"
    }
  }
}
```

### 2.4 Response Format

```json
{
  "requestId": "550e8400-e29b-41d4-a716-446655440000",
  "code": 200,
  "msg": "success",
  "payload": {
    "sku": "H618E",
    "device": "8C:2E:9C:04:A0:03:82:D1",
    "capabilities": [
      {
        "type": "devices.capabilities.on_off",
        "instance": "powerSwitch",
        "state": { "value": 1 }
      },
      {
        "type": "devices.capabilities.range",
        "instance": "brightness",
        "state": { "value": 75 }
      },
      {
        "type": "devices.capabilities.color_setting",
        "instance": "colorRgb",
        "state": { "value": 16711680 }
      }
    ]
  }
}
```

### 2.5 Rate Limiting

**Limits:**
- 10,000 requests per day per account
- Per-minute limits (undocumented, ~10/min per device)

**Response Headers:**
```
API-RateLimit-Remaining: 95      # Per-minute remaining
API-RateLimit-Reset: 1704812400  # Per-minute reset timestamp
X-RateLimit-Remaining: 9500      # Per-day remaining
X-RateLimit-Reset: 1704844800    # Per-day reset timestamp
```

### 2.6 Control Examples

**Power On/Off:**
```json
{
  "type": "devices.capabilities.on_off",
  "instance": "powerSwitch",
  "value": 1
}
```

**Brightness (0-100):**
```json
{
  "type": "devices.capabilities.range",
  "instance": "brightness",
  "value": 75
}
```

**RGB Color (packed integer):**
```json
{
  "type": "devices.capabilities.color_setting",
  "instance": "colorRgb",
  "value": 16711680
}
```
*Note: RGB packed as `(R << 16) + (G << 8) + B`. 16711680 = RGB(255, 0, 0)*

**Color Temperature (Kelvin):**
```json
{
  "type": "devices.capabilities.color_setting",
  "instance": "colorTemperatureK",
  "value": 4500
}
```

**Segment Color (RGBIC devices):**
```json
{
  "type": "devices.capabilities.segment_color_setting",
  "instance": "segmentedColorRgb",
  "value": {
    "segment": [0, 1, 2, 3],
    "rgb": 255
  }
}
```

**Scene Activation:**
```json
{
  "type": "devices.capabilities.dynamic_scene",
  "instance": "lightScene",
  "value": {
    "id": 3853,
    "paramId": 4280
  }
}
```

### 2.7 Error Codes

| Code | Description |
|------|-------------|
| 200 | Success |
| 400 | Missing/invalid parameters |
| 401 | Authentication failure |
| 404 | Device/instance not found |
| 429 | Rate limit exceeded |
| 500 | Internal server error |

---

## 3. Curl Testing Reference

Validated API testing with real device responses (H601F floor lamps with 7 segments).

### 3.1 Environment Setup

```bash
# Store your API key
export GOVEE_API_KEY="your-api-key-here"

# Base URL
export GOVEE_API="https://openapi.api.govee.com/router/api/v1"
```

### 3.2 List Devices

```bash
curl -s -X GET "$GOVEE_API/user/devices" \
  -H "Govee-API-Key: $GOVEE_API_KEY" \
  -H "Content-Type: application/json" | jq .
```

**Real Response (H601F - 7-segment floor lamp):**
```json
{
  "code": 200,
  "message": "success",
  "data": [
    {
      "sku": "H601F",
      "device": "03:9C:DC:06:75:4B:10:7C",
      "deviceName": "Master F Left",
      "type": "devices.types.light",
      "capabilities": [
        {
          "type": "devices.capabilities.on_off",
          "instance": "powerSwitch",
          "parameters": {
            "dataType": "ENUM",
            "options": [
              {"name": "on", "value": 1},
              {"name": "off", "value": 0}
            ]
          }
        },
        {
          "type": "devices.capabilities.range",
          "instance": "brightness",
          "parameters": {
            "unit": "unit.percent",
            "dataType": "INTEGER",
            "range": {"min": 1, "max": 100, "precision": 1}
          }
        },
        {
          "type": "devices.capabilities.color_setting",
          "instance": "colorRgb",
          "parameters": {
            "dataType": "INTEGER",
            "range": {"min": 0, "max": 16777215, "precision": 1}
          }
        },
        {
          "type": "devices.capabilities.color_setting",
          "instance": "colorTemperatureK",
          "parameters": {
            "dataType": "INTEGER",
            "range": {"min": 2700, "max": 6500, "precision": 1}
          }
        },
        {
          "type": "devices.capabilities.segment_color_setting",
          "instance": "segmentedColorRgb",
          "parameters": {
            "dataType": "STRUCT",
            "fields": [
              {
                "fieldName": "segment",
                "size": {"min": 1, "max": 7},
                "dataType": "Array",
                "elementRange": {"min": 0, "max": 6},
                "elementType": "INTEGER",
                "required": true
              },
              {
                "fieldName": "rgb",
                "dataType": "INTEGER",
                "range": {"min": 0, "max": 16777215, "precision": 1},
                "required": true
              }
            ]
          }
        },
        {
          "type": "devices.capabilities.dynamic_scene",
          "instance": "lightScene",
          "parameters": {"dataType": "ENUM", "options": []}
        },
        {
          "type": "devices.capabilities.dynamic_scene",
          "instance": "diyScene",
          "parameters": {"dataType": "ENUM", "options": []}
        },
        {
          "type": "devices.capabilities.dynamic_scene",
          "instance": "snapshot",
          "parameters": {"dataType": "ENUM", "options": []}
        }
      ]
    }
  ]
}
```

### 3.3 Get Device State

```bash
curl -s -X POST "$GOVEE_API/device/state" \
  -H "Govee-API-Key: $GOVEE_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "requestId": "state-001",
    "payload": {
      "sku": "H601F",
      "device": "03:9C:DC:06:75:4B:10:7C"
    }
  }' | jq .
```

**Real Response:**
```json
{
  "requestId": "state-001",
  "msg": "success",
  "code": 200,
  "payload": {
    "sku": "H601F",
    "device": "03:9C:DC:06:75:4B:10:7C",
    "capabilities": [
      {
        "type": "devices.capabilities.online",
        "instance": "online",
        "state": {"value": true}
      },
      {
        "type": "devices.capabilities.on_off",
        "instance": "powerSwitch",
        "state": {"value": 0}
      },
      {
        "type": "devices.capabilities.range",
        "instance": "brightness",
        "state": {"value": 20}
      },
      {
        "type": "devices.capabilities.color_setting",
        "instance": "colorRgb",
        "state": {"value": 0}
      },
      {
        "type": "devices.capabilities.color_setting",
        "instance": "colorTemperatureK",
        "state": {"value": 0}
      },
      {
        "type": "devices.capabilities.segment_color_setting",
        "instance": "segmentedColorRgb",
        "state": {"value": ""}
      },
      {
        "type": "devices.capabilities.dynamic_scene",
        "instance": "lightScene",
        "state": {"value": ""}
      }
    ]
  }
}
```

**Note:** Segment colors and active scenes return empty strings - this is a known API limitation.

### 3.4 Control Commands

**Power On:**
```bash
curl -s -X POST "$GOVEE_API/device/control" \
  -H "Govee-API-Key: $GOVEE_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "requestId": "power-on-001",
    "payload": {
      "sku": "H601F",
      "device": "03:9C:DC:06:75:4B:10:7C",
      "capability": {
        "type": "devices.capabilities.on_off",
        "instance": "powerSwitch",
        "value": 1
      }
    }
  }' | jq .
```

**Response:**
```json
{
  "requestId": "power-on-001",
  "msg": "success",
  "code": 200,
  "capability": {
    "type": "devices.capabilities.on_off",
    "instance": "powerSwitch",
    "state": {"status": "success"},
    "value": 1
  }
}
```

**Set Brightness (50%):**
```bash
curl -s -X POST "$GOVEE_API/device/control" \
  -H "Govee-API-Key: $GOVEE_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "requestId": "brightness-001",
    "payload": {
      "sku": "H601F",
      "device": "03:9C:DC:06:75:4B:10:7C",
      "capability": {
        "type": "devices.capabilities.range",
        "instance": "brightness",
        "value": 50
      }
    }
  }' | jq .
```

**Set RGB Color (Red = 16711680):**
```bash
curl -s -X POST "$GOVEE_API/device/control" \
  -H "Govee-API-Key: $GOVEE_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "requestId": "color-001",
    "payload": {
      "sku": "H601F",
      "device": "03:9C:DC:06:75:4B:10:7C",
      "capability": {
        "type": "devices.capabilities.color_setting",
        "instance": "colorRgb",
        "value": 16711680
      }
    }
  }' | jq .
```

**Set Segment Colors (segments 0-2 = blue):**
```bash
curl -s -X POST "$GOVEE_API/device/control" \
  -H "Govee-API-Key: $GOVEE_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "requestId": "segment-001",
    "payload": {
      "sku": "H601F",
      "device": "03:9C:DC:06:75:4B:10:7C",
      "capability": {
        "type": "devices.capabilities.segment_color_setting",
        "instance": "segmentedColorRgb",
        "value": {
          "segment": [0, 1, 2],
          "rgb": 255
        }
      }
    }
  }' | jq .
```

**Response:**
```json
{
  "requestId": "segment-001",
  "msg": "success",
  "code": 200,
  "capability": {
    "type": "devices.capabilities.segment_color_setting",
    "instance": "segmentedColorRgb",
    "state": {"status": "success"},
    "value": {"segment": [0, 1, 2], "rgb": 255}
  }
}
```

### 3.5 Get Dynamic Scenes

```bash
curl -s -X POST "$GOVEE_API/device/scenes" \
  -H "Govee-API-Key: $GOVEE_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "requestId": "scenes-001",
    "payload": {
      "sku": "H601F",
      "device": "03:9C:DC:06:75:4B:10:7C"
    }
  }' | jq .
```

**Real Response (82 scenes for H601F):**
```json
{
  "requestId": "scenes-001",
  "msg": "success",
  "code": 200,
  "payload": {
    "sku": "H601F",
    "device": "03:9C:DC:06:75:4B:10:7C",
    "capabilities": [
      {
        "type": "devices.capabilities.dynamic_scene",
        "instance": "lightScene",
        "parameters": {
          "dataType": "ENUM",
          "options": [
            {"name": "Rainbow", "value": {"id": 17936, "paramId": 28098}},
            {"name": "Aurora", "value": {"id": 17937, "paramId": 28099}},
            {"name": "Glacier", "value": {"id": 17938, "paramId": 28100}},
            {"name": "Wave", "value": {"id": 17939, "paramId": 28101}},
            {"name": "Deep sea", "value": {"id": 17940, "paramId": 28102}},
            {"name": "Cherry blossoms", "value": {"id": 17941, "paramId": 28103}},
            {"name": "Firefly", "value": {"id": 17942, "paramId": 28104}},
            {"name": "Christmas", "value": {"id": 17961, "paramId": 28123}},
            {"name": "Halloween", "value": {"id": 17958, "paramId": 28120}},
            {"name": "Sunrise", "value": {"id": 17771, "paramId": 27933}},
            {"name": "Sunset", "value": {"id": 17772, "paramId": 27934}},
            {"name": "Sleep", "value": {"id": 17983, "paramId": 28145}},
            {"name": "Reading", "value": {"id": 17776, "paramId": 27938}},
            {"name": "Romantic", "value": {"id": 17998, "paramId": 28160}}
          ]
        }
      }
    ]
  }
}
```
*Note: Response truncated - actual response contains 82 scenes including nature, holidays, moods, activities, and space themes.*

### 3.6 Activate a Scene

```bash
curl -s -X POST "$GOVEE_API/device/control" \
  -H "Govee-API-Key: $GOVEE_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "requestId": "scene-activate-001",
    "payload": {
      "sku": "H601F",
      "device": "03:9C:DC:06:75:4B:10:7C",
      "capability": {
        "type": "devices.capabilities.dynamic_scene",
        "instance": "lightScene",
        "value": {"id": 17937, "paramId": 28099}
      }
    }
  }' | jq .
```

### 3.7 Get DIY Scenes

```bash
curl -s -X POST "$GOVEE_API/device/diy-scenes" \
  -H "Govee-API-Key: $GOVEE_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "requestId": "diy-scenes-001",
    "payload": {
      "sku": "H601F",
      "device": "03:9C:DC:06:75:4B:10:7C"
    }
  }' | jq .
```

**Real Response:**
```json
{
  "requestId": "diy-scenes-001",
  "msg": "success",
  "code": 200,
  "payload": {
    "sku": "H601F",
    "device": "03:9C:DC:06:75:4B:10:7C",
    "capabilities": [
      {
        "type": "devices.capabilities.dynamic_scene",
        "instance": "diyScene",
        "parameters": {
          "dataType": "ENUM",
          "options": [
            {"name": "tj diy", "value": 21104832}
          ]
        }
      }
    ]
  }
}
```

### 3.8 RGB Color Values Reference

| Color | RGB | Packed Integer |
|-------|-----|----------------|
| Red | (255, 0, 0) | 16711680 |
| Green | (0, 255, 0) | 65280 |
| Blue | (0, 0, 255) | 255 |
| White | (255, 255, 255) | 16777215 |
| Yellow | (255, 255, 0) | 16776960 |
| Cyan | (0, 255, 255) | 65535 |
| Magenta | (255, 0, 255) | 16711935 |
| Orange | (255, 165, 0) | 16753920 |
| Purple | (128, 0, 128) | 8388736 |
| Pink | (255, 192, 203) | 16761035 |

**Python conversion:**
```python
def rgb_to_int(r, g, b):
    return (r << 16) + (g << 8) + b

def int_to_rgb(color_int):
    r = (color_int >> 16) & 0xFF
    g = (color_int >> 8) & 0xFF
    b = color_int & 0xFF
    return (r, g, b)
```

### 3.9 Device Type Notes

**H601F (Floor Lamp):**
- 7 addressable segments (0-6)
- Color temp range: 2700K - 6500K
- Brightness: 1-100%
- 82 dynamic scenes available
- Supports DIY scenes and snapshots

**SameModeGroup:**
- Virtual device for group control
- Only supports powerSwitch capability
- Cannot query state (no response)

---

## 4. AWS IoT MQTT (Undocumented)

This protocol provides real-time device state updates and is used by the Govee mobile app for instant synchronization.

### 3.1 Connection Details

| Parameter | Value |
|-----------|-------|
| **Endpoint** | `aqm3wd1qlc3dy-ats.iot.us-east-1.amazonaws.com` |
| **Port** | 8883 (MQTT over TLS) |
| **Authentication** | Mutual TLS with client certificates |
| **Keepalive** | 120 seconds |

*Endpoint confirmed via PCAP analysis. Multiple IPs observed (load-balanced):*
*98.88.204.61, 35.169.219.171, 13.223.152.107, 3.231.7.138*

### 3.2 Authentication Flow

```
┌──────────────────────────────────────────────────────────────────┐
│  1. Login to app2.govee.com                                       │
│     POST /account/rest/account/v2/login                          │
│     Body: { email, password, client }                            │
│     Returns: { token, accountId, topic }                         │
├──────────────────────────────────────────────────────────────────┤
│  2. Get IoT Credentials                                          │
│     GET /app/v1/account/iot/key                                  │
│     Header: Authorization: Bearer {token}                        │
│     Returns: { endpoint, p12, p12_pass } or PEM format           │
├──────────────────────────────────────────────────────────────────┤
│  3. Extract Certificates                                         │
│     Parse P12/PFX container (base64 decoded)                     │
│     Extract: client_cert.pem, client_key.pem                     │
│     Use Amazon Root CA 1 for server verification                 │
├──────────────────────────────────────────────────────────────────┤
│  4. Connect to AWS IoT                                           │
│     Client ID: AP/{accountId}/{uuid}                             │
│     Subscribe: GA/{account-topic-from-login}                     │
└──────────────────────────────────────────────────────────────────┘
```

### 3.3 Client ID Format

```
AP/{accountId}/{clientId}
```
- `accountId`: Numeric account ID from login response (as string)
- `clientId`: 32-character UUID (generated client-side)

### 3.4 Topic Structure

Two topic types are used:

| Prefix | Purpose | Example |
|--------|---------|---------|
| `GA/` | Account topic (receive state updates) | `GA/6e325aac784478097fe4a9c0fb4da9b3` |
| `GD/` | Device topic (send commands) | `GD/3c863a6df68bbbfb346997c964c84289` |

- Subscribe to `GA/` topic to receive state updates for all devices
- Publish commands to device-specific `GD/` topic
- Device topics obtained from `/device/rest/devices/v1/list` API

### 3.5 Message Formats

**Incoming State Update (Full Response):**

*Validated via live MQTT capture (January 2026):*

```json
{
  "proType": 2,
  "sku": "H601F",
  "device": "03:9C:DC:06:75:4B:10:7C",
  "softVersion": "1.00.24",
  "wifiSoftVersion": "1.00.24",
  "wifiHardVersion": "4.01.01",
  "cmd": "status",
  "type": 0,
  "transaction": "v_1769290509067",
  "pactType": 1,
  "pactCode": 2,
  "state": {
    "onOff": 1,
    "mode": 21,
    "brightness": 3,
    "color": { "r": 131, "g": 56, "b": 236 },
    "colorTemInKelvin": 0,
    "sta": { "stc": "7_3_61_3503820" },
    "result": 1
  },
  "op": {
    "command": [
      "qgUVAAAAAAAAAAAAAAAAAAAAALo=",
      "qqUBMoM47BSDOOwUgzjsFIM47Cg="
    ]
  }
}
```

**Response Fields:**

| Field | Description |
|-------|-------------|
| `proType` | Protocol type (2 = standard) |
| `sku` | Device model |
| `device` | Device MAC address |
| `softVersion` | Firmware version |
| `cmd` | Echo of command sent |
| `transaction` | Echo of transaction ID |
| `pactType`, `pactCode` | Protocol/packet codes |
| `state` | Current device state |
| `op.command` | BLE packet sequence (base64) |

**State Object Fields:**

| Field | Description |
|-------|-------------|
| `onOff` | Power state (0=off, 1=on) |
| `mode` | Current mode number |
| `brightness` | Brightness (1-100) |
| `color` | RGB color object |
| `colorTemInKelvin` | Color temperature |
| `sta.stc` | Status code string |
| `result` | Command result (1=success) |
| `triSta` | **Presence sensors (H5127)**: `1` = present, `0` = absent — see below |

**Sensor `state` fields (non-light devices).** Some sensor SKUs push their reading inside the same `cmd: "status"` envelope, in fields the generic light parser ignores. The **H5127 mmWave presence sensor** pushes presence over MQTT — the Developer `/device/state` poll only returns `online` for it — as:

```json
{
  "sku": "H5127",
  "device": "[MAC]",
  "cmd": "status",
  "state": { "triSta": 1, "sta": { "stc": "19_0_35_150940" }, "result": 1 },
  "op": { "command": ["..."] }
}
```

`state.triSta` (`1` present / `0` absent) maps to the Occupancy `binary_sensor` (device_class `occupancy`). Confirmed against the Govee app journal in issue #124 (fixed v2026.6.25). Note thermometers/CO2/AQI monitors (H5140, H5106, …) do **not** push their reading over MQTT at all — their `status` push carries only `onOff` + a `sta` block — so those values come from the REST poll only.

**op.command BLE Packets:**

The `op.command` array contains base64-encoded BLE packets representing device state. These use `0xAA` prefix (status packets) rather than `0x33` (command packets):

| Packet Prefix | Purpose |
|---------------|---------|
| `0xAA 0x05` | Mode/brightness info |
| `0xAA 0x07` | Sleep timer (hours, minutes) |
| `0xAA 0x13` | Current color RGB |
| `0xAA 0x23` | Segment configuration |
| `0xAA 0xA5` | Segment colors (4 RGB values per packet) |

**Outbound Commands:**

*Status Request:*
```json
{
  "msg": {
    "cmd": "status",
    "cmdVersion": 2,
    "transaction": "v_1704812400000",
    "type": 0
  }
}
```

*Power Control:*
```json
{
  "msg": {
    "cmd": "turn",
    "data": { "val": 1 },
    "cmdVersion": 0,
    "transaction": "v_1704812400000",
    "type": 1
  }
}
```

*Brightness Control:*
```json
{
  "msg": {
    "cmd": "brightness",
    "data": { "val": 75 },
    "cmdVersion": 0,
    "transaction": "v_1704812400000",
    "type": 1
  }
}
```

*Color Control:*
```json
{
  "msg": {
    "cmd": "colorwc",
    "data": {
      "color": { "r": 255, "g": 0, "b": 128 },
      "colorTemInKelvin": 0
    },
    "cmdVersion": 0,
    "transaction": "v_1704812400000",
    "type": 1
  }
}
```

*BLE Passthrough (ptReal):*
```json
{
  "msg": {
    "cmd": "ptReal",
    "data": {
      "command": ["MwUEzycAAAAAAAAAAAAAAAAAANo="]
    },
    "cmdVersion": 0,
    "transaction": "v_1704812400000",
    "type": 1
  }
}
```

### 3.6 Command Envelope Details

All MQTT commands follow this envelope:
```json
{
  "msg": {
    "cmd": "<command_name>",
    "data": { ... },
    "cmdVersion": <0|1|2>,
    "transaction": "v_{ms_timestamp}000",
    "type": <0|1>
  }
}
```

| Field | Values | Meaning |
|-------|--------|---------|
| `type` | 0 | Query/status request |
| `type` | 1 | Control command |
| `cmdVersion` | 0 | Standard (wez/govee2mqtt uses this) |
| `cmdVersion` | 1 | Alternate (TheOneOgre/govee-cloud, some device models) |
| `cmdVersion` | 2 | Status request default |
| `transaction` | `v_{epoch_ms}000` | Timestamp with 3 trailing zeros |

### 3.7 Command Variants and Fallbacks

**Color commands**: Two variants exist:
- `colorwc` (preferred): `{"color": {"r":N,"g":N,"b":N}, "colorTemInKelvin": N}` — combines RGB and CT
- `color` (legacy): `{"r":N,"g":N,"b":N}` — RGB only, `cmdVersion: 1`

Some devices only respond to `color`, not `colorwc`. The govee-cloud implementation tries `colorwc` first, watches for a state update confirmation on the GA/ topic within 5 seconds, then falls back to legacy `color` if no confirmation.

**Color temperature**: Similarly `colorwc` with `colorTemInKelvin > 0` vs legacy `colorTem` (percentage-based 0-100).

### 3.8 Device-Specific Quirks

| Device | Quirk |
|--------|-------|
| H5080, H5083 | Power values are `17` (on) / `16` (off) instead of `1` / `0` |
| H6121 | Needs `cmdVersion: 1` for status requests (not `cmdVersion: 2`) |
| Some older devices | Only respond to `color` cmd, not `colorwc` |

### 3.9 State Update op Fields

The `op` object in state updates contains base64-encoded BLE packets for device-specific data:

| Field | Purpose |
|-------|---------|
| `command` | General BLE command/status responses |
| `modeValue` | Current mode setting (e.g., scene, music mode) |
| `sleepValue` | Sleep timer data |
| `wakeupValue` | Wake-up timer data |
| `timerValue` | Timer schedule data |

### 3.10 Important: Do Not Subscribe to Device Topics

Subscribing to individual device topics (`GD/...`) causes the AWS IoT server to close the connection. Only subscribe to the account topic (`GA/...`). All state updates for all devices arrive on the account topic.

### 3.11 PCAP Traffic Analysis

From PCAP analysis (January 2026 captures):

| Metric | Jan 24 Capture | Jan 9 Capture |
|--------|----------------|---------------|
| Session Duration | 679 seconds | 296 seconds |
| Total Packets | 761 | 253 |
| Data Transferred | 200,786 bytes | 64,307 bytes |
| Outbound Packets | 377 | 61 |
| Inbound Packets | 384 | 78 |
| Avg Packet Size (Out) | 300 bytes | 205 bytes |
| Avg Packet Size (In) | 573 bytes | 700-2000 bytes |

**Timing Patterns:**
- State updates: <1 second round-trip
- Average message gap: ~4-5 seconds during activity
- Max idle period: 151 seconds (connection maintained)
- Multiple AWS IoT IPs used (load balancing)

---

## 5. Undocumented Internal API (app2.govee.com)

Used by the Govee mobile app for extended functionality not available in the public API.

### 4.1 Base Configuration

| Parameter | Value |
|-----------|-------|
| **Base URL** | `https://app2.govee.com` |
| **User-Agent** | `GoveeHome/7.4.10 (com.ihoment.GoVeeSensor; build:2; iOS 18.4.0) Alamofire/5.10.2` |

### 4.2 Authentication

**Login Request:**
```http
POST /account/rest/account/v2/login
Content-Type: application/json
appVersion: 7.4.10
clientId: {uuid}
clientType: 1
iotVersion: 0
timestamp: {epoch_ms}
User-Agent: GoveeHome/7.4.10 (com.ihoment.GoVeeSensor; build:2; iOS 18.4.0) Alamofire/5.10.2

{
  "email": "user@example.com",
  "password": "password123",
  "client": "550e8400-e29b-41d4-a716-446655440000"
}
```

> **Note:** The v1 endpoint (`/account/rest/account/v1/login`) is deprecated. Always use v2.

### Two-Factor Authentication (2FA)

Since March 2026, Govee requires email verification for some account logins. The login
endpoint returns JSON `{"status": 454}` (HTTP 200) when a verification code is required.

**Step 1 — Login returns 454 (2FA required):**
```json
{"status": 454, "message": ""}
```

**Step 2 — Request verification code:**
```http
POST /account/rest/account/v1/verification
Content-Type: application/json
appVersion: 7.4.10
clientId: {same-uuid-as-login}
clientType: 1
iotVersion: 0
timestamp: {epoch_ms}
User-Agent: GoveeHome/7.4.10 (com.ihoment.GoVeeSensor; build:2; iOS 18.4.0) Alamofire/5.10.2

{
  "type": 8,
  "email": "user@example.com"
}
```

Govee sends a 4-digit code to the user's email. Code expires in ~15 minutes.

**Step 3 — Retry login with code:**
```http
POST /account/rest/account/v2/login

{
  "email": "user@example.com",
  "password": "password123",
  "client": "550e8400-e29b-41d4-a716-446655440000",
  "code": "1234"
}
```

On success, returns the normal login response. On invalid/expired code, returns `{"status": 454}` again.

> **Important:** The `clientId` header and `client` payload field must be the same UUID across
> the initial login, verification request, and retry-with-code.

**Login Response:**
```json
{
  "status": 200,
  "message": "Login successful",
  "client": {
    "A": "encrypted_value",
    "B": "encrypted_value",
    "accountId": 12345678,
    "client": "550e8400-e29b-41d4-a716-446655440000",
    "token": "eyJhbGciOiJIUzI1NiIs...",
    "tokenExpireCycle": 604800,
    "topic": "GA/a1b2c3d4-e5f6-7890-abcd-ef1234567890"
  }
}
```

**Authenticated Request Headers:**
```http
Authorization: Bearer {token}
appVersion: 7.4.10
clientId: {uuid}
clientType: 1
iotVersion: 0
timestamp: 1704812400000
User-Agent: GoveeHome/7.4.10 (com.ihoment.GoVeeSensor; build:2; iOS 18.4.0) Alamofire/5.10.2
```

### 4.3 Key Endpoints

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/account/rest/account/v2/login` | POST | User login |
| `/account/rest/v1/first/refresh-tokens` | POST | Refresh auth tokens |
| `/app/v1/account/iot/key` | GET | Get AWS IoT credentials |
| `/device/rest/devices/v1/list` | POST | List devices with full details |
| `/device/rest/devices/v1/control` | POST | Control devices |
| `/appsku/v1/light-effect-libraries` | GET | Get scene catalog |
| `/appsku/v2/devices/scenes/attributes` | GET | Get scene attributes |
| `/appsku/v1/diys/groups-diys` | GET | Get DIY scenes |
| `/bff-app/v1/exec-plat/home` | GET | Get One-Click/Tap-to-Run |
| `/bff-app/v1/device/list` | POST | **Account device list (BFF)** — richest per-device state; see §4.3b |

### 4.3b Account Device List (BFF) — `deviceExt` scalar catalog

`POST https://app2.govee.com/bff-app/v1/device/list` (constant `GOVEE_BFF_DEVICE_LIST_URL`) returns the account's full device list with a `deviceExt` block per device. This is the **only** source for several values the Developer (API-key) API never exposes, so it requires **email/password** login, not just an API key. Each `deviceExt` sub-object is a JSON **string** under a `_json_str` key that must be re-parsed.

```
data.devices[].deviceExt
  ├── deviceSettings._json_str   → static/config scalars (battery, thresholds, gatewayInfo)
  ├── lastDeviceData._json_str   → last cached reading (online, tem/hum, or logType/logTime)
  └── extResources._json_str     → skuUrl, ic
```

**`deviceSettings` scalars the integration reads:**

| Field | Type | Meaning / use |
|-------|------|---------------|
| `battery` | int % | Battery level for battery-powered sensors (thermo-hygrometers, leak sensors, buttons, motion). **Only present here, never in the Developer API.** |
| `wifiLevel` | int | WiFi signal bars (0–4) |
| `waterFull` | int | Dehumidifier water-tank-full flag (`1` = full) — see H7150/H7152, only reliable source |
| `fahOpen` | bool | Device's Fahrenheit display toggle (informational — API still returns the value unit-less) |
| `temCali` / `humCali` | int | Calibration offsets |
| `temMin`/`temMax`, `humMin`/`humMax`, `pm25Min`/`pm25Max`, `co2Min`/`co2Max` | int | Warning thresholds (centi-scaled where applicable) |
| `comfortTemMin`/`Max`, `comfortHumMin`/`Max` | int | Comfort range |
| `gatewayId`, `gatewayInfo{sku,versions,...}` | — | Present on gateway-bridged sub-sensors (hub = H5044 etc.) |
| `sno` | int | Sensor slot on the hub — keys the `(hub, slot) → sensor` map |

**`lastDeviceData` shapes (varies by device class):**

- **Thermo-hygrometer** (e.g. H5110, H5310): `{online, tem, hum, lastTime, avgDayTem, avgDayHum}` — `tem`/`hum` are the last cached reading (scale is centi/tenths and **varies by gateway firmware**, see #102).
- **Accessory event devices**: `{online, bind, logType, logTime}` — a "last event" pointer, **not** a live state. H5126 button: `logType 7` = button 1, `8` = button 2. H5129 motion: `logType 1` = last motion.
- **Appliances / most others**: `{online}` only.

**Quirks (confirmed 2026-06):**

- **Phantom battery on mains-powered devices.** Mains devices (e.g. **H5106** air-quality monitor, H7152) report a constant `battery: 100` in `deviceSettings`. The integration suppresses battery for these (by device_type **and** by SKU allow-list, since some mains SKUs aren't a mains device_type) — see #114/#125.
- **Battery flicker.** The account list refreshes on Govee's own cadence and can momentarily omit `battery`, so the value is preserved (last-known) across cloud polls rather than flipping to `unknown` (#125).
- **No live sensor readings.** The BFF does **not** carry current temp/humidity for dehumidifiers (H7150/H7152) or a real PM2.5 for the H5106 — those are BLE-only in the Govee app (#114, #118).
- Full field catalog and per-SKU examples: `docs/_research/2026-06-30_30day-issue-sweep.md`.

### 4.4 Scene Library Request

```http
GET /appsku/v1/light-effect-libraries?sku=H6072
AppVersion: 7.3.30
```

**Response:**
```json
{
  "data": {
    "categories": [
      {
        "categoryId": 1,
        "categoryName": "Dynamic",
        "scenes": [
          {
            "sceneId": 130,
            "sceneName": "Forest",
            "sceneCode": 10191,
            "sceneType": 1,
            "lightEffects": [
              {
                "scenceParamId": 123,
                "scenceParam": "base64-encoded-animation-data"
              }
            ]
          }
        ]
      },
      {
        "categoryId": 2,
        "categoryName": "Cozy",
        "scenes": [...]
      }
    ]
  }
}
```

### 4.5 Rate Limits

- Login: 30 attempts per 24 hours
- API calls: Undocumented, but appears generous

---

## 6. LAN API (UDP)

Local network control without cloud dependency. Must be enabled in Govee app device settings.

### 5.1 Network Configuration

| Parameter | Value |
|-----------|-------|
| **Multicast Address** | `239.255.255.250` |
| **Discovery Port** | 4001 (device listens) |
| **Response Port** | 4002 (client listens) |
| **Command Port** | 4003 (device listens) |
| **Protocol** | UDP |

### 5.2 Device Discovery

**Scan Request (to 239.255.255.250:4001):**
```json
{
  "msg": {
    "cmd": "scan",
    "data": {
      "account_topic": "reserve"
    }
  }
}
```

**Scan Response (device → multicast `239.255.255.250:4002`):**

> ⚠️ The device sends its reply as **multicast** to the group on port 4002, NOT
> as a unicast back to the requester. The listening socket **must join the
> `239.255.255.250` group** (`IP_ADD_MEMBERSHIP`) or the kernel drops every
> reply — binding port 4002 alone yields zero devices (issue #57). On a
> multi-homed host, join (and emit the scan) on each enabled interface. In a
> Docker bridge container, the multicast never crosses the namespace, so host
> networking is required.

> 🆕 **Broadcast fallback (newer devices).** Some newer models (e.g. H707B)
> silently ignore the multicast scan but DO answer a scan sent to the subnet's
> directed-broadcast address (`x.x.x.255:4001`) — reported on issue #57. The
> integration now derives each enabled adapter's broadcast address from Home
> Assistant's network adapter list (`async_get_lan_broadcast_addresses`) and
> emits the scan there **in addition to** the multicast, so these devices are
> discovered without any manual configuration. Cross-VLAN devices the broadcast
> can't reach still need an explicit address in the **LAN device addresses**
> option. The periodic rescan (every `LAN_RESCAN_INTERVAL` = 300 s) re-emits the
> broadcast scan, which also re-primes devices whose firmware requires a recent
> scan before accepting local control on `:4003`.

```json
{
  "msg": {
    "cmd": "scan",
    "data": {
      "ip": "192.168.1.23",
      "device": "1F:80:C5:32:32:36:72:4E",
      "sku": "H618E",
      "bleVersionHard": "3.01.01",
      "bleVersionSoft": "1.03.01",
      "wifiVersionHard": "1.00.10",
      "wifiVersionSoft": "1.02.03"
    }
  }
}
```

### 5.3 Control Commands (to device-ip:4003)

**Power Control:**
```json
{"msg": {"cmd": "turn", "data": {"value": 1}}}
```

**Brightness:**
```json
{"msg": {"cmd": "brightness", "data": {"value": 75}}}
```

**Color/Temperature:**
```json
{
  "msg": {
    "cmd": "colorwc",
    "data": {
      "color": {"r": 255, "g": 0, "b": 128},
      "colorTemInKelvin": 0
    }
  }
}
```

**Status Query:**
```json
{"msg": {"cmd": "devStatus", "data": {}}}
```

**Status Response:**
```json
{
  "msg": {
    "cmd": "devStatus",
    "data": {
      "onOff": 1,
      "brightness": 100,
      "color": {"r": 255, "g": 0, "b": 0},
      "colorTemInKelvin": 0
    }
  }
}
```

> **Readable vs writable over LAN.** Per the reference libraries
> (`Galorhallen/govee-local-api` + `wez/govee2mqtt`), `devStatus` returns only
> four runtime fields — `onOff`, `brightness` (0–100), `color {r,g,b}`
> (whole-device), `colorTemInKelvin` — and `turn`/`brightness`/`colorwc`/`ptReal`
> are write-only (no response body). **But those libraries only parse what they
> need — they are not ground truth.** The `lan_discovery` diagnostics block runs
> a *reality probe*: it fires a read-only query battery (`devStatus` + the
> never-used-by-libs `status` cmd + a unicast `scan`) at each discovered device
> and captures **every** datagram it emits, completely unfiltered (whole payload,
> any cmd, any field). The goal is to measure on real hardware what the firmware
> actually exposes — e.g. whether the `status` reply's `pt` BLE-passthrough hex
> carries segment/scene/sensor state that the 4-field `devStatus` omits — rather
> than inherit another integration's parser. No write/control verb is ever sent.
> Captured addresses are value-scrubbed (MAC→hash, IPv4→`REDACTED_IP`) on top of
> key-name redaction, since the capture keeps unknown keys (issue #57).

### 5.4 BLE Passthrough (ptReal)

Send BLE commands through WiFi for devices supporting it:

```json
{
  "msg": {
    "cmd": "ptReal",
    "data": {
      "command": ["MwUEzycAAAAAAAAAAAAAAAAAANo="]
    }
  }
}
```

### 5.5 Supported Devices

Devices with confirmed LAN API support:
- H619Z, H6072, H619C, H7060, H619B
- H6066, H619D, H619E, H61A1, H61A3
- H61A2, H618A, H619A, H61A0, H6110
- H6117, H6159, H6163, H6141, H6052
- H6144, H615A, H6056, H6143, H6076
- H6062, H6061, and more

### 5.6 Python Implementation

```python
import socket
import json

MULTICAST_GROUP = '239.255.255.250'
DISCOVERY_PORT = 4001
COMMAND_PORT = 4003

def discover_devices(timeout=5):
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.settimeout(timeout)

    # Bind to the response port
    sock.bind(('', 4002))

    # REQUIRED: join the multicast group, else the kernel drops the replies
    # (devices answer via multicast to 239.255.255.250:4002, not unicast).
    # On a multi-homed host, join on each interface IP instead of INADDR_ANY.
    mreq = socket.inet_aton(MULTICAST_GROUP) + socket.inet_aton('0.0.0.0')
    sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)

    message = json.dumps({
        "msg": {"cmd": "scan", "data": {"account_topic": "reserve"}}
    }).encode()

    sock.sendto(message, (MULTICAST_GROUP, DISCOVERY_PORT))

    devices = []
    while True:
        try:
            data, addr = sock.recvfrom(1024)
            devices.append(json.loads(data.decode()))
        except socket.timeout:
            break

    sock.close()
    return devices

def send_command(device_ip, cmd, data):
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    message = json.dumps({"msg": {"cmd": cmd, "data": data}}).encode()
    sock.sendto(message, (device_ip, COMMAND_PORT))
    sock.close()

# Example usage
devices = discover_devices()
for d in devices:
    ip = d['msg']['data']['ip']
    send_command(ip, "turn", {"value": 1})  # Turn on
    send_command(ip, "brightness", {"value": 50})  # 50% brightness
```

---

## 7. BLE Protocol

Direct Bluetooth Low Energy control for devices without WiFi or for local-only operation.

### 6.1 Service Configuration

| Parameter | Value |
|-----------|-------|
| **Service UUID** | `00010203-0405-0607-0809-0a0b0c0d1910` |
| **Write Characteristic** | `00010203-0405-0607-0809-0a0b0c0d2b11` (H6127, H6199) |
| **Write Characteristic (alt)** | `00010203-0405-0607-0809-0a0b0c0d2b10` (H615B) |
| **Notify Characteristic** | `00010203-0405-0607-0809-0a0b0c0d2b11` (H615B) |

> **Note:** The write characteristic UUID differs between models (`0x2b10` vs `0x2b11`).
> No BLE authentication is required — any device can send commands.

### 6.2 Packet Structure

All commands are **20 bytes** with XOR checksum:

```
┌──────────┬─────────┬──────────┬──────────────────┬──────────┐
│ ID (1B)  │ Cmd(1B) │ Mode(1B) │ Data (16B)       │ XOR (1B) │
└──────────┴─────────┴──────────┴──────────────────┴──────────┘
```

### 6.3 Identifier Bytes

| Byte | Purpose |
|------|---------|
| `0x33` | Standard command (outbound) |
| `0xAA` | Status/state data (in MQTT responses) |
| `0xA1` | DIY mode data |
| `0xA3` | Multi-packet/scene data |

### 6.3b Keep-Alive

Sent every 2 seconds to maintain BLE connection:
```
AA 01 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 AB
```

### 6.4 Command Types (0x33 prefix)

| Command | Byte | Description |
|---------|------|-------------|
| Power | `0x01` | On/Off control |
| Brightness | `0x04` | Brightness level (0-255) |
| Color/Mode | `0x05` | Color and mode operations (sub-command in byte 3) |
| Segment | `0x0B` | Segment control |
| Gradient | `0x14` | Gradient toggle |
| Scene | `0x21` | Scene activation |
| Nightlight | `0x36` | Nightlight mode toggle (confirmed) |

**Color/Mode sub-commands (byte 3 of 0x33 0x05 packets):**

| Byte 3 | Description | Models |
|--------|-------------|--------|
| `0x00` | Video/DreamView mode | H6199 |
| `0x01` | Music mode | H6127 |
| `0x02` | Manual color | H6127: `33 05 02 RR GG BB ...` |
| `0x04` | Scene preset | All: `33 05 04 [scene_code] ...` |
| `0x0A` | DIY custom animation | All |
| `0x0B` | Segment color + color temp | H6199: `33 05 0B RR GG BB [CT_HI CT_LO] [SEG_L SEG_R] ...` |
| `0x0C` | Music mode | H6199 variant |
| `0x0D` | Manual color | H615B: `33 05 0D RR GG BB ...` |

> **Important:** The color sub-command byte differs between models (0x02 for H6127, 0x0B for H6199, 0x0D for H615B). Device-specific codec selection is required.

**Nightlight Command (0x33 0x36):**

*Discovered via live MQTT capture during rapid OFF→ON sequence:*

```
33 36 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 05
```

- **Physical trigger required**: Nightlight activates ONLY via rapid OFF→ON within ~2 seconds
- Value byte [2]: `0x00` observed during capture
- Device handles this locally; mode may not change in cloud state

**MQTT Activation Testing (January 2026):**

Attempted remote activation via MQTT `ptReal` commands:
- Sent `0x33 0x36 0x01` (nightlight enable) - device acknowledged but no activation
- Sent `0x33 0x14 0x01` (gradient toggle) - device acknowledged but no effect
- Sent high-level `nightLight` / `nightlightToggle` commands - device acknowledged

**Conclusion:** The nightlight feature cannot be activated remotely via MQTT or cloud API.
It requires the physical power cycling trigger (OFF→ON within 2 seconds). The device
firmware appears to only accept this mode change from local state transitions.

### 6.4.1 Status Packet Types (0xAA prefix, in MQTT responses)

*Discovered via live MQTT capture (January 2026):*

| Sub-byte | Purpose | Data Format |
|----------|---------|-------------|
| `0x05` | Mode info | `[mode_byte]` |
| `0x07` | Sleep timer | `[hours, minutes]` |
| `0x11` | Settings | Device settings packet |
| `0x12` | Extended settings | Additional config |
| `0x13` | Current color | `[?, R, G, B]` |
| `0x23` | Segment config | Segment enable flags |
| `0x26` | Status flags | General status |
| `0xA5` | Segment colors | 4 RGB triplets per packet |

### 6.4.2 Multi-Packet Types (0xA3 prefix)

| Sub-byte | Purpose |
|----------|---------|
| `0x02` | Scene end/cancel |
| `0x0A` | Scene parameter |
| `0x58` | Scene selection |

### 6.4.3 Segment Color Encoding (0xAA 0xA5)

RGBIC devices report segment colors in `op.command` array:

```
AA A5 [group] [R1 G1 B1] [R2 G2 B2] [R3 G3 B3] [R4 G4 B4] ... [checksum]
```

- Group 0x01: Segments 0-3
- Group 0x02: Segments 4-6 (last slot may be zeros)

Example (7-segment device):
```
aaa50114ffd66427ffd66427ffd66414ffd6640e  → Seg 0-3: RGB(20,255,214), RGB(100,39,255), ...
aaa50214ffd66414ffd66427ffd6640000000067  → Seg 4-6: RGB(20,255,214), RGB(100,20,255), ...
```

### 6.5 Checksum Calculation

```python
def calculate_checksum(data: list[int]) -> int:
    """XOR all bytes together"""
    checksum = 0
    for byte in data:
        checksum ^= byte
    return checksum & 0xFF

def build_packet(data: list[int]) -> bytes:
    """Build 20-byte packet with checksum"""
    packet = list(data)
    while len(packet) < 19:
        packet.append(0x00)
    packet.append(calculate_checksum(packet))
    return bytes(packet)
```

### 6.6 Command Examples

**Power On:**
```
33 01 01 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 33
```

**Power Off:**
```
33 01 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 32
```

**Brightness (50% = 0x80):**
```
33 04 80 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 B7
```

**RGB Color (Manual Mode):**
```
33 05 02 [R] [G] [B] 00 00 00 00 00 00 00 00 00 00 00 00 00 [XOR]
```

**Enable Gradient:**
```
33 14 01 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 26
```

### 6.7 Color Mode Bytes (after 0x05)

| Byte | Mode |
|------|------|
| `0x02` | Manual RGB |
| `0x01` | Music mode |
| `0x04` | Scene mode |
| `0x05` | Preset scenes |
| `0x0A` | DIY mode |
| `0x0B` | Segment color |

### 6.8 Scene Activation

```
33 05 04 [SceneCode_Low] [SceneCode_High] 00...00 [XOR]
```

Scene codes from the scene library API are split little-endian:
- Scene code 10191 = 0x27CF
- Packet: `33 05 04 CF 27 00...00 [XOR]`

### 6.9 Keep-Alive

Send every 2 seconds to maintain connection:
```
AA 01 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 AB
```

### 6.10 Python Implementation

```python
import asyncio
from bleak import BleakClient

WRITE_UUID = "00010203-0405-0607-0809-0a0b0c0d2b11"

def build_packet(data: list[int]) -> bytes:
    packet = list(data)
    while len(packet) < 19:
        packet.append(0x00)
    checksum = 0
    for b in packet:
        checksum ^= b
    packet.append(checksum)
    return bytes(packet)

async def control_light(address: str):
    async with BleakClient(address) as client:
        # Power on
        await client.write_gatt_char(
            WRITE_UUID,
            build_packet([0x33, 0x01, 0x01])
        )

        # Set brightness to 50%
        await client.write_gatt_char(
            WRITE_UUID,
            build_packet([0x33, 0x04, 0x80])
        )

        # Set color to red
        await client.write_gatt_char(
            WRITE_UUID,
            build_packet([0x33, 0x05, 0x02, 0xFF, 0x00, 0x00])
        )

asyncio.run(control_light("AA:BB:CC:DD:EE:FF"))
```

### 6.7 Cross-Protocol Relationship

The BLE 20-byte packet format is the canonical low-level protocol. MQTT and LAN tunnel these packets via `ptReal` commands for anything beyond basic operations:

| Operation | REST API | MQTT (direct) | MQTT (ptReal) | LAN (direct) | LAN (ptReal) | BLE |
|-----------|----------|---------------|----------------|---------------|---------------|-----|
| Power | Yes | `turn` | - | `turn` | - | `0x33 0x01` |
| Brightness | Yes | `brightness` | - | `brightness` | - | `0x33 0x04` |
| Color | Yes | `colorwc` | - | `colorwc` | - | `0x33 0x05 0x02` |
| Color Temp | Yes | `colorwc` | - | `colorwc` | - | `0x33 0x05 0x0B` |
| Scenes | Yes | - | Yes | - | Yes | `0x33 0x05 0x04` |
| DIY/Segments | Yes | - | Yes | - | Yes | `0x33 0x05 0x0B` |
| Music Mode | Yes | - | Yes | - | Yes | `0x33 0x05 0x01` |

`ptReal` commands wrap base64-encoded BLE packets, making BLE the universal escape hatch for features not supported by the high-level JSON commands.

### 6.8 Probe Thermometers (H5192)

Two-probe cooking thermometers use the same 20-byte packet format as the light
strips, but with their own registers and — unlike every other device in this
document — a **pull model**: the H5192 never volunteers a reading. It answers
a read request and otherwise stays silent, which is why the Govee app shows
"not connected" for a second when a device page opens, and why its history
graph takes a moment to populate. The app is asking, not listening.

Everything below was captured from an H5192 on AWS IoT and cross-checked
against what the app displayed at the same moment.

#### Frame identification

Packets arrive base64-encoded in `op.command[]` of a `status` or `ptReal`
message, split across one or two blocks that must be concatenated before
decoding. **Only byte 1 identifies the frame.** Byte 0 was observed as `0x40`,
`0x42`, `0x44`, `0x45` and `0x47` on frames that were otherwise identical, so
matching on it drops readings at random.

| byte 1 | Direction | Meaning |
|--------|-----------|---------|
| `0x0F` | device to app | Full status: both probes, readings and limits |
| `0x24` | both | Live reading for one probe (`0xAA` read, `0x33` push) |
| `0x12` | both | Alarm limits for one probe |

The `0xAA` / `0x33` prefix in byte 0 of a *request* follows the usual
convention: `0xAA` reads, `0x33` writes. Byte 2 is the probe number (1 or 2).

#### Temperature encoding

Every temperature is a **signed 16-bit big-endian value in hundredths of a
degree Celsius**: `0x0BB8` = 30.00 °C, `0xFF38` = -2.00 °C.

`0xFFFF` is a sentinel, not a temperature. It means "probe not plugged in" for
a reading and "limit not set" for a corridor bound. It has to be checked
**against the unsigned word, before the signed conversion** — as a signed value
it is -1, which is a perfectly plausible temperature for a fridge probe and
would be shown as -0.01 °C.

#### Status frame (`0x0F`)

Volunteered on power-on and when an app session opens. No request was found
that provokes it. Six values per probe, probe 1 at offset 10, probe 2 at
offset 26 of the concatenated 40-byte payload:

| Offset (probe 1) | Field |
|---|---|
| 10 | core temperature |
| 12 | core max (alarm high) |
| 14 | core min (alarm low) |
| 16 | ambient temperature |
| 18 | ambient max |
| 20 | ambient min |

Probe 2 repeats the same order 16 bytes later.

#### Live reading (`0x24`)

Read request: `AA 24 <probe> 00 …` padded to 19 bytes plus checksum.
The reply carries the core temperature at offset 22 and the ambient
temperature at offset 24 of the concatenated payload.

The device also pushes this frame unprompted with prefix `0x33` while an app
session is open, which is where the two-block split is most visible.

#### Alarm limits (`0x12`)

Read request: `AA 12 <probe>` padded to 19 bytes plus checksum. The reply
carries four values starting at offset 3, in the order core max, core min,
ambient max, ambient min.

**Byte 11 is a flag, not padding.** It reads `0xFF` while the probe has no
limit set at all and `0xEE` as soon as at least one is set. It does not encode
which limit or how many — captured across all four states on an H5192 running
firmware 1.00.81. Bytes 12-13 were `FF FF` in every capture.

Writing uses the same layout with the `0x33` prefix. **There is no partial
update** — a write carries all four values, so the three that are not being
changed have to be supplied from the last read.

`0xFFFF` is valid on a write and means "no limit". This is not a guess: a probe
with no alarms configured reports all four as `0xFFFF`, and clearing an alarm in
the Govee app puts them back to it. Setting a single limit out of that state —
the normal first-use case — therefore means writing the sentinel for the other
three, and the device accepts it and keeps them unset. An implementation that
refuses to write while a value is unknown makes the first limit unsettable,
because every probe starts with all four unset.

#### Checksum

Same as the rest of the BLE protocol: byte 19 is the XOR of bytes 0 through 18.

#### Consequences for an integration

- The device's BFF `lastDeviceData` stays `{"online": false}` permanently.
  Availability must not be gated on it.
- Nothing arrives without a request, so the poll interval *is* the update rate.
  Polling a battery device around the clock is wasteful, so this integration
  puts the poll behind an explicit per-device switch that is off by default.
- The generic BFF thermometer refresh must skip these devices. It rebuilds
  state from `create_empty()` and copies only temperature, humidity and
  battery, which would drop the probe readings on every pass.
- Light strips push their own `0xAA 0x05`, `0xAA 0x13` and `0xAA 0xA5` packets
  through the same `op.command` field. Frame dispatch has to be gated on the
  SKU, or a segment-color packet gets decoded as a probe reading.

---

## 8. State Management

### 7.1 State Sources

| Source | Update Method | Latency | Completeness |
|--------|--------------|---------|--------------|
| API Polling | HTTP GET | 2-4s | Full state |
| AWS IoT MQTT | Push | ~50ms | Full state |
| Official MQTT | Push | ~100ms | Events only |
| LAN Status | UDP request | <10ms | Basic state |
| Optimistic | Assumed | 0ms | Command only |

### 7.2 State Fields

```typescript
interface DeviceState {
  // Core state
  onOff: 0 | 1;
  brightness: number;  // 0-100

  // Color state (mutually exclusive with colorTemp)
  color?: {
    r: number;  // 0-255
    g: number;  // 0-255
    b: number;  // 0-255
  };

  // Color temperature (mutually exclusive with color)
  colorTemInKelvin?: number;  // 2000-9000

  // Mode state
  mode?: string;
  scene?: {
    id: number;
    name: string;
  };

  // Segment state (RGBIC devices)
  segments?: Array<{
    index: number;
    color: { r: number; g: number; b: number };
    brightness: number;
  }>;
}
```

### 7.3 Optimistic Updates

After sending a command, update local state immediately:

```python
class StateManager:
    def __init__(self):
        self.confirmed_state = {}
        self.pending_state = {}

    async def send_command(self, device_id, command):
        # Apply optimistic update
        self.pending_state[device_id] = {
            **self.confirmed_state.get(device_id, {}),
            **command
        }

        # Send command
        await api.control_device(device_id, command)

        # Wait for confirmation via MQTT or poll
        # On confirmation, merge to confirmed_state
```

### 7.4 Conflict Resolution

When optimistic state conflicts with confirmed state:

1. **Timestamp-based**: Prefer most recent update
2. **Source priority**: MQTT > API Poll > Optimistic
3. **Attribute-specific**: Only update changed attributes

### 7.5 Known Limitations

These states are NOT returned by the API:
- Active music mode settings
- Night light mode status
- Gradient mode toggle
- Individual segment colors (RGBIC)
- Active scene name/ID

Thermometer / sensor reading behavior (confirmed in issue #83 from v2026.5.13 + v2026.5.16 diagnostics; independently corroborated by govee2mqtt's per-SKU quirk database):
- **AWS IoT MQTT carries no thermometer data.** Even when a thermometer's `transport` reports `mqtt: true` and the AWS IoT session is connected, its `last_mqtt_message` is `null` and `mqtt.last_success` is `null` — temp/humidity readings arrive **only via REST polling**. The integration subscribes to a single account-wide MQTT topic (not per-device), so no per-thermometer subscription is missing; Govee simply never publishes these readings. govee2mqtt flags the same SKUs `iot_api_supported: false` / `ble_only: true` and polls the Platform (REST) API for them (govee2mqtt [#163](https://github.com/wez/govee2mqtt/issues/163), [#308](https://github.com/wez/govee2mqtt/issues/308)).
- **Readings refresh on Govee's cloud cadence, not the poll interval.** A value can look "frozen" while polling is healthy because it's the latest value Govee *has*. The refresh is poll-driven and gated by how often the device pushes to Govee's cloud — **observed, not guaranteed**:
  - WiFi-native thermometers (H5179): order of ~10 min.
  - BLE sensors via an H5151 gateway (H5075, H5110): gateway batch-uploads infrequently — often many minutes (observed ~15–60 min; not a fixed constant).
  - For real-time (~2 s) readings, use HA's first-party `govee_ble` integration or an ESPHome Bluetooth proxy — the cloud path cannot beat this.
- **Offline devices return empty strings.** Govee's cloud returns `""` (not `null`/`0`) for capability values when a device is offline; numeric parsers must tolerate `""` (an unguarded `int("")` raised `ValueError` and aborted the whole device fetch — fixed v2026.5.15). Independently reproduced in govee2mqtt [#308](https://github.com/wez/govee2mqtt/issues/308) (`sensorTemperature` → `"value": ""`).
- **Fahrenheit returned without a unit field.** Govee's API returns the reading in the device's app-configured unit (often °F) with no unit metadata; the consumer must convert. Same behavior seen in govee2mqtt [#206](https://github.com/wez/govee2mqtt/issues/206). See the thermometer profiles in §8.5 for the `api_temperature_unit` option.

---

## 9. Device Capabilities

### 8.1 Capability Types

| Type | Description |
|------|-------------|
| `devices.capabilities.on_off` | Power control |
| `devices.capabilities.toggle` | Feature toggles |
| `devices.capabilities.range` | Ranged values (brightness) |
| `devices.capabilities.color_setting` | Color/temp control |
| `devices.capabilities.segment_color_setting` | RGBIC segment control |
| `devices.capabilities.dynamic_scene` | Scene selection |
| `devices.capabilities.diy_color_setting` | DIY scenes |
| `devices.capabilities.music_setting` | Music mode |
| `devices.capabilities.movie_setting` | Movie/DreamView mode (H66A0) |
| `devices.capabilities.temperature_setting` | Temperature control (heaters) |
| `devices.capabilities.work_mode` | Appliance modes (fans, heaters, purifiers) |
| `devices.capabilities.mode` | Simple mode selection (HDMI source, purifier mode) |
| `devices.capabilities.property` | Read-only sensor/status data (thermometers, purifiers, CO2 monitors) |
| `devices.capabilities.online` | Online status |
| `devices.capabilities.event` | Real-time events (water full, ice full, lack water, leak detection) |

### 8.2 Instance Names

| Capability | Instances |
|------------|-----------|
| on_off | `powerSwitch` |
| toggle | `gradientToggle`, `nightlightToggle`, `oscillationToggle`, `warmMistToggle`, `dreamViewToggle`, `fanToggle` (ceiling fans), `reverseAirflowToggle` |
| range | `brightness`, `humidity`, `volume`, `targetTemperature`, `temperature`, `fanSpeed` |
| color_setting | `colorRgb`, `colorTemperatureK` |
| segment_color_setting | `segmentedColorRgb`, `segmentedBrightness` |
| dynamic_scene | `lightScene`, `diyScene`, `snapshot` |
| music_setting | `musicMode` |
| movie_setting | `movieMode` |
| temperature_setting | `targetTemperature` |
| work_mode | `workMode` |
| mode | `hdmiSource`, `purifierMode`, `nightlightScene`, `fanSpeedMode` (ceiling fans) |
| property | `sensorTemperature`, `sensorHumidity`, `carbonDioxideConcentration`, `filterLifetime`, `airQuality` |
| event | `lackWaterEvent`, `iceFullEvent`, `waterFullEvent`, `bodyAppearedEvent` (leak sensors) |

### 8.3 Device Type Detection

```python
def detect_capabilities(device_response):
    capabilities = device_response.get("capabilities", [])

    features = {
        "power": False,
        "brightness": False,
        "color": False,
        "color_temp": False,
        "segments": False,
        "scenes": False,
        "music": False,
    }

    for cap in capabilities:
        cap_type = cap.get("type", "")
        instance = cap.get("instance", "")

        if "on_off" in cap_type:
            features["power"] = True
        elif "range" in cap_type and instance == "brightness":
            features["brightness"] = True
        elif "color_setting" in cap_type:
            if instance == "colorRgb":
                features["color"] = True
            elif instance == "colorTemperatureK":
                features["color_temp"] = True
        elif "segment_color" in cap_type:
            features["segments"] = True
        elif "dynamic_scene" in cap_type:
            features["scenes"] = True
        elif "music_setting" in cap_type:
            features["music"] = True

    return features
```

### 8.4 Device Types

| Type | Examples |
|------|----------|
| `devices.types.light` | LED strips (H619C, H6198, H61A5), bulbs (H6006, H6159), bars, TV backlights (H6099, H66A0), sync boxes (H6604) |
| `devices.types.socket` | Smart plugs |
| `devices.types.air_purifier` | Air purifiers (H7120, H7122, H7123, H7124, H7127) |
| `devices.types.humidifier` | Humidifiers (H7140) |
| `devices.types.dehumidifier` | Dehumidifiers (H7151) |
| `devices.types.heater` | Space heaters (H7130, H7131, H721C) |
| `devices.types.fan` | Tower fans (H7101, H7107). NB: combo ceiling-fan-with-light units (H1310) report as `devices.types.light`, not `fan` |
| `devices.types.ice_maker` | Ice makers (H7172) |
| `devices.types.thermometer` | Temp/humidity sensors (H5103, H5107, H5109, H5179) |
| `devices.types.air_quality_monitor` | CO2/air quality monitors (H5140) |
| `devices.types.sensor` | Motion, presence, water-leak sensors (H5059) |

### 8.5 Known Device Capability Profiles

Real API responses collected from user reports and testing.

#### H6099 — TV Backlight 3 Lite (`devices.types.light`)

Camera-based TV backlight. Note: DreamView/video mode uses local camera (BLE), **not** exposed via cloud API.

```json
{
  "capabilities": [
    {"type": "devices.capabilities.on_off", "instance": "powerSwitch"},
    {"type": "devices.capabilities.toggle", "instance": "gradientToggle"},
    {"type": "devices.capabilities.range", "instance": "brightness", "range": {"min": 1, "max": 100}},
    {"type": "devices.capabilities.segment_color_setting", "instance": "segmentedBrightness", "segments": 15},
    {"type": "devices.capabilities.segment_color_setting", "instance": "segmentedColorRgb", "segments": 15},
    {"type": "devices.capabilities.color_setting", "instance": "colorRgb"},
    {"type": "devices.capabilities.color_setting", "instance": "colorTemperatureK", "range": {"min": 2000, "max": 9000}},
    {"type": "devices.capabilities.dynamic_scene", "instance": "lightScene"},
    {"type": "devices.capabilities.music_setting", "instance": "musicMode", "modes": 11},
    {"type": "devices.capabilities.dynamic_scene", "instance": "diyScene"},
    {"type": "devices.capabilities.dynamic_scene", "instance": "snapshot"}
  ]
}
```

Key observations:
- Has `gradientToggle` but **not** `dreamViewToggle` — camera-based video sync is BLE-only
- 15 RGBIC segments (`elementRange.max = 14`, so 0-14 = 15 segments)
- Music mode has 11 options (Rhythm, Spectrum, Rolling, Separation, Hopping, PianoKeys, Fountain, DayAndNight, Sprouting, Shiny, Energic) with sensitivity (0-100) and optional autoColor/rgb fields
- 236 scenes available via `/device/scenes` endpoint

#### H6159 — WiFi RGB Light (`devices.types.light`)

Basic RGB light without segments. Note: color temp range 2000-9000K (wider than some models).

```json
{
  "capabilities": [
    {"type": "devices.capabilities.on_off", "instance": "powerSwitch"},
    {"type": "devices.capabilities.range", "instance": "brightness", "range": {"min": 1, "max": 100}},
    {"type": "devices.capabilities.color_setting", "instance": "colorRgb"},
    {"type": "devices.capabilities.color_setting", "instance": "colorTemperatureK", "range": {"min": 2000, "max": 9000}},
    {"type": "devices.capabilities.dynamic_scene", "instance": "lightScene"},
    {"type": "devices.capabilities.music_setting", "instance": "musicMode", "modes": ["Rhythm", "Sprouting", "Shiny"]},
    {"type": "devices.capabilities.dynamic_scene", "instance": "diyScene"}
  ]
}
```

Key observations:
- No segments — basic RGB light
- No `gradientToggle` or `dreamViewToggle`
- Music mode has 3 options (vs 11 on H6099)
- Music mode includes `autoColor` and `rgb` optional fields

#### H619C / H6198 / H610A — RGBIC LED Strip (`devices.types.light`)

RGBIC LED strips with 15 segments and DreamView toggle. From issue #28 diagnostics.

```json
{
  "capabilities": [
    {"type": "devices.capabilities.on_off", "instance": "powerSwitch"},
    {"type": "devices.capabilities.toggle", "instance": "gradientToggle"},
    {"type": "devices.capabilities.range", "instance": "brightness", "range": {"min": 1, "max": 100}},
    {"type": "devices.capabilities.segment_color_setting", "instance": "segmentedBrightness", "segments": 15},
    {"type": "devices.capabilities.segment_color_setting", "instance": "segmentedColorRgb", "segments": 15},
    {"type": "devices.capabilities.color_setting", "instance": "colorRgb"},
    {"type": "devices.capabilities.color_setting", "instance": "colorTemperatureK", "range": {"min": 2000, "max": 9000}},
    {"type": "devices.capabilities.dynamic_scene", "instance": "lightScene"},
    {"type": "devices.capabilities.music_setting", "instance": "musicMode", "modes": ["Rhythm", "Sprouting", "Shiny", "Spectrum", "Energic"]},
    {"type": "devices.capabilities.dynamic_scene", "instance": "diyScene"},
    {"type": "devices.capabilities.dynamic_scene", "instance": "snapshot"}
  ]
}
```

Key observations:
- H6198 also has `dreamViewToggle` (hardware HDMI passthrough)
- H619C and H610A have `gradientToggle` but NOT `dreamViewToggle`
- All have 15 segments (elementRange 0-14)
- Music mode has 5 options (more than H6159, fewer than H6099)
- `snapshot` capability present — user-saved snapshot scenes

#### H7101 — Smart Tower Fan (`devices.types.fan`)

8-speed tower fan with `work_mode` STRUCT format. Sub-options have **no names** (just `{"value": N}`).

```json
{
  "capabilities": [
    {"type": "devices.capabilities.on_off", "instance": "powerSwitch"},
    {"type": "devices.capabilities.toggle", "instance": "oscillationToggle"},
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
              {"name": "FanSpeed", "value": 1},
              {"name": "Custom", "value": 2},
              {"name": "Auto", "value": 3},
              {"name": "Sleep", "value": 5},
              {"name": "Nature", "value": 6}
            ]
          },
          {
            "fieldName": "modeValue",
            "dataType": "ENUM",
            "options": [
              {"name": "FanSpeed", "options": [
                {"value": 1}, {"value": 2}, {"value": 3}, {"value": 4},
                {"value": 5}, {"value": 6}, {"value": 7}, {"value": 8}
              ]},
              {"defaultValue": 0, "name": "Custom"},
              {"defaultValue": 0, "name": "Auto"},
              {"defaultValue": 0, "name": "Sleep"},
              {"defaultValue": 0, "name": "Nature"}
            ]
          }
        ]
      }
    }
  ]
}
```

Key observations:
- `work_mode` uses STRUCT with `fields` array (not flat `options` format)
- FanSpeed sub-options are **unnamed** (`{"value": 1}` not `{"name": "Low", "value": 1}`)
- 5 work modes but only FanSpeed (value=1) has sub-options (8 speed levels)
- Other modes (Custom, Auto, Sleep, Nature) use `defaultValue: 0`

#### H6604 — AI Sync Box (`devices.types.light`)

HDMI sync box with input selection. From issue #3.

```json
{
  "capabilities": [
    {"type": "devices.capabilities.on_off", "instance": "powerSwitch"},
    {"type": "devices.capabilities.range", "instance": "brightness"},
    {"type": "devices.capabilities.color_setting", "instance": "colorRgb"},
    {"type": "devices.capabilities.color_setting", "instance": "colorTemperatureK"},
    {"type": "devices.capabilities.dynamic_scene", "instance": "lightScene"},
    {"type": "devices.capabilities.dynamic_scene", "instance": "diyScene"},
    {"type": "devices.capabilities.mode", "instance": "hdmiSource",
     "parameters": {"dataType": "ENUM", "options": [
       {"name": "HDMI 1", "value": 1}, {"name": "HDMI 2", "value": 2},
       {"name": "HDMI 3", "value": 3}, {"name": "HDMI 4", "value": 4}
     ]}}
  ]
}
```

Key observations:
- Has `devices.capabilities.mode` / `hdmiSource` — a unique capability for HDMI input selection
- Exposed as `select` entity in HA (implemented in v2026.1.52)
- 4 HDMI inputs

#### H66A0 — TV Backlight 3 Pro (`devices.types.light`)

TV backlight with **movie mode** — a capability not seen on other devices. From issue #14.

```json
{
  "capabilities": [
    {"type": "devices.capabilities.on_off", "instance": "powerSwitch"},
    {"type": "devices.capabilities.toggle", "instance": "gradientToggle"},
    {"type": "devices.capabilities.range", "instance": "brightness"},
    {"type": "devices.capabilities.segment_color_setting", "instance": "segmentedBrightness"},
    {"type": "devices.capabilities.segment_color_setting", "instance": "segmentedColorRgb"},
    {"type": "devices.capabilities.color_setting", "instance": "colorRgb"},
    {"type": "devices.capabilities.color_setting", "instance": "colorTemperatureK"},
    {"type": "devices.capabilities.dynamic_scene", "instance": "lightScene"},
    {"type": "devices.capabilities.music_setting", "instance": "musicMode"},
    {"type": "devices.capabilities.movie_setting", "instance": "movieMode"},
    {"type": "devices.capabilities.dynamic_scene", "instance": "diyScene"},
    {"type": "devices.capabilities.dynamic_scene", "instance": "snapshot"},
    {"type": "devices.capabilities.toggle", "instance": "dreamViewToggle"}
  ]
}
```

Key observations:
- **`movie_setting` / `movieMode`** is a new capability type — hardware DreamView/movie mode
- Has both `gradientToggle` AND `dreamViewToggle` (hardware HDMI passthrough)
- Not currently exposed by the integration (feature request in issue #14)

#### H6104 — WiFi RGB Light (`devices.types.light`)

Basic WiFi light with no segments. From issue #24. Notable for API brightness bug.

```json
{
  "capabilities": [
    {"type": "devices.capabilities.on_off", "instance": "powerSwitch"},
    {"type": "devices.capabilities.range", "instance": "brightness"},
    {"type": "devices.capabilities.color_setting", "instance": "colorRgb"},
    {"type": "devices.capabilities.color_setting", "instance": "colorTemperatureK"},
    {"type": "devices.capabilities.music_setting", "instance": "musicMode"},
    {"type": "devices.capabilities.dynamic_scene", "instance": "diyScene"}
  ]
}
```

Key observations:
- No `lightScene` capability — only DIY scenes
- API returned brightness value of 254 (should be 0-100 range) — may be a firmware quirk
- No segments, no gradient, no snapshot

#### H7127 — Air Purifier (`devices.types.air_purifier`)

Air purifier with complex `work_mode` STRUCT. From issue #11.

```json
{
  "capabilities": [
    {"type": "devices.capabilities.on_off", "instance": "powerSwitch"},
    {"type": "devices.capabilities.work_mode", "instance": "workMode",
     "parameters": {"dataType": "STRUCT", "fields": [
       {"fieldName": "workMode", "dataType": "ENUM", "options": [
         {"name": "gearMode", "value": 1},
         {"name": "Custom", "value": 2},
         {"name": "Auto", "value": 3}
       ]},
       {"fieldName": "modeValue", "dataType": "ENUM", "options": [
         {"name": "gearMode", "options": [
           {"name": "Sleep", "value": 1},
           {"name": "Low", "value": 2},
           {"name": "High", "value": 3}
         ]}
       ]}
     ]}}
  ]
}
```

Key observations:
- Uses `work_mode` STRUCT pattern (same as fans)
- `gearMode` sub-options have names (Sleep/Low/High) unlike fan speed values
- Also supports Auto and Custom modes

#### H7130/H7131 — Space Heater (`devices.types.heater`)

Heaters with temperature control and work mode. From issue #13. **Critical**: temperature STRUCT requires `autoStop` field.

```json
{
  "capabilities": [
    {"type": "devices.capabilities.on_off", "instance": "powerSwitch"},
    {"type": "devices.capabilities.temperature_setting", "instance": "targetTemperature",
     "parameters": {"dataType": "STRUCT", "fields": [
       {"fieldName": "temperature", "dataType": "INTEGER", "range": {"min": 16, "max": 35}},
       {"fieldName": "unit", "dataType": "ENUM", "options": [
         {"name": "Celsius", "value": 0}, {"name": "Fahrenheit", "value": 1}
       ]},
       {"fieldName": "autoStop", "dataType": "ENUM", "options": [
         {"name": "Maintain", "value": 0}, {"name": "Stop", "value": 1}
       ]}
     ]}},
    {"type": "devices.capabilities.work_mode", "instance": "workMode",
     "note": "H7131 modes: Low(1,1), Medium(1,2), High(1,3), Fan(9,0), Auto(3,0)"}
  ]
}
```

Key observations:
- **`temperature_setting` / `targetTemperature`** uses STRUCT with 3 required fields
- Omitting `autoStop` causes the device to silently ignore the command (HTTP 200 but no effect)
- H7131 also has a light capability (`toggle` / `nightLight`)
- Work mode values: Low→`workMode=1,modeValue=1`, Medium→`1,2`, High→`1,3`, Fan→`9,0`, Auto→`3,0`
- See also issue #29: H721C/H713C heaters use the same pattern with `autoHold` preference

#### H7107 — Tower Fan (`devices.types.fan`)

12-speed tower fan with oscillation toggle. From govee2mqtt #438.

```json
{
  "capabilities": [
    {"type": "devices.capabilities.on_off", "instance": "powerSwitch"},
    {"type": "devices.capabilities.toggle", "instance": "oscillationToggle"},
    {"type": "devices.capabilities.work_mode", "instance": "workMode",
     "parameters": {"dataType": "STRUCT", "fields": [
       {"fieldName": "workMode", "options": [
         {"name": "FanSpeed", "value": 1}, {"name": "Auto", "value": 2},
         {"name": "Sleep", "value": 3}, {"name": "Nature", "value": 4},
         {"name": "Custom", "value": 5}
       ]},
       {"fieldName": "modeValue", "note": "FanSpeed/Sleep/Nature support 1-12 levels"}
     ]}}
  ]
}
```

Key observations:
- 12 speed levels (vs 8 on H7101) — fan speed count varies by model
- Different work mode numbering than H7101 (Auto=2 vs Auto=3)
- `oscillationToggle` is **advertised but a no-op on the H7107**: the Platform
  API returns HTTP 200 and the sweep motor does not react (govee2mqtt #438/#709,
  disforw/goveelife #70 — all H7107 reports). The state readback works; only
  the write is dead. The H7105 shares the H7107's ranged-oscillation frame
  shape (homebridge-govee #1339) and is routed the same way. The H7106 reports
  plain on/off oscillation in homebridge's capability grouping and is NOT the
  same protocol class; the H7108 is unverified. Neither is on this path.

**Oscillation over AWS IoT (ptReal) — the channel that works.** Reverse-engineered
in homebridge-govee `lib/device/fan-H7107.js` (v11.33.0, hardware-confirmed on
H7107; identical fix for H7105 in v11.34.0) and verified on an H7107 by this
integration (2026-08-26: OFF stops the sweep in <1 s, ON resumes it; the fan
ACKs each frame with `state.result: 1` on its `GA/…` reporting topic).

| Frame | Bytes (0-2) | Base64 (20-byte packet) | Notes |
|-------|-------------|--------------------------|-------|
| ptReal OFF | `33 1d 00` + zero pad + XOR | `Mx0AAAAAAAAAAAAAAAAAAAAAAC4=` | byte-exact to homebridge |
| ptReal ON | `33 1d 01 [t0 t1 t2 t3]` + pad + XOR | — | tail = 4 swing-range bytes, optional |
| multiSync twin | `3a 1d <on>` (same body, 0x3a prefix) | — | sent alongside each ptReal frame |

- Publish the ptReal frame with the standard `{"msg": {"cmd": "ptReal", "data": {"command": [b64]}}}`
  envelope on the device topic; the twin goes out as `cmd: "multiSync"` with the same
  `command: [b64]` list.
- The 4 tail bytes are the fan's configured sweep arc, echoed from its own inbound
  `aa 1d` BLE-format status frame (bytes 3-6). A bare ON (`33 1d 01`, no tail) also
  resumes the sweep on the H7107; the tail is replayed when seen so the fan keeps its
  physically-configured arc.
- **No angle/range SET command has been reverse-engineered.** The 4 tail bytes are
  only ever replayed from the fan's own report — two H7107 units in homebridge-govee
  #1334 reported different tails (`03 32 03 e8` and `03 3c 03 84`), so the arc is
  app-configurable and a set path exists somewhere in the app protocol. This
  integration exposes oscillation as a boolean only.
- Which of the two frames the fan honours is unconfirmed (homebridge-govee #1334);
  the hardware-confirmed requirement for a reliable OFF is that the frame carries
  **no tail bytes**. The multiSync twin is best-effort.
- In this integration: `fan.py` routes `async_oscillate` for
  `MQTT_OSCILLATION_SKUS = {"H7105", "H7107"}` (`const.py`) through
  `GoveeCoordinator.async_send_fan_oscillation` → `BlePassthroughManager.async_send_fan_oscillation`
  when the AWS IoT session is up, falling back to the REST `OscillationCommand`
  otherwise (a documented no-op on these SKUs — API-key-only users are warned once
  in the log). The send and the replayed tail land in the diagnostics download
  (`recent_commands` transport `mqtt`, per-device `fan_swing_tail`).

#### H1310 — Ceiling Fan + Light (`devices.types.light`)

Combo ceiling-fan-with-light. Reports as `devices.types.light` (because of the integrated light), so it does **not** match standalone-fan detection — the fan must be detected by capability. Unlike tower fans (which use `work_mode`/`workMode`), the H1310 exposes its fan via `toggle`/`mode` instances. From issue #74 (fixed in PR #90 via `h1310-govee-diagnostics-redacted.json`).

```json
{
  "sku": "H1310",
  "type": "devices.types.light",
  "capabilities": [
    {"type": "devices.capabilities.on_off", "instance": "powerSwitch"},
    {"type": "devices.capabilities.range", "instance": "brightness"},
    {"type": "devices.capabilities.color_setting", "instance": "colorRgb"},
    {"type": "devices.capabilities.toggle", "instance": "fanToggle"},
    {"type": "devices.capabilities.mode", "instance": "fanSpeedMode",
     "parameters": {"options": [
       {"name": "Speed 1", "value": 1}, {"name": "Speed 2", "value": 2},
       {"name": "Speed 3", "value": 3}, {"name": "Speed 4", "value": 4},
       {"name": "Speed 5", "value": 5}, {"name": "Speed 6", "value": 6}
     ]}},
    {"type": "devices.capabilities.toggle", "instance": "reverseAirflowToggle"}
  ]
}
```

- Detection: a device exposing `fanToggle` + `fanSpeedMode` gets a separate `fan` entity (alongside its light).
- `fanSpeedMode` is a `mode` (6 discrete speeds), not a `work_mode` STRUCT like tower fans.
- `reverseAirflowToggle` → fan direction (forward / reverse).
- Govee's cloud poll does not report fan state → use optimistic state restored across restarts.

**Fan and light state over AWS IoT (issue #181).** The Developer API returns `""` for
`fanToggle`, `fanSpeedMode`, `reverseAirflowToggle`, `mainLightToggle` and
`backgroundLightToggle` on every poll, and the push's device-wide `onOff` is the *unit*
having power, not the light — `onOff: 1` has been captured alongside both lights and the
fan off in one status (homebridge-govee #1352). The real state rides as BLE-format frames
in the push's `op.command` list (base64), decoded from labelled captures on real
H1310/R1310/H1370 units (homebridge-govee `lib/device/fan-ceiling.js`, #1352/#1358):

| Frame | Layout | Meaning |
|-------|--------|---------|
| `aa 31 <run> <speed> <dir> .. .. <swing>` | byte 2 `01` = turning, byte 3 = speed step (1–6 on the H1310), byte 4 `01` = reverse airflow, byte 7 `01` = oscillating (H1370 only) | fan state |
| `aa 42 <mask>` | bit `0x40` main light, `0x20` background light, `0x80` either lit | lights |
| `aa 36 <main> <background>` | one byte per light | lights (same fact, second form) |

In this integration the MQTT client attaches the decoded frames to the state it hands the
coordinator as `_op_frames` (hex; visible in the diagnostics `last_mqtt_message`), and for
devices with `supports_ceiling_fan` the coordinator ignores the top-level `onOff`, decodes
these frames into `ceiling_fan_on / _speed / _reverse / _swing` and the two light toggles,
and derives the light entity's power from the light frames. The fan entity and the named
light switches prefer that pushed state and fall back to their restored optimistic state
until the fan has reported. The values are carried across Developer polls because the poll
would otherwise reset them to nothing.

Related write-side observations from the same captures: `powerSwitch` is the whole unit;
`33 31 <run> <speed>` is the ptReal form of the fan command; the `reverseAirflowToggle`
capability is the only way to *send* a direction. Changing direction while the fan is off
starts the motor (reported on an H1310 in #181), so the integration marks the fan running.

#### H5089 — Smart Outlet Extender w/ Nightlight (`devices.types.socket`)

A socket that also exposes an RGB nightlight. Important for device-type detection: a `devices.types.socket` can legitimately carry a color light, so plug-exclusion logic must keep the light entity when the socket has a color capability (regression in issue #59, fixed PR #89). From `govee-...Smart Outlet Extender....json`.

```json
{
  "sku": "H5089",
  "type": "devices.types.socket",
  "capabilities": [
    {"type": "devices.capabilities.on_off", "instance": "powerSwitch"},
    {"type": "devices.capabilities.color_setting", "instance": "colorRgb"},
    {"type": "devices.capabilities.color_setting", "instance": "colorTemperatureK"}
  ]
}
```

- Exclude a plug from the light platform **only when it has no color capability** — plain on/off plugs (H5080) stay switch-only; H5089 keeps its color light entity.

#### H7120/H7122/H7123/H7124 — Air Purifiers (`devices.types.air_purifier`)

Air purifiers with filter tracking and optional nightlight. From govee2mqtt #297.

```json
{
  "capabilities": [
    {"type": "devices.capabilities.on_off", "instance": "powerSwitch"},
    {"type": "devices.capabilities.work_mode", "instance": "workMode",
     "parameters": {"dataType": "STRUCT", "fields": [
       {"fieldName": "workMode", "options": [
         {"name": "gearMode", "value": 1}, {"name": "Custom", "value": 2},
         {"name": "Auto", "value": 3}, {"name": "Sleep", "value": 5},
         {"name": "Turbo", "value": 7}
       ]},
       {"fieldName": "modeValue", "note": "gearMode: Low(1)/Medium(2)/High(3)"}
     ]}},
    {"type": "devices.capabilities.property", "instance": "filterLifetime"},
    {"type": "devices.capabilities.property", "instance": "airQuality"}
  ]
}
```

Key observations:
- **`property`** is a read-only capability type for sensor/status data (not controllable)
- H7124 has Turbo mode (value 7) — unique to larger purifiers
- H7120/H7124 add nightlight sub-pattern: `nightlightToggle` + `brightness` + `colorRgb` + `nightlightScene`
- H7122 Custom mode accepts range 1-13 (unusual)
- `filterLifetime` tracks filter replacement status
- `airQuality` provides air quality sensor readings

#### H7151 — Smart Dehumidifier Max (`devices.types.dehumidifier`)

Dehumidifier with target humidity and water tank events. From govee2mqtt #145.

```json
{
  "capabilities": [
    {"type": "devices.capabilities.on_off", "instance": "powerSwitch"},
    {"type": "devices.capabilities.range", "instance": "humidity",
     "parameters": {"dataType": "INTEGER", "range": {"min": 30, "max": 80, "precision": 1}}},
    {"type": "devices.capabilities.work_mode", "instance": "workMode",
     "parameters": {"dataType": "STRUCT", "fields": [
       {"fieldName": "workMode", "options": [
         {"name": "gearMode", "value": 1}, {"name": "Auto", "value": 3},
         {"name": "Dryer", "value": 8}
       ]},
       {"fieldName": "modeValue", "note": "gearMode: Low/Medium/High"}
     ]}},
    {"type": "devices.capabilities.event", "instance": "waterFullEvent",
     "note": "alarmType 58: Water bucket full or removed"}
  ]
}
```

Key observations:
- `humidity` range instance for target humidity (30-80%)
- Dryer mode (value 8) — clothes drying function
- `event` capability for water tank alerts (alarmType 58)

#### H7140 — Smart Humidifier Lite (`devices.types.humidifier`)

Humidifier with 9-speed mist and full nightlight. From goveelife #6.

```json
{
  "capabilities": [
    {"type": "devices.capabilities.on_off", "instance": "powerSwitch"},
    {"type": "devices.capabilities.work_mode", "instance": "workMode",
     "parameters": {"dataType": "STRUCT", "fields": [
       {"fieldName": "workMode", "options": [
         {"name": "Manual", "value": 1}, {"name": "Custom", "value": 2},
         {"name": "Auto", "value": 3}
       ]},
       {"fieldName": "modeValue", "note": "Manual: 9 levels (1-9)"}
     ]}},
    {"type": "devices.capabilities.range", "instance": "humidity", "range": {"min": 40, "max": 80}},
    {"type": "devices.capabilities.toggle", "instance": "nightlightToggle"},
    {"type": "devices.capabilities.range", "instance": "brightness"},
    {"type": "devices.capabilities.color_setting", "instance": "colorRgb"},
    {"type": "devices.capabilities.mode", "instance": "nightlightScene",
     "parameters": {"options": ["Forest", "Ocean", "Wetland", "Leisurely", "Sleep"]}},
    {"type": "devices.capabilities.event", "instance": "lackWaterEvent"}
  ]
}
```

Key observations:
- Full nightlight sub-pattern: toggle + brightness + color + scene selector
- `nightlightScene` uses `mode` capability type (not `dynamic_scene`)
- 9-speed manual mist control
- `lackWaterEvent` for low water alerts

#### H7172 — Smart Ice Maker (`devices.types.ice_maker`)

Ice maker with ice size selection and status events. From govee2mqtt #343.

```json
{
  "capabilities": [
    {"type": "devices.capabilities.on_off", "instance": "powerSwitch"},
    {"type": "devices.capabilities.work_mode", "instance": "workMode",
     "parameters": {"dataType": "STRUCT", "fields": [
       {"fieldName": "workMode", "options": [
         {"name": "LargeIce", "value": 1}, {"name": "MediumIce", "value": 2},
         {"name": "SmallIce", "value": 3}
       ]}
     ]}},
    {"type": "devices.capabilities.event", "instance": "lackWaterEvent", "note": "alarmType 51"},
    {"type": "devices.capabilities.event", "instance": "iceFullEvent", "note": "alarmType 58"}
  ]
}
```

Key observations:
- Unique device type — ice maker
- work_mode options represent ice sizes, not speed/modes
- Two event capabilities for operational status

#### H5179 — WiFi Thermometer (`devices.types.thermometer`)

Sensor-only device with no controllable capabilities. From goveelife #6.

```json
{
  "capabilities": [
    {"type": "devices.capabilities.property", "instance": "sensorTemperature"},
    {"type": "devices.capabilities.property", "instance": "sensorHumidity"}
  ]
}
```

#### H5140 — Smart CO2 Monitor (`devices.types.air_quality_monitor`)

Air quality monitor with CO2, temperature, and humidity sensors. From HA discussions #1410.

```json
{
  "capabilities": [
    {"type": "devices.capabilities.property", "instance": "carbonDioxideConcentration"},
    {"type": "devices.capabilities.property", "instance": "sensorTemperature"},
    {"type": "devices.capabilities.property", "instance": "sensorHumidity"}
  ]
}
```

#### H5103 / H5107 / H5109 — WiFi/BLE Thermometers (`devices.types.thermometer`)

Same capability shape as the H5179 thermometer family — detection is capability-based (`sensorTemperature` / `sensorHumidity`), not SKU-locked, so any H51xx thermometer that exposes these properties is discovered. From issues #91 (H5107), #85 (H5103), #62 (H5109).

```json
{
  "capabilities": [
    {"type": "devices.capabilities.property", "instance": "sensorTemperature", "parameters": {}},
    {"type": "devices.capabilities.property", "instance": "sensorHumidity", "parameters": {}}
  ]
}
```

Unit caveat (confirmed across #78, #85, #91): Govee's API returns the temperature in **whatever unit the device is set to in the Govee app** (often °F) and includes **no unit field**. A device reporting `sensorTemperature: 84.2` is 84.2 °F (≈ 29 °C), not Celsius. The integration cannot auto-detect this; the "Temperature unit from Govee API" option (added v2026.5.7, default `celsius`) selects how raw readings are interpreted. SKUs observed reporting °F without unit metadata: **H5103, H5107, H5109, H5110, H5179, HS5106, HS5108**.

#### H5059 — Water Leak Sensor (`devices.types.sensor`)

Standalone leak sensor returned by the Developer (API-key) endpoint. Leak state is exposed as an `event` capability, **not** a `property` — there is no pollable boolean; the `eventState.options` enumerate the possible states. From issue #87.

```json
{
  "sku": "H5059",
  "type": "devices.types.sensor",
  "capabilities": [
    {
      "type": "devices.capabilities.event",
      "instance": "bodyAppearedEvent",
      "alarmType": 1,
      "eventState": {
        "options": [
          {"name": "LEAKED",    "value": 1, "probesState": {"top": 1, "bot": 1}},
          {"name": "UN_LEAKED", "value": 2, "probesState": {"top": 0, "bot": 0}}
        ]
      }
    }
  ]
}
```

- `bodyAppearedEvent` value `1` = LEAKED, `2` = UN_LEAKED → maps to a `binary_sensor` device_class `moisture`.
- `probesState.top` / `probesState.bot` report per-probe state (`1` = water present, `0` = clear); expose as attributes.
- Device IDs use the extended 16-octet form (e.g. `03:4E:CE:6D:FF:FF:FF:12:FF:FF:00:33:FF:FF:00:4C`).

##### Runtime leak delivery — hub `multiSync` packet (issue #87)

The Developer-API `event` capability above is only the **definition**. Live leak state does **not** arrive via the state poll — it is pushed over AWS IoT MQTT as a `multiSync` message from the **hub** (H5043/H5044), carrying BLE-format sensor packets (base64) in `op.command[]`. Each packet is 20 bytes:

| Byte | Meaning |
|------|---------|
| `0` | `0xEE` — sensor-report header |
| `1` | event type: `0x34` = leak/dry, `0x32` = button press |
| `2` | sensor slot (`sno`) on the hub — keys the `(hub, slot) → sensor` map |
| `5` | battery % (e.g. `0x64` = 100). **NOT the wet flag** — legacy decoders misread this byte |
| `14`, `16` | probe state (`0x01` = wet, `0x00` = dry); byte `15` = `0x03` separator |

Wet decode (leak sensors must never under-report):

```python
is_wet = raw[5] == 0x01 or (len(raw) >= 17 and (raw[14] == 0x01 or raw[16] == 0x01))
```

- **H5059** reports wet in bytes `14`/`16`; earlier SKUs (H5058) were decoded off byte `5`. The OR keeps both working.
- Button-press packets (`0x32`) encode the sensor MAC in bytes `2..9` **reverse byte order**, not a slot number.
- The `(hub, sno) → sensor_id` map is built from the account/BFF device list; a sensor whose SKU is absent from `LEAK_SENSOR_SKUS` is dropped before the map is built, so its events log as "unknown sensor". Supported SKUs: `H5058`, `H5054`, `H5055`, `H5059`; hubs: `H5043`, `H5044`.
- Full derivation: `docs/_research/2026-06-04_h5059-h5044-leak-sensor-support.md`.

#### H5054 — Water Detector (NOT in Developer API)

The H5054 water detector is **not returned by the Developer API** (the API-key `/user/devices` endpoint this integration's discovery uses), so it never appears via the standard path. It is only exposed through the **app/account API** (same path the H5058 leak sensor uses). Confirmed in issue #62: a full integration diagnostics dump omitted the user's H5054s entirely, while their Homebridge account-based client enumerated all 10.

- Account-API device IDs use a colon-less hex form: `DABFC0D6A5FE000DB6`.
- Supporting it requires a dedicated account-API discovery branch, not an entity/capability change.

#### H5127 — mmWave WiFi Presence Sensor (`devices.types.sensor`)

The H5127 presence sensor **reuses the same `bodyAppearedEvent` event capability** the H5054/H5059 water detectors use — `bodyAppearedEvent` is a generic "body appeared in range" event, not leak-specific. Its Developer-API capability carries `alarmType: 50` with `eventState.options` of Presence(1)/Absence(2):

```json
{
  "sku": "H5127",
  "type": "devices.types.sensor",
  "capabilities": [
    {
      "type": "devices.capabilities.event",
      "instance": "bodyAppearedEvent",
      "alarmType": 50,
      "eventState": { "options": [ {"name": "Presence", "value": 1}, {"name": "Absence", "value": 2} ] }
    }
  ]
}
```

- **Detection must be SKU-locked**, not shape-based. The H5054/H5059 leak sensors advertise the *same* `{1, 2}` option values (leak uses `alarmType: 1` with LEAKED(1)/UN_LEAKED(2), see H5059 above). Keying presence-vs-leak on the option shape mis-classified real H5054s as presence sensors and stripped their Water Leak entity (regression in v2026.6.24 → fixed v2026.6.25 by locking to `PRESENCE_SENSOR_SKUS = {"H5127"}`). `alarmType` (50 vs 1) is a secondary discriminator but not every leak SKU's `alarmType` is catalogued, so SKU is authoritative.
- **Live presence is MQTT-only.** The `/device/state` poll returns only `online`; present/absent arrives as `state.triSta` (`1`/`0`) in the `cmd: "status"` push — see §3.5. Maps to a `binary_sensor` device_class `occupancy`.
- BFF census: direct-WiFi device (`in_leak_sensor_skus=false`, `in_leak_hub_skus=false`, `has_sno=false`, `has_gateway_info=false`) — no hub, no LoRa.

#### H5106 — Air Quality Monitor (`devices.types.thermometer`)

Reports `devices.types.thermometer` (not `air_quality_monitor` like the H5140). Three read-only property capabilities:

```json
[
  {"type": "devices.capabilities.property", "instance": "sensorTemperature", "parameters": {}},
  {"type": "devices.capabilities.property", "instance": "sensorHumidity", "parameters": {}},
  {"type": "devices.capabilities.property", "instance": "airQuality", "parameters": {}}
]
```

- `sensorTemperature` is a **plain Fahrenheit float** (e.g. `73.76` = ~23 °C), *not* centi-encoded; `sensorHumidity` is a plain percent. The SKU must be on the Fahrenheit auto-convert list (#116, same class as #115). Readings come from the **REST poll only** — the MQTT push carries no temp/humidity.
- `airQuality` is a **coarse air-quality index** (AQI-style, observed values 1–2), **not** a PM2.5 µg/m³ measurement — surfaced as a numeric AQI-device-class sensor. Govee's cloud exposes no real PM2.5 for this model (BLE-only).
- BFF `deviceSettings` reports a **phantom `battery: 100`** (mains-powered) → battery suppressed by SKU (#114).

#### H7150 / H7152 — Smart Dehumidifier (`devices.types.dehumidifier`)

```json
{
  "sku": "H7150",
  "capabilities": [
    {"type": "devices.capabilities.on_off", "instance": "powerSwitch"},
    {"type": "devices.capabilities.range", "instance": "humidity", "parameters": {"unit": "unit.percent", "dataType": "INTEGER", "range": {"min": 30, "max": 80, "precision": 1}}},
    {"type": "devices.capabilities.work_mode", "instance": "workMode", "parameters": {"dataType": "STRUCT", "fields": [
      {"fieldName": "workMode", "dataType": "ENUM", "options": [{"name": "gearMode", "value": 1}, {"name": "Auto", "value": 3}, {"name": "Dryer", "value": 8}]},
      {"fieldName": "modeValue", "options": [{"name": "gearMode", "options": [{"name": "Low", "value": 1}, {"name": "Medium", "value": 2}, {"name": "High", "value": 3}]}, {"name": "Auto", "range": {"min": 30, "max": 80}}, {"name": "Dryer", "value": 0}]}
    ]}},
    {"type": "devices.capabilities.event", "instance": "waterFullEvent", "alarmType": 58,
     "eventState": {"options": [{"name": "waterFull", "value": 1, "message": "Water bucket is full or has been pulled out"}]}}
  ]
}
```

- **Target humidity lives in the Auto-mode `modeValue`.** `workMode` is a STRUCT `{workMode ENUM, modeValue}`. gearMode `modeValue` is the fan gear (Low 1 / Medium 2 / High 3 — the **H7150 omits Medium**). The humidity setpoint is the Auto `modeValue`, but its advertised range is **model-dependent**: the **H7150** allows a settable `30–80`, while the **H7151/H7152 pin Auto to a fixed `80` (range `min:80, max:80`)**. Either way the `/device/state` poll returns `modeValue: 0` for Auto — Govee doesn't populate the current Auto setpoint — so `configured_humidity` reads null/0 (reporter #118 on an H7150 saw `workMode 3 / modeValue 0`). This exact "Auto → modeValue 0, HA number expects 80" mismatch is independently reproduced in govee2mqtt #413.
- **`waterFullEvent` is a push-only event** (`alarmType 58`, `eventState.options[].value 1` = "Water bucket is full or has been pulled out"). It is absent from the `/device/state` poll and not pushed over MQTT, so tank-full state is read from **BFF `deviceSettings.waterFull`** (`1` = full) → `binary_sensor` device_class `problem`. Requires email/password (#118).
- The `range`/`humidity` `state.value` comes back as an **empty string `""`** in the poll. There is **no `sensorTemperature` / `sensorHumidity` capability at all** on these dehumidifiers — live room temp/humidity is BLE-only in the Govee app and unavailable to any cloud integration (confirmed in govee2mqtt #413: `"instance":"humidity","state":{"value":""}`, "reports humidity only via Bluetooth").
- Cross-validated 2026-06-30 against the **goveelife** real-device fixtures (`h7150_2024-08-12.json`, `h7151_2025-06-01.json`) and **govee2mqtt** issues #413 / #145 — see `docs/_research/2026-06-30_30day-issue-sweep.md`.

#### H5310 — Smart Thermometer P2 / Pool Thermometer (`devices.types.thermometer`, gateway-bridged)

Battery thermo-hygrometer that reaches the cloud through an **H5044 gateway**; not returned by the Developer API for some accounts (surfaces via BFF). Only `sensorTemperature` + `sensorHumidity` property capabilities.

- **Extended 16-octet device id** (`03:55:01:25:00:00:00:0B:FF:FF:00:41:FF:FF:00:33`), not the standard 8-octet Govee MAC — relevant to id parsing.
- The pool probe is **temperature-only**: `sensorHumidity` comes back as `655.35` (= `0xFFFF / 100`, a "no-data" sentinel). Treat the sentinel as unavailable rather than a real 655% reading (#100).
- Rides the gateway with **no direct MQTT topic** (`device_topic_count=0`); the H5044 pushes 20-byte `ee34…` multi-sync frames. Richer cached state (`tem`/`hum`/`avgDay…`) is in BFF `lastDeviceData`.

##### Thermo `ee34` frame layout — decoded (issues #151, #157)

An H5044 bridging an H5310 pushes an `ee34` frame every ~10 minutes (some
accounts see one per hour, at hh:56). These frames are the **origin** of the
reading everything else mirrors: the cloud copy in BFF `lastDeviceData` lands
5–7 minutes later, and for some accounts Govee never fills it at all, leaving a
gateway-bridged H5310 with no reading source whatsoever (#151).

```
ee 34 00 08 00 64 29 15 c2 6a 7e ca 3c 89 ba cc ff 80 00 2a
└──┬──┘ │  │  │  └──┬──┘ └────┬────┘ │  └────┬────┘ └─┬─┘ │
 header │  │  │   per-dev   epoch ts   │    per-dev    ?  cksum
        │  │  battery              temperature
        │  sub-device class
        slot (sno)
```

| Byte | Finding |
|------|---------|
| `0-1` | `0xEE 0x34` header — shared with leak reports |
| `2` | Slot (`sno`) of the sub-device on the gateway |
| `3` | **Sub-device class: `0x02` = leak sensor, `0x08` = thermometer.** The only byte separating the two frame types |
| `4-5` | `00 64` — byte 5 is battery %, same slot both frame types use |
| `6-8` | Per-device constants; byte `8` varies without tracking temperature |
| `9-12` | **Big-endian Unix epoch seconds.** Within 3 s of receive time across all 44 frames of the #151 capture |
| `13` | **Temperature: `T[°C] = (byte13 + 112) / 10`** |
| `14-18` | Per-device constants (`ba cc ff 80 00` here, `82 cc ff 20 00` on the #157 unit) |
| `19` | Checksum (XOR-style, varies with payload) |

**How byte 13 was settled.** @Araknus13 paired 30 on-the-hour frames against
their own logged cloud history for the same probe — pool water, 24.4–29.5 °C
over 31 hours. Least squares gives `T = 0.10010 * b13 + 11.1647`, i.e. exactly
0.1 °C per count, reproducing every point within 0.1 K. The two candidates from
#157's single-labelled-point capture miss the same data by 19.3 K (`°F + 113`)
and 38.1 K (`°C + 170`); they had looked indistinguishable only because they
intersect at 31.25 °C = 88.25 °F, right where that lone 88.34 °F reading sat.
The formula independently reproduces that #157 point from a different account.

**Known limits.** Byte 13 is unsigned, so the representable span is
11.2–36.7 °C and the validating capture only covers 24–30 °C — behaviour at
the ends (whether the byte saturates, or a sign/scale switch exists for
sub-11 °C readings) is unverified. `0x00` and `0xFF` are treated as the
frame's own no-data markers rather than 11.2/36.7 °C readings.

**Implementation.** `_decode_thermo_frame` in `api/mqtt.py` diverts byte-3
`0x08` frames ahead of the leak branch — leak decoding stays the default for
every other sub-device class, so an unknown leak SKU can never be silenced by
the discrimination. `coordinator._handle_thermo_frame` routes the reading via
`(gateway, sno) → device_id` and normalizes the decoded °C into the unit the
entity reads back, so the frame path coexists with the Developer poll instead
of claiming ownership of the device.

#### H616C — LED Strip (NOT yet in Developer API backend)

Confirmed via Govee support (#122): the **H616C is not wired into Govee's Developer API backend** as of mid-2026 — it returns no device entry, so the integration cannot discover or control it regardless of code. Govee indicated backend support arrives with app **v7.5.3**. No integration change is possible until Govee enables the SKU server-side.

Key observations for sensor devices:
- **Sensor-only pattern**: only `property` capabilities (thermometers, CO2) or `event` capabilities (leak), no controllable capabilities
- `property` instances are read-only — report values but cannot be commanded
- `event` instances carry no pollable state — react to the pushed event / `eventState.options` enum
- Some sensor-only SKUs (H5054) are absent from the Developer API and need the account API

### 8.7 Appliance Capability Patterns

Non-light devices follow consistent patterns:

| Pattern | Capabilities | Example Devices |
|---------|-------------|-----------------|
| **Appliance** | `on_off` + `work_mode` (STRUCT) + optional `event`/`range` | Fans, purifiers, humidifiers, ice makers |
| **Sensor-only** | `property` instances only (read-only) | Thermometers, CO2 monitors |
| **Nightlight sub-pattern** | `nightlightToggle` + `brightness` + `colorRgb` + `nightlightScene` | H7120, H7124, H7140 (embedded in appliance) |

### 8.8 DreamView vs Camera-based Video Sync

| Feature | DreamView (HDMI) | Camera Video Sync |
|---------|-----------------|-------------------|
| **Mechanism** | Hardware HDMI passthrough | Phone/device camera |
| **API capability** | `dreamViewToggle` | Not exposed |
| **Cloud controllable** | Yes | No (BLE only) |
| **Example devices** | HDMI sync boxes | H6099, TV backlights |

Devices with hardware HDMI passthrough expose `dreamViewToggle` via the cloud API. Camera-based video sync is a local feature controlled via BLE/the Govee app and cannot be toggled through the API.

---

## 10. Scene & DIY Modes

### 9.1 Scene Types

| Type | Source | Description |
|------|--------|-------------|
| **Dynamic Scenes** | Official API | Pre-built animations |
| **DIY Scenes** | Official API | User-created via app |
| **Light Effect Library** | app2 API | Full scene catalog |

### 9.2 Fetching Scenes (Official API)

```http
POST /router/api/v1/device/scenes

{
  "requestId": "uuid",
  "payload": {
    "sku": "H618E",
    "device": "8C:2E:9C:04:A0:03:82:D1"
  }
}
```

### 9.3 Fetching Full Scene Catalog (Undocumented)

```http
GET https://app2.govee.com/appsku/v1/light-effect-libraries?sku=H6072
```

Response includes:
- Category organization
- Scene codes for BLE activation
- Animation parameters

### 9.4 Activating Scenes

**Via API:**
```json
{
  "type": "devices.capabilities.dynamic_scene",
  "instance": "lightScene",
  "value": {"id": 3853, "paramId": 4280}
}
```

**Via BLE:**
```
33 05 04 [code_low] [code_high] 00...00 [XOR]
```

### 9.5 DIY Mode Creation

DIY modes use multi-packet BLE sequences:

1. **Start packet:** `A1 02 00 [count] ...`
2. **Color data:** `A1 02 [num] [style] [mode] [speed] ...`
3. **End packet:** `A1 02 FF ...`
4. **Activate:** `33 05 0A ...`

DIY Styles:
- `0x00` = Fade
- `0x01` = Jumping
- `0x02` = Flicker
- `0x03` = Marquee
- `0x04` = Music reactive

### 9.6 SKU Segment Count Overrides

The Govee API exposes RGBIC segment counts through three different shapes
inside `devices.capabilities.segment_color_setting.parameters`:

1. A direct `segmentCount` integer.
2. `fields[].elementRange.max` — a **0-based** max index (so `max = 14` ⇒ 15
   segments).
3. `fields[].size.max` — the protocol-level array-size ceiling.

For most SKUs the parser picks the first shape that yields a value, and
`elementRange.max + 1` is what the device actually has. **A handful of SKUs
over-report in `elementRange` while `size.max` matches the physical sections.**
The H7075 is the first observed case: `elementRange.max = 14` (so the
parser would return 15) but `size.max = 3` and the device really only has
3 physical sections. Spawning 15 segment entities on a 3-section wall light
produces 12 phantom `light.<name>_segment_{3..14}` entities that the
cloud silently rejects, which then get stuck on whatever color the
optimistic state happened to set.

The integration handles this with a two-layer correction in
`custom_components/govee/models/device.py` (`GoveeDevice.segment_count`),
applied in this order:

1. **Defensive clamp on the API count.** When `fields[].size.max` is
   present, the parser-derived count is clamped with
   `min(api_count, size.max)`. This is the *automatic* safety net — it
   catches any SKU that follows the H7075 over-reporting pattern without
   requiring a manual dict entry. The H7075 itself would already come
   out at 3 from this clamp alone; the override (step 2) makes the
   intent explicit and survives API shape changes.

   **Caveat.** `size.max` is documented in the API as the maximum array
   *length* accepted in one command, not the segment count. On every
   capture we hold, the two agree (`size.max == elementRange.max + 1`),
   so the clamp only fires on the inconsistency that signals the bug.
   Should a device ever advertise a genuine per-command batch limit
   below its real segment count, the clamp would remove working
   entities — pin the true count in `SKU_SEGMENT_OVERRIDES` to override
   it.

2. **Authoritative override.** `SKU_SEGMENT_OVERRIDES` (in
   `custom_components/govee/const.py`) maps a SKU to the physical
   segment count, compared case-insensitively against `GoveeDevice.sku`.
   When a SKU is present, the override wins over any clamp-derived
   value. The shipped entries are:

   ```python
   SKU_SEGMENT_OVERRIDES: Final = {
       "H7075": 3,  # API reports 15 (elementRange.max=14), device has 3 physical sections
       "H7076": 4,  # API reports 15 AND size.max 15; only indices 0-3 do anything
   }
   ```

**When the clamp can't help.** The H7076 Outdoor Up/Down Wall Light is
the counter-case to the H7075: it reports `elementRange.max = 14` *and*
`size.max = 15`, so the two agree and the clamp is a no-op. Only indices
0–3 physically move the light — `0` = top, `1` = bottom, `2` = part of
the left side, `3` = everything else — while `4`–`14` are accepted with
HTTP 200 `"success"` and do nothing (#160). A SKU like this can only be
corrected by an explicit override entry.

Note that the override caps entities at the count the *cloud API* can
address, which is not necessarily what the Govee app can reach. The
H7076's app exposes 17 selectable segments per side over BLE; the cloud
`segmentedColorRgb` capability collapses all of that into 4 indices, and
no integration change can recover the finer granularity while the
capability is the only channel available.

The `govee.set_segment_color` service also validates its `segments` list
against the same effective `segment_count` and logs a warning + early
returns for out-of-range indices, instead of dispatching a command the
cloud would refuse.

**Adding a new SKU.** When you observe a SKU whose `elementRange.max` is
higher than its physical section count, open a GitHub issue with the
SKU, the captured API response, and the confirmed section count from the
official Govee Android app. The change is a one-line entry in
`SKU_SEGMENT_OVERRIDES` (with a rationale comment matching the H7075
style), a test case in `TestSegmentCountOverride`, and a release note.
Follow the same workflow as `FAHRENHEIT_REPORTING_SKUS` in the same
file (issues #115 / #128 / #129 are precedents).

**Orphan entities after upgrading.** The first time an existing H7075
install upgrades to a release that includes this fix, the entity
registry keeps the 12 phantom `light.<name>_segment_{4..15}` entries
that the previous (unfixed) version created. They have to be manually
deleted from **Settings → Devices & Services → Entities** (filter by
`<device>_segment`, select each, **Delete**). This is a one-time
operation per affected device — the fix only prevents *future* phantom
entities; it does not retroactively purge existing ones.

Reference: #160 (H7076), PR #161 (H7075).

---

## 11. PCAP Analysis Details

### 11.1 Capture Information

**Primary Capture (January 24, 2026):**

| Field | Value |
|-------|-------|
| **File** | `docs/PCAPdroid_24_Jan_16_00_31.pcap` |
| **Size** | 29,311,903 bytes (28 MB) |
| **Packets** | 7,157 total |
| **Duration** | 683 seconds (~11 minutes) |
| **Source** | PCAPdroid (Android) |

**Reference Capture (January 9, 2026):**

| Field | Value |
|-------|-------|
| **File** | `logs/PCAPdroid_09_Jan_19_27_26.pcap` |
| **Size** | 7,091,980 bytes (6.8 MB) |
| **Packets** | 3,281 total |
| **Duration** | ~5 minutes |

### 11.2 Traffic Breakdown

| Protocol | Packets | Bytes | Purpose |
|----------|---------|-------|---------|
| HTTPS (443) | 6,348 | ~29 MB | App API, CDN, Firebase |
| MQTT (8883) | 761 | ~200 KB | AWS IoT real-time |
| DNS (53) | 48 | ~5 KB | Name resolution |

### 11.3 Server IPs Observed

**app2.govee.com (Auth + Internal API):**
| IP | TLS Connections |
|----|-----------------|
| 52.0.106.177 | 15 |
| 18.208.241.196 | 7 |
| 100.49.147.20 | 7 |
| 54.165.11.166 | 6 |
| 3.93.134.192 | 1 |

**AWS IoT MQTT (aqm3wd1qlc3dy-ats.iot.us-east-1.amazonaws.com):**
| IP | Purpose |
|----|---------|
| 98.88.204.61 | Primary MQTT endpoint |
| 35.169.219.171 | Failover/load-balanced |
| 13.223.152.107 | Failover/load-balanced |
| 3.231.7.138 | Failover/load-balanced |

**CDN / Static Assets:**
| IP | Service |
|----|---------|
| 3.161.193.104 | app-h5-manifest.govee.com |
| 99.84.237.29 | d1f2504ijhdyjw.cloudfront.net |
| 99.84.237.110 | d1f2504ijhdyjw.cloudfront.net |
| 99.84.237.10 | d1f2504ijhdyjw.cloudfront.net |

**Analytics:**
| IP | Service |
|----|---------|
| 74.125.136.94 | firebase-settings.crashlytics.com |
| 216.239.36.223 | firebaselogging-pa.googleapis.com |

### 11.4 TLS SNI Hostnames

| Hostname | Purpose |
|----------|---------|
| `aqm3wd1qlc3dy-ats.iot.us-east-1.amazonaws.com` | AWS IoT MQTT |
| `app2.govee.com` | Authentication & Internal API |
| `app-h5-manifest.govee.com` | H5 app resources/manifests |
| `d1f2504ijhdyjw.cloudfront.net` | CDN for static assets |
| `firebase-settings.crashlytics.com` | Crash reporting |
| `firebaselogging-pa.googleapis.com` | Firebase analytics |

### 11.5 TLS Cipher Suites

All connections use Perfect Forward Secrecy (PFS), preventing decryption with private keys alone:

| Service | Cipher Suite |
|---------|--------------|
| AWS IoT MQTT (8883) | `TLS_ECDHE_RSA_WITH_AES_128_GCM_SHA256` |
| app2.govee.com | `TLS_ECDHE_RSA_WITH_AES_128_GCM_SHA256` |
| CloudFront CDN | `TLS_AES_128_GCM_SHA256` (TLS 1.3) |
| Google/Firebase | `TLS_AES_256_GCM_SHA384` (TLS 1.3) |

*Note: To decrypt TLS traffic, use SSLKEYLOGFILE during capture or configure a MITM proxy.*

### 11.6 AWS IoT MQTT Session Analysis

| Metric | Value |
|--------|-------|
| Session Duration | 679 seconds |
| Total Packets | 761 |
| Total Bytes | 200,786 |
| Outbound Packets | 377 |
| Inbound Packets | 384 |
| Outbound Bytes | 64,444 |
| Inbound Bytes | 136,342 |
| Avg Outbound Gap | 4.99 seconds |
| Avg Inbound Gap | 3.81 seconds |
| Max Idle Period | 151 seconds |

**Packet Size Distribution:**
- Outbound: min=31, max=1,271, avg=300 bytes
- Inbound: min=31, max=5,392, avg=573 bytes

### 11.7 Connection Patterns

**MQTT Session:**
- Multiple AWS IoT IPs used (load balancing observed)
- Connection maintained for entire capture duration
- State updates arrive within seconds of app changes
- Keepalive maintains connection during idle periods

**API Patterns:**
- Heavy initial burst: scene libraries, device list
- Large asset downloads from CloudFront CDN (~4 MB)
- Multiple reconnections to app2.govee.com (15 TLS sessions)
- Firebase analytics sent periodically

---

## 12. References

### Official Documentation
- [Govee Developer Platform](https://developer.govee.com/)
- [API Reference PDF v2.0](https://govee-public.s3.amazonaws.com/developer-docs/GoveeDeveloperAPIReference.pdf)
- [LAN API Guide](https://app-h5.govee.com/user-manual/wlan-guide)

### Community Projects
- [wez/govee2mqtt](https://github.com/wez/govee2mqtt) - Rust, AWS IoT + LAN
- [homebridge-govee](https://github.com/homebridge-plugins/homebridge-govee) - Homebridge plugin (first to implement 2FA)
- [egold555/Govee-Reverse-Engineering](https://github.com/egold555/Govee-Reverse-Engineering) - BLE docs
- [BeauJBurroughs/Govee-H6127-Reverse-Engineering](https://github.com/BeauJBurroughs/Govee-H6127-Reverse-Engineering)

### 2FA Authentication References
- [homebridge-govee 2FA commit](https://github.com/homebridge-plugins/homebridge-govee/commit/25f9e52b32c80e4c22d561d43e5f16753f91f71f) - Reference implementation
- [homebridge-govee 2FA wiki](https://github.com/homebridge-plugins/homebridge-govee/wiki/AWS-Control#two-factor-authentication-2fa) - Flow explanation
- [govee2mqtt #628](https://github.com/wez/govee2mqtt/issues/628) - "Service not enabled" after auth changes
- [govee2mqtt #637](https://github.com/wez/govee2mqtt/issues/637) - "App version too low" crash
- [homebridge-govee #1253](https://github.com/homebridge-plugins/homebridge-govee/issues/1253) - Accessories not displaying

### Reverse Engineering
- [coding.kiwi - Reverse Engineering Govee](https://blog.coding.kiwi/reverse-engineering-govee-smart-lights/)
- [XDA - Govee Reverse Engineering](https://www.xda-developers.com/reverse-engineered-govee-smart-lights-smart-home/)
- [LAN API Gist](https://gist.github.com/mtwilliams5/08ae4782063b57a9b430069044f443f6)

### Home Assistant Community
- [Govee Integration Thread](https://community.home-assistant.io/t/govee-integration/228516)
- [Govee LAN API Announcement](https://community.home-assistant.io/t/govee-news-theres-a-local-api/460757)

---

*This document is based on analysis of the Govee Android app via PCAP capture and community reverse engineering efforts. The undocumented APIs may change without notice.*
