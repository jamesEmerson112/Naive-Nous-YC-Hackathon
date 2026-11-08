# Fleet learning: design

_Draft v1, Sun Nov 8, ~8:45 AM PST. Repo citations are `path:line` at `17f23be`. `N:<n>` is a line in `docs/raw/naive-llms-full.txt` and `H:<n>` a line in `docs/raw/hermes-llms-full.txt`. Outside facts: [`research.md`](research.md). *(inferred)* marks our own reasoning. Nothing here is built._

## 0. Design rules (from `cloud/CLAUDE.md` and the constitution)
1. **The batch generalizes fixes; the curator generalizes failures.** Nothing here repeats the batch's lock-in or skill writing (`services/batch.py:701-740`). The curator only takes clusters that have **no held fix** behind them.
2. **Only a verifier labels.** A model never grades its own episode. Training on unchecked model output causes collapse ([`research.md`](research.md) §6), and constitution L1.9 says an LLM sloppifies whatever it touches.
3. **The LLM writes prose and skills only** (`cloud/CLAUDE.md:16`). The curator's output is a skill and a pointer to evidence. Rule code is staged for its owner, never merged by a model.
4. **No new table.** Episodes are a pure view over existing rows; lessons live in events (`cloud/CLAUDE.md:35`). Schema changes stay additive (`:80-87`).
5. **Fakes first, stubs announce themselves.** The whole loop runs offline with `FixtureNaive`. The harness lane's stub replay keeps its "stub" label on every surface.

## 1. Components
| # | Component | Job | New or reused | Files |
|---|---|---|---|---|
| A | **Episodes** | Turn every heal attempt into one episode with a verdict a verifier gave | New; pure | `domain/episodes.py`, `services/episodes.py` (loader over `agent_runs`, `incidents`, `overlays`, `candidates`) |
| B | **Fleet dataset** | Keep one standing dataset, snapshotted and exported as training rows, with no person involved | Mostly reused: `create_dataset`, `snapshot`, `request_export`, the export worker, `redact` | `domain/datasets.py` (one format), `services/datasets.py` (`actor_class` parameter, agent runs in `freeze_context`, the new files) |
| C | **Curator** | When a failure cluster has no fix, ask a Naïve agent for one typed lesson, check it in code, and send it through the existing gate | New service; reuses `HttpNaive`/`FixtureNaive`, `registry.create_harness`, the gate | `domain/lessons.py` (schema and checks, pure), `services/curator.py`, `worker.py` (one Step), `settings.py`, `scripts/curator_spike.py` |
| D | **Probe** | Measure a candidate rule offline on labelled windows, so a lesson's claim has a number behind it | New host script; reuses `workerbench.gen_pod` and its l1 replay | `cloud/scripts/lesson_probe.py` → `data/learning/probes/<probe>.json` |
| — | **Page** | Show the newest lesson, its evidence, gate and rollout, and the dataset ([`demo.md`](demo.md) §3) | New template; reuses the Technician page's `.tc-*` styles, `partials/overview.html` and htmx polling | `api/web.py` (one route), `templates/learning.html`, `services/learning.py` (`view()`) |

- **The domain/service split** (`cloud/CLAUDE.md:12-14`): A, B and C's pure parts take dicts and `now`, and get unit tests. Services load rows and record an Event in the same transaction.
- **D runs on the host.** The containers mount only `cloud/src`, `packages/contracts/src` and `data` (`compose.dev.yaml:7-15`), so they can't import `firmware/` or `prototypes/`. The probe writes into `data/`, which the worker reads as `/data/learning/probes/`.

