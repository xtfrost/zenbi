"""Options flow for Zenbi integration."""

from __future__ import annotations

from typing import Any, Dict, Optional

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResult

from .const import (
    CONF_CALENDAR_SYNC_INTERVAL_HOURS,
    CONF_NOTIFICATION_SYNC_INTERVAL_MINS,
    DEFAULT_CALENDAR_SYNC_INTERVAL_HOURS,
    DEFAULT_NOTIFICATION_SYNC_INTERVAL_MINS,
)


class ZenbiOptionsFlowHandler(config_entries.OptionsFlow):
    """Handle Zenbi options."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        """Initialize options flow."""
        self._config_entry = config_entry

    @property
    def config_entry(self) -> config_entries.ConfigEntry:
        """Return the config entry."""
        return self._config_entry

    async def async_step_init(
        self, user_input: Optional[Dict[str, Any]] = None
    ) -> FlowResult:
        """Manage the options."""
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        current_calendar_hours = self.config_entry.options.get(
            CONF_CALENDAR_SYNC_INTERVAL_HOURS,
            self.config_entry.data.get(
                CONF_CALENDAR_SYNC_INTERVAL_HOURS, DEFAULT_CALENDAR_SYNC_INTERVAL_HOURS
            ),
        )
        current_notification_mins = self.config_entry.options.get(
            CONF_NOTIFICATION_SYNC_INTERVAL_MINS,
            self.config_entry.data.get(
                CONF_NOTIFICATION_SYNC_INTERVAL_MINS,
                DEFAULT_NOTIFICATION_SYNC_INTERVAL_MINS,
            ),
        )

        schema = vol.Schema(
            {
                vol.Required(
                    CONF_CALENDAR_SYNC_INTERVAL_HOURS,
                    default=current_calendar_hours,
                ): vol.All(vol.Coerce(int), vol.Range(min=1, max=168)),
                vol.Required(
                    CONF_NOTIFICATION_SYNC_INTERVAL_MINS,
                    default=current_notification_mins,
                ): vol.All(vol.Coerce(int), vol.Range(min=1, max=1440)),
            }
        )

        return self.async_show_form(step_id="init", data_schema=schema)

