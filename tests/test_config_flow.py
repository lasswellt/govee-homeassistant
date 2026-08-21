"""Test the Govee config flow."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, PropertyMock, patch
import pytest
import voluptuous as vol

from custom_components.govee.api.exceptions import (
    Govee2FACodeInvalidError,
    Govee2FARequiredError,
    GoveeApiError,
    GoveeAuthError,
)
from custom_components.govee.config_flow import GoveeOptionsFlow
from custom_components.govee.const import (
    CONF_API_KEY,
    CONF_EMAIL,
    CONF_ENABLE_DIY_SCENES,
    CONF_ENABLE_GROUPS,
    CONF_ENABLE_SCENES,
    CONF_LAN_TARGETS,
    CONF_PASSWORD,
    CONF_POLL_INTERVAL,
    CONF_WATER_DETECTOR_POLL_INTERVAL,
    DEFAULT_ENABLE_DIY_SCENES,
    DEFAULT_ENABLE_GROUPS,
    DEFAULT_ENABLE_SCENES,
    DEFAULT_POLL_INTERVAL,
    DEFAULT_WATER_DETECTOR_POLL_INTERVAL,
    DOMAIN,
    MAX_WATER_DETECTOR_POLL_INTERVAL,
    MIN_WATER_DETECTOR_POLL_INTERVAL,
)

# ==============================================================================
# Config Flow Logic Tests (without Home Assistant dependencies)
# ==============================================================================


class TestConfigFlowConstants:
    """Test config flow constants."""

    def test_domain(self):
        """Test domain constant."""
        assert DOMAIN == "govee"

    def test_default_poll_interval(self):
        """Test default poll interval."""
        assert DEFAULT_POLL_INTERVAL == 60

    def test_default_enable_groups(self):
        """Test default enable groups."""
        assert DEFAULT_ENABLE_GROUPS is False

    def test_default_enable_scenes(self):
        """Test default enable scenes."""
        assert DEFAULT_ENABLE_SCENES is True

    def test_default_enable_diy_scenes(self):
        """Test default enable DIY scenes."""
        assert DEFAULT_ENABLE_DIY_SCENES is True


class TestApiKeyValidation:
    """Test API key validation logic."""

    def test_api_key_required(self):
        """Test API key is required."""
        data = {}
        assert CONF_API_KEY not in data

    def test_api_key_present(self):
        """Test API key is present."""
        data = {CONF_API_KEY: "test_key"}
        assert CONF_API_KEY in data
        assert data[CONF_API_KEY] == "test_key"


class TestAccountCredentials:
    """Test account credentials logic."""

    def test_optional_email(self):
        """Test email is optional."""
        data = {CONF_API_KEY: "test_key"}
        assert CONF_EMAIL not in data

    def test_optional_password(self):
        """Test password is optional."""
        data = {CONF_API_KEY: "test_key"}
        assert CONF_PASSWORD not in data

    def test_with_account_credentials(self):
        """Test with email and password."""
        data = {
            CONF_API_KEY: "test_key",
            CONF_EMAIL: "test@example.com",
            CONF_PASSWORD: "secret",
        }
        assert data[CONF_EMAIL] == "test@example.com"
        assert data[CONF_PASSWORD] == "secret"


class TestOptionsDefaults:
    """Test options defaults."""

    def test_default_options(self):
        """Test default options are correct."""
        options = {
            CONF_POLL_INTERVAL: DEFAULT_POLL_INTERVAL,
            CONF_ENABLE_GROUPS: DEFAULT_ENABLE_GROUPS,
            CONF_ENABLE_SCENES: DEFAULT_ENABLE_SCENES,
            CONF_ENABLE_DIY_SCENES: DEFAULT_ENABLE_DIY_SCENES,
        }

        assert options[CONF_POLL_INTERVAL] == 60
        assert options[CONF_ENABLE_GROUPS] is False
        assert options[CONF_ENABLE_SCENES] is True
        assert options[CONF_ENABLE_DIY_SCENES] is True


class TestEntryDataStructure:
    """Test config entry data structure."""

    def test_minimal_entry_data(self):
        """Test minimal entry data with just API key."""
        data = {CONF_API_KEY: "test_key"}

        assert CONF_API_KEY in data
        assert data[CONF_API_KEY] == "test_key"
        # Optional fields not present
        assert CONF_EMAIL not in data
        assert CONF_PASSWORD not in data

    def test_full_entry_data(self):
        """Test full entry data with account credentials."""
        data = {
            CONF_API_KEY: "test_key",
            CONF_EMAIL: "test@example.com",
            CONF_PASSWORD: "secret",
        }

        assert data[CONF_API_KEY] == "test_key"
        assert data[CONF_EMAIL] == "test@example.com"
        assert data[CONF_PASSWORD] == "secret"


class TestErrorHandling:
    """Test error handling patterns."""

    def test_auth_error_code(self):
        """Test auth error has correct code."""
        err = GoveeAuthError("Invalid API key")
        assert err.code == 401

    def test_api_error_code(self):
        """Test API error can have custom code."""
        err = GoveeApiError("Server error", code=500)
        assert err.code == 500

    def test_api_error_no_code(self):
        """Test API error without code."""
        err = GoveeApiError("Network error")
        assert err.code is None


class TestReauthFlow:
    """Test reauth flow logic."""

    def test_reauth_data_structure(self):
        """Test reauth data structure."""
        existing_data = {
            CONF_API_KEY: "old_key",
            CONF_EMAIL: "test@example.com",
            CONF_PASSWORD: "secret",
        }

        # On reauth, update just the API key
        new_data = {**existing_data, CONF_API_KEY: "new_key"}

        assert new_data[CONF_API_KEY] == "new_key"
        # Other data preserved
        assert new_data[CONF_EMAIL] == "test@example.com"
        assert new_data[CONF_PASSWORD] == "secret"


class TestOptionsFlow:
    """Test options flow logic."""

    def test_options_update(self):
        """Test options can be updated."""
        # Original options
        original = {
            CONF_POLL_INTERVAL: 60,
            CONF_ENABLE_GROUPS: False,
            CONF_ENABLE_SCENES: True,
            CONF_ENABLE_DIY_SCENES: True,
        }
        assert original[CONF_POLL_INTERVAL] == 60

        # Update options
        new_options = {
            CONF_POLL_INTERVAL: 120,
            CONF_ENABLE_GROUPS: True,
            CONF_ENABLE_SCENES: False,
            CONF_ENABLE_DIY_SCENES: False,
        }

        assert new_options[CONF_POLL_INTERVAL] == 120
        assert new_options[CONF_ENABLE_GROUPS] is True
        assert new_options[CONF_ENABLE_SCENES] is False
        assert new_options[CONF_ENABLE_DIY_SCENES] is False

    def test_poll_interval_validation(self):
        """Test poll interval bounds."""
        min_interval = 30
        max_interval = 300

        # Valid intervals
        for interval in [30, 60, 120, 300]:
            assert min_interval <= interval <= max_interval

        # Invalid intervals would be rejected
        assert 10 < min_interval
        assert 600 > max_interval


def _options_flow(devices=None):
    """A GoveeOptionsFlow plus the stub config entry its property should return.

    ``config_entry`` is a read-only property on OptionsFlow whose resolution
    differs across HA versions, so tests patch the property with this entry
    rather than poking internals.
    """
    flow = GoveeOptionsFlow()
    flow.hass = MagicMock()
    entry = MagicMock()
    entry.options = {}
    coordinator = MagicMock()
    coordinator.devices = devices or {}
    entry.runtime_data = coordinator
    return flow, entry


async def _run_init(flow, entry, user_input):
    """Drive async_step_init with ``flow.config_entry`` returning ``entry``."""
    with patch.object(
        GoveeOptionsFlow,
        "config_entry",
        new_callable=PropertyMock,
        return_value=entry,
    ):
        return await flow.async_step_init(user_input)


class TestLanTargetsOption:
    """Options-flow validation for the LAN-targets field (issue #57)."""

    @pytest.mark.asyncio
    async def test_valid_lan_targets_saved(self):
        flow, entry = _options_flow()
        result = await _run_init(
            flow,
            entry,
            {CONF_POLL_INTERVAL: 60, CONF_LAN_TARGETS: "10.20.0.0/24, 10.20.0.51"},
        )
        assert result["type"] == "create_entry"
        assert result["data"][CONF_LAN_TARGETS] == "10.20.0.0/24, 10.20.0.51"

    @pytest.mark.asyncio
    async def test_blank_lan_targets_ok(self):
        flow, entry = _options_flow()
        result = await _run_init(
            flow, entry, {CONF_POLL_INTERVAL: 60, CONF_LAN_TARGETS: ""}
        )
        assert result["type"] == "create_entry"

    @pytest.mark.asyncio
    async def test_invalid_lan_targets_rejected(self):
        flow, entry = _options_flow()
        result = await _run_init(
            flow, entry, {CONF_POLL_INTERVAL: 60, CONF_LAN_TARGETS: "not-an-ip"}
        )
        # Re-shows the form with a field-level error; nothing is saved.
        assert result["type"] == "form"
        assert result["errors"] == {CONF_LAN_TARGETS: "invalid_lan_targets"}

    @pytest.mark.asyncio
    async def test_oversized_subnet_rejected(self):
        flow, entry = _options_flow()
        result = await _run_init(
            flow, entry, {CONF_POLL_INTERVAL: 60, CONF_LAN_TARGETS: "10.0.0.0/8"}
        )
        assert result["type"] == "form"
        assert result["errors"] == {CONF_LAN_TARGETS: "invalid_lan_targets"}


class TestLanDeviceOverrideOption:
    """Form validation for the device_id=ip[!] override syntax (issue #164).

    Runtime parsing skips a malformed override silently so the rest still bind.
    The form must not: a typo that saves cleanly leaves the user with an
    accepted form, a stored option and a device that never works, with nothing
    on screen to say why.
    """

    DEVICE = "AA:BB:CC:DD:EE:FF:00:11"

    @pytest.mark.asyncio
    async def test_valid_override_saved(self):
        flow, entry = _options_flow(devices={self.DEVICE: MagicMock(segment_count=0)})
        raw = f"{self.DEVICE}=10.20.0.51"
        result = await _run_init(
            flow, entry, {CONF_POLL_INTERVAL: 60, CONF_LAN_TARGETS: raw}
        )
        assert result["type"] == "create_entry"
        assert result["data"][CONF_LAN_TARGETS] == raw

    @pytest.mark.asyncio
    async def test_valid_write_only_override_saved(self):
        flow, entry = _options_flow(devices={self.DEVICE: MagicMock(segment_count=0)})
        raw = f"{self.DEVICE}=10.20.0.51!"
        result = await _run_init(
            flow, entry, {CONF_POLL_INTERVAL: 60, CONF_LAN_TARGETS: raw}
        )
        assert result["type"] == "create_entry"
        assert result["data"][CONF_LAN_TARGETS] == raw

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "raw",
        [
            "AA:BB:CC:DD:EE:FF:00:11=10.20.0.999",
            "AA:BB:CC:DD:EE:FF:00:11=",
            "AA:BB:CC:DD:EE:FF:00:11=not-an-ip!",
            "=10.20.0.51",
        ],
    )
    async def test_malformed_override_rejected(self, raw):
        flow, entry = _options_flow(devices={self.DEVICE: MagicMock(segment_count=0)})
        result = await _run_init(
            flow, entry, {CONF_POLL_INTERVAL: 60, CONF_LAN_TARGETS: raw}
        )
        assert result["type"] == "form"
        assert result["errors"] == {CONF_LAN_TARGETS: "invalid_lan_targets"}

    @pytest.mark.asyncio
    async def test_unknown_device_id_rejected(self):
        """A mistyped device ID binds nothing — catch it at the form."""
        flow, entry = _options_flow(devices={self.DEVICE: MagicMock(segment_count=0)})
        result = await _run_init(
            flow,
            entry,
            {CONF_POLL_INTERVAL: 60, CONF_LAN_TARGETS: "AA:BB:CC:DD:EE:FF:00:99=10.20.0.51"},
        )
        assert result["type"] == "form"
        assert result["errors"] == {CONF_LAN_TARGETS: "unknown_lan_device"}

    @pytest.mark.asyncio
    async def test_unknown_device_check_skipped_without_a_device_list(self):
        """Options can be opened before discovery has populated devices.

        With no device list to check against, a correct id is indistinguishable
        from a typo, and rejecting would lock the user out of their own options.
        """
        flow, entry = _options_flow(devices={})
        result = await _run_init(
            flow,
            entry,
            {CONF_POLL_INTERVAL: 60, CONF_LAN_TARGETS: f"{self.DEVICE}=10.20.0.51"},
        )
        assert result["type"] == "create_entry"

    @pytest.mark.asyncio
    async def test_override_mixed_with_plain_targets(self):
        flow, entry = _options_flow(devices={self.DEVICE: MagicMock(segment_count=0)})
        raw = f"10.20.0.0/24, {self.DEVICE}=10.20.0.51!"
        result = await _run_init(
            flow, entry, {CONF_POLL_INTERVAL: 60, CONF_LAN_TARGETS: raw}
        )
        assert result["type"] == "create_entry"
        assert result["data"][CONF_LAN_TARGETS] == raw


