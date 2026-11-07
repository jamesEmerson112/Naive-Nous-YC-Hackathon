# Cloud platform: technical architecture

**Status:** approved Sat Nov 7 (D17: Docker, Python and FastAPI; D18: everything else in this doc). Build from it. Changes go through the [decision log](cloud-platform-decisions.md) first.

What the system does: [`cloud-platform-v0-plan.md`](cloud-platform-v0-plan.md). Payloads and routes: [`cloud-platform-apis.md`](cloud-platform-apis.md). Build order: [`cloud-platform-build-plan.md`](cloud-platform-build-plan.md).

---

## 1. Shape of the system

```
 bench network (venue LAN / our travel router)
 ┌──────────────────────────────────────────────────────────────────────────────────────┐
 │                                                                                      │
 │  real arm (teammate)        Kent's laptop — docker compose                           │
 │  ┌───────────────┐          ┌────────────────────────────────────────────────────┐   │
 │  │ visor         │  HTTP    │  api  (FastAPI, :8000)                             │   │
 │  │ embedded agent│ ───────▶ │   device API · ops API · dashboard (Jinja + htmx)  │   │
 │  │ firmware stack│  pull    │        │                         ▲                 │   │
 │  └───────────────┘          │        ▼                         │                 │   │
 │                             │  db  (Postgres)  ◀──────────  worker               │   │
 │  fake-devices (profile sim) │  blobs (volume ./data/blobs)    batch · gate tick ·│   │
 │  ┌───────────────┐  HTTP    │                                 Naïve sync · export│   │
 │  │ N fake arms   │ ───────▶ │                                      │             │   │
 │  └───────────────┘          └──────────────────────────────────────┼─────────────┘   │
 └────────────────────────────────────────────────────────────────────┼─────────────────┘
                                                                      │ outbound HTTPS only
                                                     Naïve API (board cards) · LLM API
```

- **Devices always pull,** and the platform never calls a device (v0 plan §7.2).
- **The platform only makes outbound calls** to Naïve and the LLM. Nothing on the internet calls in, so no tunnel is needed for v0. See [`cloud-platform-naive-integration.md`](cloud-platform-naive-integration.md) for when that might change.

## 2. Containers (`compose.yaml`)

| Service | Image | Command | Notes |
|---|---|---|---|
| `db` | `postgres:17` | default | Volume `pgdata`. Health check is `pg_isready`. |
| `api` | built from `cloud/Dockerfile` | `uvicorn romulus_cloud.main:app --host 0.0.0.0 --port 8000` | Port `8000` on the LAN. Mounts `./data/blobs`. Starts after `db` is healthy. |
| `worker` | same image as `api` | `python -m romulus_cloud.worker` | One instance. Runs the background loops (§5). Mounts `./data/blobs`. |
| `fake-devices` | built from `tools/fake_device/Dockerfile` | `python -m fake_device --n 4` | Profile `sim`. Depends only on the contracts package (§3). |
| `coder` | built from `cloud/coder/Dockerfile` | started by the worker for each code patch | **M8 (D21).** Holds a scratch copy of `device/`, the open-source coding agent, and the STM32 toolchain if patches touch C firmware. Network: the LLM endpoint only |
| `tunnel` | `cloudflare/cloudflared` | quick tunnel to `api:8000` | Profile `tunnel`. **Off by default.** Only if Naïve agents must call us (open question N3). |

**Why Postgres instead of SQLite:** two containers (`api` and `worker`) write at the same time, and JSONB suits manifests, labels and diffs. Postgres in Compose costs one service definition.

**Why one image for `api` and `worker`:** they share the same code and models. They differ only in their entrypoint.

## 3. Repository layout

The repo is shared with the team. The cloud platform lives under `cloud/`, and the contracts live in their own package, so the device teammate can install them without pulling in the platform.

