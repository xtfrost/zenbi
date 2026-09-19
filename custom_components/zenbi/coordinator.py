"""DataUpdateCoordinator for the Zenbi integration."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timedelta
import logging
from typing import Dict, List, Optional, Tuple

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api.client import ZenbiApiClient, get_copenhagen_tz
from .api.exceptions import ZenbiApiError, ZenbiAuthError, ZenbiConnectionError
from .api.models import (
    ZenbiCalendarItem,
    ZenbiHomework,
    ZenbiPlanningLabel,
    ZenbiWeeklySchedule,
)
from .const import (
    CONF_CALENDAR_SYNC_INTERVAL_HOURS,
    DEFAULT_CALENDAR_SYNC_INTERVAL_HOURS,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)


def calculate_rolling_window(now: Optional[datetime] = None) -> Tuple[datetime, datetime]:
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

    async def _async_update_data(self) -> ZenbiCalendarData:
        """Fetch data from Zenbi API for rolling two-week window concurrently."""
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
                raise UpdateFailed(f"Zenbi authentication failed: {calendar_res}") from calendar_res
            if isinstance(calendar_res, ZenbiConnectionError):
                raise UpdateFailed(f"Connection error to Zenbi API: {calendar_res}") from calendar_res
            if isinstance(calendar_res, ZenbiApiError):
                raise UpdateFailed(f"Zenbi API error: {calendar_res}") from calendar_res
            _LOGGER.exception("Unexpected error fetching Zenbi calendar data: %s", calendar_res)
            raise UpdateFailed(f"Unexpected error: {calendar_res}") from calendar_res

        calendar_items: List[ZenbiCalendarItem] = calendar_res or []

        # Non-critical endpoints with fallback to empty list
        if isinstance(homework_res, Exception):
            _LOGGER.warning("Failed to fetch Zenbi homework: %s", homework_res)
            homeworks: List[ZenbiHomework] = []
        else:
            homeworks = homework_res or []

        if isinstance(weekly_res, Exception):
            _LOGGER.warning("Failed to fetch Zenbi weekly schedules: %s", weekly_res)
            weekly_schedules: List[ZenbiWeeklySchedule] = []
        else:
            weekly_schedules = weekly_res or []

        if isinstance(planning_res, Exception):
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

        return ZenbiCalendarData(
            calendar_items=calendar_items,
            planning_labels=planning_labels,
            homeworks=homeworks,
            weekly_schedules=weekly_schedules,
            last_synced=now,
            window_start=start_dt,
            window_end=end_dt,
        )

    async def async_get_calendar_items(
        self, start_date: datetime, end_date: datetime
    ) -> List[ZenbiCalendarItem]:
        """Serve calendar events, querying API on-demand if range exceeds cached window."""
        # If cache is valid and requested range is within the window, filter locally
        if (
            self.data
            and self.data.window_start
            and self.data.window_end
            and start_date >= self.data.window_start
            and end_date <= self.data.window_end
        ):
            filtered: List[ZenbiCalendarItem] = []
            for item in self.data.calendar_items:
                try:
                    item_start = dt_util.parse_datetime(item.start)
                    item_end = dt_util.parse_datetime(item.end)
                    if item_start and item_end:
                        if item_end >= start_date and item_start <= end_date:
                            filtered.append(item)
                    else:
                        filtered.append(item)
                except Exception:
                    filtered.append(item)
            return filtered

        # Out of rolling range -> fetch on-demand directly from API
        _LOGGER.debug(
            "Requested range (%s to %s) outside cached window. Fetching on demand.",
            start_date.isoformat(),
            end_date.isoformat(),
        )
        calendar_items = await self.client.get_calendar_items(start_date, end_date)
        try:
            homeworks = await self.client.get_homework(start_date, end_date)
            hw_by_calendar_id: Dict[str, List[ZenbiHomework]] = {}
            for hw in homeworks:
                if hw.calendar_item_id:
                    hw_by_calendar_id.setdefault(hw.calendar_item_id, []).append(hw)
            for item in calendar_items:
                if item.id in hw_by_calendar_id:
                    item.homework = hw_by_calendar_id[item.id]
        except Exception as err:
            _LOGGER.debug("Failed to fetch on-demand homework: %s", err)

        return calendar_items

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
                try:
                    item_start = dt_util.parse_datetime(item.start)
                    item_end = dt_util.parse_datetime(item.end)
                    if item_start and item_end:
                        if item_end >= start_date and item_start <= end_date:
                            filtered.append(item)
                    else:
                        filtered.append(item)
                except Exception:
                    filtered.append(item)
            return filtered

        _LOGGER.debug(
            "Weekly schedule range (%s to %s) outside cached window. Fetching on demand.",
            start_date.isoformat(),
            end_date.isoformat(),
        )
        return await self.client.get_weekly_schedules(start_date, end_date)
