# Fleet learning: the demo and the page

_v2, Sun Nov 8, ~8:55 AM PST, after the judges'-seat review ([`review.md`](review.md)). The rules follow `docs/naive-technician/demo-clip.md`: numbers on screen are measured, never typed in, and every number here marked **(placeholder)** must come from the probe or the gate._

## 1. What it has to land
1. One device's failure became something the whole fleet gains: a checked change, or a measured fix handed to the right person.
2. A **Naïve** agent did the thinking; plain code and the gate did the checking.
3. Each kind of change has a known place where a person steps in, and for data and non-safety changes that place is nowhere.
4. Every run leaves labelled training data behind.

**The name:** on stage it's "Every fault happens once"; the page is "Fleet learning". "Recursive self-improvement" reads as AI-takeoff hype and overclaims, since no weights are trained. Keep it for Q&A: "it's a self-improving loop with a verifier in it."

## 2. Today (1 PM): no new stage beat
The judges'-seat review was clear. Without a change the gate actually promoted, a separate beat would only say "an agent wrote a suggestion", in a demo scored on what agents *delivered*. So on stage it gets **one sentence inside beat 5** (the numbers), and the full walk-through happens at the 11:00 table.

**The run of show** (the plan's "five demo beats, the digest numbers, the levels 2–3 pitch slide", `features.txt:67`):

| # | Beat | Scale |
|---|---|---|
| 1 | The company takes a job: Naïve card → Hermes → the arm moves | company |
| 2 | Break → heal → resume: bad image 07 rolled back, the job resumes | arm |
| 3 | Some faults need hands: the Naïve Technician, `… · 1 human click` | company |
| 4 | (the existing fleet lock-in, if it's in the run of show) | fleet |
| 5 | **The numbers**: the digest, *the fleet-learning sentence*, the scale line, the levels 2–3 slide | — |

**Bridge out of beat 3** (it makes the Technician's one click look deliberate): "Spending money needs a person. A checked change doesn't."

**The fleet-learning sentence in beat 5** (44 words, about 16 s, "Naïve" at word 16):
> Every run our robots make is labelled by whatever checked it, so the fleet builds its own training set. This morning a Naïve agent found a sensor fault our pods miss, measured the fix, and handed it to its engineer: safety code waits for a person.

**The scale line** (49 words, about 18 s):
> Every arm we sell makes the others better at fixing themselves: a fault is solved once, checked by our gate, shipped to all, and kept as labelled training data for the next model. The bigger the fleet, the fewer new faults each arm sees.

- "Fewer new faults per arm" is the thesis, not a measurement, so it isn't given as a number.
- The repeat rate (`design.md` §7) is what would measure it.

## 3. The 11:00 table walk-through (about 40 s)
1. **The problem, in one breath:** "A frozen distance sensor keeps saying 'all clear'. Our arm's start gate believes any 'ok' reading (`arm/safe_replay.py:66-75`). Our arm simulator's watchdog catches it; our pods don't."
2. **Naïve Studio, the `fleet-doctor` session:** the case file goes in, and the typed proposal comes out: *missed detection; port the watchdog's check.* "It's one Naïve agent session: about 30 seconds and a few cents (placeholder; measured in the spike)."
3. **The probe:** "We measured the agent's proposal: today our pods catch 0 of 120 freezes; with the port, 120 of 120, with no new false alarms (placeholders). It still misses a 3.5-second freeze, and it says so."
4. **The routing:** "It touches the arm's start gate, so it doesn't ship itself. It's staged for Olir, our firmware engineer, with these numbers. That's the policy: data and non-safety changes ship on the gate, safety code waits for its engineer, money waits for a person."
5. **If lanes B and C landed** (`build-plan.md` §2): open `/learning` on the `learn` stack. "Every run, labelled pass or fail by what checked it, exports as Hermes trajectories. Here's the file."

**Where a judge will poke:**

| Question | Answer |
|---|---|
| "So nothing was trained?" | "Right. Every run is now labelled data; the model is the next thing through the same gate." |
| "It's all sim?" | "The frozen sensor is planted in sim, like a fire drill. The real arm's start-gate code is real." |
| "Who wrote the check?" | "James, for the sim watchdog. The agent noticed the pods lacked it; our probe measured it; we wrote the probe, and the agent chose to cite it." |
| "Did the agent get 120 of 120?" | "No, the ported check did. The agent proposed it, and code verified the claim." |
| "A skill is just markdown?" | "Yes: a playbook the healer reads. That's why the gate's replay says 'stub' for skills, and rules get a real replay." |

## 4. The page: `/learning` in Romulus (for the table, and for when T0 is built)
**Why Romulus, not Mission Control:**
- Romulus owns the registry, the gate and the datasets.
- The Technician page's look (`.tc-head`, `.tc-stages`, `.tc-verdict` ≥ 36 px in `technician.html`) makes the two features read as one product.
- Mission Control can't run on this laptop (MuJoCo import), and it's James's lane.

**Above the fold**, readable from 5 m:
```
┌ Romulus   Timeline · Data · Learning                                      kill switch ┐
│┌─────────────────────────────────────────────────────────────────────────────────────┐│
││ NAÏVE AGENT · fleet-doctor · cluster review       31 s · 7¢ · staged for Olir       ││  .tc-head; yellow badge
│└─────────────────────────────────────────────────────────────────────────────────────┘│  if recorded
│  ✓ ONE ROBOT KNEW   ✓ NAÏVE AGENT FOUND THE GAP   ✓ FIX MEASURED   ● WAITING ON ITS ENGINEER │  .tc-stages
│                                                                                         │
│   A distance that never changes is a frozen sensor, not a clear path.                  │  .tc-verdict, largest
│                                                                                         │
│   0 → 120 of 120 freezes caught · no new false alarms          (placeholders)          │  second largest
│   safety code → staged for Olir (D25) · still misses a 3.5 s freeze                    │  12–14 px
│┌ THE FLEET'S OWN DATA ──────────────────────────────────────────────────────────────────┐│
││  37 runs → 21 labelled by a verifier → 21 regression tests → 21 training rows (placeh.) ││  KPI strip
││  [↓ trajectories.jsonl]  completed = verdict · redacted · sha256 ab12…                  ││
││  repeat rate after a change: —  ·  NEXT, not built: the healer's model trains on this   ││  greyed lane
│└─────────────────────────────────────────────────────────────────────────────────────────┘│
│ below the fold: the evidence runs (links to agent-run pages) · the typed proposal ·      │
│ the probe's command and rev · the staged patch · the dataset card · gate details         │
```

**Rules for the page:**
- **The overlay names the real end state:**
  - for a staged proposal: `duration · cost · staged for <owner>`;
  - for a skill the gate promoted: `lesson → every arm · <duration including the canary> · <cost> · 0 clicks`.
- **Duration is measured from events:** `review.session_opened` to `review.staged` or `candidate.promoted`. A promoted change's figure includes the canary (120 s by default), so it's minutes, not seconds.
- **The evidence (does the change help?) and the canary (does it do no harm?) are never merged into one claim.**
- **Gate details go below the fold, small,** including "replay: stub" for skills.
- **The rollout shows as one row of chips:** `[sim-1 ✓ SIM] [sim-2 ✓ SIM·test] [sim-3 ✓ SIM] [arm-1 queued REAL]`. Only installed chips get ✓.
- **Every status has a word or a mark, never colour alone.**

**Reuse:**
- `.tc-head`, `.tc-stages`, `.tc-verdict` and the record badge from `technician.html`;
- the KPI strip from `partials/overview.html`;
- htmx polling as in `timeline.html:4`.

## 5. The full beat, once a gate-promoted skill exists (after 1 PM)
Use it for the Hermes video or a later demo. It needs a **non-safety failed heal** as its hero, so that 0 clicks is true. The frozen sensor can't be that hero, because it touches safety.

**Script** (about 17 s):
> Our Naïve agent makes sure a fault only happens once. In sim, one robot's healer couldn't fix [fault]; another could. The agent wrote the lesson down, a sim arm tried it first, and now it's shipping to every arm. Nobody clicked.

**Shot list** (15 s):

| t (s) | Shot | Caption |
|---|---|---|
| 0–3 | `/learning`, head and stages | Every fault happens once |
| 3–5 | **Naïve Studio Skills:** `romulus-healer-playbook` v1 → v2 with the new paragraph highlighted ("mirrored after gate promotion"). The session itself for 1 s at most: with no tools it's JSON in, JSON out | Naïve agent wrote it |
| 5–10 | The canary chip, then the rollout chips | Tried on a sim arm first |
| 10–15 | The overlay `lesson → every arm · 2m40s · 7¢ · 0 clicks` (placeholders) | Nobody clicked |

**Better than any transcript, if it's built (T2):** on promotion, a comment on the CEO board's INSIGHTS card ("done by agent `fleet-doctor`"). It shows a company run by agents in under 2 s.

## 6. Demo data (for the table page and the full beat)
- **Where:** its own stack, `COMPOSE_PROJECT_NAME=learn API_PORT=8001`. Never `tech` on :8000, never Olir's laptop cloud.
- **The fleet:**
  - `make seed` gives `arm-1` (real, registered) and `sim-1..3` (`sim-2` is the canary).
  - Sim pods are registered first with `POST /v1/devices` as `device_class: "sensor_pod"`. Otherwise they auto-register as `so101` (`services/devices.py:213`) and get harnesses.
- **Runs:**
  - `edge_bridge.agent_runs.build()` runs, the real shape;
  - the hero's frozen windows from `gen_pod` (fixed seed).
  - No `--scenario all`: its batch candidates would collide with a review candidate on `sim-2`.
- **The canary,** for rehearsal:
  - `CANARY_WINDOW_S=30` on `learn` only, shown on the page;
  - fake arms run with chaos off: `CHAOS=off docker compose -f compose.yaml -f compose.dev.yaml --profile sim up -d`.

## 7. Honesty rules
1. **No fleet claim beyond the registry:** "one real arm, three simulated". SIM badges in every frame.
2. **Queued isn't running.** `arm-1` shows "queued" unless the Pi pulled the version.
3. **The frozen fault came from sim.** Never imply the real pod froze; never cut real-arm footage into this.
4. **Every number comes from the probe or the gate run shown.**
5. **Say who wrote what:** "the watchdog had it; the agent noticed the gap; we wrote the probe."
6. **Staged isn't shipped.** The safety port waits for its owner, and the page says so above the fold.
7. **"Training data", never "trained".** James's L3a numbers, if shown, are labelled "classifier · sim data only".
8. **No stub dressed as a verdict**, and no evidence presented as the canary's result.
9. **Recorded or sped-up material is labelled.**

## 8. Fallbacks
| If… | Then… |
|---|---|
| The spike fails or Naïve returns 429 | At the table: the probe numbers plus the design's loop diagram. On stage: the beat-5 sentence without "this morning a Naïve agent…": "When a sensor fault like a frozen echo slips past our pods, the fleet's agents measure the fix and hand it to its engineer." |
| Olir lands the frozen rule by hand first | **Retro-validation:** "Our engineer wrote this check at X; given only the runs, the Naïve agent proposed the same port." Or move to the next real gap, the out-of-range freeze (0 of 30, placeholder) |
| A judge asks to see it ship | "Data and non-safety changes ship on the gate today (here's the gate on a skill from the batch); this one is safety code, so it waits." |
