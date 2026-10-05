"""Tests for Home Assistant integration components (coordinator, calendar, flows, init)."""

from __future__ import annotations

import asyncio
from datetime import date, datetime, time, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.zenbi import (
    async_remove_entry,
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
from custom_components.zenbi.todo import (
    ZenbiHomeworkTodoListEntity,
    async_setup_entry as async_setup_todo_entry,
)
from custom_components.zenbi.sensor import (
    ZenbiLastSyncedSensor,
    ZenbiNextWeeklyPlanSensor,
    ZenbiWeeklyPlanSensor,
    format_attachment_display_name,
    is_image_file,
    async_setup_entry as async_setup_sensor_entry,
)
from custom_components.zenbi.http import ZenbiFileDownloadView
from homeassistant.components.sensor import SensorDeviceClass, SensorStateClass
from homeassistant.components.todo import TodoItem, TodoItemStatus
from homeassistant.const import EntityCategory
from homeassistant.helpers.selector import NumberSelector, NumberSelectorMode
from homeassistant.util import dt as dt_util
from custom_components.zenbi.config_flow import ZenbiConfigFlow
from custom_components.zenbi.const import (
    CONF_CALENDAR_SYNC_INTERVAL_HOURS,
    CONF_PASSWORD,
    CONF_UNIQUE_DEVICE_ID,
    CONF_USERNAME,
    DEFAULT_CALENDAR_SYNC_INTERVAL_HOURS,
    DOMAIN,
    STORAGE_KEY_TODO,
)
from custom_components.zenbi.coordinator import (
    ZenbiCalendarData,
    ZenbiCalendarDataUpdateCoordinator,
)
from custom_components.zenbi.options_flow import ZenbiOptionsFlowHandler
from tests.conftest import (
    ConfigEntryAuthFailed,
    MockConfigEntry,
    MockHomeAssistant,
    MockStore,
)


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
            CONF_CALENDAR_SYNC_INTERVAL_HOURS: 6,
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
    """Test coordinator raises ConfigEntryAuthFailed (not UpdateFailed) on auth error so HA triggers the reauth UI."""
    mock_client.get_calendar_items.side_effect = ZenbiAuthError("Auth failed")

    coordinator = ZenbiCalendarDataUpdateCoordinator(mock_hass, mock_client, mock_config_entry)
    with pytest.raises(ConfigEntryAuthFailed):
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
    assert next_event.summary == "Danish Literature 📚"
    assert next_event.location == "Room 402"
    assert "Reading essay" in next_event.description
    assert "Note: Bring text book" in next_event.description
    assert "Substitutes: Mrs. Jensen" in next_event.description
    assert "Resources: Room 402" in next_event.description
    assert "Lektier:" in next_event.description
    assert "Afleveringsfrist:" in next_event.description
    assert "Read essay pages 10-15" in next_event.description
    assert "Vedhæftede filer:\n- essay_notes.pdf" in next_event.description
    assert "<a href=" not in next_event.description
    assert "###" not in next_event.description

    # extra_state_attributes — files are now flattened to name strings (not raw dicts)
    attrs = entity.extra_state_attributes
    assert "homework" in attrs
    assert len(attrs["homework"]) == 1
    assert attrs["homework"][0]["description"] == "Read essay pages 10-15"
    assert attrs["homework"][0]["files"] == ["essay_notes.pdf"]

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

    today = date(2026, 9, 21)  # A Monday
    schedule = ZenbiWeeklySchedule(
        id="ws-999",
        start=today.isoformat(),
        end=(today + timedelta(days=7)).isoformat(),
        description="**Kære forældre**\n\nBesked her.",
        raw_description="...",
        title="Kære forældre",
        files=[{"id": "file-ov-1", "name": "oversigt.pdf"}],
    )
    coordinator.data = ZenbiCalendarData(
        weekly_schedules=[schedule],
        window_start=datetime.combine(
            today - timedelta(days=1), datetime.min.time(), tzinfo=timezone.utc
        ),
        window_end=datetime.combine(
            today + timedelta(days=14), datetime.min.time(), tzinfo=timezone.utc
        ),
    )

    entity = ZenbiWeeklyMessagesCalendarEntity(coordinator, mock_config_entry)
    assert entity.unique_id == "entry_123_weekly_messages"

    # Next event
    next_event = entity.event
    assert next_event is not None
    assert next_event.summary == "Kære forældre"
    assert next_event.start == today
    # End date in HA calendar should span Monday to Friday (exclusive end date is Saturday = start + 5 days)
    assert next_event.end == today + timedelta(days=5)
    assert next_event.end.weekday() == 5  # Saturday (exclusive)
    assert "Vedhæftede filer:\n- oversigt.pdf" in next_event.description
    assert "<a href=" not in next_event.description

    # Extra state attributes with files
    attrs = entity.extra_state_attributes
    assert attrs["files"] == [
        {
            "name": "oversigt.pdf",
            "id": "file-ov-1",
            "url": "/api/zenbi/file/entry_123/file-ov-1",
            "is_image": False,
        }
    ]

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
        instance.authenticate = AsyncMock(side_effect=ZenbiAuthError("Invalid password"))

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
    """Test options flow allows updating calendar sync interval."""
    flow = ZenbiOptionsFlowHandler(mock_config_entry)

    # Initial view
    res = await flow.async_step_init()
    assert res["type"] == "form"
    schema = res["schema"].schema
    assert len(schema) == 1
    cal_key = next(
        k for k in schema if getattr(k, "schema", None) == CONF_CALENDAR_SYNC_INTERVAL_HOURS
    )
    cal_sel = schema[cal_key]
    assert isinstance(cal_sel, NumberSelector)
    assert cal_sel.config.mode == NumberSelectorMode.BOX
    assert cal_sel.config.min == 1
    assert cal_sel.config.max == 168

    # Save options
    res2 = await flow.async_step_init(
        {
            CONF_CALENDAR_SYNC_INTERVAL_HOURS: 12,
        }
    )
    assert res2["type"] == "create_entry"
    assert res2["data"][CONF_CALENDAR_SYNC_INTERVAL_HOURS] == 12


@pytest.mark.asyncio
async def test_entry_setup_and_unload(mock_hass, mock_config_entry):
    """Test async_setup_entry and async_unload_entry lifecycle."""
    with (
        patch("custom_components.zenbi.ZenbiApiClient") as mock_client_cls,
        patch(
            "custom_components.zenbi.ZenbiCalendarDataUpdateCoordinator.async_config_entry_first_refresh"
        ) as mock_refresh,
    ):
        mock_refresh.return_value = None
        mock_hass.config_entries.async_forward_entry_setups = AsyncMock(return_value=True)
        mock_hass.config_entries.async_unload_platforms = AsyncMock(return_value=True)

        setup_ok = await async_setup_entry(mock_hass, mock_config_entry)
        assert setup_ok is True
        assert DOMAIN in mock_hass.data
        assert mock_config_entry.entry_id in mock_hass.data[DOMAIN]
        coordinator = mock_hass.data[DOMAIN][mock_config_entry.entry_id]

        with patch.object(coordinator, "async_shutdown", AsyncMock()) as mock_shutdown:
            unload_ok = await async_unload_entry(mock_hass, mock_config_entry)
            assert unload_ok is True
            assert mock_config_entry.entry_id not in mock_hass.data[DOMAIN]
            assert mock_config_entry.runtime_data is None
            mock_shutdown.assert_called_once()
            # Crucial: Shared aiohttp session must NOT be closed during unload
            mock_client_cls.return_value.close.assert_not_called()


@pytest.mark.asyncio
async def test_entity_registry_enabled_default(mock_hass, mock_config_entry, mock_client):
    """Test all calendar entities are enabled by default (hardcoded True)."""
    coordinator = ZenbiCalendarDataUpdateCoordinator(mock_hass, mock_client, mock_config_entry)

    # With empty data
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

    # All entities are always enabled by default — user can disable unused ones manually.
    assert sched_ent.entity_registry_enabled_default is True
    assert plan_ent.entity_registry_enabled_default is True
    assert wm_ent.entity_registry_enabled_default is True

    # Still True with populated data
    coordinator.data = ZenbiCalendarData(
        calendar_items=[ZenbiCalendarItem(id="c1", title="Math", start="", end="")],
        planning_labels=[],
        weekly_schedules=[
            ZenbiWeeklySchedule(
                id="w1",
                start="",
                end="",
                description="",
                raw_description="",
                title="Plan",
            )
        ],
    )

    assert sched_ent.entity_registry_enabled_default is True
    assert plan_ent.entity_registry_enabled_default is True
    assert wm_ent.entity_registry_enabled_default is True


@pytest.mark.asyncio
async def test_config_flow_reauth(mock_hass, mock_config_entry):
    """Test reauth flow handling password update."""
    flow = ZenbiConfigFlow()
    flow.hass = mock_hass
    flow.context = {
        "entry_id": mock_config_entry.entry_id,
        "title": mock_config_entry.title,
    }
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
        homeworks=[
            ZenbiHomework(
                id="h1",
                calendar_item_id="c1",
                description="HW",
                raw_description="",
                date="",
            )
        ],
        weekly_schedules=[],
        last_synced=now,
        window_start=now,
        window_end=now + timedelta(days=14),
    )
    mock_hass.data[DOMAIN] = {mock_config_entry.entry_id: coordinator}

    diag = await async_get_config_entry_diagnostics(mock_hass, mock_config_entry)
    assert diag["entry"]["title"] == "student@school.dk"
    assert diag["entry"]["data"][CONF_USERNAME] == "**REDACTED**"
    assert diag["entry"]["data"][CONF_PASSWORD] == "**REDACTED**"
    assert diag["entry"]["data"][CONF_UNIQUE_DEVICE_ID] == "**REDACTED**"
    assert diag["coordinator"]["calendar_items_count"] == 1
    assert diag["coordinator"]["homeworks_count"] == 1
    assert diag["coordinator"]["planning_labels_count"] == 0
    assert diag["coordinator"]["last_synced"] == now.isoformat()


