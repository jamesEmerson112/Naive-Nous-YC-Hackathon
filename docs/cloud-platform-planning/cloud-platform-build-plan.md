# Cloud platform: build plan and risk management

How the cloud platform gets built between now (**Sat 3:30 PM**) and the code freeze (**Sun 10:00 AM**). Hacking ends Sun 11:00 AM and presentations start at 1:00 PM ([`docs/hackathons.md`](../hackathons.md)).

- **What "working" means:** [`cloud-platform-flows.md`](cloud-platform-flows.md). Its tracker is the source of truth for progress.
- **How the code is laid out:** [`cloud-platform-architecture.md`](cloud-platform-architecture.md).
- **Assumption:** Kent builds alone, with coding agents. Times are estimates *(inferred)*.

---

## 1. What matters most, in order

Judges score "how much of the company runs without you" (`~/git2/docs/STRATEGY.md` §1), and the platform's top value is legibility (D9). Attention therefore goes to these, in this order:

1. **The hero loop, live and readable:** F2 → F3 → F4 on the incident timeline. If only one thing works, it's this.
2. **The overnight run (F10).** "It ran while we slept" is the strongest autonomy evidence (`~/git2/docs/1-hackathon-plan.md` §0). It has to **launch by ~12:30 AM**, so everything it needs comes first.
3. **Naïve integration (F6).** Naïve is mandatory, and the Mobilerun call is the sponsor-track story.
4. **Telemetry as evidence (F7).** Each incident's story shows its recordings. This is the first slice of the data asset (D16).
5. **The dataset (F8).** It's built on Sunday morning from the data the overnight run produced. Ordering it last makes the "every failure becomes training data" story true by construction.

**Rule:** a lower item never takes time from a higher one. When behind, cut from the bottom of §6.

## 2. Timeline

| When | Milestone | Done when | Flows |
|---|---|---|---|
| Sat 3:30–4:15 | Docs round 2 (this) | Kent has reviewed the docs; D18–D20 decided or deferred | — |
| **4:15–5:15** | **M0 · Scaffold, contracts, spikes** | `make up` serves `/healthz`; contracts v0.1 sent to the device teammate; one real Naïve card created by API; one real LLM call | — |
| **5:15–6:45** | **M1 · Walking skeleton** | Fake device does F1 and F2 against the API; `GET /v1/incidents/{id}/story` returns the chain | F1, F2 |
| **6:45–7:30** | **M2 · Incident timeline page** | Someone else can follow one incident on screen without help | F2 |
| **7:30–9:30** | **M3 · Batch, gate, kill switch** | F3, F4 and F5 pass with fake devices | F3, F4, F5 |
| **9:30–10:30** | **M4 · Telemetry ingest** | Incident windows, logs and agent traces upload and show as evidence | F7 |
| **10:30–11:30** | **M5 · Work orders → Naïve** | F6 passes in fake mode; one live card appears on the real board | F6 |
| **11:30–12:30 AM** | **M7 · Overnight launch** | Chaos schedule running, settings switched to overnight, laptop kept awake, `demo` tag pushed | F10 (F9 if quick) |
| Overnight | Nobody touches it | — | F10 |
| Sun 7:00–7:30 | Read the digest; fix whatever broke | Digest numbers written down | F10 |
| **7:30–9:00** | **M6 · Datasets** | Snapshot plus regression-corpus export from the overnight data; gate replays the corpus | F8 |
| **9:00–10:00** | **Real arm + polish** | F2 and F3 run on the real arm (if the device side is ready); timeline polish | ⭐ marks |
| **10:00** | **CODE FREEZE** | Bug fixes only. `demo` tag moved to the frozen commit | — |
| 10:00–11:00 | Rehearse | Demo walk-through ×3 (flows doc, last section); backup video recorded | — |

M6 deliberately comes after M7 (§1, item 5).

**M8 (coding agent) has no fixed slot.** It starts as soon as its blockers B1–B7 clear (§3, M8), and it is the first P1 item to pick up. Its contract pieces ship in M0. The earliest real start is after M3, because blocker B6 is the gate.

## 3. Milestone tasks

Tick these off in this file as you go. Each milestone ends with its flows' tracker rows updated.