```
Naive-Nous-YC-Hackathon/
├── compose.yaml                 # runs the platform locally
├── Makefile                     # the commands in §8
├── .env.example                 # every setting in §6, with safe defaults (fake modes)
├── pyproject.toml               # uv workspace root
├── uv.lock
├── packages/
│   └── contracts/               # romulus-contracts: shared with the device side
│       ├── pyproject.toml
│       └── src/romulus_contracts/
│           ├── manifest.py      # FirmwareManifest, HarnessMeta
│           ├── device.py        # CheckIn, CheckInResponse, InstallResult
│           ├── incident.py      # IncidentReport, OverlayStatus
│           ├── telemetry.py     # RecordingMeta, RecordingKind
│           ├── rules.py         # evaluate(rules, signals) -> Action   (pure; runs on device AND in the gate)
│           └── version.py       # CONTRACT_VERSION = "0.1"
├── cloud/                       # the platform (Kent)
│   ├── CLAUDE.md                # coding-session rules (copy of §9, created in M0)
│   ├── pyproject.toml
│   ├── Dockerfile
│   ├── src/romulus_cloud/       # §4
│   └── tests/
│       ├── unit/                # domain functions, no DB
│       └── flows/               # one test per core flow: test_f1_version_sync.py …
├── tools/
│   └── fake_device/             # test client that acts like N arms (depends only on romulus-contracts)
├── device/                      # the teammate's device side (placeholder; they own it)
├── naive/                       # Naïve crew config (naive.config.ts), owner TBD
├── data/                        # git-ignored: blobs/, exports/
└── docs/
```

The fake device depending **only** on `romulus-contracts` plus HTTP is deliberate. If the fake can do everything, the contracts are enough for the teammate too.

## 4. Inside `romulus_cloud`

```
romulus_cloud/
├── main.py            # FastAPI app factory; mounts the routers
├── settings.py        # pydantic-settings; reads env (§6)
├── db.py              # SQLAlchemy engine + session; create_all() on startup
├── models.py          # every table, in one file for v0 (v0 plan §4 + telemetry §4)
├── events.py          # record(actor, kind, refs, why): the ONLY way to write the Event log
├── domain/            # PURE functions: no DB, no network, no clock (pass `now` in)
│   ├── merge.py       # apply_overlay(manifest, diff) -> manifest
│   ├── gate.py        # replay(candidate, corpus) -> ReplayResult; canary_verdict(obs, now) -> Verdict
│   ├── story.py       # build_story(events) -> Story            (the incident timeline)
│   ├── triage.py      # needs_work_order(incident) -> WorkOrderSpec | None
│   ├── labels.py      # auto_labels(recording, incident) -> set[str]
│   └── digest.py      # summarize(events, since) -> Digest
├── services/          # one module per capability: DB + adapters + domain; every write records an Event
│   ├── registry.py    devices.py    incidents.py   telemetry.py   datasets.py
│   ├── batch.py       gate.py       fleet.py       workorders.py  digest.py
├── adapters/          # the outside world, each behind a Protocol with a Fake
│   ├── blobstore.py   # BlobStore: put(sha256, stream), get, exists. FsBlobStore now; S3 later
│   ├── naive.py       # NaiveClient: create_card, get_cards. HttpNaive | FakeNaive
│   ├── llm.py         # Llm: explain(incident) -> str; write_skill(incident) -> str. OpenAICompat (naive_proxy | runpod) | FakeLlm
│   └── coder.py       # Coder: draft(patch, incident, code_rev) -> CodeDraft. LocalAgent | NaiveSession | FakeCoder (M8)
├── api/
│   ├── device.py      # device-facing routes (APIs §3)
│   ├── ops.py         # operator JSON routes (APIs §4)
│   └── web.py         # HTML pages
├── web/
│   ├── templates/     # Jinja: base, timeline, incident, data, versions
│   └── static/        # htmx.min.js, one CSS file
├── worker.py          # the background loops (§5)
└── cli.py             # seed, reset, digest, export
```

**Dependency direction:** `api → services → {domain, adapters, models, events}`. `domain` imports only `romulus_contracts` and the standard library. This is the composable core: every decision the platform makes (merge, replay, canary verdict, triage, labels, story) is a pure function that can be unit-tested in milliseconds and read in one sitting.

