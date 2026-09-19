"""Tests for Zenbi API client and models."""

from __future__ import annotations

import base64
from datetime import datetime, timezone
import json
import time
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import pytest

from custom_components.zenbi.api.client import (
    ZenbiApiClient,
    _decode_jwt_exp,
    generate_stable_device_id,
)
from custom_components.zenbi.api.exceptions import (
    ZenbiApiError,
    ZenbiAuthError,
    ZenbiConnectionError,
)
from custom_components.zenbi.api.models import (
    ZenbiAuthResponse,
    ZenbiCalendarItem,
    ZenbiGlobalData,
    ZenbiHomework,
    ZenbiPlanningLabel,
    ZenbiPlanningMeta,
    ZenbiWeeklySchedule,
    extract_student_names,
    parse_quill_delta,
)
from custom_components.zenbi.coordinator import calculate_rolling_window


def _create_mock_jwt(exp_timestamp: int, payload_extra: dict = None) -> str:
    """Helper to create an unverified JWT token with specified expiry."""
    header = base64.urlsafe_b64encode(b'{"alg":"HS256","typ":"JWT"}').decode().rstrip("=")
    payload_data = {"exp": exp_timestamp, "sub": "test_user"}
    if payload_extra:
        payload_data.update(payload_extra)
    payload_json = json.dumps(payload_data).encode()
    payload = base64.urlsafe_b64encode(payload_json).decode().rstrip("=")
    sig = "fake_signature"
    return f"{header}.{payload}.{sig}"


def test_decode_jwt_exp():
    """Test extracting exp from JWT string."""
    future_exp = int(time.time()) + 3600
    token = _create_mock_jwt(future_exp)
    decoded_exp = _decode_jwt_exp(token)
    assert decoded_exp == future_exp

    # Invalid token handling
    assert _decode_jwt_exp("invalid.token") is None
    assert _decode_jwt_exp("") is None


@pytest.mark.asyncio
async def test_authenticate_success():
    """Test successful authentication."""
    future_exp = int(time.time()) + 3600
    mock_token = _create_mock_jwt(future_exp)

    mock_resp = AsyncMock()
    mock_resp.status = 200
    mock_resp.json = AsyncMock(
        return_value={
            "userId": "user-guid-123",
            "token": mock_token,
            "refreshToken": "refresh-token-456",
            "changePassword": False,
        }
    )

    mock_session = MagicMock(spec=aiohttp.ClientSession)
    mock_session.closed = False
    mock_session.post.return_value.__aenter__.return_value = mock_resp

    client = ZenbiApiClient(
        username="test@example.com",
        password="secretpassword",
        unique_device_id="custom-device-id",
        session=mock_session,
    )

    auth_res = await client.authenticate()
    assert auth_res.user_id == "user-guid-123"
    assert auth_res.token == mock_token
    assert client.token == mock_token
    assert client.user_id == "user-guid-123"
    assert not client.is_token_expired()


@pytest.mark.asyncio
async def test_authenticate_invalid_credentials():
    """Test authentication rejection (401)."""
    mock_resp = AsyncMock()
    mock_resp.status = 401
    mock_resp.text = AsyncMock(return_value="Invalid credentials")

    mock_session = MagicMock(spec=aiohttp.ClientSession)
    mock_session.closed = False
    mock_session.post.return_value.__aenter__.return_value = mock_resp

    client = ZenbiApiClient("user", "wrong_password", session=mock_session)
    with pytest.raises(ZenbiAuthError):
        await client.authenticate()


@pytest.mark.asyncio
async def test_authenticate_server_error():
    """Test 500 error during authentication."""
    mock_resp = AsyncMock()
    mock_resp.status = 500
    mock_resp.text = AsyncMock(return_value="Internal Server Error")

    mock_session = MagicMock(spec=aiohttp.ClientSession)
    mock_session.closed = False
    mock_session.post.return_value.__aenter__.return_value = mock_resp

    client = ZenbiApiClient("user", "pass", session=mock_session)
    with pytest.raises(ZenbiApiError):
        await client.authenticate()


