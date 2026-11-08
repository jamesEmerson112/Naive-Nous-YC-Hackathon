# Cloud platform: API and contracts

- **Status:** contract **v0.1, draft.** It stays a draft until the device teammate agrees to it (build plan M0).
- **Single source of truth:** this file defines every payload and route. The Pydantic models in `packages/contracts` must match it ([architecture](cloud-platform-architecture.md) §3).
- **Changes:** v0.1 is a draft until the I0 handshake. Optional, additive changes don't bump `CONTRACT_VERSION`; only breaking changes do (a removed or renamed field, a new required field, a changed meaning), and the server keeps accepting the previous version (D28). Every change goes to the device teammate.

---

## 1. Conventions

- **Base URL:** `http://<laptop-LAN-IP>:8000`. JSON in and out, except blob uploads and downloads.
- **Auth:** none in v0. The platform is reachable only on the bench network (D1; architecture §10).
- **IDs:**
  - Incident and recording IDs are **UUIDs made on the device**, so a retry repeats the same request and creates nothing new (D8).
  - The server makes other IDs: `ov_`, `cand_`, `wo_`, `ds_`, `exp_`, `evt_`, and for the Fleet Doctor `ask_` and `prop_` (§2.11–§2.12). Overlay IDs are an exception: the device makes them and the server stores them as given.
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
- `kind` (optional extra: `real`, `sim` or `test`) is read only when the check-in registers a device the cloud hasn't seen; a device that sends none, or something else, registers as `unknown`, which counts as real for safety (never a canary until a person sets its kind with `PATCH /v1/devices/{id}`). **Real hardware sends `kind: "real"` or no kind; only simulators send `"sim"` or `"test"`.** K17 relies on this (a `touches_safety` candidate needs a non-real canary), so it is an **I0 item for the device teammate** (contracts README, open item 7). The fake device sends `"sim"`. No model or `CONTRACT_VERSION` change: `CheckIn` already keeps extra fields.

Response:
```json
{"assigned": {"firmware": 8, "harness": 4}, "drop_overlay": false, "frozen": false, "proposal": null}
```

- `proposal` (optional, Fleet Doctor L2; no version bump, D28) is `{"proposal_id", "incident_id", "expires_at", "sha256"}` when a checked fix is waiting for this device (§2.12), and `null` otherwise. It is always `null` while `frozen`. `sha256` (optional) is the hash of the exact body route 3.9 will serve, so the device can check the pulled bytes against the check-in as well as the response header. A device on an older contract ignores the field, and a malformed pointer reads as `null`: a device that can't parse its check-in would lose `frozen`, the kill switch.

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
- `overlay` is `null` if the agent didn't patch anything. `overlay.proposal_id` (optional, D28) is provenance only. A Fleet Doctor proposal overlay reaches the cloud through its proposal result (§2.13), from which the cloud creates the overlay row, never inside an incident. The device sets `proposal_id` on a *later* incident when the overlay active at that time came from a proposal (for example, the fault recurred under it), and the cloud keeps it in `raw`. It is `null` for the healer's own patches.
- `consult` (optional, Fleet Doctor L2, D28): `{"wanted": true, "trigger": "T2", "attempts": 2}` asks the cloud to work on an unresolved incident. `trigger` is the device's escalation trigger (T1–T7, D26) and `attempts` is how many patches the healer tried. Send `running.code_rev` with it: without one the cloud declines the consult (an event, never a `422`). `true` is shorthand for `{"wanted": true}`. A bad sub-field reads as its default without touching the others (`{"wanted": true, "trigger": true}` → `wanted: true, trigger: null`), and a `consult` that isn't an object reads as `null`; neither rejects the incident. The cloud also starts consults on its own for unresolved incidents whose `suspected_cause` isn't `hardware`, within the caps (D25, D26).

### 2.6 Overlay status

```json
{"status": "held", "reason": "no recurrence for 20 min"}
```