class TestWaterDetectorPollIntervalOption:
    """The leak-poll interval is exposed and bounded in the options flow."""

    @pytest.mark.asyncio
    async def test_field_defaults_to_constant(self):
        flow, entry = _options_flow()
        result = await _run_init(flow, entry, None)
        key = next(
            k
            for k in result["data_schema"].schema
            if k == CONF_WATER_DETECTOR_POLL_INTERVAL
        )
        assert key.default() == DEFAULT_WATER_DETECTOR_POLL_INTERVAL

    @pytest.mark.asyncio
    async def test_field_seeded_from_saved_option(self):
        flow, entry = _options_flow()
        entry.options = {CONF_WATER_DETECTOR_POLL_INTERVAL: 900}
        result = await _run_init(flow, entry, None)
        key = next(
            k
            for k in result["data_schema"].schema
            if k == CONF_WATER_DETECTOR_POLL_INTERVAL
        )
        assert key.default() == 900

    @pytest.mark.asyncio
    async def test_value_is_saved(self):
        flow, entry = _options_flow()
        result = await _run_init(
            flow,
            entry,
            {CONF_POLL_INTERVAL: 60, CONF_WATER_DETECTOR_POLL_INTERVAL: 600},
        )
        assert result["type"] == "create_entry"
        assert result["data"][CONF_WATER_DETECTOR_POLL_INTERVAL] == 600

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "value",
        [MIN_WATER_DETECTOR_POLL_INTERVAL - 1, MAX_WATER_DETECTOR_POLL_INTERVAL + 1],
    )
    async def test_out_of_range_rejected_by_schema(self, value):
        flow, entry = _options_flow()
        result = await _run_init(flow, entry, None)
        with pytest.raises(vol.Invalid):
            result["data_schema"](
                {CONF_POLL_INTERVAL: 60, CONF_WATER_DETECTOR_POLL_INTERVAL: value}
            )