@pytest.mark.asyncio
async def test_authenticate_network_failure():
    """Test network connection error."""
    mock_session = MagicMock(spec=aiohttp.ClientSession)
    mock_session.closed = False
    mock_session.post.side_effect = aiohttp.ClientConnectionError("Network is unreachable")

    client = ZenbiApiClient("user", "pass", session=mock_session)
    with pytest.raises(ZenbiConnectionError):
        await client.authenticate()


@pytest.mark.asyncio
async def test_get_calendar_items_and_empty_handling():
    """Test calendar items retrieval and graceful handling of empty responses."""
    future_exp = int(time.time()) + 3600
    token = _create_mock_jwt(future_exp)

    mock_items_data = [
        {
            "id": "event-1",
            "title": "Mathematics",
            "start": "2026-09-21T08:00:00+02:00",
            "end": "2026-09-21T09:30:00+02:00",
            "description": "Algebra chapter 4",
            "note": "Bring calculator",
            "resources": [{"id": "r1", "name": "Room 101"}],
            "substitutes": [{"id": "s1", "name": "Mr. Hansen"}],
            "planning": {
                "elementId": "plan-1",
                "color": "#ff0000",
                "icon": "book",
                "asAbsence": False,
                "worktime": True,
            },
        }
    ]

    mock_session = MagicMock(spec=aiohttp.ClientSession)
    mock_session.closed = False

    # Setup request mock
    mock_resp = AsyncMock()
    mock_resp.status = 200
    mock_resp.json = AsyncMock(return_value=mock_items_data)
    mock_session.request.return_value.__aenter__.return_value = mock_resp

    client = ZenbiApiClient("user", "pass", session=mock_session)
    client._token = token
    client._token_expiry = float(future_exp)

    start = datetime(2026, 9, 21, 0, 0, tzinfo=timezone.utc)
    end = datetime(2026, 9, 27, 23, 59, tzinfo=timezone.utc)
    items = await client.get_calendar_items(start, end)

    assert len(items) == 1
    assert items[0].id == "event-1"
    assert items[0].title == "Mathematics"
    assert items[0].note == "Bring calculator"
    assert items[0].resources[0]["name"] == "Room 101"
    assert items[0].planning is not None
    assert items[0].planning.color == "#ff0000"

    # Test empty response handling ([])
    mock_resp.json = AsyncMock(return_value=[])
    empty_items = await client.get_calendar_items(start, end)
    assert empty_items == []


@pytest.mark.asyncio
async def test_get_planning_labels():
    """Test planning labels parsing."""
    future_exp = int(time.time()) + 3600
    token = _create_mock_jwt(future_exp)

    mock_labels_data = [
        {
            "id": "label-1",
            "title": "Study Day",
            "startDate": "2026-09-22",
            "endDate": "2026-09-22",
            "description": "Independent study at home",
            "color": "#00ff00",
        }
    ]

    mock_session = MagicMock(spec=aiohttp.ClientSession)
    mock_session.closed = False
    mock_resp = AsyncMock()
    mock_resp.status = 200
    mock_resp.json = AsyncMock(return_value=mock_labels_data)
    mock_session.request.return_value.__aenter__.return_value = mock_resp

    client = ZenbiApiClient("user", "pass", session=mock_session)
    client._token = token
    client._token_expiry = float(future_exp)

    labels = await client.get_planning_labels(timeframe_id="tf-123")
    assert len(labels) == 1
    assert labels[0].id == "label-1"
    assert labels[0].title == "Study Day"
    assert labels[0].start_date == "2026-09-22"
    assert labels[0].color == "#00ff00"


@pytest.mark.asyncio
async def test_request_auto_reauth_on_401():
    """Test that client automatically re-authenticates if request returns 401."""
    future_exp = int(time.time()) + 3600
    new_token = _create_mock_jwt(future_exp, {"new": True})

    mock_session = MagicMock(spec=aiohttp.ClientSession)
    mock_session.closed = False

    # 1. First request returns 401
    resp_401 = AsyncMock()
    resp_401.status = 401

    # 2. Auth call returns 200
    resp_auth = AsyncMock()
    resp_auth.status = 200
    resp_auth.json = AsyncMock(
        return_value={"userId": "uid", "token": new_token}
    )
    mock_session.post.return_value.__aenter__.return_value = resp_auth

    # 3. Second request returns 200
    resp_success = AsyncMock()
    resp_success.status = 200
    resp_success.json = AsyncMock(return_value=[{"id": "retry-success", "title": "Success"}])

    mock_session.request.return_value.__aenter__.side_effect = [
        resp_401,
        resp_success,
    ]

    client = ZenbiApiClient("user", "pass", session=mock_session)
    client._token = "old_expired_token"
    client._token_expiry = float(time.time() + 1000)

    res = await client._request("GET", "/test-endpoint")
    assert res == [{"id": "retry-success", "title": "Success"}]
    assert client.token == new_token


