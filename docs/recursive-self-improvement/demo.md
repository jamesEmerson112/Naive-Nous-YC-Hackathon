# Fleet learning: the demo beat and the page

_Draft v1, Sun Nov 8, ~8:45 AM PST. Every number marked **(placeholder)** must be replaced by the value the probe or the gate actually prints. The rules follow `docs/naive-technician/demo-clip.md`: numbers on screen are measured, never typed in._

_What the beat has to land, in order:_
1. _One device's failure became a lesson for every device._
2. _A **Naïve** agent wrote the lesson; plain code and the gate checked it._
3. _Nobody clicked._
4. _Every run left labelled training data behind._

## 1. Name, and where it sits
**Stage name: "Every fault happens once." Page title: "Fleet learning."**
- "Recursive self-improvement" hurts with these judges. It reads as AI-takeoff hype, and it overclaims, since no weights are trained today. The judges score "what its agents actually delivered" (`docs/hackathons.md`).
- Keep the term for the folder and for Q&A: "it's a self-improving loop with a verifier in it."

**The run of show** (the plan calls for "five demo beats, the digest numbers, the levels 2–3 pitch slide", `features.txt:67`). There's one scale per beat, and each beat answers "what's left that the one before couldn't do?" *(inferred)*.

| # | Beat | Scale | Stage s |
|---|---|---|---|
| 1 | The company takes a job: Naïve card → Hermes → the arm moves | company | 10 |
| 2 | Break → heal → resume: bad image 07, the pod healer's `fw_rollback`, the job resumes; ends on the Romulus timeline | arm | 30 |
| 3 | Some faults need hands: the Naïve Technician, `… · 1 human click` | company | 15 |
| 4 | **Every fault happens once: fleet learning** | fleet × time | **18** |
| 5 | The numbers: the digest (agent vs human actions), the scale line, the levels 2–3 slide | — | 20 |

