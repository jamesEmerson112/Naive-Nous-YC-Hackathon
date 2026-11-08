# Fleet learning: design

_v2, Sun Nov 8, ~8:55 AM PST, after three reviews ([`review.md`](review.md)). Repo citations are `path:line` at `origin/main` (`17f23be` or later). `N:<n>` is a line in `docs/raw/naive-llms-full.txt` and `H:<n>` a line in `docs/raw/hermes-llms-full.txt`. Outside facts are in [`research.md`](research.md). *(inferred)* marks our own reasoning. Nothing here is built._

## 0. Rules
1. **The batch takes held fixes; cluster review takes failures with no held fix behind them.** Nothing here repeats the batch's lock-in or skill writing (`cloud/src/romulus_cloud/services/batch.py:701-740`).
2. **Only a verifier labels.** A model never grades its own run. Training on unchecked model output leads to collapse ([`research.md`](research.md) §6), and constitution L1.9 says LLMs sloppify what they touch.
3. **Route by gap type:**
   - a **missed detection** becomes a **rule**, built by plain code from a probe;
   - a **failed heal** becomes a **skill** (prose).
   - The LLM writes prose and skills only (`cloud/CLAUDE.md:16`). It never writes rule code.
4. **Cluster review is a Fleet Doctor mode, not a new agent.** It inherits the Doctor's limits (D25: out of safety, caps, `frozen` stops it, evidence counts as untrusted data) and its spend ledger. Draft D31 (§8) settles the one difference.
5. **No new table.** Labelled runs are a pure view; proposals live in events (`cloud/CLAUDE.md:35`). Schema changes stay additive (`:80-87`).
6. **Fakes first; stubs announce themselves.** The loop runs offline with `FixtureNaive`. The harness lane's stub replay says "stub" on every surface.

## 1. Components
| # | Component | Job | Files (new unless marked) |
|---|---|---|---|
| A | **Labelled runs** | Give every heal attempt one verdict from a verifier | `domain/runs.py` (pure verdict), `services/runs.py` (loader over `agent_runs`, `incidents`, `overlays`, `candidates`) |
| B | **Fleet dataset** | Keep one standing dataset snapshotted and exported as training rows, with no person involved | `domain/datasets.py` (one format), `services/datasets.py` (`actor_class` parameter, agent runs in `freeze_context`, the new files): both existing |
| C | **Cluster review** | Turn a failure cluster into one checked proposal, routed to the right lane | `domain/review.py` (schema, checks, routing; pure), `services/review.py`, one `worker.py` Step, `settings.py`, `cloud/scripts/review_spike.py` |
| D | **Probe** | Measure a proposed rule offline on labelled windows | `cloud/scripts/lesson_probe.py` → `data/learning/probes/<probe>.json` (runs on the host) |
| E | **Page** | Show labelled runs, the dataset, and the newest proposal ([`demo.md`](demo.md) §3) | `services/learning.py` (`view()`), `templates/learning.html`, one route in `api/web.py`, a nav link |

**Labelled runs are the spine, not just this feature's input.** The digest's repeat rate (§7), the Doctor's escalation outcomes, the Technician's trigger and James's Level 3 training set (`docs/healer-data-levels.md:94`) can all read the same verdicts *(inferred: one primitive, many readers)*.

## 2. Labelled runs
```python
@dataclass(frozen=True)
class LabelledRun:
    id: str                  # "run:<device>/<run_id>" or "inc:<incident_id>" (fake-arm fallback)
    device_id: str; device_class: str; device_kind: str   # kind "sim" → SIM badge everywhere
    kind: str                # fault kind; a run takes its first linked incident's
    started_at: datetime
    model: str | None
    versions: dict           # {"firmware", "harness", "mcu_build"} when known
    steps: tuple[dict, ...]  # {thought, tool, args, effect, ok}: report.steps, or the agent_trace fallback
    verdict: str             # pass | fail | unverified | excluded
    verifier: str            # a sentence: "gate promoted harness v4 (canary held 120 s)"
    incident_id: str | None; run_id: str | None
```