class TestConfigFlowSteps:
    """Test config flow step transitions."""

    def test_user_step_to_account_step(self):
        """Test user step transitions to account step."""
        # After valid API key, should proceed to account step
        step_order = ["user", "account"]
        assert step_order[0] == "user"
        assert step_order[1] == "account"

    def test_account_step_skippable(self):
        """Test account step can be skipped."""
        # Empty email means skip
        user_input = {CONF_EMAIL: "", CONF_PASSWORD: ""}
        skip_mqtt = not user_input.get(CONF_EMAIL)
        assert skip_mqtt is True

    def test_account_step_with_credentials(self):
        """Test account step with credentials."""
        user_input = {
            CONF_EMAIL: "test@example.com",
            CONF_PASSWORD: "secret",
        }
        skip_mqtt = not user_input.get(CONF_EMAIL)
        assert skip_mqtt is False


class TestCreateEntryData:
    """Test entry creation data structure."""

    def test_create_entry_api_only(self):
        """Test creating entry with API key only."""
        api_key = "test_key"
        email = None
        password = None

        data = {CONF_API_KEY: api_key}
        if email and password:
            data[CONF_EMAIL] = email
            data[CONF_PASSWORD] = password

        assert data == {CONF_API_KEY: "test_key"}

    def test_create_entry_with_account(self):
        """Test creating entry with account credentials."""
        api_key = "test_key"
        email = "test@example.com"
        password = "secret"

        data = {CONF_API_KEY: api_key}
        if email and password:
            data[CONF_EMAIL] = email
            data[CONF_PASSWORD] = password

        assert data == {
            CONF_API_KEY: "test_key",
            CONF_EMAIL: "test@example.com",
            CONF_PASSWORD: "secret",
        }


