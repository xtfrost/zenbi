"""Tests for Home Assistant integration components (coordinator, calendar, flows, init)."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.zenbi import (
    async_reload_entry,
    async_setup_entry,
    async_unload_entry,
)
from custom_components.zenbi.api.exceptions import (
    ZenbiApiError,
    ZenbiAuthError,
    ZenbiConnectionError,
)
from custom_components.zenbi.api.models import (
    ZenbiCalendarItem,
    ZenbiHomework,
    ZenbiPlanningLabel,
    ZenbiPlanningMeta,
    ZenbiWeeklySchedule,
)
from custom_components.zenbi.diagnostics import async_get_config_entry_diagnostics
from custom_components.zenbi.calendar import (
    ZenbiPlanningCalendarEntity,
    ZenbiScheduleCalendarEntity,
    ZenbiWeeklyMessagesCalendarEntity,
    async_setup_entry as async_setup_calendar_entry,
)
from custom_components.zenbi.config_flow import ZenbiConfigFlow
from custom_components.zenbi.const import (
    CONF_CALENDAR_SYNC_INTERVAL_HOURS,
    CONF_NOTIFICATION_SYNC_INTERVAL_MINS,
    CONF_PASSWORD,
    CONF_UNIQUE_DEVICE_ID,
    CONF_USERNAME,
    DEFAULT_CALENDAR_SYNC_INTERVAL_HOURS,
    DEFAULT_NOTIFICATION_SYNC_INTERVAL_MINS,
    DOMAIN,
)
from custom_components.zenbi.coordinator import (
    ZenbiCalendarData,
    ZenbiCalendarDataUpdateCoordinator,
)
from custom_components.zenbi.options_flow import ZenbiOptionsFlowHandler
from tests.conftest import MockConfigEntry, MockHomeAssistant, UpdateFailed


@pytest.fixture
def mock_hass():
    return MockHomeAssistant()


@pytest.fixture
def mock_config_entry():
    return MockConfigEntry(
        entry_id="entry_123",
        title="student@school.dk",
        data={
            CONF_USERNAME: "student@school.dk",
            CONF_PASSWORD: "testpassword",
            CONF_UNIQUE_DEVICE_ID: "device-uuid-123",
        },
        options={
            CONF_CALENDAR_SYNC_INTERVAL_HOURS: 24,
            CONF_NOTIFICATION_SYNC_INTERVAL_MINS: 15,
        },
    )


@pytest.fixture
def mock_client():
    client = MagicMock()
    client.get_calendar_items = AsyncMock(return_value=[])
    client.get_planning_labels = AsyncMock(return_value=[])
    client.get_homework = AsyncMock(return_value=[])
    client.get_weekly_schedules = AsyncMock(return_value=[])
    client.authenticate = AsyncMock()
    return client


@pytest.mark.asyncio
async def test_coordinator_update_success(mock_hass, mock_config_entry, mock_client):
    """Test coordinator updates data successfully."""
    item = ZenbiCalendarItem(
        id="item-1",
        title="Physics",
        start="2026-09-21T10:00:00+02:00",
        end="2026-09-21T11:30:00+02:00",
    )
    label = ZenbiPlanningLabel(
        id="label-1",
        title="Exam Week",
        start_date="2026-09-21",
    )

    mock_hw = ZenbiHomework(
        id="hw-1",
        calendar_item_id="item-1",
        description="Read page 20-23",
        raw_description="...",
        date="2026-09-21",
    )

    mock_client.get_calendar_items.return_value = [item]
    mock_client.get_planning_labels.return_value = [label]
    mock_client.get_homework.return_value = [mock_hw]

    coordinator = ZenbiCalendarDataUpdateCoordinator(mock_hass, mock_client, mock_config_entry)
    data = await coordinator._async_update_data()

    assert len(data.calendar_items) == 1
    assert data.calendar_items[0].title == "Physics"
    assert len(data.calendar_items[0].homework) == 1
    assert data.calendar_items[0].homework[0].description == "Read page 20-23"
    assert len(data.planning_labels) == 1
    assert data.planning_labels[0].title == "Exam Week"
    assert len(data.homeworks) == 1
    assert data.window_start is not None
    assert data.window_end is not None


@pytest.mark.asyncio
async def test_coordinator_update_failure(mock_hass, mock_config_entry, mock_client):
    """Test coordinator handles API failure with UpdateFailed."""
    mock_client.get_calendar_items.side_effect = ZenbiAuthError("Auth failed")

    coordinator = ZenbiCalendarDataUpdateCoordinator(mock_hass, mock_client, mock_config_entry)
    with pytest.raises(UpdateFailed):
        await coordinator._async_update_data()


@pytest.mark.asyncio
async def test_coordinator_on_demand_caching(mock_hass, mock_config_entry, mock_client):
    """Test coordinator serves from cache when within window, and queries API when outside."""
    item_in_window = ZenbiCalendarItem(
        id="item-1",
        title="Chemistry",
        start="2026-09-22T08:00:00+02:00",
        end="2026-09-22T09:00:00+02:00",
    )
    coordinator = ZenbiCalendarDataUpdateCoordinator(mock_hass, mock_client, mock_config_entry)
    coordinator.data = ZenbiCalendarData(
        calendar_items=[item_in_window],
        window_start=datetime(2026, 9, 21, 0, 0, tzinfo=timezone.utc),
        window_end=datetime(2026, 10, 4, 23, 59, tzinfo=timezone.utc),
    )

    # 1. Query within window
    query_start = datetime(2026, 9, 22, 0, 0, tzinfo=timezone.utc)
    query_end = datetime(2026, 9, 22, 23, 59, tzinfo=timezone.utc)
    res = await coordinator.async_get_calendar_items(query_start, query_end)
    assert len(res) == 1
    assert res[0].title == "Chemistry"
    # Should NOT have called mock_client
    mock_client.get_calendar_items.assert_not_called()

    # 2. Query outside window (e.g. November)
    out_start = datetime(2026, 11, 1, 0, 0, tzinfo=timezone.utc)
    out_end = datetime(2026, 11, 5, 0, 0, tzinfo=timezone.utc)
    mock_client.get_calendar_items.return_value = [
        ZenbiCalendarItem(
            id="item-out",
            title="Future Class",
            start="2026-11-02T08:00:00+02:00",
            end="2026-11-02T09:00:00+02:00",
        )
    ]
    res_out = await coordinator.async_get_calendar_items(out_start, out_end)
    assert len(res_out) == 1
    assert res_out[0].title == "Future Class"
    mock_client.get_calendar_items.assert_called_once_with(out_start, out_end)


@pytest.mark.asyncio
async def test_calendar_schedule_entity(mock_hass, mock_config_entry, mock_client):
    """Test ZenbiScheduleCalendarEntity properties and event conversion."""
    coordinator = ZenbiCalendarDataUpdateCoordinator(mock_hass, mock_client, mock_config_entry)

    # Prepare upcoming event
    now = datetime.now(timezone.utc)
    future_start = (now + timedelta(hours=2)).isoformat()
    future_end = (now + timedelta(hours=3)).isoformat()

    item = ZenbiCalendarItem(
        id="cal-123",
        title="Danish Literature",
        start=future_start,
        end=future_end,
        description="Reading essay",
        note="Bring text book",
        resources=[{"name": "Room 402"}],
        substitutes=[{"name": "Mrs. Jensen"}],
        homework=[
            ZenbiHomework(
                id="hw-1",
                calendar_item_id="cal-123",
                description="Read essay pages 10-15",
                raw_description="...",
                date=future_start,
                files=[{"name": "essay_notes.pdf"}],
            )
        ],
        planning=ZenbiPlanningMeta(color="#0000ff", icon="book"),
    )
    coordinator.data = ZenbiCalendarData(
        calendar_items=[item],
        window_start=now - timedelta(days=1),
        window_end=now + timedelta(days=7),
    )

    entity = ZenbiScheduleCalendarEntity(coordinator, mock_config_entry)
    assert entity.unique_id == "entry_123_schedule"
    assert entity.device_info.manufacturer == "Zenbi"

    # Current/next event property
    next_event = entity.event
    assert next_event is not None
    assert next_event.summary == "Danish Literature"
    assert next_event.location == "Room 402"
    assert "Reading essay" in next_event.description
    assert "Note: Bring text book" in next_event.description
    assert "Substitutes: Mrs. Jensen" in next_event.description
    assert "Resources: Room 402" in next_event.description
    assert "Homework:" in next_event.description
    assert "Read essay pages 10-15" in next_event.description
    assert "essay_notes.pdf" in next_event.description

    # extra_state_attributes
    attrs = entity.extra_state_attributes
    assert "homework" in attrs
    assert len(attrs["homework"]) == 1
    assert attrs["homework"][0]["description"] == "Read essay pages 10-15"

    # async_get_events within window
    events = await entity.async_get_events(
        mock_hass,
        now,
        now + timedelta(days=5),
    )
    assert len(events) == 1
    assert events[0].uid == "cal-123"


@pytest.mark.asyncio
async def test_calendar_planning_entity(mock_hass, mock_config_entry, mock_client):
    """Test ZenbiPlanningCalendarEntity properties and all-day event conversion."""
    coordinator = ZenbiCalendarDataUpdateCoordinator(mock_hass, mock_client, mock_config_entry)

    today = date.today()
    label = ZenbiPlanningLabel(
        id="plan-456",
        title="Sports Day",
        start_date=today.isoformat(),
        end_date=today.isoformat(),
        description="Annual sports tournament",
        color="#ffa500",
    )
    coordinator.data = ZenbiCalendarData(planning_labels=[label])

    entity = ZenbiPlanningCalendarEntity(coordinator, mock_config_entry)
    assert entity.unique_id == "entry_123_planning"

    # Next event
    next_event = entity.event
    assert next_event is not None
    assert next_event.summary == "Sports Day"
    assert next_event.start == today
    # End date in HA calendar should be exclusive (today + 1 day)
    assert next_event.end == today + timedelta(days=1)
    assert next_event.description == "Annual sports tournament"


@pytest.mark.asyncio
async def test_calendar_weekly_messages_entity(mock_hass, mock_config_entry, mock_client):
    """Test ZenbiWeeklyMessagesCalendarEntity properties and 7-day all-day event conversion."""
    coordinator = ZenbiCalendarDataUpdateCoordinator(mock_hass, mock_client, mock_config_entry)

    today = date.today()
    schedule = ZenbiWeeklySchedule(
        id="ws-999",
        start=today.isoformat(),
        end=(today + timedelta(days=7)).isoformat(),
        description="**Kære forældre**\n\nBesked her.",
        raw_description="...",
        title="Kære forældre",
        files=[{"name": "oversigt.pdf"}],
    )
    coordinator.data = ZenbiCalendarData(
        weekly_schedules=[schedule],
        window_start=datetime.combine(today - timedelta(days=1), datetime.min.time(), tzinfo=timezone.utc),
        window_end=datetime.combine(today + timedelta(days=14), datetime.min.time(), tzinfo=timezone.utc),
    )

    entity = ZenbiWeeklyMessagesCalendarEntity(coordinator, mock_config_entry)
    assert entity.unique_id == "entry_123_weekly_messages"

    # Next event
    next_event = entity.event
    assert next_event is not None
    assert next_event.summary == "Kære forældre"
    assert next_event.start == today
    # End date in HA calendar should be 7 days later
    assert next_event.end == today + timedelta(days=7)
    assert "**Kære forældre**" in next_event.description
    assert "oversigt.pdf" in next_event.description

    # async_get_events
    events = await entity.async_get_events(
        mock_hass,
        datetime.combine(today, datetime.min.time(), tzinfo=timezone.utc),
        datetime.combine(today + timedelta(days=10), datetime.min.time(), tzinfo=timezone.utc),
    )
    assert len(events) == 1
    assert events[0].uid == "ws-999"


@pytest.mark.asyncio
async def test_config_flow_success(mock_hass):
    """Test successful config flow."""
    flow = ZenbiConfigFlow()
    flow.hass = mock_hass

    # 1. Initial step returns form
    result = await flow.async_step_user()
    assert result["type"] == "form"
    assert result["step_id"] == "user"

    # 2. Submit form with mock authentication
    with patch("custom_components.zenbi.config_flow.ZenbiApiClient") as mock_client_cls:
        instance = mock_client_cls.return_value
        instance.authenticate = AsyncMock()

        result2 = await flow.async_step_user(
            {
                CONF_USERNAME: "user@test.dk",
                CONF_PASSWORD: "correctpassword",
            }
        )

        assert result2["type"] == "create_entry"
        assert result2["title"] == "user@test.dk"
        assert result2["data"][CONF_USERNAME] == "user@test.dk"
        assert CONF_UNIQUE_DEVICE_ID in result2["data"]
        assert (
            result2["options"][CONF_CALENDAR_SYNC_INTERVAL_HOURS]
            == DEFAULT_CALENDAR_SYNC_INTERVAL_HOURS
        )


@pytest.mark.asyncio
async def test_config_flow_invalid_auth(mock_hass):
    """Test config flow with wrong credentials."""
    flow = ZenbiConfigFlow()
    flow.hass = mock_hass

    with patch("custom_components.zenbi.config_flow.ZenbiApiClient") as mock_client_cls:
        instance = mock_client_cls.return_value
        instance.authenticate = AsyncMock(
            side_effect=ZenbiAuthError("Invalid password")
        )

        result = await flow.async_step_user(
            {
                CONF_USERNAME: "user@test.dk",
                CONF_PASSWORD: "wrongpassword",
            }
        )

        assert result["type"] == "form"
        assert result["errors"]["base"] == "invalid_auth"


@pytest.mark.asyncio
async def test_options_flow(mock_config_entry):
    """Test options flow allows updating intervals."""
    flow = ZenbiOptionsFlowHandler(mock_config_entry)

    # Initial view
    res = await flow.async_step_init()
    assert res["type"] == "form"

    # Save options
    res2 = await flow.async_step_init(
        {
            CONF_CALENDAR_SYNC_INTERVAL_HOURS: 12,
            CONF_NOTIFICATION_SYNC_INTERVAL_MINS: 30,
        }
    )
    assert res2["type"] == "create_entry"
    assert res2["data"][CONF_CALENDAR_SYNC_INTERVAL_HOURS] == 12
    assert res2["data"][CONF_NOTIFICATION_SYNC_INTERVAL_MINS] == 30


@pytest.mark.asyncio
async def test_entry_setup_and_unload(mock_hass, mock_config_entry):
    """Test async_setup_entry and async_unload_entry lifecycle."""
    with patch("custom_components.zenbi.ZenbiApiClient") as mock_client_cls, patch(
        "custom_components.zenbi.ZenbiCalendarDataUpdateCoordinator.async_config_entry_first_refresh"
    ) as mock_refresh:
        mock_refresh.return_value = None
        mock_hass.config_entries.async_forward_entry_setups = AsyncMock(
            return_value=True
        )
        mock_hass.config_entries.async_unload_platforms = AsyncMock(
            return_value=True
        )

        setup_ok = await async_setup_entry(mock_hass, mock_config_entry)
        assert setup_ok is True
        assert DOMAIN in mock_hass.data
        assert mock_config_entry.entry_id in mock_hass.data[DOMAIN]

        unload_ok = await async_unload_entry(mock_hass, mock_config_entry)
        assert unload_ok is True
        assert mock_config_entry.entry_id not in mock_hass.data[DOMAIN]


@pytest.mark.asyncio
async def test_entity_registry_enabled_default(mock_hass, mock_config_entry, mock_client):
    """Test calendar entities enabled_default reflects data presence."""
    coordinator = ZenbiCalendarDataUpdateCoordinator(mock_hass, mock_client, mock_config_entry)

    # 1. When all data lists are empty
    coordinator.data = ZenbiCalendarData(
        calendar_items=[],
        planning_labels=[],
        weekly_schedules=[],
    )

    sched_ent = ZenbiScheduleCalendarEntity(coordinator, mock_config_entry)
    plan_ent = ZenbiPlanningCalendarEntity(coordinator, mock_config_entry)
    wm_ent = ZenbiWeeklyMessagesCalendarEntity(coordinator, mock_config_entry)

    assert sched_ent.translation_key == "schedule"
    assert plan_ent.translation_key == "planning"
    assert wm_ent.translation_key == "weekly_messages"

    assert sched_ent.entity_registry_enabled_default is False
    assert plan_ent.entity_registry_enabled_default is False
    assert wm_ent.entity_registry_enabled_default is False

    # 2. When data is populated
    coordinator.data = ZenbiCalendarData(
        calendar_items=[ZenbiCalendarItem(id="c1", title="Math", start="", end="")],
        planning_labels=[],  # School has no planning labels
        weekly_schedules=[ZenbiWeeklySchedule(id="w1", start="", end="", description="", raw_description="", title="Plan")],
    )

    assert sched_ent.entity_registry_enabled_default is True
    assert plan_ent.entity_registry_enabled_default is False
    assert wm_ent.entity_registry_enabled_default is True


@pytest.mark.asyncio
async def test_config_flow_reauth(mock_hass, mock_config_entry):
    """Test reauth flow handling password update."""
    flow = ZenbiConfigFlow()
    flow.hass = mock_hass
    flow.context = {"entry_id": mock_config_entry.entry_id, "title": mock_config_entry.title}
    flow._reauth_entry = mock_config_entry

    # 1. Step reauth triggers reauth_confirm form
    res = await flow.async_step_reauth(mock_config_entry.data)
    assert res["type"] == "form"
    assert res["step_id"] == "reauth_confirm"

    # 2. Submit wrong password -> error
    with patch("custom_components.zenbi.config_flow.ZenbiApiClient") as mock_client_cls:
        instance = mock_client_cls.return_value
        instance.authenticate = AsyncMock(side_effect=ZenbiAuthError("Invalid credentials"))

        res_err = await flow.async_step_reauth_confirm({CONF_PASSWORD: "wrong"})
        assert res_err["type"] == "form"
        assert res_err["errors"]["base"] == "invalid_auth"

    # 3. Submit valid password -> reauth_successful abort
    with patch("custom_components.zenbi.config_flow.ZenbiApiClient") as mock_client_cls:
        instance = mock_client_cls.return_value
        instance.authenticate = AsyncMock()

        res_ok = await flow.async_step_reauth_confirm({CONF_PASSWORD: "new_valid_password"})
        assert res_ok["type"] == "abort"
        assert res_ok["reason"] == "reauth_successful"
        mock_hass.config_entries.async_update_entry.assert_called_once()
        mock_hass.config_entries.async_reload.assert_called_once_with(mock_config_entry.entry_id)


@pytest.mark.asyncio
async def test_diagnostics(mock_hass, mock_config_entry, mock_client):
    """Test diagnostics output and credential redaction."""
    coordinator = ZenbiCalendarDataUpdateCoordinator(mock_hass, mock_client, mock_config_entry)
    now = datetime(2026, 9, 21, 12, 0, tzinfo=timezone.utc)
    coordinator.data = ZenbiCalendarData(
        calendar_items=[ZenbiCalendarItem(id="c1", title="Math", start="", end="")],
        planning_labels=[],
        homeworks=[ZenbiHomework(id="h1", calendar_item_id="c1", description="HW", raw_description="", date="")],
        weekly_schedules=[],
        last_synced=now,
        window_start=now,
        window_end=now + timedelta(days=14),
    )
    mock_hass.data[DOMAIN] = {mock_config_entry.entry_id: coordinator}

    diag = await async_get_config_entry_diagnostics(mock_hass, mock_config_entry)
    assert diag["entry"]["title"] == "student@school.dk"
    assert diag["entry"]["data"][CONF_PASSWORD] == "**REDACTED**"
    assert diag["entry"]["data"][CONF_UNIQUE_DEVICE_ID] == "**REDACTED**"
    assert diag["coordinator"]["calendar_items_count"] == 1
    assert diag["coordinator"]["homeworks_count"] == 1
    assert diag["coordinator"]["planning_labels_count"] == 0
    assert diag["coordinator"]["last_synced"] == now.isoformat()


@pytest.mark.asyncio
async def test_coordinator_concurrent_partial_failure(mock_hass, mock_config_entry, mock_client):
    """Test coordinator handles partial endpoint failure gracefully via asyncio.gather."""
    item = ZenbiCalendarItem(id="cal-1", title="Biology", start="2026-09-21T08:00:00+02:00", end="2026-09-21T09:00:00+02:00")
    mock_client.get_calendar_items.return_value = [item]
    mock_client.get_homework.return_value = []
    # Weekly schedules and planning endpoints throw errors
    mock_client.get_weekly_schedules.side_effect = ZenbiApiError("Weekly schedule 500 error")
    mock_client.get_planning_labels.side_effect = ZenbiApiError("Planning 404 error")

    coordinator = ZenbiCalendarDataUpdateCoordinator(mock_hass, mock_client, mock_config_entry)
    data = await coordinator._async_update_data()

    # Core items still parsed and returned despite non-critical failures
    assert len(data.calendar_items) == 1
    assert data.calendar_items[0].title == "Biology"
    assert data.weekly_schedules == []
    assert data.planning_labels == []


@pytest.mark.asyncio
async def test_coordinator_on_demand_weekly_schedules(mock_hass, mock_config_entry, mock_client):
    """Test coordinator on-demand query for weekly schedules outside cached window."""
    coordinator = ZenbiCalendarDataUpdateCoordinator(mock_hass, mock_client, mock_config_entry)
    coordinator.data = ZenbiCalendarData(
        weekly_schedules=[],
        window_start=datetime(2026, 9, 21, 0, 0, tzinfo=timezone.utc),
        window_end=datetime(2026, 10, 4, 0, 0, tzinfo=timezone.utc),
    )

    out_start = datetime(2026, 11, 1, 0, 0, tzinfo=timezone.utc)
    out_end = datetime(2026, 11, 8, 0, 0, tzinfo=timezone.utc)
    expected_ws = [ZenbiWeeklySchedule(id="ws-out", start="", end="", description="", raw_description="", title="Future WS")]
    mock_client.get_weekly_schedules.return_value = expected_ws

    result = await coordinator.async_get_weekly_schedules(out_start, out_end)
    assert len(result) == 1
    assert result[0].id == "ws-out"
    mock_client.get_weekly_schedules.assert_called_once_with(out_start, out_end)


@pytest.mark.asyncio
async def test_calendar_entity_malformed_items_handling(mock_hass, mock_config_entry, mock_client):
    """Test calendar entities gracefully return None when encountering unparseable items."""
    coordinator = ZenbiCalendarDataUpdateCoordinator(mock_hass, mock_client, mock_config_entry)

    sched_ent = ZenbiScheduleCalendarEntity(coordinator, mock_config_entry)
    plan_ent = ZenbiPlanningCalendarEntity(coordinator, mock_config_entry)
    wm_ent = ZenbiWeeklyMessagesCalendarEntity(coordinator, mock_config_entry)

    # Malformed schedule item (invalid start/end datetime)
    bad_item = ZenbiCalendarItem(id="bad-1", title="Broken", start="not-a-datetime", end="not-a-datetime")
    assert sched_ent._item_to_calendar_event(bad_item) is None

    # Malformed planning label (no valid dates)
    bad_label = ZenbiPlanningLabel(id="bad-2", title="Broken Label", start_date="")
    assert plan_ent._label_to_calendar_event(bad_label) is None

    # Malformed weekly schedule (no start date)
    bad_ws = ZenbiWeeklySchedule(id="bad-3", start="", end="", description="", raw_description="", title="Broken WS")
    assert wm_ent._schedule_to_calendar_event(bad_ws) is None


@pytest.mark.asyncio
async def test_config_flow_reauth_cannot_connect(mock_hass, mock_config_entry):
    """Test reauth flow handles connection error gracefully."""
    flow = ZenbiConfigFlow()
    flow.hass = mock_hass
    flow.context = {"entry_id": mock_config_entry.entry_id, "title": mock_config_entry.title}
    flow._reauth_entry = mock_config_entry

    with patch("custom_components.zenbi.config_flow.ZenbiApiClient") as mock_client_cls:
        instance = mock_client_cls.return_value
        instance.authenticate = AsyncMock(side_effect=ZenbiConnectionError("Server unreachable"))

        res = await flow.async_step_reauth_confirm({CONF_PASSWORD: "any_password"})
        assert res["type"] == "form"
        assert res["errors"]["base"] == "cannot_connect"