**Sources:**
- `agent_runs` first (`cloud/CLAUDE.md:45-50`).
- For incidents with no run (the fake arms), `Incident.agent` plus the `agent_trace` recording, which `cloud/CLAUDE.md:48-50` allows.
- An incident that has a run yields one labelled run, from the run.

The name avoids "episode", which already means a LeRobot task recording (APIs §2, D20).

**The verdict** is a pure function, `verdict(run, incident, overlay, candidates) -> (verdict, verifier)`. The first matching row wins.

| Verdict | When | Verifier text |
|---|---|---|
| `excluded` | `run.sim` (a rehearsal, `cloud/CLAUDE.md:54`); the run is still `running`; hardware (a work order or a `needs_human` park) | "rehearsal", "still running", "hardware: work order wo_…" |
| `fail` | `verification.passed is False`; `status = broken`; the overlay is `lockin_rolled_back`; a candidate that folded it was `rolled_back`; the same kind came back on the device inside the window | the exact check |
| `pass` | `verification.passed is True` (the bench's `score.json`); a candidate that folded it was `promoted`; the overlay `held` (the mark-good timer) | "bench check passed", "gate promoted firmware v2", "held on device" |
| `unverified` | everything else, **including a device that says `fixed` with nothing independent behind it** | "device says fixed; nothing checked it" |

**Two meanings of `sim`:**
- `agent_runs.sim` is a rehearsal and is excluded.
- A device of kind `sim` belongs to the demo fleet. Its runs are counted and badged SIM.

## 3. The fleet dataset and the training export
**The standing dataset is the "managed autonomously" part, and it needs no model.** On each review tick, plain code:
- makes sure `fleet-runs` exists (`purpose=harness`, empty filter);
- whenever labelled runs with a `pass` or `fail` verdict have grown by `LEARNING_MIN_NEW`, snapshots it and requests a `trajectories` export.

The writes record actor class `agent`: `create_dataset`, `snapshot` and `request_export` gain `actor_class: str = "human"`. Today it's hard-coded at `services/datasets.py:122, 216, 312, 385`, so the digest would count these writes as human work.

**The `trajectories` format** goes in `FORMATS` (`domain/datasets.py:28`), with a branch in `build_export` (`services/datasets.py:573-584`):

| File | What's in it |
|---|---|
| `trajectories.jsonl` | One row per `pass`/`fail` run, in Hermes Agent's trajectory format ([`research.md`](research.md) §3; H:76940-77075): `{conversations, timestamp, model, completed, metadata}`. Roles are system/human/gpt/tool, calls are inline `<tool_call>` blocks and results `<tool_response>`, and a generated system message lists the `<tools>`. **`completed` is the verdict**; `metadata` carries the run id, device, kind, verifier and versions |
| `sft.jsonl` | TRL `messages`, `completed` rows only (T1) |
| `kto.jsonl` | TRL `prompt/completion/label`, all rows, with `label` = verdict (T1). KTO learns from exactly this kind of binary signal |
| `DATASET_CARD.md` | Counts by device and kind; the verdict table; what was excluded and why; the redaction rule; the snapshot sha256; a **SIM FLEET** notice when any device is `sim` |

- **Determinism:**
  - `freeze_context` (`services/datasets.py:438-465`) also freezes each incident's agent-run `report` (`agent_runs.for_incident`) plus the computed verdict.
  - The export reads only the frozen manifest, so the same snapshot always gives byte-identical files.
- **Redaction:** `redact()` runs over every string by default (`services/datasets.py:536-570`).
- **Unverified runs** are counted on the card, never exported as rows.
- **It closes a gap:** `traces_jsonl` reads only `agent_trace` and `doctor_consult` recordings (`services/datasets.py:533`), so real pod runs never reached an export. `traces_jsonl` itself stays as it is.

## 4. Cluster review
### 4.1 Trigger (plain code)
A cluster is one fault `kind` with no held fix behind it and no review cooling down. It qualifies in one of two ways:
- **Transfer (one failure is enough):** one `fail` or `unverified` run, while another device or class already handles that kind. Handling means a `pass` run of the same kind, or a detector that names the kind; the probe registry lists known detectors per kind. This is Kent's "singular failure, generalized to the fleet".
- **Generalize:** at least 2 `fail`/`unverified` runs on at least 2 devices, and nothing in the fleet handles the kind.

At most one session per tick, within the Doctor's caps (D25) plus `REVIEW_MAX_SESSIONS_PER_HOUR`.
- **Paused** while the kind has a rolled-back proposal that no person has cleared.
- **Frozen** while the fleet is frozen (D11).

### 4.2 The case file
Built in code and sent as the message. It's untrusted data, handled under the Doctor's rules (`docs/fleet-doctor/architecture.md` §6).
- The kind, and the reason the cluster qualified.
- Each run: id, device, class, verdict, verifier, headline, and its first 8 steps, with `thought` and `excerpt` cut to 200 characters and passed through `redact`.
- Who handles the kind elsewhere: the detector's file and the function's docstring, or a passing run's steps.
- The probes available for this kind, each with id, description and scorecard summary. **We wrote the probes; the agent chooses whether to cite one.** The page says so.
- The current harness skill names and the device classes present.

### 4.3 The Naïve agent
**`fleet-doctor`:**
- `pi` harness, the only one that returns typed results (N:21543).
- `handoffs:false`, so Outcomes stay possible later (N:13425).
- Tools default to deny, with none allowed.
- `anthropic/claude-sonnet-5.5`, the model the Technician used live: about 30 s and $0.07 a session (`cloud/tests/fixtures/technician/`).
- $0.50 a task.
- Created once through the Naïve MCP, with Kent's OK.

### 4.4 The proposal (output schema, `structured_output_required: true`)
```json
{
  "type": "object", "additionalProperties": false,
  "required": ["gap", "title", "pattern", "evidence", "applies_to", "proposal", "confidence"],
  "properties": {
    "gap":        {"enum": ["missed_detection", "failed_heal"]},
    "title":      {"type": "string", "maxLength": 80},
    "pattern":    {"type": "string", "maxLength": 280},
    "evidence":   {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 12},
    "applies_to": {"type": "array", "items": {"type": "string"}, "minItems": 1},
    "proposal": {"oneOf": [
      {"type": "object", "additionalProperties": false, "required": ["kind", "probe"],
       "properties": {"kind": {"const": "rule"}, "probe": {"enum": ["<probe ids from the case file>"]}}},
      {"type": "object", "additionalProperties": false, "required": ["kind", "slug", "markdown"],
       "properties": {"kind": {"const": "skill"}, "slug": {"type": "string", "pattern": "^[a-z0-9-]{3,48}$"},
                      "markdown": {"type": "string", "maxLength": 2000}}}]},
    "confidence": {"type": "number", "minimum": 0, "maximum": 1}
  }
}
```
**Validation:** reuse the schema checker in `domain/technician.py:153-210` rather than writing a new one.

### 4.5 The checks and the routing (pure, `domain/review.py`; each failure is reported as one sentence)
1. The output validates.
2. Every `evidence` id is in the case file, and at least one is `fail` or `unverified`.
3. `applies_to` is a subset of the case file's device classes.
4. **Rule:** the probe exists, is less than 24 h old, and shows `after.caught > before.caught` and `after.false_alarms <= before.false_alarms`. The rule's target files come from the probe's port spec, never from the model.
5. **Skill:** the slug is new; the markdown is at most 2,000 characters, with no URLs and no code fences.
6. **Safety, decided in code (D25):**
   - A rule is safety-touching when any target file is on `SAFETY_PATHS`.
   - A skill is safety-touching when it names a safety path.
   - Until Ishaan's K17 list arrives, `arm/safe_replay.py` and the visor count as safety by default *(inferred: the start gate is the safety layer)*.
   - A safety-touching proposal is **staged for its owner, never shipped**.

| Outcome | What happens (one transaction, actor `agent:fleet-doctor`, class `agent`) |
|---|---|
| `review.staged` (safety) | Record the proposal, scorecard, owner, session id and cost. No candidate. The page shows "staged for Olir (D25)" |
| `review.accepted` (rule) | **T2:** a firmware candidate built by plain code from the probe's port spec (§5) |
| `review.accepted` (skill) | A harness candidate (§5) |
| `review.rejected` | Record the failed checks. No candidate; the kind cools down (`REVIEW_COOLDOWN_S`, or until a new run). The cost still counts |

**Pending** needs no table. `review.session_opened` carries the session id. After a crash, `find_session` finds it by `metadata.romulus_case` (`adapters/naive.py:250-259`) set to `review:<kind>:<n>`, with the same idempotency key.

## 5. Rollout lanes
| Lane | Path to every device | What the gate checks | Today |
|---|---|---|---|
| **Skill → harness** | `registry.create_harness(session, store, tarball, *, actor, actor_class, why, source, parent_version, fleet_id)` (`services/registry.py:161`) with `skills/<slug>.md` added, the way `_write_skills` does (`batch.py:701-740`); then `Candidate(type="harness", from_overlays=[])` | Replay: the stub, labelled (`services/gate.py:219-220`). Canary: real; `sim-2` installs the version and stays healthy for `canary_window_s` (`settings.py`). Then promote to the class | The only lane wired end to end, and only on fake arms (`tools/fake_device/src/fake_device/sim.py:129-150`) |
| **Rule → firmware** | Plain code turns the probe's port spec into a rules overlay → a firmware candidate | **Real replay** against the class's regression corpus, then the canary | **Blocked:** the rules format compares one signal to a threshold (`packages/contracts/src/romulus_contracts/rules.py:9-30`), so a frozen check needs a device signal such as `distance_spread_mm`. That's an optional contract field for Olir, after 1 PM |
| **Staged** | A patch description for the owner, derived in code from the probe: "port `armsim/level1.py:30-44` into `detect.py` `RULES` and `safe_replay.py:66-75`" | — (the owner's own review) | The hero's lane |

**Notes:**
- **`Candidate` has no `data` column** (`models.py:254-281`). The proposal, skill and scorecard go in the `candidate.created` event's data.
- **Don't race the batch.** Only one harness candidate can be on `sim-2` at a time (`gate.py:190-201`), and a candidate built on an old version goes `stale` (`gate.py:686-700`). So review waits while any harness candidate is live, and always builds on the current default harness with `parent_version` set.
- **`from_overlays=[]` is safe** (`gate.py:132, 455, 480, 513, 788` all read `or []`). F15 asserts it end to end.
- **Pods vs arms.** Lessons install on the `so101` arms (`services/seed.py:39-46`). Pods are `sensor_pod` and install nothing (`tools/edge_bridge/src/edge_bridge/bridge.py:411-420`). A pod's run can be evidence, but a pod-side rule waits for a pod install lane.

## 6. The probe
`cloud/scripts/lesson_probe.py`:
- about 60 lines, standard library, run on the host;
- imports `workerbench` with `prototypes/arm-sim` on `sys.path`;
- `l1.py:22-27` adds `firmware/healer` itself, and needs no podd.

**Writes:** `{probe, kind, port{from, to[]}, before{caught, of, false_alarms, healthy}, after{…}, limits[…], command, rev, generated_at, seconds}`.

**Inputs:**
1. the committed `gen_pod` test items (seed 99, 600 items, 20 variants including the five hard-negative families);
2. 120 frozen windows of at least 4 s, from a fixed seed, now committed;
3. Olir's `firmware/ultrasonic-sample.json`, cut into sliding windows.

**Numbers so far** (8:45 AM; the probe must reproduce them before they appear anywhere):

| Set | Today | With the port |
|---|---|---|
| Freezes of 4 s or more | 0 / 120 | 120 / 120 |
| New false alarms on the committed set | — | 0 |
| A 3.5 s freeze (`frozen-early`) | — | 4 / 30 |
| A freeze on out-of-range rows | — | 0 / 30 |
| Olir's real sample | 0 of 260 windows (8:25 AM) | re-run |

## 7. The honest metric: repeat rate
"Every fault happens once" can be measured. **Repeat rate** = the runs of a kind that arrive after a promoted change for that kind, divided by the runs of that kind before it, counted per class and leaving out `sim` rehearsals.
- It's a pure function over labelled runs and `candidate.promoted` events.
- The digest and the page show it per kind.
- Nothing claims "smarter" without it. A skill written by a strong model can even hurt a small one (`docs/fleet-doctor/research.md:112-114`); this is how that would show up.

## 8. Draft D31: cluster review is a Fleet Doctor mode
- **Question:** what limits a Naïve session that proposes fleet-wide rules and skills?
- **Proposed:**
  - **The Doctor's limits apply as they are** (D25): out of safety, caps, a daily dollar cap, `frozen` stops it, evidence is untrusted data.
  - **One clarification of the K17 block:** a prose skill that names no safety path isn't held by `safety_paths_confirmed = false`, because it touches no path. Every rule, and anything naming a safety path, is staged for its owner until the list arrives.
  - **A rolled-back proposal pauses review for that kind** until a person clears it.
  - Publishing to Naïve Skills happens only after a gate promotes. The Skills page is a mirror, not a lane, until a reader of it is itself gated.
- **Option not chosen:** a new, separate "curator" agent. That would be a fourth agent of the same shape as the Doctor and the Technician (constitution, Curated).

## 9. Settings, events, tests
**Settings,** with safe defaults:

| Setting | Default | What it does |
|---|---|---|
| `REVIEW_MODE` | `off` | `off`, `fixture` or `live`. `steps_for` drops the step when it's `off`, like `doctor_mode` |
| `NAIVE_REVIEW_AGENT` | — | |
| `REVIEW_FIXTURE` | — | readable in the container: `compose.dev.yaml` mounts `cloud/tests/fixtures` |
| `REVIEW_BUDGET_USD` | 0.50 | |
| `REVIEW_MAX_SESSIONS_PER_HOUR` | 2 | |
| `REVIEW_COOLDOWN_S` | 900 | |
| `LEARNING_MIN_NEW` | 1 | |
| `PROBE_DIR` | `/data/learning/probes` | |
| `NAIVE_SKILL_SLUG` | `romulus-healer-playbook` | |

**Worker step** `learning.tick`, every 30 s:
1. the standing dataset (§3);
2. one cluster: open or poll a session, then stage, accept or reject.
3. **T1:** after `candidate.promoted`, mirror the playbook to Naïve Skills (`POST /v1/skills`, then `/versions`, N:10591-10650, scope `agents:write`, N:10707) and record `review.published`.

Adding the step changes the step lists asserted in `tests/unit/test_worker_loop.py:72,83`; update them.

**Events:**
- `review.session_opened`, `review.staged`, `review.accepted`, `review.rejected`, `review.published`.
- Refs: `incident` (the first evidence), `candidate`, `dataset`.
- Add them to `events.py` and the APIs doc. The digest counts staged, accepted and published as agent actions.

**Tests** (fakes first; no Naïve, no LLM):
- `tests/unit/domain/test_runs.py`: the verdict table, one case per row.
- `tests/unit/domain/test_review.py`: each check failing alone, the safety routing, and the transfer vs generalize triggers.
- `tests/flows/test_f15_learning.py`, modelled on `test_f3_lock_in.py`, using the seeded fleet plus `edge_bridge.agent_runs.build()` runs, **not** `--scenario all`:
  1. the standing dataset exports `completed` true and false rows matching the verdicts;
  2. a fixture skill proposal goes stub replay → canary → promote;
  3. a fixture rule proposal touching `safe_replay.py` is staged, with no candidate;
  4. a proposal citing an invented run is rejected.
- Real-shape agent runs only (`tests/flows/test_f13_agent_runs.py`).

## 10. Deliberately not built
- **Training weights.** There are too few verified runs. A LoRA on a seeded sim fleet would be theatre; the export is the input for James's L3b when there's data worth training on.
- **A new agent, or a `lessons` table.** See §0 and D31.
- **ML clustering.** Grouping by `kind` is enough at four devices.
- **Naïve Outcomes as the reward.** The grader sees only the final answer, never the trace (N:13356-13360). It could be a second signal later.
- **Auto-merging rule code.** The LLM never writes rule code, and the owner reviews anything touching safety.
- **The LeRobot export.** Cut at K1 (`docs/cloud-platform-planning/cloud-platform-build-plan.md:173`).
- **Webhooks, Apps and cron.** Naïve can't reach the laptop cloud (`docs/cloud-platform-planning/cloud-platform-naive-integration.md:12`).
