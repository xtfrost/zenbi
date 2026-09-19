"""Dataclasses for Zenbi API responses."""

from dataclasses import dataclass, field
from datetime import datetime
import html
import json
import re
from typing import Any, Dict, List, Optional


def parse_quill_delta(raw_text: str) -> str:
    """Parse Quill Delta JSON format into clean Markdown text with full Unicode/Danish support."""
    if not raw_text or not isinstance(raw_text, str):
        return ""
    stripped = raw_text.strip()
    if stripped.startswith("{") and "ops" in stripped:
        try:
            data = json.loads(stripped)
            if isinstance(data, dict) and isinstance(data.get("ops"), list):
                rendered_lines: List[str] = []
                current_line_parts: List[str] = []

                for op in data["ops"]:
                    insert_val = op.get("insert")
                    if not isinstance(insert_val, str):
                        continue

                    attrs = op.get("attributes") or {}

                    # Check if this op is a block-level formatting newline
                    if insert_val == "\n" and attrs:
                        line_content = "".join(current_line_parts)
                        if attrs.get("list") == "bullet":
                            rendered_lines.append(f"- {line_content}")
                        elif attrs.get("list") == "ordered":
                            rendered_lines.append(f"1. {line_content}")
                        elif attrs.get("header"):
                            try:
                                level = min(int(attrs.get("header", 1)), 6)
                            except (ValueError, TypeError):
                                level = 1
                            rendered_lines.append(f"{'#' * level} {line_content}")
                        else:
                            rendered_lines.append(line_content)
                        current_line_parts = []
                        continue

                    # Process text chunks which may contain embedded newlines
                    pieces = insert_val.split("\n")
                    for idx, piece in enumerate(pieces):
                        if idx > 0:
                            rendered_lines.append("".join(current_line_parts))
                            current_line_parts = []

                        if piece:
                            piece_text = piece
                            if piece_text.strip():
                                l_ws = len(piece_text) - len(piece_text.lstrip())
                                r_ws = len(piece_text) - len(piece_text.rstrip())
                                core = piece_text.strip()

                                if attrs.get("bold") and attrs.get("italic"):
                                    core = f"***{core}***"
                                elif attrs.get("bold"):
                                    core = f"**{core}**"
                                elif attrs.get("italic"):
                                    core = f"*{core}*"

                                if attrs.get("link"):
                                    core = f"[{core}]({attrs['link']})"

                                piece_text = (" " * l_ws) + core + (" " * r_ws)

                            current_line_parts.append(piece_text)

                if current_line_parts:
                    rendered_lines.append("".join(current_line_parts))

                combined = "\n".join(rendered_lines)
                combined = html.unescape(combined)
                # Normalize 3+ consecutive newlines into 2
                combined = re.sub(r"\n{3,}", "\n\n", combined).strip()
                return combined
        except Exception:
            pass

    return html.unescape(stripped)


@dataclass
class ZenbiAuthResponse:
    """Response payload from authentication endpoint."""

    user_id: str
    token: str
    refresh_token: Optional[str] = None
    change_password: bool = False

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ZenbiAuthResponse":
        return cls(
            user_id=data.get("userId", ""),
            token=data.get("token", ""),
            refresh_token=data.get("refreshToken"),
            change_password=data.get("changePassword", False),
        )


@dataclass
class ZenbiPlanningMeta:
    """Planning metadata attached to calendar items."""

    element_id: Optional[str] = None
    color: Optional[str] = None
    icon: Optional[str] = None
    worktime: bool = False

    @classmethod
    def from_dict(cls, data: Optional[Dict[str, Any]]) -> Optional["ZenbiPlanningMeta"]:
        if not data:
            return None
        return cls(
            element_id=data.get("elementId"),
            color=data.get("color"),
            icon=data.get("icon"),
            worktime=data.get("worktime", False),
        )