### M0 · Scaffold, contracts, spikes
- [ ] uv workspace: root `pyproject.toml`, `packages/contracts`, `cloud`, `tools/fake_device` (architecture §3)
- [ ] `compose.yaml` (db, api, worker; `sim` profile), `cloud/Dockerfile`, `Makefile`, `.env.example` with fake modes
- [ ] `settings.py`, `db.py`, `models.py` (every table, even the empty ones), `events.record()`
- [ ] Contracts v0.1: the Pydantic models from APIs §2, including the code-package and `code_patch` contract (§2.10, D21), plus `rules.evaluate()` with unit tests
- [ ] `cloud/CLAUDE.md` (architecture §9)
- [ ] **Naïve spike (15 min):** with a real API key, create and read one card on the company board ([Naïve integration](cloud-platform-naive-integration.md) §5). Write down the board ID and the ops agent's name
- [ ] **LLM spike (10 min):** one live call from the `llm` adapter
- [ ] **Hand-off:** send the contracts to the device teammate (APIs doc §2–3 plus the package path), and agree on a time for the first real check-in

### M1 · Walking skeleton
- [ ] `make seed`
- [ ] Device API: check-in, pull firmware and harness, install result, incidents, overlay status (APIs §3)
- [ ] Services: `registry`, `devices`, `incidents`, each writing Events
- [ ] `domain/story.py` and `GET /v1/incidents/{id}/story`
- [ ] Fake device: check-in loop, install, scripted incident with overlay, then held
- [ ] Flow tests F1 and F2 (API level)

### M2 · Incident timeline page
- [ ] `/` timeline (htmx polling), `/incidents/{id}` story, Freeze button wired to an empty route
- [ ] Every step shows its `why`; times are local and readable

### M3 · Batch, gate, kill switch
- [ ] `domain/merge.py`, `domain/gate.py` (replay against one incident, canary verdict), with unit tests
- [ ] `services/batch.py` (merge plus LLM explanation and skill; fake LLM by default) → candidates
- [ ] `services/gate.py` state machine, plus `worker.py` loop
- [ ] `services/fleet.py`: freeze and unfreeze; pin to last good
- [ ] Fake device bad-candidate mode
- [ ] Flow tests F3, F4 and F5; the story ends in "→ fleet" or "→ rolled back"

### M4 · Telemetry ingest
- [ ] `adapters/blobstore.py` (`FsBlobStore`, content-addressed)
- [ ] Recording routes: metadata, then blob upload with sha256 check (APIs §3.7)
- [ ] `domain/labels.py` automatic labels; evidence links in the story
- [ ] Fake device uploads an incident window, a log window and an agent trace. These are small synthetic files: a real MCAP file (written with the `mcap` library), a JSONL trace, and a *placeholder* tar labeled `lerobot_v3_tar`. Real LeRobot datasets come from the arm, because generating them needs the heavy `lerobot` package ([telemetry](cloud-platform-telemetry.md) §2)
- [ ] `metric_samples` written on every check-in (P1, one insert)
- [ ] Flow test F7

### M5 · Work orders → Naïve
- [ ] `domain/triage.py` + `cloud/config/parts.yaml`, `services/workorders.py` (outbox, spend caps in code), `adapters/naive.py` (Fake and Http)
- [ ] Worker: post cards with an `Idempotency-Key` header, poll each open card by id, parse the `RESULT:`/`STOPPED:` note, and show the status in the story ([Naïve integration](cloud-platform-naive-integration.md) §3.1–3.3)
- [ ] Flow test F6 (fake); one live card created by hand during the M5 check

### M7 · Overnight launch
- [ ] Chaos schedule in the fake device (flows doc F10)
- [ ] Overnight `.env` values; `restart: unless-stopped` on api and worker
- [ ] `domain/digest.py` and `make digest`
- [ ] `telemetry.enforce_budget()`
- [ ] Keep the laptop awake (`caffeinate -dims`, power connected, lid open or clamshell with display); Docker Desktop set to start on login
- [ ] Commit, tag `demo-overnight`, push
- [ ] (If quick) F9 offline test

### M8 · Coding agent for code patches (P1, starts as soon as nothing blocks it; D21, D21a)

**Goal:** a held overlay with a `code_patch` → a coding agent writes a clean change and runs the device tests → a candidate with a new code version → the same gate (D6). Flow F11.

**Rule:** M8 is the first P1 item to pick up. It starts the moment every blocker below is 🟢, and runs in any slack, as long as no P0 milestone is late. Update this table at every checkpoint.

