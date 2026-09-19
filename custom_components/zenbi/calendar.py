"""Calendar platform for the Zenbi integration."""

from __future__ import annotations

from datetime import date, datetime, timedelta
import logging
from typing import Any, Dict, List, Optional

from homeassistant.components.calendar import CalendarEntity, CalendarEvent
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from .const import DOMAIN
from .coordinator import ZenbiCalendarData, ZenbiCalendarDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Zenbi calendar entities based on a config entry."""
    coordinator: ZenbiCalendarDataUpdateCoordinator = (
        getattr(entry, "runtime_data", None) or hass.data[DOMAIN][entry.entry_id]
    )

    entities: List[CalendarEntity] = [
        ZenbiScheduleCalendarEntity(coordinator, entry),
        ZenbiPlanningCalendarEntity(coordinator, entry),
        ZenbiWeeklyMessagesCalendarEntity(coordinator, entry),
    ]

    async_add_entities(entities)


class ZenbiBaseCalendarEntity(
    CoordinatorEntity[ZenbiCalendarDataUpdateCoordinator], CalendarEntity
):
    """Base calendar entity for Zenbi."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: ZenbiCalendarDataUpdateCoordinator,
        entry: ConfigEntry,
        key: str,
    ) -> None:
        """Initialize the base calendar entity."""
        super().__init__(coordinator)
        self.entry = entry
        self._key = key
        self._attr_translation_key = key
        self._attr_unique_id = f"{entry.entry_id}_{key}"

    @property
    def device_info(self) -> DeviceInfo:
        """Return device information to link entities together."""
        title = self.entry.title
        if (
            self.coordinator.data
            and self.coordinator.data.students
            and len(self.coordinator.data.students) == 1
        ):
            title = self.coordinator.data.students[0]
        return DeviceInfo(
            identifiers={(DOMAIN, self.entry.entry_id)},
            name=f"Zenbi ({title})",
            manufacturer="Zenbi",
            model="Zenbi Education Portal",
            entry_type=DeviceEntryType.SERVICE,
        )


class ZenbiScheduleCalendarEntity(ZenbiBaseCalendarEntity):
    """Calendar entity for Zenbi timed schedule items (school schema)."""

    def __init__(
        self,
        coordinator: ZenbiCalendarDataUpdateCoordinator,
        entry: ConfigEntry,
    ) -> None:
        """Initialize Zenbi schedule calendar."""
        super().__init__(coordinator, entry, key="schedule")

    @property
    def entity_registry_enabled_default(self) -> bool:
        """Only enable calendar by default if there are schedule items."""
        return bool(self.coordinator.data and self.coordinator.data.calendar_items)

    @property
    def event(self) -> Optional[CalendarEvent]:
        """Return the next or current upcoming event."""
        if not self.coordinator.data or not self.coordinator.data.calendar_items:
            return None

        now = dt_util.now()
        upcoming_events: List[CalendarEvent] = []

        for item in self.coordinator.data.calendar_items:
            event = self._item_to_calendar_event(item)
            if event:
                event_end = (
                    event.end
                    if isinstance(event.end, datetime)
                    else dt_util.start_of_local_day(event.end)
                )
                if event_end > now:
                    upcoming_events.append(event)

        if not upcoming_events:
            return None

        # Sort by start time
        upcoming_events.sort(
            key=lambda e: e.start
            if isinstance(e.start, datetime)
            else dt_util.start_of_local_day(e.start)
        )
        return upcoming_events[0]

    def _item_to_calendar_event(self, item: Any) -> Optional[CalendarEvent]:
        """Convert a ZenbiCalendarItem into a Home Assistant CalendarEvent."""
        try:
            start_dt = dt_util.parse_datetime(item.start)
            end_dt = dt_util.parse_datetime(item.end)
            if not start_dt or not end_dt:
                return None

            # Build rich description
            desc_parts: List[str] = []
            if item.description:
                desc_parts.append(item.description)
            if item.note:
                desc_parts.append(f"Note: {item.note}")

            location: Optional[str] = None
            if item.resources:
                resource_names = [
                    r.get("name") or r.get("title")
                    for r in item.resources
                    if isinstance(r, dict) and (r.get("name") or r.get("title"))
                ]
                if resource_names:
                    location = ", ".join(resource_names)
                    desc_parts.append(f"Resources: {location}")

            if item.substitutes:
                sub_names = [
                    s.get("name") or s.get("title")
                    for s in item.substitutes
                    if isinstance(s, dict) and (s.get("name") or s.get("title"))
                ]
                if sub_names:
                    desc_parts.append(f"Substitutes: {', '.join(sub_names)}")

            if item.homework:
                hw_sections: List[str] = []
                for hw in item.homework:
                    section: List[str] = []
                    if hw.description:
                        section.append(hw.description)
                    if hw.files:
                        file_names = [
                            f.get("name") or f.get("title")
                            for f in hw.files
                            if isinstance(f, dict) and (f.get("name") or f.get("title"))
                        ]
                        if file_names:
                            section.append(f"Files: {', '.join(filter(None, file_names))}")
                    if section:
                        hw_sections.append("\n".join(section))
                if hw_sections:
                    desc_parts.append("Homework:\n" + "\n---\n".join(hw_sections))

            return CalendarEvent(
                start=start_dt,
                end=end_dt,
                summary=item.title,
                description="\n\n".join(desc_parts) if desc_parts else None,
                location=location,
                uid=str(item.id),
            )
        except Exception as err:
            _LOGGER.warning("Error parsing calendar item %s: %s", getattr(item, "id", "unknown"), err)
            return None

    @property
    def extra_state_attributes(self) -> Dict[str, Any]:
        """Return extra state attributes of the schedule entity."""
        attrs: Dict[str, Any] = {}
        if not self.coordinator.data or not self.coordinator.data.calendar_items:
            return attrs

        current_event = self.event
        if current_event and current_event.uid:
            for item in self.coordinator.data.calendar_items:
                if str(item.id) == current_event.uid:
                    if item.homework:
                        attrs["homework"] = [
                            {
                                "id": hw.id,
                                "description": hw.description,
                                "date": hw.date,
                                "files": hw.files,
                            }
                            for hw in item.homework
                        ]
                    if item.note:
                        attrs["note"] = item.note
                    if item.substitutes:
                        attrs["substitutes"] = item.substitutes
                    if item.planning:
                        attrs["planning_icon"] = item.planning.icon
                        attrs["planning_color"] = item.planning.color
                    break
        return attrs

    async def async_get_events(
        self,
        hass: HomeAssistant,
        start_date: datetime,
        end_date: datetime,
    ) -> List[CalendarEvent]:
        """Return calendar events within a datetime range."""
        items = await self.coordinator.async_get_calendar_items(start_date, end_date)
        events: List[CalendarEvent] = []

        for item in items:
            event = self._item_to_calendar_event(item)
            if event:
                event_start = (
                    event.start
                    if isinstance(event.start, datetime)
                    else dt_util.start_of_local_day(event.start)
                )
                event_end = (
                    event.end
                    if isinstance(event.end, datetime)
                    else dt_util.start_of_local_day(event.end)
                )
                if event_end >= start_date and event_start <= end_date:
                    events.append(event)

        return events


