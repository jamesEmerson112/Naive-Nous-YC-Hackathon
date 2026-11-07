# Cloud platform: telemetry and datasets

- **Status:** researched and approved Sat Nov 7 (**D20**, [decision log](cloud-platform-decisions.md)).
- **Already decided:** robot telemetry is a first-class, general-purpose asset (D16).
- **Payloads and routes:** [`cloud-platform-apis.md`](cloud-platform-apis.md) §2.8 and §3.7.
- **Marking:** facts cite a source with its version and date (checked 2026-11-07). *(inferred)* marks our own reasoning.

---

## 1. Why this exists

Every incident the heal loop handles leaves evidence behind: what the arm saw and felt, what its software logged, and what the agent thought and did. D16 makes that evidence an asset in its own right, with three uses:

| Use | What it needs | What it improves |
|---|---|---|
| **Firmware** | A regression corpus: each past incident's signals plus the action that fixed it | Every new version is replayed against every past incident before it reaches an arm (US9). The gate gets stricter as the fleet fails *(inferred)* |
| **Harness** | Agent traces labeled with what happened next (held, reverted, escalated) | Prompts, skills and evals for the embedded agent |
| **Policy** | Task episodes and incident windows in LeRobot format, labeled success, failure or intervention | Training data for the arm's policy model. Training itself is a non-goal (D13) |

The regression corpus is what connects the data to the loop. The more the fleet fails, the stricter the gate becomes, so the heal loop and the data asset reinforce each other *(inferred)*.

## 2. What gets captured

| Kind | Contents | Format | When | Size target | Priority |
|---|---|---|---|---|---|
| `incident_window` | Joints, actions, servo temperature, load and voltage, a low-res camera, reflex flags, from ~30 s before to ~30 s after the fault | **LeRobotDataset v3.0**: one small dataset per incident, finalized, then tarred (`lerobot_v3_tar`) | Every incident | ≤ 50 MB | P0 |
| `log_window` | Control-stack logs, servo diagnostics (~100 Hz), reflex decisions, overlay changes | **MCAP**: Foxglove `Log` schema for logs, JSON-schema channels for diagnostics (`mcap`) | Every incident | ≤ 20 MB | P0 |
| `agent_trace` | The embedded agent's session: messages, tool calls, results | **JSONL** from the agent's own session export (`jsonl`) | Every incident the agent touched | ≤ 5 MB | P0 |
| `episode` | One normal task attempt, same fields as `incident_window` | `lerobot_v3_tar` | Sampled, e.g. 1 in 5 normal episodes, for negatives | ≤ 50 MB | P1 |
| metrics | Check-in numbers: task success rate, maximum servo temperature, bus voltage | Rows in Postgres (`metric_samples`), no blob | Every check-in (~10 s) | tiny | P1 |

**Why these formats:**