@pytest.mark.asyncio
async def test_coordinator_concurrent_partial_failure(mock_hass, mock_config_entry, mock_client):
    """Test coordinator handles partial endpoint failure gracefully via asyncio.gather."""
    item = ZenbiCalendarItem(
        id="cal-1",
        title="Biology",
        start="2026-09-21T08:00:00+02:00",
        end="2026-09-21T09:00:00+02:00",
    )
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
    expected_ws = [
        ZenbiWeeklySchedule(
            id="ws-out",
            start="",
            end="",
            description="",
            raw_description="",
            title="Future WS",
        )
    ]
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
    bad_item = ZenbiCalendarItem(
        id="bad-1", title="Broken", start="not-a-datetime", end="not-a-datetime"
    )
    assert sched_ent._item_to_calendar_event(bad_item) is None

    # Malformed planning label (no valid dates)
    bad_label = ZenbiPlanningLabel(id="bad-2", title="Broken Label", start_date="")
    assert plan_ent._label_to_calendar_event(bad_label) is None

    # Malformed weekly schedule (no start date)
    bad_ws = ZenbiWeeklySchedule(
        id="bad-3",
        start="",
        end="",
        description="",
        raw_description="",
        title="Broken WS",
    )
    assert wm_ent._schedule_to_calendar_event(bad_ws) is None


@pytest.mark.asyncio
async def test_config_flow_reauth_cannot_connect(mock_hass, mock_config_entry):
    """Test reauth flow handles connection error gracefully."""
    flow = ZenbiConfigFlow()
    flow.hass = mock_hass
    flow.context = {
        "entry_id": mock_config_entry.entry_id,
        "title": mock_config_entry.title,
    }
    flow._reauth_entry = mock_config_entry

    with patch("custom_components.zenbi.config_flow.ZenbiApiClient") as mock_client_cls:
        instance = mock_client_cls.return_value
        instance.authenticate = AsyncMock(side_effect=ZenbiConnectionError("Server unreachable"))

        res = await flow.async_step_reauth_confirm({CONF_PASSWORD: "any_password"})
        assert res["type"] == "form"
        assert res["errors"]["base"] == "cannot_connect"


@pytest.mark.asyncio
async def test_todo_platform_setup(mock_hass, mock_config_entry, mock_client):
    """Test todo platform async_setup_entry."""
    coordinator = ZenbiCalendarDataUpdateCoordinator(mock_hass, mock_client, mock_config_entry)
    mock_hass.data[DOMAIN] = {mock_config_entry.entry_id: coordinator}

    added_entities = []

    def async_add_entities(entities):
        added_entities.extend(entities)

    await async_setup_todo_entry(mock_hass, mock_config_entry, async_add_entities)
    assert len(added_entities) == 1
    assert isinstance(added_entities[0], ZenbiHomeworkTodoListEntity)
    assert added_entities[0].translation_key == "homework"


@pytest.mark.asyncio
async def test_todo_entity_properties_and_items(mock_hass, mock_config_entry, mock_client):
    """Test ZenbiHomeworkTodoListEntity item conversion, summary, due date and single-student naming."""
    coordinator = ZenbiCalendarDataUpdateCoordinator(mock_hass, mock_client, mock_config_entry)

    item = ZenbiCalendarItem(
        id="cal-math",
        title="Matematik",
        start="2026-09-22T08:00:00+02:00",
        end="2026-09-22T09:00:00+02:00",
    )
    hw = ZenbiHomework(
        id="hw-100",
        calendar_item_id="cal-math",
        description="Regn side 12\nEkstra opgave 3",
        raw_description="...",
        date="2026-09-22",
        files=[{"name": "opgaver.pdf"}],
    )
    coordinator.data = ZenbiCalendarData(
        calendar_items=[item],
        homeworks=[hw],
        students=["Albert Hansen"],
    )

    entity = ZenbiHomeworkTodoListEntity(coordinator, mock_config_entry)
    assert entity.unique_id == "entry_123_homework"
    assert entity.translation_key == "homework"
    assert entity.device_info.name == "Zenbi (Albert Hansen)"
    assert entity.device_info.manufacturer == "Zenbi"

    # Verify todo items
    items = await entity.async_get_todo_items()
    assert len(items) == 1
    todo_item = items[0]
    assert todo_item.uid == "hw-100"
    assert todo_item.summary == "Matematik: Regn side 12"
    assert todo_item.due == date(2026, 9, 22)
    assert todo_item.status == TodoItemStatus.NEEDS_ACTION
    assert "Regn side 12" in todo_item.description
    assert "Files: opgaver.pdf" in todo_item.description


@pytest.mark.asyncio
async def test_todo_entity_toggle_completion(mock_hass, mock_config_entry, mock_client):
    """Test toggling todo item completion status (NEEDS_ACTION <-> COMPLETED)."""
    coordinator = ZenbiCalendarDataUpdateCoordinator(mock_hass, mock_client, mock_config_entry)

    hw = ZenbiHomework(
        id="hw-200",
        calendar_item_id="cal-1",
        description="Dansk stil",
        raw_description="",
        date="2026-09-23",
    )
    coordinator.data = ZenbiCalendarData(homeworks=[hw])

    entity = ZenbiHomeworkTodoListEntity(coordinator, mock_config_entry)

    # Initial state
    items = await entity.async_get_todo_items()
    assert items[0].status == TodoItemStatus.NEEDS_ACTION

    # Mark completed
    await entity.async_update_todo_item(
        TodoItem(uid="hw-200", summary="", status=TodoItemStatus.COMPLETED)
    )
    items_after = await entity.async_get_todo_items()
    assert items_after[0].status == TodoItemStatus.COMPLETED

    # Unmark completed (back to NEEDS_ACTION)
    await entity.async_update_todo_item(
        TodoItem(uid="hw-200", summary="", status=TodoItemStatus.NEEDS_ACTION)
    )
    items_reset = await entity.async_get_todo_items()
    assert items_reset[0].status == TodoItemStatus.NEEDS_ACTION

    # Updating with empty UID is a safe no-op
    await entity.async_update_todo_item(TodoItem(uid="", summary=""))


@pytest.mark.asyncio
async def test_todo_entity_empty_and_fallback(mock_hass, mock_config_entry, mock_client):
    """Test empty homeworks and fallback behavior for summary and multi-student device name."""
    coordinator = ZenbiCalendarDataUpdateCoordinator(mock_hass, mock_client, mock_config_entry)

    # 1. Empty data
    coordinator.data = ZenbiCalendarData(homeworks=[])
    entity = ZenbiHomeworkTodoListEntity(coordinator, mock_config_entry)
    assert await entity.async_get_todo_items() == []

    # 2. Multi-student unassigned device name is Zenbi (School), 0 students is Zenbi (never username)
    coordinator.data = ZenbiCalendarData(students=["Albert", "Ida"])
    entity._handle_coordinator_update()
    assert entity.device_info.name == "Zenbi (School)"

    coordinator.data = ZenbiCalendarData(students=[])
    entity._handle_coordinator_update()
    assert entity.device_info.name == "Zenbi"

    # 3. Homework with no calendar item and no date (uses fallback date from calendar item if present or None)
    hw_no_cal = ZenbiHomework(
        id="hw-no-cal",
        calendar_item_id="missing-id",
        description="Only description here",
        raw_description="",
        date="",
    )
    coordinator.data = ZenbiCalendarData(homeworks=[hw_no_cal])
    items = await entity.async_get_todo_items()
    assert len(items) == 1
    assert items[0].summary == "Only description here"
    assert items[0].due is None

    # 4. Homework with empty description and no subject -> defaults to "Homework"
    hw_empty = ZenbiHomework(
        id="hw-empty",
        calendar_item_id="missing-id",
        description="",
        raw_description="",
        date="",
    )
    coordinator.data = ZenbiCalendarData(homeworks=[hw_empty])
    items2 = await entity.async_get_todo_items()
    assert items2[0].summary == "Homework"


@pytest.mark.asyncio
async def test_todo_entity_malformed_homework(mock_hass, mock_config_entry, mock_client):
    """Test that malformed homework items do not crash the entity."""
    coordinator = ZenbiCalendarDataUpdateCoordinator(mock_hass, mock_client, mock_config_entry)

    # An invalid item that triggers an exception during parsing
    broken_hw = MagicMock()
    broken_hw.id = "bad-hw"
    # Make description raise an exception when accessed
    type(broken_hw).description = property(
        lambda self: (_ for _ in ()).throw(ValueError("Corrupt"))
    )

    valid_hw = ZenbiHomework(
        id="valid-hw",
        calendar_item_id="",
        description="Normal homework",
        raw_description="",
        date="2026-09-24",
    )
    coordinator.data = ZenbiCalendarData(homeworks=[broken_hw, valid_hw])

    entity = ZenbiHomeworkTodoListEntity(coordinator, mock_config_entry)
    items = await entity.async_get_todo_items()
    assert len(items) == 1
    assert items[0].uid == "valid-hw"


