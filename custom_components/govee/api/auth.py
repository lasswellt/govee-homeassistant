"""Govee authentication API for AWS IoT MQTT credentials.

Authenticates with Govee's account API to obtain certificates for AWS IoT MQTT
which provides real-time device state updates.
"""

from __future__ import annotations

import base64
import json
import logging
import time
import uuid
from dataclasses import dataclass
from typing import Any

import aiohttp
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
    pkcs12,
)

from .exceptions import (
    Govee2FACodeInvalidError,
    Govee2FARequiredError,
    GoveeApiError,
    GoveeAuthError,
    GoveeLoginRejectedError,
)

_LOGGER = logging.getLogger(__name__)

_SENSITIVE_FIELDS = frozenset(
    {
        "token",
        "refreshToken",
        "password",
        "p12",
        "p12Pass",
        "p12_pass",
        "privateKey",
        "certificatePem",
        "caCertificate",
        "email",
        "code",
    }
)

GOVEE_LOGIN_URL = "https://app2.govee.com/account/rest/account/v2/login"
GOVEE_VERIFICATION_URL = (
    "https://app2.govee.com/account/rest/account/v1/verification"
)
GOVEE_CLIENT_SETTINGS_URL = (
    "https://app2.govee.com/account/rest/account/v1/client/settings"
)
GOVEE_USER_INFO_URL = "https://app2.govee.com/bi/rest/v1/user-informations"
GOVEE_IOT_KEY_URL = "https://app2.govee.com/app/v1/account/iot/key"
GOVEE_DEVICE_LIST_URL = "https://app2.govee.com/device/rest/devices/v1/list"


def _sanitize_response_for_logging(data: Any) -> Any:
    """Mask sensitive fields in API response for safe logging."""
    if isinstance(data, dict):
        sanitized: dict[str, Any] = {}
        for key, value in data.items():
            if key in _SENSITIVE_FIELDS:
                sanitized[key] = "[REDACTED]"
            elif isinstance(value, dict):
                sanitized[key] = _sanitize_response_for_logging(value)
            elif isinstance(value, list):
                sanitized[key] = [
                    _sanitize_response_for_logging(item)
                    if isinstance(item, (dict, list))
                    else item
                    for item in value
                ]
            elif isinstance(value, str) and len(value) > 100:
                sanitized[key] = f"{value[:50]}...[truncated, {len(value)} chars]"
            else:
                sanitized[key] = value
        return sanitized

    if isinstance(data, list):
        return [
            _sanitize_response_for_logging(item)
            if isinstance(item, (dict, list))
            else item
            for item in data
        ]

    return data


def _sanitize_payload_for_logging(data: dict[str, Any]) -> dict[str, Any]:
    """Mask sensitive request payload fields."""
    return _sanitize_response_for_logging(data)


def _message_indicates_2fa(message: str) -> bool:
    """Return True only when the response message really looks like 2FA."""
    lowered = (message or "").lower()
    hints = (
        "code",
        "2fa",
        "two-factor",
        "verification",
        "verify",
        "otp",
        "captcha",
    )
    return any(hint in lowered for hint in hints)


def _extract_p12_credentials(
    p12_base64: str,
    password: str | None = None,
) -> tuple[str, str]:
    """Extract certificate and private key from P12/PFX container."""
    if not p12_base64:
        raise GoveeApiError("Empty P12 data received from Govee API")

    try:
        cleaned = (
            p12_base64.strip().replace("\n", "").replace("\r", "").replace(" ", "")
        )
        cleaned = cleaned.replace("-", "+").replace("_", "/")

        padding_needed = len(cleaned) % 4
        if padding_needed:
            cleaned += "=" * (4 - padding_needed)

        try:
            p12_data = base64.b64decode(cleaned)
        except Exception as b64_err:
            raise GoveeApiError(f"Base64 decode failed: {b64_err}") from b64_err

        pwd_bytes = password.encode("utf-8") if password else None
        try:
            private_key, certificate, _ = pkcs12.load_key_and_certificates(
                p12_data, pwd_bytes
            )
        except Exception as p12_err:
            raise GoveeApiError(f"P12 container parse failed: {p12_err}") from p12_err

        if private_key is None:
            raise GoveeApiError("No private key found in P12 container")
        if certificate is None:
            raise GoveeApiError("No certificate found in P12 container")

        key_pem = private_key.private_bytes(
            encoding=Encoding.PEM,
            format=PrivateFormat.PKCS8,
            encryption_algorithm=NoEncryption(),
        ).decode("utf-8")

        cert_pem = certificate.public_bytes(Encoding.PEM).decode("utf-8")

        _LOGGER.debug("Successfully extracted certificate and key from P12 container")
        return cert_pem, key_pem

    except GoveeApiError:
        raise
    except Exception as err:
        raise GoveeApiError(f"Failed to parse P12 certificate: {err}") from err