@dataclass
class ZenbiHomework:
    """Homework related to a calendar item."""

    id: str
    calendar_item_id: str
    description: str
    raw_description: str
    date: str
    files: List[Dict[str, Any]] = field(default_factory=list)
    updated_time: Optional[str] = None
    updated_by_user_id: Optional[str] = None
    made_by_user_id: Optional[str] = None
    raw_data: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ZenbiHomework":
        raw_desc = data.get("description") or ""
        clean_desc = parse_quill_delta(raw_desc)
        return cls(
            id=str(data.get("id", "")),
            calendar_item_id=str(data.get("calendarItemId", "")),
            description=clean_desc,
            raw_description=raw_desc,
            date=str(data.get("date", "")),
            files=data.get("files") or [],
            updated_time=data.get("updatedTime"),
            updated_by_user_id=data.get("updatedByUserId"),
            made_by_user_id=data.get("madeByUserId"),
            raw_data=data,
        )


@dataclass
class ZenbiWeeklySchedule:
    """Weekly schedule message / ugeplan from Zenbi."""

    id: str
    start: str
    end: str
    description: str
    raw_description: str
    title: str
    participant_ids: List[str] = field(default_factory=list)
    files: List[Dict[str, Any]] = field(default_factory=list)
    updated_time: Optional[str] = None
    updated_by_user_id: Optional[str] = None
    made_by_user_id: Optional[str] = None
    raw_data: Dict[str, Any] = field(default_factory=dict)
    # Parsed datetimes cached at construction time (compare=False to keep equality semantics)
    start_dt: Optional[datetime] = field(default=None, compare=False, repr=False)
    end_dt: Optional[datetime] = field(default=None, compare=False, repr=False)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ZenbiWeeklySchedule":
        raw_desc = data.get("description") or ""
        clean_desc = parse_quill_delta(raw_desc)
        files = data.get("files") or []

        # Derive a human-readable title from first line of text or file name
        title = ""
        if clean_desc:
            for line in clean_desc.splitlines():
                clean_line = line.strip().lstrip("#*- \t")
                if clean_line:
                    title = clean_line[:80]
                    break

        if not title and files:
            first_file = files[0]
            if isinstance(first_file, dict):
                title = first_file.get("name") or first_file.get("title") or ""

        if not title:
            title = "Weekly Message"

        def _parse_iso(s: str) -> Optional[datetime]:
            if not s:
                return None
            try:
                return datetime.fromisoformat(s)
            except (ValueError, TypeError):
                return None

        start_str = str(data.get("start", ""))
        end_str = str(data.get("end", ""))

        instance = cls(
            id=str(data.get("id", "")),
            start=start_str,
            end=end_str,
            description=clean_desc,
            raw_description=raw_desc,
            title=title,
            participant_ids=data.get("participantIds") or [],
            files=files,
            updated_time=data.get("updatedTime"),
            updated_by_user_id=data.get("updatedByUserId"),
            made_by_user_id=data.get("madeByUserId"),
            raw_data=data,
        )
        instance.start_dt = _parse_iso(start_str)
        instance.end_dt = _parse_iso(end_str)
        return instance


