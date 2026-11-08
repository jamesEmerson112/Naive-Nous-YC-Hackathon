# CLAUDE.md — Naive Nous YC Hackathon

Shared context for everyone working in this repo, people and Claude sessions alike. A Claude Code session rewrites the **Team status** section about every 10 minutes and pushes it to `main`. You can edit everything else by hand.

## What this repo is
Prep and build work for two hackathons running at the same time:
- **Naïve Autonomous Company Hackathon** (YC office, SF).
  - Hacking runs **Sat Nov 7 11:30 AM → Sun Nov 8 11:00 AM PST**; presentations are at 1 PM Sunday.
  - Every team must use the Naïve SDK.
- **Hermes Agent Plugin Hackathon** (Nous Research, online). Entries are due **Wed Nov 11, 5 PM EST**.

## Where things are
- `features.txt`: the feature tracker. One short line per feature; claim one by adding `<--- YourName`, and move it to FINISHED when done. Check it before you start anything.
- `hub/`: source of the "Arm Company Stack Map" artifact (visual map of the stack, team lanes, faults, tools). Content is in `hub/src/content/*.ts`; `npm run build` writes `hub/dist/artifact.html`.
- `docs/so101-research.md`: LeRobot 0.7 on the SO-101, NVIDIA sim on RunPod, resets, edge devices, models, with sources
- `docs/project-overview.md`: everything about the self-healing robot project (problem, layers, hardware, healing loop, cloud, demo, open questions); a living doc
- `docs/fault-catalog.md`: every fault to plant, test or demo, with how to plant, detect, fix and verify each one (Pi app, arm, camera, ultrasonic, STM32 firmware)
- `docs/hackathons.md`: rules, schedule, prize tracks, credits
- `docs/scenarios.html`: four prize-track scenarios with diagrams (open it in a browser)
- `docs/sponsors.md`: how each sponsor (Mobilerun, AgentMail, Monid, Stripe…) connects to a Naïve agent
- `docs/setup-diagrams.html`: architecture of Naïve, Hermes, and how they connect. Both pages share `docs/diagrams.css`.
- Terminology: `docs/naive/terminology.md`, `docs/hermes/terminology.md`, and `docs/cross-reference.md` for words the two share
- `docs/raw/`: full doc dumps. Refresh them with `bash docs/raw/fetch.sh`.

## Working rules
- Every fact cites a source; anything you worked out yourself is marked *(inferred)*.
- No Claude or other AI co-author lines in commits.
- Run `git pull --rebase origin main` before you push. Never force-push `main`.
- Don't hand-edit between the `team-status` markers below; the next refresh overwrites it. Put notes for the team in **Notes from the team** instead.
- Before starting a feature, look in `features.txt`. If nobody has it, claim it with `<--- YourName` and push; if someone does, talk to them first.

## Notes from the team
_Add anything others should know here (who is doing what, decisions, blockers)._

### Please everyone: set up your own status sync (from James)
James and Kent each run a Claude session that posts what they're working on here about every 10 minutes. Please set one up too (Ishaan, Olir, Andre), so we can all see who's on what and don't redo or overwrite each other's work.

**How to set it up:** in the Claude desktop app, create a scheduled task that runs every 10 minutes, or in Claude Code run `/loop 10m <prompt>`. Use a prompt like this one:

