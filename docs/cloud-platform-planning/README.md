# Cloud platform planning

Planning docs for the Romulus cloud platform: the cloud half of the arm company's self-healing loop, plus the data asset it produces. Kent owns the initial build. Last updated Sat Nov 7, ~7:35 PM PST.

## Reading order

| # | Doc | Owns (the single source for…) | Read when |
|---|---|---|---|
| 1 | [`cloud-platform-prd.md`](cloud-platform-prd.md) | Objective, principles, users, stories (US), requirements (R), non-goals, success criteria, traceability | You need to know *what* and *why* |
| 2 | [`cloud-platform-decisions.md`](cloud-platform-decisions.md) | Every decision (D0–D20), with options, implications and history | Before changing anything; it wins any disagreement |
| 3 | [`cloud-platform-v0-plan.md`](cloud-platform-v0-plan.md) | System design: the loop, core objects, how each part works, the demo path | You need to know *how it behaves* |
| 4 | [`cloud-platform-apis.md`](cloud-platform-apis.md) | Contracts (payloads) and every route; event kinds | Writing code that talks to anything |
| 5 | [`cloud-platform-telemetry.md`](cloud-platform-telemetry.md) | Telemetry capture, sync, index, curation, exports, fleet dataset schema | Working on recordings or datasets |
| 6 | [`cloud-platform-naive-integration.md`](cloud-platform-naive-integration.md) | How the platform uses Naïve: cards, polling, spend caps, secrets, crew config, the spike | Working on work orders or Naïve |
| 7 | [`cloud-platform-architecture.md`](cloud-platform-architecture.md) | Containers, repo layout, code structure, config, libraries, commands, rules for coding sessions | Before writing code |
| 8 | [`cloud-platform-build-plan.md`](cloud-platform-build-plan.md) | Priorities, timeline, milestone tasks, checkpoints, risks, cut lines, dependencies | Deciding what to do next |
| 9 | [`cloud-platform-flows.md`](cloud-platform-flows.md) | The eleven core flows (happy paths and checks) and the **progress tracker** | Testing, demoing, checking status |

## Rules for these docs

- **One fact, one place.** Each doc owns the topics in its row above; other docs link to it instead of repeating it.
- **Mark sources.** Facts cite a source. Our own reasoning is marked *(inferred)*. Decisions are cited as (D#).
- **Decisions go in the log first.** Then update the doc that owns the topic.
- **Progress goes in the tracker** in [`cloud-platform-flows.md`](cloud-platform-flows.md), at every checkpoint.

## Where things stand

- **Decided: D0–D22, everything.** That covers the heal loop and overlays, the healer patching anything including safety (D5a), the automatic gate with the kill switch as the only human control, Naïve for agents with caps in our code, telemetry as an asset, the coding agent for code patches (P1, tracked blockers), Naïve and RunPod for models, the architecture, and local Docker with Python and FastAPI.
- **Waiting on Kent:** nothing blocks the code. Open items belong to the device teammate (build plan §7) and Naïve staff (Naïve integration §7).
- **Built (Sat ~7:35 PM):** M0–M7 merged in PRs #1–#18; F1–F8 and F10 pass with fake devices (82 flow tests). M8 is in progress (#19 open, M8b not started). Details: build plan §3 ("What was built") and the [flows tracker](cloud-platform-flows.md). How to run it: [`cloud/README.md`](../../cloud/README.md).
- **Still open:** the live Naïve card (pending Kent's go-ahead), the I0 contract review with the device teammate, the overnight launch, and M8 blockers B1, B7, B8.

## For a coding session

Start with [`cloud-platform-architecture.md`](cloud-platform-architecture.md) §9 (rules), then take the next unchecked task in [`cloud-platform-build-plan.md`](cloud-platform-build-plan.md) §3. A task is done when its flow test passes and the flow's tracker row is updated.

## Background (outside this folder)

- Kent's Romulus docs in `~/git2/docs` (local, not pushed): `VISION.md`, `STRATEGY.md`, `ARCHITECTURE.md`, `IDEAS.md`, `1-hackathon-plan.md`, `research/`.
- Repo docs: [`../hackathons.md`](../hackathons.md), [`../sponsors.md`](../sponsors.md), [`../naive/terminology.md`](../naive/terminology.md), [`../hermes/terminology.md`](../hermes/terminology.md), and the raw doc dumps in `../raw/`.
