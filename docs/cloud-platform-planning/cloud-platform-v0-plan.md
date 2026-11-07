# Cloud platform: v0 plan

**Status: proposed, not yet approved** (decision D15 in [`cloud-platform-decisions.md`](cloud-platform-decisions.md)).

- Lines tagged with a decision, like (D3), come from the decision log.
- Everything else in this file is a proposal worked out from those decisions *(inferred)*. Change it freely.
- When this plan and the decision log disagree, the decision log wins.

---

## 1. What v0 does

v0 is the cloud half of a self-healing loop for our own arms: one tenant, run locally (D1, D2).

1. When something breaks, the arm's embedded agent fixes it on the device by applying a patch as a local overlay.
2. The device reports the incident and the patch to the cloud, whether or not the fix worked.
3. When an overlay holds, the batch job locks it in as a new firmware or harness version (D0).
4. The version is checked by replay, then on one canary device, and then promoted to the whole fleet automatically, or rolled back automatically (D6).
5. When the fault is hardware, the platform posts a work order to Naïve, and a Naïve agent phones in the part through Mobilerun (D12).
6. One human control exists: the kill switch (D11).
7. Every step is shown on one screen, the incident timeline (D9, D10).

## 2. The loop, end to end

```
 DEVICE (teammate builds, D14)                      CLOUD (Kent builds)
 ─────────────────────────────                      ───────────────────
 fault ─▶ embedded agent patches
          as a local OVERLAY (D5, D5a)
          │
          ├─ fault comes back / health fails
          │    └─▶ overlay reverts itself ───────▶ incident (unresolved)
          │                                         └─ suspected hardware? ─▶ WORK ORDER
          │                                              ─▶ Naïve board card ─▶ Mobilerun call
          │
          └─ overlay holds ──────────────────────▶ incident (resolved) + overlay diff
                                                    │
                                                    ▼
                                                   BATCH JOB: held overlay ─▶ CANDIDATE version
                                                    │
                                                    ▼
                                                   GATE: replay ─▶ canary (1 device, N min)
                                                    │                 │
                                                    │        unhealthy └─▶ auto ROLLBACK
                                                    ▼ healthy
                                                   PROMOTE: assigned version for all devices
                                                    │
 visor pulls the assigned version ◀─────────────────┘
 hash check ─▶ install ─▶ health check
   └─ fail ─▶ roll back to last good (D7)
 (a new version replaces any overlay)

 KILL SWITCH (human, D11): freeze all rollouts, pin every device to last good
```

## 3. Scope at a glance