- **Fold any separate "fleet lock-in" beat into beat 4.** Otherwise two beats both say "the fleet gets the fix".
- **Bridge in**, at the end of beat 3: "Some faults need hands. Most should only ever happen once."
- **Bridge out**, into beat 5: "And every one of those runs is now gate-labelled training data. Next, the healer's own model learns from it, through the same gate."
- **The contrast:** beat 3 ends on **1 human click** (spending money needs a person, D30); beat 4 ends on **0** (a verified software lesson doesn't, D6).

## 2. Scripts
**Stage, 18 s (45 words, "Naïve" at word 9):**
> Most faults should happen only once. A sim pod's distance sensor froze, still reading "clear", and our arm's safety gate believed it. A Naïve agent turned that into a lesson; our gate canaried it, and every arm's healer has it now. Nobody clicked.

**Table, 30 s, walking the page:**
1. Read the lesson (the verdict line).
2. Point at the evidence card: "Today our pods catch 0 of 120 freezes; with the lesson's check, 120 of 120, with no new false alarms (placeholders). It also shows where it still misses."
3. Switch to the **Naïve Studio** tab: the curator's session, then the Skills page at v2.
4. Open the data strip: "Every run, labelled pass or fail by the gate, exports as Hermes trajectories. Here's the file."
5. "Where did we step in? Nowhere. Software lessons ship on the gate; only spending money needs a person."

## 3. The page: `/learning` in Romulus
**Why Romulus, not Mission Control:**
- Romulus owns the registry, the gate, datasets and exports.
- The Technician page's visual language (`.tc-head`, `.tc-stages`, `.tc-verdict` ≥ 36 px, `cloud/src/romulus_cloud/web/templates/technician.html`) makes beats 3 and 4 look like one product.
- Mission Control can't run on this laptop (`armsim/__init__.py` imports MuJoCo), and it's James's lane.

**What it shows:** the newest lesson, polled with htmx like the timeline (`timeline.html:4`).

```
┌ Romulus   Timeline · Data · Learning                                     kill switch ┐
│┌────────────────────────────────────────────────────────────────────────────────────┐│
││ NAÏVE AGENT · curator · live session               31 s · $0.07 · 0 human clicks   ││  .tc-head; or the yellow
││ lesson L-1 · evidence: sim-pod-2, sim-pod-3, arm-1's watchdog · 08:52             ││  "Recorded Naïve session"
│└────────────────────────────────────────────────────────────────────────────────────┘│
│  ✓ A SENSOR FROZE   ✓ AGENT WROTE A LESSON   ✓ GATE: CANARY HELD   ● EVERY ARM HAS IT │  .tc-stages, 4 columns
│┌ LESSON ─────────────────────────────────────────────────────────────────────────────┐│
││  A distance that never changes is a frozen sensor, not a clear path.              ││  .tc-verdict: the
││  Every arm now parks on it.                                                       ││  LARGEST text, ≤140 chars
││  known by: arm-sim watchdog (level1.py) · missed by: pod detect.py, safe_replay.py  ││
│└─────────────────────────────────────────────────────────────────────────────────────┘│
│┌ EVIDENCE · probe, recorded 08:41 ──────────┐ ┌ GATE · harness v2 ───────────────────┐│
││ freezes ≥4 s caught   0/120  →  120/120     │ │ replay   stub (prose skill)          ││
││ new false alarms      0 of 600 windows      │ │ canary   sim-2 healthy 120 s  ✓      ││
││ still missed: 3.5 s freeze 26/30,           │ │ PROMOTED · automatic (D6) · safety ⚑ ││
││   out-of-range freeze 30/30                 │ ├ ROLLOUT ─────────────────────────────┤│
││ $ uv run python cloud/scripts/lesson_probe… │ │ sim-1  v1 → v2  ✓ installed      SIM ││
│└─────────────────────────────────────────────┘ │ sim-2  v1 → v2  ✓ installed  SIM·can ││
│                                                │ sim-3  v1 → v2  ✓ installed      SIM ││
│                                                │ arm-1  v1 → v2  assigned        REAL ││
│                                                │ Naïve Skill playbook v1 → v2  ↗      ││
│                                                └──────────────────────────────────────┘│
│┌ THE DATA THIS LEFT BEHIND ───────────────────────────────────────────────────────────┐│
││  37 episodes → 21 verified → 21 regression tests → 21 training rows   (placeholders)  ││  KPI strip
││  [↓ trajectories.jsonl]  completed = gate verdict · redacted · sha256 ab12…           ││  (partials/overview.html)
││  NEXT · not built: the healer's own model learns from these rows, through this gate  ││  greyed 4th lane
│└──────────────────────────────────────────────────────────────────────────────────────┘│
│ below the fold: evidence episodes (links to incident and agent-run pages) · curator   │
│ feed · the typed lesson (collapsed) · staged rule patch for Olir · dataset card       │
```

**The overlay: `duration · cost · 0 human clicks`.** It rhymes with the Technician's `duration · cost · 1 human click`, and comes from events:
- **duration:** `lesson.session_opened` → `candidate.promoted`;
- **cost:** the curator session's `consumed_micro_usd`;
- **human clicks:** the human-actor events in the lesson's chain.

**Two rules for the cards:**
- **Evidence and gate are separate cards.** The probe is the evidence; the gate's replay is a stub and says so. Nothing labelled "verified" sits on a stub.
- **The data strip shows counts and a real file, never "trained".** The weights lane is greyed out and says "not built".

**Reuse:** `.tc-head`, `.tc-stages`, `.tc-verdict` and the record badge from `technician.html`; the KPI strip from `partials/overview.html`; feed lines from the Technician case partial. Every status has a word or a mark, never colour alone.

## 4. Shot list (18 s)
| t (s) | Shot | Caption (≤ 5 words) | Must be true when filmed |
|---|---|---|---|
| 0–3 | `/learning`: head band and stages, the first two ticked | Every fault happens once | the lesson came from a curator session on these episodes |
| 3–6 | **Naïve Studio:** the curator session, case file → `submit_output` | Naïve agent writes lesson | the same session id as the page header |
| 6–10 | Page, zoomed: the evidence card `0/120 → 120/120`, then the gate card, canary ✓ | Checked before shipping | the probe ran on the stated rev; the canary really held |
| 10–13 | Rollout chips at v2 (SIM badges visible), then a 1.5 s cut to **Studio Skills: playbook v1 → v2** | Every arm has it | the skill version was published by Romulus after promotion; every ✓ chip really pulled v2 |
| 13–18 | Data strip + overlay `31 s · $0.07 · 0 human clicks` (placeholders) | Nobody clicked | the counts come from the export file; the overlay comes from events |

Before filming: browser zoom 125–150%, notifications off, both Studio tabs pre-opened.

## 5. Demo data
- **Where:** its own stack, `COMPOSE_PROJECT_NAME=learn API_PORT=8001`. Never Kent's `tech` stack on :8000 (the Technician demo), and never Olir's laptop cloud, which holds real runs (`cloud/CLAUDE.md`).
- **The fleet:**
  - `make seed`: `arm-1` (real, registered) and `sim-1..3` (`sim-2` is the canary).
  - Plus `sim-pod-2` and `sim-pod-3` as `sensor_pod`, kind `sim`, for the evidence episodes.
  - Every sim device is badged SIM.
- **Episodes,** from one seeded script posting through the real device API:
  1. fake-arm scenarios `--scenario all`, which give incidents, overlays, gate verdicts and recordings;
  2. `edge_bridge.fake_healer` runs (`agent_run_01` shape), the real-shape agent runs `cloud/CLAUDE.md` requires;
  3. **the hero cluster:** two sim-pod `us_frozen` incidents whose distance windows are `gen_pod` frozen windows (fixed seed), each with a pod agent run that ends `broken` or unverified, plus the arm watchdog's finding as cross-device evidence.
- **Live vs recorded:**

| Piece | On stage | Note |
|---|---|---|
| Probe numbers | recorded, with the command and rev on the card | reproducible in about 1 s |
| Gate: canary + promote | **live** in rehearsal; on stage the finished page | the canary takes 120 s |
| Export file and counts | **live** | the worker builds every 5 s (`worker.py:73-76`) |
| Curator session | **recorded** fixture by default, from a live rehearsal | live only if rehearsal shows ≤ 60 s and no 429s (the Technician's live runs took 29.6 s and 32.8 s, about $0.07) |
| Naïve Skill v1 → v2 | published at rehearsal; the Studio tab stays open | |

## 6. Honesty rules for this beat
1. **No fleet claim beyond the registry.** Say "every arm in our registry: one real, three simulated", never "our fleet". SIM badges stay in every frame.
2. **Assigned isn't running.** `arm-1` reads "assigned" unless the Pi really pulled v2. Only chips that installed show ✓.
3. **The frozen fault came from sim pods.** Never imply the real pod froze, and never cut real-arm footage into this beat.
4. **Every number comes from the probe or the gate run on the page.** This doc's numbers are placeholders.
5. **Say where the check came from:** "the arm-sim watchdog already had it; the agent made it every arm's lesson", not "invented".
6. **The rule port is staged, not shipped.** What ships is a skill for the healer. The `detect.py` and `safe_replay.py` port waits for its owner, and the page says so below the fold.
7. **"Training data", never "trained."** If James's L3a numbers appear on the levels 2–3 slide, label them "classifier · sim data only".
8. **No stub dressed as a verdict.** The gate card says "replay: stub".
9. **Recorded or sped-up material is labelled.**

## 7. Fallbacks
| If… | Then… |
|---|---|
| Naïve returns 429 or "at capacity" | Play the recorded fixture with its badge. The Studio tab still shows the rehearsal session |
| The build isn't done at 10:00 | The **minimum beat** (`build-plan.md` §2): the probe's real numbers, the curator spike's session in Studio, the Skills page at v2, and one slide. **Never a mock page with typed numbers** |
| Olir lands the frozen rule by hand first | **Retro-validation:** "Our engineer wrote this check at X AM; given only the runs, the Naïve agent proposed the same lesson." Or move to the next real gap, the out-of-range freeze (0 of 30, placeholder) |
| The live lesson fails the checks or the canary | It's honest and the page says "held" or "rolled back", but the stage uses the recorded run |

## 8. The scale line (beat 5)
> Every arm we sell makes every other arm safer: each fault is fixed once, checked by the gate, shipped to the whole fleet, and kept as labelled training data. Today that took one Naïve agent **31 seconds, 7 cents and no people** (placeholders).
