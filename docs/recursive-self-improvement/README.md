# Fleet learning: every fault happens once

_Kent's request, planned by a Claude session with sub-agents (four investigations, two designs, three reviews), Sun Nov 8, 8:20–8:55 AM PST. Status: **built and run live on the `learn` stack, Sun Nov 8, 10:04 AM** (PRs #74–#92, see *What's built* below); the plan was v2, after review ([`review.md`](review.md)). The folder name is Kent's. The feature is "Fleet learning", and "Every fault happens once" is only the stage tagline._

## In one sentence
**Every heal is labelled by whatever checked it, the labelled runs become the fleet's training set on their own, and when a failure has no working fix in the fleet, a Naïve agent session proposes a rule or a skill that ships only through the same gate as every other change.**

## Words used here
| Word | Meaning |
|---|---|
| **Labelled run** | One heal attempt (an agent run, APIs §3.11) plus a **verdict** that only a verifier gives: `pass`, `fail`, `unverified` or `excluded` |
| **Verifier** | Something that checked, never a model's opinion: the gate promoted or rolled back, the fix held on the device, or a bench check passed |
| **Fleet dataset** | One standing dataset that plain code snapshots and exports as labelled runs (Hermes trajectories, `completed` = verdict) |
| **Failure cluster** | Failed or unverified runs of one fault kind that **no held fix** covers |
| **Cluster review** | A Fleet Doctor mode that runs as a Naïve agent session. It reads a failure cluster and returns one typed **proposal**: a rule for a missed detection, or a skill for a failed heal |

The rest is the existing vocabulary: candidate, gate, canary, version (`docs/cloud-platform-planning/`).

## The loop
```
 heal attempt on one device
        │
        ▼
 LABELLED RUN ── verdict from a verifier only
        │
        ├──► regression case        (exists: every fixed incident guards every release)
        ├──► fleet dataset          (new, plain code, 0 clicks: snapshot → trajectories export)
        └──► failure cluster? ──► cluster review (Naïve session) ──► proposal, checked in code
                                         ├─ missed detection → proposed RULE  → gate: real replay → canary → every device
                                         ├─ failed heal      → proposed SKILL → gate: canary → every device
                                         └─ touches safety   → staged for its owner (D25): never ships on its own
```