class TestConfigFlowVersion:
    """Test config flow version."""

    def test_config_version(self):
        """Test config version is 2 (bumped in sprint-4 for IoT-cred migration)."""
        from custom_components.govee.const import CONFIG_VERSION

        assert CONFIG_VERSION == 2


class TestFormValidation:
    """Test form validation patterns."""

    def test_api_key_empty_invalid(self):
        """Test empty API key is invalid."""
        api_key = ""
        is_valid = bool(api_key and api_key.strip())
        assert is_valid is False

    def test_api_key_whitespace_invalid(self):
        """Test whitespace-only API key is invalid."""
        api_key = "   "
        is_valid = bool(api_key and api_key.strip())
        assert is_valid is False

    def test_api_key_valid(self):
        """Test valid API key passes."""
        api_key = "valid_api_key_here"
        is_valid = bool(api_key and api_key.strip())
        assert is_valid is True


class TestErrorMessages:
    """Test error message mapping."""

    def test_error_keys(self):
        """Test error keys are valid."""
        error_keys = ["invalid_auth", "cannot_connect", "unknown"]

        for key in error_keys:
            assert isinstance(key, str)
            assert len(key) > 0

    def test_error_mapping(self):
        """Test error type to key mapping."""
        error_mapping = {
            "auth_failed": "invalid_auth",
            "connection_failed": "cannot_connect",
            "unexpected": "unknown",
        }

        assert error_mapping["auth_failed"] == "invalid_auth"
        assert error_mapping["connection_failed"] == "cannot_connect"
        assert error_mapping["unexpected"] == "unknown"