def test_calculate_rolling_window():
    """Test the calculation of the rolling 2-week window."""
    # Wednesday, Sept 23, 2026 at 14:30
    wednesday = datetime(2026, 9, 23, 14, 30, 0, tzinfo=timezone.utc)
    start_dt, end_dt = calculate_rolling_window(wednesday)

    # Monday of that week is Sept 21
    assert start_dt.year == 2026
    assert start_dt.month == 9
    assert start_dt.day == 21
    assert start_dt.hour == 0
    assert start_dt.minute == 0
    assert start_dt.second == 0

    # End of rolling window is Monday of the 3rd week (Oct 5 at 00:00:00)
    assert end_dt.year == 2026
    assert end_dt.month == 10
    assert end_dt.day == 5
    assert end_dt.hour == 0
    assert end_dt.minute == 0
    assert end_dt.second == 0


def test_models_parsing():
    """Test models edge cases and fallback values."""
    # Calendar item with missing fields
    cal_item = ZenbiCalendarItem.from_dict({"id": "1", "title": "Test"})
    assert cal_item.id == "1"
    assert cal_item.title == "Test"
    assert cal_item.description == ""
    assert cal_item.note == ""
    assert cal_item.planning is None

    # Planning label with alternative field names
    plan_label = ZenbiPlanningLabel.from_dict(
        {"name": "Holiday", "date": "2026-10-01", "note": "School closed"}
    )
    assert plan_label.title == "Holiday"
    assert plan_label.start_date == "2026-10-01"
    assert plan_label.end_date == "2026-10-01"
    assert plan_label.description == "School closed"

    # Global data fallback
    gd = ZenbiGlobalData.from_dict({"timeframes": [{"id": "tf-fallback-1"}]})
    assert gd.timeframe_id == "tf-fallback-1"


def test_parse_quill_delta():
    """Test Quill delta parsing with Markdown formatting."""
    raw_delta = '{"ops":[{"insert":"Kære forældre\\n\\n"},{"attributes":{"bold":true},"insert":"Vigtigt nyt"},{"insert":"\\nTekst her."}]}'
    parsed = parse_quill_delta(raw_delta)
    assert "**Vigtigt nyt**" in parsed
    assert "Kære forældre" in parsed
    assert "Tekst her." in parsed

    # Plain text unchanged
    assert parse_quill_delta("Standard text") == "Standard text"
    assert parse_quill_delta("") == ""
    assert parse_quill_delta(None) == ""


@pytest.mark.asyncio
async def test_get_homework():
    """Test fetching and parsing homework relations."""
    future_exp = int(time.time()) + 3600
    token = _create_mock_jwt(future_exp)

    mock_hw_data = [
        {
            "id": "22619014-b89e-4e71-9371-3e31c60eeadc",
            "calendarItemId": "365f25e4-b4e4-46d2-baa2-a9b091a0de23",
            "description": '{"ops":[{"insert":"Kom og læs læsebogen s. 20-23\\nArbejdsbogen s. 14 og 15\\n"}]}',
            "date": "2026-09-14T00:00:00+02:00",
            "files": [{"id": "f1", "name": "workpage.pdf"}],
            "updatedTime": "2026-09-11T11:14:28.816424+00:00",
            "updatedByUserId": "f5b8328b-17f2-421a-b85c-49eeb39bb665",
            "madeByUserId": "f5b8328b-17f2-421a-b85c-49eeb39bb665",
        }
    ]

    mock_session = MagicMock(spec=aiohttp.ClientSession)
    mock_session.closed = False
    mock_resp = AsyncMock()
    mock_resp.status = 200
    mock_resp.json = AsyncMock(return_value=mock_hw_data)
    mock_session.request.return_value.__aenter__.return_value = mock_resp

    client = ZenbiApiClient("user", "pass", session=mock_session)
    client._token = token
    client._token_expiry = float(future_exp)

    start = datetime(2026, 9, 14, 0, 0, tzinfo=timezone.utc)
    end = datetime(2026, 9, 28, 0, 0, tzinfo=timezone.utc)
    homeworks = await client.get_homework(start, end)

    assert len(homeworks) == 1
    hw = homeworks[0]
    assert hw.id == "22619014-b89e-4e71-9371-3e31c60eeadc"
    assert hw.calendar_item_id == "365f25e4-b4e4-46d2-baa2-a9b091a0de23"
    assert "Kom og læs læsebogen" in hw.description
    assert hw.date == "2026-09-14T00:00:00+02:00"
    assert len(hw.files) == 1
    assert hw.files[0]["name"] == "workpage.pdf"