@pytest.mark.asyncio
async def test_entry_remove_cleans_storage(mock_hass, mock_config_entry):
    """Test async_remove_entry deletes persistent storage file."""
    storage_key = STORAGE_KEY_TODO.format(entry_id=mock_config_entry.entry_id)
    MockStore._storage_data[storage_key] = {"completed_ids": ["hw-123"]}

    await async_remove_entry(mock_hass, mock_config_entry)
    assert storage_key not in MockStore._storage_data


@pytest.mark.asyncio
async def test_entry_remove_handles_storage_error(mock_hass, mock_config_entry):
    """Test async_remove_entry handles storage removal errors gracefully without crashing."""
    with patch("tests.conftest.MockStore.async_remove", side_effect=OSError("Disk error")):
        # Should not raise exception
        await async_remove_entry(mock_hass, mock_config_entry)


@pytest.mark.asyncio
async def test_todo_entity_store_persistence(mock_hass, mock_config_entry, mock_client):
    """Test ZenbiHomeworkTodoListEntity loads and saves completed IDs via Home Assistant Store helper."""
    coordinator = ZenbiCalendarDataUpdateCoordinator(mock_hass, mock_client, mock_config_entry)

    # Pre-seed persistent storage with one completed homework ID
    storage_key = STORAGE_KEY_TODO.format(entry_id=mock_config_entry.entry_id)
    MockStore._storage_data[storage_key] = {"completed_ids": ["hw-100"]}

    hw1 = ZenbiHomework(
        id="hw-100",
        calendar_item_id="",
        description="Math exercise 1",
        raw_description="",
        date="2026-09-22",
    )
    hw2 = ZenbiHomework(
        id="hw-200",
        calendar_item_id="",
        description="English essay",
        raw_description="",
        date="2026-09-23",
    )
    coordinator.data = ZenbiCalendarData(homeworks=[hw1, hw2])

    entity = ZenbiHomeworkTodoListEntity(coordinator, mock_config_entry)

    # Simulate HA adding entity to hass -> triggers async_added_to_hass
    await entity.async_added_to_hass()

    items = await entity.async_get_todo_items()
    assert len(items) == 2
    # hw-100 was loaded from storage as COMPLETED
    assert items[0].uid == "hw-100"
    assert items[0].status == TodoItemStatus.COMPLETED
    # hw-200 was not in storage -> NEEDS_ACTION
    assert items[1].uid == "hw-200"
    assert items[1].status == TodoItemStatus.NEEDS_ACTION

    # Update hw-200 to COMPLETED -> verifies async_save to Store
    await entity.async_update_todo_item(
        TodoItem(uid="hw-200", summary="", status=TodoItemStatus.COMPLETED)
    )
    assert set(MockStore._storage_data[storage_key]["completed_ids"]) == {
        "hw-100",
        "hw-200",
    }

    # Toggle hw-100 back to NEEDS_ACTION -> verifies removal in saved Store
    await entity.async_update_todo_item(
        TodoItem(uid="hw-100", summary="", status=TodoItemStatus.NEEDS_ACTION)
    )
    assert set(MockStore._storage_data[storage_key]["completed_ids"]) == {"hw-200"}


@pytest.mark.asyncio
async def test_todo_entity_store_load_error_handling(mock_hass, mock_config_entry, mock_client):
    """Test that store loading exceptions do not crash entity initialization."""
    coordinator = ZenbiCalendarDataUpdateCoordinator(mock_hass, mock_client, mock_config_entry)
    coordinator.data = ZenbiCalendarData(
        homeworks=[
            ZenbiHomework(
                id="h1",
                calendar_item_id="",
                description="",
                raw_description="",
                date="",
            )
        ]
    )

    entity = ZenbiHomeworkTodoListEntity(coordinator, mock_config_entry)
    with patch("tests.conftest.MockStore.async_load", side_effect=ValueError("Corrupt JSON")):
        await entity.async_added_to_hass()

    # Defaults cleanly to empty set, item is NEEDS_ACTION
    items = await entity.async_get_todo_items()
    assert len(items) == 1
    assert items[0].status == TodoItemStatus.NEEDS_ACTION


@pytest.mark.asyncio
async def test_coordinator_auth_error_secondary_endpoints(
    mock_hass, mock_config_entry, mock_client
):
    """Test that ZenbiAuthError from secondary endpoints also raises ConfigEntryAuthFailed.

    Prevents silent data loss where expired credentials would return empty todo lists
    instead of triggering the HA reauth UI.
    """
    # Calendar succeeds; homework returns auth error
    item = ZenbiCalendarItem(
        id="cal-1",
        title="Biology",
        start="2026-09-21T08:00:00+02:00",
        end="2026-09-21T09:00:00+02:00",
    )
    mock_client.get_calendar_items.return_value = [item]
    mock_client.get_homework.side_effect = ZenbiAuthError("Token expired")
    mock_client.get_weekly_schedules.return_value = []
    mock_client.get_planning_labels.return_value = []

    coordinator = ZenbiCalendarDataUpdateCoordinator(mock_hass, mock_client, mock_config_entry)
    with pytest.raises(ConfigEntryAuthFailed):
        await coordinator._async_update_data()


@pytest.mark.asyncio
async def test_unload_order_shutdown_before_platforms(mock_hass, mock_config_entry):
    """Test that coordinator.async_shutdown fires before async_unload_platforms.

    Ensures an in-flight coordinator refresh cannot race entity removal during unload.
    """
    call_order = []

    with (
        patch("custom_components.zenbi.ZenbiApiClient"),
        patch(
            "custom_components.zenbi.ZenbiCalendarDataUpdateCoordinator.async_config_entry_first_refresh"
        ),
    ):
        mock_hass.config_entries.async_forward_entry_setups = AsyncMock(return_value=True)
        await async_setup_entry(mock_hass, mock_config_entry)

    coordinator = mock_hass.data["zenbi"][mock_config_entry.entry_id]

    async def mock_shutdown():
        call_order.append("shutdown")

    async def mock_unload_platforms(entry, platforms):
        call_order.append("unload_platforms")
        return True

    with patch.object(coordinator, "async_shutdown", side_effect=mock_shutdown):
        mock_hass.config_entries.async_unload_platforms = AsyncMock(
            side_effect=mock_unload_platforms
        )
        await async_unload_entry(mock_hass, mock_config_entry)

    assert call_order == ["shutdown", "unload_platforms"], (
        f"Expected coordinator shutdown before platform unload, got: {call_order}"
    )


@pytest.mark.asyncio
async def test_extra_state_attributes_substitutes_are_strings(
    mock_hass, mock_config_entry, mock_client
):
    """Test that substitutes in extra_state_attributes are name strings, not raw API dicts."""
    coordinator = ZenbiCalendarDataUpdateCoordinator(mock_hass, mock_client, mock_config_entry)

    now = datetime.now(timezone.utc)
    future_start = (now + timedelta(hours=1)).isoformat()
    future_end = (now + timedelta(hours=2)).isoformat()

    item = ZenbiCalendarItem(
        id="sub-test",
        title="PE",
        start=future_start,
        end=future_end,
        substitutes=[{"name": "Mr. Larsen"}, {"name": "Ms. Nielsen"}],
    )
    coordinator.data = ZenbiCalendarData(
        calendar_items=[item],
        window_start=now - timedelta(days=1),
        window_end=now + timedelta(days=7),
    )

    entity = ZenbiScheduleCalendarEntity(coordinator, mock_config_entry)
    attrs = entity.extra_state_attributes

    assert "substitutes" in attrs
    # Must be a list of strings, not dicts — prevents recorder bloat
    assert attrs["substitutes"] == ["Mr. Larsen", "Ms. Nielsen"]
    for sub in attrs["substitutes"]:
        assert isinstance(sub, str), f"Expected string, got {type(sub)}: {sub}"


@pytest.mark.asyncio
async def test_todo_cal_by_id_cache(mock_hass, mock_config_entry, mock_client):
    """Test that _cal_by_id lookup is built from existing coordinator data in __init__
    and refreshed by _handle_coordinator_update when data changes."""
    coordinator = ZenbiCalendarDataUpdateCoordinator(mock_hass, mock_client, mock_config_entry)

    item = ZenbiCalendarItem(id="cal-abc", title="History", start="", end="")
    hw = ZenbiHomework(
        id="hw-abc",
        calendar_item_id="cal-abc",
        description="Read ch 5",
        raw_description="",
        date="",
    )
    coordinator.data = ZenbiCalendarData(calendar_items=[item], homeworks=[hw])

    entity = ZenbiHomeworkTodoListEntity(coordinator, mock_config_entry)

    # __init__ pre-populates the cache from whatever coordinator.data holds
    assert "cal-abc" in entity._cal_by_id
    assert entity._cal_by_id["cal-abc"].title == "History"

    # Simulate data change: new item replaces old one; homework also points to new item
    new_item = ZenbiCalendarItem(id="cal-xyz", title="Physics", start="", end="")
    new_hw = ZenbiHomework(
        id="hw-abc",
        calendar_item_id="cal-xyz",
        description="Read ch 5",
        raw_description="",
        date="",
    )
    coordinator.data = ZenbiCalendarData(calendar_items=[new_item], homeworks=[new_hw])

    # Before _handle_coordinator_update the cache still has the old data
    assert "cal-abc" in entity._cal_by_id

    # Trigger coordinator update — cache should refresh
    entity._handle_coordinator_update()
    assert "cal-abc" not in entity._cal_by_id
    assert "cal-xyz" in entity._cal_by_id
    assert entity._cal_by_id["cal-xyz"].title == "Physics"

    # todo_items should use the refreshed dict
    items = await entity.async_get_todo_items()
    assert len(items) == 1
    assert items[0].summary == "Physics: Read ch 5"