| In v0 | Out of v0 (D13) |
|---|---|
| Firmware and harness version registry | Digital twins, ERP (PRD story 5) |
| Device API: check-in, pull, install results, incident reports | Billing per heal (events are recorded, but nothing is billed) |
| Batch job: held overlay → candidate version | Trust ledger |
| Gate: replay → canary → auto promote or rollback | Policy model retraining (the policy version stays pinned) |
| Kill switch | Multiple tenants and real auth (D1) |
| Work orders → Naïve → Mobilerun | Signing of versions (D7) |
| Incident timeline screen | Fleet grid and version pipeline screens (later, if time allows) |
| Test client that acts like a device | The real device side (the teammate's work, D14) |

## 4. Core objects

Nine tables. Every table has a `fleet_id` column so more tenants can be added later without a rewrite (D1).

| Object | What it is | Key fields |
|---|---|---|
| **Device** | One arm: real, simulated, or test | `id`, `kind` (real/sim/test), `role` (normal/canary), `assigned_firmware`, `assigned_harness`, `running_firmware`, `running_harness`, `last_good_firmware`, `last_good_harness`, `overlay_id`, `health`, `last_seen_at` |
| **FirmwareVersion** | A manifest (D3). Never changed after creation | `version`, `manifest` (JSON), `sha256`, `parent_version`, `source` (manual/batch), `why` |
| **HarnessVersion** | A package whose contents the cloud doesn't parse (D4). Never changed | `version`, `path`, `sha256`, `parent_version`, `source`, `why` |
| **Overlay** | A local patch on one device (D5a) | `id`, `device_id`, `base_firmware`, `base_harness`, `diff`, `status` (active/held/reverted/locked_in/dropped), `incident_id` |
| **Incident** | One fault report from a device | `id` (created on the device, D8), `device_id`, `occurred_at`, `received_at`, `kind`, `summary`, `agent_reasoning`, `actions`, `overlay_id`, `resolved`, `suspected_cause`, `signals`, `logs` |
| **Candidate** | A proposed new version moving through the gate | `id`, `type` (firmware/harness), `version`, `from_overlays`, `status` (replaying/replay_failed/canary/promoted/rolled_back/frozen), `canary_device`, `verdict`, `why` |
| **WorkOrder** | A hardware fix handed to Naïve | `id`, `incident_id`, `device_id`, `type` (part/technician), `description`, `naive_card_id`, `status` |
| **FleetState** | One row per fleet | `frozen`, `frozen_by`, `frozen_at` |
| **Event** | Append-only log of every step | `id`, `ts`, `actor` (`device:arm-1`, `agent:batch`, `gate`, `human:kent`), `kind`, `refs` (ids of the objects involved), `why` |

**Legibility rule (D9):** every change to any other table also writes an **Event** with a plain-English `why`. The incident timeline (D10) is built by following an incident's `refs` through the Event log. This is the "Mark" from `~/git2/docs/ARCHITECTURE.md` §4, kept small.

**Versions are never edited.** That makes "CRUD" in [`cloud-platform-apis.md`](cloud-platform-apis.md) mean create, read and retire. A fix is always a new version, so rollback is just pointing back at an old one.

## 5. Contracts for the device teammate

The device side belongs to a teammate (D14), so these contracts are Kent's first deliverable. Everything below is a draft until the teammate agrees.

### 5.1 Firmware manifest (D3)

```json
{
  "version": 8,
  "code_rev": "a1b2c3d",
  "rules": {
    "camera_occluded": {"threshold": 0.35, "action": "safe_pose"},
    "gripper_stall":   {"current_ma": 900, "action": "backoff_retry"}
  },
  "params": {"max_speed": 0.6},
  "policy": {"name": "pick_cube", "rev": "hf:org/act@3f9e"}
}
```

### 5.2 Harness package (D4)

A `.tar.gz` file. Its contents belong to the device team. The cloud reads only `harness.json` at the root:

```json
{"version": 5, "framework": "hermes", "knowledge_dir": "skills/"}
```

`knowledge_dir` is the folder where the batch job adds new markdown skills or runbooks. The cloud touches nothing else in the package.

### 5.3 Incident report

`POST /v1/devices/{device_id}/incidents`. The device sends one report for every fault, whether it resolved or not. Sending the same report twice is harmless, because `incident_id` is created on the device and the cloud ignores duplicates (D8).

```json
{
  "incident_id": "0b6c…",
  "occurred_at": "2026-11-07T22:02:11Z",
  "kind": "camera_occluded",
  "summary": "Camera brightness fell below threshold during pick",
  "running": {"firmware": 7, "harness": 4},
  "agent": {
    "reasoning": "Frames dark for 3 s; occlusion, not a hardware fault. Held safe pose, raised threshold.",
    "actions": ["safe_pose", "patch rules.camera_occluded.threshold 0.2 -> 0.35"]
  },
  "overlay": {
    "overlay_id": "ov-12",
    "diff": {"rules": {"camera_occluded": {"threshold": 0.35}}},
    "code_patch": null
  },
  "outcome": {"resolved": true, "suspected_cause": "software"},
  "signals": {"brightness": [0.41, 0.18, 0.12, 0.33]},
  "logs": "last 200 lines of the control-stack log"
}
```

- `signals` holds the recorded values that replay will use (§7.4).
- `suspected_cause` can be `software`, `hardware` or `unknown`. A report with `hardware` that also has `resolved: false` creates a work order (§7.5).

### 5.4 Overlay status

`POST /v1/devices/{device_id}/overlays/{overlay_id}` with `{"status": "held" | "reverted", "reason": "…"}`. The device reports `held` after the overlay has survived N minutes without the fault coming back. It reports `reverted` when the overlay reverted itself (D5a).

### 5.5 Check-in (also the heartbeat and health signal)

`POST /v1/devices/{device_id}/checkin`, sent every ~10 s:

```json
{"running": {"firmware": 7, "harness": 4}, "overlay_id": "ov-12",
 "health": "ok", "metrics": {"task_success_rate": 0.92}}
```

The response:

```json
{"assigned": {"firmware": 8, "harness": 4}, "drop_overlay": false, "frozen": false}
```

If `assigned` differs from `running`, the visor pulls and installs the assigned version (§5.6).

### 5.6 Pull and install result (D7)

- `GET /v1/firmware/{version}` returns `{manifest, sha256}`.
- `GET /v1/harness/{version}/package` returns the tarball, with `X-Sha256` in the header.
- `POST /v1/devices/{device_id}/installs` with `{"type": "firmware", "version": 8, "result": "installed" | "rolled_back" | "hash_mismatch", "detail": "…"}`.

## 6. Cloud API

**Device-facing:** the routes in §5.

**Operator- and dashboard-facing:**

| Route | Purpose | Maps to `cloud-platform-apis.md` |
|---|---|---|
| `POST/GET /v1/firmware`, `GET /v1/firmware/{v}`, `POST /v1/firmware/{v}/retire` | Firmware versions | CRUD on firmware |
| `POST/GET /v1/harness`, `GET /v1/harness/{v}`, `POST /v1/harness/{v}/retire` | Harness versions | CRUD on self-healing agent harness |
| `GET /v1/devices`, `POST /v1/devices` | Register devices, set canary role | — |
| `GET /v1/incidents`, `GET /v1/incidents/{id}/story` | Incident list; one incident's full chain | Push logs from edge to cloud |
| `POST /v1/batch/run` | Run the batch job now (it also runs on a timer) | Batch jobs for updating harness |
| `GET /v1/candidates` | Candidates and where each is in the gate | — |
| `POST /v1/fleet/freeze`, `POST /v1/fleet/unfreeze` | Kill switch (D11) | — |
| `GET /v1/work-orders` | Work orders and their Naïve status | — |
| `GET /` | Incident timeline page (D10) | — |

## 7. How each part works in v0

### 7.1 Registry
SQLite tables plus a folder on disk for harness tarballs. Firmware manifests are stored as JSON. To promote a version, update the `assigned_*` fields on the devices and move the old version to `last_good_*`.

### 7.2 Device API
Plain HTTP. The device always pulls; the cloud never calls the device. That answers the "bench pulls vs tunnel" open question in `CLAUDE.md` in favor of the bench pulling, as long as both are on the same network. Incident reports are stored by `occurred_at` and duplicates are ignored (D8).

### 7.3 Batch job (D0)
Runs on demand (`POST /v1/batch/run`) and on a timer (every few minutes for the demo, nightly as the story).

1. **Pick:** overlays with status `held` that haven't been locked in yet.
2. **Merge (no LLM):** candidate firmware = the currently assigned manifest with the overlay's `rules`/`params` diff applied on top. This step is deterministic, so it's easy to check and easy to roll back.
3. **Explain and teach (LLM):** an LLM reads the incident (reasoning, actions, signals) and writes:
   - the candidate's `why`, in plain English;
   - a markdown skill for the harness, which becomes a harness candidate with the skill added under `knowledge_dir`.
4. Write Events. Hand the candidates to the gate.

The LLM only writes prose and skills; it never edits the manifest. This keeps it away from the part that gets merged (`CLAUDE.md` L1.9: anything an LLM touches gets sloppier).

**Not handled in v0:** an overlay with a `code_patch`. The timeline flags it ("code patch held; needs a device-side engineer to lock in"), and the batch job skips it (see the open questions).

### 7.4 Gate (D6) and kill switch (D11)
1. **Replay:** run the candidate's rules over the incident's recorded `signals` and pass if the rule now fires the action that resolved the incident. This needs the rule evaluator to be one shared pure function, `evaluate(rules, signals) -> action`, used by both the device and the cloud. If we can't share it in time, replay is a stub that passes and says so in its Event ("replay not implemented; passed by default"). The stub is ugly but honest. MuJoCo replay comes later.
2. **Canary:** assign the candidate to the device with `role = canary` (a sim or test device). Wait N minutes: 2 for the demo, 20 overnight. Healthy means all three of:
   - the install succeeded;
   - check-ins report `health: ok`;
   - no new incident of the same `kind` on that device.
3. **Promote or roll back:**
   - **Healthy:** assign the candidate to every device, and mark it `promoted`.
   - **Unhealthy:** put the canary back on its last good version, and mark the candidate `rolled_back` with the reason.
4. **Kill switch:** `frozen = true` stops every gate step and assigns every device its last good version. Each use is recorded as an Event with a `human:` actor.

### 7.5 Work orders (D12)
- **Trigger:** an incident with `resolved: false` and `suspected_cause: hardware`.
- **Action:** create a WorkOrder, then create a Naïve board card with `POST /v1/boards/{agt_id}/cards`:
  - `key: "wo-<id>"`, so retries don't make duplicate cards;
  - `title` and `body` describe the device, the part and the incident summary;
  - `assignee` is the Naïve agent that has the Mobilerun MCP.

  Assigning a card to an agent starts that agent on it ([Naïve board API](https://usenaive.ai/docs/api/board)).
- **Status:** poll `GET /v1/boards/{id}/cards` and show the card's status on the timeline.
- **Spend cap:** lives in Naïve's policy file, not in our code (D11, D12).

### 7.6 Dashboard (D10)
One server-rendered page: the incident timeline, refreshing every few seconds, with the kill switch at the top. Each incident opens into its story: what broke → what the agent did → overlay → held or reverted → candidate → replay → canary → fleet, or → work order → Naïve card status. Every step shows its `why`.

### 7.7 Test client (D14)
A `fake_device.py` script that runs several fake devices, so the cloud can be built and demoed before the real device works. It's a test fixture, not a second device stack. Each fake device:
- checks in and follows its assignments;
- sends scripted incidents with overlays, then reports them as held or reverted;
- has a "bad candidate" mode that makes the canary fail.

It also drives the overnight run if the teammate's sim fleet isn't ready.

## 8. Build order

Each milestone ends with something that works end to end. Hours are rough guesses.

| # | Milestone | Done when | ~h |
|---|---|---|---|
| M0 | **Contracts:** §5 as Pydantic models plus example payloads, shared with the teammate | The teammate agrees to the shapes | 1 |
| M1 | **Walking skeleton:** SQLite, registry, device API, Event log, test client | Test client checks in, gets firmware v1, sends an incident; `GET /v1/incidents/{id}/story` returns the chain | 2 |
| M2 | **Incident timeline page** | A judge can read one incident's story without help | 1.5 |
| M3 | **Batch job:** held overlay → candidate firmware and harness, with an LLM-written `why` and skill | A held overlay from the test client becomes candidate v2 with a readable reason | 1.5 |
| M4 | **Gate and kill switch:** replay, canary, promote or roll back, freeze | A good candidate reaches the whole fleet; the bad-candidate mode rolls back automatically; freezing pins every device to last good | 2 |
| M5 | **Work orders:** Naïve card, Mobilerun, status shown on the timeline | A hardware incident from the test client becomes a Naïve card, and the card's status shows on the timeline | 1.5 |
| M6 | **Overnight run:** test or sim fleet plus scheduled faults; morning counts taken from Events | It runs unattended for an hour with no errors | 1 |

Swap in the real arm whenever the teammate's device side can speak the contracts. That can happen at any milestone after M1.

## 9. Demo path (how v0 shows up on stage)

1. **A judge covers the camera on the real arm.** The agent holds a safe pose and raises the threshold as an overlay. The incident appears on the timeline.
2. **The overlay holds,** the batch job locks it in as firmware v8, replay passes, the canary passes on sim-2, and v8 reaches the whole fleet. It's all on one incident story.
3. **A bad candidate** (bad-candidate mode): the canary fails, it rolls back automatically, and the reason is on the timeline.
4. **A servo stall that won't heal:** the overlay reverts, a work order is created, a Naïve card goes up, and the Mobilerun call goes out (sponsor track).
5. **Kill switch:** press it, and the whole fleet pins to last good. This is the one human control.
6. **Overnight numbers from the Event log:** incidents, heals, versions promoted, rollbacks, and how many human actions there were (ideally zero kill-switch uses).

## 10. Proposed stack (not decided)

| Choice | Proposal | Why |
|---|---|---|
| Language | Python 3.12, managed with `uv` | Same language as LeRobot on the device side, so the contracts can be shared Pydantic models |
| Server | FastAPI, one process | Small, typed, and generates its own API docs |
| Storage | SQLite file plus a folder for tarballs | Nothing to set up; it's a local v0 |
| Dashboard | Server-rendered HTML (Jinja) with htmx polling | No front-end build step; one screen |
| Batch timer | An asyncio background task in the same process | No extra infrastructure |
| LLM for the batch job | Open: Claude API or the team's RunPod model | Only writes prose and skills, so it isn't latency-critical |
| Where it runs | Kent's laptop on the bench network | Devices pull over HTTP on the LAN (see the venue Wi-Fi notes in `CLAUDE.md`) |

The alternative in `~/git2/docs/ARCHITECTURE.md` §6 is Next.js with Supabase or Convex. That's heavier for a local v0 and a second language.

## 11. Open questions

**For the device teammate (D14):**
- Can the rule evaluator be a shared pure function `evaluate(rules, signals) -> action`? Real replay depends on it (§7.4).
- What does the device's health check test (D7)? What counts as "held" for an overlay, and after how many minutes (§5.4)?
- What decides `suspected_cause: hardware` (D12)?
- Is the harness Hermes, and what is its `knowledge_dir` (D4)?
- Which sim or fake devices can act as the canary (D6)?

**For Kent:**
- **Code patches in overlays:** D5 allows them, but v0's batch job only locks in rules, params and harness changes. Is it OK to flag code patches and skip them, or does the batch job need a coding agent?
- Which LLM does the batch job use?
- Is the stack in §10 OK?
- Which Naïve agent owns work orders, what's its name, and who sets up its Mobilerun MCP and spend cap?
- What's the canary window: 2 minutes for the demo, 20 overnight?
