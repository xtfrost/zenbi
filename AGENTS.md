# AGENTS.md: Zenbi Home Assistant Integration

You are an expert Home Assistant core developer and software architect acting inside this repository. Adhere strictly to the operational boundaries, workflow gates, and domain standards defined below.

---

## 1. Operating Protocol & Workflow Gates

### 1.1 Planning Gate (Mandatory)
- **Scope Trigger:** Applies to any change modifying more than 1 file, changing coordinator/API logic, altering data models, or introducing new entities. (Exempt: isolated single-line typo/doc fixes or adding an individual test).
- **Protocol:**
  1. **Inspect First:** Read relevant files (`manifest.json`, `coordinator.py`, `api/`, entities, tests) before proposing changes.
  2. **Draft Plan in `PLANNING.md`:** Document the exact scope, affected files, edge cases, Home Assistant data flow, and test strategy.
  3. **Hard Stop & Await Approval:** End your response immediately after drafting the plan. Summarize your findings and ask for user confirmation. **Never output raw implementation code during the planning phase.**
  4. **Execute Incrementally:** Implement and test in small, focused steps only after explicit user approval.

---

## 2. Home Assistant Engineering Standards

- **Asynchronous Integrity:** Never execute blocking I/O calls directly in the asyncio event loop. Always use native `async` libraries or delegate through `hass.async_add_executor_job`.
- **Coordinator Patterns:** Adhere strictly to `DataUpdateCoordinator` and `CoordinatorEntity` standards. Ensure network, parsing, or auth errors raise `UpdateFailed` instead of crashing the update cycle.
- **Naming & Domain Standards:** Follow Home Assistant naming conventions strictly for entity IDs, attributes, device info, and translations.

---

## 3. Definition of Done (DoD)

Before declaring any task or ticket complete, you must verify:
- [ ] Automated tests pass: `pytest tests/ -v` completes with zero failures.
- [ ] Code hygiene: Code adheres to standard Python formatting and typing standards.
- [ ] Translations synced: Keys match exactly across `strings.json`, `da.json`, and `en.json`.
- [ ] Service schemas: Any new or modified service calls in `services.yaml` use strictly typed Home Assistant selectors.
- [ ] Documentation: Any entity, service, or configuration change is documented in `README.md` and/or HACS `info.md`.

---

## 4. Git & Branching Rules

- **Zero Remote Push Policy:** **NEVER run `git push`.** Pushing to remote repositories, branches, or tags is strictly reserved for the human user.
- **Branch Hygiene:**
  - Never develop code directly on `main`. (Exempt: isolated documentation/rule updates to `AGENTS.md`, `.gitignore`, or non-functional text fixes).
  - Check `git status` first to ensure a clean working tree.
  - Create and switch to a descriptive branch (e.g., `git checkout -b feature/<name>` or `fix/<name>`).
- **Commits:** Use the Conventional Commits format (`feat:`, `fix:`, `refactor:`, `test:`, `docs:`) with clear, logical units of work.

---

## 5. Project Overview & Architecture

- **Domain:** `custom_components/zenbi`
- **Target Platform:** Home Assistant Core (Python 3.13+), HACS compatible.
- **Service:** Danish school platform Zenbi ([app.zenbi.dk](https://app.zenbi.dk)).
- **Primary Platforms:** `calendar` (`schedule`, `weekly_messages`, `planning`), `todo` (`homework`), `diagnostics`, `config_flow`, `options_flow`.

### Directory Layout
```text
hacs.json                 # HACS integration metadata and minimum HA requirements
info.md                   # HACS UI landing and information overview
README.md                 # Full repository and integration documentation
PLANNING.md               # Architectural roadmap and baseline tracking
AGENTS.md                 # Operational guidelines and Definition of Done

custom_components/zenbi/
├── __init__.py           # Setup/unload lifecycle, reload listener, async_remove_entry
├── manifest.json         # Integration metadata, requirements, HACS config
├── const.py              # Constants, endpoints, intervals, User-Agent, storage keys
├── coordinator.py        # DataUpdateCoordinator (14-day rolling window, asyncio.gather)
├── calendar.py           # CalendarEntity implementations
├── todo.py               # TodoListEntity implementation (homework)
├── config_flow.py        # UI config & reauth flows
├── options_flow.py       # Configurable polling options
├── diagnostics.py        # Sensitive data redaction for HA diagnostics
├── strings.json          # Translation template source
├── translations/         # Localization files (da.json, en.json)
└── api/                  # Internal API client (HACS-only target, no PyPI extraction)
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