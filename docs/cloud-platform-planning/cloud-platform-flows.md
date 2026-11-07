# Cloud platform: core flows and happy paths

Eleven flows define "working" for the cloud platform. Each one is:
- a user-visible path, written as numbered steps;
- a list of **checks** that must hold;
- one flow test in `cloud/tests/flows/` with the same ID.

The build plan schedules the flows ([`cloud-platform-build-plan.md`](cloud-platform-build-plan.md)), and the PRD traces each one to a story ([`cloud-platform-prd.md`](cloud-platform-prd.md) §8).

**Update the tracker at every checkpoint.** It's the single place that says what works.

---

## Tracker

Status: ⬜ not started · 🟨 in progress · 🟩 passing with fake devices / fake adapters · ⭐ demo-ready (real arm or live Naïve, rehearsed)

| Flow | Priority | Milestone | Test | Fake | Real / live | Notes |
|---|---|---|---|---|---|---|
| **F1** Version sync | P0 | M1 | `test_f1_version_sync.py` | ⬜ | ⬜ | |
| **F2** Incident → story | P0 | M1–M2 | `test_f2_incident_story.py` | ⬜ | ⬜ | |
| **F3** Lock-in → fleet | P0 | M3 | `test_f3_lock_in.py` | ⬜ | ⬜ | Hero demo beat 2 |
| **F4** Bad candidate → rollback | P0 | M3 | `test_f4_rollback.py` | ⬜ | ⬜ | Hero demo beat 3 |
| **F5** Kill switch | P0 | M3 | `test_f5_kill_switch.py` | ⬜ | ⬜ | |
| **F6** Hardware → work order → Naïve | P0 | M5 | `test_f6_work_order.py` | ⬜ | ⬜ | Mobilerun sponsor track |
| **F7** Telemetry sync | P0 | M4 | `test_f7_telemetry.py` | ⬜ | ⬜ | |
| **F8** Dataset snapshot and export | P1 | M6 | `test_f8_datasets.py` | ⬜ | ⬜ | Uses overnight data |
| **F9** Offline backfill | P1 | M7 | `test_f9_offline.py` | ⬜ | ⬜ | |
| **F10** Overnight unattended run | P0 | M7 | manual + `make digest` | ⬜ | ⬜ | Starts by ~12:30 AM |
| **F11** Code patch → coding agent → candidate | P1 (first) | M8 | `test_f11_coding_agent.py` | ⬜ | ⬜ | Starts when blockers B1–B7 clear (build plan §3, M8) |

**Shared setup:** `make seed` creates firmware v1, harness v1, `arm-1` (real, normal), `sim-1` (normal), `sim-2` (canary) and `sim-3` (normal), all in `device_class = so101`.

---

## F1. Version sync (P0, US1, R1–R2)

**Starting point:** shared setup. The device has never checked in.

1. The device checks in (`running: null`). The response assigns firmware v1 and harness v1.
2. The device pulls firmware v1 (manifest plus `sha256`) and harness v1 (tarball plus `X-Sha256`).
3. The device checks the hashes, installs, runs its health check, and reports `installed`.
4. The next check-in reports `running: {firmware: 1, harness: 1}`.

**Checks:**
- `running` equals `assigned`, and `last_good` is set to v1.
- Events: `device.first_seen`, `install.reported`. Routine check-ins write **no** event; only changes do (new version, health change, overlay change).

**Unhappy paths (P1):**
- A hash mismatch is reported as `hash_mismatch`; `running` and `last_good` stay unchanged and an event records it.
- A failed health check after install is reported as `rolled_back`; the device stays on `last_good`.

## F2. Incident → story (P0, US3, R3, R10)

**Starting point:** F1 done.

1. The device sends an incident report: `camera_occluded`, `resolved: true`, overlay `ov-12` raising the threshold, the agent's reasoning, its actions and the recorded `signals`.
2. Later, the device reports overlay `ov-12` as `held`.

