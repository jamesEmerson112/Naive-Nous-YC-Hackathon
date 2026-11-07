# Cloud platform: API and contracts

- **Status:** contract **v0.1, draft.** It stays a draft until the device teammate agrees to it (build plan M0).
- **Single source of truth:** this file defines every payload and route. The Pydantic models in `packages/contracts` must match it ([architecture](cloud-platform-architecture.md) §3).
- **Changes:** any change bumps `CONTRACT_VERSION` and goes to the device teammate.

---

## 1. Conventions

- **Base URL:** `http://<laptop-LAN-IP>:8000`. JSON in and out, except blob uploads and downloads.
- **Auth:** none in v0. The platform is reachable only on the bench network (D1; architecture §10).
- **IDs:**
  - Incident and recording IDs are **UUIDs made on the device**, so a retry repeats the same request and creates nothing new (D8).
  - The server makes other IDs: `ov_`, `cand_`, `wo_`, `ds_`, `exp_`, `evt_`. Overlay IDs are an exception: the device makes them and the server stores them as given.
- **Versions:** integers that go up within each fleet. Firmware and harness versions are **never edited** (D3, D4). Retiring one hides it from new assignments.
- **Time:** UTC ISO 8601. Each device-reported object carries `occurred_at` (or `started_at`), and the server adds `received_at`.
- **Errors:** `{"error": "<code>", "detail": "<sentence>"}` with the right HTTP status.
- **Routine check-ins don't write events.** Only state changes do (§5).

## 2. Shared contracts (`romulus_contracts`)

### 2.1 Firmware manifest (D3)

```json
{
  "version": 8,
  "device_class": "so101",
  "code_rev": "a1b2c3d",
  "rules": {
    "camera_occluded": {"signal": "brightness",         "below": 0.35, "action": "safe_pose"},
    "gripper_stall":   {"signal": "gripper_current_ma", "above": 900,  "action": "backoff_retry"}
  },
  "params": {"max_speed": 0.6},
  "policy": {"name": "pick_cube", "rev": "hf:org/act@3f9e"}
}
```

A rule fires when any sample of its `signal` is `below` or `above` the threshold. The list of signals and actions is agreed with the device teammate.

### 2.2 Harness metadata (D4)

The package is a `.tar.gz` whose contents belong to the device team. It must contain `harness.json` at its root:

```json
{"version": 5, "framework": "pi", "knowledge_dir": "skills/"}
```

The batch job adds markdown files under `knowledge_dir` and touches nothing else.

### 2.3 Check-in and response

Request:
```json
{"running": {"firmware": 7, "harness": 4, "code_rev": "a1b2c3d", "mcu_build": "stm32-f042-0007"}, "overlay_id": "ov-12",
 "health": "ok", "metrics": {"task_success_rate": 0.92, "servo_temp_c_max": 41.5, "bus_voltage_v": 12.1},
 "contract_version": "0.1"}
```
- `running` is `null` on first boot. `mcu_build` is the STM32's C-firmware build id; the healer reflashes it on the device, so the cloud tracks it as reported, not assigned (device side, `docs/project-overview.md`).
- `health` is `ok`, `degraded` or `failing`.
- `metrics` is a free-form dict of numbers, kept as environmental history ([telemetry](cloud-platform-telemetry.md) §3).

Response:
```json
{"assigned": {"firmware": 8, "harness": 4}, "drop_overlay": false, "frozen": false}
```

### 2.4 Install result

```json
{"type": "firmware", "version": 8, "result": "installed", "detail": "health ok after 5 s"}
```

`result` is `installed`, `rolled_back` or `hash_mismatch`.

### 2.5 Incident report

```json
{
  "incident_id": "0b6c6f3e-…",
  "occurred_at": "2026-11-07T22:02:11Z",
  "kind": "camera_occluded",
  "summary": "Camera brightness fell below threshold during pick",
  "running": {"firmware": 7, "harness": 4},
  "agent": {
    "reasoning": "Frames dark for 3 s; occlusion, not hardware. Held safe pose, raised threshold.",
    "actions": ["safe_pose", "patch rules.camera_occluded.below 0.2 -> 0.35"]
  },
  "overlay": {
    "overlay_id": "ov-12",
    "diff": {"rules": {"camera_occluded": {"below": 0.35}}},
    "code_patch": null
  },
  "outcome": {"resolved": true, "resolving_action": "safe_pose", "suspected_cause": "software"},
  "signals": {"brightness": [0.41, 0.18, 0.12, 0.33]},
  "recordings": ["5d1e…", "77ab…"]
}
```

- `suspected_cause` is `software`, `hardware` or `unknown`.
- `signals` is the small, inline sample that replay uses (§2.7).
- `recordings` lists the IDs of the bigger evidence files, which are uploaded separately (§3.7).
- `overlay` is `null` if the agent didn't patch anything.

