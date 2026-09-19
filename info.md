# Zenbi Home Assistant Integration

Connect Home Assistant to the Danish school communication platform **Zenbi** ([app.zenbi.dk](https://app.zenbi.dk)).

This integration brings daily class schedules, homework tasks, weekly teacher letters ("ugebreve"), and annual school planning directly into your Home Assistant dashboards and automations.

---

## Entities Provided

| Entity | Type | Description |
| :--- | :--- | :--- |
| `calendar.zenbi_schedule` | Calendar | Timed class schedule (**Skema**), classrooms, teachers, and attached homework. |
| `calendar.zenbi_weekly_messages` | Calendar | 7-day all-day events for weekly teacher letters (**Ugebreve**) with attachment previews. |
| `calendar.zenbi_planning` | Calendar | Semester milestones, school vacations, and all-day events (**Årsplan**). |
| `todo.zenbi_homework` | Todo List | Interactive checklist of school homework (**Lektier**) with due dates and descriptions. |

---

## Key Highlights

- 🇩🇰 **Full Danish Localization**: Native Danish (`da`) translations for entities and configuration dialogues.
- 📝 **Native Todo Dashboard Card**: Interactive checklist for homework directly on Lovelace dashboards with persistent completion states across reboots.
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