class ZenbiPlanningCalendarEntity(ZenbiBaseCalendarEntity):
    """Calendar entity for Zenbi all-day planning labels."""

    def __init__(
        self,
        coordinator: ZenbiCalendarDataUpdateCoordinator,
        entry: ConfigEntry,
    ) -> None:
        """Initialize Zenbi planning calendar."""
        super().__init__(coordinator, entry, key="planning")

    @property
    def entity_registry_enabled_default(self) -> bool:
        """Only enable calendar by default if there are planning labels."""
        return bool(self.coordinator.data and self.coordinator.data.planning_labels)

    @property
    def event(self) -> Optional[CalendarEvent]:
        """Return the next or current upcoming all-day event."""
        if not self.coordinator.data or not self.coordinator.data.planning_labels:
            return None

        today = dt_util.now().date()
        upcoming: List[CalendarEvent] = []

        for label in self.coordinator.data.planning_labels:
            event = self._label_to_calendar_event(label)
            if event:
                end_date = event.end if isinstance(event.end, date) else event.end.date()
                if end_date >= today:
                    upcoming.append(event)

        if not upcoming:
            return None

        upcoming.sort(
            key=lambda e: e.start if isinstance(e.start, date) else e.start.date()
        )
        return upcoming[0]

    def _label_to_calendar_event(self, label: Any) -> Optional[CalendarEvent]:
        """Convert a ZenbiPlanningLabel into an all-day CalendarEvent."""
        try:
            # Parse start date
            start_date: Optional[date] = None
            if hasattr(label, "start_date") and label.start_date:
                parsed_dt = dt_util.parse_datetime(label.start_date)
                if parsed_dt:
                    start_date = parsed_dt.date()
                else:
                    start_date = dt_util.parse_date(label.start_date)

            if not start_date:
                return None

            # Parse end date
            end_date: Optional[date] = None
            if hasattr(label, "end_date") and label.end_date:
                parsed_end_dt = dt_util.parse_datetime(label.end_date)
                if parsed_end_dt:
                    end_date = parsed_end_dt.date()
                else:
                    end_date = dt_util.parse_date(label.end_date)

            # In Home Assistant, all-day calendar event end date is exclusive
            if not end_date or end_date <= start_date:
                end_date = start_date + timedelta(days=1)
            else:
                end_date = end_date + timedelta(days=1)

            desc = label.description if hasattr(label, "description") else None

            return CalendarEvent(
                start=start_date,
                end=end_date,
                summary=label.title,
                description=desc,
                uid=str(label.id),
            )
        except Exception as err:
            _LOGGER.warning("Error parsing planning label %s: %s", getattr(label, "id", "unknown"), err)
            return None

    async def async_get_events(
        self,
        hass: HomeAssistant,
        start_date: datetime,
        end_date: datetime,
    ) -> List[CalendarEvent]:
        """Return all planning events intersecting the given range."""
        if not self.coordinator.data or not self.coordinator.data.planning_labels:
            return []

        start_d = start_date.date()
        end_d = end_date.date()
        events: List[CalendarEvent] = []

        for label in self.coordinator.data.planning_labels:
            event = self._label_to_calendar_event(label)
            if event:
                ev_start = event.start if isinstance(event.start, date) else event.start.date()
                ev_end = event.end if isinstance(event.end, date) else event.end.date()
                if ev_end >= start_d and ev_start <= end_d:
                    events.append(event)

        return events


