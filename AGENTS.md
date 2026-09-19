# AGENTS.md: Zenbi Home Assistant Integration

This document defines the operational boundaries, workflow gates, Home Assistant standards, and domain constraints for AI agents operating in this repository.

---

## 1. Operating Protocol & Workflow Gates

### 1.1 Planning Gate (Mandatory)
For any non-trivial feature, refactor, or architectural change:
1. **Inspect First:** Read relevant files (`manifest.json`, `coordinator.py`, `api/`, etc.) before proposing changes.
2. **Draft the Plan in `PLANNING.md`:**
   - Define exact scope, affected files, edge cases, and test strategy.
   - Outline the proposed data flow and changes to HA entities or API models.
3. **Stop & Await Approval:** Conclude your initial turn with a summary of the plan and ask for confirmation. **Never output raw implementation code in the planning turn.**
4. **Execute Incrementally:** Once approved, implement changes in focused steps.

### 1.2 Definition of Done (DoD)
Before marking any task as complete, you must:
- [ ] Run automated tests via `pytest tests/ -v` and confirm they pass with zero errors.
- [ ] Ensure translations are synchronized across `strings.json`, `da.json`, and `en.json`.
- [ ] Verify that entity IDs, attributes, and translations strictly follow Home Assistant naming standards.

---

## 2. Project Overview & Architecture

- **Domain:** `custom_components/zenbi`
- **Target Platform:** Home Assistant Core (Python 3.13+), HACS compatible.
- **Service:** Danish school platform Zenbi ([app.zenbi.dk](https://app.zenbi.dk)).
- **Primary Platforms:** `calendar` (`schedule`, `weekly_messages`, `planning`), `diagnostics`, `config_flow`, `options_flow`.

### Directory Layout
```text
custom_components/zenbi/
├── __init__.py           # Setup/unload lifecycle, reload listener
├── manifest.json         # Integration metadata, requirements, HACS config
├── const.py              # Constants, endpoints, intervals, User-Agent
├── coordinator.py        # DataUpdateCoordinator (14-day rolling window, asyncio.gather)
├── calendar.py           # CalendarEntity implementations
├── config_flow.py        # UI config & reauth flows
├── options_flow.py       # Configurable polling options
├── diagnostics.py        # Sensitive data redaction for HA diagnostics
├── strings.json          # Translation template source
├── translations/         # Localization files (da.json, en.json)
└── api/
    ├── __init__.py       # Package exports
    ├── client.py         # Async HTTP client (JWT auto-refresh, stable device ID)
    ├── exceptions.py     # ZenbiApiError, ZenbiAuthError, ZenbiConnectionError
    └── models.py         # Dataclasses & Quill Delta rich text parser

scripts/
└── test_live.py          # Standalone CLI tool to verify live API responses

tests/
├── conftest.py           # Lightweight HA shims and mock fixtures
├── test_api_client.py    # Unit tests for client, models, and parser
└── test_integration.py   # Integration tests for coordinator, entities, and flows