# Cloud platform: PRD

- **Status:** v2, Sat Nov 7, 2026. v1 was Kent's first draft; §9 maps every v1 item to v2.
- **Owner:** Kent.
- **Decisions:** references like (D5) point to [`cloud-platform-decisions.md`](cloud-platform-decisions.md).
- **Design:** [`cloud-platform-v0-plan.md`](cloud-platform-v0-plan.md).
- **Index:** [`README.md`](README.md).

---

## 1. Objective

The platform has two jobs:

1. **The heal loop:** the cloud half of a self-healing loop for our robot arms. It collects every incident, locks verified on-device fixes into new firmware and harness versions, and ships them back safely (D0, D2).
2. **The data asset:** everything the robots experience (traces, sensor data, software logs, agent sessions) is synced to the platform. It becomes a curated, versioned asset for training policy models, improving agent harnesses and developing firmware. That's a general-purpose use, not only healing (D16).

> *Every failure makes the fleet better by morning, and leaves training data behind.*

## 2. Principles

1. **The agent fixes; the batch locks it in.** On-device reasoning handles faults nobody has seen before. Fixes that hold become cheap, deterministic firmware (D0).
2. **Narrow loop, general data.** Every feature serves the heal loop, except the data asset, which is deliberately general-purpose (D2 refined by D16).
3. **Legible, never magical.** Every step leaves a readable "why" and links to what caused it. If it can't be followed, it didn't happen (D9).
4. **Local-first.** Devices never brick. Reports and recordings queue while offline and backfill later (D8).
5. **Patches are overlays; versions are immutable.** A local patch can always be dropped, and a version is never edited (D5a, D7).
6. **Automatic by default, with one human brake.** Rollouts promote and roll back on their own. The kill switch is the only human control (D6, D11).
7. **LLMs write prose and skills, never merged config.** The merge that produces a new version is deterministic (v0 plan §7.3).
8. **Compose, don't build.** Naïve handles agents, money and policy. Mobilerun handles phone calls. Standard formats (LeRobot, MCAP, JSONL) handle data (D12, D16).

## 3. Users

| User | What they need from the platform |
|---|---|
| **Us, running the arm company's fleet** (single tenant, D1) | See what broke and why; trust automatic rollouts; stop them instantly |
| **The device-side teammate** (D14) | Stable contracts to build the visor, firmware stack and embedded agent against |
| **The embedded agent on each arm** | A version to run, and a place to send incidents and recordings |
| **The Naïve ops agent** | Self-contained work orders it can act on without calling back into our platform (D12) |
| **Developers of policies, harnesses and firmware** (later, us) | Curated datasets and standard exports |
| **A judge** (the 3-minute user) | Follow one incident's story end to end; see that it ran without us |

## 4. User stories

Status: **v0** = in the hackathon build · **later** = after the hackathon · **cut** = out (D13).