@dataclass(frozen=True)
class GoveeLoginProfile:
    """Represents one request profile for Govee login attempts."""

    name: str
    app_version: str
    client_type: str
    iot_version: str
    user_agent: str
    accept_language: str
    sys_version: str
    timezone: str
    country: str
    client_name: str
    model: str
    version_code: str


DEFAULT_LOGIN_PROFILES: tuple[GoveeLoginProfile, ...] = (
    GoveeLoginProfile(
        name="ios_real_7_4_10",
        app_version="7.4.10",
        client_type="1",
        iot_version="0",
        user_agent=(
            "GoveeHome/7.4.10 "
            "(com.ihoment.GoVeeSensor; build:8; iOS 26.4.0) "
            "Alamofire/5.11.0"
        ),
        accept_language="de",
        sys_version="26.4",
        timezone="Europe/Zurich",
        country="CH",
        client_name="iPhone",
        model="iPhone_15_Pro_Max",
        version_code="8",
    ),
)


@dataclass
class GoveeIotCredentials:
    """Credentials for AWS IoT MQTT connection."""

    token: str
    refresh_token: str
    account_topic: str
    iot_cert: str
    iot_key: str
    iot_ca: str | None
    client_id: str
    endpoint: str

    @property
    def is_valid(self) -> bool:
        """Check if credentials appear valid."""
        return bool(
            self.token and self.iot_cert and self.iot_key and self.account_topic
        )