@pytest.mark.asyncio
async def test_multi_student_platform_setup(mock_hass, mock_config_entry, mock_client):
    """Test that multiple students generate separate student devices + a shared school device."""
    coordinator = ZenbiCalendarDataUpdateCoordinator(mock_hass, mock_client, mock_config_entry)
    coordinator.data = ZenbiCalendarData(
        students=["Albert Hansen", "Ida Hansen"],
        calendar_items=[],
        homeworks=[],
        weekly_schedules=[],
        planning_labels=[],
    )
    mock_hass.data[DOMAIN] = {mock_config_entry.entry_id: coordinator}

    # 1. Calendar setup
    cal_entities = []
    await async_setup_calendar_entry(
        mock_hass, mock_config_entry, lambda ents: cal_entities.extend(ents)
    )
    # 2 student schedule calendars + 1 planning + 1 weekly_messages = 4 entities
    assert len(cal_entities) == 4
    sched_albert = next(
        e for e in cal_entities if e.unique_id == "entry_123_albert_hansen_schedule"
    )
    sched_ida = next(e for e in cal_entities if e.unique_id == "entry_123_ida_hansen_schedule")
    planning = next(e for e in cal_entities if e.unique_id == "entry_123_planning")
    weekly = next(e for e in cal_entities if e.unique_id == "entry_123_weekly_messages")

    assert sched_albert.device_info.name == "Zenbi (Albert Hansen)"
    assert (DOMAIN, "entry_123_albert_hansen") in sched_albert.device_info.identifiers

    assert sched_ida.device_info.name == "Zenbi (Ida Hansen)"
    assert (DOMAIN, "entry_123_ida_hansen") in sched_ida.device_info.identifiers

    assert planning.device_info.name == "Zenbi (School)"
    assert (DOMAIN, "entry_123_school") in planning.device_info.identifiers

    assert weekly.device_info.name == "Zenbi (School)"
    assert (DOMAIN, "entry_123_school") in weekly.device_info.identifiers

    # 2. Todo setup
    todo_entities = []
    await async_setup_todo_entry(
        mock_hass, mock_config_entry, lambda ents: todo_entities.extend(ents)
    )
    assert len(todo_entities) == 2
    todo_albert = next(
        e for e in todo_entities if e.unique_id == "entry_123_albert_hansen_homework"
    )
    todo_ida = next(e for e in todo_entities if e.unique_id == "entry_123_ida_hansen_homework")

    assert todo_albert.device_info.name == "Zenbi (Albert Hansen)"
    assert todo_ida.device_info.name == "Zenbi (Ida Hansen)"

    # 3. Sensor setup (weekly_plan and next_weekly_plan per student + last_synced)
    sensor_entities = []
    await async_setup_sensor_entry(
        mock_hass, mock_config_entry, lambda ents: sensor_entities.extend(ents)
    )
    assert len(sensor_entities) == 5
    sensor_albert = next(
        e for e in sensor_entities if e.unique_id == "entry_123_albert_hansen_weekly_plan"
    )
    sensor_albert_next = next(
        e for e in sensor_entities if e.unique_id == "entry_123_albert_hansen_next_weekly_plan"
    )
    sensor_ida = next(
        e for e in sensor_entities if e.unique_id == "entry_123_ida_hansen_weekly_plan"
    )
    sensor_ida_next = next(
        e for e in sensor_entities if e.unique_id == "entry_123_ida_hansen_next_weekly_plan"
    )
    last_synced = next(e for e in sensor_entities if e.unique_id == "entry_123_last_synced")

    assert sensor_albert.device_info.name == "Zenbi (Albert Hansen)"
    assert sensor_albert_next.device_info.name == "Zenbi (Albert Hansen)"
    assert sensor_ida.device_info.name == "Zenbi (Ida Hansen)"
    assert sensor_ida_next.device_info.name == "Zenbi (Ida Hansen)"
    assert last_synced.device_info.name == "Zenbi (School)"
    assert (DOMAIN, "entry_123_school") in last_synced.device_info.identifiers


@pytest.mark.asyncio
async def test_strict_student_filtering(mock_hass, mock_config_entry, mock_client):
    """Test that calendar and todo entities strictly isolate data to the assigned student."""
    coordinator = ZenbiCalendarDataUpdateCoordinator(mock_hass, mock_client, mock_config_entry)

    # Albert's class & homework
    item_albert = ZenbiCalendarItem(
        id="c-bio",
        title="Biologi",
        start="2026-09-22T08:00:00+02:00",
        end="2026-09-22T09:00:00+02:00",
        participant_models=[{"name": "Albert Hansen"}],
    )
    hw_albert = ZenbiHomework(
        id="hw-bio",
        calendar_item_id="c-bio",
        description="Read biology ch 2",
        raw_description="",
        date="2026-09-22",
    )

    # Ida's class & homework
    item_ida = ZenbiCalendarItem(
        id="c-chem",
        title="Kemi",
        start="2026-09-22T10:00:00+02:00",
        end="2026-09-22T11:00:00+02:00",
        participant_models=[{"name": "Ida Hansen"}],
    )
    hw_ida = ZenbiHomework(
        id="hw-chem",
        calendar_item_id="c-chem",
        description="Lab report",
        raw_description="",
        date="2026-09-22",
    )

    coordinator.data = ZenbiCalendarData(
        students=["Albert Hansen", "Ida Hansen"],
        calendar_items=[item_albert, item_ida],
        homeworks=[hw_albert, hw_ida],
        window_start=datetime.fromisoformat("2026-09-21T00:00:00+02:00"),
        window_end=datetime.fromisoformat("2026-10-05T00:00:00+02:00"),
    )

    # Test Albert's calendar
    cal_albert = ZenbiScheduleCalendarEntity(
        coordinator, mock_config_entry, student_name="Albert Hansen"
    )
    albert_events = await cal_albert.async_get_events(
        mock_hass,
        datetime.fromisoformat("2026-09-21T00:00:00+02:00"),
        datetime.fromisoformat("2026-09-25T00:00:00+02:00"),
    )
    assert len(albert_events) == 1
    assert albert_events[0].summary == "Biologi"

    # Test Ida's calendar
    cal_ida = ZenbiScheduleCalendarEntity(coordinator, mock_config_entry, student_name="Ida Hansen")
    ida_events = await cal_ida.async_get_events(
        mock_hass,
        datetime.fromisoformat("2026-09-21T00:00:00+02:00"),
        datetime.fromisoformat("2026-09-25T00:00:00+02:00"),
    )
    assert len(ida_events) == 1
    assert ida_events[0].summary == "Kemi"

    # Test Albert's todo list
    todo_albert = ZenbiHomeworkTodoListEntity(
        coordinator, mock_config_entry, student_name="Albert Hansen"
    )
    albert_todos = await todo_albert.async_get_todo_items()
    assert len(albert_todos) == 1
    assert albert_todos[0].summary == "Biologi: Read biology ch 2"

    # Test Ida's todo list
    todo_ida = ZenbiHomeworkTodoListEntity(
        coordinator, mock_config_entry, student_name="Ida Hansen"
    )
    ida_todos = await todo_ida.async_get_todo_items()
    assert len(ida_todos) == 1
    assert ida_todos[0].summary == "Kemi: Lab report"


@pytest.mark.asyncio
async def test_weekly_plan_sensor_attributes(mock_hass, mock_config_entry, mock_client):
    """Test ZenbiWeeklyPlanSensor exposes current and next week plan in markdown."""
    coordinator = ZenbiCalendarDataUpdateCoordinator(mock_hass, mock_client, mock_config_entry)

    sched_current = ZenbiWeeklySchedule(
        id="ws-cur",
        title="Uge 39 - Dansk tema",
        start="2026-09-21T00:00:00+02:00",
        end="2026-09-27T23:59:59+02:00",
        description="# Uge 39\nVi læser H.C. Andersen denne uge.\n\n- Mandag: Oplæsning\n- Fredag: Opgave",
        raw_description="",
        files=[{"name": "hc_andersen_tekst.pdf"}],
    )
    sched_next = ZenbiWeeklySchedule(
        id="ws-nxt",
        title="Uge 40 - Matematikuge",
        start="2026-09-28T00:00:00+02:00",
        end="2026-10-04T23:59:59+02:00",
        description="# Uge 40\nVi arbejder med brøker.",
        raw_description="",
    )

    coordinator.data = ZenbiCalendarData(
        students=["Albert Hansen"],
        weekly_schedules=[sched_current, sched_next],
    )

    fixed_now = datetime(2026, 9, 23, 12, 0, 0, tzinfo=timezone(timedelta(hours=2)))
    with patch("custom_components.zenbi.sensor.dt_util.now", return_value=fixed_now):
        sensor = ZenbiWeeklyPlanSensor(coordinator, mock_config_entry, student_name="Albert Hansen")
        assert sensor.unique_id == "entry_123_albert_hansen_weekly_plan"
        assert sensor.translation_key == "weekly_plan"
        assert sensor.device_info.name == "Zenbi (Albert Hansen)"

        # State is current week message count
        assert sensor.native_value == 1

        attrs = sensor.extra_state_attributes
        # Current week markdown content
        assert "content" in attrs
        assert "H.C. Andersen" in attrs["content"]
        assert attrs["title"] == "Uge 39 - Dansk tema"
        assert attrs["files"] == [{"name": "hc_andersen_tekst.pdf", "is_image": False}]

        # Next week sensor
        sensor_next = ZenbiNextWeeklyPlanSensor(
            coordinator, mock_config_entry, student_name="Albert Hansen"
        )
        assert sensor_next.unique_id == "entry_123_albert_hansen_next_weekly_plan"
        assert sensor_next.translation_key == "next_weekly_plan"
        assert sensor_next.device_info.name == "Zenbi (Albert Hansen)"
        assert sensor_next.native_value == 1

        attrs_next = sensor_next.extra_state_attributes
        assert "content" in attrs_next
        assert "brøker" in attrs_next["content"]
        assert attrs_next["title"] == "Uge 40 - Matematikuge"


