# Fleet learning: how this was planned, the reviews, and what changed

_Sun Nov 8, 8:20–8:55 AM PST. One Claude session coordinated sub-agents in three rounds. Every sub-agent was read-only: it edited nothing and made no Naïve writes. Their reports aren't committed; what they found is cited in the docs where it's used._

## Round 1 · four investigations (in parallel)
| Investigation | Key findings that shaped the design |
|---|---|
| **Cloud seams** | Datasets, labels, snapshots and exports exist (F8) but are **manual**, and their writers hard-code actor `"human"` (`services/datasets.py:122, 216, 312, 385`). The regression corpus is already automatic (`sync_regression_cases`). **`agent_runs`, the only real data, feeds no dataset**: `traces_jsonl` reads only `agent_trace` and `doctor_consult` recordings (`services/datasets.py:533`). The batch already turns held fixes into versions and writes skills (`batch.py:701-740`). Kent's `tech` stack was empty, and the overnight run never happened |
| **Device side and data** | The harness bundle (prompt + `skills/*.md`) is the only update lane wired end to end, and only fake arms install it. `detect.py` has no cloud path, and the bridge installs nothing. Little data is on disk, but `workerbench.gen_pod` regenerates 600 labelled pod windows in about 1 s with the standard library. The frozen-ultrasonic gap is real and measurable. `AgentRunReport` is already shaped like RL data (steps plus an outcome) |
| **Naïve and Hermes** | A Naïve session with typed output reuses the Technician's adapter (`HttpNaive`, `FixtureNaive`). Naïve Skills are versioned SKILL.md files, the same standard Hermes uses. Outcomes graders never see the trace, so they make a weak reward. Webhooks, Apps and cron can't reach the laptop cloud. **The CEO board already has an INSIGHTS card describing this agent's job.** Hermes trajectories carry a `completed` flag |
| **Outside research** ([`research.md`](research.md)) | The flywheel loop is trigger → mine → label → train → evaluate → deploy. **"The gate is the verifier; its verdict is the reward"** (RLVR). Viam closes the loop but promotes new models by hand. Training on unverified output risks collapse. Atropos was archived on Jul 4, 2026 |

## Round 2 · two designs (forked sessions with the full context)
- **Architecture** proposed four components with no new table and a strict verdict table. Its split: "the batch generalizes fixes; the curator generalizes failures." It also re-measured the probe: 0/120 → 120/120 for freezes of 4 s or more, with the limits 4/30 and 0/30.
- **Demo and interface** proposed the stage name "Every fault happens once", a `/learning` page reusing the Technician's look, and the 1-click (Technician) vs 0-click (fleet learning) contrast, along with honesty rules and fallbacks.

Merged into **v1** (8:45 AM; Kent pushed it to main as `38b2ad2`).

## Round 3 · three reviews of v1 (fresh sessions, in parallel)
### Execution risk
- **M by 9:30:** likely. **T0a by 10:00:** unlikely.
- **Top risks:**
  1. Kent's OKs landed on Technician gates.
  2. T0a was really 70–90 minutes: the canary under the fake clock, `_write_skills`, the worker step-list test.
  3. Batch candidates would make a review candidate go `stale`.
  4. The worktree lacks the untracked docs, `.env` and the probe prototype.
  5. The canary needs fake arms running with chaos off.
  6. Merging into main would hot-reload Kent's demo API.
  7. The seed script wasn't budgeted.
  8. Naïve risks are low.
- **Factual errors:**
  - PRs #68 and #70 were already merged.
  - `Candidate` has no `data` column.
  - `find_session` matches `romulus_case`.
  - The compose mounts include the fixtures.
  - `technician_spike.py` is 656 lines.

### The judges' seat
- **Keep the beat, but stop implying the safety hole is closed:** what ships is prose, and `safe_replay.py` doesn't change.
- **The overlay's "31 s"** can't include the 120 s canary.
- **Plain words,** and say "Naïve" early.
- **On a projector:** one big number and one row of chips.
- **Naïve is most visible on the Skills-page diff,** or as a comment on the INSIGHTS card, more than in the tool-less session transcript.
- **Without a promoted change, drop the separate stage beat.**

