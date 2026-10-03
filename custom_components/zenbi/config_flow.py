"""Config flow for Zenbi integration."""

from __future__ import annotations

import logging
from typing import Any, Dict, Mapping, Optional

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.config_entries import ConfigFlowResult
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api.client import ZenbiApiClient, generate_stable_device_id
from .api.exceptions import ZenbiAuthError, ZenbiConnectionError
from .const import (
    CONF_CALENDAR_SYNC_INTERVAL_HOURS,
    CONF_PASSWORD,
    CONF_UNIQUE_DEVICE_ID,
    CONF_USERNAME,
    DEFAULT_CALENDAR_SYNC_INTERVAL_HOURS,
    DOMAIN,
)
from .options_flow import ZenbiOptionsFlowHandler

_LOGGER = logging.getLogger(__name__)

STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_USERNAME): str,
        vol.Required(CONF_PASSWORD): str,
        vol.Optional(CONF_UNIQUE_DEVICE_ID): str,
    }
)


class ZenbiConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Zenbi."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize Zenbi config flow."""
        self._reauth_entry: Optional[config_entries.ConfigEntry] = None

    async def async_step_user(
        self, user_input: Optional[Dict[str, Any]] = None
    ) -> ConfigFlowResult:
        """Handle the initial setup step."""
        errors: Dict[str, str] = {}

        if user_input is not None:
            username = user_input[CONF_USERNAME].strip()
            password = user_input[CONF_PASSWORD]
            unique_device_id = (
                user_input.get(CONF_UNIQUE_DEVICE_ID, "").strip()
                or generate_stable_device_id(username)
            )

            session = async_get_clientsession(self.hass)
            client = ZenbiApiClient(
                username=username,
                password=password,
                unique_device_id=unique_device_id,
                session=session,
            )

            try:
                await client.authenticate()
            except ZenbiAuthError:
                errors["base"] = "invalid_auth"
            except ZenbiConnectionError:
                errors["base"] = "cannot_connect"
            except Exception as err:
                _LOGGER.exception("Unexpected error during authentication: %s", err)
                errors["base"] = "unknown"
            else:
                # Use normalized lowercase username as unique identifier
                await self.async_set_unique_id(username.lower())
                self._abort_if_unique_id_configured()

                return self.async_create_entry(
                    title=username,
                    data={
                        CONF_USERNAME: username,
                        CONF_PASSWORD: password,
                        CONF_UNIQUE_DEVICE_ID: unique_device_id,
                    },
                    options={
                        CONF_CALENDAR_SYNC_INTERVAL_HOURS: DEFAULT_CALENDAR_SYNC_INTERVAL_HOURS,
                    },
                )

        return self.async_show_form(
            step_id="user",
            data_schema=STEP_USER_DATA_SCHEMA,
            errors=errors,
        )

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """Handle re-authentication trigger from ConfigEntryAuthFailed."""
        if not self._reauth_entry:
            context = getattr(self, "context", {}) or {}
            entry_id = context.get("entry_id")
            if entry_id and hasattr(self.hass, "config_entries"):
                self._reauth_entry = self.hass.config_entries.async_get_entry(entry_id)
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: Optional[Dict[str, Any]] = None
    ) -> ConfigFlowResult:
        """Handle user confirmation and updated password submission for reauth."""
        errors: Dict[str, str] = {}
        username = (
            self._reauth_entry.data[CONF_USERNAME]
            if self._reauth_entry
            else self.context.get("title", "")
        )
        unique_device_id = (
            self._reauth_entry.data.get(CONF_UNIQUE_DEVICE_ID)
            if self._reauth_entry
            else None
        )

        if user_input is not None:
            password = user_input[CONF_PASSWORD]
            session = async_get_clientsession(self.hass)
            client = ZenbiApiClient(
                username=username,
                password=password,
                unique_device_id=unique_device_id,
                session=session,
            )

            try:
                await client.authenticate()
            except ZenbiAuthError:
                errors["base"] = "invalid_auth"
            except ZenbiConnectionError:
                errors["base"] = "cannot_connect"
            except Exception as err:
                _LOGGER.exception("Unexpected error during re-authentication: %s", err)
                errors["base"] = "unknown"
            else:
                if self._reauth_entry:
                    self.hass.config_entries.async_update_entry(
                        self._reauth_entry,
                        data={
                            **self._reauth_entry.data,
                            CONF_PASSWORD: password,
                        },
                    )
                    await self.hass.config_entries.async_reload(
                        self._reauth_entry.entry_id
                    )
                    return self.async_abort(reason="reauth_successful")

                return self.async_abort(reason="reauth_successful")

        schema = vol.Schema({vol.Required(CONF_PASSWORD): str})
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=schema,
            errors=errors,
            description_placeholders={"username": username},
        )

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> config_entries.OptionsFlow:
        """Return the options flow handler."""
        return ZenbiOptionsFlowHandler(config_entry)
