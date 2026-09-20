# Zenbi Home Assistant Integration

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://github.com/hacs/default)
[![GitHub release](https://img.shields.io/github/v/release/custom-components/zenbi?include_prereleases&style=flat-square)](https://github.com/custom-components/zenbi/releases)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

A custom [Home Assistant](https://www.home-assistant.io/) integration for the Danish school platform **Zenbi** ([app.zenbi.dk](https://app.zenbi.dk)).

This integration brings school schedules, homework tasks, weekly letters ("ugebreve"), and annual planning directly into Home Assistant calendars and dashboards.

> [!WARNING]
> **Disclaimer**: This integration is an independent open-source project and is **not affiliated with, endorsed by, or officially associated with Zenbi or Zenbi ApS**. It communicates with Zenbi using reverse-engineered web client endpoints. Because these are unofficial internal APIs, they may change, break, or be updated at any time without notice.

---

## Features

- 📅 **Timed Class Schedule (`calendar.zenbi_schedule` / `Skema`)**:
  - Displays daily timed classes with start/end times and subjects.
  - Automatically correlates and includes **homework** for each subject.
  - Shows classroom resources, substitute teachers, notes, and planning markers.
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
- 🔤 **Markdown & Quill Delta Parsing**:
  - Converts Zenbi's internal Quill Delta rich-text JSON into clean GitHub-flavored Markdown.
  - Full native support for Danish special characters (`æ`, `ø`, `å`, `Æ`, `Ø`, `Å`), bullet lists (`- item`), numbered lists, links, and bold/italic styles.
- 🔒 **Persistent Device ID & Anti-Spam Login Protection**:
  - Uses a deterministic RFC 4122 UUID per account to prevent Zenbi's security system from flagging each sync as a "new browser login".
  - Optionally allows cloning your desktop browser's existing device ID from `localStorage`.
- ⚡ **Asynchronous & Concurrent**:
  - Fully asynchronous client powered by `asyncio.gather` for parallelized endpoint querying.
  - Error isolation: Non-critical endpoint failures never break the primary school timetable.
  - Zero memory leaks: Clean replacement of state data on every sync cycle.
- 🔄 **Re-Authentication Flow**:
  - If school credentials expire or change, Home Assistant prompts for an updated password via the UI without requiring re-configuration.
- 🩺 **Diagnostics Platform**:
  - Native Home Assistant diagnostics support with automatic redaction of passwords, tokens, and device identifiers.
- 🧹 **Lifecycle Safety & Storage Integrity**:
  - Persistent completed homework states saved atomically via Home Assistant's native `Store` helper (`.storage/zenbi.<entry_id>`).
  - Automated cleanup in `async_remove_entry` purging storage files upon uninstallation.
  - Standalone entity provisioning with zero injection or pollution of native `local_calendar` or `local_todo`.
  - Safe unloading preserving shared `aiohttp` connections while cleanly terminating coordinator update timers.

---

## Installation

### Method 1: Manual Installation
1. Download the latest release (or clone this repository).
2. Copy the `custom_components/zenbi` folder into your Home Assistant directory under:
   ```text
   <config_dir>/custom_components/zenbi/
   ```
3. Restart Home Assistant.

### Method 2: HACS (Custom Repository)
1. In Home Assistant, open **HACS** > **Integrations**.
2. Click the top-right three dots menu and choose **Custom repositories**.
3. Add the repository URL, select category **Integration**, and click **Add**.
4. Search for **Zenbi** and click **Download**.
5. Restart Home Assistant.

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
   - **Calendar Sync Interval (hours)**: Default is `6` hours (rolling 14-day window in `Europe/Copenhagen` timezone).

---

## Multi-Student Architecture

When an account is connected, Zenbi automatically discovers all enrolled children and provisions dedicated devices:
- **Student Device (`Zenbi ({Student Name})`)**: Contains that student's Schedule Calendar, Homework Todo List, and Weekly Plan Markdown Sensor.
- **School Device (`Zenbi (School)`)**: Contains the school-wide Planning Calendar (holidays/terms), full Weekly Messages Archive Calendar, and any unassigned events.

---

## Entities Provided

| Entity ID | Default Name (DA / EN) | Device | Description |
| :--- | :--- | :--- | :--- |
| `calendar.zenbi_{student}_schedule` | Skema / Schedule | Student | Timed school timetable, subjects, classroom resources, substitutes, and homework. |
| `todo.zenbi_{student}_homework` | Lektier / Homework | Student | Dedicated checklist of homework tasks with due dates, previews, and completion toggles. |
| `sensor.zenbi_{student}_weekly_plan` | Ugeplan / Weekly Plan | Student | Active weekly plan with formatted Markdown text attributes for Lovelace cards. |
| `calendar.zenbi_planning` | Årsplan / Planning | School | All-day school semester milestones, term dates, and holidays. |
| `calendar.zenbi_weekly_messages` | Ugebreve / Weekly Messages | School | 7-day all-day events containing weekly teacher letters and attachment lists. |
| `sensor.zenbi_last_synced` | Sidst synkroniseret / Last Synced | School | Diagnostic timestamp sensor tracking last successful sync, status, and error details. |

---

## Lovelace Dashboard Examples

Here are ready-to-use Lovelace dashboard configurations to get the most out of your Zenbi school data.

### 1. Weekly Plan (Ugeplan) Markdown Card
Renders the active week's plan with rich formatting, clickable file attachment links, and an optional preview of next week:

```yaml
type: markdown
title: Ugeplan
content: >-
  {{ state_attr('sensor.zenbi_weekly_plan', 'current_week_plan') }}

  {% if state_attr('sensor.zenbi_weekly_plan', 'next_week_plan') %}
  ---
  ### Næste uges plan ({{ state_attr('sensor.zenbi_weekly_plan', 'next_week_title') }})
  {{ state_attr('sensor.zenbi_weekly_plan', 'next_week_plan') }}
  {% endif %}
```

### 2. Timed Class Schedule (Calendar Card)
Displays daily class schedule alongside school holidays and semester milestones:

```yaml
type: calendar
title: Skoleskema
initial_view: dayGridMonth
entities:
  - calendar.zenbi_schedule
  - calendar.zenbi_planning
```

### 3. Homework Checklist (To-do List Card)
An interactive checklist of school assignments that syncs completion status persistently:

```yaml
type: todo-list
entity: todo.zenbi_homework
title: Lektier
```

### 4. Integration Sync Status (Tile Card)
Displays the timestamp of the last successful sync with relative time formatting, along with diagnostic status:

```yaml
type: tile
entity: sensor.zenbi_last_synced
name: Zenbi Synkronisering
icon: mdi:sync
```

### 5. Complete Student Dashboard (Vertical Stack)
Combine all cards into a single cohesive school dashboard view:

```yaml
type: vertical-stack
cards:
  - type: tile
    entity: sensor.zenbi_last_synced
    name: Zenbi Status
    icon: mdi:school-outline

  - type: todo-list
    entity: todo.zenbi_homework
    title: Lektier

  - type: calendar
    entities:
      - calendar.zenbi_schedule
      - calendar.zenbi_planning
    initial_view: dayGridMonth
    title: Skema & Årsplan

  - type: markdown
    title: Ugeplan
    content: >-
      {{ state_attr('sensor.zenbi_weekly_plan', 'current_week_plan') }}
```

> [!TIP]
> If you have multiple children enrolled in Zenbi, their entities will automatically include their name slug (e.g. `calendar.zenbi_albert_schedule`, `todo.zenbi_albert_homework`, and `sensor.zenbi_albert_weekly_plan`). Simply substitute the entity ID corresponding to each child.

### On-Demand Attachment Downloads
Zenbi attachment files (e.g. SFO calendars, classroom handouts, homework files) are served via an on-demand proxy endpoint (`/api/zenbi/file/{entry_id}/{file_id}`). When clicked from dashboard cards, Home Assistant automatically generates a fresh Azure Blob SAS token and redirects to the download, ensuring attachment links never expire.

---

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

