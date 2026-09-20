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

from .api.models import strip_markdown
from .const import DOMAIN, slugify_name
from .coordinator import (
    ZenbiCalendarData,
    ZenbiCalendarDataUpdateCoordinator,
    ZenbiCalendarItem,
)
from .sensor import format_attachment_display_name, is_image_file

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

    entities: List[CalendarEntity] = []

    students = coordinator.data.students if coordinator.data else []

    if students:
        for student in students:
            entities.append(
                ZenbiScheduleCalendarEntity(coordinator, entry, student_name=student)
            )
        # If unassigned schedule items exist, expose a general school schedule
        unassigned = coordinator.get_student_calendar_items(None)
        if unassigned:
            entities.append(
                ZenbiScheduleCalendarEntity(coordinator, entry, student_name=None)
            )
    else:
        # Fallback if no students discovered
        entities.append(
            ZenbiScheduleCalendarEntity(coordinator, entry, student_name=None)
        )

    # School-wide planning calendar and weekly messages archive
    entities.append(ZenbiPlanningCalendarEntity(coordinator, entry))
    entities.append(ZenbiWeeklyMessagesCalendarEntity(coordinator, entry))

    async_add_entities(entities)


def _format_date_clean(val: Optional[str]) -> Optional[str]:
    """Format an ISO date or datetime string into a readable format."""
    if not val:
        return None
    try:
        parsed = dt_util.parse_datetime(str(val))
        if parsed:
            return (
                parsed.strftime("%Y-%m-%d %H:%M")
                if (parsed.hour or parsed.minute)
                else parsed.strftime("%Y-%m-%d")
            )
        d = dt_util.parse_date(str(val))
        if d:
            return d.strftime("%Y-%m-%d")
    except Exception:
        pass
    clean = str(val).split(".")[0]
    return clean.replace("T00:00:00", "").replace("T", " ")


class ZenbiBaseCalendarEntity(
    CoordinatorEntity[ZenbiCalendarDataUpdateCoordinator], CalendarEntity
):
    """Base calendar entity for Zenbi."""

    _attr_has_entity_name = True
    # All entities are enabled by default; users can disable unused ones manually.
    _attr_entity_registry_enabled_default = True

    def __init__(
        self,
        coordinator: ZenbiCalendarDataUpdateCoordinator,
        entry: ConfigEntry,
        key: str,
        student_name: Optional[str] = None,
    ) -> None:
        """Initialize the base calendar entity."""
        super().__init__(coordinator)
        self.entry = entry
        self._key = key
        self._student_name = student_name
        self._attr_translation_key = key

        if student_name:
            slug = slugify_name(student_name)
            self._attr_unique_id = f"{entry.entry_id}_{slug}_{key}"
        else:
            self._attr_unique_id = f"{entry.entry_id}_{key}"

        self._cached_device_info: DeviceInfo = self._build_device_info()

    def _build_device_info(self) -> DeviceInfo:
        """Build a DeviceInfo object assigned to either student or school device."""
        if self._student_name:
            slug = slugify_name(self._student_name)
            return DeviceInfo(
                identifiers={(DOMAIN, f"{self.entry.entry_id}_{slug}")},
                name=f"Zenbi ({self._student_name})",
                manufacturer="Zenbi",
                model="Zenbi Education Portal",
                entry_type=DeviceEntryType.SERVICE,
            )

        # School-wide or generic fallback device
        has_students = bool(self.coordinator.data and self.coordinator.data.students)
        device_name = "Zenbi (School)" if has_students else "Zenbi"
        ident = f"{self.entry.entry_id}_school" if has_students else self.entry.entry_id
        return DeviceInfo(
            identifiers={(DOMAIN, ident)},
            name=device_name,
            manufacturer="Zenbi",
            model="Zenbi Education Portal",
            entry_type=DeviceEntryType.SERVICE,
        )

    def _handle_coordinator_update(self) -> None:
        """Refresh device info cache on coordinator data update, then write state."""
        self._cached_device_info = self._build_device_info()
        super()._handle_coordinator_update()

    async def async_added_to_hass(self) -> None:
        """Refresh device info cache once HA has confirmed coordinator data."""
        await super().async_added_to_hass()
        self._cached_device_info = self._build_device_info()

    @property
    def device_info(self) -> DeviceInfo:
        """Return cached device information."""
        return self._cached_device_info