@pytest.mark.asyncio
async def test_weekly_plan_sensor_smart_grouping_and_aggregation(
    mock_hass, mock_config_entry, mock_client
):
    """Test smart grouping of multiple messages in the same week, attachment aggregation, and fallback."""
    coordinator = ZenbiCalendarDataUpdateCoordinator(mock_hass, mock_client, mock_config_entry)

    today = dt_util.now().date()
    start_cur = (today - timedelta(days=today.weekday())).isoformat() + "T00:00:00+02:00"
    end_cur = (today + timedelta(days=6 - today.weekday())).isoformat() + "T23:59:59+02:00"

    # 1. Active week with a text-rich message and an attachment-only message
    sched_teacher = ZenbiWeeklySchedule(
        id="ws-1",
        title="Kære forældre i Pluto",
        start=start_cur,
        end=end_cur,
        description="Vi har haft en fantastisk emneuge.",
        raw_description="",
        files=[{"name": "Hold (1)"}],
    )
    sched_fritter = ZenbiWeeklySchedule(
        id="ws-2",
        title="Fritter-kalender-26-27-uge-37",
        start=start_cur,
        end=end_cur,
        description="",
        raw_description="",
        files=[{"name": "Fritter-kalender-26-27-uge-37.pdf"}],
    )

    coordinator.data = ZenbiCalendarData(
        students=["Albert Hansen"],
        weekly_schedules=[sched_teacher, sched_fritter],
    )

    sensor = ZenbiWeeklyPlanSensor(coordinator, mock_config_entry, student_name="Albert Hansen")

    # Native value shows message count of active schedules (2)
    assert sensor.native_value == 2

    attrs = sensor.extra_state_attributes
    # content must NOT be empty or overridden by fritter
    assert "Vi har haft en fantastisk emneuge." in attrs["content"]
    assert "### Vedhæftede filer" in attrs["content"]
    # files must aggregate files from both messages without duplicates
    assert [f["name"] for f in attrs["files"]] == [
        "Hold (1)",
        "Fritter-kalender-26-27-uge-37.pdf",
    ]

    # 2. Both schedules have text descriptions -> headers and separator
    sched_fritter.description = "Husk skiftetøj til fredag."
    attrs_multi = sensor.extra_state_attributes
    assert "# Kære forældre i Pluto" in attrs_multi["content"]
    assert "# Fritter-kalender-26-27-uge-37" in attrs_multi["content"]
    assert "\n\n---\n\n" in attrs_multi["content"]

    # 3. Neither schedule has text descriptions -> fallback list of files
    sched_teacher.description = ""
    sched_fritter.description = ""
    attrs_fallback = sensor.extra_state_attributes
    assert "*Vedhæftede filer til denne uge:*" in attrs_fallback["content"]
    assert "- Hold (1)" in attrs_fallback["content"]
    assert "- Fritter-kalender-26-27-uge-37.pdf" in attrs_fallback["content"]


@pytest.mark.asyncio
async def test_zenbi_file_download_view(mock_hass, mock_config_entry, mock_client):
    """Test ZenbiFileDownloadView handles proxy downloads and redirects properly."""
    coordinator = ZenbiCalendarDataUpdateCoordinator(mock_hass, mock_client, mock_config_entry)
    mock_hass.data[DOMAIN] = {mock_config_entry.entry_id: coordinator}

    view = ZenbiFileDownloadView()
    assert view.requires_auth is False
    assert view.url == "/api/zenbi/file/{entry_id}/{file_id}"

    mock_request = MagicMock()
    mock_request.app = {"hass": mock_hass}

    # 1. Successful stream with Content-Disposition: inline
    sas_url = "https://zenbistore.blob.core.windows.net/private-files/test.png?sas=token"
    mock_client.get_weekly_schedule_file_download_url = AsyncMock(return_value=sas_url)

    mock_upstream_resp = MagicMock()
    mock_upstream_resp.status = 200
    mock_upstream_resp.headers = {
        "Content-Type": "image/png",
    }
    mock_upstream_resp.read = AsyncMock(return_value=b"fake_png_data")

    mock_session = MagicMock()
    mock_session.get.return_value.__aenter__.return_value = mock_upstream_resp
    mock_client.session = mock_session

    response = await view.get(mock_request, mock_config_entry.entry_id, "file-123")
    assert response.status == 200
    assert response.headers["Content-Disposition"] == "inline"
    assert response.headers["Content-Type"] == "image/png"
    assert response.body == b"fake_png_data"
    mock_client.get_weekly_schedule_file_download_url.assert_called_with("file-123")

    # 2. Unknown entry_id -> 404
    resp_404 = await view.get(mock_request, "nonexistent_entry", "file-123")
    assert resp_404.status == 404

    # 3. Client error getting download URL -> 502
    mock_client.get_weekly_schedule_file_download_url = AsyncMock(
        side_effect=ZenbiApiError("Upstream timeout")
    )
    resp_502 = await view.get(mock_request, mock_config_entry.entry_id, "file-123")
    assert resp_502.status == 502

    # 4. Upstream storage non-200 -> mirrors status
    mock_client.get_weekly_schedule_file_download_url = AsyncMock(return_value=sas_url)
    mock_upstream_resp.status = 403
    resp_403 = await view.get(mock_request, mock_config_entry.entry_id, "file-123")
    assert resp_403.status == 403

    # 5. Stream network failure -> 502
    mock_session.get.side_effect = Exception("Connection reset")
    resp_stream_err = await view.get(mock_request, mock_config_entry.entry_id, "file-123")
    assert resp_stream_err.status == 502


@pytest.mark.asyncio
async def test_weekly_plan_sensor_file_download_links(mock_hass, mock_config_entry, mock_client):
    """Test ZenbiWeeklyPlanSensor embeds proxy URLs in markdown and files attributes."""
    coordinator = ZenbiCalendarDataUpdateCoordinator(mock_hass, mock_client, mock_config_entry)

    today = dt_util.now().date()
    start_cur = (today - timedelta(days=today.weekday())).isoformat() + "T00:00:00+02:00"
    end_cur = (today + timedelta(days=6 - today.weekday())).isoformat() + "T23:59:59+02:00"

    sched = ZenbiWeeklySchedule(
        id="ws-links",
        title="Uge 40",
        start=start_cur,
        end=end_cur,
        description="Velkommen til en ny uge.",
        raw_description="",
        files=[
            {"id": "file-uuid-img", "name": "Skema.png"},
            {"id": "file-uuid-hold", "name": "Hold (1)", "extension": "png"},
            {"fileId": "file-uuid-doc", "name": "Lektier", "extension": "pdf"},
        ],
    )
    coordinator.data = ZenbiCalendarData(
        students=["Albert Hansen"],
        weekly_schedules=[sched],
    )

    sensor = ZenbiWeeklyPlanSensor(coordinator, mock_config_entry, student_name="Albert Hansen")
    attrs = sensor.extra_state_attributes

    expected_url_img = f"/api/zenbi/file/{mock_config_entry.entry_id}/file-uuid-img"
    expected_url_hold = f"/api/zenbi/file/{mock_config_entry.entry_id}/file-uuid-hold"
    expected_url_doc = f"/api/zenbi/file/{mock_config_entry.entry_id}/file-uuid-doc"

    # Inline image rendering in content for both extension in name and in extension field
    assert f'<a href="{expected_url_img}" target="_blank" title="Skema.png">' in attrs["content"]
    assert f'<img src="{expected_url_img}" alt="Skema.png"' in attrs["content"]

    assert f'<a href="{expected_url_hold}" target="_blank" title="Hold (1)">' in attrs["content"]
    assert f'<img src="{expected_url_hold}" alt="Hold (1)"' in attrs["content"]

    # Non-image document download link under Vedhæftede filer
    assert "### Vedhæftede filer" in attrs["content"]
    assert (
        f'<a href="{expected_url_doc}" target="_blank" download>Lektier.pdf</a>' in attrs["content"]
    )

    # Structured dicts in files attribute with is_image tag and extension metadata
    assert len(attrs["files"]) == 3
    assert attrs["files"][0] == {
        "name": "Skema.png",
        "id": "file-uuid-img",
        "url": expected_url_img,
        "is_image": True,
    }
    assert attrs["files"][1] == {
        "name": "Hold (1)",
        "id": "file-uuid-hold",
        "url": expected_url_hold,
        "is_image": True,
        "extension": "png",
    }
    assert attrs["files"][2] == {
        "name": "Lektier.pdf",
        "id": "file-uuid-doc",
        "url": expected_url_doc,
        "is_image": False,
        "extension": "pdf",
    }

    # Test image-only schedule (no text description)
    sched_img_only = ZenbiWeeklySchedule(
        id="ws-img-only",
        title="Foto-brev.jpg",
        start=start_cur,
        end=end_cur,
        description="",
        raw_description="",
        files=[{"id": "file-img-2", "name": "Foto-brev.jpg"}],
    )
    coordinator.data = ZenbiCalendarData(
        students=["Albert Hansen"],
        weekly_schedules=[sched_img_only],
    )
    sensor_img_only = ZenbiWeeklyPlanSensor(
        coordinator, mock_config_entry, student_name="Albert Hansen"
    )
    attrs_img_only = sensor_img_only.extra_state_attributes
    url_img_2 = f"/api/zenbi/file/{mock_config_entry.entry_id}/file-img-2"
    assert f'<img src="{url_img_2}" alt="Foto-brev.jpg"' in attrs_img_only["content"]
    # Should not have Vedhæftede filer header since there are no non-image documents
    assert "### Vedhæftede filer" not in attrs_img_only["content"]