| # | Blocker | Owner | Status | How it clears |
|---|---|---|---|---|
| B1 | **Device code is in the repo with a test suite the agent can run** (`device/`, one command such as `uv run pytest`) | Device teammate | 🔴 | The teammate pushes code and tests, and they pass in a clean container |
| B2 | **Code-package contract agreed:** the device fetches code by version from the platform (`GET /v1/code/{rev}/package` + sha256), and the visor installs it with hash check, health check and rollback (D7) | Kent proposes (M0) · teammate implements in the visor | 🔴 | In contracts v0.1 (APIs §2.10, §3); the teammate confirms |
| B3 | **`code_patch` format agreed:** a unified diff against `code_rev`, with paths relative to `device/` | Kent proposes (M0) · teammate's agent produces it | 🔴 | In contracts v0.1 (APIs §2.10); one real example from the device agent |
| B4 | **Which coding agent and LLM, plus access** (Q2) | Kent | 🔴 | Q2 decided; key in `.env`; one headless run works |
| B5 | **Sandbox:** a `coder` container that mounts a scratch copy of `device/`, can reach only the LLM API, and is run by the worker | Kent | 🔴 | Built after M0; one dry run that edits and tests a toy change |
| B6 | **The gate exists** (M3): candidates, replay, canary and promote are working | Kent | 🔴 | F3 and F4 🟩 |
| B7 | **A canary that runs real device code.** The fake devices don't execute device code, so a code candidate needs the teammate's sim twin (MuJoCo SO-101 running the device stack) or a supervised real-arm canary | Device teammate (sim twin) · Kent (gate rule) | 🔴 | A sim-twin device checks in with `kind = sim` and can install a code package |

**Tasks** (each can start as soon as its own blockers clear):
- [ ] (M0) Code-package and `code_patch` contract in v0.1 (APIs §2.10); `code_versions` table; `POST /v1/code`, `GET /v1/code/{rev}/package` *(B2, B3)*
- [ ] `coder` container + `adapters/coder.py` (Protocol + Fake) *(B4, B5)*
- [ ] `services/batch.py` third path: `code_patch → coder → code version → candidate`, with the coder's test output recorded in the candidate's `why` *(B1, B6)*
- [ ] Gate rule: a code candidate first runs the device tests (in the sandbox), then goes to a canary that can run real code (B7). If no such canary exists, it stops at `awaiting_canary` and says so on the timeline
- [ ] Fake device: one scripted incident with a `code_patch` for the demo
- [ ] Flow test F11

### M6 · Datasets (Sun morning)
- [ ] Labels: add or remove on a recording (ops API and a small Data page)
- [ ] Snapshots: create an immutable dataset version from a filter
- [ ] Exports: `regression_corpus` (P1), `traces_jsonl` (P1), `lerobot` (P2) ([telemetry doc](cloud-platform-telemetry.md) §6)
- [ ] The gate replays against the regression corpus
- [ ] Flow test F8

## 4. Checkpoints (every ~90 minutes, ≤10 minutes each)

1. Run `make flows`, and update the tracker in the flows doc. Update the M8 blocker table (§3); if every blocker is 🟢 and no P0 milestone is late, start M8.
2. Click through the hero path (F2 → F3 → F4) on the timeline, as a judge would.
3. Re-read §5 (risks). Has anything changed likelihood?
4. Behind schedule? Cut from the bottom of §6 and write the cut into the tracker's notes.
5. Write down any decision made in the decision log, and commit.

Fixed checkpoints: **5:15 PM** (M0), **7:30 PM**, **9:30 PM**, **11:30 PM**, **12:30 AM** (overnight launch: go/no-go), **7:30 AM**, **9:00 AM**, **10:00 AM** (freeze).

## 5. Risk register

