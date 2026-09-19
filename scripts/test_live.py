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
            print("  [+] SKIPPED login request to /account/authenticate (ZERO security emails triggered!)")
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
                f_names = [f.get('name') or f.get('title') for f in hw.files if isinstance(f, dict)]
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
                res_names = [
                    r.get("name", "")
                    for r in item.resources
                    if isinstance(r, dict)
                ]
                print(f"          Location/Resources: {', '.join(filter(None, res_names))}")
            if item.note:
                print(f"          Note: {item.note}")
            if item.substitutes:
                sub_names = [
                    s.get("name", "")
                    for s in item.substitutes
                    if isinstance(s, dict)
                ]
                print(f"          Substitutes: {', '.join(filter(None, sub_names))}")
            if item.homework:
                for h in item.homework:
                    print(f"          -> Homework: {h.description}")
            if item.planning:
                print(
                    f"          Planning: color={item.planning.color}, icon={item.planning.icon}"
                )

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
                f_names = [f.get('name') or f.get('title') for f in ws.files if isinstance(f, dict)]
                print(f"          Attachments: {', '.join(filter(None, f_names))}")

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

    username = args.username or input("Zenbi Username: ").strip()
    password = args.password or getpass.getpass("Zenbi Password: ")

    if not username or not password:
        print("Error: Username and password are required.")
        sys.exit(1)

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