**Checks:**
- The incident appears on the timeline within about 5 s.
- Its story shows, in plain English: what broke → what the agent did → overlay → held.
- Sending the same report again returns `200` with the same incident and creates no duplicate.
- A report that arrives late is placed by `occurred_at`.

**Demo beat:** step 1 of the demo (v0 plan §8).

## F3. Lock-in → fleet (P0, US2, R5, R6)

**Starting point:** F2 done, with `ov-12` held.

1. The batch job runs (on its timer, or `POST /v1/batch/run`). It creates candidate firmware v2 (v1 + the `ov-12` diff) with an LLM-written `why`, and candidate harness v2 with a new skill.
2. The gate replays: v2's rules fire the resolving action on `ov-12`'s signals (and on the regression corpus once F8 exists).
3. The gate assigns v2 to `sim-2` (the canary). `sim-2` installs it and stays healthy for `CANARY_WINDOW_S`.
4. The gate promotes v2: every device in `so101` is assigned v2 and pulls it. `ov-12` becomes `locked_in`.

**Checks:**
- Each gate step writes an event with a `why`.
- The incident's story ends with "→ locked into firmware v2 → canary ✓ → fleet".
- Every device runs v2, and no device kept the old overlay.
- No device other than the canary was assigned v2 before promotion.

**Demo beat:** step 2.

## F4. Bad candidate → rollback (P0, US2, R6)

**Starting point:** a held overlay from a fake device in **bad-candidate mode**. After installing the candidate, that device repeats the same kind of incident, or reports `health: failing`.

1. The batch job creates candidate v3. Replay passes.
2. The gate assigns v3 to the canary. The canary reports the repeat incident or a failing health check.
3. The gate rolls back: the canary is assigned its `last_good`, and v3 is marked `rolled_back` with the reason.

**Checks:**
- No non-canary device was ever assigned v3.
- The reason is visible on the timeline ("canary sim-2 repeated camera_occluded 40 s after install").
- The canary is back on its last good version.

**Demo beat:** step 3.

## F5. Kill switch (P0, US4, R7)

**Starting point:** a candidate is in its canary window.

1. The operator presses **Freeze** on the dashboard.
2. The fleet is frozen: every device is assigned its `last_good`, the candidate becomes `frozen`, and check-in responses carry `frozen: true`.
3. The operator presses **Unfreeze**. Frozen candidates restart from replay.

**Checks:**
- Each press writes an event with actor `human:<name>`.
- While frozen, the only assignment changes are the pins to `last_good`.
- The batch job may still create candidates, but they wait.

**Demo beat:** step 5.

## F6. Hardware → work order → Naïve (P0, US3a, R8)

**Starting point:** for 🟩, `NAIVE_MODE=fake`. For ⭐, live Naïve with the ops agent and the Mobilerun MCP set up ([Naïve integration](cloud-platform-naive-integration.md)).

1. The device reports `gripper_stall`, `resolved: false`, `suspected_cause: hardware`. Its overlay reverted itself.
2. The platform creates work order `wo_…` ("replace servo 3 on arm-1").
3. The worker posts a Naïve board card with header `Idempotency-Key: wo_<id>`, assigned to `fleet-ops`. The card body follows the template in [Naïve integration](cloud-platform-naive-integration.md) §3.3.
4. The worker polls the card by id and shows its status in the incident's story. A `RESULT: ordered ORDER_REF=…` note means done; anything else means `needs_human`.

**Checks:**
- Exactly one card per work order, even after retries.
- The card body is self-contained: device, part, symptom, incident summary and the platform's link.
- The status shows on the timeline.
- **(Live only)** The Naïve agent picks up the card, a Mobilerun call is made, and the card's comments or status show the result.

**Demo beat:** step 4.

## F7. Telemetry sync (P0, US6, R4)

**Starting point:** F2.

1. Alongside the incident, the device uploads recordings, each as metadata first and then the blob ([telemetry](cloud-platform-telemetry.md) §2):
   - the **incident window**: joints, servos and a low-res camera for about ±30 s, as a LeRobotDataset v3.0 tar;
   - the **log window**, as MCAP;
   - the **agent trace**, as JSONL.
