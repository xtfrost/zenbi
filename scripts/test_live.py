"""Standalone test script to verify Zenbi API credentials and live data without Home Assistant."""

from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timedelta, timezone
import getpass
import json
from pathlib import Path
import sys
import time

# Ensure repository root is in sys.path
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# Load lightweight shims if running in an environment without Home Assistant installed
try:
    import homeassistant  # noqa: F401
except ImportError:
    from tests.conftest import register_mock_modules

    register_mock_modules()

from custom_components.zenbi.api.client import (
    ZenbiApiClient,
    format_zenbi_datetime,
    generate_stable_device_id,
    get_copenhagen_tz,
)
from custom_components.zenbi.api.exceptions import (
    ZenbiApiError,
    ZenbiAuthError,
    ZenbiConnectionError,
)
from custom_components.zenbi.api.models import extract_student_names
from custom_components.zenbi.const import slugify_name

SESSION_FILE = REPO_ROOT / ".zenbi_session.json"


def load_cached_session(username: str) -> tuple[str | None, str | None, float | None]:
    """Load cached token and device ID if valid and matching username."""
    if not SESSION_FILE.exists():
        return None, None, None
    try:
        with open(SESSION_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if data.get("username", "").lower() == username.lower():
            device_id = data.get("device_id")
            token = data.get("token")
            expiry = data.get("token_expiry")
            if token and expiry and time.time() < (float(expiry) - 120):
                return device_id, token, float(expiry)
            return device_id, None, None
    except Exception:
        pass
    return None, None, None


def save_cached_session(username: str, device_id: str, token: str, expiry: float | None) -> None:
    """Save active session to local cache to prevent repeated login emails."""
    try:
        data = {
            "username": username,
            "device_id": device_id,
            "token": token,
            "token_expiry": expiry,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        with open(SESSION_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
    except Exception:
        pass


def calculate_two_week_window():
    """Get rolling 2-week window (Monday this week to Monday 14 days later in Copenhagen time)."""
    cph_tz = get_copenhagen_tz()
    now = datetime.now(cph_tz)
    start_of_week = (now - timedelta(days=now.weekday())).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    end_of_window = (start_of_week + timedelta(days=14)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    return start_of_week, end_of_window


def _format_weekly_plan_text(schedules: list, entry_id: str = "entry_id") -> str:
    """Format plan text, merging multiple messages or listing attachments if text is empty."""
    with_desc = [s for s in schedules if s.description and s.description.strip()]

    file_links = []
    seen_keys = set()
    for s in schedules:
        if s.files:
            for f in s.files:
                if isinstance(f, dict):
                    f_id = f.get("id") or f.get("fileId")
                    name = f.get("name") or f.get("title") or "Vedhæftet fil"
                    key = f_id or name
                    if key not in seen_keys:
                        seen_keys.add(key)
                        if f_id:
                            file_links.append(
                                f'<a href="/api/zenbi/file/{entry_id}/{f_id}" target="_blank" download>{name}</a>'
                            )
                        else:
                            file_links.append(name)

    main_text = ""
    if len(with_desc) == 1:
        main_text = with_desc[0].description.strip()
    elif len(with_desc) > 1:
        sections: list[str] = []
        for s in with_desc:
            title = s.title or "Ugeplan"
            sections.append(f"# {title}\n\n{s.description.strip()}")
        main_text = "\n\n---\n\n".join(sections)

    if main_text:
        if file_links:
            file_list = "\n".join(f"- {link}" for link in file_links)
            return f"{main_text}\n\n### Vedhæftede filer\n{file_list}"
        return main_text

    # No messages have descriptions -> list files if any exist
    if file_links:
        file_list = "\n".join(f"- {link}" for link in file_links)
        return f"*Vedhæftede filer til denne uge:*\n{file_list}"

    return ""


def _get_schedule_dates(schedule) -> tuple[datetime.date | None, datetime.date | None]:
    start_d = None
    end_d = None
    if schedule.start_dt:
        start_d = schedule.start_dt.date()
    elif schedule.start:
        try:
            start_d = datetime.fromisoformat(schedule.start).date()
        except Exception:
            pass
    if schedule.end_dt:
        end_d = schedule.end_dt.date()
    elif schedule.end:
        try:
            end_d = datetime.fromisoformat(schedule.end).date()
        except Exception:
            pass
    return start_d, end_d


def _get_active_and_next_groups(schedules: list) -> tuple[list, list]:
    """Return all schedules grouped into the active week and next week."""
    if not schedules:
        return [], []

    def _sort_key(s) -> datetime:
        if s.start_dt:
            return s.start_dt
        if s.start:
            try:
                return datetime.fromisoformat(s.start)
            except Exception:
                pass
        return datetime.min.replace(tzinfo=timezone.utc)

    sorted_schedules = sorted(schedules, key=_sort_key)
    cph_tz = get_copenhagen_tz()
    today = datetime.now(cph_tz).date()

    # 1. Identify active week window
    active_window_start = None
    active_window_end = None

    for s in sorted_schedules:
        start_d, end_d = _get_schedule_dates(s)
        if start_d and end_d and start_d <= today <= end_d:
            active_window_start = start_d
            active_window_end = end_d
            break

    # If no schedule strictly covers today, pick the earliest upcoming
    if not active_window_start:
        for s in sorted_schedules:
            start_d, end_d = _get_schedule_dates(s)
            if start_d and start_d >= today:
                active_window_start = start_d
                active_window_end = end_d
                break

    # Fallback to latest schedule if all are in the past
    if not active_window_start and sorted_schedules:
        active_window_start, active_window_end = _get_schedule_dates(sorted_schedules[-1])

    active_schedules: list = []
    next_schedules: list = []
    next_window_start = None

    for s in sorted_schedules:
        start_d, end_d = _get_schedule_dates(s)
        if active_window_start and start_d == active_window_start:
            active_schedules.append(s)
        elif active_window_start and start_d and start_d > active_window_start:
            if next_window_start is None:
                next_window_start = start_d
            if start_d == next_window_start:
                next_schedules.append(s)

    return active_schedules, next_schedules


def print_weekly_plan_sensor_preview(
    entity_id: str,
    unique_id: str,
    weekly_schedules: list,
    entry_id: str = "entry_id",
) -> None:
    """Print Home Assistant preview for a weekly plan sensor."""
    print(f"\n  * ENTITY: {entity_id}")
    print("    Name         : Weekly Plan (Ugeplan)")
    print(f"    Unique ID    : {unique_id}")
    if not weekly_schedules:
        print("    State        : None (No weekly messages published)")
        return

    active_schedules, next_schedules = _get_active_and_next_groups(weekly_schedules)
    if not active_schedules:
        print("    State        : None (No active weekly schedule found)")
        return

    with_desc = [s for s in active_schedules if s.description and s.description.strip()]
    primary = with_desc[0] if with_desc else active_schedules[0]
    title = primary.title or "Weekly Plan"
    if len(active_schedules) > 1:
        title = f"{title} (+{len(active_schedules) - 1} mere)"

    print(f'    State        : "{title}"')

    plan_text = _format_weekly_plan_text(active_schedules, entry_id=entry_id)
    print("    Attribute 'current_week_plan':")
    if plan_text:
        lines = plan_text.splitlines()
        for line in lines[:10]:
            print(f"      | {line}")
        if len(lines) > 10:
            print(f"      | ... ({len(lines) - 10} more lines)")
    else:
        print("      | (empty)")

    files: list[dict] = []
    seen_keys = set()
    for s in active_schedules:
        if s.files:
            for f in s.files:
                if isinstance(f, dict):
                    f_id = f.get("id") or f.get("fileId")
                    name = f.get("name") or f.get("title")
                    key = f_id or name
                    if key and key not in seen_keys:
                        seen_keys.add(key)
                        item = {"name": name}
                        if f_id:
                            item["id"] = f_id
                            item["url"] = f"/api/zenbi/file/{entry_id}/{f_id}"
                        files.append(item)
    if files:
        print(f"    Attribute 'files': {files}")

    if next_schedules:
        with_desc_next = [s for s in next_schedules if s.description and s.description.strip()]
        primary_next = with_desc_next[0] if with_desc_next else next_schedules[0]
        nxt_title = primary_next.title or "Weekly Plan"
        print(f"    Attribute 'next_week_plan': (Title: {nxt_title})")
        nxt_plan_text = _format_weekly_plan_text(next_schedules, entry_id=entry_id)
        if nxt_plan_text:
            lines = nxt_plan_text.splitlines()
            for line in lines[:4]:
                print(f"      | {line}")
            if len(lines) > 4:
                print(f"      | ... ({len(lines) - 4} more lines)")


async def test_zenbi(
    username: str,
    password: str,
    device_id: str | None = None,
    no_cache: bool = False,
) -> None:
    """Connect to Zenbi and print retrieved live data."""
    print("=" * 60)
    print("ZENBI LIVE API TEST")
    print("=" * 60)
    print("Target Server : https://app.zenbi.dk")
    print(f"Username      : {username}")
    print("-" * 60)

    cached_device_id, cached_token, cached_expiry = (
        (None, None, None) if no_cache else load_cached_session(username)
    )

    final_device_id = device_id or cached_device_id or generate_stable_device_id(username)

    client = ZenbiApiClient(
        username=username,
        password=password,
        unique_device_id=final_device_id,
        token=cached_token,
        token_expiry=cached_expiry,
    )

    try:
        # 1. Authenticate / Check Session
        print("\n[1/6] Checking Zenbi authentication session...")
        if not client.is_token_expired():
            print("  [+] Active cached token found! Reusing existing session.")
            print(f"  [+] Device ID     : {client.unique_device_id}")
            print(f"  [+] Token preview : {client.token[:25]}...")
            print(
                "  [+] SKIPPED login request to /account/authenticate (ZERO security emails triggered!)"
            )
        else:
            print("  [*] Authenticating against Zenbi with persistent device ID...")
            auth = await client.authenticate()
            print("  [+] Authentication SUCCESSFUL!")
            print(f"  [+] User ID       : {auth.user_id}")
            print(f"  [+] Device ID     : {client.unique_device_id}")
            print(f"  [+] Token preview : {client.token[:25]}...")
            save_cached_session(
                username=username,
                device_id=client.unique_device_id,
                token=client.token,
                expiry=client.token_expiry,
            )
            print("  [+] Session cached locally to .zenbi_session.json for future runs.")

        # 2. Global Data / Session Context
        print("\n[2/6] Fetching Global Data bootstrap...")
        global_data = await client.get_global_data()
        print(f"  [+] Timeframe ID  : {global_data.timeframe_id or 'None found'}")

        # 3. Calendar items
        start_dt, end_dt = calculate_two_week_window()
        print(
            f"\n[3/6] Fetching Calendar items ({format_zenbi_datetime(start_dt)} to {format_zenbi_datetime(end_dt)})..."
        )
        items = await client.get_calendar_items(start_dt, end_dt)
        print(f"  [+] Found {len(items)} calendar item(s):")
        if not items:
            print("      (No schedule items found in this 2-week window)")

        # 4. Homework
        print(
            f"\n[4/6] Fetching Homework ({format_zenbi_datetime(start_dt)} to {format_zenbi_datetime(end_dt)})..."
        )
        homeworks = await client.get_homework(start_dt, end_dt)
        print(f"  [+] Found {len(homeworks)} homework assignment(s):")
        hw_by_cal_id = {}
        for hw in homeworks:
            if hw.calendar_item_id:
                hw_by_cal_id.setdefault(hw.calendar_item_id, []).append(hw)

        for idx, hw in enumerate(homeworks, start=1):
            print(f"      [{idx}] For item {hw.calendar_item_id} (Date: {hw.date}):")
            print(f"          Description: {hw.description}")
            if hw.files:
                f_names = [f.get("name") or f.get("title") for f in hw.files if isinstance(f, dict)]
                print(f"          Files: {', '.join(filter(None, f_names))}")

        # Link homework to calendar items for display
        for item in items:
            if item.id in hw_by_cal_id:
                item.homework = hw_by_cal_id[item.id]

        print("\n  Schedule item breakdown with homework:")
        for idx, item in enumerate(items, start=1):
            hw_tag = f" [HAS {len(item.homework)} HOMEWORK]" if item.homework else ""
            print(f"      [{idx}] {item.start} - {item.end}: {item.title}{hw_tag}")
            if item.resources:
                res_names = [r.get("name", "") for r in item.resources if isinstance(r, dict)]
                print(f"          Location/Resources: {', '.join(filter(None, res_names))}")
            if item.note:
                print(f"          Note: {item.note}")
            if item.substitutes:
                sub_names = [s.get("name", "") for s in item.substitutes if isinstance(s, dict)]
                print(f"          Substitutes: {', '.join(filter(None, sub_names))}")
            if item.homework:
                for h in item.homework:
                    print(f"          -> Homework: {h.description}")
            if item.planning:
                print(f"          Planning: color={item.planning.color}, icon={item.planning.icon}")

        # 5. Weekly Messages / Ugeplaner
        print(
            f"\n[5/6] Fetching Weekly Messages ({format_zenbi_datetime(start_dt)} to {format_zenbi_datetime(end_dt)})..."
        )
        weekly_schedules = await client.get_weekly_schedules(start_dt, end_dt)
        print(f"  [+] Found {len(weekly_schedules)} weekly message(s):")
        if not weekly_schedules:
            print("      (No weekly messages found in this 2-week window)")
        for idx, ws in enumerate(weekly_schedules, start=1):
            print(f"      [{idx}] {ws.start} to {ws.end} | Title: {ws.title}")
            if ws.description:
                preview_lines = [l for l in ws.description.splitlines() if l.strip()][:3]
                preview_text = " | ".join(preview_lines)
                print(f"          Preview: {preview_text[:120]}...")
            if ws.files:
                f_names = [f.get("name") or f.get("title") for f in ws.files if isinstance(f, dict)]
                print(f"          Attachments: {', '.join(filter(None, f_names))}")
                first_file = next(
                    (
                        f
                        for f in ws.files
                        if isinstance(f, dict) and (f.get("id") or f.get("fileId"))
                    ),
                    None,
                )
                if first_file and idx == 1:
                    test_fid = first_file.get("id") or first_file.get("fileId")
                    try:
                        dl_url = await client.get_weekly_schedule_file_download_url(test_fid)
                        print(f"          [OK] Verified File Download SAS URL: {dl_url[:75]}...")
                    except Exception as err:
                        print(f"          [!] File download test failed: {err}")

        # 6. Planning labels
        print("\n[6/6] Fetching Planning labels (all-day events)...")
        labels = await client.get_planning_labels(global_data.timeframe_id)
        print(f"  [+] Found {len(labels)} planning label(s):")
        if not labels:
            print("      (No planning labels found - school has not posted årsplan labels)")
        for idx, label in enumerate(labels, start=1):
            date_info = label.start_date
            if label.end_date and label.end_date != label.start_date:
                date_info += f" to {label.end_date}"
            print(f"      [{idx}] {date_info}: {label.title}")
            if label.description:
                print(f"          Description: {label.description}")

        # 7. Home Assistant Device & Entity Preview (Phase 5 Architecture)
        print("\n" + "=" * 60)
        print("HOME ASSISTANT DEVICE & ENTITY PREVIEW")
        print("=" * 60)

        students = extract_student_names(items)
        cal_by_id = {item.id: item for item in items}

        if not students:
            print("\n  [!] No students explicitly tagged in timetable participant models.")
            print(
                "      Home Assistant will provision generic entities (no username in names/IDs)."
            )

            print("\n" + "-" * 60)
            print("DEVICE: Zenbi")
            print("  Identifier : ('zenbi', 'entry_id')")
            print("  Role       : General Device")
            print("-" * 60)

            # 1. Schedule Calendar
            print("\n  * ENTITY: calendar.zenbi_schedule")
            print("    Name         : Schedule (Skema)")
            print("    Unique ID    : entry_id_schedule")
            print(f"    Classes      : {len(items)} class(es) in rolling 2-week window")
            for c_idx, c_item in enumerate(items[:5], start=1):
                hw_count = len(c_item.homework) if c_item.homework else 0
                hw_note = f" [has {hw_count} homework]" if hw_count else ""
                print(
                    f"      - {c_item.start[:16]} to {c_item.end[11:16]} : {c_item.title}{hw_note}"
                )
            if len(items) > 5:
                print(f"      ... and {len(items) - 5} more classes")

            # 2. Homework Todo List
            print("\n  * ENTITY: todo.zenbi_homework")
            print("    Name         : Homework (Lektier)")
            print("    Unique ID    : entry_id_homework")
            print(f"    Assignments  : {len(homeworks)} task(s)")
            for h_idx, hw in enumerate(homeworks, start=1):
                cal_it = cal_by_id.get(hw.calendar_item_id)
                subj = cal_it.title if cal_it else "Homework"
                first_line = hw.description.splitlines()[0][:60] if hw.description else ""
                summary = f"{subj}: {first_line}" if first_line else subj
                print(f"      [{h_idx}] {summary} (Due: {hw.date or 'No date'})")
                if hw.description:
                    desc_lines = [l for l in hw.description.splitlines() if l.strip()][:3]
                    for dl in desc_lines:
                        print(f"          {dl}")
                if hw.files:
                    f_names = [
                        f.get("name") or f.get("title") for f in hw.files if isinstance(f, dict)
                    ]
                    print(f"          Files: {', '.join(filter(None, f_names))}")

            # 3. Weekly Plan Sensor
            print_weekly_plan_sensor_preview(
                "sensor.zenbi_weekly_plan",
                "entry_id_weekly_plan",
                weekly_schedules,
            )

            # 4. Planning Calendar
            print("\n  * ENTITY: calendar.zenbi_planning")
            print("    Name         : Planning (Årsplan)")
            print("    Unique ID    : entry_id_planning")
            print(f"    Labels       : {len(labels)} milestone(s)/holiday(s)")
            for l_idx, lbl in enumerate(labels[:5], start=1):
                print(f"      - {lbl.start_date} to {lbl.end_date or lbl.start_date}: {lbl.title}")
            if len(labels) > 5:
                print(f"      ... and {len(labels) - 5} more labels")

            # 5. Weekly Messages Calendar
            print("\n  * ENTITY: calendar.zenbi_weekly_messages")
            print("    Name         : Weekly Messages (Ugebreve)")
            print("    Unique ID    : entry_id_weekly_messages")
            print(f"    Messages     : {len(weekly_schedules)} weekly letter(s)")
            for w_idx, ws in enumerate(weekly_schedules[:3], start=1):
                print(f"      - {ws.start} to {ws.end}: {ws.title}")

        else:
            print(f"\n  [+] Discovered {len(students)} student(s): {', '.join(students)}")

            for s_idx, student in enumerate(students, start=1):
                slug = slugify_name(student)
                print("\n" + "-" * 60)
                print(f"DEVICE: Zenbi ({student})")
                print(f"  Identifier : ('zenbi', 'entry_id_{slug}')")
                print("  Role       : Student Device")
                print("-" * 60)

                # 1. Schedule Calendar
                student_items = [it for it in items if student in it.student_names]
                print(f"\n  * ENTITY: calendar.zenbi_{slug}_schedule")
                print("    Name         : Schedule (Skema)")
                print(f"    Unique ID    : entry_id_{slug}_schedule")
                print(f"    Classes      : {len(student_items)} class(es) in rolling 2-week window")
                for c_idx, c_item in enumerate(student_items[:5], start=1):
                    hw_count = len(c_item.homework) if c_item.homework else 0
                    hw_note = f" [has {hw_count} homework]" if hw_count else ""
                    print(
                        f"      - {c_item.start[:16]} to {c_item.end[11:16]} : {c_item.title}{hw_note}"
                    )
                if len(student_items) > 5:
                    print(f"      ... and {len(student_items) - 5} more classes")

                # 2. Homework Todo List
                student_hws = []
                for hw in homeworks:
                    cal_it = cal_by_id.get(hw.calendar_item_id)
                    if cal_it and student in cal_it.student_names:
                        student_hws.append((hw, cal_it))

                print(f"\n  * ENTITY: todo.zenbi_{slug}_homework")
                print("    Name         : Homework (Lektier)")
                print(f"    Unique ID    : entry_id_{slug}_homework")
                print(f"    Assignments  : {len(student_hws)} task(s)")
                for h_idx, (hw, cal_it) in enumerate(student_hws, start=1):
                    subj = cal_it.title if cal_it else "Homework"
                    first_line = hw.description.splitlines()[0][:60] if hw.description else ""
                    summary = f"{subj}: {first_line}" if first_line else subj
                    print(f"      [{h_idx}] {summary} (Due: {hw.date or 'No date'})")
                    if hw.description:
                        desc_lines = [l for l in hw.description.splitlines() if l.strip()][:3]
                        for dl in desc_lines:
                            print(f"          {dl}")
                    if hw.files:
                        f_names = [
                            f.get("name") or f.get("title") for f in hw.files if isinstance(f, dict)
                        ]
                        print(f"          Files: {', '.join(filter(None, f_names))}")

                # 3. Weekly Plan Sensor
                print_weekly_plan_sensor_preview(
                    f"sensor.zenbi_{slug}_weekly_plan",
                    f"entry_id_{slug}_weekly_plan",
                    weekly_schedules,
                )

            # Shared School Device
            print("\n" + "-" * 60)
            print("DEVICE: Zenbi (School)")
            print("  Identifier : ('zenbi', 'entry_id_school')")
            print("  Role       : Shared School Device")
            print("-" * 60)

            # 1. Planning Calendar
            print("\n  * ENTITY: calendar.zenbi_planning")
            print("    Name         : Planning (Årsplan)")
            print("    Unique ID    : entry_id_planning")
            print(f"    Labels       : {len(labels)} milestone(s)/holiday(s)")
            for l_idx, lbl in enumerate(labels[:5], start=1):
                print(f"      - {lbl.start_date} to {lbl.end_date or lbl.start_date}: {lbl.title}")
            if len(labels) > 5:
                print(f"      ... and {len(labels) - 5} more labels")

            # 2. Weekly Messages Calendar
            print("\n  * ENTITY: calendar.zenbi_weekly_messages")
            print("    Name         : Weekly Messages (Ugebreve)")
            print("    Unique ID    : entry_id_weekly_messages")
            print(f"    Messages     : {len(weekly_schedules)} weekly letter(s)")
            for w_idx, ws in enumerate(weekly_schedules[:3], start=1):
                print(f"      - {ws.start} to {ws.end}: {ws.title}")

            # 3. Unassigned classes (if any)
            unassigned_items = [it for it in items if not it.student_names]
            if unassigned_items:
                print("\n  * ENTITY: calendar.zenbi_school_schedule")
                print("    Name         : School Schedule (Fællesskema)")
                print("    Unique ID    : entry_id_school_schedule")
                print(
                    f"    Classes      : {len(unassigned_items)} unassigned / school-wide class(es)"
                )

        print("\n" + "=" * 60)
        print("ALL CHECKS PASSED: Your Zenbi credentials and API endpoints are working!")
        print("=" * 60)

    except ZenbiAuthError as err:
        print(f"\n[ERROR] Authentication failed: {err}")
        print("Please check your username and password.")
    except ZenbiConnectionError as err:
        print(f"\n[ERROR] Connection error: {err}")
        print("Check your network connection and verify https://app.zenbi.dk is reachable.")
    except ZenbiApiError as err:
        print(f"\n[ERROR] API error: {err}")
    except Exception as err:
        print(f"\n[ERROR] Unexpected error: {err}")
    finally:
        await client.close()


def main():
    parser = argparse.ArgumentParser(
        description="Test Zenbi API connection and data fetching without Home Assistant."
    )
    parser.add_argument("-u", "--username", help="Zenbi username / email")
    parser.add_argument("-p", "--password", help="Zenbi password")
    parser.add_argument(
        "-d",
        "--device-id",
        help="Optional explicit uniqueDeviceId (e.g. from browser localStorage)",
    )
    parser.add_argument(
        "--no-cache",
        action="store_true",
        help="Ignore cached session and force re-authenticating",
    )
    args = parser.parse_args()

    username = args.username
    password = args.password

    cached_device_id, cached_token, cached_expiry = (
        (None, None, None) if args.no_cache or not username else load_cached_session(username)
    )

    if not username:
        # Check if session file has a default username
        if not args.no_cache and SESSION_FILE.exists():
            try:
                with open(SESSION_FILE, "r", encoding="utf-8") as f:
                    u_cached = json.load(f).get("username")
                    if u_cached:
                        username = u_cached
                        cached_device_id, cached_token, cached_expiry = load_cached_session(
                            username
                        )
            except Exception:
                pass
        if not username:
            username = input("Zenbi Username: ").strip()
            if not args.no_cache:
                cached_device_id, cached_token, cached_expiry = load_cached_session(username)

    if not password and not cached_token:
        password = getpass.getpass("Zenbi Password: ")

    if not username or (not password and not cached_token):
        print("Error: Username and password are required.")
        sys.exit(1)

    password = password or ""

    asyncio.run(
        test_zenbi(
            username=username,
            password=password,
            device_id=args.device_id,
            no_cache=args.no_cache,
        )
    )


if __name__ == "__main__":
    main()