class ZenbiScheduleCalendarEntity(ZenbiBaseCalendarEntity):
    """Calendar entity for Zenbi timed schedule items (school schema)."""

    def __init__(
        self,
        coordinator: ZenbiCalendarDataUpdateCoordinator,
        entry: ConfigEntry,
        student_name: Optional[str] = None,
    ) -> None:
        """Initialize Zenbi schedule calendar."""
        super().__init__(coordinator, entry, key="schedule", student_name=student_name)

    def _get_items(self) -> List[ZenbiCalendarItem]:
        """Get calendar items scoped to this entity's student or general school."""
        if not self.coordinator.data or not self.coordinator.data.calendar_items:
            return []
        if self._student_name is not None:
            return self.coordinator.get_student_calendar_items(self._student_name)
        # If no students are known, return all items
        if not self.coordinator.data.students:
            return self.coordinator.data.calendar_items
        return self.coordinator.get_student_calendar_items(None)

    @property
    def event(self) -> Optional[CalendarEvent]:
        """Return the next or current upcoming event."""
        items = self._get_items()
        if not items:
            return None

        now = dt_util.now()
        upcoming_events: List[CalendarEvent] = []

        for item in items:
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

            summary = item.title
            if item.homework:
                summary = f"{item.title} 📚"

            if item.homework:
                hw_sections: List[str] = []
                for hw in item.homework:
                    section: List[str] = []
                    meta_lines: List[str] = []
                    if getattr(hw, "date", None):
                        clean_d = _format_date_clean(hw.date)
                        if clean_d:
                            meta_lines.append(f"**Afleveringsfrist:** {clean_d}")
                    if getattr(hw, "updated_time", None):
                        clean_u = _format_date_clean(hw.updated_time)
                        if clean_u:
                            meta_lines.append(f"**Oprettet / opdateret:** {clean_u}")
                    if meta_lines:
                        section.append("\n".join(meta_lines))

                    if hw.description:
                        section.append(hw.description)

                    if hw.files:
                        file_links: List[str] = []
                        for f in hw.files:
                            if isinstance(f, dict):
                                f_id = f.get("id") or f.get("fileId")
                                name = f.get("name") or f.get("title")
                                display_name = format_attachment_display_name(f)
                                if name and f_id:
                                    url = f"/api/zenbi/file/{self.entry.entry_id}/{f_id}"
                                    file_links.append(f'<a href="{url}" target="_blank" download>{display_name}</a>')
                                elif name:
                                    file_links.append(display_name)
                        if file_links:
                            section.append("Vedhæftede filer:\n" + "\n".join(f"- {fl}" for fl in file_links))

                    if section:
                        hw_sections.append("\n\n".join(section))

                if hw_sections:
                    desc_parts.append("### Lektier\n\n" + "\n\n---\n\n".join(hw_sections))

            return CalendarEvent(
                start=start_dt,
                end=end_dt,
                summary=summary,
                description="\n\n".join(desc_parts) if desc_parts else None,
                location=location,
                uid=str(item.id),
            )
        except Exception as err:
            _LOGGER.warning("Error parsing calendar item %s: %s", getattr(item, "id", "unknown"), err)
            return None

    @property
    def extra_state_attributes(self) -> Dict[str, Any]:
        """Return extra state attributes of the schedule entity.

        Only scalar values and flattened name lists are written to the recorder
        to prevent unbounded raw API dicts accumulating in the database.
        """
        attrs: Dict[str, Any] = {}
        items = self._get_items()
        if not items:
            return attrs

        current_event = self.event
        if current_event and current_event.uid:
            for item in items:
                if str(item.id) == current_event.uid:
                    if item.homework:
                        attrs["homework"] = [
                            {
                                "id": hw.id,
                                "description": hw.description,
                                "date": hw.date,
                                # Flatten to name strings only — no raw API dicts in recorder
                                "files": [
                                    f.get("name") or f.get("title", "")
                                    for f in hw.files
                                    if isinstance(f, dict)
                                ],
                            }
                            for hw in item.homework
                        ]
                    if item.note:
                        attrs["note"] = item.note
                    if item.substitutes:
                        # Flatten substitutes to name strings only
                        attrs["substitutes"] = [
                            s.get("name") or s.get("title", "")
                            for s in item.substitutes
                            if isinstance(s, dict)
                        ]
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
        items = await self.coordinator.async_get_calendar_items(
            start_date, end_date, student_name=self._student_name
        )
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
    """Calendar entity for Zenbi all-day planning labels (assigned to School device)."""

    def __init__(
        self,
        coordinator: ZenbiCalendarDataUpdateCoordinator,
        entry: ConfigEntry,
    ) -> None:
        """Initialize Zenbi planning calendar."""
        super().__init__(coordinator, entry, key="planning", student_name=None)

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
    """Calendar entity for Zenbi weekly schedule messages (assigned to School device)."""

    def __init__(
        self,
        coordinator: ZenbiCalendarDataUpdateCoordinator,
        entry: ConfigEntry,
    ) -> None:
        """Initialize Zenbi weekly messages calendar."""
        super().__init__(coordinator, entry, key="weekly_messages", student_name=None)

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
            # On weekends if next week is not yet published, fallback to most recent message
            if self.coordinator.data.weekly_schedules:
                return self._schedule_to_calendar_event(self.coordinator.data.weekly_schedules[-1])
            return None

        upcoming.sort(
            key=lambda e: e.start if isinstance(e.start, date) else e.start.date()
        )
        return upcoming[0]

    def _schedule_to_calendar_event(self, schedule: Any) -> Optional[CalendarEvent]:
        """Convert a ZenbiWeeklySchedule into a Monday-to-Friday all-day CalendarEvent."""
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

            # Calculate end date: weekly messages span Monday to Friday (excluding weekends).
            # In Home Assistant, all-day event end_date is exclusive.
            # Setting end_date to Saturday (5 days from Monday) displays the event across Mon, Tue, Wed, Thu, Fri.
            if start_date.weekday() <= 4:
                # End after Friday (exclusive end date is Saturday)
                end_date = start_date + timedelta(days=(5 - start_date.weekday()))
            else:
                # If message started on a weekend, default to 1 day
                end_date = start_date + timedelta(days=1)

            desc_parts: List[str] = []
            if getattr(schedule, "description", None):
                desc_parts.append(schedule.description)

            if getattr(schedule, "files", None):
                file_links: List[str] = []
                for f in schedule.files:
                    if isinstance(f, dict):
                        f_id = f.get("id") or f.get("fileId")
                        name = f.get("name") or f.get("title")
                        display_name = format_attachment_display_name(f)
                        if name and f_id:
                            url = f"/api/zenbi/file/{self.entry.entry_id}/{f_id}"
                            file_links.append(f'<a href="{url}" target="_blank" download>{display_name}</a>')
                        elif name:
                            file_links.append(display_name)
                if file_links:
                    desc_parts.append(f"Vedhæftede filer:\n" + "\n".join(f"- {fl}" for fl in file_links))

            return CalendarEvent(
                start=start_date,
                end=end_date,
                summary=strip_markdown(getattr(schedule, "title", "Weekly Message")),
                description="\n\n".join(desc_parts) if desc_parts else None,
                uid=str(schedule.id),
            )
        except Exception as err:
            _LOGGER.warning("Error parsing weekly schedule %s: %s", getattr(schedule, "id", "unknown"), err)
            return None

    @property
    def extra_state_attributes(self) -> Dict[str, Any]:
        """Return extra state attributes containing attachment files."""
        attrs: Dict[str, Any] = {}
        if not self.coordinator.data or not self.coordinator.data.weekly_schedules:
            return attrs

        current_event = self.event
        if not current_event or not current_event.uid:
            return attrs

        for s in self.coordinator.data.weekly_schedules:
            if str(s.id) == current_event.uid:
                if s.files:
                    files: List[Dict[str, Any]] = []
                    for f in s.files:
                        if isinstance(f, dict):
                            f_id = f.get("id") or f.get("fileId")
                            display_name = format_attachment_display_name(f)
                            is_img = is_image_file(f)
                            item: Dict[str, Any] = {
                                "name": display_name,
                                "is_image": is_img,
                            }
                            ext = f.get("extension")
                            if isinstance(ext, str) and ext.strip():
                                item["extension"] = ext.strip().lstrip(".")
                            if f.get("size") is not None:
                                item["size"] = f["size"]
                            if f_id:
                                item["id"] = f_id
                                item["url"] = f"/api/zenbi/file/{self.entry.entry_id}/{f_id}"
                            files.append(item)
                    if files:
                        attrs["files"] = files
                break
        return attrs

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