- **LeRobotDataset v3.0** is what LeRobot v0.7.0 writes (`CODEBASE_VERSION = "v3.0"`).
  - Layout: `meta/info.json`, `meta/stats.json`, `meta/tasks.parquet`, `meta/episodes/…parquet`, `data/…parquet`, `videos/<cam>/…mp4`.
  - Custom per-frame features are allowed.
  - `finalize()` must run before the parquet files are valid.
  - `lerobot-edit-dataset` can delete episodes, split and merge, but **merging needs identical features** in every dataset.
  - Sources: [dataset_metadata.py @ v0.7.0](https://github.com/huggingface/lerobot/blob/v0.7.0/src/lerobot/datasets/dataset_metadata.py), [v3 docs](https://huggingface.co/docs/lerobot/lerobot-dataset-v3), [dataset tools](https://huggingface.co/docs/lerobot/using_dataset_tools).
- **One small dataset per incident** is safer than one long recording. A process that dies before `finalize()` corrupts only that incident's dataset, and the platform merges incidents later *(inferred, from the finalize requirement)*.
- **MCAP** is an append-only, self-describing log container that can be indexed and compressed. It is the ROS 2 default, and a truncated file can be repaired with `mcap recover` ([mcap.dev](https://mcap.dev/), [CLI](https://mcap.dev/guides/cli)).
  - Python writers: `mcap` 1.6.0 (2026-10-19) and `foxglove-sdk` 0.30.3 (2026-10-26) (PyPI).
  - It suits logs and diagnostics, but it isn't a training format *(inferred)*.
- **Agent traces.** The device's embedded agent is **not Hermes**: it's a small local model with a small harness (pi or mini-swe-agent being tested). Source: device side, `docs/project-overview.md`; D4 History. So the trace comes from that harness's own transcript, exported as JSONL. The Hermes facts below still apply to the bench's Hermes agent, which drives the arm for job-board tasks (`hub/`):
  - Hermes stores sessions in SQLite at `~/.hermes/state.db`.
  - `hermes sessions export --format jsonl|trace --redact` exports one session ([sessions](https://hermes-agent.nousresearch.com/docs/user-guide/sessions)).
  - Plugin hooks (`on_session_start`, `post_llm_call`, `post_tool_call`, `on_session_end`) can stream it live ([hooks](https://hermes-agent.nousresearch.com/docs/user-guide/features/hooks)).
  - Its outbound webhooks retry only once and drop events when their queue is full, so **the session export is the record of truth**, not the webhooks.

## 3. Sync: device → cloud

**The device side (the teammate's, D14):**

1. **Black box.** An always-on ring buffer of the last ~30–60 s.
   - If the arm runs under `lerobot-rollout`, its `highlight` strategy is already a ring buffer (default 10 s, 1024 MB), and a custom `lerobot_strategy_*` package can trigger it on faults instead of keystrokes ([rollout configs @ v0.7.0](https://github.com/huggingface/lerobot/blob/v0.7.0/src/lerobot/rollout/configs.py)).
   - If the arm runs a scripted stack, a `deque` plus `LeRobotDataset.create/add_frame/save_episode/finalize` does the same job *(inferred)*.
   - Store JPEG or low-res frames in the buffer. Raw 640×480 RGB at 30 fps for 60 s is about 1.66 GB.
2. **On a fault:** write out the buffer, keep recording the post-fault window, finalize the per-incident dataset, and tar it. Close the MCAP log window. When the agent's session ends, export its trace.
3. **Outbox.** Each file goes into an on-disk outbox. The device sends the incident report first, then each recording (D8, APIs §3). Retries back off; nothing is deleted until the server confirms it.

**The upload protocol (APIs §3.7):**

1. `POST /v1/devices/{id}/recordings` with metadata, including `sha256`.
   - If the server already has a blob with that hash, it answers `upload: "exists"` and the device is done.
2. Otherwise, `PUT /v1/recordings/{id}/blob` with the raw bytes and `X-Sha256`.
   - The server checks the hash and stores the blob under its hash. Sending it again is harmless.
3. **Later:** chunked, resumable upload (S3 multipart or tus) for files over ~200 MB. v0 stays under that by design *(inferred)*.

This is the same pattern Foxglove's on-device agent uses: watch a directory, upload, and keep files until the upload succeeds ([Foxglove manage data](https://docs.foxglove.dev/docs/foxglove-agent/manage-data)).

## 4. Index (Postgres)

These tables extend the v0 plan's §4. Blobs live on disk (§7), and only metadata lives in Postgres.

| Table | Key fields | Notes |
|---|---|---|
| `recordings` | `id` (device UUID), `device_id`, `kind`, `format`, `sha256`, `size_bytes`, `started_at`, `ended_at`, `firmware`, `harness`, `overlay_id`, `incident_id`, `episode` (JSONB), `streams` | One row per uploaded file. A blob shared by two recordings is stored once |
| `blobs` | `sha256` (PK), `size_bytes`, `media_type`, `path`, `created_at` | Content-addressed |
| `labels` | `target_type` (recording/incident), `target_id`, `key`, `value`, `source` (auto/agent/human/robometer), `author`, `confidence`, `created_at` | **Append-only.** Removing a label writes a tombstone row. The current labels are a view |
| `metric_samples` | `device_id`, `ts`, `metrics` (JSONB) | From check-ins (APIs §2.3) |
| `datasets` | `id`, `name`, `purpose` (firmware/harness/policy), `filter` (JSONB) | A named, saved query |
| `dataset_versions` | `dataset_id`, `n`, `recording_ids[]`, `incident_ids[]`, `manifest_sha256`, `created_at` | **Immutable snapshot.** Later label changes don't touch it |
| `exports` | `id`, `dataset_id`, `n`, `format`, `status`, `path`, `sha256` | Built by the worker |
| `regression_cases` | `incident_id`, `signals`, `expected_action`, `kind`, `added_at`, `last_result` | One per resolved incident. Feeds the gate's replay (§6.1) |

## 5. Curation

**Automatic labels** (`domain/labels.py`, `source = auto`) are applied when a recording or incident arrives:

| Label | Comes from |
|---|---|
| `kind:<incident kind>` | The incident report |
| `outcome:resolved` / `outcome:escalated` | `outcome.resolved` and work-order creation |
| `overlay:held` / `overlay:reverted` / `overlay:locked_in` | Overlay events |
| `cause:software` / `cause:hardware` | `outcome.suspected_cause` |
| `episode:success` / `episode:failure` | The episode's own `success` |
| `intervention` | Any frame with `intervention = true` (the convention of LeRobot's `dagger` strategy, [dagger.py @ v0.7.0](https://github.com/huggingface/lerobot/blob/v0.7.0/src/lerobot/rollout/strategies/dagger.py)) |
| `firmware:v<N>` | The running version |

**Manual labels** (`source = human`) are added on the Data page or with `POST /v1/recordings/{id}/labels`.

**Snapshots:**
- A dataset is a saved filter, for example `{"kind": "incident_window", "labels": ["kind:gripper_stall"], "since": "…"}`.
- Taking a snapshot freezes the filter's current matches into `dataset_versions` with a manifest hash.
- An export is always built from a snapshot, so it can be rebuilt exactly.

## 6. Exports

### 6.1 `regression_corpus` (firmware; P1, and it feeds the gate)

```
regression_corpus_ds3_v2/
├── manifest.json                 # snapshot id, versions, case count
└── cases/<incident_id>/
    ├── case.json                 # kind, signals, expected_action, firmware at the time
    ├── log_window.mcap           # evidence (optional for replay)
    └── incident_window.tar       # evidence (optional for replay)
```

- **v0 replay is open-loop:** `romulus_contracts.rules.evaluate(candidate.rules, case.signals)` must fire `expected_action` for every case (APIs §2.7). The gate reads `regression_cases` directly, and this export is the portable copy.
- **Later: closed-loop re-simulation** in MuJoCo's `robotstudio_so101`. Applied Intuition's log-management handbook draws the open-loop/closed-loop distinction ([pt 3](https://appliedintuition.com/blog/log-management-handbook-part-3)).
- **Gap:** v0 has only positive cases, where the rule should fire. Negative cases, where nothing should fire, come from sampled normal episodes in P2. Until then, a candidate that fires too often isn't caught by replay; the canary catches it *(inferred)*.

### 6.2 `traces_jsonl` (harness; P1)

One line per agent session, in the ShareGPT-style shape Hermes uses for trajectories (`conversations[{from, value, tool_calls}]`, `completed`) ([trajectory format](https://hermes-agent.nousresearch.com/docs/developer-guide/trajectory-format)). Each line adds:

```json
{"incident_id": "…", "overlay_diff": {…}, "overlay_outcome": "held", "locked_into": 8, "work_order": null}
```

An `evals.jsonl` sits alongside, one line per incident: {evidence summary, expected diagnosis, expected action}. It's for testing prompts and skills.

### 6.3 `lerobot` (policy; P2)

1. Merge the snapshot's per-incident and episode datasets with `lerobot-edit-dataset --operation.type merge`. This needs the frozen schema (§8).
2. Optionally `split` into train and validation.
3. Write per-episode labels to a sidecar `snapshot_manifest.parquet`, because LeRobot has no first-class per-episode metadata API (from the v0.7.0 source).

Pushing to a private HF Hub repo tagged with the snapshot id is a non-goal this weekend.

## 7. Storage and disk budget

- **Blob store:** content-addressed files at `data/blobs/sha256/ab/cd/<hash>`, behind the `BlobStore` interface ([architecture](cloud-platform-architecture.md) §4). LeRobot tools and Rerun all want local directories anyway.
- **Later, for S3 compatibility:** Garage v2.5.0 (2026-10-24, AGPL) has a single-node mode ([quick start](https://garagehq.deuxfleurs.fr/documentation/quick-start/)); SeaweedFS 4.52 (`weed mini`, Apache-2.0) is the alternative.
- **Don't use `minio/minio` images.** The MinIO community repo is archived and says it is no longer maintained (GitHub, checked 2026-11-07).
- **Budget:** `BLOB_BUDGET_GB` (default 20). `telemetry.enforce_budget()` deletes the oldest `episode` blobs first, and **never** deletes incident evidence or anything in a dataset snapshot.

## 8. Fleet dataset schema v0.1 (a contract with the device teammate)

Every `incident_window` and `episode` dataset uses the same LeRobot features, so the platform can merge them. **Freeze this tonight** (build plan K14).

| Feature | dtype · shape | Notes |
|---|---|---|
| `observation.state` | float32 · [6] | Joint positions (SO-101, 6 servos) |
| `action` | float32 · [6] | Commanded positions |
| `observation.images.front` | video | Low-res (e.g. 320×240 at 15 fps) |
| `servo_temp` | float32 · [6] | °C |
| `servo_load` | float32 · [6] | |
| `servo_voltage` | float32 · [6] | V |
| `fault_code` | int32 · [1] | 0 = none |
| `reflex_active` | bool · [1] | A rule fired this frame |
| `intervention` | bool · [1] | A human took over (dagger convention) |

`fps`, `robot_type = so101_follower` and the task string go in `meta/info.json` and the tasks table, as LeRobot does. Adding or removing a feature bumps the contract version.

## 9. Viewing

- **v0:** the incident story lists its recordings with kind, time range, size, and a download link. The MCAP and LeRobot files open in desktop tools.
- **P2:** generate one `.rrd` per incident and embed the Rerun web viewer.
  - Rerun opens MCAP directly and has a built-in LeRobot v3 loader. rerun-sdk is at 0.42.0 (2026-11-04).
  - The viewer version must exactly match the SDK version that wrote the `.rrd`, so pin both ([embed](https://rerun.io/docs/howto/integrations/embed-web), [LeRobot loader](https://rerun.io/docs/howto/logging-and-ingestion/lerobot)).
  - It's unconfirmed whether Rerun decodes JSON-encoded MCAP channels ([MCAP how-to](https://rerun.io/docs/howto/mcap)).

## 10. Privacy

- Camera frames at the venue will include **judges and other people**. Keep frames low-res, record no audio, keep everything on the laptop, and push nothing to a public hub *(inferred)*.
- Export agent traces with `--redact` (Hermes supports it, [sessions](https://hermes-agent.nousresearch.com/docs/user-guide/sessions)).
- Datasets can hold people's images, so exports are for our own use.

## 11. Risks and open questions

| Risk or question | Mitigation or owner |
|---|---|
| Telemetry eats the hero-loop time (build plan K1) | P0 is incident evidence only; episodes P1; datasets after the overnight run; LeRobot export P2 |
| The feature schema drifts, so `merge` fails | Freeze §8 tonight; version it |
| Ring-buffer RAM (raw video) | Low-res or JPEG frames (§3) |
| A dataset dies before `finalize()` | One small dataset per incident (§2) |
| LeRobot v0.7.0 changed the Feetech motor bus (now rustypot `SerialMotorsBus`) and could break existing SO-101 code ([release](https://github.com/huggingface/lerobot/releases/tag/v0.7.0)) | Teammate; pin versions |
| Clock skew between arm and laptop | Store both monotonic and wall-clock time; NTP; sort by `occurred_at` |
| **Open:** is the arm driven by `lerobot-rollout` with a policy, or by a scripted stack? That decides whether the `highlight` and `dagger` strategies apply | Teammate |
| ~~Is the embedded agent Hermes?~~ **No:** a small local model with the pi or mini-swe-agent harness (D4 History). Still open: that harness's transcript format, mapped to JSONL | Teammate |