@pytest.mark.asyncio
async def test_multi_student_todo_persistence(mock_hass, mock_config_entry, mock_client):
    """Test that completion states are tracked independently per student in persistent storage."""
    coordinator = ZenbiCalendarDataUpdateCoordinator(mock_hass, mock_client, mock_config_entry)
    storage_key = STORAGE_KEY_TODO.format(entry_id=mock_config_entry.entry_id)

    # Pre-seed storage with Albert having completed hw-1 and Ida having completed hw-2
    MockStore._storage_data[storage_key] = {
        "completed_ids": {
            "Albert Hansen": ["hw-1"],
            "Ida Hansen": ["hw-2"],
        }
    }

    hw1 = ZenbiHomework(
        id="hw-1",
        calendar_item_id="",
        description="Albert hw 1",
        raw_description="",
        date="",
    )
    hw2 = ZenbiHomework(
        id="hw-2",
        calendar_item_id="",
        description="Ida hw 1",
        raw_description="",
        date="",
    )
    hw3 = ZenbiHomework(
        id="hw-3",
        calendar_item_id="",
        description="Albert hw 2",
        raw_description="",
        date="",
    )

    coordinator.data = ZenbiCalendarData(
        students=["Albert Hansen", "Ida Hansen"],
        homeworks=[hw1, hw2, hw3],
    )

    todo_albert = ZenbiHomeworkTodoListEntity(
        coordinator, mock_config_entry, student_name="Albert Hansen"
    )
    todo_ida = ZenbiHomeworkTodoListEntity(
        coordinator, mock_config_entry, student_name="Ida Hansen"
    )

    await todo_albert.async_added_to_hass()
    await todo_ida.async_added_to_hass()

    assert todo_albert._completed_ids == {"hw-1"}
    assert todo_ida._completed_ids == {"hw-2"}

    # Albert completes hw-3 -> updates Albert's list without erasing Ida's
    await todo_albert.async_update_todo_item(
        TodoItem(uid="hw-3", summary="", status=TodoItemStatus.COMPLETED)
    )

    saved_data = MockStore._storage_data[storage_key]["completed_ids"]
    assert isinstance(saved_data, dict)
    assert set(saved_data["Albert Hansen"]) == {"hw-1", "hw-3"}
    assert set(saved_data["Ida Hansen"]) == {"hw-2"}


@pytest.mark.asyncio
async def test_last_synced_sensor_success(mock_hass, mock_config_entry, mock_client):
    """Test ZenbiLastSyncedSensor on successful coordinator cycle."""
    coordinator = ZenbiCalendarDataUpdateCoordinator(mock_hass, mock_client, mock_config_entry)
    now = datetime(2026, 9, 21, 10, 0, 0, tzinfo=timezone.utc)
    coordinator.last_sync_success = now
    coordinator.last_sync_status = "success"
    coordinator.last_error = None
    coordinator.consecutive_failures = 0
    coordinator.data = ZenbiCalendarData(
        students=["Albert Hansen"],
        window_start=datetime(2026, 9, 21, 0, 0, 0, tzinfo=timezone.utc),
        window_end=datetime(2026, 10, 5, 0, 0, 0, tzinfo=timezone.utc),
    )

    sensor = ZenbiLastSyncedSensor(coordinator, mock_config_entry)

    assert sensor.unique_id == f"{mock_config_entry.entry_id}_last_synced"
    assert sensor.device_class == SensorDeviceClass.TIMESTAMP
    assert sensor.entity_category == EntityCategory.DIAGNOSTIC
    assert sensor.native_value == now

    attrs = sensor.extra_state_attributes
    assert attrs["last_status"] == "success"
    assert attrs["last_error"] is None
    assert attrs["consecutive_failures"] == 0
    assert "2026-09-21" in attrs["rolling_window_start"]
    assert "2026-10-05" in attrs["rolling_window_end"]

    # Device assignment: School device because students are present
    assert sensor.device_info.name == "Zenbi (School)"
    assert (
        DOMAIN,
        f"{mock_config_entry.entry_id}_school",
    ) in sensor.device_info.identifiers


@pytest.mark.asyncio
async def test_last_synced_sensor_failure_resilience(mock_hass, mock_config_entry, mock_client):
    """Test that last synced sensor retains last good timestamp when sync fails, but updates failure attributes."""
    coordinator = ZenbiCalendarDataUpdateCoordinator(mock_hass, mock_client, mock_config_entry)
    last_good_time = datetime(2026, 9, 21, 8, 0, 0, tzinfo=timezone.utc)
    coordinator.last_sync_success = last_good_time

    mock_client.get_calendar_items.side_effect = ZenbiConnectionError(
        "Connection timed out to Zenbi"
    )

    # Simulate failing update
    with pytest.raises(Exception):
        await coordinator._async_update_data()

    assert coordinator.last_sync_status == "failure"
    assert coordinator.consecutive_failures == 1
    assert "Connection timed out" in coordinator.last_error

    sensor = ZenbiLastSyncedSensor(coordinator, mock_config_entry)

    # State still retains the previous successful sync timestamp!
    assert sensor.native_value == last_good_time

    # Extra state attributes reflect the failure details
    attrs = sensor.extra_state_attributes
    assert attrs["last_status"] == "failure"
    assert "Connection timed out" in attrs["last_error"]
    assert attrs["consecutive_failures"] == 1


@pytest.mark.asyncio
async def test_coordinator_on_demand_calendar_ttl_cache(mock_hass, mock_config_entry, mock_client):
    """Test that out-of-window on-demand queries are cached in memory with a 2-hour TTL."""
    coordinator = ZenbiCalendarDataUpdateCoordinator(mock_hass, mock_client, mock_config_entry)
    coordinator.data = ZenbiCalendarData(
        window_start=datetime(2026, 9, 21, 0, 0, tzinfo=timezone.utc),
        window_end=datetime(2026, 10, 5, 0, 0, tzinfo=timezone.utc),
    )

    out_of_window_start = datetime(2026, 8, 1, 0, 0, tzinfo=timezone.utc)
    out_of_window_end = datetime(2026, 8, 31, 23, 59, tzinfo=timezone.utc)

    mock_client.get_calendar_items.return_value = [
        ZenbiCalendarItem(
            id="past-cal-1",
            title="Historie",
            start="2026-08-15T08:00:00+02:00",
            end="2026-08-15T09:00:00+02:00",
        )
    ]
    mock_client.get_homework.return_value = []

    # First query -> cache miss -> queries API
    items_first = await coordinator.async_get_calendar_items(out_of_window_start, out_of_window_end)
    assert len(items_first) == 1
    assert items_first[0].id == "past-cal-1"
    assert mock_client.get_calendar_items.call_count == 1

    # Second query for same range -> cache hit -> returns from in-memory cache with ZERO extra API calls
    items_second = await coordinator.async_get_calendar_items(
        out_of_window_start, out_of_window_end
    )
    assert len(items_second) == 1
    assert items_second[0].id == "past-cal-1"
    assert mock_client.get_calendar_items.call_count == 1  # call count unchanged!

    # Test cache shutdown cleanup
    await coordinator.async_shutdown()
    assert coordinator._on_demand_cache == {}


def test_is_image_file_detection():
    """Test is_image_file correctly identifies images using extension, contentType, or name."""
    # Extension in separate field (Zenbi API structure)
    assert is_image_file({"name": "Hold (1)", "extension": "png"}) is True
    assert is_image_file({"name": "Fritter-kalender-26-27-uge-37", "extension": "png"}) is True
    assert is_image_file({"name": "Foto", "extension": "JPG"}) is True
    assert is_image_file({"name": "Forældrebrev Blokdag", "extension": "pdf"}) is False
    assert is_image_file({"name": "Vejledning", "extension": "docx"}) is False

    # MIME type
    assert is_image_file({"name": "Ukendt", "contentType": "image/png"}) is True
    assert is_image_file({"name": "Ukendt", "mimeType": "image/jpeg"}) is True
    assert is_image_file({"name": "Ukendt", "contentType": "application/pdf"}) is False

    # Extension in name
    assert is_image_file({"name": "Skema.png"}) is True
    assert is_image_file({"name": "Billede.JPG"}) is True
    assert is_image_file({"name": "Oversigt.pdf"}) is False

    # Direct filename string
    assert is_image_file("billede.webp") is True
    assert is_image_file("dokument.pdf") is False
    assert is_image_file(None) is False


