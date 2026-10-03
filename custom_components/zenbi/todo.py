"""Todo platform for the Zenbi integration."""

from __future__ import annotations

import asyncio
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
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from .const import DOMAIN, STORAGE_KEY_TODO, STORAGE_VERSION, slugify_name
from .coordinator import ZenbiCalendarDataUpdateCoordinator, ZenbiHomework

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

    entities: List[ZenbiHomeworkTodoListEntity] = []

    students = coordinator.data.students if coordinator.data else []

    if students:
        for student in students:
            entities.append(
                ZenbiHomeworkTodoListEntity(coordinator, entry, student_name=student)
            )
    else:
        # Fallback if no students discovered
        entities.append(
            ZenbiHomeworkTodoListEntity(coordinator, entry, student_name=None)
        )

    async_add_entities(entities)


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
        student_name: Optional[str] = None,
    ) -> None:
        """Initialize the Zenbi homework todo list entity."""
        super().__init__(coordinator)
        self.entry = entry
        self._student_name = student_name
        self._attr_translation_key = "homework"

        if student_name:
            slug = slugify_name(student_name)
            self._attr_unique_id = f"{entry.entry_id}_{slug}_homework"
            self._storage_student_key = student_name
        else:
            self._attr_unique_id = f"{entry.entry_id}_homework"
            self._storage_student_key = "__default__"

        self._completed_ids: Set[str] = set()

        # Cache for calendar-item lookup
        if coordinator.data and coordinator.data.calendar_items:
            self._cal_by_id: Dict[str, Any] = {
                item.id: item for item in coordinator.data.calendar_items
            }
        else:
            self._cal_by_id = {}

        self._store: Optional[Store] = None
        if hasattr(coordinator, "hass") and coordinator.hass:
            self._store = Store(
                coordinator.hass,
                STORAGE_VERSION,
                STORAGE_KEY_TODO.format(entry_id=entry.entry_id),
            )
        self._store_lock = asyncio.Lock()
        self._cached_device_info: DeviceInfo = self._build_device_info()

    # ------------------------------------------------------------------
    # Device info (cached to avoid creating new objects on every read)
    # ------------------------------------------------------------------

    def _build_device_info(self) -> DeviceInfo:
        """Build a DeviceInfo object assigned to either student or school device."""
        student = self._student_name
        if student is None:
            if (
                self.coordinator.data
                and self.coordinator.data.students
                and len(self.coordinator.data.students) == 1
            ):
                student = self.coordinator.data.students[0]

        if student:
            slug = slugify_name(student)
            return DeviceInfo(
                identifiers={(DOMAIN, f"{self.entry.entry_id}_{slug}")},
                name=f"Zenbi ({student})",
                manufacturer="Zenbi",
                model="Zenbi Education Portal",
                entry_type=DeviceEntryType.SERVICE,
            )

        # Fallback / unassigned device (avoids using login credentials/email)
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
        """Rebuild internal caches when coordinator data changes."""
        if self.coordinator.data:
            cal_items = self.coordinator.data.calendar_items or []
            self._cal_by_id = {item.id: item for item in cal_items}
        else:
            self._cal_by_id = {}
        self._cached_device_info = self._build_device_info()
        super()._handle_coordinator_update()

    @property
    def device_info(self) -> DeviceInfo:
        """Return cached device information."""
        return self._cached_device_info

    # ------------------------------------------------------------------
    # Lifecycle & Storage
    # ------------------------------------------------------------------

    async def async_added_to_hass(self) -> None:
        """Load saved completed IDs when entity is added to Home Assistant."""
        await super().async_added_to_hass()
        self._cached_device_info = self._build_device_info()
        if self._store:
            try:
                data = await self._store.async_load()
                if data and isinstance(data, dict):
                    raw_completed = data.get("completed_ids")
                    loaded: Set[str] = set()

                    if isinstance(raw_completed, dict):
                        # Multi-student dictionary format
                        student_list = raw_completed.get(self._storage_student_key, [])
                        if isinstance(student_list, list):
                            loaded = set(student_list)
                    elif isinstance(raw_completed, list):
                        # Legacy flat list format
                        loaded = set(raw_completed)

                    if loaded != self._completed_ids:
                        self._completed_ids = loaded
                        self.async_write_ha_state()
            except Exception as err:
                _LOGGER.warning(
                    "Error loading persistent Zenbi homework state for %s: %s",
                    getattr(self, "entity_id", None) or self._attr_unique_id,
                    err,
                )

    # ------------------------------------------------------------------
    # Todo items
    # ------------------------------------------------------------------

    def _get_homeworks(self) -> List[ZenbiHomework]:
        """Get homework items scoped to this entity's student."""
        if not self.coordinator.data or not self.coordinator.data.homeworks:
            return []
        if self._student_name is not None:
            return self.coordinator.get_student_homeworks(self._student_name)
        # If no students are known, return all homework
        if not self.coordinator.data.students:
            return self.coordinator.data.homeworks
        return self.coordinator.get_student_homeworks(None)

    @property
    def todo_items(self) -> Optional[List[TodoItem]]:
        """Return the list of todo items from cached homework data."""
        homeworks = self._get_homeworks()
        if not homeworks:
            return []

        cal_by_id = self._cal_by_id
        items: List[TodoItem] = []

        for hw in homeworks:
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
                    file_links = []
                    for f in hw.files:
                        if isinstance(f, dict):
                            f_id = f.get("id") or f.get("fileId")
                            name = f.get("name") or f.get("title")
                            if name and f_id:
                                url = f"/api/zenbi/file/{self.entry.entry_id}/{f_id}"
                                file_links.append(f"{name} ({url})")
                            elif name:
                                file_links.append(name)
                    if file_links:
                        desc_parts.append(f"Files: {', '.join(filter(None, file_links))}")
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

        if self._store:
            try:
                async with self._store_lock:
                    if self._storage_student_key == "__default__":
                        # Single-student or default entity: write as flat list for backward compatibility
                        await self._store.async_save({"completed_ids": list(self._completed_ids)})
                    else:
                        # Multi-student dictionary format
                        data = await self._store.async_load() or {}
                        raw_completed = data.get("completed_ids")
                        completed_dict: Dict[str, List[str]] = {}

                        if isinstance(raw_completed, dict):
                            completed_dict = dict(raw_completed)
                        elif isinstance(raw_completed, list):
                            completed_dict["__default__"] = list(raw_completed)

                        completed_dict[self._storage_student_key] = list(self._completed_ids)
                        await self._store.async_save({"completed_ids": completed_dict})
            except Exception as err:
                _LOGGER.warning(
                    "Error saving persistent Zenbi homework state for %s: %s",
                    getattr(self, "entity_id", None) or self._attr_unique_id,
                    err,
                )
