# Migration Guide

This guide helps you migrate from previous versions of the Govee integration to the Platinum Quality refactored version.

## What's New

### Platinum Quality Features
- **Strict Type Checking**: Full mypy --strict compliance
- **Diagnostics**: Download diagnostic data for troubleshooting
- **Repair Flows**: Guided resolution for common issues
- **Enhanced Translations**: Comprehensive i18n support

### New Entities
- **Rate Limit Sensors** (disabled by default): Monitor API usage
  - `sensor.govee_rate_limit_remaining_minute`
  - `sensor.govee_rate_limit_remaining_day`
- **Action Buttons**:
  - Refresh Scenes: Reload scene lists from Govee API
  - Identify: Flash device for identification

### Improved Features
- **Segment Control**: Individual light entities for RGBIC device segments
- **Group Device Support**: Commands work with optimistic state tracking
- **Scene State Tracking**: Better scene sync with invalidation on manual control

## Breaking Changes

### Entity ID Changes

Some entity IDs may have changed for consistency. If your automations reference specific entity IDs, you may need to update them.

**Before:**
```yaml
entity_id: light.govee_living_room
```

**After:**
```yaml
entity_id: light.govee_h6199_living_room
```

### Configuration Version

The config entry version has been updated to v2. Migration happens automatically on startup.

## Migration Steps

### 1. Backup Your Configuration

Before upgrading, backup your Home Assistant configuration:

```bash
# Create backup
ha backups new --name "pre-govee-upgrade"
```

### 2. Update the Integration

Update to the latest version through HACS or manually.

### 3. Restart Home Assistant

A restart is required to apply the new version.

### 4. Check Repair Issues

After restart, check Settings → System → Repairs for any issues that need attention:

- **Group Device Limitation**: Informational warning about group devices
- **Rate Limit Warning**: Appears if approaching API limits
- **Authentication Failed**: Fixable issue for invalid API keys

### 5. Update Automations

If you have automations using Govee entities, verify they still work:

```yaml
# Check your automations for entity_id references
automation:
  - alias: "Living Room Lights"
    trigger:
      - platform: state
        entity_id: light.govee_h6199_living_room  # Verify this ID
```

### 6. Enable Optional Entities

New entities are disabled by default. Enable them if needed:

1. Go to Settings → Devices & Services → Govee
2. Click on a device
3. Enable the entities you want:
   - Rate limit sensors (for API monitoring)
   - Segment lights (for RGBIC devices)

## FAQ

### Q: My automations stopped working after the update

Check if entity IDs changed. Go to Settings → Devices & Services → Govee and find the new entity IDs for your devices.

### Q: I see a "Group Device Limitation" repair issue

This is informational. Group devices (Govee Home app groups) can receive commands but cannot report their state. The integration uses optimistic state tracking for these devices.

### Q: Rate limit warnings keep appearing

The Govee API has limits of 100 requests/minute and 10,000 requests/day. If you're hitting limits:
- Increase poll interval in integration options
- Reduce the number of automations controlling Govee devices

### Q: How do I control individual segments on my RGBIC light?

1. Go to Settings → Devices & Services → Govee
2. Find your RGBIC device
3. Enable the segment entities (disabled by default)
4. Each segment appears as a separate light entity

### Q: Scene selection doesn't persist in the UI

This is expected behavior. The Govee API doesn't report the current scene, so the integration uses optimistic tracking. The scene state is cleared when you manually change brightness or color.

## Getting Help

If you encounter issues:

1. Check the [GitHub Issues](https://github.com/LaggAt/hacs-govee/issues)
2. Download diagnostics (Settings → System → Repairs → Govee)
3. Create a new issue with the diagnostic data

## Version History

### v2025.12.x (Platinum Refactoring)
- Full Platinum quality scale compliance
- New sensor and button platforms
- Enhanced segment control
- Improved group device handling
- Repair flows for common issues
- Comprehensive diagnostics