## 5. Background work (`worker.py`)

One process, one loop, ticking every ~2 s. Each step is a service call that is safe to repeat:

| Step | What it does | Cadence |
|---|---|---|
| `gate.tick(now)` | Moves each candidate one state forward: replaying → canary → promoted, rolled_back or frozen | every tick |
| `batch.maybe_run(now)` | Locks in held overlays when the timer is due or someone pressed "run now" | `BATCH_INTERVAL_S` (120 s demo / 1 h overnight) |
| `workorders.sync()` | Posts new work orders to Naïve; polls card status | every 15 s |
| `datasets.run_exports()` | Builds requested export files | every tick |
| `telemetry.enforce_budget()` | Deletes the oldest unlabeled episode blobs over `BLOB_BUDGET_GB` | every 10 min |

The state lives in Postgres: candidate status, `fleet_state.batch_requested`, work-order status and export status. A crashed worker therefore resumes where it stopped. There's no job queue (no Celery or Redis). With one worker no locking is needed; if we ever run two, steps claim rows with `SELECT … FOR UPDATE SKIP LOCKED`.

## 6. Configuration (`.env`)

| Variable | Default | Meaning |
|---|---|---|
| `DATABASE_URL` | `postgresql+psycopg://romulus:romulus@db:5432/romulus` | |
| `BLOB_DIR` | `/data/blobs` | Root of the filesystem blob store |
| `BLOB_BUDGET_GB` | `20` | Disk cap for episode blobs (incident evidence is never auto-deleted) |
| `NAIVE_MODE` | `fake` | `fake` logs cards locally; `live` calls the Naïve API |
| `NAIVE_API_KEY`, `NAIVE_BASE_URL` (`https://api.vetta.sh/v1`), `NAIVE_VERSION` (`2026-09-30`), `NAIVE_BOARD_ID` (the CEO's `agt_` id), `NAIVE_OPS_AGENT` (`fleet-ops`) | — | See the Naïve integration doc |
| `ORDER_MAX_USD`, `ORDERS_PER_DAY_MAX`, `ORDERS_PER_ROBOT_DAY_MAX` | `50`, `3`, `1` | Spend caps, enforced in our code before a card is posted (Naïve integration §3.4) |
| `LLM_MODE` | `fake` | `fake` returns templated text; `live` calls the provider |
| `LLM_PROVIDER` (`fake` \| `naive_proxy` \| `runpod`), `LLM_BASE_URL`, `LLM_MODEL`, `LLM_API_KEY` | `fake` | One OpenAI-compatible client for both live providers (D22). Naïve proxy: `https://api.vetta.sh/v1/proxy/openai/v1`, key with `proxy:write`. RunPod: the team's vLLM endpoint (James's lane) |
| `CODER_PROVIDER` (`fake` \| `local_agent` \| `naive_session`), `CODER_AGENT`, `CODER_IMAGE` | `fake` | The coding agent for code patches (D21, D22). `local_agent` runs in the `coder` container against the same LLM endpoint |
| `CANARY_WINDOW_S` | `120` | 1200 for the overnight run |
| `BATCH_INTERVAL_S` | `120` | 3600 for the overnight run |

**Fake modes are the defaults** so that `make up` works offline, with no keys, on venue Wi-Fi. Going live means changing one variable per adapter.

## 7. Libraries

Pinned in `uv.lock` at M0. Adding a library means adding a row here.