class ZenbiWeeklyMessagesCalendarEntity(ZenbiBaseCalendarEntity):
    """Calendar entity for Zenbi weekly schedule messages (ugeplaner)."""

    def __init__(
        self,
        coordinator: ZenbiCalendarDataUpdateCoordinator,
        entry: ConfigEntry,
    ) -> None:
        """Initialize Zenbi weekly messages calendar."""
        super().__init__(coordinator, entry, key="weekly_messages")

    @property
    def entity_registry_enabled_default(self) -> bool:
        """Only enable calendar by default if there are weekly schedule messages."""
        return bool(self.coordinator.data and self.coordinator.data.weekly_schedules)

    @property
    def event(self) -> Optional[CalendarEvent]:
        """Return the next or current upcoming weekly message."""
        if not self.coordinator.data or not self.coordinator.data.weekly_schedules:
            return None

        today = dt_util.now().date()
        upcoming: List[CalendarEvent] = []

        for schedule in self.coordinator.data.weekly_schedules:
            event = self._schedule_to_calendar_event(schedule)
            if event:
                end_date = event.end if isinstance(event.end, date) else event.end.date()
                if end_date >= today:
                    upcoming.append(event)

        if not upcoming:
            return None

        upcoming.sort(
            key=lambda e: e.start if isinstance(e.start, date) else e.start.date()
        )
        return upcoming[0]

    def _schedule_to_calendar_event(self, schedule: Any) -> Optional[CalendarEvent]:
        """Convert a ZenbiWeeklySchedule into a 7-day all-day CalendarEvent."""
        try:
            start_date: Optional[date] = None
            if hasattr(schedule, "start") and schedule.start:
                parsed_dt = dt_util.parse_datetime(schedule.start)
                if parsed_dt:
                    start_date = parsed_dt.date()
                else:
                    start_date = dt_util.parse_date(schedule.start)

            if not start_date:
                return None

            end_date: Optional[date] = None
            if hasattr(schedule, "end") and schedule.end:
                parsed_end_dt = dt_util.parse_datetime(schedule.end)
                if parsed_end_dt:
                    end_date = parsed_end_dt.date()
                else:
                    end_date = dt_util.parse_date(schedule.end)

            # In Home Assistant, all-day calendar event end date is exclusive (7 days)
            if not end_date or end_date <= start_date:
                end_date = start_date + timedelta(days=7)

            desc_parts: List[str] = []
            if getattr(schedule, "description", None):
                desc_parts.append(schedule.description)

            if getattr(schedule, "files", None):
                file_names = [
                    f.get("name") or f.get("title")
                    for f in schedule.files
                    if isinstance(f, dict) and (f.get("name") or f.get("title"))
                ]
                if file_names:
                    desc_parts.append(f"Attachments: {', '.join(file_names)}")

            return CalendarEvent(
                start=start_date,
                end=end_date,
                summary=getattr(schedule, "title", "Weekly Message"),
                description="\n\n".join(desc_parts) if desc_parts else None,
                uid=str(schedule.id),
            )
        except Exception as err:
            _LOGGER.warning("Error parsing weekly schedule %s: %s", getattr(schedule, "id", "unknown"), err)
            return None

    async def async_get_events(
        self,
        hass: HomeAssistant,
        start_date: datetime,
        end_date: datetime,
    ) -> List[CalendarEvent]:
        """Return all weekly schedule events intersecting the given range."""
        schedules = await self.coordinator.async_get_weekly_schedules(start_date, end_date)
        start_d = start_date.date()
        end_d = end_date.date()
        events: List[CalendarEvent] = []

        for schedule in schedules:
            event = self._schedule_to_calendar_event(schedule)
            if event:
                ev_start = event.start if isinstance(event.start, date) else event.start.date()
                ev_end = event.end if isinstance(event.end, date) else event.end.date()
                if ev_end >= start_d and ev_start <= end_d:
                    events.append(event)

        return events