## Who decides what (the company's autonomy policy, made visible)
| Change | Decided by | Human clicks |
|---|---|---|
| Dataset snapshots and training exports | plain code | 0 |
| A proposed rule or skill that doesn't touch safety | the gate (replay, simulated canary, promote or roll back; D6) | 0 |
| Anything that touches safety | its owner, with the measured evidence attached (D25) | 1 review |
| Spending money | a person (the Technician's D30) | 1 click |

**After a bad change:** a rolled-back proposal pauses cluster review for that fault kind until a person clears it. That's the pitch's "each action type earns autonomy and loses it after a bad fix" (CLAUDE.md).

## The hero story: a frozen sensor that says "clear"
- **The fault:** the pod's ultrasonic freezes. The distance stops changing, but each reading still says `st=ok`.
- **Why it matters:** the arm's start gate counts any `ok` reading at or above `min_mm` as "clear" (`arm/safe_replay.py:66-75`), and nothing checks for a flat echo. So a frozen sensor would let the arm move into whatever is there *(inferred from that code)*.
- **Who already knows:**
  - The arm-sim watchdog has a frozen check (`prototypes/arm-sim/armsim/level1.py:30-44`, "the rule Olir's `check_ultrasonic` is missing").
  - The pod's `detect.py` doesn't (`firmware/healer/healer/detect.py:217-276`). The team status lists this as an open gap.
  - So **one failure on one device** is enough: another part of the fleet already handles it.
- **What fleet learning does:**
  1. Cluster review names a missed detection and proposes porting the watchdog's check.
  2. A probe measures the port offline.
  3. Because the port touches the arm's start gate, it's **staged for its owner (Olir) with the numbers attached**, not shipped (D25).
  4. In the stage line, the agent knows where its autonomy stops.
- **Measured** (8:45 AM, standard library, 0.74 s; the probe script must reproduce these before anything is shown):
  - freezes of 4 s or more: **0 of 120** caught today, **120 of 120** with the port;
  - **no new false alarms** on the 600-item `gen_pod` set, which includes five hard-negative families;
  - **still missed:** a 3.5 s freeze is caught only 4 of 30 times, and a freeze on out-of-range rows 0 of 30.

## What's core, and what isn't
| Core | Not built, on purpose |
|---|---|
| **Labelled runs:** a pure view with the verdict table, the one spine the digest, the Doctor and James's Level 3 can all read | Training weights (a LoRA on a seeded sim fleet would be theatre) |
| **Fleet dataset:** standing, snapshotted on its own, `trajectories` export | New tables (events are the spine) |
| **Cluster review:** a Naïve session, plain-code checks, routing by gap type | A new agent beside the Doctor; ML clustering; Naïve Outcomes as the reward |
| **Probe:** an offline scorecard for a proposed rule | Auto-merging rule code; the LeRobot export (cut at K1) |
| **`/learning` page** | Webhooks, Apps, cron (Naïve can't reach the laptop cloud) |

## Why it's a Naïve beat
- The judges score "how much of the company runs without you" (`docs/hackathons.md:12`). The table above *is* that answer, with the one place a person steps in for each kind of change.
- The team already wrote this agent's job down: the CEO board's open **INSIGHTS** card, "Batches robot and embedded-agent traces, clusters failures… ranks them by fleet impact… proposing firmware/harness changes" (read-only Naïve MCP check, 8:25 AM).

## What's built (Sun Nov 8, 9:05–10:10 AM PST)
Built by one coordinating Claude session with parallel sub-agents, each PR reviewed before merge. The live run and how to show it are in [`runbook.md`](runbook.md).

| Piece | PR | What it does |
|---|---|---|
| Probe + live spike | #75 | `cloud/scripts/lesson_probe.py` measures the frozen-echo port: **0 → 120 of 120** freezes caught, no new false alarms on 410 healthy windows; still misses a 3.5 s freeze (4 of 30) and a freeze on out-of-range rows (0 of 30). Created the Naïve agent `fleet-doctor` |
| Demo data | #76 | `cloud/scripts/learning_demo.py seed`: 9 real-shape runs on the SIM fleet (frozen hero `us_frozen`, skill hero `servo_read_error`) |
| Labelled runs + dataset | #78 | `domain/runs.py` verdict table, `services/runs.py`, the standing `fleet-runs` dataset with a `trajectories` export (Hermes format, `completed` = verdict), `LEARNING_MODE` off · data · fixture · live |
| Cluster review | #74, #82 | `domain/review.py` (trigger, case file, schema, checks, routing) and `services/review.py`: a Naïve session per cluster; rules and anything touching safety are **staged** for the owner; a non-safety skill becomes a harness candidate through the gate |
| Naïve Skills mirror | #81 | after the gate promotes, the healer playbook goes to Naïve Skills as a new version, plus one comment on the CEO board's agent-specs card |
| `/learning` page | #77, #86, #90 | the hero, the stages, the computed numbers, the data strip, the `?case=` switcher between reviews, the rollout chips |
| Live-run polish, runbook | #89, #92 | 5 s learning tick; the publisher goes live with `LEARNING_MODE=live`; [`runbook.md`](runbook.md) |

**The live run (10:04 AM, `learn` stack on :8001):**
- `us_frozen`: one Naïve session, 26 s and 2¢, proposed porting the watchdog's check. It touches `arm/safe_replay.py`, so it was **staged for Olir** (D25).
- `servo_read_error`: one Naïve session, 22 s and 3¢, wrote a skill. Stub replay, then a 32.5 s canary on `sim-2`, then **promoted to every so101 arm 57 s after the session opened, with 0 human clicks**. Naïve Skills `romulus-healer-playbook` went v1 → v2.
- Data: 9 runs → 4 labelled by a verifier (2 pass, 2 fail) → 4 training rows.

**Cut, on purpose:** `sft.jsonl`/`kto.jsonl`, the repeat-rate number (the page shows "—"), the rule lane through the gate (T2, needs Olir's `distance_spread_mm`), Naïve Files, and the Hermes `on_session_end` plugin (for the Hermes entry, due Wed).

**On stage:** a sentence in beat 5 ([`demo.md`](demo.md) §2). **At the 11:00 table:** the walk-through in [`runbook.md`](runbook.md).

## Documents
| Doc | What it holds |
|---|---|
| [`design.md`](design.md) | The spec: labelled runs and the verdict table, the fleet dataset and export, cluster review (trigger, case file, proposal schema, checks, routing), rollout lanes, the probe, draft D31, settings, events, tests |
| [`demo.md`](demo.md) | Where it sits in the 1 PM run of show, the scripts, the page wireframe, the table walk-through, honesty rules, fallbacks |
| [`runbook.md`](runbook.md) | The live run's measured numbers, the `learn` stack commands, the table script, fallbacks |
| [`build-plan.md`](build-plan.md) | Three lanes before the freeze (evidence, data, page), then T0 → T2 after 1 PM, gotchas for the build sessions |
| [`review.md`](review.md) | The three reviews (execution risk, judges' seat, coherence) and what changed |
| [`research.md`](research.md) | Every outside fact, with links |

## Decisions for Kent
_The build session took every default below so it could run; Kent can reverse any. (1) All lanes, then T0 and the T1 Skills mirror. (2) D31 is implemented as drafted, but it's still a draft for Kent to lock. (3) `fleet-doctor` is `agt_8r84z0242xzrm5m7qnay38fsa2`. (4) Olir was asked in CLAUDE.md to hold his rule; the port is staged for him. (5) The `learn` stack runs on :8001, and merges to main turned out to be safe._

1. **Today's scope:** lanes A, B and C before the freeze (default), or A alone if you'd rather keep the morning for rehearsal ([`build-plan.md`](build-plan.md) §2).
2. **D31, drafted in [`design.md`](design.md) §8:** cluster review is a Fleet Doctor mode with the same limits (D25). A prose skill that names no safety path isn't held by the K17 block; anything touching safety is staged for its owner. Default: accept.
3. **The Naïve agent `fleet-doctor`** (`pi`, no tools, $0.50 a task), created through the Naïve MCP with your OK. Default: yes, at 9:00, so lane A can start.
4. **Olir's P0 frozen-ultrasonic rule:** ask him to hold it until after 1 PM, or land it as the staged proposal with the probe's numbers. Default: the latter, if he's free.
5. **Where it runs:** its own stack (`COMPOSE_PROJECT_NAME=learn`, `API_PORT=8001`), and **no merge into main before 1 PM**, because Kent's `tech` API hot-reloads from the main checkout.
