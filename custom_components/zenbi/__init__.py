"""The Zenbi integration."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.storage import Store

from .api.client import ZenbiApiClient
from .const import (
    CONF_PASSWORD,
    CONF_UNIQUE_DEVICE_ID,
    CONF_USERNAME,
    DOMAIN,
    PLATFORMS,
    STORAGE_KEY_TODO,
    STORAGE_VERSION,
)
from .coordinator import ZenbiCalendarDataUpdateCoordinator
from .http import ZenbiFileDownloadView

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Zenbi from a config entry."""
    hass.data.setdefault(DOMAIN, {})

    username = entry.data[CONF_USERNAME]
    password = entry.data[CONF_PASSWORD]
    unique_device_id = entry.data.get(CONF_UNIQUE_DEVICE_ID)

    session = async_get_clientsession(hass)
    client = ZenbiApiClient(
        username=username,
        password=password,
        unique_device_id=unique_device_id,
        session=session,
    )

    coordinator = ZenbiCalendarDataUpdateCoordinator(hass, client, entry)

    # Perform initial data fetch
    await coordinator.async_config_entry_first_refresh()

    hass.data[DOMAIN][entry.entry_id] = coordinator
    if hasattr(entry, "runtime_data"):
        entry.runtime_data = coordinator

    # Forward setup to platforms (calendar, todo, sensor)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Register file download view once across all Zenbi entries
    if not hass.data[DOMAIN].get("_view_registered"):
        if hasattr(hass, "http") and hasattr(hass.http, "register_view"):
            hass.http.register_view(ZenbiFileDownloadView())
        hass.data[DOMAIN]["_view_registered"] = True

    # Register options update listener
    entry.async_on_unload(entry.add_update_listener(async_reload_entry))

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a Zenbi config entry."""
    # 1. Stop the background refresh loop BEFORE platform entities start tearing down,
    #    to prevent an in-flight coordinator refresh from racing entity removal.
    coordinator = (
        getattr(entry, "runtime_data", None)
        or hass.data.get(DOMAIN, {}).get(entry.entry_id)
    )
    if coordinator and hasattr(coordinator, "async_shutdown"):
        await coordinator.async_shutdown()

    # 2. Now safely unload all platform entities.
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
        if hasattr(entry, "runtime_data"):
            entry.runtime_data = None

    return unload_ok


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Handle cleanup when a Zenbi config entry is deleted by the user.
    
    Home Assistant Core automatically purges associated EntityRegistry and
    DeviceRegistry entries. This hook deletes custom persistent .storage files.
    """
    _LOGGER.debug("Removing persistent Zenbi storage for entry %s", entry.entry_id)
    store = Store(hass, STORAGE_VERSION, STORAGE_KEY_TODO.format(entry_id=entry.entry_id))
    try:
        await store.async_remove()
        _LOGGER.debug("Successfully removed Zenbi storage for entry %s", entry.entry_id)
    except Exception as err:
        _LOGGER.warning(
            "Error removing persistent Zenbi storage for entry %s: %s",
            entry.entry_id,
            err,
        )


async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload the config entry when options change."""
    await hass.config_entries.async_reload(entry.entry_id)