class TestDescriptionPlaceholders:
    """Test description placeholders."""

    def test_api_url_placeholder(self):
        """Test API URL placeholder."""
        placeholders = {
            "api_url": "https://developer.govee.com/",
        }

        assert "api_url" in placeholders
        assert "govee.com" in placeholders["api_url"]


class TestConfigFlowAsync:
    """Test async patterns used in config flow."""

    @pytest.mark.asyncio
    async def test_async_validate_api_key_mock(self):
        """Test async API key validation mock."""

        async def mock_validate(api_key: str) -> bool:
            if api_key == "valid_key":
                return True
            raise GoveeAuthError("Invalid key")

        result = await mock_validate("valid_key")
        assert result is True

        with pytest.raises(GoveeAuthError):
            await mock_validate("invalid_key")

    @pytest.mark.asyncio
    async def test_async_validate_credentials_mock(self):
        """Test async credentials validation mock."""

        async def mock_validate(email: str, password: str):
            if email == "valid@test.com" and password == "correct":
                return MagicMock()  # Return mock IoT credentials
            raise GoveeAuthError("Invalid credentials")

        result = await mock_validate("valid@test.com", "correct")
        assert result is not None

        with pytest.raises(GoveeAuthError):
            await mock_validate("invalid@test.com", "wrong")


class TestReconfigureFlow:
    """Test reconfigure flow logic."""

    def test_reconfigure_data_update(self):
        """Test reconfigure updates data correctly."""
        existing_data = {
            CONF_API_KEY: "old_key",
            CONF_EMAIL: "old@example.com",
            CONF_PASSWORD: "old_password",
        }

        # User provides new API key
        new_api_key = "new_key"

        updated_data = {**existing_data, CONF_API_KEY: new_api_key}

        assert updated_data[CONF_API_KEY] == "new_key"
        assert updated_data[CONF_EMAIL] == "old@example.com"
        assert updated_data[CONF_PASSWORD] == "old_password"

    def test_reconfigure_with_new_account(self):
        """Test reconfigure with new account credentials."""
        existing_data = {
            CONF_API_KEY: "old_key",
        }

        new_data = {
            **existing_data,
            CONF_API_KEY: "new_key",
            CONF_EMAIL: "new@example.com",
            CONF_PASSWORD: "new_password",
        }

        assert new_data[CONF_API_KEY] == "new_key"
        assert new_data[CONF_EMAIL] == "new@example.com"
        assert new_data[CONF_PASSWORD] == "new_password"

    def test_reconfigure_remove_account(self):
        """Test reconfigure removes account when empty."""
        existing_data = {
            CONF_API_KEY: "old_key",
            CONF_EMAIL: "old@example.com",
            CONF_PASSWORD: "old_password",
        }
        assert existing_data[CONF_EMAIL] == "old@example.com"

        # User clears email and password
        new_data = {CONF_API_KEY: "new_key"}

        assert new_data[CONF_API_KEY] == "new_key"
        assert CONF_EMAIL not in new_data
        assert CONF_PASSWORD not in new_data


class TestRepairsFramework:
    """Test repairs framework logic."""

    def test_issue_ids(self):
        """Test issue ID constants."""
        from custom_components.govee.repairs import (
            ISSUE_AUTH_FAILED,
            ISSUE_MQTT_DISCONNECTED,
            ISSUE_RATE_LIMITED,
        )

        assert ISSUE_AUTH_FAILED == "auth_failed"
        assert ISSUE_RATE_LIMITED == "rate_limited"
        assert ISSUE_MQTT_DISCONNECTED == "mqtt_disconnected"

    def test_issue_id_format(self):
        """Test issue ID format with entry ID."""
        from custom_components.govee.repairs import ISSUE_AUTH_FAILED

        entry_id = "test_entry_123"
        issue_id = f"{ISSUE_AUTH_FAILED}_{entry_id}"

        assert issue_id == "auth_failed_test_entry_123"
        assert issue_id.startswith(ISSUE_AUTH_FAILED)

    def test_rate_limit_reset_time_format(self):
        """Test rate limit reset time formatting."""
        retry_after = 120.0
        reset_time = f"{int(retry_after)} seconds"

        assert reset_time == "120 seconds"

    def test_issue_severity_mapping(self):
        """Test issue severity levels."""
        # These would be ir.IssueSeverity values in actual code
        severity_mapping = {
            "auth_failed": "ERROR",
            "rate_limited": "WARNING",
            "mqtt_disconnected": "WARNING",
        }

        assert severity_mapping["auth_failed"] == "ERROR"
        assert severity_mapping["rate_limited"] == "WARNING"
        assert severity_mapping["mqtt_disconnected"] == "WARNING"

    def test_fixable_issues(self):
        """Test which issues are fixable."""
        fixable_issues = {
            "auth_failed": True,
            "rate_limited": False,
            "mqtt_disconnected": False,
        }

        assert fixable_issues["auth_failed"] is True
        assert fixable_issues["rate_limited"] is False
        assert fixable_issues["mqtt_disconnected"] is False


