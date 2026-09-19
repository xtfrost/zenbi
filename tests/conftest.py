"""Pytest fixtures and Home Assistant shims for testing without full HA installation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timezone
import enum
import sys
import types
from typing import Any, Callable, Dict, Generic, List, Optional, TypeVar
from unittest.mock import MagicMock

_DataT = TypeVar("_DataT")
_CoordinatorT = TypeVar("_CoordinatorT")


# Define shim classes
class MockConfigEntry:
    def __init__(
        self,
        entry_id: str = "test_entry_id",
        data: Optional[Dict[str, Any]] = None,
        options: Optional[Dict[str, Any]] = None,
        title: str = "test@example.com",
        domain: str = "zenbi",
        version: int = 1,
    ):
        self.entry_id = entry_id
        self.data = data or {}
        self.options = options or {}
        self.title = title
        self.domain = domain
        self.version = version
        self.runtime_data = None
        self._update_listeners = []

    def async_on_unload(self, callback: Callable) -> None:
        pass

    def add_update_listener(self, listener: Callable) -> Callable:
        self._update_listeners.append(listener)
        return lambda: self._update_listeners.remove(listener)


class MockConfigFlow:
    VERSION = 1

    def __init_subclass__(cls, domain: str = "", **kwargs):
        super().__init_subclass__(**kwargs)
        cls.DOMAIN = domain

    def __init__(self):
        self.hass = None
        self.unique_id = None

    async def async_set_unique_id(self, unique_id: str) -> Optional[Any]:
        self.unique_id = unique_id
        return getattr(self, "_existing_entry", None)

    def _abort_if_unique_id_configured(self) -> None:
        pass

    def async_abort(
        self, reason: str, description_placeholders: Optional[Dict[str, str]] = None
    ) -> Dict[str, Any]:
        return {
            "type": "abort",
            "reason": reason,
            "description_placeholders": description_placeholders or {},
        }

    def async_show_form(
        self,
        step_id: str,
        data_schema: Any,
        errors: Optional[Dict[str, str]] = None,
        description_placeholders: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        return {
            "type": "form",
            "step_id": step_id,
            "schema": data_schema,
            "errors": errors or {},
            "description_placeholders": description_placeholders or {},
        }

    def async_create_entry(
        self,
        title: str,
        data: Dict[str, Any],
        options: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        return {
            "type": "create_entry",
            "title": title,
            "data": data,
            "options": options or {},
        }


class MockOptionsFlow:
    def __init__(self, config_entry: Any = None):
        self.config_entry = config_entry

    def async_show_form(
        self, step_id: str, data_schema: Any, errors: Optional[Dict[str, str]] = None
    ) -> Dict[str, Any]:
        return {
            "type": "form",
            "step_id": step_id,
            "schema": data_schema,
            "errors": errors or {},
        }

    def async_create_entry(self, title: str, data: Dict[str, Any]) -> Dict[str, Any]:
        return {"type": "create_entry", "title": title, "data": data}


class MockHomeAssistant:
    def __init__(self):
        from unittest.mock import AsyncMock
        self.data: Dict[str, Any] = {}
        self.config_entries = MagicMock()
        self.config_entries.async_reload = AsyncMock(return_value=True)
        self.config_entries.async_update_entry = MagicMock()
        self.config_entries.async_forward_entry_setups = AsyncMock(return_value=True)
        self.config_entries.async_unload_platforms = AsyncMock(return_value=True)


@dataclass
class CalendarEvent:
    start: datetime | date
    end: datetime | date
    summary: str
    description: Optional[str] = None
    location: Optional[str] = None
    uid: Optional[str] = None
    recurrence_id: Optional[str] = None
    rrule: Optional[str] = None


class CalendarEntity:
    _attr_has_entity_name = False
    _attr_name: Optional[str] = None
    _attr_unique_id: Optional[str] = None
    _attr_translation_key: Optional[str] = None

    @property
    def unique_id(self) -> Optional[str]:
        return self._attr_unique_id

    @property
    def translation_key(self) -> Optional[str]:
        return self._attr_translation_key

    @property
    def entity_registry_enabled_default(self) -> bool:
        return True

    @property
    def name(self) -> Optional[str]:
        return self._attr_name

    @property
    def event(self) -> Optional[CalendarEvent]:
        return None

    async def async_get_events(
        self, hass: Any, start_date: datetime, end_date: datetime
    ) -> List[CalendarEvent]:
        return []


class DeviceEntryType(str, enum.Enum):
    SERVICE = "service"


@dataclass
class DeviceInfo:
    identifiers: Any = None
    name: Optional[str] = None
    manufacturer: Optional[str] = None
    model: Optional[str] = None
    entry_type: Optional[DeviceEntryType] = None


class UpdateFailed(Exception):
    """Exception to indicate update failure."""


class DataUpdateCoordinator(Generic[_DataT]):
    def __init__(self, hass: Any, logger: Any, name: str, update_interval: Any):
        self.hass = hass
        self.logger = logger
        self.name = name
        self.update_interval = update_interval
        self.data: Optional[_DataT] = None

    async def _async_update_data(self) -> _DataT:
        raise NotImplementedError

    async def async_config_entry_first_refresh(self):
        self.data = await self._async_update_data()


class CoordinatorEntity(Generic[_CoordinatorT]):
    def __init__(self, coordinator: _CoordinatorT):
        self.coordinator = coordinator


class MockDtUtil:
    @staticmethod
    def now(tz: Any = None) -> datetime:
        return datetime.now(tz or timezone.utc)

    @staticmethod
    def parse_datetime(dt_str: str) -> Optional[datetime]:
        try:
            return datetime.fromisoformat(dt_str)
        except Exception:
            return None

    @staticmethod
    def parse_date(d_str: str) -> Optional[date]:
        try:
            return date.fromisoformat(d_str)
        except Exception:
            return None

    @staticmethod
    def start_of_local_day(d: date) -> datetime:
        return datetime.combine(d, time.min, tzinfo=timezone.utc)


def register_mock_modules():
    """Create real module types and inject into sys.modules."""
    # homeassistant
    ha = types.ModuleType("homeassistant")

    # homeassistant.core
    core = types.ModuleType("homeassistant.core")
    core.HomeAssistant = MockHomeAssistant
    core.callback = lambda f: f
    ha.core = core

    # homeassistant.config_entries
    config_entries = types.ModuleType("homeassistant.config_entries")
    config_entries.ConfigEntry = MockConfigEntry
    config_entries.ConfigFlow = MockConfigFlow
    config_entries.OptionsFlow = MockOptionsFlow
    ha.config_entries = config_entries

    # homeassistant.data_entry_flow
    data_entry_flow = types.ModuleType("homeassistant.data_entry_flow")
    data_entry_flow.FlowResult = dict
    ha.data_entry_flow = data_entry_flow

    # homeassistant.components
    components = types.ModuleType("homeassistant.components")
    # homeassistant.components.calendar
    calendar = types.ModuleType("homeassistant.components.calendar")
    calendar.CalendarEntity = CalendarEntity
    calendar.CalendarEvent = CalendarEvent
    components.calendar = calendar
    ha.components = components

    # homeassistant.helpers
    helpers = types.ModuleType("homeassistant.helpers")
    aiohttp_client = types.ModuleType("homeassistant.helpers.aiohttp_client")
    aiohttp_client.async_get_clientsession = MagicMock(return_value=MagicMock())
    helpers.aiohttp_client = aiohttp_client

    device_registry = types.ModuleType("homeassistant.helpers.device_registry")
    device_registry.DeviceEntryType = DeviceEntryType
    device_registry.DeviceInfo = DeviceInfo
    helpers.device_registry = device_registry

    entity_platform = types.ModuleType("homeassistant.helpers.entity_platform")
    entity_platform.AddEntitiesCallback = Callable
    helpers.entity_platform = entity_platform

    update_coordinator = types.ModuleType("homeassistant.helpers.update_coordinator")
    update_coordinator.DataUpdateCoordinator = DataUpdateCoordinator
    update_coordinator.CoordinatorEntity = CoordinatorEntity
    update_coordinator.UpdateFailed = UpdateFailed
    helpers.update_coordinator = update_coordinator
    ha.helpers = helpers

    # homeassistant.util
    util = types.ModuleType("homeassistant.util")
    dt = MockDtUtil
    util.dt = dt
    ha.util = util

    # Inject into sys.modules
    sys.modules["homeassistant"] = ha
    sys.modules["homeassistant.core"] = core
    sys.modules["homeassistant.config_entries"] = config_entries
    sys.modules["homeassistant.data_entry_flow"] = data_entry_flow
    sys.modules["homeassistant.components"] = components
    sys.modules["homeassistant.components.calendar"] = calendar
    sys.modules["homeassistant.helpers"] = helpers
    sys.modules["homeassistant.helpers.aiohttp_client"] = aiohttp_client
    sys.modules["homeassistant.helpers.device_registry"] = device_registry
    sys.modules["homeassistant.helpers.entity_platform"] = entity_platform
    sys.modules["homeassistant.helpers.update_coordinator"] = update_coordinator
    sys.modules["homeassistant.util"] = util
    sys.modules["homeassistant.util.dt"] = dt


register_mock_modules()

