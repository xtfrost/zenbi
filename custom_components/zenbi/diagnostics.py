"""Diagnostics support for the Zenbi integration."""

from __future__ import annotations

from typing import Any, Dict

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import CONF_PASSWORD, CONF_UNIQUE_DEVICE_ID, DOMAIN
from .coordinator import ZenbiCalendarDataUpdateCoordinator

TO_REDACT = {CONF_PASSWORD, CONF_UNIQUE_DEVICE_ID, "token", "refreshToken"}


def _redact_data(data: Dict[str, Any]) -> Dict[str, Any]:
    """Redact sensitive fields from dictionary."""
    redacted = {}
    for key, value in data.items():
        if key in TO_REDACT:
            redacted[key] = "**REDACTED**"
        elif isinstance(value, dict):
            redacted[key] = _redact_data(value)
        else:
            redacted[key] = value
    return redacted


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> Dict[str, Any]:
    """Return diagnostics for a config entry."""
    coordinator: ZenbiCalendarDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]

    diag_data: Dict[str, Any] = {
        "entry": {
            "title": entry.title,
            "domain": entry.domain,
            "version": entry.version,
            "data": _redact_data(dict(entry.data)),
            "options": dict(entry.options),
        },
        "coordinator": {
            "last_synced": (
                coordinator.data.last_synced.isoformat()
                if coordinator.data and coordinator.data.last_synced
                else None
            ),
            "window_start": (
                coordinator.data.window_start.isoformat()
                if coordinator.data and coordinator.data.window_start
                else None
            ),
            "window_end": (
                coordinator.data.window_end.isoformat()
                if coordinator.data and coordinator.data.window_end
                else None
            ),
            "calendar_items_count": (
                len(coordinator.data.calendar_items)
                if coordinator.data and coordinator.data.calendar_items
                else 0
            ),
            "homeworks_count": (
                len(coordinator.data.homeworks)
                if coordinator.data and coordinator.data.homeworks
                else 0
            ),
            "weekly_schedules_count": (
                len(coordinator.data.weekly_schedules)
                if coordinator.data and coordinator.data.weekly_schedules
                else 0
            ),
            "planning_labels_count": (
                len(coordinator.data.planning_labels)
                if coordinator.data and coordinator.data.planning_labels
                else 0
            ),
        },
        "client": {
            "base_url": coordinator.client.base_url,
            "has_token": bool(coordinator.client.token),
            "token_expired": coordinator.client.is_token_expired(),
            "timeframe_id": coordinator.client.timeframe_id,
        },
    }

    return diag_data