def test_format_attachment_display_name():
    """Test format_attachment_display_name formats non-images with extension and leaves images clean."""
    # Non-images without extension in name: should append extension
    assert (
        format_attachment_display_name(
            {"name": "Fritter-kalender-26-27-uge-37", "extension": "pdf"}
        )
        == "Fritter-kalender-26-27-uge-37.pdf"
    )
    assert (
        format_attachment_display_name({"name": "Vejledning", "extension": "docx"})
        == "Vejledning.docx"
    )
    assert (
        format_attachment_display_name({"name": "Regnskab", "extension": ".xlsx"})
        == "Regnskab.xlsx"
    )

    # Non-images that already have extension in name: do NOT double append
    assert (
        format_attachment_display_name({"name": "Fritter-kalender.pdf", "extension": "pdf"})
        == "Fritter-kalender.pdf"
    )
    assert (
        format_attachment_display_name({"name": "Fritter-kalender.PDF", "extension": "pdf"})
        == "Fritter-kalender.PDF"
    )

    # Images: should NOT append extension if missing, keep clean visual title
    assert format_attachment_display_name({"name": "Hold (1)", "extension": "png"}) == "Hold (1)"
    assert format_attachment_display_name({"name": "Foto", "extension": "jpg"}) == "Foto"
    # Images with extension already in name
    assert format_attachment_display_name({"name": "Skema.png", "extension": "png"}) == "Skema.png"

    # Fallbacks and edge cases
    assert format_attachment_display_name({"name": "Dokument"}) == "Dokument"
    assert format_attachment_display_name(None) == "Vedhæftet fil"
    assert format_attachment_display_name("fil.pdf") == "fil.pdf"


@pytest.mark.asyncio
async def test_schedule_calendar_agenda_today_and_tomorrow(
    mock_hass, mock_config_entry, mock_client
):
    """Test ZenbiScheduleCalendarEntity computes agenda_today and agenda_tomorrow."""
    coordinator = ZenbiCalendarDataUpdateCoordinator(mock_hass, mock_client, mock_config_entry)

    now = dt_util.now()
    today = now.date()
    tomorrow = today + timedelta(days=1)
    yesterday = today - timedelta(days=1)
    future_3d = today + timedelta(days=3)

    # Class 1: Today 08:00 - 08:45 (with homework)
    start_c1 = datetime.combine(today, time(8, 0)).astimezone()
    end_c1 = datetime.combine(today, time(8, 45)).astimezone()
    c1 = ZenbiCalendarItem(
        id="c1",
        title="Dansk",
        start=start_c1.isoformat(),
        end=end_c1.isoformat(),
        resources=[{"name": "Lokale 402"}],
        homework=[
            ZenbiHomework(
                id="hw-c1",
                calendar_item_id="c1",
                description="Læs side 10-15",
                raw_description="",
                date=start_c1.isoformat(),
                files=[{"name": "opgave", "extension": "pdf"}],
            )
        ],
        note="Husk bog",
    )

    # Class 2: Today 09:00 - 09:45 (with substitute)
    start_c2 = datetime.combine(today, time(9, 0)).astimezone()
    end_c2 = datetime.combine(today, time(9, 45)).astimezone()
    c2 = ZenbiCalendarItem(
        id="c2",
        title="Matematik",
        start=start_c2.isoformat(),
        end=end_c2.isoformat(),
        resources=[{"name": "Lokale 402"}],
        substitutes=[{"name": "Vikar Hans"}],
    )

    # Class 3: Tomorrow 10:00 - 10:45
    start_c3 = datetime.combine(tomorrow, time(10, 0)).astimezone()
    end_c3 = datetime.combine(tomorrow, time(10, 45)).astimezone()
    c3 = ZenbiCalendarItem(
        id="c3",
        title="Historie",
        start=start_c3.isoformat(),
        end=end_c3.isoformat(),
        resources=[{"name": "Lokale 101"}],
    )

    # Class 4: 3 days in future (should NOT be in today or tomorrow)
    start_c4 = datetime.combine(future_3d, time(8, 0)).astimezone()
    end_c4 = datetime.combine(future_3d, time(8, 45)).astimezone()
    c4 = ZenbiCalendarItem(
        id="c4",
        title="Engelsk",
        start=start_c4.isoformat(),
        end=end_c4.isoformat(),
    )

    # Class 5: Yesterday (should NOT be in today or tomorrow)
    start_c5 = datetime.combine(yesterday, time(8, 0)).astimezone()
    end_c5 = datetime.combine(yesterday, time(8, 45)).astimezone()
    c5 = ZenbiCalendarItem(
        id="c5",
        title="Idræt",
        start=start_c5.isoformat(),
        end=end_c5.isoformat(),
    )

    coordinator.data = ZenbiCalendarData(
        calendar_items=[c4, c2, c1, c3, c5],  # intentionally unordered
        window_start=start_c5,
        window_end=end_c4,
    )

    entity = ZenbiScheduleCalendarEntity(coordinator, mock_config_entry)
    attrs = entity.extra_state_attributes

    # Check counts
    assert attrs["classes_today"] == 2
    assert attrs["classes_tomorrow"] == 1
    assert len(attrs["agenda_today"]) == 2
    assert len(attrs["agenda_tomorrow"]) == 1

    # Check chronological ordering and formatting of agenda_today
    c1_out = attrs["agenda_today"][0]
    assert c1_out["title"] == "Dansk"
    assert c1_out["time"] == "08:00 - 08:45"
    assert c1_out["start_time"] == "08:00"
    assert c1_out["end_time"] == "08:45"
    assert c1_out["location"] == "Lokale 402"
    assert c1_out["note"] == "Husk bog"
    assert c1_out["has_homework"] is True
    assert len(c1_out["homework"]) == 1
    assert c1_out["homework"][0]["description"] == "Læs side 10-15"
    assert c1_out["homework"][0]["files"] == ["opgave.pdf"]

    c2_out = attrs["agenda_today"][1]
    assert c2_out["title"] == "Matematik"
    assert c2_out["time"] == "09:00 - 09:45"
    assert c2_out["substitutes"] == ["Vikar Hans"]
    assert c2_out["has_homework"] is False

    # Check agenda_tomorrow
    c3_out = attrs["agenda_tomorrow"][0]
    assert c3_out["title"] == "Historie"
    assert c3_out["time"] == "10:00 - 10:45"
    assert c3_out["location"] == "Lokale 101"

    # Test empty schedule returns safe defaults
    coordinator.data = ZenbiCalendarData(calendar_items=[])
    empty_attrs = entity.extra_state_attributes
    assert empty_attrs["agenda_today"] == []
    assert empty_attrs["agenda_tomorrow"] == []
    assert empty_attrs["classes_today"] == 0
    assert empty_attrs["classes_tomorrow"] == 0


@pytest.mark.asyncio
async def test_weekly_plan_sensor_automatic_monday_transition(
    mock_hass, mock_config_entry, mock_client
):
    """Test ZenbiWeeklyPlanSensor automatically transitions to the new week on Monday."""
    coordinator = ZenbiCalendarDataUpdateCoordinator(mock_hass, mock_client, mock_config_entry)

    sched_w38 = ZenbiWeeklySchedule(
        id="ws-38",
        title="Uge 38 Plan",
        start="2026-09-14T00:00:00+02:00",
        end="2026-09-21T00:00:00+02:00",
        description="Besked for uge 38.",
        raw_description="",
    )
    sched_w39 = ZenbiWeeklySchedule(
        id="ws-39",
        title="Uge 39 Plan",
        start="2026-09-21T00:00:00+02:00",
        end="2026-09-28T00:00:00+02:00",
        description="Besked for uge 39.",
        raw_description="",
    )
    sched_w40 = ZenbiWeeklySchedule(
        id="ws-40",
        title="Uge 40 Plan",
        start="2026-09-28T00:00:00+02:00",
        end="2026-10-05T00:00:00+02:00",
        description="Besked for uge 40.",
        raw_description="",
    )

    coordinator.data = ZenbiCalendarData(
        students=["Albert Hansen"],
        weekly_schedules=[sched_w38, sched_w39, sched_w40],
    )
    sensor = ZenbiWeeklyPlanSensor(coordinator, mock_config_entry, student_name="Albert Hansen")
    sensor_next = ZenbiNextWeeklyPlanSensor(
        coordinator, mock_config_entry, student_name="Albert Hansen"
    )

    # 1. On Sunday 2026-09-20 (end of week 38): current is W38, next is W39
    sunday_dt = datetime(2026, 9, 20, 18, 0, 0, tzinfo=timezone.utc)
    with patch("custom_components.zenbi.sensor.dt_util.now", return_value=sunday_dt):
        assert sensor.native_value == 1
        attrs_sun = sensor.extra_state_attributes
        assert "Besked for uge 38." in attrs_sun["content"]
        assert attrs_sun["title"] == "Uge 38 Plan"

        assert sensor_next.native_value == 1
        attrs_sun_next = sensor_next.extra_state_attributes
        assert "Besked for uge 39." in attrs_sun_next["content"]
        assert attrs_sun_next["title"] == "Uge 39 Plan"

    # 2. On Monday 2026-09-21 at 00:00:01 / 16:13 (start of week 39): current MUST be W39, next is W40!
    monday_dt = datetime(2026, 9, 21, 16, 13, 0, tzinfo=timezone.utc)
    with patch("custom_components.zenbi.sensor.dt_util.now", return_value=monday_dt):
        assert sensor.native_value == 1
        attrs_mon = sensor.extra_state_attributes
        assert "Besked for uge 39." in attrs_mon["content"]
        assert attrs_mon["title"] == "Uge 39 Plan"

        assert sensor_next.native_value == 1
        attrs_mon_next = sensor_next.extra_state_attributes
        assert "Besked for uge 40." in attrs_mon_next["content"]
        assert attrs_mon_next["title"] == "Uge 40 Plan"


