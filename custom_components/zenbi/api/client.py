"""Zenbi asynchronous API client."""

from __future__ import annotations

import base64
from datetime import datetime, timezone
import json
import logging
import time
from typing import Any, Dict, List, Optional
import uuid

import aiohttp

from ..const import (
    BASE_URL,
    DEFAULT_USER_AGENT,
    ENDPOINT_AUTHENTICATE,
    ENDPOINT_CALENDAR_ITEMS,
    ENDPOINT_GLOBALDATA,
    ENDPOINT_HOMEWORKS,
    ENDPOINT_PLANNING_LABELS,
    ENDPOINT_WEEKLY_SCHEDULES,
    TWO_FACTOR_NULL_GUID,
)
from .exceptions import ZenbiApiError, ZenbiAuthError, ZenbiConnectionError
from .models import (
    ZenbiAuthResponse,
    ZenbiCalendarItem,
    ZenbiGlobalData,
    ZenbiHomework,
    ZenbiPlanningLabel,
    ZenbiWeeklySchedule,
)

_LOGGER = logging.getLogger(__name__)


def generate_stable_device_id(username: str) -> str:
    """Generate a deterministic UUID from username so Zenbi recognizes the device across runs."""
    clean_username = username.lower().strip()
    return str(uuid.uuid5(uuid.NAMESPACE_DNS, f"zenbi-device-{clean_username}"))


def _decode_jwt_exp(token: str) -> Optional[float]:
    """Extract expiry epoch from JWT token without verifying signature."""
    try:
        parts = token.split(".")
        if len(parts) >= 2:
            payload_b64 = parts[1]
            padded = payload_b64 + "=" * ((4 - len(payload_b64) % 4) % 4)
            payload_bytes = base64.urlsafe_b64decode(padded)
            payload = json.loads(payload_bytes.decode("utf-8"))
            exp = payload.get("exp")
            if exp is not None:
                return float(exp)
    except Exception as err:
        _LOGGER.debug("Failed to decode JWT expiration: %s", err)
    return None


def get_copenhagen_tz() -> Any:
    """Get Europe/Copenhagen timezone, with fallback."""
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo("Europe/Copenhagen")
    except Exception:
        return datetime.now().astimezone().tzinfo or timezone.utc


def format_zenbi_datetime(dt: datetime) -> str:
    """Format datetime for Zenbi API with Copenhagen timezone and millisecond precision."""
    cph_tz = get_copenhagen_tz()
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=cph_tz)
    else:
        dt = dt.astimezone(cph_tz)
    return dt.isoformat(timespec="milliseconds")