| # | Risk | Likelihood | Impact | Mitigation | Trigger to act |
|---|---|---|---|---|---|
| K1 | **Telemetry and datasets eat the hero-loop time** | High | High | P0 telemetry is incident evidence only; episodes are P1; datasets wait until after the overnight run; the LeRobot export is cut first | M4 runs past 10:45 PM |
| K2 | **The device side isn't ready, or the contracts drift** | High | Medium | The fake device proves the contracts; `CONTRACT_VERSION`; the fake fleet carries the demo; the real arm is ⭐, not 🟩 | No real check-in by 9:30 PM |
| K3 | **Naïve unknowns:** key, scopes, board ID, ops agent, Mobilerun MCP setup, reachability | Medium | High | 15-minute spike in M0; fake mode; self-contained cards; outbound only; ask staff early ([Naïve integration](cloud-platform-naive-integration.md) §7) | The spike fails |
| K4 | **The overnight run dies silently** (laptop sleeps, Docker stops, worker crashes) | Medium | High | `caffeinate`; Compose restart policy; the worker resumes from DB state; digest; optional 3 AM check | — |
| K5 | **Venue Wi-Fi or LLM latency** during the demo | High | Medium | Fake modes by default; templated explanations if the LLM is slow; our own router (`CLAUDE.md`) | LLM call over 5 s at the M5 check |
| K6 | **A gate bug sends a bad version to the real arm** | Low | High | The canary is always simulated; the real arm is `normal`; the device rolls back on failed health (D7); the kill switch; the F4 test | — |
| K7 | **Schema change after the overnight run wipes the data** | Medium | Medium | Additive-only after M7 (architecture §7); `make reset` refuses without `FORCE=1` | — |
| K8 | **Blobs fill the disk** | Medium | Medium | `BLOB_BUDGET_GB`; small frames; episodes optional | Disk over 80% |
| K9 | **Clock skew between devices and the laptop** muddles ordering | Medium | Low | Sort by `occurred_at`, keep `received_at`; device NTP | Stories out of order |
| K10 | **Breaking the demo while hacking on Sunday** | Medium | High | `demo-overnight` tag; freeze at 10:00; rehearse from the tag | — |
| K11 | **No auth on the LAN** | Low | Low | Our own travel router; no tunnel by default | Tunnel turned on |
| K12 | **One developer, about 11 hours** | High | High | This plan's order; checkpoints; cut lines | Any milestone 30+ minutes late |
| K13 | **Overnight chaos phones a real supplier at 3 AM** | Medium | High | Overnight `NAIVE_MODE=fake`, or point `parts.yaml` at a teammate's phone; cap orders per day ([Naïve integration](cloud-platform-naive-integration.md) §6) | Before the M7 launch |
| K14 | **The dataset feature schema drifts**, so LeRobot `merge` fails | Medium | Medium | Freeze schema v0.1 tonight with the teammate ([telemetry](cloud-platform-telemetry.md) §8) | Teammate changes a feature |
| K15 | **Camera frames include judges and other people** | High | Medium | Low-res, no audio, local only, no public push, `--redact` traces ([telemetry](cloud-platform-telemetry.md) §10) | — |
| K16 | **Naïve spend caps can't live in Naïve policy** (the earlier assumption was wrong) | Certain | Medium | Caps in our code; over-cap behaviour is D19b ([Naïve integration](cloud-platform-naive-integration.md) §3.4) | — |

## 6. Cut lines

| Must (P0) | Should (P1) | Could (P2) | Won't (this weekend) |
|---|---|---|---|
| F1–F5 with fake devices | F6 live with the real Naïve agent and Mobilerun | Uploading every task episode | Policy training |
| Incident timeline with a "why" on every step | F8 snapshots and regression-corpus replay | `traces_jsonl` export | Push to the HF Hub |
| F6 in fake mode | F9 offline backfill | LeRobotDataset export | Tunnel / inbound MCP for Naïve |
| | **F11 coding agent for code patches (first P1; D21a)** | | |
| F7 incident evidence | Real arm for F2 and F3 | Fleet grid and version-pipeline pages | Auth, billing, trust ledger |
| F10 overnight run plus digest | Data page for labels | Rerun viewer embed | Digital twins, ERP |

When behind, cut from the right. Never cut a Must to add a Could (`~/git2/docs/1-hackathon-plan.md` §4).

## 7. Dependencies on other people

| Who | What we need | By when | Fallback |
|---|---|---|---|
| Device teammate (TBD, D14) | M8 blockers B1 (device code and tests in the repo), B2/B3 (code-package and `code_patch` contract), B7 (sim-twin canary). Agreement on contracts v0.1, **including the frozen dataset schema** (telemetry §8); first real check-in; one real incident with an overlay and recordings; answers to telemetry §11's open questions (rollout or scripted stack; Hermes or not) | 5:15 PM / 9:30 PM / Sun 9:00 AM | Fake fleet only; the real arm stays off the demo path |
| Whoever owns Naïve for the team | API key with board scopes; `fleet-ops` declared in `naive/naive.config.json`; Mobilerun bearer in the vault, bound to fleet-ops; a Naïve budget on fleet-ops ([Naïve integration](cloud-platform-naive-integration.md) §3.5–3.6) | 5:15 PM (key) / 11:30 PM (agent) | `NAIVE_MODE=fake`; show the card payload instead |
| LLM access | API key, or a RunPod endpoint for batch explanations | 5:15 PM | `LLM_MODE=fake` (templated text) |

## 8. How this plan changes

- The tracker in the flows doc changes at every checkpoint.
- This file changes when a milestone moves or something is cut (note why in the tracker's notes column).
- New decisions go in the decision log. Changes to the design go in the v0 plan, the APIs doc or the architecture doc, and **one** place only (README "single source" rule).
