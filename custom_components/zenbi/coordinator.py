"""DataUpdateCoordinator for the Zenbi integration."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timedelta
import logging
import time
from typing import Dict, List, Optional, Tuple

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api.client import ZenbiApiClient, get_copenhagen_tz
from .api.exceptions import ZenbiApiError, ZenbiAuthError, ZenbiConnectionError
from .api.models import (
    ZenbiCalendarItem,
    ZenbiHomework,
    ZenbiPlanningLabel,
    ZenbiWeeklySchedule,
    extract_student_names,
)
from .const import (
    CONF_CALENDAR_SYNC_INTERVAL_HOURS,
    DEFAULT_CALENDAR_SYNC_INTERVAL_HOURS,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)


def calculate_rolling_window(
    now: Optional[datetime] = None,
) -> Tuple[datetime, datetime]:
    """Calculate rolling window covering 2 full weeks (Monday 00:00:00 to Monday 00:00:00 14 days later)."""
    if now is None:
        now = dt_util.now()

    cph_tz = get_copenhagen_tz()
    if now.tzinfo is None:
        now = now.replace(tzinfo=cph_tz)
    else:
        now = now.astimezone(cph_tz)

    start_of_week = (now - timedelta(days=now.weekday())).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    # End of rolling window is 14 days later (Monday 00:00:00 of the 3rd week)
    end_of_window = (start_of_week + timedelta(days=14)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    return start_of_week, end_of_window


@dataclass
class ZenbiCalendarData:
    """Class to hold Zenbi calendar and planning cached data."""

    calendar_items: List[ZenbiCalendarItem] = field(default_factory=list)
    planning_labels: List[ZenbiPlanningLabel] = field(default_factory=list)
    homeworks: List[ZenbiHomework] = field(default_factory=list)
    weekly_schedules: List[ZenbiWeeklySchedule] = field(default_factory=list)
    students: List[str] = field(default_factory=list)
    last_synced: Optional[datetime] = None
    window_start: Optional[datetime] = None
    window_end: Optional[datetime] = None


class ZenbiCalendarDataUpdateCoordinator(DataUpdateCoordinator[ZenbiCalendarData]):
    """Coordinator to manage rolling window fetching of Zenbi calendar and planning data."""

    def __init__(
        self,
        hass: HomeAssistant,
        client: ZenbiApiClient,
        entry: ConfigEntry,
    ) -> None:
        """Initialize the coordinator."""
        interval_hours = entry.options.get(
            CONF_CALENDAR_SYNC_INTERVAL_HOURS,
            entry.data.get(CONF_CALENDAR_SYNC_INTERVAL_HOURS, DEFAULT_CALENDAR_SYNC_INTERVAL_HOURS),
        )
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_{entry.entry_id}",
            update_interval=timedelta(hours=interval_hours),
        )
        self.client = client
        self.entry = entry
        self.last_sync_success: Optional[datetime] = None
        self.last_sync_status: str = "success"
        self.last_error: Optional[str] = None
        self.consecutive_failures: int = 0
        self._on_demand_cache: Dict[str, Tuple[float, List[ZenbiCalendarItem]]] = {}

    async def async_shutdown(self) -> None:
        """Cancel background update tasks and shut down coordinator."""
        _LOGGER.debug("Shutting down Zenbi coordinator for %s", self.name)
        self._on_demand_cache.clear()
        if hasattr(DataUpdateCoordinator, "async_shutdown"):
            await super().async_shutdown()

    async def _async_update_data(self) -> ZenbiCalendarData:
        """Fetch data from Zenbi API for rolling two-week window concurrently."""
        try:
            now = dt_util.now()
            start_dt, end_dt = calculate_rolling_window(now)
            _LOGGER.debug(
                "Fetching Zenbi calendar window from %s to %s concurrently",
                start_dt.isoformat(),
                end_dt.isoformat(),
            )

            # Run all 4 endpoint queries concurrently
            results = await asyncio.gather(
                self.client.get_calendar_items(start_dt, end_dt),
                self.client.get_homework(start_dt, end_dt),
                self.client.get_weekly_schedules(start_dt, end_dt),
                self.client.get_planning_labels(),
                return_exceptions=True,
            )

            calendar_res, homework_res, weekly_res, planning_res = results

            # Critical endpoint: calendar items
            if isinstance(calendar_res, Exception):
                if isinstance(calendar_res, ZenbiAuthError):
                    raise ConfigEntryAuthFailed(
                        f"Zenbi credentials are invalid: {calendar_res}"
                    ) from calendar_res
                if isinstance(calendar_res, ZenbiConnectionError):
                    raise UpdateFailed(
                        f"Connection error to Zenbi API: {calendar_res}"
                    ) from calendar_res
                if isinstance(calendar_res, ZenbiApiError):
                    raise UpdateFailed(f"Zenbi API error: {calendar_res}") from calendar_res
                _LOGGER.error(
                    "Unexpected error fetching Zenbi calendar data: %s",
                    calendar_res,
                    exc_info=calendar_res,
                )
                raise UpdateFailed(f"Unexpected error: {calendar_res}") from calendar_res

            calendar_items: List[ZenbiCalendarItem] = calendar_res or []

            # Non-critical endpoints with fallback to empty list
            if isinstance(homework_res, Exception):
                if isinstance(homework_res, ZenbiAuthError):
                    raise ConfigEntryAuthFailed(
                        f"Zenbi credentials are invalid: {homework_res}"
                    ) from homework_res
                _LOGGER.warning("Failed to fetch Zenbi homework: %s", homework_res)
                homeworks: List[ZenbiHomework] = []
            else:
                homeworks = homework_res or []

            if isinstance(weekly_res, Exception):
                if isinstance(weekly_res, ZenbiAuthError):
                    raise ConfigEntryAuthFailed(
                        f"Zenbi credentials are invalid: {weekly_res}"
                    ) from weekly_res
                _LOGGER.warning("Failed to fetch Zenbi weekly schedules: %s", weekly_res)
                weekly_schedules: List[ZenbiWeeklySchedule] = []
            else:
                weekly_schedules = weekly_res or []

            if isinstance(planning_res, Exception):
                if isinstance(planning_res, ZenbiAuthError):
                    raise ConfigEntryAuthFailed(
                        f"Zenbi credentials are invalid: {planning_res}"
                    ) from planning_res
                _LOGGER.warning("Failed to fetch Zenbi planning labels: %s", planning_res)
                planning_labels: List[ZenbiPlanningLabel] = []
            else:
                planning_labels = planning_res or []

            # Associate homework items with their calendar entries
            if homeworks and calendar_items:
                hw_by_calendar_id: Dict[str, List[ZenbiHomework]] = {}
                for hw in homeworks:
                    if hw.calendar_item_id:
                        hw_by_calendar_id.setdefault(hw.calendar_item_id, []).append(hw)

                for item in calendar_items:
                    if item.id in hw_by_calendar_id:
                        item.homework = hw_by_calendar_id[item.id]

            # Extract student names from calendar items
            students = extract_student_names(calendar_items)

            data = ZenbiCalendarData(
                calendar_items=calendar_items,
                planning_labels=planning_labels,
                homeworks=homeworks,
                weekly_schedules=weekly_schedules,
                students=students,
                last_synced=now,
                window_start=start_dt,
                window_end=end_dt,
            )

            self.last_sync_success = dt_util.utcnow()
            self.last_sync_status = "success"
            self.last_error = None
            self.consecutive_failures = 0
            return data

        except Exception as err:
            self.last_sync_status = "failure"
            self.last_error = str(err)
            self.consecutive_failures += 1
            raise

    def get_student_calendar_items(
        self, student_name: Optional[str] = None
    ) -> List[ZenbiCalendarItem]:
        """Return calendar items filtered by student.

        If student_name is provided, returns items where student_name is in item.student_names.
        If student_name is None, returns items with no student assigned (school-wide / general).
        """
        if not self.data or not self.data.calendar_items:
            return []

        if student_name is not None:
            return [item for item in self.data.calendar_items if student_name in item.student_names]

        # Unassigned items
        return [item for item in self.data.calendar_items if not item.student_names]

    def get_student_homeworks(self, student_name: Optional[str] = None) -> List[ZenbiHomework]:
        """Return homework items filtered by student.

        Matches homework to student via its associated calendar item.
        """
        if not self.data or not self.data.homeworks:
            return []

        cal_by_id = {item.id: item for item in (self.data.calendar_items or [])}

        if student_name is not None:
            filtered: List[ZenbiHomework] = []
            for hw in self.data.homeworks:
                cal_item = cal_by_id.get(hw.calendar_item_id)
                if cal_item and student_name in cal_item.student_names:
                    filtered.append(hw)
            return filtered

        # Unassigned homework
        filtered_unassigned: List[ZenbiHomework] = []
        for hw in self.data.homeworks:
            cal_item = cal_by_id.get(hw.calendar_item_id)
            if not cal_item or not cal_item.student_names:
                filtered_unassigned.append(hw)
        return filtered_unassigned

    async def async_get_calendar_items(
        self,
        start_date: datetime,
        end_date: datetime,
        student_name: Optional[str] = None,
    ) -> List[ZenbiCalendarItem]:
        """Serve calendar events, querying API on-demand if range exceeds cached window."""
        items: List[ZenbiCalendarItem] = []

        # If cache is valid and requested range is within the window, filter locally
        if (
            self.data
            and self.data.window_start
            and self.data.window_end
            and start_date >= self.data.window_start
            and end_date <= self.data.window_end
        ):
            for item in self.data.calendar_items:
                item_start = item.start_dt
                item_end = item.end_dt
                if item_start and item_end:
                    if item_end >= start_date and item_start <= end_date:
                        items.append(item)
                else:
                    # Keep items whose dates could not be parsed
                    items.append(item)
        else:
            # Out of rolling range -> check on-demand TTL cache (2-hour TTL = 7200s)
            cache_key = f"{start_date.isoformat()}_{end_date.isoformat()}"
            now_ts = time.time()
            if cache_key in self._on_demand_cache:
                cached_time, cached_items = self._on_demand_cache[cache_key]
                if now_ts < cached_time + 7200:
                    _LOGGER.debug(
                        "Serving on-demand calendar range (%s to %s) from 2h TTL cache",
                        start_date.isoformat(),
                        end_date.isoformat(),
                    )
                    items = cached_items
                else:
                    self._on_demand_cache.pop(cache_key, None)

            if not items:
                _LOGGER.debug(
                    "Requested range (%s to %s) outside cached window. Fetching on demand.",
                    start_date.isoformat(),
                    end_date.isoformat(),
                )
                try:
                    items = await self.client.get_calendar_items(start_date, end_date)
                    try:
                        homeworks = await self.client.get_homework(start_date, end_date)
                        hw_by_calendar_id: Dict[str, List[ZenbiHomework]] = {}
                        for hw in homeworks:
                            if hw.calendar_item_id:
                                hw_by_calendar_id.setdefault(hw.calendar_item_id, []).append(hw)
                        for item in items:
                            if item.id in hw_by_calendar_id:
                                item.homework = hw_by_calendar_id[item.id]
                    except Exception as err:
                        _LOGGER.debug("Failed to fetch on-demand homework: %s", err)

                    self._on_demand_cache[cache_key] = (now_ts, items)
                except (ZenbiApiError, ZenbiConnectionError, ZenbiAuthError) as err:
                    _LOGGER.warning(
                        "On-demand calendar fetch failed (%s to %s): %s",
                        start_date.isoformat(),
                        end_date.isoformat(),
                        err,
                    )
                    items = []
                except Exception as err:
                    _LOGGER.error(
                        "Unexpected error during on-demand calendar fetch (%s to %s): %s",
                        start_date.isoformat(),
                        end_date.isoformat(),
                        err,
                    )
                    items = []

        # Apply student filter if specified
        if student_name is not None:
            return [it for it in items if student_name in it.student_names]

        return items

    async def async_get_weekly_schedules(
        self, start_date: datetime, end_date: datetime
    ) -> List[ZenbiWeeklySchedule]:
        """Serve weekly schedules, querying API on-demand if range exceeds cached window."""
        if (
            self.data
            and self.data.window_start
            and self.data.window_end
            and start_date >= self.data.window_start
            and end_date <= self.data.window_end
        ):
            filtered: List[ZenbiWeeklySchedule] = []
            for item in self.data.weekly_schedules:
                item_start = item.start_dt
                item_end = item.end_dt
                if item_start and item_end:
                    if item_end >= start_date and item_start <= end_date:
                        filtered.append(item)
                else:
                    # Keep items whose dates could not be parsed
                    filtered.append(item)
            return filtered

        _LOGGER.debug(
            "Weekly schedule range (%s to %s) outside cached window. Fetching on demand.",
            start_date.isoformat(),
            end_date.isoformat(),
        )
        try:
            return await self.client.get_weekly_schedules(start_date, end_date)
        except (ZenbiApiError, ZenbiConnectionError, ZenbiAuthError) as err:
            _LOGGER.warning(
                "On-demand weekly schedule fetch failed (%s to %s): %s",
                start_date.isoformat(),
                end_date.isoformat(),
                err,
            )
            return []
        except Exception as err:
            _LOGGER.error(
                "Unexpected error during on-demand weekly schedule fetch (%s to %s): %s",
                start_date.isoformat(),
                end_date.isoformat(),
                err,
            )
            return []