class GoveeAuthClient:
    """Client for Govee account authentication."""

    def __init__(self, session: aiohttp.ClientSession | None = None) -> None:
        self._session = session
        self._owns_session = session is None

    async def __aenter__(self) -> GoveeAuthClient:
        if self._session is None:
            self._session = aiohttp.ClientSession()
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self.close()

    async def close(self) -> None:
        if self._owns_session and self._session:
            await self._session.close()
            self._session = None

    @staticmethod
    def _build_govee_headers(
        client_id: str | None = None,
        profile: GoveeLoginProfile | None = None,
        authorization: str | None = None,
    ) -> dict[str, str]:
        """Build request headers closely matching the real iOS app."""
        if client_id is None:
            client_id = uuid.uuid4().hex

        if profile is None:
            profile = DEFAULT_LOGIN_PROFILES[0]

        headers = {
            "content-type": "application/json",
            "accept": "*/*",
            "timestamp": str(time.time() * 1000),
            "envid": "0",
            "clientid": client_id,
            "appversion": profile.app_version,
            "accept-language": profile.accept_language,
            "sysversion": profile.sys_version,
            "clienttype": profile.client_type,
            "user-agent": profile.user_agent,
            "timezone": profile.timezone,
            "country": profile.country,
            "iotversion": profile.iot_version,
        }

        if authorization:
            headers["authorization"] = authorization

        return headers

    async def get_user_information(
        self,
        client_id: str,
        profile: GoveeLoginProfile,
    ) -> dict[str, Any]:
        """Pre-login client identity bootstrap."""
        if self._session is None:
            self._session = aiohttp.ClientSession()
            self._owns_session = True

        headers = self._build_govee_headers(client_id=client_id, profile=profile)

        _LOGGER.debug(
            "Fetching user-information with profile=%s client_id=%s",
            profile.name,
            client_id,
        )

        try:
            async with self._session.get(
                GOVEE_USER_INFO_URL,
                headers=headers,
            ) as response:
                data = await response.json(content_type=None)
                _LOGGER.debug(
                    "Govee user-information HTTP response: profile=%s status=%d",
                    profile.name,
                    response.status,
                )

                if response.status != 200:
                    message = (
                        data.get("message", f"HTTP {response.status}")
                        if isinstance(data, dict)
                        else f"HTTP {response.status}"
                    )
                    raise GoveeApiError(
                        f"Failed to get user-information: {message}",
                        code=response.status,
                    )

                if not isinstance(data, dict):
                    raise GoveeApiError("Unexpected non-JSON user-information response")

                json_status = data.get("status")
                if json_status != 200:
                    message = data.get("message", "user-information failed")
                    raise GoveeApiError(
                        f"Failed to get user-information: {message}",
                        code=json_status,
                    )

                return data.get("data", {})

        except aiohttp.ClientError as err:
            raise GoveeApiError(
                f"Connection error getting user-information: {err}"
            ) from err

    async def post_client_settings(
        self,
        token: str,
        client_id: str,
        profile: GoveeLoginProfile,
    ) -> None:
        """Register/update client settings after login."""
        if self._session is None:
            self._session = aiohttp.ClientSession()
            self._owns_session = True

        headers = self._build_govee_headers(
            client_id=client_id,
            profile=profile,
            authorization=f"Bearer {token}",
        )

        payload = {
            "client": client_id,
            "clientName": profile.client_name,
            "versionName": profile.app_version,
            "model": profile.model,
            "versionCode": profile.version_code,
            "sysVersion": profile.sys_version,
            "clientType": profile.client_type,
        }

        _LOGGER.debug(
            "Posting client settings with profile=%s client_id=%s payload=%s",
            profile.name,
            client_id,
            payload,
        )

        try:
            async with self._session.post(
                GOVEE_CLIENT_SETTINGS_URL,
                headers=headers,
                json=payload,
            ) as response:
                data = await response.json(content_type=None)
                _LOGGER.debug(
                    "Govee client-settings HTTP response: profile=%s status=%d",
                    profile.name,
                    response.status,
                )

                if response.status != 200:
                    message = (
                        data.get("message", f"HTTP {response.status}")
                        if isinstance(data, dict)
                        else f"HTTP {response.status}"
                    )
                    raise GoveeApiError(
                        f"Failed to post client settings: {message}",
                        code=response.status,
                    )

                if not isinstance(data, dict):
                    raise GoveeApiError("Unexpected non-JSON client-settings response")

                json_status = data.get("status")
                if json_status != 200:
                    message = data.get("message", "client-settings failed")
                    raise GoveeApiError(
                        f"Failed to post client settings: {message}",
                        code=json_status,
                    )

        except aiohttp.ClientError as err:
            raise GoveeApiError(
                f"Connection error posting client settings: {err}"
            ) from err

    async def get_iot_key(
        self,
        token: str,
        client_id: str,
        profile: GoveeLoginProfile,
    ) -> dict[str, Any]:
        """Fetch IoT credentials from Govee API."""
        if self._session is None:
            self._session = aiohttp.ClientSession()
            self._owns_session = True

        headers = self._build_govee_headers(
            client_id=client_id,
            profile=profile,
            authorization=f"Bearer {token}",
        )

        safe_headers = {
            k: v for k, v in headers.items() if k.lower() != "authorization"
        }
        _LOGGER.debug(
            "Fetching IoT credentials with profile=%s client_id=%s headers=%s",
            profile.name,
            client_id,
            safe_headers,
        )

        try:
            async with self._session.get(
                GOVEE_IOT_KEY_URL,
                headers=headers,
            ) as response:
                data = await response.json(content_type=None)
                _LOGGER.debug(
                    "Govee IoT key HTTP response: profile=%s status=%d",
                    profile.name,
                    response.status,
                )

                if response.status != 200:
                    message = (
                        data.get("message", f"HTTP {response.status}")
                        if isinstance(data, dict)
                        else f"HTTP {response.status}"
                    )
                    _LOGGER.warning(
                        "Govee IoT key request failed: profile=%s status=%d message='%s' response=%s",
                        profile.name,
                        response.status,
                        message,
                        (
                            _sanitize_response_for_logging(data)
                            if isinstance(data, (dict, list))
                            else data
                        ),
                    )
                    raise GoveeApiError(
                        f"Failed to get IoT key: {message}", code=response.status
                    )

                if not isinstance(data, dict):
                    raise GoveeApiError("Unexpected non-JSON IoT key response")

                json_status = data.get("status")
                if json_status != 200:
                    message = data.get("message", "IoT key request failed")
                    _LOGGER.warning(
                        "Govee IoT key error: profile=%s json_status=%s message='%s' response=%s",
                        profile.name,
                        json_status,
                        message,
                        _sanitize_response_for_logging(data),
                    )
                    raise GoveeApiError(
                        f"Failed to get IoT key: {message}", code=json_status
                    )

                return data.get("data", {})

        except aiohttp.ClientError as err:
            _LOGGER.warning(
                "Connection error fetching IoT key for profile=%s: %s (%s)",
                profile.name,
                type(err).__name__,
                str(err),
            )
            raise GoveeApiError(f"Connection error getting IoT key: {err}") from err

    async def fetch_device_topics(self, token: str) -> dict[str, str]:
        """Fetch device-specific MQTT topics from undocumented Govee API.

        This API returns device_ext.device_settings.topic for each device,
        which is required for publishing MQTT commands (ptReal, etc).
        """
        if self._session is None:
            self._session = aiohttp.ClientSession()
            self._owns_session = True

        headers = self._build_govee_headers()
        headers["Authorization"] = f"Bearer {token}"

        try:
            async with self._session.post(
                GOVEE_DEVICE_LIST_URL,
                headers=headers,
                json={},
            ) as response:
                data = await response.json()

                if response.status != 200:
                    message = data.get("message", f"HTTP {response.status}")
                    raise GoveeApiError(
                        f"Failed to get device list: {message}", code=response.status
                    )

                device_topics: dict[str, str] = {}
                devices = data.get("devices", [])

                for device in devices:
                    device_id = device.get("device")
                    if not device_id:
                        continue

                    device_ext = device.get("deviceExt", {})
                    if isinstance(device_ext, str):
                        try:
                            device_ext = json.loads(device_ext)
                        except (json.JSONDecodeError, TypeError):
                            device_ext = {}

                    device_settings = device_ext.get("deviceSettings", {})
                    if isinstance(device_settings, str):
                        try:
                            device_settings = json.loads(device_settings)
                        except (json.JSONDecodeError, TypeError):
                            device_settings = {}

                    topic = device_settings.get("topic")
                    if topic:
                        device_topics[device_id] = topic
                        _LOGGER.debug(
                            "Device %s has MQTT topic: %s...", device_id, topic[:30]
                        )
                    else:
                        is_likely_group = device_id.isdigit() if device_id else False
                        if is_likely_group:
                            _LOGGER.debug(
                                "Group device %s has no MQTT topic (expected - groups are virtual)",
                                device_id,
                            )
                        else:
                            _LOGGER.debug(
                                "Device %s has no MQTT topic in response",
                                device_id,
                            )

                _LOGGER.info("Fetched MQTT topics for %d devices", len(device_topics))
                return device_topics

        except aiohttp.ClientError as err:
            raise GoveeApiError(
                f"Connection error fetching device topics: {err}"
            ) from err

    async def request_verification_code(
        self,
        email: str,
        client_id: str,
    ) -> None:
        """Request Govee to send a 2FA verification code to the user's email."""
        if self._session is None:
            self._session = aiohttp.ClientSession()
            self._owns_session = True

        profile = DEFAULT_LOGIN_PROFILES[0]
        headers = self._build_govee_headers(client_id=client_id, profile=profile)
        payload = {"type": 8, "email": email}

        _LOGGER.debug("Requesting Govee verification code for %s", email)

        try:
            async with self._session.post(
                GOVEE_VERIFICATION_URL,
                json=payload,
                headers=headers,
            ) as response:
                data = await response.json(content_type=None)

                if response.status != 200:
                    _LOGGER.warning(
                        "Verification code request failed: status=%d response=%s",
                        response.status,
                        (
                            _sanitize_response_for_logging(data)
                            if isinstance(data, (dict, list))
                            else data
                        ),
                    )
                    raise GoveeApiError(
                        f"Failed to request verification code: HTTP {response.status}"
                    )

                _LOGGER.debug("Verification code requested for %s", email)

        except aiohttp.ClientError as err:
            raise GoveeApiError(
                f"Connection error requesting verification code: {err}"
            ) from err

    async def _attempt_login_with_profile(
        self,
        email: str,
        password: str,
        client_id: str,
        profile: GoveeLoginProfile,
        code: str | None = None,
    ) -> GoveeIotCredentials:
        """Attempt login with one specific profile."""
        payload: dict[str, Any] = {
            "client": client_id,
            "email": email,
            "password": password,
        }
        if code:
            payload["code"] = code

        headers = self._build_govee_headers(client_id=client_id, profile=profile)

        _LOGGER.debug(
            "Govee login profile=%s headers=%s",
            profile.name,
            headers,
        )
        _LOGGER.debug(
            "Attempting Govee account login with profile=%s payload=%s",
            profile.name,
            _sanitize_payload_for_logging(payload),
        )

        try:
            async with self._session.post(
                GOVEE_LOGIN_URL,
                json=payload,
                headers=headers,
            ) as response:
                data = await response.json(content_type=None)
                _LOGGER.debug(
                    "Govee login HTTP response: profile=%s status=%d",
                    profile.name,
                    response.status,
                )

                if response.status == 401:
                    _LOGGER.debug(
                        "Govee login failed with HTTP 401 for profile=%s. Response=%s",
                        profile.name,
                        (
                            _sanitize_response_for_logging(data)
                            if isinstance(data, (dict, list))
                            else data
                        ),
                    )
                    raise GoveeAuthError("Invalid email or password", code=401)

                if response.status != 200:
                    message = (
                        data.get("message", f"HTTP {response.status}")
                        if isinstance(data, dict)
                        else f"HTTP {response.status}"
                    )
                    _LOGGER.warning(
                        "Govee login failed with HTTP %d for profile=%s: %s. Response=%s",
                        response.status,
                        profile.name,
                        message,
                        (
                            _sanitize_response_for_logging(data)
                            if isinstance(data, (dict, list))
                            else data
                        ),
                    )
                    raise GoveeLoginRejectedError(
                        f"Login rejected (HTTP {response.status}, profile {profile.name}): {message}"
                    )

                if not isinstance(data, dict):
                    raise GoveeApiError(
                        f"Unexpected non-JSON login response for profile {profile.name}"
                    )

                status = data.get("status")
                message = data.get("message", "") or ""

                if status != 200:
                    sanitized = _sanitize_response_for_logging(data)
                    _LOGGER.warning(
                        "Govee login rejected: profile=%s json_status=%s message='%s' response=%s",
                        profile.name,
                        status,
                        message,
                        sanitized,
                    )

                    if status == 454:
                        if _message_indicates_2fa(message):
                            if code:
                                raise Govee2FACodeInvalidError()
                            raise Govee2FARequiredError()

                        raise GoveeLoginRejectedError(
                            f"Login rejected (status 454, profile {profile.name}, ambiguous server response). "
                            f"Message: {message or 'empty message'}"
                        )

                    if status == 401 or "password" in message.lower():
                        raise GoveeAuthError(
                            message or "Invalid email or password", code=status
                        )

                    raise GoveeLoginRejectedError(
                        f"Login rejected (status {status}, profile {profile.name}): "
                        f"{message or 'empty message'}"
                    )

                client_data = data.get("client", {})
                token = client_data.get("token", "")
                if not token:
                    raise GoveeApiError(
                        f"No token in login response for profile {profile.name}"
                    )

                refresh_token = client_data.get("refreshToken", "")
                account_topic = client_data.get("topic", "")

                credentials = GoveeIotCredentials(
                    token=token,
                    refresh_token=refresh_token,
                    account_topic=account_topic,
                    iot_cert="",
                    iot_key="",
                    iot_ca=client_data.get("caCertificate"),
                    client_id=client_id,
                    endpoint="",
                )

                return credentials

        except aiohttp.ContentTypeError as err:
            _LOGGER.warning(
                "Invalid/non-JSON response during Govee login for profile=%s: %s",
                profile.name,
                err,
            )
            raise GoveeApiError(f"Invalid response during login: {err}") from err
        except aiohttp.ClientError as err:
            _LOGGER.warning(
                "Connection error during Govee login for profile=%s: %s (%s)",
                profile.name,
                type(err).__name__,
                str(err),
            )
            raise GoveeApiError(f"Connection error during login: {err}") from err

    async def login(
        self,
        email: str,
        password: str,
        client_id: str | None = None,
        code: str | None = None,
    ) -> GoveeIotCredentials:
        """Login to Govee account to obtain AWS IoT credentials."""
        if self._session is None:
            self._session = aiohttp.ClientSession()
            self._owns_session = True

        if client_id is None:
            # Test value from working iPhone app session
            client_id = "YOUR_CLIENT_ID"
            """client_id = uuid.uuid4().hex"""

        profile = DEFAULT_LOGIN_PROFILES[0]

        _LOGGER.debug(
            "Starting Govee login with profile=%s client_id=%s",
            profile.name,
            client_id,
        )

        # Step 1: pre-login identity bootstrap
        user_info = await self.get_user_information(client_id, profile)
        _LOGGER.debug(
            "Govee user-information returned identity=%s identityType=%s",
            user_info.get("identity"),
            user_info.get("identityType"),
        )

        # Step 2: login
        credentials = await self._attempt_login_with_profile(
            email=email,
            password=password,
            client_id=client_id,
            profile=profile,
            code=code,
        )

        # Step 3: post client settings
        await self.post_client_settings(credentials.token, client_id, profile)

        # Step 4: fetch IoT credentials
        iot_data = await self.get_iot_key(credentials.token, client_id, profile)

        iot_endpoint = iot_data.get(
            "endpoint",
            "aqm3wd1qlc3dy-ats.iot.us-east-1.amazonaws.com",
        )

        cert_pem = iot_data.get("certificatePem", "")
        key_pem = iot_data.get("privateKey", "")

        if not (cert_pem and key_pem):
            p12_base64 = iot_data.get("p12", "")
            p12_password = iot_data.get("p12Pass") or iot_data.get("p12_pass", "")

            if not p12_base64:
                raise GoveeApiError("No certificate data in IoT key response")

            cert_pem, key_pem = _extract_p12_credentials(
                p12_base64, p12_password
            )

        account_id = ""
        try:
            token_parts = credentials.token.split(".")
            if len(token_parts) >= 2:
                import base64 as _b64
                padded = token_parts[1] + "=" * (-len(token_parts[1]) % 4)
                payload = json.loads(_b64.urlsafe_b64decode(padded).decode("utf-8"))
                account_data = payload.get("data", {}).get("account")
                if isinstance(account_data, str):
                    account_data = json.loads(account_data)
                if isinstance(account_data, dict):
                    account_id = str(account_data.get("accountId", ""))
        except Exception:
            account_id = ""

        mqtt_client_id = f"AP/{account_id}/{client_id}" if account_id else client_id

        final_credentials = GoveeIotCredentials(
            token=credentials.token,
            refresh_token=credentials.refresh_token,
            account_topic=credentials.account_topic,
            iot_cert=cert_pem,
            iot_key=key_pem,
            iot_ca=credentials.iot_ca,
            client_id=mqtt_client_id,
            endpoint=iot_endpoint,
        )

        if not final_credentials.is_valid:
            raise GoveeApiError("Missing IoT credentials in response")

        _LOGGER.info(
            "Successfully authenticated with Govee using profile=%s",
            profile.name,
        )
        return final_credentials


async def validate_govee_credentials(
    email: str,
    password: str,
    code: str | None = None,
    client_id: str | None = None,
    session: aiohttp.ClientSession | None = None,
) -> GoveeIotCredentials:
    """Validate Govee account credentials and return IoT credentials."""
    async with GoveeAuthClient(session=session) as client:
        return await client.login(email, password, client_id=client_id, code=code)