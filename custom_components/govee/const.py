"""Constants for Govee integration."""

from typing import Final

DOMAIN: Final = "govee"

# Config entry keys
CONF_API_KEY: Final = "api_key"
CONF_EMAIL: Final = "email"
CONF_PASSWORD: Final = "password"

# Options keys
CONF_POLL_INTERVAL: Final = "poll_interval"
CONF_ENABLE_GROUPS: Final = "enable_groups"
CONF_ENABLE_SCENES: Final = "enable_scenes"
CONF_ENABLE_DIY_SCENES: Final = "enable_diy_scenes"
CONF_ENABLE_SEGMENTS: Final = "enable_segments"
CONF_SEGMENT_MODE: Final = "segment_mode"
CONF_EXPOSE_TRANSPORT_ENTITIES: Final = "expose_transport_entities"
CONF_ENABLE_MQTT_CONTROL: Final = "enable_mqtt_control"

# Extra LAN discovery targets for devices the local multicast scan can't reach —
# e.g. Govee devices on a different VLAN/subnet than Home Assistant (issue #57).
# Free-text list (comma / newline / space separated) of device IPs, broadcast
# addresses, and CIDR subnets (≤ /24, unicast-swept since inter-VLAN firewalls
# usually drop directed broadcast). Empty = local multicast scan only.
CONF_LAN_TARGETS: Final = "lan_targets"

# Some Govee thermometer/hygrometer SKUs report temperatures in Fahrenheit via
# the Cloud API without unit metadata, while the native sensor unit is tagged
# Celsius — so a 101°F reading surfaces as 213.5°F in HA (issues #72, #78, #96).
# This option controls normalization:
#   "auto"       -> convert only for SKUs in FAHRENHEIT_REPORTING_SKUS (default)
#   "fahrenheit" -> always convert (treat API value as °F)
#   "celsius"    -> never convert (trust API value as °C)
CONF_API_TEMPERATURE_UNIT: Final = "api_temperature_unit"

# SKUs observed reporting sensorTemperature in Fahrenheit regardless of account
# locale. Used by the "auto" mode to convert out-of-the-box. Compared
# case-insensitively against GoveeDevice.sku.
#   H717A (smart kettle): reports its temperature in °F under the °C-tagged
#     native unit, so 187°F surfaced as 187°C — impossible for a kettle (water
#     boils at 100°C); real value ~86°C (issue #115).
#   H5106 / H5140 (air-quality / CO₂ monitors): report °F as a plain float
#     (e.g. 73.9°F ≈ 23.3°C), surfaced under the °C unit as a "wrong large
#     value". Confirmed by reporter diagnostics (issue #116) — they are NOT
#     centi-encoded, just Fahrenheit; same class as #72/#78/#96.
FAHRENHEIT_REPORTING_SKUS: Final = frozenset(
    {"H5179", "H5109", "H5110", "HS5108", "HS5106", "H717A", "H5106", "H5140"}
)


def resolve_fahrenheit_conversion(
    sku: str, api_unit: str, device_unit_hint: str | None = None
) -> bool:
    """Whether a Developer-API ``sensor_temperature`` should be treated as °F.

    Shared by the sensor entity (which converts °F→°C for display) and the
    coordinator's BFF reading path (which must store the value in the SAME
    unit the entity expects, so a true-°C BFF reading round-trips correctly
    instead of being double-converted) — issues #96, #83.

    ``device_unit_hint`` is explicit unit metadata reported by the device
    itself — heaters (e.g. H713B) carry a ``unit`` field in their
    temperature_setting STRUCT state and report ``sensorTemperature`` in that
    same unit. In "auto" mode the device's own metadata beats the static SKU
    allowlist, so Fahrenheit-configured heaters normalize out-of-the-box
    without a per-SKU entry (issue #129).
    """
    if api_unit == "auto":
        if device_unit_hint is not None:
            return device_unit_hint.lower() == "fahrenheit"
        return sku.upper() in FAHRENHEIT_REPORTING_SKUS
    return api_unit == "fahrenheit"


# Defaults
DEFAULT_POLL_INTERVAL: Final = 60  # seconds
DEFAULT_ENABLE_GROUPS: Final = False
DEFAULT_ENABLE_SCENES: Final = True
DEFAULT_ENABLE_DIY_SCENES: Final = True
DEFAULT_ENABLE_SEGMENTS: Final = True
DEFAULT_SEGMENT_MODE: Final = "individual"  # "disabled", "grouped", or "individual"
DEFAULT_EXPOSE_TRANSPORT_ENTITIES: Final = False
DEFAULT_ENABLE_MQTT_CONTROL: Final = False
DEFAULT_API_TEMPERATURE_UNIT: Final = "auto"
DEFAULT_LAN_TARGETS: Final = ""

# Optimistic state handling
# Grace window (seconds) during which API polls do NOT overwrite optimistic
# power/brightness. Masks out-of-range BLE devices and slow cloud responses
# without producing the "UI flipflop" that longer windows would. MQTT push
# confirmations clear the window early.
OPTIMISTIC_GRACE_CAP_SECONDS: Final = 15

# How often (seconds) the coordinator re-checks the Govee account device list to
# pick up devices added after startup (issue #101). Throttled well above the
# poll interval to respect the 100/min, 10k/day API rate limits — a new device
# appears within this window without a manual reload.
DEVICE_REDISCOVERY_INTERVAL: Final = 300

