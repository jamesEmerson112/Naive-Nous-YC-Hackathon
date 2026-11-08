# cloud/CLAUDE.md — Romulus cloud platform

Rules for every coding session in `cloud/`. Specs live in `docs/cloud-platform-planning/`
(`PLAN/`): architecture §3–10 for layout and conventions, the APIs doc for payloads, the
v0 plan for the data model, the flows doc for what each flow test proves.

## Coding rules (architecture §9)
1. **Read first:** `PLAN/README.md`, the PRD, the v0 plan, the APIs doc, the architecture,
   then the build plan. Take the next task from the build plan's tracker.
2. **Vertical slices.** Each task ends with something that works through the API and a flow
   test (`tests/flows/test_fN_*.py`) that proves it.
3. **Domain stays pure.** `romulus_cloud/domain/` imports only `romulus_contracts` and the
   standard library. No DB, network or clock: pass `now` in. Each domain function gets unit tests.
4. **Every write records an Event** through `events.record(...)`, with a plain-English `why`,
   in the same transaction. `events.record` never commits.
5. **The LLM never writes merged config.** It writes prose and skills only.
6. **Contracts are shared (D28).** Only a breaking change to `romulus-contracts` (removed or
   renamed field, new required field, changed meaning) bumps `CONTRACT_VERSION`; optional
   additions don't. Every change updates the APIs doc and gets a message to the device teammate.
7. **Fakes first.** Every adapter works in fake mode; tests never call Naïve or an LLM.
8. **Explain it in one sentence**, or don't commit it. Small commits; `git pull --rebase
   origin main` before pushing; no AI co-author lines.

## Building flexibly (`~/git2/docs/CLOUD-PLATFORM-BUILD.md` §2)
1. **Thin end-to-end first.** Every stage exists as a stub before any gets deep; a
   pass-through stage is a valid stage.
2. **Seams over features.** Care goes into contracts, adapters and stage interfaces;
   internals stay plain and replaceable.
3. **Store raw, interpret later.** Keep device payloads verbatim in `raw` JSONB next to the
   parsed fields; contract models allow extra fields; `kind`/`format` are open strings.
4. **Config over code for anything uncertain:** providers, windows, caps, thresholds, stubbed
   stages, pass-through promotion. Each is a setting with a safe default (`settings.py`).
5. **Events are the spine.** The UI and the digest read only events.
6. **Fakes are first-class:** fake device, Naïve, LLM and coder. The loop runs offline.
7. **Version the contract, tolerate skew.** Accept the current and previous `contract_version`.
8. **Stubs announce themselves.** A stub's event says so ("replay: stub, passed by default").
9. **Robustness is earned.** Skip hardening until a real failure asks for it; write down the trigger.

## How this codebase does it
- **Transactions:** services never commit. A route or worker step commits once at the end,
  so a change and its Event land together or not at all.
- **IDs and time:** `ids.new_id("cand")`; `ids.utcnow()` only in services and the worker.
  Device rows keep `occurred_at` and `received_at`; sort by `occurred_at`.
- **Errors:** raise `errors.ApiError(status, code, sentence)` → `{"error", "detail"}`.
- **Routers:** fill `api/device.py`, `api/ops.py`, `api/web.py`; use `SessionDep` and
  `SettingsDep` from `api/deps.py`.
- **Worker:** append a `worker.Step(name, fn(session, now), every_s=...)` to `worker.STEPS`.
- **Events:** use the standard `refs` keys listed in `events.py` (`device`, `incident`,
  `overlay` + `device`, `candidate`, `work_order`, `recording`, ...) and always include
  `incident` when known. Read them with `events.find(session, {"incident": id})`
  (`refs @>`, GIN-indexed), never `refs["x"].astext == ...`.
- **Overlay ids are per device:** the key is `(device_id, id)`; anything pointing at an
  overlay carries both.
- **Tables:** all in `models.py`. Until the overnight run starts (M7), `create_all` plus
  `make reset` handles changes. **After M7, schema changes are additive only, and
  `create_all` never adds columns or constraints to an existing table.** New tables are
  fine (`create_all` creates them on restart). A new column on an existing table must be
  nullable (or have a server default), added to `models.py` *and* applied by hand:
  `docker compose exec db psql -U romulus -d romulus -c
  "ALTER TABLE <table> ADD COLUMN IF NOT EXISTS <col> <type>;"`. Never `make reset` to
  pick up a schema change after M7.
- **Tests:** `make test` (starts the shared `romulus-testdb` container, one throwaway DB per
  pytest session). Fixtures in `tests/conftest.py`: `db_session`, `sessions`, `app`,
  `client`, `settings`, `make_settings`, `now`. Everything in a test (requests, worker
  steps via `run_tick(steps, sessions, now, {})`, `db_session`) shares one rolled-back
  transaction, so they see each other's writes and nothing leaks. Tests never read `.env`
  or the shell environment; adapter modes are forced to fake.