| ID | Story | Status |
|---|---|---|
| **US1** | As the fleet operator, I want each arm to run self-healing software (firmware plus agent harness) that the platform versions and delivers, so every arm runs a known, recoverable version. | v0 (the device software itself is the teammate's, D14) |
| **US2** | As the fleet operator, I want a fix that works on one arm to reach every comparable arm automatically, once it has been checked, so the fleet improves without me. | v0 |
| **US3** | As the fleet operator, I want one screen showing every incident's story, from fault to fix or escalation, so I'm never lost. | v0 (incident timeline, D10) |
| **US3a** | As the fleet operator, I want hardware faults to turn into parts orders or technician bookings automatically, so arms get repaired without me making calls. | v0 (Naïve + Mobilerun, D12) |
| **US4** | As the fleet operator, I want one control that freezes all rollouts and pins every arm to its last good version. | v0 (D11) |
| **US5** | As the fleet operator, I want my systems to talk to each other (digital twins, ERP, other services). | cut (D13) |
| **US6** | As the fleet operator, I want traces, sensor data, software logs and agent sessions from every arm synced to the platform, so nothing an arm experiences is lost. | v0 (D16) |
| **US7** | As a developer of policies, harnesses or firmware, I want to label, filter and snapshot that data into versioned datasets and export them in standard formats, so I can train models, improve harnesses and test firmware against real incidents. | v0: labels, snapshots, regression corpus · later: training runs |
| **US8** | As the fleet operator, I want an arm to keep working and keep its data when the cloud can't be reached. | v0 (D8) |
| **US9** | As the fleet operator, I want every new version tested against all past incidents before it reaches an arm, so a new fix doesn't break an old one. | v0 (regression corpus replay) |

## 5. Requirements

| ID | Requirement | Stories | Priority |
|---|---|---|---|
| **R1** | **Version registry.** Create, read and retire firmware manifests (D3) and harness packages (D4). Versions are immutable. Track the assigned, running and last-good versions for each device. Each device has a `device_class` that defines which arms count as "comparable". | US1, US2 | P0 |
| **R2** | **Device sync contract.** Check-in (heartbeat and health), pull the assigned version, hash check, install result, and rollback on failure (D7). The visor on the device is the teammate's to build; the contract is ours. | US1, US8 | P0 |
| **R3** | **Incident reporting.** Every fault is reported, resolved or not, with the overlay diff, the agent's reasoning, the actions taken and the outcome. Reports are idempotent and may arrive late (D8). | US3, US6 | P0 |
| **R4** | **Telemetry sync.** Recordings (task episodes, incident windows, log chunks, agent traces) are uploaded as content-addressed blobs, stored on the device and forwarded when the cloud is reachable, and indexed with their version, overlay and incident links. | US6, US8 | P0 for incident evidence; P1 for all episodes |
| **R5** | **Batch lock-in.** Held overlays become candidate firmware and harness versions. The merge is deterministic; an LLM writes the "why" and a harness skill. | US2 | P0 |
| **R6** | **Gate.** Replay against the regression corpus, then a canary, then automatic promotion to all devices in the class, or automatic rollback (D6). | US2, US9 | P0 (replay against all past incidents is P1) |
| **R7** | **Kill switch.** Freeze rollouts and pin every device to its last good version. Every use is recorded (D11). | US4 | P0 |
| **R8** | **Work orders through Naïve.** Hardware faults become cards on the Naïve board. A Naïve agent with the Mobilerun MCP makes the call. Status comes back to the platform, and spend caps live in Naïve's policy (D12). | US3a | P0 |
| **R9** | **Curation and datasets.** Automatic labels (from incidents and outcomes) and manual labels; versioned, immutable dataset snapshots; exports (regression corpus, agent-trace JSONL, LeRobotDataset). | US7, US9 | P1 (LeRobot export is P2) |
| **R10** | **Event log and incident timeline.** An append-only log of every step, with a "why". One screen rebuilds each incident's story (D9, D10). | US3 | P0 |
| **R11** | **Runs locally with one command.** Docker Compose, plus fake devices for testing and demos (D17). | all | P0 |
| **R12** | **Overnight autonomy.** Runs unattended overnight; a morning digest is computed from the event log. | US2, US3 | P0 |

**Non-functional requirements:**
- Single tenant, but every table has a `fleet_id` (D1).
- No auth in v0; the platform is reachable only on the bench network.
- An incident shows on the timeline within about 5 s of arriving.
- `make up` gets a clean laptop running in under 5 minutes.
- The blob store stays under a set disk budget (§7 of [`cloud-platform-telemetry.md`](cloud-platform-telemetry.md)).

## 6. Non-goals for v0

- Digital twins and ERP (US5).
- Billing.
- A trust ledger.
- Training policy models (we export datasets only).
- Multi-tenancy and real auth.
- Version signing.
- Generic fleet-management features (asset inventory, user management, generic log search).
- Live video streaming or teleop.
- Anything in the safety layer (D13, `~/git2/docs/VISION.md`).

## 7. Success criteria for the hackathon

1. Core flows F1–F7 and F10 pass end to end with fake devices. F2 and F3 also pass with the real arm ([`cloud-platform-flows.md`](cloud-platform-flows.md)).
2. The overnight run goes at least 6 h unattended, and the morning digest shows the counts: incidents, heals, promotions, rollbacks, work orders and human actions.
3. At least one dataset snapshot is exported, and the gate replays against the regression corpus built from the overnight run.
4. A judge can open one incident and follow it from fault to fix without help.

## 8. Traceability

| Story | Requirements | Design (v0 plan) | Flows | Milestone ([build plan](cloud-platform-build-plan.md)) |
|---|---|---|---|---|
| US1 | R1, R2 | §4, §7.1–7.2 | F1 | M0–M1 |
| US2 | R1, R5, R6 | §7.3–7.4 | F3, F4 | M3 |
| US3 | R3, R10 | §4, §7.6 | F2 | M1–M2 |
| US3a | R8 | §7.5, [Naïve integration](cloud-platform-naive-integration.md) | F6 | M5 |
| US4 | R7 | §7.4 | F5 | M3 |
| US6 | R3, R4 | §7.7, [telemetry](cloud-platform-telemetry.md) | F7 | M4 |
| US7 | R9 | [telemetry](cloud-platform-telemetry.md) §5–6 | F8 | M6 |
| US8 | R2, R4 | §7.2 | F9 | M7 |
| US9 | R6, R9 | §7.4 | F8 | M6 |
| (all) | R11, R12 | [architecture](cloud-platform-architecture.md) | F10 | M0, M7 |

## 9. What changed from v1

| v1 item (Kent's first draft) | v2 | Why |
|---|---|---|
| Objective: "general purpose platform for managing the software and operations of robotic systems" | Two jobs: a narrow heal loop and a general-purpose data asset | D2 narrowed the objective; D16 brought "general purpose" back for data only |
| Story 1: custom embedded self-healing software | US1. The platform versions and delivers it; the teammate builds it | D14 |
| Story 2: continuous updates from comparable systems | US2. "Comparable" becomes a `device_class` field; the gate checks before promoting | D6; adds R1's `device_class` |
| Story 3: dashboard for operating robots | US3. Incident timeline first | D9, D10 |
| Story 3a: ordering parts and dispatching technicians | US3a. Through Naïve and Mobilerun, not our own agents | D12 |
| Story 5 (tentative): systems communicate (digital twins, ERP) | US5, cut | D13 |
| — | **New:** US4 kill switch, US6 telemetry, US7 datasets, US8 offline, US9 regression replay | D11, D16, D8, round-2 feedback |
| Req 1: CI/CD for firmware and agent | R1 registry + R6 gate (replay → canary → promote) | D6 |
| Req 1a: visor layer pulls and validates | R2 contract. The teammate builds the visor | D7, D14 |
| Req 1b: cloud endpoints for downloading versions | R2 pull routes ([APIs](cloud-platform-apis.md) §3) | — |
| Req 2: firmware pushes logs and environmental data | R3 incidents + R4 telemetry sync | D16 expands it into a data asset |
| Req 3: batch analysis creates new firmware and harness versions (and model) | R5 lock-in. "And model" becomes dataset export (R9); training is a non-goal | D13 |
| Req 4: phone calls with Mobilerun for parts or appointments | R8, through a Naïve agent | D12 |
| — | **New:** R7 kill switch, R9 datasets, R10 event log, R11 local Docker, R12 overnight | D11, D16, D9, D17 |
