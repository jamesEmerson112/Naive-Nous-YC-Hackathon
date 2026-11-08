# Fleet learning: every fault happens once

_Kent's request, planned by a Claude session with sub-agents (four investigations, two designs, three reviews), Sun Nov 8, 8:20–8:55 AM PST. Status: **v2, after review** ([`review.md`](review.md)); nothing built. The folder name is Kent's. The feature is "Fleet learning", and "Every fault happens once" is only the stage tagline._

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

## What lands today (details in [`build-plan.md`](build-plan.md))
**Before the 10:00 freeze, three lanes run in parallel, none of them needing the gate,** because the hero is staged:
- **A · evidence:** the probe's real numbers, plus one live cluster-review session in the Naïve Studio, saved as a fixture.
- **B · data:** labelled runs, the standing dataset and the `trajectories` export, on the `learn` stack.
- **C · page:** `/learning` with the staged hero and the data strip.

**On stage:** a sentence in beat 5. **At the 11:00 table:** the walk-through.

**After 1 PM:** the skill lane through the gate, live mode, and the rule lane. The full T0 is 70–90 minutes, too long to build before the freeze.

## Documents
| Doc | What it holds |
|---|---|
| [`design.md`](design.md) | The spec: labelled runs and the verdict table, the fleet dataset and export, cluster review (trigger, case file, proposal schema, checks, routing), rollout lanes, the probe, draft D31, settings, events, tests |
| [`demo.md`](demo.md) | Where it sits in the 1 PM run of show, the scripts, the page wireframe, the table walk-through, honesty rules, fallbacks |
| [`build-plan.md`](build-plan.md) | Three lanes before the freeze (evidence, data, page), then T0 → T2 after 1 PM, gotchas for the build sessions |
| [`review.md`](review.md) | The three reviews (execution risk, judges' seat, coherence) and what changed |
| [`research.md`](research.md) | Every outside fact, with links |

## Decisions for Kent
1. **Today's scope:** lanes A, B and C before the freeze (default), or A alone if you'd rather keep the morning for rehearsal ([`build-plan.md`](build-plan.md) §2).
2. **D31, drafted in [`design.md`](design.md) §8:** cluster review is a Fleet Doctor mode with the same limits (D25). A prose skill that names no safety path isn't held by the K17 block; anything touching safety is staged for its owner. Default: accept.
3. **The Naïve agent `fleet-doctor`** (`pi`, no tools, $0.50 a task), created through the Naïve MCP with your OK. Default: yes, at 9:00, so lane A can start.
4. **Olir's P0 frozen-ultrasonic rule:** ask him to hold it until after 1 PM, or land it as the staged proposal with the probe's numbers. Default: the latter, if he's free.
5. **Where it runs:** its own stack (`COMPOSE_PROJECT_NAME=learn`, `API_PORT=8001`), and **no merge into main before 1 PM**, because Kent's `tech` API hot-reloads from the main checkout.
