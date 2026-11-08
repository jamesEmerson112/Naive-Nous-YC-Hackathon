# Fleet learning: every fault happens once

_Kent's request, planned by a Claude session with sub-agents, Sun Nov 8, ~8:45 AM PST. Status: **draft v1, before review**; nothing built. The folder is called `recursive-self-improvement`; on stage it's "Every fault happens once" and the page is "Fleet learning" ([`demo.md`](demo.md) §1 says why)._

## The idea
Romulus runs one heal loop at three scales: the arm heals itself, the fleet gets fixes that hold, and the company (Naïve) takes the faults that need hands. **Fleet learning adds the fourth dimension, time: every turn of the loop leaves the next one smarter.**

1. **Every heal is an episode, and a verifier's verdict is its reward.** An episode is one heal attempt: what the healer saw, thought and did (`agent_runs`, APIs §3.11). Its label comes only from something that checked: the gate promoted or rolled back, the overlay held on the device, or a bench check passed. A device saying "fixed" with nothing behind it stays `unverified`, and never becomes a label *(the RLVR framing, [`research.md`](research.md) §4)*.
2. **The fleet keeps its own dataset.** Plain code keeps one standing dataset, `fleet-episodes`, snapshots it as verified episodes arrive, and exports it as training rows: Hermes trajectories with `completed` set to the verdict, plus SFT and KTO files and a dataset card. No person and no model is involved.
3. **A Naïve curator generalizes failures; the batch already generalizes fixes.** Romulus's batch job already turns every *held* fix into a fleet version (`cloud/src/romulus_cloud/services/batch.py:1-37`). Nothing handles the failures nobody fixed: a healer gave up, or a detector missed. When those cluster, a **Naïve agent session** reads the episodes and returns one typed **lesson**. Plain code checks it, and the existing gate (replay → simulated canary → promote or roll back) ships it to every arm as a healer skill.
4. **Weights are the same story, later.** The model is one more artifact through the same gate. Not built today; shown greyed out.

## The loop
```
 one device                     Romulus (plain code)                                   Naïve
 ──────────                     ────────────────────                                   ─────
 heal attempt ─► agent run ─►  EPISODE  verdict from a verifier only
                               (pass · fail · unverified · excluded)
                                  │
              ┌───────────────────┼─────────────────────────────┐
              ▼                   ▼                             ▼
     regression case        fleet-episodes dataset      failure cluster (same kind, ≥2 devices,
     (exists: guards        snapshot → export:          ≥1 fail/unverified, no fix behind it)
      every release)        trajectories · sft · kto ·        │ case file
                            card (no human)                   ▼
                                                        curator session (pi, typed) ──────►  agent "curator"
                                                              │ FleetLesson               ◄──  returns one lesson
                                                        CHECKS in code: evidence exists,
                                                        classes, prose-only skill, probe ↑
                                                              │ accept
                                                        harness candidate (+ skill) ─► GATE: replay (stub, says so)
                                                                                         → canary sim-2 (real)
                                                                                         → promote to every arm
                                                              │ promoted
                                                        publish skill v(n+1) ─────────────►  Naïve Skills page
```

## The hero story: a frozen sensor that says "clear"
- **The fault:** the pod's ultrasonic freezes, so the distance stops changing. It still reports `st=ok`.
- **Why it matters:** the arm's safety gate trusts any `ok` reading at or above `min_mm` as "clear" (`arm/safe_replay.py:66-75`), and nothing checks for a flat echo. So a frozen reading lets the arm move into whatever is there *(inferred from that code)*.
- **Who already knows:** the arm-sim watchdog has a frozen check (`prototypes/arm-sim/armsim/level1.py:30-44`, "the rule Olir's `check_ultrasonic` is missing"). The pod's `detect.py` doesn't (`firmware/healer/healer/detect.py:217-276`), and that's an open gap in the team status.
- **What the curator does:** it notices one healer knows what the others don't, and writes the lesson. The lesson ships as a skill to every arm's healer. Because it changes the safety behaviour, it's flagged `touches_safety` and goes to the simulated canary first (K17). The rule port for `detect.py` and `safe_replay.py` is staged for its owner (Olir), measured but not auto-merged.
- **Measured** (architect fork, 8:45 AM, standard library, 0.74 s; re-run by the probe script before anything is shown):
  - freezes of 4 s or more: **0 of 120** caught today → **120 of 120** with the port;
  - **0 new false alarms** on the committed 600-item `gen_pod` set, which includes five hard-negative families;
  - **limits:** a 3.5 s freeze is caught 4 of 30 times, and a freeze on out-of-range rows 0 of 30.

## What's core, and what isn't
| Built (tiers in [`build-plan.md`](build-plan.md)) | Not built, on purpose |
|---|---|
| **Episodes:** a pure view with the verdict table | Training weights (a LoRA on a seeded sim fleet would be theatre) |
| **Fleet dataset:** standing, auto-snapshotted, `trajectories` export | A `lessons` table (events are the spine) |
| **Curator:** a Naïve session, plain-code checks, a harness candidate | ML clustering (grouping by `kind` is enough at four devices) |
| **Probe:** an offline scorecard for a lesson's claim | Naïve Outcomes as the reward (the grader never sees the trace) |
| **`/learning` page** | Auto-merging rule code; the LeRobot export (cut at K1); webhooks; Apps |

## Why this is a Naïve beat
- The judges score "how much of the company runs without you" (`docs/hackathons.md:12`).
- The Technician beat ends on **1 human click**, because spending money needs a person (D30). Fleet learning ends on **0 human clicks**, because a verified software lesson doesn't (D6). Put side by side, the two show the company's autonomy policy without anyone saying it.
- The team already wrote this agent's job down: the CEO board's open **INSIGHTS** card, "Batches robot and embedded-agent traces, clusters failures… ranks them by fleet impact… proposing firmware/harness changes" (read-only MCP check, 8:25 AM).

## Documents
| Doc | What it holds |
|---|---|
| [`design.md`](design.md) | The spec: components, the Episode and its verdict table, the curator contract and checks, the training export, the rollout lane, the probe, settings, events, tests |
| [`demo.md`](demo.md) | Where the beat sits in the 1 PM run of show, the scripts, the page wireframe, the shot list, demo data, honesty rules, fallbacks |
| [`build-plan.md`](build-plan.md) | Tiers against the 10:00 freeze, the guaranteed minimum beat, the file list, coordination, cut order |
| [`review.md`](review.md) | The three reviews (execution risk, demo, coherence) and what changed |
| [`research.md`](research.md) | Every outside fact, with links |

## Decisions for Kent
1. **Go or no-go before 10:00**, and at which tier ([`build-plan.md`](build-plan.md)). The default is the guaranteed minimum beat now, and T0 only if a free Claude session starts by 9:10.
2. **The hero lesson and Olir's P0 rule.** The default is the frozen sensor. Ask Olir to hold his hand-written rule until after 1 PM, or land it as the curator's staged patch.
3. **Live Naïve writes:** the agent `curator` and the skill `romulus-healer-playbook`, at most $0.50 a session. The default is yes, after the Technician's G1.
4. **Verdict strictness.** The default is strict: only held, promoted or bench-checked counts as a pass.
5. **Where it runs:** its own stack (`COMPOSE_PROJECT_NAME=learn`, `API_PORT=8001`), never `tech` on :8000 or Olir's laptop.