| Need | Library |
|---|---|
| Web framework and validation | FastAPI, Pydantic v2, pydantic-settings, uvicorn |
| Database | SQLAlchemy 2 (typed ORM), psycopg 3 |
| HTTP client (Naïve, LLM) | httpx; the `openai` Python client pointed at `LLM_BASE_URL` for Chat Completions (works for the Naïve proxy and vLLM, D22) |
| Pages | Jinja2, htmx (one vendored JS file) |
| Tests | pytest, pytest-asyncio (only if needed) |
| Lint and format | ruff |
| Logs and diagnostics files | `mcap` (the fake device writes MCAP; the cloud only stores it in v0) |
| Data export (M6) | pyarrow (sidecar parquet). **`lerobot==0.7.0`** only for the P2 LeRobot export, as an optional extra (`cloud[lerobot]`), because it pulls in torch and would bloat the main image |
| Blob store | Plain filesystem, `data/blobs/sha256/ab/cd/<hash>`. Garage (single-node) if we need S3 later. **No `minio/minio`**: the community edition is archived ([telemetry](cloud-platform-telemetry.md) §7) |
| Naïve | REST with httpx (`https://api.vetta.sh/v1`, `Vetta-Version: 2026-09-30`). There's no Python SDK ([Naïve integration](cloud-platform-naive-integration.md) §4) |

**Schema changes:** `create_all()` on startup until the overnight run starts (M7). After that, changes must be additive (new tables or nullable columns, applied by hand with `ALTER`), so the overnight data survives. Bring in Alembic only if a destructive change becomes unavoidable.

## 8. Commands (`Makefile`)

| Command | What it does |
|---|---|
| `make up` | `docker compose up --build -d` (db, api, worker) |
| `make dev` | Same, with source mounted and `uvicorn --reload` |
| `make sim` | Also starts `fake-devices` (profile `sim`) |
| `make test` | `uv run pytest` against a throwaway `romulus_test` database in the `db` container |
| `make flows` | Only `tests/flows/`; prints a pass/fail line per flow (feeds the tracker in the flows doc) |
| `make seed` | Creates firmware v1, harness v1, and devices `arm-1` (real) plus `sim-1…3` (one canary) |
| `make reset` | Drops and recreates the database and clears `data/` (**refused once the overnight run has started**, unless `FORCE=1`) |
| `make digest` | Prints the digest since a given time |
| `make logs` | Follows the api and worker logs |

## 9. Rules for coding sessions (copied to `cloud/CLAUDE.md` in M0)

1. **Read first:** `README.md` in this folder, the PRD, the v0 plan, the APIs doc, this doc, then the build plan. Take the next task from the build plan's tracker.
2. **Vertical slices.** Each task ends with something that works through the API and a flow test that proves it. Update the flow's status in [`cloud-platform-flows.md`](cloud-platform-flows.md).
3. **Domain stays pure.** No DB, network or clock in `domain/`. Each domain function gets unit tests.
4. **Every write records an Event** through `events.record(...)`, with a plain-English `why`, in the same transaction. No exceptions: it's what the timeline is built from (D9).
5. **The LLM never writes merged config.** It writes prose and skills only (PRD principle 7).
6. **Contracts are shared.** Any change to `romulus-contracts` bumps `CONTRACT_VERSION`, updates the APIs doc, and gets a message to the device teammate.
7. **Fakes first.** Every adapter works in fake mode, and tests never call Naïve or an LLM.
8. **Explain it in one sentence.** Don't commit code you can't explain in one sentence (`~/git2/docs/1-hackathon-plan.md`, rule against slop). Small commits; `git pull --rebase origin main` before pushing; no AI co-author lines (repo `CLAUDE.md`).

## 10. Conventions

- **IDs:** prefixed strings, created by the server: `ov_`, `cand_`, `wo_`, `rec_`, `ds_`, `evt_`. Exceptions: incident and recording IDs are UUIDs created on the device, so retries are harmless (D8). Firmware and harness versions are integers that go up within each fleet.
- **Time:** UTC, timezone-aware, ISO 8601 in JSON. Sort by `occurred_at`, never by `received_at`.
- **Errors:** JSON `{"error": "<code>", "detail": "<sentence>"}` with the matching HTTP status.
- **Logs:** one JSON line per request or worker step to stdout. `make logs` reads them.
- **Security (v0):** no auth; the platform is reachable only on the bench network. If the `tunnel` profile is ever turned on, the routes it exposes need a shared bearer token first (open question N3).