class ZenbiApiClient:
    """API client for Zenbi."""

    def __init__(
        self,
        username: str,
        password: str,
        unique_device_id: Optional[str] = None,
        session: Optional[aiohttp.ClientSession] = None,
        base_url: str = BASE_URL,
        user_agent: str = DEFAULT_USER_AGENT,
        token: Optional[str] = None,
        token_expiry: Optional[float] = None,
    ) -> None:
        """Initialize the API client."""
        self.username = username
        self.password = password
        self.unique_device_id = unique_device_id or generate_stable_device_id(username)
        self.base_url = base_url.rstrip("/")
        self.user_agent = user_agent

        self._session = session
        self._owns_session = session is None

        self._token: Optional[str] = token
        self._token_expiry: Optional[float] = token_expiry or (_decode_jwt_exp(token) if token else None)
        self._timeframe_id: Optional[str] = None
        self._user_id: Optional[str] = None

    @property
    def token(self) -> Optional[str]:
        """Return the current auth token."""
        return self._token

    @property
    def token_expiry(self) -> Optional[float]:
        """Return the current token expiry epoch timestamp."""
        return self._token_expiry

    @property
    def timeframe_id(self) -> Optional[str]:
        """Return the cached timeframe ID."""
        return self._timeframe_id

    @property
    def user_id(self) -> Optional[str]:
        """Return the authenticated user ID."""
        return self._user_id

    def _get_session(self) -> aiohttp.ClientSession:
        """Get active ClientSession, creating one if owned and closed."""
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
            self._owns_session = True
        return self._session

    def is_token_expired(self, buffer_seconds: int = 60) -> bool:
        """Check if current token is missing or expired."""
        if not self._token or self._token_expiry is None:
            return True
        return time.time() >= (self._token_expiry - buffer_seconds)

    async def authenticate(self) -> ZenbiAuthResponse:
        """Authenticate with Zenbi API and retrieve JWT token."""
        url = f"{self.base_url}{ENDPOINT_AUTHENTICATE}"
        payload = {
            "username": self.username,
            "password": self.password,
            "twoFactorType": TWO_FACTOR_NULL_GUID,
            "uniqueDeviceId": self.unique_device_id,
        }
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": self.user_agent,
        }

        session = self._get_session()
        try:
            async with session.post(url, json=payload, headers=headers) as response:
                if response.status in (401, 403):
                    error_text = await response.text()
                    _LOGGER.warning("Authentication failed (HTTP %s): %s", response.status, error_text)
                    raise ZenbiAuthError(f"Invalid credentials or access denied: HTTP {response.status}")

                if response.status != 200:
                    error_text = await response.text()
                    _LOGGER.error("Auth request failed (HTTP %s): %s", response.status, error_text)
                    raise ZenbiApiError(f"Authentication failed with status {response.status}: {error_text}")

                data = await response.json()
        except aiohttp.ClientError as err:
            _LOGGER.error("Connection error during authentication: %s", err)
            raise ZenbiConnectionError(f"Failed to connect to Zenbi API: {err}") from err

        auth_response = ZenbiAuthResponse.from_dict(data)
        if not auth_response.token:
            raise ZenbiAuthError("Response did not contain an authentication token")

        self._token = auth_response.token
        self._user_id = auth_response.user_id
        self._token_expiry = _decode_jwt_exp(self._token)
        _LOGGER.debug(
            "Authenticated successfully for user %s (expiry: %s)",
            self._user_id,
            datetime.fromtimestamp(self._token_expiry, tz=timezone.utc).isoformat()
            if self._token_expiry
            else "unknown",
        )
        return auth_response

    async def _request(
        self,
        method: str,
        endpoint: str,
        params: Optional[Dict[str, Any]] = None,
        json_data: Optional[Any] = None,
        retry_on_401: bool = True,
    ) -> Any:
        """Make an authenticated HTTP request to Zenbi."""
        if self.is_token_expired():
            await self.authenticate()

        url = f"{self.base_url}{endpoint}"
        headers = {
            "Authorization": f"Bearer {self._token}",
            "Accept": "application/json",
            "User-Agent": self.user_agent,
        }
        if json_data is not None:
            headers["Content-Type"] = "application/json"

        session = self._get_session()
        try:
            async with session.request(
                method=method,
                url=url,
                params=params,
                json=json_data,
                headers=headers,
            ) as response:
                if response.status == 401 and retry_on_401:
                    _LOGGER.info("Token expired during request (HTTP 401), re-authenticating...")
                    await self.authenticate()
                    return await self._request(
                        method=method,
                        endpoint=endpoint,
                        params=params,
                        json_data=json_data,
                        retry_on_401=False,
                    )

                if response.status in (401, 403):
                    raise ZenbiAuthError(f"Unauthorized or forbidden: HTTP {response.status}")

                if response.status not in (200, 201, 204):
                    text = await response.text()
                    raise ZenbiApiError(f"API request to {endpoint} returned status {response.status}: {text}")

                if response.status == 204:
                    return None

                return await response.json()
        except aiohttp.ClientError as err:
            raise ZenbiConnectionError(f"Network error while requesting {endpoint}: {err}") from err

    async def get_global_data(self) -> ZenbiGlobalData:
        """Fetch global bootstrap data and extract session context."""
        data = await self._request("GET", ENDPOINT_GLOBALDATA)
        global_data = ZenbiGlobalData.from_dict(data or {})
        if global_data.timeframe_id:
            self._timeframe_id = global_data.timeframe_id
            _LOGGER.debug("Cached timeframeId: %s", self._timeframe_id)
        return global_data

    async def get_calendar_items(
        self, start_dt: datetime, end_dt: datetime
    ) -> List[ZenbiCalendarItem]:
        """Fetch timed calendar schedule items for a given date range."""
        params = {
            "start": format_zenbi_datetime(start_dt),
            "end": format_zenbi_datetime(end_dt),
        }
        data = await self._request("GET", ENDPOINT_CALENDAR_ITEMS, params=params)

        if not data:
            return []

        # Handle list vs wrapped object responses
        items_raw: List[Dict[str, Any]] = []
        if isinstance(data, list):
            items_raw = data
        elif isinstance(data, dict):
            for key in ("items", "calendarItems", "data", "calendarItemList"):
                if isinstance(data.get(key), list):
                    items_raw = data[key]
                    break

        return [ZenbiCalendarItem.from_dict(item) for item in items_raw]

    async def get_planning_labels(
        self, timeframe_id: Optional[str] = None
    ) -> List[ZenbiPlanningLabel]:
        """Fetch all-day labels from the planning module."""
        tf_id = timeframe_id or self._timeframe_id
        if not tf_id:
            # Try fetching global data to get timeframe_id
            global_data = await self.get_global_data()
            tf_id = global_data.timeframe_id

        params: Dict[str, str] = {}
        if tf_id:
            params["timeframeId"] = tf_id

        data = await self._request("GET", ENDPOINT_PLANNING_LABELS, params=params)

        if not data:
            return []

        labels_raw: List[Dict[str, Any]] = []
        if isinstance(data, list):
            labels_raw = data
        elif isinstance(data, dict):
            for key in ("labels", "data", "items", "labelList"):
                if isinstance(data.get(key), list):
                    labels_raw = data[key]
                    break

        return [ZenbiPlanningLabel.from_dict(lbl) for lbl in labels_raw]

    async def get_notifications(self) -> List[Dict[str, Any]]:
        """Placeholder for future notifications endpoint."""
        return []

    async def get_homework(
        self, start_dt: datetime, end_dt: datetime
    ) -> List[ZenbiHomework]:
        """Fetch homework items for a given date range."""
        params = {
            "start": format_zenbi_datetime(start_dt),
            "end": format_zenbi_datetime(end_dt),
        }
        data = await self._request("GET", ENDPOINT_HOMEWORKS, params=params)

        if not data:
            return []

        items_raw: List[Dict[str, Any]] = []
        if isinstance(data, list):
            items_raw = data
        elif isinstance(data, dict):
            for key in ("items", "homeworks", "data", "homeworkList"):
                if isinstance(data.get(key), list):
                    items_raw = data[key]
                    break

        return [ZenbiHomework.from_dict(item) for item in items_raw]

    async def get_weekly_schedules(
        self, start_dt: datetime, end_dt: datetime
    ) -> List[ZenbiWeeklySchedule]:
        """Fetch weekly schedules / messages for a given date range."""
        params = {
            "start": format_zenbi_datetime(start_dt),
            "end": format_zenbi_datetime(end_dt),
        }
        data = await self._request("GET", ENDPOINT_WEEKLY_SCHEDULES, params=params)

        if not data:
            return []

        items_raw: List[Dict[str, Any]] = []
        if isinstance(data, list):
            items_raw = data
        elif isinstance(data, dict):
            for key in ("items", "schedules", "weeklySchedules", "data"):
                if isinstance(data.get(key), list):
                    items_raw = data[key]
                    break

        return [ZenbiWeeklySchedule.from_dict(item) for item in items_raw]

    async def close(self) -> None:
        """Close client session if owned by this client."""
        if self._owns_session and self._session and not self._session.closed:
            await self._session.close()