> In this repo, run `git pull --rebase origin main`. Rewrite only my own block in CLAUDE.md, between `<!-- <name>-sync:start -->` and `<!-- <name>-sync:end -->` (create the block under "Notes from the team" if it's missing). Write 3–5 lines on what I'm working on, what I just finished, what I'm blocked on, and my branch and files. Don't touch anything else. If only the timestamp would change, make no commit. Otherwise `git add CLAUDE.md`, commit "Update <name> status in CLAUDE.md", and `git push origin main`. If the push is rejected, pull --rebase once and retry. Never force-push.

**Rules:**
- Edit only your own block. The `team-status` and `romulus-sync` blocks belong to James and Kent.
- Before you start something big, check here that nobody else already has it.

### Romulus: self-healing + fleet layer for the arm company (Kent)
<!-- romulus-sync:start -->
_Kent's local Claude session refreshes this block about every 10 min. Last synced: Sat Nov 7, 5:18 PM PST. Write your own notes outside the markers. The full research (7 reports) is in Kent's local `~/git2/docs`, which isn't pushed yet; ask Kent._

**The idea:** what the arm company sells is uptime. Romulus runs one loop at three scales:
- **Arm:** detect → diagnose → act → verify → learn, as on-bench reflexes.
- **Fleet:** canary rollout and automatic rollback across many arms.
- **Company:** every verified heal is also a billing event, an incident email and a training example *(inferred)*.

**Why it matters for judging:** nobody closes the loop yet.
- Formant F3 only *recommends* fixes ([BusinessWire](https://www.businesswire.com/news/home/20250630008190/en/Formant-F3-Brings-Generative-AI-and-Agentic-Reasoning-to-Robot-Ops)).
- Alloy raised $8M in Aug 2026 to *diagnose* robot-fleet failures ([AI Insider](https://theaiinsider.tech/2026/08/13/alloy-robotics-raises-8m-in-new-funding-to-help-engineers-debug-robot-fleets-with-ai-agents/)).
- AI incident response for software reached $1B valuations twice: Resolve ([TechCrunch](https://techcrunch.com/2026/02/04/ai-sre-resolve-ai-confirms-125m-raise-unicorn-valuation/)) and Traversal ([Dealroom](https://dealroom.co/news/169321-traversal-raises-120m-at-1b-valuation-to-automate-incident-response/)).
- Main-track pitch: "PagerDuty with hands" *(inferred)*.

**Design rules worth sharing:**
- No LLM sits in the arm's real-time control loop. Fast deterministic reflexes run on the bench. **Updated:** under D5a the healer *may* patch the safety layer and the visor. Those candidates carry a `touches_safety` flag on the timeline, always go to a simulated canary first, and stay under the kill switch. The pitch no longer claims "the LLM never touches the safety layer" ([build plan](docs/cloud-platform-planning/cloud-platform-build-plan.md) K17). The LLM only *proposes* typed, reversible actions (`rollback(model, rev)`, `set_mode(safe)`, `restart(proc)`), and a checker approves them *(inferred)*.
- Each type of action earns autonomy (suggest → approve → auto) and loses it after a bad fix. Showing that demotion live is what builds judges' trust *(inferred)*.
- Get the company loop live tonight so it runs overnight unattended. A morning count of agent vs human actions answers "how much runs without you" *(inferred)*.

**Hero demo:**
1. A judge covers the camera, or we push a bad policy to the arm.
2. The bench detects it and goes to a safe pose or rolls back automatically.
3. The Fleet Doctor agent writes a root cause.
4. AgentMail sends the incident report.
5. If the fault is hardware, Mobilerun phones in a parts order. That's a sponsor-track fit *(inferred)*.

**Answers to the open questions below (from Kent's research):**
- **Pin versions.** LeRobot v0.7.0 (Nov 5) has breaking changes, and MuJoCo 4.0.0 shipped Nov 6 ([LeRobot release](https://github.com/huggingface/lerobot/releases/tag/v0.7.0)).
- **Sim twins of the arm:** MuJoCo Menagerie ships `robotstudio_so101` and runs natively on Macs ([menagerie](https://github.com/google-deepmind/mujoco_menagerie)).
- **Zero-shot "did the task succeed?" from video:** Robometer-4B in LeRobot. It wants a GPU, so it fits on RunPod ([docs](https://github.com/huggingface/lerobot/blob/main/docs/source/robometer.mdx)).
- **RunPod:** with the LLM outside the control loop, diagnosis isn't latency-critical, so serverless is probably fine. The model should read images, because camera frames are the best evidence *(inferred)*.
- **Venue Wi-Fi sinks robot demos.** Bring a router, an Ethernet cable and a recorded backup ([Nailing the Demo](https://generalrobots.substack.com/p/nailing-the-demo)).

**Kent's status (code goes in through PRs from `cloud/*` branches; the plans are in `docs/cloud-platform-planning/`):**
- **Now:** coding M0, due 5:15 PM. That means contracts v0.1 for Ishaan, `make up` serving `/healthz`, one real Naïve card and one real LLM call through the Naïve proxy. The overnight run launches at 12:30 AM and the code freeze is Sun 10 AM ([build plan](docs/cloud-platform-planning/cloud-platform-build-plan.md) §2).
- **Just merged — PR #2 (`87d4e41`, 4:34 PM, from `cloud/m0-adapters`):** the M0 adapters in `cloud/src/romulus_cloud/adapters/`, each with a fake and unit tests.
  - A content-addressed blob store that checks sha256 while streaming.
  - `OpenAICompatLlm`, one client for both the Naïve proxy and RunPod vLLM (D22).
  - `HttpNaive`, which posts cards with an `Idempotency-Key` and retries 409s that are safe to retry. Its fake walks cards to RESULT or STOPPED.
  - `FakeCoder`, the stand-in for the M8 coding agent.
- **Just merged:**
  - **PR #3 (`84b4a93`, 4:52 PM):** `romulus-contracts` v0.1, with models, the shared `rules.evaluate`, `code_patch` checks, example payloads and a README for the device side. Ishaan should review it: it proposes the code-package and `code_patch` formats (B2, B3).
  - **PR #4 (`b76b332`, 4:54 PM):** the cloud scaffold: settings, the data model, the event log, the FastAPI app with `/healthz`, the worker, the Compose stack, a Makefile and `cloud/CLAUDE.md`.
  - **Earlier:** the workspace skeleton (PR #1) and the adapters (PR #2). M0's live Naïve card and LLM call are still to come *(inferred from the build plan)*.
- **Merged 5:11–5:13 PM:**
  - **#5 (`9245bd1`, M1):** the fake fleet. Its client is also the **reference visor and outbox for the Pi**: hash-checked installs that roll back to last good (D7), an on-disk FIFO outbox, and the `rules`, `code`, `bad` and `park` scenarios.
  - **#6 (`9870a67`, M4, M5, M7):** pure functions for work-order triage, spend caps and card text, plus `cloud/config/parts.yaml`. Its supplier phone numbers are placeholders (K13).
- **Open:**
  - **#8, `cloud/m1-skeleton` (M1 walking skeleton, F1 and F2):** the device API Ishaan's Pi will call (handoffs I1–I3). That covers check-in, firmware, harness and code pulls with `X-Sha256`, install results, incidents and overlay status. Unsupported contract versions are rejected, unknown devices register on first contact, and retried posts don't create duplicates. It also adds the ops API: version uploads, devices, and incidents with `/story` (PR #8 description).
  - **#7, `cloud/m3a-domain-gate` (M3a):** the pure gate core: overlay merge, replay, the canary verdict and the candidate state machine.
- **Behind schedule:** M0's live Naïve card and LLM call were due at 5:15 PM and still aren't in any PR. B4 is probably blocking them: either a Naïve key with `proxy:write` or James's RunPod endpoint *(inferred)*.
- **Ask from James:** the current Naïve key covers only agents and sessions. Hermes needs a key with Board scopes to pick up and close cards, either from Kent or via `naive mcp create` (team status).
- **Decisions locked (D0–D22):** Docker Compose, Postgres 17, FastAPI, Jinja + htmx (D17, D18). Naïve is outbound only, with caps in our code and over-cap orders blocked (D19, D19b). LLM access goes through the Naïve proxy or RunPod, with a swappable `Coder` for code patches (D21, D22). D5a is accepted, with its risk logged as K17 ([decision log](docs/cloud-platform-planning/cloud-platform-decisions.md)).
- **Blocked on Ishaan and James:** the device code and tests need to be in `device/` (B1), the code-package and `code_patch` formats need agreement (B2, B3), the STM32 toolchain has to go in the sandbox (B8), and Ishaan has to supply the list of safety and visor paths (K17). Either a Naïve key with `proxy:write` or James's RunPod endpoint is needed (B4) (build plan §3, M8). Ishaan's test bed lives in `edge/`, while the contracts expect `device/`, so the two need to agree on one folder *(inferred)*.
<!-- romulus-sync:end -->

### Device-side healer (Ishaan)
<!-- ishaan-sync:start -->
_Ishaan's local Claude session refreshes this block about every 30 min. Last synced: Sat Nov 7, 5:12 PM PST._
- **Now:** screening healer harness versions h1, h2 and h3 in `edge/healer/harnesses/` (uncommitted), about halfway through. granite4.1:3b on h3 scored 1/6: all five software tasks hit the 30-step limit now that parking can't be used to escape. qwen3.5:4b with thinking reported health-checked fixes for D1, D2, D3 and S1 and is on S2; hidden-check scores come when its run ends, then thinking-off and qwen3:4b-instruct-2507 (config d, now downloaded) (sessions "btw: what types of tests..." and "Current idea summary").
- **Just finished:** claimed the device lines in `features.txt` (`cd54c16`, `e59093f`); the project overview and fault catalog (`16c6b9c`, `38771aa`, `325a4bb`); device-side answers in Kent's decision log (`5cea9c2`). Kent accepted the D5a change (logged as K17).
- **Blocked on:** nothing. Owes Kent the safety and visor path list (K17), a call on `edge/` vs `device/` (B1), and a review of the code-package and `code_patch` formats in PR #3 (B2, B3), per Kent's block.
- **Branch and files:** `main`. `edge/` (robot app, sim, supervisor, healer harnesses h1–h3, `bench/run_bench.py`, `bench/analyze.py`) stays local until its own checks pass.
<!-- ishaan-sync:end -->

## Team status
<!-- team-status:start -->
_Last updated: Sat Nov 7, 5:19 PM PST_

**Current focus:** a robotic-arm company. Naïve runs the business and the job board; Hermes on the bench drives the arm through a plugin; an LLM operates the arm, with RunPod for anything power- or VRAM-heavy. Kent merged PRs #5 (M1 fake device) and #6 (M4/M5/M7 triage), and still has #7 (M3a gate core) and #8 (`cloud/m1-skeleton`, the device API the Pi will call) open. M0's live Naïve card and LLM call missed the 5:15 PM target. Ishaan is halfway through screening healer harnesses h1–h3 in `edge/` (local). James is cutting the Naïve console down to the agents whose models match on pi and Hermes.

**Recently done:**
- James pushed `4c5f5b2` (5:17 PM): a reasoning-effort picker for Hermes in the Naïve console, plus a fix to the Hermes MCP toolset name. The picker only changes Hermes. Only the OpenAI agents (hello-luna, hello-sol) run the same model on both sides; GLM and Claude agents fall back to `gpt-6-luna` on Hermes (James's session)
- Kent merged PR #5 (`9245bd1`, 5:13 PM): the fake fleet, whose client is the reference visor and outbox for the Pi (hash-checked installs with rollback, on-disk FIFO outbox, four scenarios)
- Kent merged PR #6 (`9870a67`, 5:11 PM): pure functions for work-order triage, spend caps and card text, plus `cloud/config/parts.yaml` with placeholder supplier phones (K13)
- Ishaan's harness screen: granite4.1:3b on h3 scored 1/6; qwen3.5:4b with thinking reported fixes for D1, D2, D3 and S1, and its hidden-check scores aren't in yet (Ishaan's block)

**Open questions:**
- Kent is blocked on: device code and tests in `device/` (B1), the STM32 toolchain in the sandbox (B8), the list of safety and visor paths from Ishaan (K17), and either a Naïve key with `proxy:write` or James's RunPod endpoint (B4). B4 probably also holds up M0's live Naïve card and LLM call *(inferred, Kent's block)*
- Ishaan owes Kent a review of the code-package and `code_patch` formats in PR #3 (B2, B3) and a call on `edge/` vs `device/` (B1). PR #8 is the device API his Pi will call (Kent's and Ishaan's blocks)
- The current Naïve key covers agents and sessions only. A key with `proxy:write` would let Hermes run the same models as pi through Naïve's proxy. A key with Board scopes (and `NAIVE_API_KEY` in `~/.hermes/.env`) would let Hermes pick up and close cards
- Which small model and harness run on the Pi 5, and what does the STM32 do in the robot loop?

**Recent commits (all authors):**
- `4c5f5b2` Sat 17:17 · jamesEmerson112 · Naive console: Hermes reasoning-effort picker; fix Hermes MCP toolset name
- `09b3259` Sat 17:16 · Kent Gang · Update Kent status in CLAUDE.md
- `9245bd1` Sat 17:13 · kgang · Romulus M1: fake device + reference visor/outbox client (#5)
- `2535308` Sat 17:12 · Ishaan Ranjan · Update Ishaan status in CLAUDE.md
- `9870a67` Sat 17:11 · kgang · Romulus M4/M5/M7: pure domain triage, labels, digest + parts.yaml (#6)
- `89f2fbf` Sat 17:09 · jamesEmerson112 · Update team status and features.txt
- `59d6764` Sat 17:07 · jamesEmerson112 · Naive console: pi vs Hermes side by side, matched models, locked-scope panel
- `fecf8d3` Sat 17:06 · Kent Gang · Update Kent status in CLAUDE.md
- `54c8aaf` Sat 16:58 · jamesEmerson112 · Update team status in CLAUDE.md
- `8c9df7d` Sat 16:56 · Kent Gang · Update Kent status in CLAUDE.md

**Branches on GitHub:** `main`, `firmware` (Olir), `cloud/fake-device` (Kent, merged as PR #5), `cloud/domain-triage-labels-digest` (Kent, merged as PR #6), `cloud/m3a-domain-gate` (Kent, PR #7), `cloud/m1-skeleton` (Kent, PR #8)
<!-- team-status:end -->