@dataclass
class ZenbiCalendarItem:
    """Timed schema calendar entry."""

    id: str
    title: str
    start: str  # ISO 8601 timestamp string
    end: str    # ISO 8601 timestamp string
    description: str = ""
    note: str = ""
    only_date: bool = False
    private: bool = False
    planning: Optional[ZenbiPlanningMeta] = None
    resources: List[Dict[str, Any]] = field(default_factory=list)
    substitutes: List[Dict[str, Any]] = field(default_factory=list)
    participant_models: List[Dict[str, Any]] = field(default_factory=list)
    homework: List[ZenbiHomework] = field(default_factory=list)
    raw_data: Dict[str, Any] = field(default_factory=dict)
    # Parsed datetimes cached at construction time (compare=False to keep equality semantics)
    start_dt: Optional[datetime] = field(default=None, compare=False, repr=False)
    end_dt: Optional[datetime] = field(default=None, compare=False, repr=False)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ZenbiCalendarItem":
        homework_raw = data.get("homework") or data.get("homeworks") or []
        homework_items = [
            ZenbiHomework.from_dict(hw) for hw in homework_raw if isinstance(hw, dict)
        ]

        raw_desc = data.get("description") or ""
        clean_desc = parse_quill_delta(raw_desc)

        start_str = data.get("start", "")
        end_str = data.get("end", "")

        def _parse_iso(s: str) -> Optional[datetime]:
            if not s:
                return None
            try:
                return datetime.fromisoformat(s)
            except (ValueError, TypeError):
                return None

        instance = cls(
            id=data.get("id", ""),
            title=data.get("title", ""),
            start=start_str,
            end=end_str,
            description=clean_desc,
            note=data.get("note") or "",
            only_date=data.get("onlyDate", False),
            private=data.get("private", False),
            planning=ZenbiPlanningMeta.from_dict(data.get("planning")),
            resources=data.get("resources") or [],
            substitutes=data.get("substitutes") or [],
            participant_models=data.get("participantModels") or [],
            homework=homework_items,
            raw_data=data,
        )
        instance.start_dt = _parse_iso(start_str)
        instance.end_dt = _parse_iso(end_str)
        return instance

    @property
    def student_names(self) -> List[str]:
        """Return participant student names for this calendar item."""
        names: List[str] = []
        for pm in self.participant_models:
            if isinstance(pm, dict):
                name = pm.get("name") or pm.get("fullName") or pm.get("title")
                if name and isinstance(name, str) and name.strip():
                    names.append(name.strip())
        return names


def extract_student_names(items: List[ZenbiCalendarItem]) -> List[str]:
    """Extract unique student names from calendar items."""
    names: List[str] = []
    seen: set[str] = set()
    for item in items:
        for name in item.student_names:
            if name not in seen:
                seen.add(name)
                names.append(name)
    return names


@dataclass
class ZenbiPlanningLabel:
    """All-day label or event from the planning module."""

    id: str
    title: str
    start_date: str  # ISO string or YYYY-MM-DD
    end_date: Optional[str] = None
    description: str = ""
    color: Optional[str] = None
    icon: Optional[str] = None
    raw_data: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ZenbiPlanningLabel":
        # Handle flexible key names in planning labels (e.g., name vs title, date vs start)
        title = data.get("title") or data.get("name") or data.get("label") or "All-day Event"
        start_date = (
            data.get("start")
            or data.get("startDate")
            or data.get("date")
            or data.get("timeframeStart")
            or ""
        )
        end_date = data.get("end") or data.get("endDate") or start_date

        raw_desc = data.get("description") or data.get("note") or ""
        clean_desc = parse_quill_delta(raw_desc)

        return cls(
            id=str(data.get("id") or data.get("guid") or hash(f"{title}_{start_date}")),
            title=title,
            start_date=start_date,
            end_date=end_date,
            description=clean_desc,
            color=data.get("color"),
            icon=data.get("icon"),
            raw_data=data,
        )


@dataclass
class ZenbiGlobalData:
    """Session context and timeframe info."""

    timeframe_id: Optional[str]
    raw_data: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ZenbiGlobalData":
        # Search common fields for timeframe ID in globaldata bootstrap
        timeframe_id = (
            data.get("timeframeId")
            or data.get("currentTimeframeId")
            or (data.get("timeframe") or {}).get("id")
            or (data.get("activeTimeframe") or {}).get("id")
        )
        # Fallback: search top-level keys if nested in lists or objects
        if not timeframe_id and isinstance(data.get("timeframes"), list) and len(data["timeframes"]) > 0:
            timeframe_id = data["timeframes"][0].get("id")

        return cls(
            timeframe_id=timeframe_id,
            raw_data=data,
        )