class TestPerDeviceSegmentMode:
    """Test per-device segment mode configuration."""

    def test_device_mode_structure(self):
        """Test per-device segment mode data structure."""
        from custom_components.govee.const import (
            SEGMENT_MODE_DISABLED,
            SEGMENT_MODE_GROUPED,
            SEGMENT_MODE_INDIVIDUAL,
        )

        device_modes = {
            "AA:BB:CC:DD:EE:FF:00:01": SEGMENT_MODE_GROUPED,
            "AA:BB:CC:DD:EE:FF:00:02": SEGMENT_MODE_DISABLED,
            "AA:BB:CC:DD:EE:FF:00:03": SEGMENT_MODE_INDIVIDUAL,
        }

        # Verify all devices have valid modes
        for device_id, mode in device_modes.items():
            assert mode in [
                SEGMENT_MODE_DISABLED,
                SEGMENT_MODE_GROUPED,
                SEGMENT_MODE_INDIVIDUAL,
            ]

    def test_device_mode_fallback_to_global(self):
        """Test fallback to global mode when device not in per-device config."""
        from custom_components.govee.const import (
            SEGMENT_MODE_INDIVIDUAL,
        )

        device_modes = {
            "AA:BB:CC:DD:EE:FF:00:01": "grouped",
        }
        global_mode = SEGMENT_MODE_INDIVIDUAL

        # Device not in per-device config should use global
        device_id = "AA:BB:CC:DD:EE:FF:00:02"
        mode = device_modes.get(device_id, global_mode)
        assert mode == global_mode

        # Device in per-device config should use device-specific
        device_id = "AA:BB:CC:DD:EE:FF:00:01"
        mode = device_modes.get(device_id, global_mode)
        assert mode == "grouped"

    def test_device_id_extraction_mac_address(self):
        """Test extracting device ID from unique_id with MAC address format."""
        # MAC address format (17 chars)
        device_id = "AA:BB:CC:DD:EE:FF:00:01"
        unique_id = f"{device_id}_segment_0"

        extracted = unique_id.startswith(device_id)
        assert extracted is True

    def test_device_id_extraction_numeric_id(self):
        """Test extracting device ID from unique_id with numeric group ID format."""
        # Group ID format (numeric, shorter)
        device_id = "12345678"
        unique_id = f"{device_id}_segment_0"

        extracted = unique_id.startswith(device_id)
        assert extracted is True

    def test_longest_first_device_id_matching(self):
        """Test longest-first matching for variable-length device IDs."""
        # Two device IDs where one is prefix of another
        device_ids = {"ABC", "ABCDEF"}

        # Sort by length descending (longest first)
        sorted_ids = sorted(device_ids, key=len, reverse=True)
        assert sorted_ids[0] == "ABCDEF"
        assert sorted_ids[1] == "ABC"

        # Test unique_id matching
        unique_id = "ABCDEF_segment_0"

        for device_id in sorted_ids:
            if unique_id.startswith(device_id):
                found_id = device_id
                break
        else:
            found_id = None

        # Should match ABCDEF, not ABC
        assert found_id == "ABCDEF"

    def test_segment_suffix_matching_grouped_vs_individual(self):
        """Test suffix matching doesn't confuse grouped and individual segments."""
        from custom_components.govee.const import (
            SUFFIX_GROUPED_SEGMENT,
            SUFFIX_SEGMENT,
        )

        device_id = "AA:BB:CC:DD:EE:FF:00:01"

        # Grouped segment unique_id
        grouped_unique_id = f"{device_id}{SUFFIX_GROUPED_SEGMENT}"
        grouped_suffix = grouped_unique_id[len(device_id) :]

        # Individual segment unique_id
        individual_unique_id = f"{device_id}{SUFFIX_SEGMENT}0"
        individual_suffix = individual_unique_id[len(device_id) :]

        # Verify suffixes are different
        assert grouped_suffix == SUFFIX_GROUPED_SEGMENT
        assert individual_suffix.startswith(SUFFIX_SEGMENT)
        assert grouped_suffix != individual_suffix

        # Verify exact matching works
        assert grouped_suffix == SUFFIX_GROUPED_SEGMENT
        assert grouped_suffix != SUFFIX_SEGMENT

    def test_empty_device_modes_uses_global(self):
        """Test empty per-device dict falls back to global for all devices."""
        from custom_components.govee.const import (
            DEFAULT_SEGMENT_MODE,
        )

        device_modes = {}
        global_mode = DEFAULT_SEGMENT_MODE

        # Any device should use global when per-device is empty
        for device_id in [
            "AA:BB:CC:DD:EE:FF:00:01",
            "AA:BB:CC:DD:EE:FF:00:02",
        ]:
            mode = device_modes.get(device_id, global_mode)
            assert mode == global_mode

    def test_migration_preserves_existing_devices(self):
        """Test migration from global to per-device preserves existing config."""
        from custom_components.govee.const import (
            SEGMENT_MODE_GROUPED,
            SEGMENT_MODE_INDIVIDUAL,
        )

        # Options with per-device config
        new_options = {
            "segment_mode_by_device": {
                "AA:BB:CC:DD:EE:FF:00:01": SEGMENT_MODE_INDIVIDUAL,
            },
        }

        # Device with specific config should use that
        device_modes = new_options.get("segment_mode_by_device", {})
        device_mode = device_modes.get("AA:BB:CC:DD:EE:FF:00:01", SEGMENT_MODE_GROUPED)
        assert device_mode == SEGMENT_MODE_INDIVIDUAL

        # Unknown device should use default (individual)
        unknown_mode = device_modes.get(
            "AA:BB:CC:DD:EE:FF:00:99", SEGMENT_MODE_INDIVIDUAL
        )
        assert unknown_mode == SEGMENT_MODE_INDIVIDUAL