`status` is `held`, `reverted` or `superseded` (a newer firmware or code version installed and replaced the overlay, D7; not a failure).

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
- **`code_patch`** in an overlay (§2.5) is a **unified diff** against the overlay's base `code_rev`, with paths relative to the `device/` root, for example `--- a/control/gripper.py`. It is `null` if the agent only changed rules or params. It changes regular text files only: symlinks and submodules (git modes `120000` and `160000`, read as octal in any spelling, e.g. `0120000`), binary changes and paths outside `device/` are reported as problems, and the patch is treated as touching the safety layer (fail closed, K17). Safety paths are matched case-insensitively after normalization (`SAFETY/x.py` and `./safety/x.py` count as `safety/x.py`), because macOS and Docker Desktop mounts are case-insensitive.

### 2.11 Doctor ask and advice (Fleet Doctor L1)

§2.11–§2.13 are optional additions, so `CONTRACT_VERSION` stays `0.1` (D28). The design is in [`fleet-doctor/architecture.md`](../fleet-doctor/architecture.md) §4–5 and the decisions are D23–D26.

Request (`DoctorAsk`, route 3.8):
```json
{"incident_id": "6d0a4c2e-…", "trigger": "T4",
 "question": "Lowering RELEASE_THRESHOLD_MM keeps moving where the gripper opens instead of stopping it. What am I missing?",
 "context": {"tier": 3, "code_rev": "c0de001",
             "signals": {"dist_mm": [212, 140, 64, 31, 18, 12]},
             "log_tail": ["S HOLDING->RELEASE reason=dist_lt_threshold dist_mm=12"],
             "recent_steps": ["patch control/hold.py: RELEASE_THRESHOLD_MM 25 -> 15", "verify d1_hand_trace: FAIL, released at dist_mm=12"],
             "files": [{"path": "control/hold.py", "excerpt": "…"}]},
 "budget_s": 20, "contract_version": "0.1"}
```

Response (`DoctorAdvice`):
```json
{"ask_id": "ask_5b9e…",
 "hypotheses": [{"claim": "The threshold isn't the bug: SPEC.md says never open the gripper because of the ultrasonic reading while holding.",
                 "how_to_check": "Remove the release branch in step() and run d1_hand_trace: the gripper should stay closed."}],
 "model": "…"}
```

- `incident_id` is optional, because the healer may ask before it reports the incident. `trigger` is an open string; the known values are T1–T7 (D26). Every `context` field is optional, and `recent_steps` holds the healer's last ≤5 messages.
- `budget_s` (default 20) is how long the device waits. The contract clamps it to 3–120 s and reads a non-numeric, non-finite or null value as 20. The cloud's LLM timeout is shorter: `min(DOCTOR_ASK_TIMEOUT_S, budget_s − 2)`.
- The answer is hypotheses, each with how to check it, never a patch. It stays at about 300 tokens in total, which the cloud enforces. A hypothesis that names a safety or visor path is dropped (D25).
- This is a direct call, not the outbox. Any non-200 means "no answer", and the device's circuit breaker skips the rest of that heal's asks (D26).

### 2.12 Proposal (Fleet Doctor L2)

When a consult's fix passes the cloud's checks, the cloud offers it as a proposal, and the next check-in points at it (§2.3 `proposal`). The device pulls it with route 3.9:

```json
{"proposal_id": "prop_8c2e…", "incident_id": "6d0a4c2e-…",
 "base": {"firmware": 1, "harness": 1, "code_rev": "c0de001", "mcu_build": null, "overlay_id": null},
 "code_patch": "--- a/control/hold.py\n+++ b/control/hold.py\n@@ -18,6 +18,5 @@ …",
 "repro_test": "tests/test_hold_proximity.py::test_hand_near_while_holding_keeps_gripper_closed",
 "why": "SPEC.md forbids opening the gripper on the ultrasonic reading while holding; this removes the rule and adds a regression test.",
 "checks": {"stub": false, "fails_before": true, "passes_after": true},
 "expires_at": "2026-11-08T04:20:00Z"}
```

