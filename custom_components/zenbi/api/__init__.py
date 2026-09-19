"""Zenbi API client package."""

from .client import ZenbiApiClient, format_zenbi_datetime, get_copenhagen_tz
from .exceptions import ZenbiApiError, ZenbiAuthError, ZenbiConnectionError, ZenbiError
from .models import (
    ZenbiAuthResponse,
    ZenbiCalendarItem,
    ZenbiGlobalData,
    ZenbiHomework,
    ZenbiPlanningLabel,
    ZenbiPlanningMeta,
    ZenbiWeeklySchedule,
    parse_quill_delta,
)

__all__ = [
    "ZenbiApiClient",
    "ZenbiError",
    "ZenbiAuthError",
    "ZenbiApiError",
    "ZenbiConnectionError",
    "ZenbiAuthResponse",
    "ZenbiCalendarItem",
    "ZenbiGlobalData",
    "ZenbiHomework",
    "ZenbiPlanningLabel",
    "ZenbiPlanningMeta",
    "ZenbiWeeklySchedule",
    "parse_quill_delta",
    "format_zenbi_datetime",
    "get_copenhagen_tz",
]
