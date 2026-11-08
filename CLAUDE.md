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
_Kent's local Claude session refreshes this block about every 10 min. Last synced: Sat Nov 7, 4:28 PM PST. Write your own notes outside the markers. The full research (7 reports) is in Kent's local `~/git2/docs`, which isn't pushed yet; ask Kent._

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
- **Just merged — PR #1 (`8eb532f`, from `cloud/m0-workspace`):** the uv workspace skeleton. It has three members: `packages/contracts` (`romulus-contracts`, shared with the device side), `cloud/` (`romulus-cloud`) and `tools/fake_device/`. It pins Python 3.12, with the library set from architecture §7 in `uv.lock`.
- **Decisions locked (D0–D22):** Docker Compose, Postgres 17, FastAPI, Jinja + htmx (D17, D18). Naïve is outbound only, with caps in our code and over-cap orders blocked (D19, D19b). LLM access goes through the Naïve proxy or RunPod, with a swappable `Coder` for code patches (D21, D22). D5a is accepted, with its risk logged as K17 ([decision log](docs/cloud-platform-planning/cloud-platform-decisions.md)).
- **Blocked on Ishaan and James:** the device code and tests need to be in `device/` (B1), the code-package and `code_patch` formats need agreement (B2, B3), the STM32 toolchain has to go in the sandbox (B8), and Ishaan has to supply the list of safety and visor paths (K17). Either a Naïve key with `proxy:write` or James's RunPod endpoint is needed (B4) (build plan §3, M8). Ishaan's test bed lives in `edge/`, while the contracts expect `device/`, so the two need to agree on one folder *(inferred)*.
<!-- romulus-sync:end -->

### Device-side healer (Ishaan)
<!-- ishaan-sync:start -->
_Ishaan's local Claude session refreshes this block about every 30 min. Last synced: Sat Nov 7, 4:12 PM PST._
- **Now:** tuning the healer harness in `edge/` (uncommitted) and rerunning the benchmark (session "Build the edge test bed"). The Mac test bed works: the oracle passes 6/6 tasks and a do-nothing healer 0/6. granite4.1:3b on Ollama ran end to end but failed S1 and D2 (blind sed edits, a no-op fix reported as done, submitting while health still failed). Granite is only a first pick; no model comparison yet.
- **Just finished:** claimed the device lines in `features.txt` (`cd54c16`, `e59093f`); the project overview and fault catalog (`16c6b9c`, `38771aa`, `325a4bb`); device-side answers in Kent's decision log (`5cea9c2`). Kent accepted the D5a change (logged as K17).
- **Blocked on:** nothing. Owes Kent the safety and visor path list (K17) and a call on `edge/` vs `device/` (B1), per Kent's block.
- **Branch and files:** `main`. `edge/` (robot app, sim, supervisor, healer harness, `bench/run_bench.py`) stays local until its own checks pass.
<!-- ishaan-sync:end -->

## Team status
<!-- team-status:start -->
_Last updated: Sat Nov 7, 4:28 PM PST_

**Current focus:** a robotic-arm company. Naïve runs the business and the job board; Hermes on the bench drives the arm through a plugin; an LLM operates the arm, with RunPod for anything power- or VRAM-heavy. Kent is coding M0 (contracts v0.1, `make up` serving `/healthz`, one real Naïve card, one real LLM call), due 5:15 PM. Ishaan is tuning the device-side healer harness in `edge/` (local, uncommitted). James has claimed the bench Hermes install and its OpenAI key, is signed in to the Romulus org on the Naïve CLI, and is starting a Naïve hello world.

**Recently done:**
- Kent merged PR #1 (`8eb532f`): a uv workspace skeleton with `packages/contracts` (`romulus-contracts`, shared with the device side), `cloud/` (`romulus-cloud`) and `tools/fake_device/`, pinned to Python 3.12. Kent's code now goes in through PRs from `cloud/*` branches (4:21 PM)
- James claimed "Hermes install on the bench" and "OpenAI key for bench Hermes" in `features.txt`, and signed in to the Romulus org on the Naïve CLI; the agent and model lists both return (4:26 PM)
- James added `docs/naive-vs-runpod-setup.md`: Naïve hosts agents on its own fixed harnesses (`pi`, `vetta`, `claude_code`, `hermes`) with no custom image, so the bench Hermes calls Naïve over API or MCP; GPUs stay on RunPod (4:16 PM)
- Ishaan's Mac test bed works: the oracle healer passes 6/6 tasks and a do-nothing healer 0/6; granite4.1:3b on Ollama ran end to end but failed S1 and D2 (4:12 PM)

**Open questions:**
- Kent is blocked on: device code and tests in `device/` (B1), the code-package and `code_patch` formats (B2, B3), the STM32 toolchain in the sandbox (B8), the list of safety and visor paths from Ishaan (K17), and either a Naïve key with `proxy:write` or James's RunPod endpoint (B4)
- Ishaan's test bed is in `edge/` but Kent's contracts expect `device/`; the two need to pick one folder *(inferred)*
- Which small model and harness run on the Pi 5 (granite4.1:3b is only a first pick), and which RunPod model ids does James's lane use?
- What does the STM32 do in the robot loop?

**Recent commits (all authors):**
- `a88ef75` Sat 16:27 · Kent Gang · Update Kent status in CLAUDE.md
- `7576f53` Sat 16:26 · jamesEmerson112 · Claim the bench Hermes key and install lines in features.txt
- `8eb532f` Sat 16:21 · kgang · Add uv workspace skeleton for the Romulus cloud platform (#1)
- `f7d3a9e` Sat 16:18 · jamesEmerson112 · Update team status in CLAUDE.md
- `6869c8d` Sat 16:16 · jamesEmerson112 · Add note on why Naive does not set up like RunPod
- `33d4cf4` Sat 16:12 · Ishaan Ranjan · Update Ishaan status in CLAUDE.md
- `1a58412` Sat 16:10 · jamesEmerson112 · Hub: Features inside view read from features.txt; black boxes for not worked on
- `f61d966` Sat 16:08 · jamesEmerson112 · Update team status in CLAUDE.md
- `41dc22d` Sat 16:07 · Kent Gang · Update Kent status in CLAUDE.md
- `0b459ee` Sat 16:06 · Kent Gang · cloud platform planning updates

**Branches on GitHub:** `main`, `firmware` (Olir)
<!-- team-status:end -->