- `code_patch` uses the §2.10 format against `base.code_rev` and includes the reproduction test. `repro_test` names that test (a path or pytest node id): it failed on the base and passes with the patch. `checks` is open and informational.
- `base` is what must be running. The device applies the proposal only when all of these hold: the arm is parked with no task running; `firmware`, `code_rev` (and `mcu_build`, for STM32 patches) match; the active overlay is `base.overlay_id`, which the proposal replaces; and `expires_at` hasn't passed. Otherwise it reports `stale` or `rejected` (§2.13).
- The body is served with `X-Sha256`, the sha256 of the exact body bytes (canonical JSON, as in §3.2). Before applying, the device checks that the hash of the bytes it received equals `X-Sha256`, and equals the check-in pointer's `sha256` when one was sent. The cloud computes both with `proposal_body_and_sha256` on each request, from the same stored proposal, and never serves a stored hash next to a re-dumped body.
- The device applies it as an overlay under a mark-good timer (D24) and reports the result (§2.13). The cloud creates the overlay row from an `applied` result, and overlay statuses (§2.6) then follow as usual.
- A patch that touches the safety layer is never offered, and nothing is offered while the safety paths are unconfirmed (D25).

### 2.13 Proposal result

```json
{"result": "applied", "overlay_id": "ov-d1-0002", "reason": "parked, base matched, sha256 ok; verify d1_hand_trace passed",
 "occurred_at": "2026-11-08T03:41:10Z", "contract_version": "0.1"}
```

- `result` is `applied`, `rejected` (the device declined it: not parked, expired or a hash mismatch), `stale` (the base no longer matches what's running) or `failed_verify` (applied, failed verify and rolled back).
- **The one strict rule:** `applied` must carry a non-blank `overlay_id`, or the request gets a `422`. This result is how a proposal overlay reaches the cloud: the cloud creates the overlay row from it (incidents are write-once and never carry it), so a lost `applied` means no overlay row and no lock-in. Afterwards, an incident reported while that overlay is active carries `overlay.proposal_id` as provenance (§2.5).
- Everything else is lenient, so the result is never dead-lettered: a numeric `overlay_id` reads as a string, a non-string `reason` is stringified (`null` reads as `""`), and a malformed `occurred_at` reads as `null`.
- It is queued in the outbox like an overlay status. The proposal ID is in the URL.

## 3. Device-facing routes

| # | Route | Body → response | Notes |
|---|---|---|---|
| 3.1 | `POST /v1/devices/{device_id}/checkin` | §2.3 → §2.3 response | Every ~10 s. It also serves as the canary health signal |
| 3.2 | `GET /v1/firmware/{version}` | → `{"manifest": §2.1, "sha256": "…"}` | `sha256` is computed over the canonical JSON of the manifest |
| 3.3 | `GET /v1/harness/{version}/package` | → tarball, with header `X-Sha256` | |
| 3.3c | `GET /v1/code/{rev}/package` | → tarball, with header `X-Sha256` | Code packages (§2.10, D21) |
| 3.4 | `POST /v1/devices/{device_id}/installs` | §2.4 → `204`, or `200 {"status": "already_received"}` for a resend | A resend is the same `(type, version, result, occurred_at)`. `422 unknown_version` if the version isn't in the registry. `last_good` moves only for the assigned version; a report older than the newest one of its type is kept but moves nothing |
| 3.5 | `POST /v1/devices/{device_id}/incidents` | §2.5 → `201 {"incident_id"}`, or `200` if already received | Idempotent on `incident_id` |
| 3.6 | `POST /v1/devices/{device_id}/overlays/{overlay_id}` | §2.6 → `204`, or `200 {"status": "already_received"}` for a resend | `superseded` is terminal. After the cloud sets `locked_in`/`dropped`, device statuses are recorded but don't change it. One exception: a `held` overlay (never locked in, so the new version wasn't built from it) stays `held` on `superseded`, since the cloud keeps its diff and re-batches it on the new base; the report is still recorded, and later device statuses don't move it. `superseded` on an `active` overlay is terminal. A status may arrive before its incident |
| 3.7a | `POST /v1/devices/{device_id}/recordings` | §2.8 → `201 {"recording_id", "upload": "needed" \| "exists"}`, or `200` if already received | If a blob with that `sha256` is already stored, no upload is needed. Idempotent on `recording_id`; the incident may arrive before or after (M4) |
| 3.7b | `PUT /v1/recordings/{recording_id}/blob` | raw bytes + `X-Sha256` → `204`, or `409 sha256_mismatch`, or `413 blob_too_large` (over `RECORDING_MAX_BYTES`, default 200 MB) | Idempotent; nothing is kept on a mismatch. Resumable chunked upload is a later improvement ([telemetry](cloud-platform-telemetry.md) §3) |
| 3.8 | `POST /v1/devices/{device_id}/doctor/ask` | §2.11 → `200` §2.11 response | Fleet Doctor L1. A direct call, not queued; the device treats any non-200 as "no answer". `503 doctor_off` (Doctor mode off), `409 fleet_frozen`, `429 doctor_cap` (asks per incident, asks per device per day, or daily spend), `504 doctor_timeout`, `502 doctor_unavailable` (LLM) |
| 3.9 | `GET /v1/devices/{device_id}/proposals/{proposal_id}` | → §2.12, with header `X-Sha256` over the exact body bytes | Fleet Doctor L2. `404 unknown_proposal` (also when it belongs to another device), `410 proposal_gone` (no longer offered, or expired), `409 fleet_frozen` |
| 3.10 | `POST /v1/devices/{device_id}/proposals/{proposal_id}/result` | §2.13 → `204`, or `200` for an identical resend | `404 unknown_proposal`, `409 proposal_result_conflict` (a different result than the one recorded), `422` if `applied` has no `overlay_id` |