### Coherence and principles
1. **What ships can't fix the hero fault.** It's a detection gap, and a skill only runs after something detects. So route by gap type: a missed detection becomes a rule, a failed heal becomes a skill.
2. **A safety-touching lesson with 0 clicks contradicts D25.**
3. **A fourth agent of the same shape duplicates Fleet Doctor.** Make it a Doctor mode.
4. **"A single failure goes fleet-wide" wasn't met** by a two-device minimum.
5. **Honesty wording:** "verified" sat on a stub, and "smarter" was unmeasured.
6. **"Episode" collides** with the LeRobot recording kind.
7. **The README was too dense.**

## What changed in v2
| Review point | v2 |
|---|---|
| Safety vs 0 clicks (coherence 2, judges 1) | The hero is **staged for its owner** (D25). 0 clicks covers data and non-safety changes only. The README states the autonomy policy as a table |
| A skill can't fix a detection gap (coherence 1) | **Route by gap type.** A rule is built by plain code from a probe and gets the firmware lane's real replay (blocked until a `distance_spread_mm` signal exists; T2 with Olir). A skill rides the harness lane |
| A fourth agent (coherence 3) | **Cluster review, a Fleet Doctor mode** run as a Naïve session (agent `fleet-doctor`), under draft **D31** (`design.md` §8) |
| A single failure (coherence 4) | A **transfer** trigger: one failure, while another device or class already handles the kind |
| Unmeasured "smarter" (coherence 5) | A **repeat-rate** metric (`design.md` §7). "Gate-checked", never "verified", on a stub. "We wrote the probe; the agent chose to cite it" |
| Vocabulary (coherence 6, 7) | "Labelled run", "failure cluster", "cluster review", "proposal". A one-sentence summary and a five-line glossary at the top of the README |
| T0 too big; Kent's slots (execution 1, 2) | Before 10:00, only lanes that don't need the gate: evidence (A), data (B), page (C). The full T0 comes after 1 PM. Kent turned out to be free from 9:00, since the Technician was merged at 8:44 |
| Hot reload of the demo API (execution 6) | **No `cloud/src` merge into main before 1 PM.** Scripts, fixtures and docs only |
| Batch race, seed, canary (execution 3, 5, 7) | Wait while any harness candidate is live; no `--scenario all`; sim pods registered as `sensor_pod`; `CANARY_WINDOW_S=30` and chaos off on `learn` |
| Factual errors (execution) | Fixed: event data, not a `data` column; `romulus_case`; the real `create_harness` signature; the merged PRs |
| Overlay duration and the stage beat (judges 2, 3) | Duration is measured from events and includes the canary. No separate beat today, just one sentence in beat 5 and the table walk-through. The full beat waits for a promoted, non-safety skill |
| Naïve visibility (judges 5) | The full beat leads with the Skills-page diff, mirrored after promotion. A comment on the INSIGHTS card is T2 |

## What we deliberately didn't take
- **Renaming the folder to `fleet-learning/`** (coherence 6). Kent chose the path. The feature is called "Fleet learning" everywhere inside it.
- **Dropping the probe** (coherence: "built for the answer"). It stays as evidence, disclosed as ours. Without it, a proposed rule has no number behind it.
- **A separate stage beat with the frozen sensor** (the demo fork's v1). Since that lesson is safety and gets staged, a "0 clicks" beat on it would be untrue.
- **Training anything today.** A LoRA on a seeded sim fleet would be theatre. The export is James's Level 3 input for when real runs exist.

## Principles check *(our reading of the constitution)*
| Principle | How v2 meets it |
|---|---|
| **Tasteful, Curated** | Five components. No new agent, no new table. Webhooks, Apps, cron, Outcomes, LeRobot and weights all cut |
| **Ethical** | Only a verifier labels; traces are redacted; the autonomy policy is explicit; safety waits for its owner; the honesty rules forbid "trained", "verified" on a stub, and fleet claims beyond the registry |
| **Composable** | Labelled runs are one spine many readers can share. Every shipped change goes through the one gate. Rules, skills, datasets and future weights are just artifact types |
| **Heterarchical** | The Naïve agent proposes, plain code and the gate decide, and the owner decides safety. Leadership depends on the kind of change, not on rank |
| **Generative** | The README's one sentence plus the verdict table and the routing table regenerate the design. The tests pin the tables |
| **L1.9, L2.20** | No model output becomes a label or merged code. The README opens with one sentence, a glossary and one diagram, so nobody should feel lost |