## 2. The Episode
```python
@dataclass(frozen=True)
class Episode:
    id: str                  # "run:<device>/<run_id>" or "inc:<incident_id>"
    device_id: str; device_class: str; device_kind: str   # kind "sim" → SIM badge
    kind: str                # incident kind ("us_frozen"); a run takes its first linked incident's
    started_at: datetime
    model: str | None        # report.agent.model, or Incident.agent's
    versions: dict           # {"firmware": n, "harness": n, "mcu_build": crc} when known
    steps: tuple[dict, ...]  # {thought, tool, args, effect, ok}: report.steps, or the agent_trace fallback
    verdict: str             # pass | fail | unverified | excluded
    verifier: str            # who decided, as a sentence: "gate promoted harness v4 (replay + canary)"
    incident_id: str | None; run_id: str | None
```

**Sources:**
- `agent_runs` first (`cloud/CLAUDE.md:45-50`).
- Incidents with no run (the fake arms) fall back to `Incident.agent` plus the `agent_trace` recording, which `cloud/CLAUDE.md:48-50` allows.
- An incident that has a run yields one episode, from the run.

**The verdict** is a pure function, `verdict(run, incident, overlay, candidates) -> (verdict, verifier)`. The first matching row wins.

| Verdict | When | Verifier text |
|---|---|---|
| `excluded` | `run.sim` (a rehearsal, `cloud/CLAUDE.md:54`); the run is still `running`; hardware (a work order, or a `needs_human` park) | "rehearsal", "still running", "hardware: work order wo_…" |
| `fail` | `verification.passed is False`; the run's `status = broken`; the overlay is `lockin_rolled_back`; a candidate that folded it was `rolled_back`; the same kind came back on the device inside the window | the exact check, e.g. "canary rolled back harness v3" |
| `pass` | `verification.passed is True` (the bench's `score.json`); a candidate that folded it was `promoted`; the overlay was `held` (the mark-good timer) | "bench check passed", "gate promoted firmware v2", "held on device" |
| `unverified` | everything else, **including a device that says `fixed` with nothing independent behind it** | "device says fixed; nothing checked it" |

- **Two meanings of `sim`:**
  - `agent_runs.sim` marks a rehearsal, which is excluded.
  - A device of kind `sim` belongs to the fake fleet, which is the fleet in the demo stack. Its episodes are counted and carry a **SIM** badge everywhere.
- **Why there's no table:**
  - At four devices, the view recomputes in milliseconds.
  - Snapshots freeze it for determinism (§4).
  - A table would duplicate the state of `agent_runs` and `candidates`, and could drift from them.

## 3. The curator
### 3.1 When it runs
Plain code picks a **cluster**: one incident `kind` with
- at least 2 non-excluded episodes on at least 2 devices;
- at least 1 `fail` or `unverified` episode;
- no `lesson.accepted` for that kind yet;
- no cool-down running.

At most one session per tick, and at most `CURATOR_MAX_SESSIONS_PER_HOUR`.

### 3.2 The case file
Built in code and sent as the session's message:
- the cluster kind;
- each episode's id, device, class, verdict, verifier and headline, plus its first 8 steps (`thought` and `excerpt` cut to 200 characters, passed through `redact`);
- the fleet's current harness skill names, so it doesn't write a duplicate;
- the available probes: id, one-line description, and the scorecard summary;
- the device classes present.

### 3.3 The agent
- **`curator`** on Naïve: harness `pi`, the only one that returns typed results (N:21543).
- `handoffs:false`; tools default to deny with **none** allowed, because everything it needs is in the case file.
- Model `anthropic/claude-sonnet-5.5`, the one the Technician used live.
- Budget: $0.50 per task.
- Created once through the Naïve MCP, with Kent's OK.

### 3.4 `FleetLesson` (output schema, `structured_output_required: true`)
```json
{
  "type": "object", "additionalProperties": false,
  "required": ["title", "pattern", "evidence", "applies_to", "skill", "probe", "touches_safety", "confidence"],
  "properties": {
    "title":          {"type": "string", "maxLength": 80},
    "pattern":        {"type": "string", "maxLength": 280},
    "evidence":       {"type": "array", "items": {"type": "string"}, "minItems": 2, "maxItems": 12},
    "applies_to":     {"type": "array", "items": {"type": "string"}, "minItems": 1},
    "skill": {"type": "object", "additionalProperties": false, "required": ["slug", "markdown"],
              "properties": {"slug": {"type": "string", "pattern": "^[a-z0-9-]{3,48}$"},
                             "markdown": {"type": "string", "maxLength": 2000}}},
    "probe":          {"type": "string", "enum": ["<probe ids from the case file>", "none"]},
    "touches_safety": {"type": "boolean"},
    "confidence":     {"type": "number", "minimum": 0, "maximum": 1}
  }
}
```

### 3.5 The checks, in plain code
These live in `domain/lessons.py`, which is pure. Each failure is reported as one sentence.
1. The output validates against the schema.
2. Every `evidence` id is in the case file, so no evidence is invented, and at least one cited episode is `fail` or `unverified`.
3. `applies_to` is a subset of the case file's device classes.
4. The skill:
   - its `slug` isn't already in the current harness;
   - its markdown is at most 2,000 characters, with no URLs and no code fences (prose only, `cloud/CLAUDE.md:16`).
5. **The probe:**
   - If `probe` names one, `/data/learning/probes/<probe>.json` must exist, be less than 24 h old, and show `after.caught > before.caught` **and** `after.false_alarms <= before.false_alarms`.
   - With `probe: none`, the lesson can still pass, but the candidate's `why` says "no measured evidence; replay is a stub".
6. **Safety:** the code sets `touches_safety` to true whenever the skill mentions parking, stopping or the start gate *(a keyword list; inferred)*, whatever the model claimed. A `touches_safety` lesson needs a canary device in its class, or it's held (K17). The hero lesson is `touches_safety`.

### 3.6 Accept, reject, pending
**On accept,** one transaction, actor `agent:curator`, class `agent`:
- record `lesson.accepted`, with the lesson, the scorecard, the Naïve session id and the cost;
- create dataset `lesson-<kind>` (`purpose=harness`, filter `labels: ["kind:<kind>"]`, which matches the automatic labels, `domain/labels.py:7-19`), snapshot it, and request a `trajectories` export;
- create the harness candidate (§5).

**On reject:**
- record `lesson.rejected`, listing the failed checks;
- no dataset and no candidate;
- the cluster cools down (`CURATOR_COOLDOWN_S`, or until a new episode arrives);
- the session's cost still counts in the digest.

**Pending** needs no table:
- `lesson.session_opened` carries the session id, and the next tick polls the newest one.
- After a crash, `find_session` finds it by `metadata.romulus_lesson` (`adapters/naive.py:250-259`), with idempotency key `lesson:<kind>:<n>`.

## 4. The fleet dataset and the training export
### The standing dataset (the "managed autonomously" part, no model)
On every curator tick, plain code:
1. makes sure `fleet-episodes` exists (`purpose=harness`, empty filter);
2. when verified episodes have grown by `CURATOR_MIN_NEW`, snapshots it and requests a `trajectories` export.

### The `trajectories` export format
A new format in `FORMATS` (`domain/datasets.py:28`), with one branch in `build_export` (`services/datasets.py:573-584`). It writes four files:

| File | What's in it |
|---|---|
| `trajectories.jsonl` | One row per `pass` or `fail` episode, in Hermes Agent's trajectory format ([`research.md`](research.md) §3; H:76940-77075): `{conversations, timestamp, model, completed, metadata}`. Roles are system/human/gpt/tool; calls are inline `<tool_call>` blocks, results are `<tool_response>`, and a generated system message lists the `<tools>`. **`completed` is the verdict.** `metadata` carries the episode id, device, kind, verifier and versions |
| `sft.jsonl` | TRL `messages`, from `completed` rows only |
| `kto.jsonl` | TRL `prompt/completion/label`, from every row, with `label` set to the verdict |
| `DATASET_CARD.md` | source counts by device and kind; the verdict table from §2; what was excluded and why; the redaction rule; the snapshot sha256; a **SIM FLEET** notice when any device is `sim` |

- **Determinism:**
  - `freeze_context` (`services/datasets.py:438-465`) now also freezes each incident's agent-run `report` (via `agent_runs.for_incident`), plus the computed verdict and verifier.
  - The export reads only the frozen manifest, so the same snapshot always gives byte-identical files (`canonical`, `jsonl`).
- **Redaction:** `redact()` runs over every string by default, as `traces_jsonl` already does (`services/datasets.py:536-570`).
- **This closes a gap:**
  - `traces_jsonl` reads only `agent_trace` and `doctor_consult` recordings (`services/datasets.py:533`), so real pod runs never reached an export.
  - `trajectories` reads frozen agent runs first.
  - `traces_jsonl` stays as it is.
- **Unverified episodes are never exported as rows.** The card counts them.

## 5. How a lesson rolls out
### The lane
It rides the **harness lane**, the only one wired end to end (`batch.py:30-31`; the fake arms install it, `tools/fake_device/src/fake_device/sim.py:129-150`).
- `registry.create_harness(base + skills/<slug>.md, actor="agent:curator", actor_class="agent")`, the same way `_write_skills` does it (`batch.py:701-726`).
- Then `Candidate(type="harness", from_overlays=[], incident_id=<first evidence incident>, data={"lesson", "skills", "scorecard"})`.
- The gate reads `from_overlays or []` everywhere (`services/gate.py:132, 455, 480, 513, 788`), so an empty list is safe *(inferred; F15 asserts it)*.

### What the gate checks, honestly
- **Replay:** the stub, which announces itself ("a harness skill is prose; there are no rules to replay", `services/gate.py:219-220`).
- **Canary:** real. `sim-2` installs the version and has to stay healthy for `canary_window_s=120` (`settings.py:135`).
- **Promote:** every device in the class (`so101`) is assigned the version. A rollback needs no overlays to settle.

### The scorecard
- It's **evidence checked before the gate** (§3.5, check 5), never shown as the gate's replay.
- It's shown as recorded, for example: "probe `us_frozen_port`: offline replay of 720 labelled windows on the host at 08:41, rev `17f23be`, 0.74 s", with the command that reproduces it.

### The rule port never rides this lane
- Nothing installs `detect.py`; the bridge installs nothing (`tools/edge_bridge/src/edge_bridge/bridge.py:411-420`).
- The lesson records a **staged patch**, derived in code from the probe id (the model never writes it): "port `prototypes/arm-sim/armsim/level1.py:30-44` into `firmware/healer/healer/detect.py:299` `RULES`, and add the same check to `arm/safe_replay.py:66-75` (owner: Olir)".
- **T2:** the coder lane drafts it and stops at B7, like every code candidate.

### Pods vs arms
- The fleet that can install a lesson is the `so101` arms (`services/seed.py:39-46`).
- Pods are `sensor_pod`, and nothing installs on them (`bridge.py:411-420`).
- So the lesson's evidence can come from a pod, but it ships to the arms' healers. For this fault that's the right target: the arm is the one that acts on the reading.

## 6. The probe
`cloud/scripts/lesson_probe.py`, about 60 lines, standard library only, run on the host. It writes `{probe, before{caught, of, false_alarms, healthy}, after{…}, limits[…], command, rev, generated_at, seconds}`.

**Inputs:**
1. the committed `gen_pod` test items (seed 99, 600 items, 20 variants including the five hard-negative families);
2. 120 frozen windows of at least 4 s, generated from a fixed seed and now committed;
3. Olir's `firmware/ultrasonic-sample.json`, cut into sliding windows.

**Scoring:** each window runs through `detect.run` alone, then with the port.

**Numbers so far** (architect fork, 8:45 AM; the probe must reproduce them before they appear anywhere):

| Set | Today | With the port |
|---|---|---|
| Freezes of 4 s or more | 0 / 120 | 120 / 120 |
| New false alarms, committed set | — | 0 |
| `frozen-early` (a 3.5 s freeze) | — | 4 / 30 |
| `frozen-at-out_of_range` | — | 0 / 30 |
| Olir's real sample | 0 of 260 windows (device investigator, 8:25) | re-run |

## 7. Settings, events, actors, tests
**Settings** (`settings.py`, safe defaults):

| Setting | Default | What it does |
|---|---|---|
| `CURATOR_MODE` | `off` | `off`, `fixture` or `live`. `steps_for` drops the step when it's `off`, like `doctor_mode` (`worker.py:89-96`) |
| `NAIVE_CURATOR_AGENT` | — | |
| `CURATOR_FIXTURE` | — | |
| `CURATOR_BUDGET_USD` | 0.50 | |
| `CURATOR_MAX_SESSIONS_PER_HOUR` | 2 | |
| `CURATOR_COOLDOWN_S` | 900 | |
| `CURATOR_MIN_NEW` | 1 | |
| `NAIVE_SKILL_SLUG` | `romulus-healer-playbook` | |
| `PROBE_DIR` | `/data/learning/probes` | |

**Worker step** `curator.tick`, every 30 s:
1. the standing dataset (§4);
2. one cluster: open or poll a session, then accept or reject it;
3. **T1:** after `candidate.promoted` on a lesson's candidate, push the playbook to Naïve Skills (`POST /v1/skills`, then `/versions`, N:10591-10650) and record `lesson.published` with Naïve's version number.

**Events:**
- `lesson.session_opened`, `lesson.accepted`, `lesson.rejected`, `lesson.published`.
- Refs: `lesson`, `incident` (the first evidence), `candidate`, `dataset`.
- They're added to `events.py`'s list and to the APIs doc.
- The digest counts `lesson.accepted` and `lesson.published` as agent actions (`domain/digest.py:87-110`).

**Actors:**
- `create_dataset`, `snapshot` and `request_export` gain `actor_class: str = "human"`. Today it's hard-coded (`services/datasets.py:122, 216, 312, 385`).
- The curator passes `"agent"`, so the digest doesn't count curated datasets as human work.

**Tests** (fakes first; no Naïve, no LLM):
- `tests/unit/domain/test_episodes.py`: the verdict table, one case per row.
- `tests/unit/domain/test_lessons.py`: each check failing on its own.
- `tests/flows/test_f15_learning.py`:
  1. Fake arms `--scenario all`, plus one `edge_bridge.agent_runs.build()` pod run that ends `broken`.
  2. `curator.tick` in fixture mode creates and snapshots `fleet-episodes` as `agent`. The `trajectories` export has `completed` true and false rows matching the verdicts.
  3. An accepted lesson's harness candidate goes stub replay → canary → promote, and every `so101` device is assigned.
  4. A lesson citing an invented episode id is rejected: no candidate, and a cool-down starts.
  - The real-shape rule for agent runs is F13's (`tests/flows/test_f13_agent_runs.py`).

## 8. Deliberately not built
- **Training weights.** There are too few verified episodes, and a LoRA on a seeded sim fleet would be theatre.
  - The greyed fourth lane says "weights: the same gate, next".
  - James's L3a classifier (`docs/healer-data-levels.md:78-93`, sim data only) is the honest exhibit of a model learned from labelled runs.
- **A `lessons` table.** Events are the spine.
- **Embedding or ML clustering.** At four devices, grouping by `kind` is enough.
- **Naïve Outcomes as the reward.** The grader sees only the final answer, never the trace (N:13356-13360).
- **Auto-merging rule code.** There's no lane, the LLM never writes merged config, and Olir owns `detect.py`.
- **The LeRobot export.** It was cut at K1 (`docs/cloud-platform-planning/cloud-platform-build-plan.md:173`), and no recording splits observation from action.
- **Webhooks, Apps, a cron deployment.** Naïve can't reach the laptop cloud (`docs/cloud-platform-planning/cloud-platform-naive-integration.md:12`).
- **Dataset forms on the Data page.** The curator makes the datasets.