@pytest.mark.asyncio
async def test_get_weekly_schedules():
    """Test fetching and parsing weekly schedules / messages."""
    future_exp = int(time.time()) + 3600
    token = _create_mock_jwt(future_exp)

    mock_schedules_data = [
        {
            "id": "ws-123",
            "participantIds": ["part-1"],
            "description": '{"ops":[{"insert":"Kære forældre i Pluto\\n\\nNyt emne om dinosaurer.\\n\\n"},{"attributes":{"bold":true},"insert":"Læsning og lektier"},{"insert":"\\nLæs hver dag."}]}',
            "start": "2026-09-14T00:00:00+02:00",
            "end": "2026-09-21T00:00:00+02:00",
            "files": [{"id": "f1", "name": "Hold (1).png"}],
            "updatedTime": "2026-09-11T15:12:49+00:00",
        },
        {
            "id": "ws-456",
            "participantIds": ["part-2"],
            "start": "2026-09-07T00:00:00+02:00",
            "end": "2026-09-14T00:00:00+02:00",
            "files": [{"id": "f2", "name": "Fritter-kalender-uge-37.png"}],
        }
    ]

    mock_session = MagicMock(spec=aiohttp.ClientSession)
    mock_session.closed = False
    mock_resp = AsyncMock()
    mock_resp.status = 200
    mock_resp.json = AsyncMock(return_value=mock_schedules_data)
    mock_session.request.return_value.__aenter__.return_value = mock_resp

    client = ZenbiApiClient("user", "pass", session=mock_session)
    client._token = token
    client._token_expiry = float(future_exp)

    start = datetime(2026, 9, 14, 0, 0, tzinfo=timezone.utc)
    end = datetime(2026, 9, 28, 0, 0, tzinfo=timezone.utc)
    schedules = await client.get_weekly_schedules(start, end)

    assert len(schedules) == 2
    # Item 1 with text
    assert schedules[0].id == "ws-123"
    assert schedules[0].title == "Kære forældre i Pluto"
    assert "**Læsning og lektier**" in schedules[0].description
    assert schedules[0].start == "2026-09-14T00:00:00+02:00"
    assert schedules[0].end == "2026-09-21T00:00:00+02:00"

    # Item 2 with file fallback title
    assert schedules[1].id == "ws-456"
    assert schedules[1].title == "Fritter-kalender-uge-37.png"


def test_generate_stable_device_id():
    """Test that device ID is deterministic and case-insensitive."""
    id1 = generate_stable_device_id("Student@School.dk")
    id2 = generate_stable_device_id("student@school.dk ")
    assert id1 == id2
    assert len(id1) == 36
    assert id1.count("-") == 4

    # Different username generates different ID
    id3 = generate_stable_device_id("teacher@school.dk")
    assert id1 != id3

    # Client uses stable device ID by default
    client = ZenbiApiClient("Student@School.dk", "password")
    assert client.unique_device_id == id1


