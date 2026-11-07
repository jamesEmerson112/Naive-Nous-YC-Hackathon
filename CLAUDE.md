# CLAUDE.md — Naive Nous YC Hackathon

Shared context for everyone working in this repo, people and Claude sessions alike. A Claude Code session rewrites the **Team status** section about every 10 minutes and pushes it to `main`. You can edit everything else by hand.

## What this repo is
Prep and build work for two hackathons running at the same time:
- **Naïve Autonomous Company Hackathon** (YC office, SF).
  - Hacking runs **Sat Nov 7 11:30 AM → Sun Nov 8 11:00 AM PST**; presentations are at 1 PM Sunday.
  - Every team must use the Naïve SDK.
- **Hermes Agent Plugin Hackathon** (Nous Research, online). Entries are due **Wed Nov 11, 5 PM EST**.

## Where things are
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
_Kent's local Claude session refreshes this block about every 10 min. Last synced: Sat Nov 7, 3:50 PM PST. Write your own notes outside the markers. The full research (7 reports) is in Kent's local `~/git2/docs`, which isn't pushed yet; ask Kent._

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
- **Decisions locked:** D15 (v0 plan, approved in principle; v2), D17 and D18 (Docker Compose, **Postgres 17**, FastAPI, Jinja + htmx; *not* SQLite), D20 (telemetry: LeRobot v3 incident windows, MCAP logs, JSONL agent traces), and D21 (a coding agent turns code patches from held overlays into candidates that go through the gate).
- **Just pushed:** a reading-order guide at `docs/cloud-platform-planning/README.md`, v0 plan v2, the Naïve company setup notes at `docs/naive/company-setup.md`, and the decision log updates above.
- **Still open:** D19 (Naïve integration), D19b (what happens when spend goes over the cap) and D21a (when the coding agent gets built). Ishaan's change to D5a lets the healer patch the safety layer and the visor, which needs Kent's review ([decision log](docs/cloud-platform-planning/cloud-platform-decisions.md)).
<!-- romulus-sync:end -->

## Team status
<!-- team-status:start -->
_Last updated: Sat Nov 7, 3:38 PM PST_

**Current focus:** a robotic-arm company. Naïve runs the business and the job board; Hermes on the bench drives the arm through a plugin; an LLM operates the arm, with RunPod for anything power- or VRAM-heavy. Kent is finishing the cloud-platform build plan (hero loop F2→F3→F4 first, overnight run F10 launching by about 12:30 AM, code freeze Sun 10 AM). James is folding Kent's new docs into the stack hub and `features.txt`, then specifying the bench-to-cloud bridge (Naïve REST, Romulus device API, RunPod).

**Recently done:**
- James refreshed the stack hub (`hub/`) with Kent's v0 plan and decisions D0–D15, Ishaan's overview and 47-fault catalog, the bench hardware and heal loops, and added `features.txt`: one line per feature, claim one with "<--- Name" (3:36 PM)
- Kent pushed `docs/cloud-platform-planning/`: build plan, Naïve integration (pending D19), telemetry (pending D20), expanded APIs, architecture and flows/progress tracker (3:31–3:35 PM)
- Ishaan added `docs/project-overview.md` and `docs/fault-catalog.md`, folded in Kent's decision log and v0 plan, and added SO-101 facts (3:22–3:26 PM)
- Isaac Sim needs an NVIDIA RTX GPU (about $0.33/hr on RunPod community cloud); MuJoCo with the Menagerie SO-101 model is the free fallback on a Mac

**Open questions:**
- Decisions waiting on the team: D15 (v0 plan), D19 (Naïve integration, incl. D19b on spend over the cap), D20 (telemetry)
- Who owns the device side (embedded agent, overlays, visor, harness)? D14 still needs a teammate
- What does the STM32 do in the robot loop, and are C fixes (D1, F1–F11) cloud-deployed or local patches only? The v0 batch job skips code patches
- Which harness and model run on the Pi 5, and which model runs cloud diagnosis on RunPod?

**Recent commits (all authors):**
- `2283dce` Sat 15:36 · jamesEmerson112 · Refresh stack hub with team updates; add features.txt
- `2e99839` Sat 15:36 · Kent Gang · Update Kent status in CLAUDE.md
- `13d0f09` Sat 15:35 · Kent Gang · Add cloud-platform build plan, Naive integration and telemetry docs; expand APIs
- `19e2982` Sat 15:31 · Kent Gang · more planning
- `2b7a4dc` Sat 15:28 · jamesEmerson112 · Update team status in CLAUDE.md
- `325a4bb` Sat 15:26 · Ishaan Ranjan · Add SO-101 research facts to the overview and fault catalog
- `38771aa` Sat 15:25 · Ishaan Ranjan · Fold the cloud decision log and v0 plan into the project overview
- `f5336a4` Sat 15:22 · jamesEmerson112 · Add stack hub artifact and SO-101 research notes
- `16c6b9c` Sat 15:22 · Ishaan Ranjan · Add project overview and fault catalog for the self-healing robot
- `5a69311` Sat 15:18 · jamesEmerson112 · Update team status in CLAUDE.md

**Branches on GitHub:** `main`, `firmware` (Olir)
<!-- team-status:end -->
