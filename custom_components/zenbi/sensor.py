"""Sensor platform for the Zenbi integration."""

from __future__ import annotations

from datetime import date, datetime
import logging
from typing import Any, Dict, List, Optional

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from .const import DOMAIN, slugify_name
from .coordinator import ZenbiCalendarDataUpdateCoordinator, ZenbiWeeklySchedule

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Zenbi sensor entities based on a config entry."""
    coordinator: ZenbiCalendarDataUpdateCoordinator = (
        getattr(entry, "runtime_data", None) or hass.data[DOMAIN][entry.entry_id]
    )

    entities: List[SensorEntity] = []
    students = coordinator.data.students if coordinator.data else []

    if students:
        for student in students:
            entities.append(
                ZenbiWeeklyPlanSensor(coordinator, entry, student_name=student)
            )
    else:
        entities.append(
            ZenbiWeeklyPlanSensor(coordinator, entry, student_name=None)
        )

    # Diagnostic sync status timestamp sensor on the primary school/integration device
    entities.append(ZenbiLastSyncedSensor(coordinator, entry))

    async_add_entities(entities)


class ZenbiLastSyncedSensor(
    CoordinatorEntity[ZenbiCalendarDataUpdateCoordinator], SensorEntity
):
    """Diagnostic sensor exposing the timestamp of the last successful synchronization with Zenbi."""

    _attr_has_entity_name = True
    _attr_translation_key = "last_synced"
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(
        self,
        coordinator: ZenbiCalendarDataUpdateCoordinator,
        entry: ConfigEntry,
    ) -> None:
        """Initialize the last synced diagnostic sensor."""
        super().__init__(coordinator)
        self.entry = entry
        self._attr_unique_id = f"{entry.entry_id}_last_synced"
        self._cached_device_info: DeviceInfo = self._build_device_info()

    def _build_device_info(self) -> DeviceInfo:
        """Build a DeviceInfo object assigned to the primary school/integration device."""
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
        """Update device info cache and state when coordinator data refreshes."""
        self._cached_device_info = self._build_device_info()
        super()._handle_coordinator_update()

    @property
    def device_info(self) -> DeviceInfo:
        """Return the device info."""
        return self._cached_device_info

    @property
    def native_value(self) -> Optional[datetime]:
        """Return the timestamp of the last successful synchronization."""
        return self.coordinator.last_sync_success

    @property
    def extra_state_attributes(self) -> Dict[str, Any]:
        """Return diagnostic sync details and rolling window metadata."""
        attrs: Dict[str, Any] = {
            "last_status": self.coordinator.last_sync_status,
            "last_error": self.coordinator.last_error,
            "consecutive_failures": self.coordinator.consecutive_failures,
        }
        if self.coordinator.data:
            if self.coordinator.data.window_start:
                attrs["rolling_window_start"] = self.coordinator.data.window_start.isoformat()
            if self.coordinator.data.window_end:
                attrs["rolling_window_end"] = self.coordinator.data.window_end.isoformat()
        return attrs


class ZenbiWeeklyPlanSensor(
    CoordinatorEntity[ZenbiCalendarDataUpdateCoordinator], SensorEntity
):
    """Sensor exposing active and upcoming weekly plans in clean Markdown."""

    _attr_has_entity_name = True
    _attr_translation_key = "weekly_plan"

    def __init__(
        self,
        coordinator: ZenbiCalendarDataUpdateCoordinator,
        entry: ConfigEntry,
        student_name: Optional[str] = None,
    ) -> None:
        """Initialize the weekly plan sensor."""
        super().__init__(coordinator)
        self.entry = entry
        self._student_name = student_name

        if student_name:
            slug = slugify_name(student_name)
            self._attr_unique_id = f"{entry.entry_id}_{slug}_weekly_plan"
        else:
            self._attr_unique_id = f"{entry.entry_id}_weekly_plan"

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
        """Update device info cache and state when coordinator data refreshes."""
        self._cached_device_info = self._build_device_info()
        super()._handle_coordinator_update()

    async def async_added_to_hass(self) -> None:
        """Refresh device info cache once entity is registered."""
        await super().async_added_to_hass()
        self._cached_device_info = self._build_device_info()

    @property
    def device_info(self) -> DeviceInfo:
        """Return cached device information."""
        return self._cached_device_info

    @staticmethod
    def _get_dates(schedule: ZenbiWeeklySchedule) -> tuple[Optional[date], Optional[date]]:
        """Extract start and end date from a weekly schedule."""
        start_d: Optional[date] = None
        end_d: Optional[date] = None

        if schedule.start_dt:
            start_d = schedule.start_dt.date()
        elif schedule.start:
            p = dt_util.parse_datetime(schedule.start)
            start_d = p.date() if p else dt_util.parse_date(schedule.start)

        if schedule.end_dt:
            end_d = schedule.end_dt.date()
        elif schedule.end:
            p = dt_util.parse_datetime(schedule.end)
            end_d = p.date() if p else dt_util.parse_date(schedule.end)

        return start_d, end_d

    def _get_active_and_next_groups(
        self,
    ) -> tuple[List[ZenbiWeeklySchedule], List[ZenbiWeeklySchedule]]:
        """Return all schedules grouped into the active week and next week."""
        if not self.coordinator.data or not self.coordinator.data.weekly_schedules:
            return [], []

        schedules = list(self.coordinator.data.weekly_schedules)

        def _sort_key(s: ZenbiWeeklySchedule) -> datetime:
            if s.start_dt:
                return s.start_dt
            try:
                parsed = dt_util.parse_datetime(s.start)
                if parsed:
                    return parsed
            except Exception:
                pass
            return datetime.min.replace(tzinfo=dt_util.now().tzinfo)

        schedules.sort(key=_sort_key)

        now = dt_util.now()
        today = now.date()

        # 1. Identify active week window
        active_window_start: Optional[date] = None
        active_window_end: Optional[date] = None

        for s in schedules:
            start_d, end_d = self._get_dates(s)
            if start_d and end_d and start_d <= today <= end_d:
                active_window_start = start_d
                active_window_end = end_d
                break

        # If no schedule strictly covers today, pick the earliest upcoming
        if not active_window_start:
            for s in schedules:
                start_d, end_d = self._get_dates(s)
                if start_d and start_d >= today:
                    active_window_start = start_d
                    active_window_end = end_d
                    break

        # Fallback to latest schedule if all are in the past
        if not active_window_start and schedules:
            active_window_start, active_window_end = self._get_dates(schedules[-1])

        active_schedules: List[ZenbiWeeklySchedule] = []
        next_schedules: List[ZenbiWeeklySchedule] = []
        next_window_start: Optional[date] = None

        for s in schedules:
            start_d, end_d = self._get_dates(s)
            if active_window_start and start_d == active_window_start:
                active_schedules.append(s)
            elif active_window_start and start_d and start_d > active_window_start:
                if next_window_start is None:
                    next_window_start = start_d
                if start_d == next_window_start:
                    next_schedules.append(s)

        return active_schedules, next_schedules

    @classmethod
    def _format_attachment_links(
        cls, schedules: List[ZenbiWeeklySchedule], entry_id: Optional[str] = None
    ) -> List[str]:
        """Format markdown links for unique files across schedules."""
        seen_keys = set()
        links: List[str] = []
        for s in schedules:
            if not s.files:
                continue
            for f in s.files:
                if not isinstance(f, dict):
                    continue
                file_id = f.get("id") or f.get("fileId")
                name = f.get("name") or f.get("title") or "Vedhæftet fil"
                key = file_id or name
                if key in seen_keys:
                    continue
                seen_keys.add(key)

                if file_id and entry_id:
                    url = f"/api/zenbi/file/{entry_id}/{file_id}"
                    links.append(f'<a href="{url}" target="_blank" download>{name}</a>')
                else:
                    links.append(name)
        return links

    @classmethod
    def _format_plan_text(
        cls, schedules: List[ZenbiWeeklySchedule], entry_id: Optional[str] = None
    ) -> str:
        """Format plan text, merging multiple messages or listing attachments if text is empty."""
        with_desc = [s for s in schedules if s.description and s.description.strip()]
        file_links = cls._format_attachment_links(schedules, entry_id)

        main_text = ""
        if len(with_desc) == 1:
            main_text = with_desc[0].description.strip()
        elif len(with_desc) > 1:
            sections: List[str] = []
            for s in with_desc:
                title = s.title or "Ugeplan"
                sections.append(f"# {title}\n\n{s.description.strip()}")
            main_text = "\n\n---\n\n".join(sections)

        if main_text:
            if file_links:
                file_list = "\n".join(f"- {link}" for link in file_links)
                return f"{main_text}\n\n### Vedhæftede filer\n{file_list}"
            return main_text

        # No messages have descriptions -> list files if any exist
        if file_links:
            file_list = "\n".join(f"- {link}" for link in file_links)
            return f"*Vedhæftede filer til denne uge:*\n{file_list}"

        return ""

    @property
    def native_value(self) -> Optional[str]:
        """Return the primary title of the active weekly plan."""
        active_schedules, _ = self._get_active_and_next_groups()
        if not active_schedules:
            return None

        # Prioritize schedule with description
        with_desc = [s for s in active_schedules if s.description and s.description.strip()]
        primary = with_desc[0] if with_desc else active_schedules[0]

        title = primary.title or "Weekly Plan"
        if len(active_schedules) > 1:
            title = f"{title} (+{len(active_schedules) - 1} mere)"
        return title[:255]

    @property
    def extra_state_attributes(self) -> Dict[str, Any]:
        """Return extra state attributes containing Markdown text and metadata."""
        attrs: Dict[str, Any] = {}
        active_schedules, next_schedules = self._get_active_and_next_groups()
        entry_id = self.entry.entry_id

        if active_schedules:
            with_desc = [s for s in active_schedules if s.description and s.description.strip()]
            primary = with_desc[0] if with_desc else active_schedules[0]

            attrs["current_week_plan"] = self._format_plan_text(
                active_schedules, entry_id
            )
            attrs["title"] = primary.title
            attrs["start_date"] = primary.start
            attrs["end_date"] = primary.end

            # Aggregate all files across active schedules as rich dicts
            files: List[Dict[str, Any]] = []
            seen_file_keys = set()
            for s in active_schedules:
                if s.files:
                    for f in s.files:
                        if isinstance(f, dict):
                            file_id = f.get("id") or f.get("fileId")
                            name = f.get("name") or f.get("title")
                            key = file_id or name
                            if key and key not in seen_file_keys:
                                seen_file_keys.add(key)
                                item: Dict[str, Any] = {"name": name}
                                if file_id:
                                    item["id"] = file_id
                                    item["url"] = f"/api/zenbi/file/{entry_id}/{file_id}"
                                files.append(item)
            if files:
                attrs["files"] = files

        if next_schedules:
            with_desc = [s for s in next_schedules if s.description and s.description.strip()]
            primary_next = with_desc[0] if with_desc else next_schedules[0]

            attrs["next_week_plan"] = self._format_plan_text(
                next_schedules, entry_id
            )
            attrs["next_week_title"] = primary_next.title
            attrs["next_week_start"] = primary_next.start

        return attrs