# LAN transport constants (issue #57)
# Runtime tuning for the local UDP (:4002/:4003) transport. These are NOT
# user-facing options — the only LAN option is CONF_LAN_TARGETS (above), which
# feeds extra cross-VLAN unicast scan targets. LAN auto-enables when a device
# answers the discovery scan and its scan MAC correlates to a coordinator
# device_id; there is no enable toggle.
#
# Wall-clock window (seconds) for one shared solicited read-batch collection.
# Bounds how long async_read_batch waits to gather devStatus replies.
LAN_READ_WINDOW: Final = 1.0
# Timeout (seconds) for the verify-by-read confirm after a LAN write. A reply
# within this window confirms the write; otherwise the command falls through
# to MQTT/REST so a device is never stranded.
LAN_WRITE_CONFIRM_TIMEOUT: Final = 0.5
# How often (seconds) to re-run the LAN discovery scan and re-correlate scan
# MACs to device_ids. Re-promotes demoted devices and picks up new ones / IP
# reassignments. Mirrors DEVICE_REDISCOVERY_INTERVAL throttling.
LAN_RESCAN_INTERVAL: Final = 300
# Consecutive solicited-read misses before a device is demoted out of the LAN
# device map so both reads and writes fall back to MQTT/REST.
LAN_READ_MISS_DEMOTE_THRESHOLD: Final = 3
# Consecutive LAN write-confirm failures (verify-by-read timeout OR value
# mismatch) before LAN write *attempts* are suppressed for a device. Issue #57:
# LAN transport health is READ-driven — a write whose readback does not confirm
# (a freshly-powered controller reporting its pre-command state late, or simply
# answering the confirm read after LAN_WRITE_CONFIRM_TIMEOUT) must NOT flip the
# lan_connectivity sensor off, which produced a flap synced to control activity
# (the reported symptom). Instead, after this many consecutive unconfirmed
# writes the device's writes skip the LAN attempt (and its confirm-read wait)
# and go straight to MQTT/REST for LAN_WRITE_SUPPRESS_SECONDS, while LAN reads
# keep working and the sensor stays Connected. A confirmed write or the cooldown
# expiry re-arms LAN writes.
LAN_WRITE_SUPPRESS_THRESHOLD: Final = 3
# Cooldown (seconds) after LAN writes are suppressed for a device before the next
# command probes the LAN write path again. Aligned with LAN_RESCAN_INTERVAL so a
# device that started confirming after a rescan/firmware change is re-tried at
# roughly the same cadence it would be re-correlated.
LAN_WRITE_SUPPRESS_SECONDS: Final = 300
# Age (seconds) past which a device's last successful LAN exchange is treated as
# stale, marking its 'lan' transport health unavailable. Set just above the 60s
# default poll so a single missed poll is tolerated.
LAN_STALE_SECONDS: Final = 90
# Time-to-live (seconds) for a scan MAC↔IP correlation. A devStatus read is only
# applied for an IP whose correlation is fresher than this, guarding against
# DHCP IP reassignment mis-routing a read to the wrong device. Kept at or above
# LAN_RESCAN_INTERVAL so a correlation stays valid across a full rescan cycle.
LAN_CORRELATION_TTL_SECONDS: Final = 600

# BLE constants
# Govee AWS/BLE advert manufacturer ID. Verified against
# Bluetooth-Devices/govee-ble (used by H5127 and related). Additional IDs
# remain unverified and are omitted until observed in the wild.
GOVEE_BLE_MANUFACTURER_IDS: Final = (0x8803,)  # 34819

# Segment mode options
SEGMENT_MODE_DISABLED: Final = "disabled"
SEGMENT_MODE_GROUPED: Final = "grouped"
SEGMENT_MODE_INDIVIDUAL: Final = "individual"

# Config entry schema version. Bumped to 2 in sprint-4 when IoT credentials
# moved from hass.data[DOMAIN] to entry.data (see async_migrate_entry).
CONFIG_VERSION: Final = 2

# Keys for storing cached data in hass.data[DOMAIN]
KEY_IOT_CREDENTIALS: Final = "iot_credentials"
KEY_IOT_LOGIN_FAILED: Final = "iot_login_failed"

# Entity unique_id suffixes
# Used in entity creation and orphan cleanup to keep patterns consistent
SUFFIX_SEGMENT: Final = "_segment_"
SUFFIX_GROUPED_SEGMENT: Final = "_grouped_segments"
SUFFIX_SCENE_SELECT: Final = "_scene_select"
SUFFIX_SNAPSHOT_SELECT: Final = "_snapshot_select"
SUFFIX_DIY_SCENE_SELECT: Final = "_diy_scene_select"
SUFFIX_DIY_STYLE_SELECT: Final = "_diy_style_select"
SUFFIX_HDMI_SOURCE_SELECT: Final = "_hdmi_source_select"
SUFFIX_MUSIC_MODE_SELECT: Final = "_music_mode_select"
SUFFIX_REFRESH_SCENES: Final = "_refresh_scenes"
SUFFIX_NIGHT_LIGHT: Final = "_night_light"
SUFFIX_LIGHT_ZONE: Final = "_light_zone_"
SUFFIX_SOCKET: Final = "_socket_"
SUFFIX_MAIN_LIGHT: Final = "_main_light"
SUFFIX_BACKGROUND_LIGHT: Final = "_background_light"
SUFFIX_MUSIC_MODE: Final = "_music_mode"
SUFFIX_MUSIC_SENSITIVITY: Final = "_music_sensitivity"
SUFFIX_DREAMVIEW: Final = "_dreamview"
SUFFIX_HEATER_TEMPERATURE: Final = "_heater_temperature"
SUFFIX_HEATER_FAN_SPEED: Final = "_heater_fan_speed"
SUFFIX_HEATER_AUTO_STOP: Final = "_heater_auto_stop"
SUFFIX_PURIFIER_MODE_SELECT: Final = "_purifier_mode_select"
SUFFIX_PRESET_SCENE_SELECT: Final = "_preset_scene_select"
SUFFIX_NIGHTLIGHT_SCENE_SELECT: Final = "_nightlight_scene_select"
