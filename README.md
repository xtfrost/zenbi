# Zenbi Home Assistant Integration

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=xtfrost&repository=zenbi&category=integration)
[![Open in HACS](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=xtfrost&repository=zenbi&category=integration)
[![GitHub release](https://img.shields.io/github/v/release/xtfrost/zenbi?include_prereleases&style=flat-square)](https://github.com/xtfrost/zenbi/releases)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

A custom [Home Assistant](https://www.home-assistant.io/) integration for the Danish school platform **Zenbi** ([app.zenbi.dk](https://app.zenbi.dk)).

This integration brings school schedules, homework tasks, weekly letters ("ugebreve"), and annual planning directly into Home Assistant calendars and dashboards.

> [!WARNING]
> **Disclaimer**: This integration is an independent open-source project and is **not affiliated with, endorsed by, or officially associated with Zenbi**. It communicates with Zenbi using reverse-engineered web client endpoints. Because these are unofficial internal APIs, they may change, break, or be updated at any time without notice.

---

## Features

- 📅 **Timed Class Schedule (`calendar.zenbi_schedule` / `Skema`)**:
  - Displays daily timed classes with start/end times, subjects, and teacher notes.
  - Automatically correlates and includes **homework** for each subject.
  - Provides structured attributes (`agenda_today`, `classes_today`) for automations and daily briefings.
- 📬 **Weekly Messages (`calendar.zenbi_weekly_messages` / `Ugebreve`)**:
  - Weekly class letters and schedules ("Ugeplaner") represented as 7-day all-day events.
  - Automatically parses text preview and lists attached file names.
- 🗓️ **Annual Planning (`calendar.zenbi_planning` / `Årsplan`)**:
  - School holidays, milestones, and all-day semester events.
  - *Note:* Automatically defaults to disabled in the entity registry if your school does not publish annual planning labels.
- 📝 **Native Homework Todo Platform (`todo.zenbi_homework` / `Lektier`)**:
  - Dedicated Home Assistant Todo checklist for school assignments.
  - Shows subject, homework preview, full markdown description, attachment filenames, and due dates.
  - Interactively check off tasks directly from your Lovelace dashboard.
- 🇩🇰 **Full Danish Localization**:
  - UI configuration, options dialogs, and entity names natively support Danish (`da`) and English (`en`).

---

## Installation

### Method 1: HACS (Recommended 1-Click)

Click the button below to open your Home Assistant instance and automatically add the repository:

[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=xtfrost&repository=zenbi&category=integration)

*(Or manually add `https://github.com/xtfrost/zenbi` under **HACS** > **Integrations** > Three dots menu > **Custom repositories** > Category: **Integration**).*

### Method 2: Manual Installation
1. Download the latest release (or clone this repository).
2. Copy the `custom_components/zenbi` folder into your Home Assistant directory under:
   ```text
   <config_dir>/custom_components/zenbi/
   ```
3. Restart Home Assistant.

---

## Configuration

1. In Home Assistant, navigate to **Settings** > **Devices & Services**.
2. Click **Add Integration** and search for **Zenbi**.
3. Fill in your Zenbi login credentials:
   - **Username / Email**: Your Zenbi username (e.g., `parent@example.dk`).
   - **Password**: Your Zenbi account password.
   - **Device ID (Optional)**: If you want Zenbi to treat Home Assistant as your already-trusted desktop browser, copy your `uniqueDeviceId` from your browser's DevTools (`F12` > Application > Local Storage > `https://app.zenbi.dk`). Leaving this blank will automatically generate a stable, deterministic device ID.
4. Click **Submit**.

### Integration Options
To adjust update intervals:
1. Go to **Settings** > **Devices & Services** > **Zenbi**.
2. Click **Configure**.
3. Customize:
   - **Calendar Sync Interval (hours)**: Default is `6` hours (supports `1` to `168` hours).

---

## Multi-Student Architecture

When an account is connected, Zenbi automatically discovers all enrolled children and provisions dedicated devices:
- **Student Device (`Zenbi ({Student Name})`)**: Contains that student's Schedule Calendar, Homework Todo List, and Weekly Plan Markdown Sensor.
- **School Device (`Zenbi (School)`)**: Contains the school-wide Planning Calendar (holidays/terms), full Weekly Messages Archive Calendar, and any unassigned events.

---

## Entities Provided

| Entity ID | Default Name (DA / EN) | Device | Description |
| :--- | :--- | :--- | :--- |
| `calendar.zenbi_{student}_schedule` | Skema / Schedule | Student | Timed school timetable, subjects, teacher notes, and homework. |
| `todo.zenbi_{student}_homework` | Lektier / Homework | Student | Dedicated checklist of homework tasks with due dates, previews, and completion toggles. |
| `sensor.zenbi_{student}_weekly_plan` | Ugeplan / Weekly Plan | Student | Active weekly plan (state = message count) with formatted Markdown `content` and `files` attributes. |
| `sensor.zenbi_{student}_next_weekly_plan` | Ugeplan næste uge / Next Weekly Plan | Student | Next week's plan (state = message count, `0` until published) with formatted Markdown `content` and `files` attributes. |
| `calendar.zenbi_planning` | Årsplan / Planning | School | All-day school semester milestones, term dates, and holidays. |
| `calendar.zenbi_weekly_messages` | Ugebreve / Weekly Messages | School | 7-day all-day events containing weekly teacher letters and attachment lists. |
| `sensor.zenbi_last_synced` | Sidst synkroniseret / Last Synced | School | Diagnostic timestamp sensor tracking last successful sync, status, and error details. |

---

## Automation & Dashboard Examples

Ready-to-use YAML examples and dashboard card configurations are provided in the [`examples/`](examples/) directory:

- [**School Overview Dashboard Cards**](examples/dashboards/school_dashboard_card.yaml): Complete Lovelace vertical stack featuring a daily timetable with homework indicators, current & next week's plans (with automatic conditional visibility), and an interactive homework checklist.
- [**Notify on New Homework**](examples/automations/notify_new_homework.yaml): Push notification alerting parents when new homework is assigned or updated with summaries and due dates.
- [**Notify on New Weekly Plan**](examples/automations/notify_new_weekly_plan.yaml): Alert when next week's plan or a new weekly plan message is posted by teachers.
- [**Morning School Briefing**](examples/automations/morning_schedule_briefing.yaml): Automated morning push notification or TTS announcement detailing today's lessons and homework due (automatically skips weekends and holidays).

> [!TIP]
> If you have multiple children enrolled in Zenbi, their entities will automatically include their name slug (e.g. `calendar.zenbi_albert_schedule`, `todo.zenbi_albert_homework`, `sensor.zenbi_albert_weekly_plan`, and `sensor.zenbi_albert_next_weekly_plan`). Simply substitute the entity ID corresponding to each child.

## Standalone Diagnostic Script (`scripts/test_live.py`)

You can test your Zenbi credentials and preview API payloads without running Home Assistant:

```bash
# Run interactive live verification
python scripts/test_live.py

# Or supply credentials via flags
python scripts/test_live.py -u "student@school.dk" -p "secret"

# Use your desktop browser's existing device ID
python scripts/test_live.py -u "student@school.dk" -p "secret" -d "YOUR-BROWSER-UUID"
```

### Session Caching in the Script
`test_live.py` automatically caches active session tokens in `.zenbi_session.json` (ignored by git). Subsequent runs will reuse the valid JWT token until expiration, **triggering zero login security emails**.

---

## Development & Automated Testing

The integration includes a test suite covering authentication, token decoding, API endpoints, rolling window calculations, Danish character parsing, coordinator error resilience, diagnostics redaction, and config flows:

```bash
# Install testing dependencies
pip install pytest pytest-asyncio aiohttp voluptuous tzdata

# Run tests
python -m pytest tests/ -v
```

---

## Architecture Overview

```text
custom_components/zenbi/
├── __init__.py           # Entry setup/teardown and platform forwarding
├── manifest.json         # Component metadata (domain, version, dependencies)
├── const.py              # Constants, endpoints, defaults, and User-Agent
├── coordinator.py        # DataUpdateCoordinator with 14-day rolling window & asyncio.gather
├── calendar.py           # CalendarEntity implementations (Schedule, Planning, WeeklyMessages)
├── todo.py               # TodoListEntity implementation (Homework / Lektier)
├── config_flow.py        # UI config flow & reauth modal
├── options_flow.py       # UI options flow (sync intervals)
├── diagnostics.py        # Sensitive credential redaction and diagnostics export
├── strings.json          # Translatable string keys
├── translations/
│   ├── da.json           # Danish localization
│   └── en.json           # English localization
└── api/
    ├── __init__.py       # API package exports
    ├── client.py         # Async HTTP client with JWT auto-refresh and stable device IDs
    ├── exceptions.py     # ZenbiApiError, ZenbiAuthError, ZenbiConnectionError
    └── models.py         # Dataclasses & Quill Delta to Markdown parser
```

---

## License

This project is licensed under the [MIT License](LICENSE).

