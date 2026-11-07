# Cloud platform: v0 plan (system design)

- **Status:** v2, Sat Nov 7.
  - v1 was approved in principle (D15). v2 adds telemetry and datasets (D16), the Docker/FastAPI runtime (D17), and the Naïve research ([Naïve integration](cloud-platform-naive-integration.md)).
  - Lines tagged with a decision, like (D3), come from the decision log. Everything else is a proposal *(inferred)*. When this file and the decision log disagree, the log wins.
- **What this doc covers:** what the system does.
- **Where everything else lives:** see [`README.md`](README.md) for the full index.
  - [`cloud-platform-apis.md`](cloud-platform-apis.md): payloads and routes
  - [`cloud-platform-architecture.md`](cloud-platform-architecture.md): code and containers
  - [`cloud-platform-build-plan.md`](cloud-platform-build-plan.md): when it gets built
  - [`cloud-platform-flows.md`](cloud-platform-flows.md): how we know it works

---

## 1. What v0 does

v0 is the cloud half of a self-healing loop for our own arms, plus the data asset that loop produces. It's one tenant, running locally (D1, D2, D16).

1. When something breaks, the arm's embedded agent fixes it on the device by applying a patch as a local overlay (D5, D5a).
2. The device reports the incident and the patch, whether or not the fix worked. It also uploads the evidence: the incident window, log window and agent trace.
3. When an overlay holds, the batch job locks it in as a new firmware or harness version (D0).
4. The new version is checked: first replay against every past incident, then one canary device, then automatic promotion to every comparable arm, or automatic rollback (D6).
5. If the fault is hardware, the platform posts a work order to Naïve, and a Naïve agent phones in the part through Mobilerun (D12).
6. Everything is indexed, labeled and snapshot-able into datasets for firmware, harness and policy work (D16).
7. There is one human control, the kill switch (D11). Every step is shown on the incident timeline (D9, D10).

## 2. The loop, end to end

```
 DEVICE (teammate, D14)                              CLOUD (Kent)
 ──────────────────────                              ────────────
 ring buffer (black box) ─────────────────────────▶ RECORDINGS: incident window · log window · agent trace
 fault ─▶ agent patches as an OVERLAY (D5, D5a)        │ index + auto labels
          │                                            ▼
          ├─ fault returns / health fails             DATA: labels → dataset snapshots → exports
          │    └─▶ overlay reverts ──────────────────▶ incident (unresolved)        │
          │                                            └─ hardware? ─▶ WORK ORDER   │ regression corpus
          │                                                 (caps in our code)      │
          │                                                 ─▶ Naïve card ─▶ Mobilerun call
          │                                                 ◀─ poll card status     │
          └─ overlay holds ──────────────────────────▶ incident (resolved) + diff  │
                                                       ▼                            │
                                                      BATCH: held overlay ─▶ CANDIDATE
                                                       ▼                            │
                                                      GATE: replay all past incidents ◀┘
                                                            ─▶ canary (1 sim device, N min)
                                                       │              └─ unhealthy ─▶ auto ROLLBACK
                                                       ▼ healthy
                                                      PROMOTE to every device in the class
 visor pulls ◀─────────────────────────────────────────┘
 hash ─▶ install ─▶ health ─▶ (fail ─▶ last good) (D7)
                                                      KILL SWITCH (human, D11): freeze, pin last good
```

## 3. Scope at a glance

