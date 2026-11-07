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
_Kent's local Claude session refreshes this block about every 10 min. Last synced: Sat Nov 7, 3:59 PM PST. Write your own notes outside the markers. The full research (7 reports) is in Kent's local `~/git2/docs`, which isn't pushed yet; ask Kent._

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
- The RunPod/Hermes LLM never sits in the arm's control or safety loop. Fast deterministic reflexes run on the bench. The LLM only *proposes* typed, reversible actions (`rollback(model, rev)`, `set_mode(safe)`, `restart(proc)`), and a checker approves them *(inferred)*.
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

**Kent's status (branch `main`, all in `docs/cloud-platform-planning/`):**
- **Now:** planning is finished and Kent is starting to write the cloud-platform code. First is M0 (scaffold, contracts v0.1 for the device teammate, one real Naïve card, one real LLM call), due by 5:15 PM. The overnight run launches at 12:30 AM and the code freeze is Sun 10 AM ([build plan](docs/cloud-platform-planning/cloud-platform-build-plan.md) §2).
- **Decisions locked (D15–D22, all ✅):** v0 plan v2; Docker Compose with **Postgres 17**, FastAPI, Jinja + htmx; Naïve integration is outbound only, with caps checked in our code and over-cap orders blocked until a person resolves them (D19, D19b); telemetry formats (D20); a coding agent for code patches (D21), built as M8 and the first P1 item once its blockers clear (D21a) ([decision log](docs/cloud-platform-planning/cloud-platform-decisions.md)).
- **Just pushed — D22, the batch job's models:** one OpenAI-compatible client that talks to Naïve's model proxy by default or to James's RunPod vLLM, plus a swappable `Coder` that starts as a local open-source agent in a new `coder` sandbox container ([architecture](docs/cloud-platform-planning/cloud-platform-architecture.md)).
- **Blocked on the device side and James:** the device code and tests need to be in `device/` (B1), the code-package and `code_patch` formats need agreement (B2, B3), and the STM32 toolchain has to go in the sandbox (B8). Either a Naïve key with `proxy:write` or James's RunPod endpoint is needed (B4) ([build plan](docs/cloud-platform-planning/cloud-platform-build-plan.md) §3, M8).
- **Note:** Ishaan changed D5a so the healer may patch the safety layer and the visor. That's at odds with the design rule above about keeping the model out of the safety loop *(inferred)*.
<!-- romulus-sync:end -->

### Device-side healer (Ishaan)
<!-- ishaan-sync:start -->
_Ishaan's local Claude session refreshes this block about every 30 min. Last synced: Sat Nov 7, 3:58 PM PST._
- **Now:** building the Mac test bed for the device-side healer in `edge/` (uncommitted). Written so far: the robot app, a fake SO-101 arm and HC-SR04 trace replay, a supervisor with tier 1-3 health checks, and the healer harness (prompt, skills, health/park/report tools). Next: benchmark faults D1-D3, S1, S2 and A5 from `docs/fault-catalog.md`, then a small local model through mini-swe-agent on Ollama.
- **Just finished:** the project overview and fault catalog (`16c6b9c`, `38771aa`, `325a4bb`), and device-side answers in Kent's decision log (`5cea9c2`): the healer may patch everything, including the safety layer and the visor, and the device runs its own small model and harness, not Hermes.
- **Blocked on:** nothing. Kent's review of the D5a change is still open.
- **Branch and files:** `main`. `edge/` stays local until its own checks pass.
<!-- ishaan-sync:end -->

## Team status
<!-- team-status:start -->
_Last updated: Sat Nov 7, 3:58 PM PST_

**Current focus:** a robotic-arm company. Naïve runs the business and the job board; Hermes on the bench drives the arm through a plugin; an LLM operates the arm, with RunPod for anything power- or VRAM-heavy. Kent has locked every cloud decision (D15–D22) and is writing M0 (scaffold, contracts v0.1, one real Naïve card, one real LLM call), due 5:15 PM. Ishaan is building the device-side healer test bed in `edge/` (local, uncommitted).

**Recently done:**
- Kent decided D19b (over-cap work orders are blocked as `blocked_over_cap` until a person resolves them) and D22 (one OpenAI-compatible LLM adapter: Naïve model proxy by default, James's RunPod vLLM as the alternative; a swappable `Coder` in a new `coder` sandbox) (3:56 PM)
- James refreshed the stack hub with Kent's decisions D15–D21 and the Naïve org setup, and pointed CLAUDE.md at `features.txt` (3:53 PM)
- Ishaan wrote the `edge/` test bed so far: robot app, fake SO-101 arm, HC-SR04 trace replay, a supervisor with tier 1–3 health checks, and the healer harness; next is benchmarking faults D1–D3, S1, S2, A5 (3:58 PM)
- Kent approved D18 (Docker Compose, Postgres 17, FastAPI, Jinja + htmx) and D20 (telemetry), and decided D21 (a coding agent for code patches) (3:46 PM)

**Open questions:**
- Kent is blocked on: device code and tests in `device/` (B1), the code-package and `code_patch` formats (B2, B3), the STM32 toolchain in the sandbox (B8), and either a Naïve key with `proxy:write` or James's RunPod endpoint (B4)
- Ishaan's D5a change (healer may patch the safety layer and visor) still needs Kent's review; Kent notes it conflicts with keeping the model out of the safety loop
- Which small model and harness run on the Pi 5 (Ishaan is trying mini-swe-agent on Ollama), and which RunPod model ids does James's lane use?
- What does the STM32 do in the robot loop?

**Recent commits (all authors):**
- `e8f196f` Sat 15:58 · Ishaan Ranjan · Update Ishaan status in CLAUDE.md
- `199e767` Sat 15:57 · Kent Gang · Update Kent status in CLAUDE.md
- `c8eef1a` Sat 15:56 · Kent Gang · Decide spend-cap blocking (D19b) and batch LLM and coding agent (D22); add coder sandbox
- `821ad27` Sat 15:53 · jamesEmerson112 · Refresh hub with Kent's decisions D15-D21 and the Naive org setup
- `7d952ed` Sat 15:52 · Kent Gang · more planning updates
- `2267167` Sat 15:49 · jamesEmerson112 · Fold Kent's and Olir's notes into the hub; point CLAUDE.md at features.txt
- `fc8158d` Sat 15:48 · jamesEmerson112 · Update team status in CLAUDE.md
- `a0ca803` Sat 15:47 · Kent Gang · Update Kent status in CLAUDE.md
- `7ce7941` Sat 15:46 · Kent Gang · Approve architecture (D18) and telemetry (D20); decide coding agent for code patches (D21)
- `dab713d` Sat 15:38 · Kent Gang · final refinements before generating cloud platform code

**Branches on GitHub:** `main`, `firmware` (Olir)
<!-- team-status:end -->