def test_danish_and_list_quill_delta_parsing():
    """Test Quill Delta parsing with Danish characters, bullet lists, and HTML entities."""
    # 1. Danish characters preserved losslessly
    danish_delta = json.dumps({
        "ops": [
            {"insert": "Kære forældre i Århus med æbler, pærer og øl.\n"},
            {"insert": "Husk tøj til sne og blæst!\n"}
        ]
    })
    parsed = parse_quill_delta(danish_delta)
    assert "Kære forældre i Århus med æbler, pærer og øl." in parsed
    assert "Husk tøj til sne og blæst!" in parsed

    # 2. Bullet list with bold and HTML entity
    bullet_delta = json.dumps({
        "ops": [
            {"insert": "Pakkeliste &amp; info:"},
            {"insert": "\n", "attributes": {"header": 2}},
            {"insert": "Madpakke"},
            {"insert": "\n", "attributes": {"list": "bullet"}},
            {"insert": "Drikkedunk "},
            {"attributes": {"bold": True}, "insert": "med vand"},
            {"insert": "\n", "attributes": {"list": "bullet"}},
            {"insert": "Gummistøvler"},
            {"insert": "\n", "attributes": {"list": "bullet"}}
        ]
    })
    parsed_bullets = parse_quill_delta(bullet_delta)
    assert "## Pakkeliste & info:" in parsed_bullets
    assert "- Madpakke" in parsed_bullets
    assert "- Drikkedunk **med vand**" in parsed_bullets
    assert "- Gummistøvler" in parsed_bullets


@pytest.mark.asyncio
async def test_client_close_and_session_ownership():
    """Test client.close() behavior for owned vs external session."""
    # 1. External session -> should not be closed by client.close()
    mock_session = MagicMock(spec=aiohttp.ClientSession)
    mock_session.closed = False
    mock_session.close = AsyncMock()
    client_ext = ZenbiApiClient("user", "pass", session=mock_session)
    assert client_ext._owns_session is False
    await client_ext.close()
    mock_session.close.assert_not_called()

    # 2. Owned session -> closed by client.close()
    client_owned = ZenbiApiClient("user", "pass", session=None)
    assert client_owned._owns_session is True
    # Force session creation
    session = client_owned._get_session()
    assert session is not None
    await client_owned.close()
    assert session.closed is True


@pytest.mark.asyncio
async def test_client_get_notifications_placeholder():
    """Test get_notifications placeholder method."""
    client = ZenbiApiClient("user", "pass")
    notifs = await client.get_notifications()
    assert notifs == []
    await client.close()


@pytest.mark.asyncio
async def test_client_authenticate_missing_token():
    """Test authenticate raises ZenbiAuthError when response lacks a token."""
    mock_session = MagicMock(spec=aiohttp.ClientSession)
    mock_session.closed = False
    mock_resp = AsyncMock()
    mock_resp.status = 200
    mock_resp.json = AsyncMock(return_value={"userId": "123", "token": ""})
    mock_session.post.return_value.__aenter__.return_value = mock_resp

    client = ZenbiApiClient("user", "pass", session=mock_session)
    with pytest.raises(ZenbiAuthError, match="did not contain an authentication token"):
        await client.authenticate()


def test_calculate_rolling_window_naive_datetime():
    """Test calculate_rolling_window with a timezone-naive datetime."""
    naive_dt = datetime(2026, 9, 23, 14, 30)  # Wednesday
    start, end = calculate_rolling_window(naive_dt)
    assert start.tzinfo is not None
    assert start.weekday() == 0  # Monday
    assert start.hour == 0
    assert (end - start).days == 14


def test_extract_student_names():
    """Test extracting unique student names from participant models."""
    item1 = ZenbiCalendarItem(
        id="item-1",
        title="Dansk",
        start="2026-09-21T08:00:00+02:00",
        end="2026-09-21T09:00:00+02:00",
        participant_models=[
            {"name": "Albert Hansen", "id": "p1"},
            {"fullName": "Ida Hansen", "id": "p2"},
        ],
    )
    item2 = ZenbiCalendarItem(
        id="item-2",
        title="Matematik",
        start="2026-09-21T09:00:00+02:00",
        end="2026-09-21T10:00:00+02:00",
        participant_models=[
            {"name": "Albert Hansen", "id": "p1"},  # duplicate
            {"title": "Carl Hansen", "id": "p3"},
        ],
    )

    assert item1.student_names == ["Albert Hansen", "Ida Hansen"]
    unique_names = extract_student_names([item1, item2])
    assert unique_names == ["Albert Hansen", "Ida Hansen", "Carl Hansen"]