**Device rules:** everything in 3.4–3.7 and 3.10 is queued on disk and retried with backoff while the cloud is unreachable (D8). Incidents and their recordings may arrive in either order; the cloud links them (M4).

**Errors:** a `contract_version` outside the supported list → `400 unsupported_contract_version` (required on 3.1, optional elsewhere). Routes 3.4–3.7a and 3.8 from a device that never checked in → `404 unknown_device`; the first check-in registers a device.

## 4. Operator routes

These are your v1 headings, filled out. The dashboard uses the same routes.

### 4.1 CI/CD and DevOps: firmware and harness

| Route | Purpose |
|---|---|
| `POST /v1/firmware` | Create a firmware version by hand; the batch job also creates them. Body `{"manifest": §2.1, "why", "by", "parent_version"}`; `422 unknown_code_rev` if the manifest's `code_rev` isn't registered |
| `GET /v1/firmware`, `GET /v1/firmware/{v}` | List or read |
| `POST /v1/firmware/{v}/retire` | Hide from new assignments (versions are never deleted) |
| `POST /v1/harness` (multipart tarball), `GET /v1/harness`, `GET /v1/harness/{v}`, `POST /v1/harness/{v}/retire` | Same, for harness packages (CRUD on the embedded SRE) |
| `POST /v1/code` (multipart tarball), `GET /v1/code`, `GET /v1/code/{rev}` | Code versions: seeded by hand, then created by the coding agent (D21) |
| `GET /v1/devices`, `POST /v1/devices`, `PATCH /v1/devices/{id}` | Register devices; set `role` (normal/canary), `device_class` and `kind` (real/sim/test/unknown; an `unknown` device can't be a canary: `409 unknown_kind`) |

### 4.2 Harness updates: incidents, batch and gate

| Route | Purpose |
|---|---|
| `GET /v1/incidents?device=&kind=&since=` | Incident list (pushed logs land here and in §4.3) |
| `GET /v1/incidents/{id}/story` | One incident's full chain, built from events (D10) |
| `POST /v1/batch/run` | Run the batch job now (it also runs on a timer). Body `{"by": "kent"}` (optional) → `{"requested", "changed", "why"}`; the worker runs it on its next tick |
| `GET /v1/candidates`, `GET /v1/candidates/{id}` | Candidates and their gate state; one candidate also carries its `events` |

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
| `POST /v1/fleet/freeze`, `POST /v1/fleet/unfreeze` | Kill switch (D11). Body `{"by": "kent", "why": "…"}` (`reason` is accepted too); `by` may be bare or `human:kent`, default `operator`. → `{"frozen", "changed", "why", "pinned"}`. Freeze pins each device to its `last_good`, and a canary running an in-flight candidate to what it had before that candidate (with none on record: the class's promoted release, else what its normal devices run; never the candidate). The `fleet.frozen` event keeps a snapshot of each device's pre-freeze assignment. Unfreeze restores that snapshot, except a device type a person reassigned during the freeze (a `human` `device.assigned` event since the freeze), which keeps the person's choice and is named in the why; a device with no snapshot goes to its class's latest promoted release. A second press changes nothing and writes no event |
| `GET /v1/work-orders?status=`, `GET /v1/work-orders/{id}` | Work orders and their Naïve card status; the single read adds the order's events |
| `POST /v1/work-orders/{id}/resolve` | A person resolves an order waiting for one (D19b). Body `{"action": "approve" \| "deny", "by": "kent", "why": "…"}`. `by` and `why` must be non-blank (`422`). `approve` re-queues an order with no card (the card frozen on the first attempt is resent with the same `Idempotency-Key`; refused for an order created in another `NAIVE_MODE`), or accepts as `done` a card that Naïve finished (`done`) but that came back `needs_human`/`stopped`. `deny` closes it as `denied`, only for an order waiting for a person whose card (if any) is finished, or a card-less `pending` one. `409` otherwise, with the reason (e.g. "close card crd_… on the board first") |
| `GET /v1/digest?since=&until=` | Counts for the morning digest. `since`: ISO time or `30m` / `12h` / `2d` ago; default the overnight start, else 12 h. JSON counts plus `text` (what `make digest` prints) and `storage` (blob bytes vs budget) |
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
| Devices | `device.first_seen`, `device.registered`, `device.assigned`, `device.updated`, `device.running_changed`, `device.health_changed`, `device.overlay_changed`, `install.reported` |
| Versions | `firmware.version_created`, `harness.version_created`, `code.version_created` |
| Incidents | `incident.reported`, `incident.agent_acted`, `overlay.applied`, `overlay.held`, `overlay.reverted`, `overlay.superseded`, `overlay.locked_in`, `overlay.dropped` |
| Batch and gate (events for overlays and candidates whose diff touches safety-layer or visor paths carry `touches_safety: true` in `refs`; D5a, K17) | `batch.requested`, `batch.ran`, `candidate.created`, `candidate.replayed`, `candidate.canary_started`, `candidate.promoted`, `candidate.rolled_back`, `candidate.frozen`, `candidate.restarted` (unfrozen; back to replay), `candidate.awaiting_canary`, `candidate.stale` (its base was replaced; its overlays go back to `held`), `candidate.error`, `batch.error`, `overlay.deferred` (stays held for the next batch), `overlay.requeued`, `overlay.lockin_rolled_back` and `overlay.lockin_error` (sticky cloud statuses; the device keeps its fix). A candidate folded from several incidents lists them in `refs.incidents` |
| Coding agent (D21) | `coder.started`, `coder.drafted`, `coder.tests_passed`, `coder.tests_failed`, `code.version_created` |
| Fleet | `fleet.frozen`, `fleet.unfrozen`, `fleet.overnight_started`, `fleet.overnight_stopped` (M7: `cli overnight start/stop`; `reset` refuses while started) |
| Work orders (each carries `refs.work_order`, `refs.incident` and `refs.status`; changes add `refs.from`/`refs.to`) | `workorder.created`, `workorder.card_posted`, `workorder.status_changed`, `workorder.post_retried` (a retryable Naïve error; the order stays `pending`), `workorder.post_held` (not posted while the fleet is frozen; once per order), `workorder.card_updated` (Naïve changed the card but the platform status stayed, e.g. a new comment on a parked card) |
| Data | `recording.received`, `recording.blob_stored`, `recording.linked` (an incident arrived after its recording), `recording.blobs_pruned` (M7: the disk budget deleted the oldest unlabeled episode blobs; one event per batch), `label.changed`, `dataset.snapshotted`, `export.built` |

## 6. Where your v1 list went

| v1 (Kent's first draft) | v2 |
|---|---|
| CI/CD and DevOps · CRUD on firmware | §4.1 firmware routes. Create, read and retire, since versions are never edited |
| CRUD on self-healing agent harness (embedded SRE) | §4.1 harness routes |
| Harness Updates · Push logs from edge to cloud | §3.5 incidents + §3.7 recordings (logs, sensor data, agent traces) |
| Batch jobs for updating harness | §4.2 `POST /v1/batch/run` + the worker timer; candidates for both firmware and harness |
