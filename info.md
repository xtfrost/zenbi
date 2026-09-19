# Zenbi Home Assistant Integration

Connect Home Assistant to the Danish school communication platform **Zenbi** ([app.zenbi.dk](https://app.zenbi.dk)).

This integration brings daily class schedules, homework tasks, weekly teacher letters ("ugeplaner"), and annual school planning directly into your Home Assistant dashboards and automations.

---

## Multi-Student Architecture

Zenbi automatically detects all enrolled students and creates dedicated devices:
- **Student Device (`Zenbi ({Student Name})`)**: Contains that student's Schedule Calendar, Homework Todo List, and Weekly Plan Markdown Sensor.
- **School Device (`Zenbi (School)`)**: Contains the school-wide Planning Calendar (holidays/terms), full Weekly Messages Archive Calendar, and unassigned events.

---

## Entities Provided

| Entity | Platform | Device | Description |
| :--- | :--- | :--- | :--- |
| `calendar.zenbi_{student}_schedule` | Calendar | Student | Timed class schedule (**Skema**), classrooms, teachers, and attached homework. |
| `todo.zenbi_{student}_homework` | Todo List | Student | Interactive checklist of school homework (**Lektier**) with due dates and descriptions. |
| `sensor.zenbi_{student}_weekly_plan` | Sensor | Student | Active weekly plan (**Ugeplan**) with clean Markdown text attributes for dashboard cards. |
| `calendar.zenbi_planning` | Calendar | School | Semester milestones, school vacations, and all-day events (**Årsplan**). |
| `calendar.zenbi_weekly_messages` | Calendar | School | 7-day all-day events for weekly teacher letters (**Ugebreve**) with attachment previews. |
| `sensor.zenbi_last_synced` | Sensor | School | Diagnostic timestamp (**Sidst synkroniseret**) and health attributes for sync tracking. |

---

## Lovelace Dashboard Examples

### Weekly Plan (Ugeplan) Markdown Card
```yaml
type: markdown
title: Ugeplan
content: '{{ state_attr("sensor.zenbi_weekly_plan", "current_week_plan") }}'
```

### Sync Status Tile Card
```yaml
type: tile
entity: sensor.zenbi_last_synced
name: Zenbi Synkronisering
icon: mdi:sync
```

---

## Key Highlights

- 🇩🇰 **Full Danish Localization**: Native Danish (`da`) translations for entities and configuration dialogues.
- 👨‍👧‍👦 **Multi-Student Support**: Automatically partitions schedules, homework, and weekly plans per child.
- 📝 **Native Todo Dashboard Card**: Interactive checklist for homework directly on Lovelace dashboards with persistent completion states across reboots.
- 📰 **Markdown Dashboard Sensors**: Clean weekly plan text ready for instant display in Lovelace Markdown cards.
- 📎 **Perpetual File Downloads**: On-demand proxy redirects to fresh Azure Blob SAS download links so attachments never expire.
- 🔒 **Anti-Spam Login ID**: Generates a deterministic UUID per account to prevent Zenbi from sending repeated "New browser login" security alert emails.
- ⚡ **Optimized Cloud Polling**: 14-day rolling window query with concurrent endpoints, non-blocking asynchronous I/O, and zero memory leaks.

---

## Quick Setup

1. In Home Assistant, navigate to **Settings** > **Devices & Services**.
2. Click **Add Integration** and search for **Zenbi**.
3. Enter your Zenbi login credentials:
   - **Username / Email**: Your Zenbi username.
   - **Password**: Your Zenbi password.
   - **Device ID (Optional)**: Leave empty to automatically use a stable, deterministic device ID.
4. Click **Submit**.