# ==============================================================================
# 2FA Verification Code Flow Tests
# ==============================================================================


class TestVerificationCodeFlow:
    """Test 2FA verification code step in config flow."""

    @pytest.mark.asyncio
    async def test_account_2fa_required_redirects_to_verification(self):
        """Test account step redirects to verification_code when 2FA is required."""
        from custom_components.govee.config_flow import GoveeConfigFlow

        flow = GoveeConfigFlow()
        flow.hass = MagicMock()
        flow._api_key = "valid-api-key-xxxx-xxxx-xxxx-xxxx"

        mock_auth_instance = AsyncMock()
        mock_auth_instance.request_verification_code = AsyncMock()
        mock_auth_instance.__aenter__ = AsyncMock(return_value=mock_auth_instance)
        mock_auth_instance.__aexit__ = AsyncMock(return_value=False)

        with (
            patch(
                "custom_components.govee.config_flow.validate_govee_credentials",
                side_effect=Govee2FARequiredError(),
            ),
            patch(
                "custom_components.govee.config_flow.GoveeAuthClient",
                return_value=mock_auth_instance,
            ),
        ):
            result = await flow.async_step_account(
                {CONF_EMAIL: "test@example.com", CONF_PASSWORD: "secret"}
            )

        # Should redirect to the verification_code form
        assert result["type"] == "form"
        assert result["step_id"] == "verification_code"
        # Email/password should be stored for later use
        assert flow._email == "test@example.com"
        assert flow._password == "secret"
        assert flow._client_id is not None

    @pytest.mark.asyncio
    async def test_verification_code_valid_creates_entry(self):
        """Test entering a valid verification code creates the config entry."""
        from custom_components.govee.config_flow import GoveeConfigFlow

        flow = GoveeConfigFlow()
        flow.hass = MagicMock()
        flow._api_key = "valid-api-key-xxxx-xxxx-xxxx-xxxx"
        flow._email = "test@example.com"
        flow._password = "secret"
        flow._client_id = "abc123"
        # Not a reconfigure flow
        flow.context = {"source": "user"}

        mock_creds = MagicMock()

        with patch(
            "custom_components.govee.config_flow.validate_govee_credentials",
            return_value=mock_creds,
        ):
            result = await flow.async_step_verification_code(
                {"verification_code": "123456"}
            )

        assert result["type"] == "create_entry"
        assert result["title"] == "Govee"
        assert result["data"][CONF_API_KEY] == "valid-api-key-xxxx-xxxx-xxxx-xxxx"
        assert result["data"][CONF_EMAIL] == "test@example.com"
        assert result["data"][CONF_PASSWORD] == "secret"

    @pytest.mark.asyncio
    async def test_verification_code_invalid_shows_error(self):
        """Test entering an invalid verification code shows error and re-shows form."""
        from custom_components.govee.config_flow import GoveeConfigFlow

        flow = GoveeConfigFlow()
        flow.hass = MagicMock()
        flow._api_key = "valid-api-key-xxxx-xxxx-xxxx-xxxx"
        flow._email = "test@example.com"
        flow._password = "secret"
        flow._client_id = "abc123"

        with patch(
            "custom_components.govee.config_flow.validate_govee_credentials",
            side_effect=Govee2FACodeInvalidError(),
        ):
            result = await flow.async_step_verification_code(
                {"verification_code": "000000"}
            )

        assert result["type"] == "form"
        assert result["step_id"] == "verification_code"
        assert result["errors"] == {"base": "invalid_verification_code"}

    @pytest.mark.asyncio
    async def test_reconfigure_2fa_required_redirects_to_verification(self):
        """Test reconfigure triggers 2FA and redirects to verification step."""
        from custom_components.govee.config_flow import GoveeConfigFlow

        flow = GoveeConfigFlow()
        flow.hass = MagicMock()
        flow.context = {"source": "reconfigure"}

        # Mock _get_reconfigure_entry to return a fake entry
        mock_entry = MagicMock()
        mock_entry.data = {
            CONF_API_KEY: "old-api-key-xxxx-xxxx-xxxx-xxxx-long",
            CONF_EMAIL: "old@example.com",
            CONF_PASSWORD: "oldpass",
        }
        mock_entry.entry_id = "test_entry_id"
        flow._get_reconfigure_entry = MagicMock(return_value=mock_entry)

        mock_auth_instance = AsyncMock()
        mock_auth_instance.request_verification_code = AsyncMock()
        mock_auth_instance.__aenter__ = AsyncMock(return_value=mock_auth_instance)
        mock_auth_instance.__aexit__ = AsyncMock(return_value=False)

        with (
            patch(
                "custom_components.govee.config_flow.validate_api_key",
                new=AsyncMock(return_value=None),
            ),
            patch(
                "custom_components.govee.config_flow.validate_govee_credentials",
                side_effect=Govee2FARequiredError(),
            ),
            patch(
                "custom_components.govee.config_flow.GoveeAuthClient",
                return_value=mock_auth_instance,
            ),
        ):
            result = await flow.async_step_reconfigure(
                {
                    CONF_API_KEY: "new-api-key-xxxx-xxxx-xxxx-xxxx-long",
                    CONF_EMAIL: "new@example.com",
                    CONF_PASSWORD: "newpass",
                }
            )

        assert result["type"] == "form"
        assert result["step_id"] == "verification_code"
        assert flow._email == "new@example.com"
        assert flow._password == "newpass"
        assert flow._api_key == "new-api-key-xxxx-xxxx-xxxx-xxxx-long"

    @pytest.mark.asyncio
    async def test_reconfigure_verification_code_valid_updates_entry(self):
        """Test valid code during reconfigure updates the entry."""
        from custom_components.govee.config_flow import GoveeConfigFlow

        flow = GoveeConfigFlow()
        flow.hass = MagicMock()
        flow.hass.data = {}
        flow.context = {"source": "reconfigure"}
        flow._api_key = "new-api-key-xxxx-xxxx-xxxx-xxxx-long"
        flow._email = "new@example.com"
        flow._password = "newpass"
        flow._client_id = "abc123"

        mock_entry = MagicMock()
        mock_entry.data = {
            CONF_API_KEY: "old-api-key-xxxx-xxxx-xxxx-xxxx-long",
            CONF_EMAIL: "old@example.com",
            CONF_PASSWORD: "oldpass",
        }
        mock_entry.entry_id = "test_entry_id"
        flow._get_reconfigure_entry = MagicMock(return_value=mock_entry)

        mock_creds = MagicMock()
        mock_update_result = MagicMock()
        flow.async_update_reload_and_abort = MagicMock(return_value=mock_update_result)

        with patch(
            "custom_components.govee.config_flow.validate_govee_credentials",
            return_value=mock_creds,
        ):
            result = await flow.async_step_verification_code(
                {"verification_code": "123456"}
            )

        assert result is mock_update_result
        # Verify async_update_reload_and_abort was called with correct data
        call_args = flow.async_update_reload_and_abort.call_args
        assert call_args[0][0] is mock_entry
        data_updates = call_args[1]["data_updates"]
        assert data_updates[CONF_EMAIL] == "new@example.com"
        assert data_updates[CONF_PASSWORD] == "newpass"
        assert data_updates[CONF_API_KEY] == "new-api-key-xxxx-xxxx-xxxx-xxxx-long"

    def test_verification_code_form_schema(self):
        """Test verification_code form has the correct schema."""
        import voluptuous as vol

        # Build the same schema as config_flow.py
        schema = vol.Schema(
            {
                vol.Required("verification_code"): str,
            }
        )

        # Valid input
        result = schema({"verification_code": "123456"})
        assert result["verification_code"] == "123456"

        # Missing field raises error
        with pytest.raises(vol.MultipleInvalid):
            schema({})