2. **(P1)** It also uploads sampled normal task **episodes**.

**Checks:**
- Each blob is stored once by `sha256`; uploading it again changes nothing.
- Each recording is indexed with its device, firmware and harness versions, overlay, incident and time range.
- The incident's story shows evidence links (view or download).
- Automatic labels are applied: incident kind, `resolved` or `escalated`, and `success` or `failure` for episodes.

## F8. Dataset snapshot and export (P1, US7, US9, R9)

**Starting point:** recordings and incidents exist (ideally from the overnight run).

1. The operator filters recordings (e.g. `kind = incident_window`, `label = gripper_stall`) and adds a manual label to one.
2. The operator creates dataset snapshot `ds_… v1` for a purpose (`firmware`, `harness` or `policy`).
3. The operator requests an export: `regression_corpus`, `traces_jsonl`, or (P2) `lerobot`. The worker builds it and the operator downloads it.
4. From now on, the gate's replay runs each candidate against the **regression corpus** (all past resolved incidents).

**Checks:**
- A snapshot never changes, even if labels change later.
- The same snapshot always produces the same export file.
- Replay events say how many incidents were checked ("replayed 37 incidents; 37 pass").

## F9. Offline backfill (P1, US8, R2, R4)

1. Stop the `api` container while fake devices keep running and faulting.
2. Restart it.

**Checks:**
- Every incident and recording queued on the devices arrives exactly once.
- Stories are ordered by `occurred_at`.
- No duplicate events.

## F10. Overnight unattended run (P0, R12)

1. Set `CANARY_WINDOW_S=1200` and `BATCH_INTERVAL_S=3600`. Set `NAIVE_MODE=fake`, or keep it live only if `parts.yaml` points at a teammate's phone and the daily order cap is set. **Never** let the chaos loop call a real business (build plan K13).
2. Run `make sim` with the chaos schedule:
   - a fault every ~10 minutes, spread across the devices;
   - about 1 in 5 held overlays are bad (F4);
   - a hardware fault about every 2 hours (F6).
3. Leave it running for at least 6 h.
4. In the morning, run `make digest`.

**Checks:**
- `api` and `worker` stayed up, or recovered on their own (Compose `restart: unless-stopped`).
- The digest counts incidents, heals, promotions, rollbacks, work orders and human actions (target: 0).
- The blob store stayed under `BLOB_BUDGET_GB`.

**Demo beat:** step 6, the overnight numbers.

## F11. Code patch → coding agent → candidate (P1, D21, D21a)

**Starting point:** M8's blockers are cleared (build plan §3, M8). A device reports an incident whose overlay carries a `code_patch` (a unified diff against `code_rev`), and later reports it `held`.

1. The batch job sends the patch, the incident and the device code at `code_rev` to the coding agent in the `coder` sandbox.
2. The coding agent writes a clean change and runs the device test suite. The platform stores the result as code version `rev+1`, a tarball with its sha256.
3. A candidate is created with a manifest whose `code_rev` points to the new version. Its `why` holds the coding agent's summary and the test results.
4. The gate first runs the device tests in the sandbox, then canaries the candidate on a device that runs real code (the sim twin). If it's healthy, the candidate is promoted.

**Checks:**
- A code change never reaches a device without passing its tests and a real-code canary.
- If no real-code canary exists, the candidate stops at `awaiting_canary`, and the timeline says why.
- The coding agent's diff, test output and summary can be read from the incident's story.
- The coding agent never edits the manifest; the merge does (PRD principle 7).

---

## Demo walk-through order (rehearse this)

F2 (the judge covers the camera) → F7 (show the evidence) → F3 (lock-in) → F4 (a bad candidate rolls back) → F6 (the servo work order goes to Naïve and Mobilerun) → F5 (kill switch) → F10 (overnight numbers) → F8 (the dataset the night produced).