@pytest.mark.asyncio
async def test_next_weekly_plan_sensor_empty_and_populated(
    mock_hass, mock_config_entry, mock_client
):
    """Test ZenbiNextWeeklyPlanSensor returns 0 and None attributes when no plan is posted for next week."""
    coordinator = ZenbiCalendarDataUpdateCoordinator(mock_hass, mock_client, mock_config_entry)

    today = dt_util.now().date()
    start_cur = (today - timedelta(days=today.weekday())).isoformat() + "T00:00:00+02:00"
    end_cur = (today + timedelta(days=6 - today.weekday())).isoformat() + "T23:59:59+02:00"

    sched_cur = ZenbiWeeklySchedule(
        id="ws-cur",
        title="Ugeplan for denne uge",
        start=start_cur,
        end=end_cur,
        description="Kun plan for denne uge er lagt op.",
        raw_description="",
    )

    coordinator.data = ZenbiCalendarData(
        students=["Albert Hansen"],
        weekly_schedules=[sched_cur],
    )

    sensor_next = ZenbiNextWeeklyPlanSensor(
        coordinator, mock_config_entry, student_name="Albert Hansen"
    )
    # No schedule for next week yet -> state is 0
    assert sensor_next.native_value == 0
    attrs = sensor_next.extra_state_attributes
    assert attrs["content"] is None
    assert attrs["title"] is None
    assert attrs["start_date"] is None
    assert attrs["end_date"] is None
    assert attrs["files"] == []

    # Now teacher posts next week's plan
    start_nxt = (
        today - timedelta(days=today.weekday()) + timedelta(days=7)
    ).isoformat() + "T00:00:00+02:00"
    end_nxt = (
        today + timedelta(days=6 - today.weekday()) + timedelta(days=7)
    ).isoformat() + "T23:59:59+02:00"

    sched_nxt = ZenbiWeeklySchedule(
        id="ws-nxt",
        title="Næste uge er lejrtur",
        start=start_nxt,
        end=end_nxt,
        description="Husk sovepose og rygsæk.",
        raw_description="",
        files=[{"name": "Pakkeliste.pdf"}],
    )
    coordinator.data.weekly_schedules.append(sched_nxt)

    assert sensor_next.native_value == 1
    attrs_populated = sensor_next.extra_state_attributes
    assert (
        attrs_populated["content"]
        == "Husk sovepose og rygsæk.\n\n### Vedhæftede filer\n- Pakkeliste.pdf"
    )
    assert attrs_populated["title"] == "Næste uge er lejrtur"
    assert attrs_populated["start_date"] == start_nxt
    assert attrs_populated["end_date"] == end_nxt
    assert attrs_populated["files"] == [{"name": "Pakkeliste.pdf", "is_image": False}]


@pytest.mark.asyncio
async def test_calendar_planning_entity_consecutive_label_merging(
    mock_hass, mock_config_entry, mock_client
):
    """Test that consecutive daily planning labels with identical titles are merged into clean multi-day CalendarEvents."""
    coordinator = ZenbiCalendarDataUpdateCoordinator(mock_hass, mock_client, mock_config_entry)

    # 5 daily labels matching Autumn break (Mon-Wed Fritter, Thu-Fri Ferien)
    labels = [
        ZenbiPlanningLabel(
            id="l1",
            title="Efterårsferie - Fritter åben",
            start_date="2026-10-12T10:00:00",
            description="Ferie",
        ),
        ZenbiPlanningLabel(
            id="l2",
            title="Efterårsferie - Fritter åben",
            start_date="2026-10-13T10:00:00",
            description="Ferie",
        ),
        ZenbiPlanningLabel(
            id="l3",
            title="Efterårsferie - Fritter åben",
            start_date="2026-10-14T10:00:00",
            description="Ferie",
        ),
        ZenbiPlanningLabel(
            id="l4",
            title="Efterårsferie",
            start_date="2026-10-15T10:00:00",
            description="Ferie",
        ),
        ZenbiPlanningLabel(
            id="l5",
            title="Efterårsferie",
            start_date="2026-10-16T10:00:00",
            description="Ferie",
        ),
    ]
    coordinator.data = ZenbiCalendarData(planning_labels=labels)

    entity = ZenbiPlanningCalendarEntity(coordinator, mock_config_entry)

    # _get_calendar_events merges them into 2 multi-day blocks
    events = entity._get_calendar_events()
    assert len(events) == 2

    # Block 1: Mon Oct 12 to Thu Oct 15 (exclusive end date)
    assert events[0].summary == "Efterårsferie - Fritter åben"
    assert events[0].start == date(2026, 10, 12)
    assert events[0].end == date(2026, 10, 15)
    assert events[0].description == "Ferie"

    # Block 2: Thu Oct 15 to Sat Oct 17 (exclusive end date)
    assert events[1].summary == "Efterårsferie"
    assert events[1].start == date(2026, 10, 15)
    assert events[1].end == date(2026, 10, 17)
    assert events[1].description == "Ferie"

    # async_get_events within window returns both
    query_start = datetime(2026, 10, 1, tzinfo=timezone.utc)
    query_end = datetime(2026, 10, 31, tzinfo=timezone.utc)
    fetched = await entity.async_get_events(mock_hass, query_start, query_end)
    assert len(fetched) == 2


@pytest.mark.asyncio
async def test_coordinator_on_demand_calendar_error_handling(
    mock_hass, mock_config_entry, mock_client
):
    """Test coordinator handles on-demand calendar fetch errors gracefully without crashing."""
    coordinator = ZenbiCalendarDataUpdateCoordinator(mock_hass, mock_client, mock_config_entry)
    coordinator.data = ZenbiCalendarData(
        window_start=datetime(2026, 9, 21, 0, 0, tzinfo=timezone.utc),
        window_end=datetime(2026, 10, 5, 0, 0, tzinfo=timezone.utc),
    )

    out_start = datetime(2026, 11, 1, 0, 0, tzinfo=timezone.utc)
    out_end = datetime(2026, 11, 7, 23, 59, tzinfo=timezone.utc)

    # API raises ConnectionError
    mock_client.get_calendar_items = AsyncMock(
        side_effect=ZenbiConnectionError("Server unreachable")
    )

    items = await coordinator.async_get_calendar_items(out_start, out_end)
    assert items == []


@pytest.mark.asyncio
async def test_coordinator_on_demand_weekly_schedules_error_handling(
    mock_hass, mock_config_entry, mock_client
):
    """Test coordinator handles on-demand weekly schedules fetch errors gracefully without crashing."""
    coordinator = ZenbiCalendarDataUpdateCoordinator(mock_hass, mock_client, mock_config_entry)
    coordinator.data = ZenbiCalendarData(
        window_start=datetime(2026, 9, 21, 0, 0, tzinfo=timezone.utc),
        window_end=datetime(2026, 10, 5, 0, 0, tzinfo=timezone.utc),
    )

    out_start = datetime(2026, 11, 1, 0, 0, tzinfo=timezone.utc)
    out_end = datetime(2026, 11, 7, 23, 59, tzinfo=timezone.utc)

    # API raises ApiError
    mock_client.get_weekly_schedules = AsyncMock(side_effect=ZenbiApiError("Bad request"))

    items = await coordinator.async_get_weekly_schedules(out_start, out_end)
    assert items == []


@pytest.mark.asyncio
async def test_concurrent_todo_item_updates(mock_hass, mock_config_entry, mock_client):
    """Test concurrent async_update_todo_item calls are protected by _store_lock and serialize saves."""
    coordinator = ZenbiCalendarDataUpdateCoordinator(mock_hass, mock_client, mock_config_entry)
    todo1 = ZenbiHomeworkTodoListEntity(coordinator, mock_config_entry, student_name="Student 1")
    todo2 = ZenbiHomeworkTodoListEntity(coordinator, mock_config_entry, student_name="Student 2")

    # Both share the same store mock
    item1 = TodoItem(summary="Hw 1", uid="hw-1", status=TodoItemStatus.COMPLETED)
    item2 = TodoItem(summary="Hw 2", uid="hw-2", status=TodoItemStatus.COMPLETED)

    await asyncio.gather(
        todo1.async_update_todo_item(item1),
        todo2.async_update_todo_item(item2),
    )

    assert "hw-1" in todo1._completed_ids
    assert "hw-2" in todo2._completed_ids


def test_weekly_plan_sensor_state_class(mock_hass, mock_config_entry, mock_client):
    """Test weekly plan sensors have MEASUREMENT state_class and unit."""
    coordinator = ZenbiCalendarDataUpdateCoordinator(mock_hass, mock_client, mock_config_entry)
    sensor_curr = ZenbiWeeklyPlanSensor(coordinator, mock_config_entry, student_name="Albert")
    sensor_next = ZenbiNextWeeklyPlanSensor(coordinator, mock_config_entry, student_name="Albert")

    assert sensor_curr._attr_state_class == SensorStateClass.MEASUREMENT
    assert sensor_curr._attr_native_unit_of_measurement == "messages"
    assert sensor_next._attr_state_class == SensorStateClass.MEASUREMENT
    assert sensor_next._attr_native_unit_of_measurement == "messages"
