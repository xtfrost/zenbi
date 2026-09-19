"""Todo platform for the Zenbi integration."""

from __future__ import annotations

from datetime import date, datetime
import logging
from typing import Any, Dict, List, Optional, Set

from homeassistant.components.todo import (
    TodoItem,
    TodoItemStatus,
    TodoListEntity,
    TodoListEntityFeature,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from .const import DOMAIN
from .coordinator import ZenbiCalendarDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Zenbi todo entities based on a config entry."""
    coordinator: ZenbiCalendarDataUpdateCoordinator = (
        getattr(entry, "runtime_data", None) or hass.data[DOMAIN][entry.entry_id]
    )

    async_add_entities([ZenbiHomeworkTodoListEntity(coordinator, entry)])


class ZenbiHomeworkTodoListEntity(
    CoordinatorEntity[ZenbiCalendarDataUpdateCoordinator], TodoListEntity
):
    """Todo list entity for Zenbi homework items."""

    _attr_has_entity_name = True
    _attr_supported_features = TodoListEntityFeature.UPDATE_TODO_ITEM

    def __init__(
        self,
        coordinator: ZenbiCalendarDataUpdateCoordinator,
        entry: ConfigEntry,
    ) -> None:
        """Initialize the Zenbi homework todo list entity."""
        super().__init__(coordinator)
        self.entry = entry
        self._attr_translation_key = "homework"
        self._attr_unique_id = f"{entry.entry_id}_homework"
        self._completed_ids: Set[str] = set()

    @property
    def device_info(self) -> DeviceInfo:
        """Return device information to link entities together."""
        title = self.entry.title
        if (
            self.coordinator.data
            and self.coordinator.data.students
            and len(self.coordinator.data.students) == 1
        ):
            title = self.coordinator.data.students[0]
        return DeviceInfo(
            identifiers={(DOMAIN, self.entry.entry_id)},
            name=f"Zenbi ({title})",
            manufacturer="Zenbi",
            model="Zenbi Education Portal",
            entry_type=DeviceEntryType.SERVICE,
        )

    @property
    def todo_items(self) -> Optional[List[TodoItem]]:
        """Return the list of todo items from cached homework data."""
        if not self.coordinator.data or not self.coordinator.data.homeworks:
            return []

        cal_items = self.coordinator.data.calendar_items or []
        cal_by_id = {item.id: item for item in cal_items}

        items: List[TodoItem] = []
        for hw in self.coordinator.data.homeworks:
            try:
                cal_item = cal_by_id.get(hw.calendar_item_id)
                subject = cal_item.title if cal_item else None

                desc = hw.description or ""
                first_line = ""
                if desc:
                    for line in desc.splitlines():
                        clean = line.strip().lstrip("#*- \t")
                        if clean:
                            first_line = clean[:100]
                            break

                if subject and first_line:
                    summary = f"{subject}: {first_line}"
                elif subject:
                    summary = subject
                elif first_line:
                    summary = first_line
                else:
                    summary = "Homework"

                desc_parts: List[str] = []
                if desc:
                    desc_parts.append(desc)
                if hw.files:
                    file_names = [
                        f.get("name") or f.get("title")
                        for f in hw.files
                        if isinstance(f, dict) and (f.get("name") or f.get("title"))
                    ]
                    if file_names:
                        desc_parts.append(f"Files: {', '.join(filter(None, file_names))}")
                full_description = "\n\n".join(desc_parts) if desc_parts else None

                due: Optional[date | datetime] = None
                if hw.date:
                    parsed_dt = dt_util.parse_datetime(hw.date)
                    if parsed_dt:
                        due = parsed_dt.date()
                    else:
                        due = dt_util.parse_date(hw.date)
                if not due and cal_item and cal_item.start:
                    parsed_cal_dt = dt_util.parse_datetime(cal_item.start)
                    if parsed_cal_dt:
                        due = parsed_cal_dt.date()

                status = (
                    TodoItemStatus.COMPLETED
                    if hw.id in self._completed_ids
                    else TodoItemStatus.NEEDS_ACTION
                )

                items.append(
                    TodoItem(
                        summary=summary,
                        uid=str(hw.id),
                        status=status,
                        due=due,
                        description=full_description,
                    )
                )
            except Exception as err:
                _LOGGER.warning("Error parsing homework item %s: %s", getattr(hw, "id", "unknown"), err)

        return items

    async def async_get_todo_items(self) -> List[TodoItem]:
        """Return the list of todo items."""
        return self.todo_items or []

    async def async_update_todo_item(self, item: TodoItem) -> None:
        """Update a todo item (e.g. check/uncheck status)."""
        if not item.uid:
            return
        if item.status == TodoItemStatus.COMPLETED:
            self._completed_ids.add(item.uid)
        elif item.status == TodoItemStatus.NEEDS_ACTION:
            self._completed_ids.discard(item.uid)
        self.async_write_ha_state()