### 2.6 Overlay status

```json
{"status": "held", "reason": "no recurrence for 20 min"}
```

`status` is `held` or `reverted`.

### 2.7 Rules evaluation (shared pure function)

`evaluate(rules, signals) -> list[Fired]` returns every rule that fires, in sorted rule-name order. It runs on the device (reflexes) **and** in the gate (replay), so both sides agree by construction.

A candidate **passes replay** for an incident when that incident's `resolving_action` is among the fired actions.

### 2.8 Recording metadata ([telemetry](cloud-platform-telemetry.md))

```json
{
  "recording_id": "5d1e…",
  "kind": "incident_window",
  "format": "lerobot_v3_tar",
  "sha256": "9c1f…",
  "size_bytes": 18322110,
  "started_at": "2026-11-07T22:01:41Z",
  "ended_at": "2026-11-07T22:02:41Z",
  "running": {"firmware": 7, "harness": 4},
  "overlay_id": "ov-12",
  "incident_id": "0b6c6f3e-…",
  "episode": null,
  "streams": ["observation.state", "action", "observation.images.front", "servo_temp", "servo_load", "servo_voltage", "fault_code", "reflex_active", "intervention"],
  "schema_version": "0.1",
  "labels": ["camera_occluded"]
}
```

- `kind` and `format` go together ([telemetry](cloud-platform-telemetry.md) §2):

  | `kind` | `format` |
  |---|---|
  | `incident_window` | `lerobot_v3_tar`: one finalized LeRobotDataset v3.0, tarred |
  | `log_window` | `mcap` |
  | `agent_trace` | `jsonl` (the agent's session export) |
  | `episode` | `lerobot_v3_tar` |

- `streams` lists what's inside the file. For LeRobot files these are the fleet feature schema's names (§2.9).
- `episode` is set only for `kind = episode`: `{"task": "pick_cube", "success": true, "index": 412}`.
- `labels` holds labels the device suggests. The platform adds its own labels automatically.

### 2.9 Fleet dataset schema

Every `lerobot_v3_tar` recording uses the same LeRobot features (schema **v0.1**), defined in [telemetry](cloud-platform-telemetry.md) §8, so the platform can merge them. Changing that list bumps both `schema_version` and `CONTRACT_VERSION`.

### 2.10 Code packages and `code_patch` (D21)

- **`code_rev`** in the firmware manifest names a code version in the platform's registry. It isn't a git ref the device has to fetch.
- **Code packages:** a code version is a `.tar.gz` of `device/` at that version, with a sha256. The device pulls it like a harness package (§3.3c) and installs it with hash check, health check and rollback (D7).
- **`code_patch`** in an overlay (§2.5) is a **unified diff** against the overlay's base `code_rev`, with paths relative to the `device/` root, for example `--- a/control/gripper.py`. It is `null` if the agent only changed rules or params.

## 3. Device-facing routes

| # | Route | Body → response | Notes |
|---|---|---|---|
| 3.1 | `POST /v1/devices/{device_id}/checkin` | §2.3 → §2.3 response | Every ~10 s. It also serves as the canary health signal |
| 3.2 | `GET /v1/firmware/{version}` | → `{"manifest": §2.1, "sha256": "…"}` | `sha256` is computed over the canonical JSON of the manifest |
| 3.3 | `GET /v1/harness/{version}/package` | → tarball, with header `X-Sha256` | |
| 3.3c | `GET /v1/code/{rev}/package` | → tarball, with header `X-Sha256` | Code packages (§2.10, D21) |
| 3.4 | `POST /v1/devices/{device_id}/installs` | §2.4 → `204` | |
| 3.5 | `POST /v1/devices/{device_id}/incidents` | §2.5 → `201 {"incident_id"}`, or `200` if already received | Idempotent on `incident_id` |
| 3.6 | `POST /v1/devices/{device_id}/overlays/{overlay_id}` | §2.6 → `204` | |
| 3.7a | `POST /v1/devices/{device_id}/recordings` | §2.8 → `201 {"recording_id", "upload": "needed" \| "exists"}` | If a blob with that `sha256` is already stored, no upload is needed |
| 3.7b | `PUT /v1/recordings/{recording_id}/blob` | raw bytes + `X-Sha256` → `204`, or `409 sha256_mismatch` | Idempotent. Resumable chunked upload is a later improvement ([telemetry](cloud-platform-telemetry.md) §3) |

**Device rules:** everything in 3.4–3.7 is queued on disk and retried with backoff while the cloud is unreachable (D8). Send incidents before their recordings.

## 4. Operator routes

These are your v1 headings, filled out. The dashboard uses the same routes.

### 4.1 CI/CD and DevOps: firmware and harness

| Route | Purpose |
|---|---|
| `POST /v1/firmware` | Create a firmware version by hand (manifest body); the batch job also creates them |
| `GET /v1/firmware`, `GET /v1/firmware/{v}` | List or read |
| `POST /v1/firmware/{v}/retire` | Hide from new assignments (versions are never deleted) |
| `POST /v1/harness` (multipart tarball), `GET /v1/harness`, `GET /v1/harness/{v}`, `POST /v1/harness/{v}/retire` | Same, for harness packages (CRUD on the embedded SRE) |
| `POST /v1/code` (multipart tarball), `GET /v1/code`, `GET /v1/code/{rev}` | Code versions: seeded by hand, then created by the coding agent (D21) |
| `GET /v1/devices`, `POST /v1/devices`, `PATCH /v1/devices/{id}` | Register devices; set `role` (normal/canary) and `device_class` |

### 4.2 Harness updates: incidents, batch and gate

| Route | Purpose |
|---|---|
| `GET /v1/incidents?device=&kind=&since=` | Incident list (pushed logs land here and in §4.3) |
| `GET /v1/incidents/{id}/story` | One incident's full chain, built from events (D10) |
| `POST /v1/batch/run` | Run the batch job now (it also runs on a timer) |
| `GET /v1/candidates`, `GET /v1/candidates/{id}` | Candidates and their gate state |

### 4.3 Telemetry and datasets (D16)

| Route | Purpose |
|---|---|
| `GET /v1/recordings?device=&kind=&label=&incident=&since=&until=` | Search the index |
| `GET /v1/recordings/{id}`, `GET /v1/recordings/{id}/blob` | Metadata; download the blob |
| `POST /v1/recordings/{id}/labels` | `{"add": [...], "remove": [...]}` |
| `GET /v1/devices/{id}/metrics?since=` | Environmental history from check-ins |
| `POST /v1/datasets` | `{"name", "purpose": "firmware" \| "harness" \| "policy", "filter": {…}}` |
| `POST /v1/datasets/{id}/versions` | Snapshot the filter's current matches into an immutable version |
| `GET /v1/datasets`, `GET /v1/datasets/{id}/versions/{n}` | List or read |
| `POST /v1/datasets/{id}/versions/{n}/exports` | `{"format": "regression_corpus" \| "traces_jsonl" \| "lerobot"}` → `exp_…` |
| `GET /v1/exports/{id}`, `GET /v1/exports/{id}/file` | Status; download |

### 4.4 Fleet control and work orders

| Route | Purpose |
|---|---|
| `POST /v1/fleet/freeze`, `POST /v1/fleet/unfreeze` | Kill switch (D11). Body `{"by": "kent", "reason": "…"}` |
| `GET /v1/work-orders`, `GET /v1/work-orders/{id}` | Work orders and their Naïve card status |
| `GET /v1/digest?since=` | Counts for the morning digest |
| `GET /v1/events?since=&ref=` | The raw event log |

### 4.5 Pages

| Route | Page |
|---|---|
| `GET /` | Incident timeline (D10) |
| `GET /incidents/{id}` | One incident's story, with evidence links |
| `GET /data` | Recordings, labels, datasets (M6) |
| `GET /versions` | Firmware, harness and candidates (P2) |

## 5. Event kinds

Every event has `actor`, `kind`, `refs` and `why` (v0 plan §4).

| Area | Kinds |
|---|---|
| Devices | `device.first_seen`, `device.health_changed`, `install.reported` |
| Incidents | `incident.reported`, `overlay.held`, `overlay.reverted`, `overlay.locked_in`, `overlay.dropped` |
| Batch and gate | `batch.ran`, `candidate.created`, `candidate.replayed`, `candidate.canary_started`, `candidate.promoted`, `candidate.rolled_back`, `candidate.frozen`, `candidate.awaiting_canary` |
| Coding agent (D21) | `coder.started`, `coder.drafted`, `coder.tests_passed`, `coder.tests_failed`, `code.version_created` |
| Fleet | `fleet.frozen`, `fleet.unfrozen` |
| Work orders | `workorder.created`, `workorder.card_posted`, `workorder.status_changed` |
| Data | `recording.received`, `label.changed`, `dataset.snapshotted`, `export.built` |

## 6. Where your v1 list went

| v1 (Kent's first draft) | v2 |
|---|---|
| CI/CD and DevOps · CRUD on firmware | §4.1 firmware routes. Create, read and retire, since versions are never edited |
| CRUD on self-healing agent harness (embedded SRE) | §4.1 harness routes |
| Harness Updates · Push logs from edge to cloud | §3.5 incidents + §3.7 recordings (logs, sensor data, agent traces) |
| Batch jobs for updating harness | §4.2 `POST /v1/batch/run` + the worker timer; candidates for both firmware and harness |