| In v0 | Out of v0 (D13, PRD §6) |
|---|---|
| Firmware and harness registry, with `device_class` | Digital twins, ERP |
| Device API: check-in, pull, installs, incidents, overlays, recordings | Billing |
| Batch job: held overlay → candidate | Trust ledger |
| Gate: replay (incident, then regression corpus) → canary → promote or roll back | Policy training (export only) |
| Kill switch | Multi-tenancy, auth, signing |
| Work orders → Naïve → Mobilerun, with caps in our code | Inbound calls from Naïve (MCP or webhooks need a public URL) |
| Telemetry ingest, index, automatic labels, evidence in the story | Live video and teleop |
| Labels, dataset snapshots, exports (corpus, traces; LeRobot as P2) | Push to the HF Hub |
| Incident timeline (first screen), Data page | Fleet grid, version pipeline (P2) |
| Fake devices; overnight run; digest | The real device side (the teammate's) |

## 4. Core objects

Every table has `fleet_id` (D1). The telemetry tables are detailed in [telemetry](cloud-platform-telemetry.md) §4.

| Object | What it is | Key fields |
|---|---|---|
| **Device** | One arm: real, sim or test | `id`, `kind`, `device_class` (which arms are "comparable", US2), `role` (normal/canary), `assigned_*`, `running_*`, `last_good_*`, `overlay_id`, `health`, `last_seen_at` |
| **FirmwareVersion** | A manifest (D3). Never edited | `version`, `device_class`, `manifest`, `sha256`, `parent_version`, `source`, `why`, `retired` |
| **HarnessVersion** | A package the cloud doesn't parse (D4). Never edited | `version`, `path`, `sha256`, `parent_version`, `source`, `why`, `retired` |
| **Overlay** | A local patch on one device (D5a) | `id`, `device_id`, `base_firmware`, `base_harness`, `diff`, `code_patch`, `status`, `incident_id` |
| **Incident** | One fault report | `id` (from the device), `device_id`, `occurred_at`, `received_at`, `kind`, `summary`, `agent_reasoning`, `actions`, `overlay_id`, `resolved`, `resolving_action`, `suspected_cause`, `signals` |
| **Candidate** | A proposed version moving through the gate | `id`, `type`, `version`, `from_overlays`, `status`, `canary_device`, `replay` (cases checked and passed), `verdict`, `why` |
| **WorkOrder** | A hardware fix handed to Naïve (also the outbox) | `id`, `incident_id`, `device_id`, `part`, `max_usd`, `naive_card_id`, `status` (pending/posted/in_progress/done/stopped/needs_human/blocked_over_cap), `result` |
| **Recording**, **Blob**, **Label**, **MetricSample**, **Dataset**, **DatasetVersion**, **Export**, **RegressionCase** | The data asset | [Telemetry](cloud-platform-telemetry.md) §4 |
| **FleetState** | One row per fleet | `frozen`, `frozen_by`, `frozen_at`, `batch_requested` |
| **Event** | Append-only log of every step | `id`, `ts`, `actor` (`device:arm-1`, `agent:batch`, `gate`, `naive:ses_…`, `human:kent`), `actor_class` (agent/automation/human), `kind`, `refs`, `why` |

**The legibility rule (D9):** every change to any table writes an Event with a plain-English `why`, in the same transaction. The incident timeline (D10) and the digest are both built from the Event log. `actor_class` is what the autonomy ledger counts ([Naïve integration](cloud-platform-naive-integration.md) §3.7).

**Versions are never edited,** so "CRUD" means create, read and retire. A fix is always a new version, and rollback means pointing back at an old one.

## 5. Contracts

All payloads are in [`cloud-platform-apis.md`](cloud-platform-apis.md) §2. They cover:
- the firmware manifest and the harness metadata;
- check-in, install results, incident reports and overlay status;
- recordings, and the shared `rules.evaluate()` function;
- the fleet dataset schema.

Draft v0.1 goes to the device teammate in M0.

## 6. Operator surface

| Page | What it shows | Milestone |
|---|---|---|
| **Timeline** `/` | Incidents, newest first, with a Freeze button at the top | M2 |
| **Incident** `/incidents/{id}` | The full story: what broke → agent → overlay → held or reverted → candidate → replay → canary → fleet, or → work order → Naïve card status. Each step shows its `why`, and the evidence links (recordings) are listed | M2, M4 |
| **Data** `/data` | Search recordings, add or remove labels, take snapshots, request exports | M6 |
| **Versions** `/versions` | Firmware, harness, candidates | P2 |

## 7. How each part works

### 7.1 Registry
Postgres tables, plus harness tarballs in the blob store. Firmware manifests are stored as JSONB. To promote, update `assigned_*` on every device in the candidate's `device_class`. `last_good_*` moves forward only after a device reports a healthy install.

### 7.2 Device API
Plain HTTP on the bench network. Devices always pull, and the platform never calls them. Incidents and recordings use IDs made on the device, so duplicates are ignored, and they are ordered by `occurred_at` (D8). Routine check-ins write no events; only changes do.

### 7.3 Batch job (D0)
Runs on demand and on a timer (`BATCH_INTERVAL_S`).
1. **Pick** held overlays that haven't been locked in.
2. **Merge, with no LLM:** candidate manifest = the currently assigned manifest + the overlay's `rules`/`params` diff (`domain/merge.py`).
3. **Explain and teach, with the LLM:** it writes the candidate's `why` and a markdown skill. The skill goes into a harness candidate under `knowledge_dir`.
4. Write Events, then hand the candidates to the gate.

The LLM only writes prose and skills, never the manifest (PRD principle 7).

**Third path, for code patches (D21; P1, build plan M8):** a held overlay with a `code_patch` goes to a coding agent in a sandbox. It writes a clean change and runs the device tests, and the result becomes a new code version and a candidate. The gate then runs the tests again and canaries the candidate on a device that runs real code (flow F11).

### 7.4 Gate (D6) and kill switch (D11)
1. **Replay** through the shared `evaluate(rules, signals)` (APIs §2.7):
   - **P0:** the triggering incident's signals must fire its `resolving_action`.
   - **P1 (M6):** every case in `regression_cases` must still pass (US9). The event says "replayed 37 incidents; 37 pass".
2. **Canary:** assign the candidate to the `role = canary` device (always simulated) for `CANARY_WINDOW_S`. It's healthy when the install succeeded, `health` stayed `ok`, and no incident of the same kind repeated.
3. **Promote** to every device in the class, or **roll back** the canary to `last_good` and record the reason.
4. **Kill switch:** `frozen = true` stops the gate and pins every device to `last_good`. Each use is a `human` event.

### 7.5 Work orders (D12)
The full design is in [Naïve integration](cloud-platform-naive-integration.md) §3.

1. `domain/triage.py` returns a work order for an incident that is unresolved and `suspected_cause: hardware`. It maps the fault kind to a part using `cloud/config/parts.yaml`.
2. **Spend caps are checked in our code first** (`ORDER_MAX_USD`, `ORDERS_PER_DAY_MAX`, `ORDERS_PER_ROBOT_DAY_MAX`). An order over a cap becomes `blocked_over_cap` and waits for a person (D19b).
3. The worker posts a self-contained card to the company board, with an `Idempotency-Key` header, assigned to `fleet-ops`.
4. It polls each open card by id and parses the note: `RESULT: ordered ORDER_REF=…` or `STOPPED: …`. Anything else becomes `needs_human`.

### 7.6 Telemetry and datasets (D16)
The full design is in [telemetry](cloud-platform-telemetry.md).

- **Formats:** LeRobotDataset v3.0 tars for incident windows and episodes, MCAP for log windows, JSONL for agent traces. Metrics from check-ins become rows.
- **Upload:** metadata first, then the content-addressed blob.
- **Labels:** applied automatically from incidents, overlays and outcomes; manual labels on the Data page.
- **Datasets:** a dataset is a saved filter, and a snapshot is an immutable version.
- **Exports:** `regression_corpus` (feeds the gate), `traces_jsonl` (harness), and `lerobot` (policy, P2).
- **Disk:** a budget that never deletes incident evidence or anything in a snapshot.

### 7.7 Test client (D14)
`tools/fake_device` runs N fake arms using only the contracts package plus HTTP, which proves the contracts are enough for the teammate. Each fake arm:
- checks in and follows its assignments;
- produces scripted incidents with overlays, then marks them held or reverted;
- uploads small synthetic recordings;
- has a bad-candidate mode;
- follows a chaos schedule for the overnight run.

## 8. Demo path

1. **A judge covers the camera on the real arm.** The agent holds a safe pose and raises the threshold as an overlay. The incident and its evidence appear on the timeline (F2, F7).
2. **The overlay holds** → the batch job creates v8 → replay passes → the canary on sim-2 passes → the fleet runs v8 (F3).
3. **A bad candidate:** the canary fails and it rolls back automatically, with the reason shown (F4).
4. **A servo stall that won't heal** → the overlay reverts → a work order → a Naïve card → the Mobilerun call (F6).
5. **Kill switch:** the whole fleet pins to last good, the one human control (F5).
6. **Overnight numbers from the Event log:** incidents, heals, promotions, rollbacks, work orders, and how many actions were agent, automation or human (F10).
7. **The dataset the night produced:** the regression corpus the gate now replays against (F8).

## 9. Open questions

**For Kent:**
- ~~Q1, code patches in overlays~~: decided as D21 (a coding agent drafts them) and D21a (P1, as soon as nothing blocks it).
- ~~Q2, the batch job's LLM~~: decided as D22 (Naïve proxy and RunPod first, behind swappable adapters).
- **D18:** approve the architecture proposals.
- **D19:** approve the Naïve design, and D19b: what happens over the spend cap.
- **D20:** approve the telemetry formats and capture policy.

**For the device teammate:** the frozen dataset schema; rollout-driven or scripted; Hermes or not; health-check definition; what counts as "held"; how a fault gets judged to be hardware. See telemetry §11 and APIs §2.

**For Naïve staff:** [Naïve integration](cloud-platform-naive-integration.md) §7.
