# CLAUDE.md — Naive Nous YC Hackathon

Shared context for everyone working in this repo, people and Claude sessions alike. A Claude Code session rewrites the **Team status** section about every 10 minutes and pushes it to `main`. You can edit everything else by hand.

## What this repo is
Prep and build work for two hackathons running at the same time:
- **Naïve Autonomous Company Hackathon** (YC office, SF).
  - Hacking runs **Sat Nov 7 11:30 AM → Sun Nov 8 11:00 AM PST**; presentations are at 1 PM Sunday.
  - Every team must use the Naïve SDK.
- **Hermes Agent Plugin Hackathon** (Nous Research, online). Entries are due **Wed Nov 11, 5 PM EST**.

## Where things are
- `README.md`: how the pieces fit and what each code folder is: `cloud/` (Romulus cloud), `packages/contracts/` (the device ↔ cloud contract), `tools/fake_device/` (fake arms), `tools/edge_bridge/` (runs on the Pi and reports Olir's pod healer to the cloud), `firmware/` (Olir's STM32 pod and its healer)
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

### Cloud work: use the agent run schema (from Olir, Sun Nov 8 ~5 AM)
The real pod now sends each heal-agent run to the cloud as an **agent run report**: APIs doc §3.11, table `agent_runs`, contract `AgentRunReport`, sent by `tools/edge_bridge` (on `full-test-3` at `5ac13d7`). That's the format real agent logs arrive in. Any remaining cloud work that needs what an agent did (Fleet Doctor evidence, datasets and exports, work-order cards, the digest) reads it from `agent_runs`, not from the fake arms' `Incident.agent` or `agent_trace`. Test with real-shape run data from `edge_bridge.fake_healer`, not hand-written dicts. Details: [`cloud/CLAUDE.md`](cloud/CLAUDE.md), "Real device data". The laptop cloud holds real runs, so never `make reset` it.

### Romulus: self-healing + fleet layer for the arm company (Kent)
<!-- romulus-sync:start -->
_Kent's session refreshes this every 10 min · Last synced: Sun Nov 8, 8:49 AM PST · write notes outside the markers_

- **Pitch:** we sell uptime: one heal loop at arm, fleet and company scale. Rivals only diagnose ([Alloy](https://theaiinsider.tech/2026/08/13/alloy-robotics-raises-8m-in-new-funding-to-help-engineers-debug-robot-fleets-with-ai-agents/)) or recommend fixes ([Formant F3](https://www.businesswire.com/news/home/20250630008190/en/Formant-F3-Brings-Generative-AI-and-Agentic-Reasoning-to-Robot-Ops)). We close the loop: "PagerDuty with hands" *(inferred)*.
- **Rule:** no LLM in the real-time control loop. Models propose reversible fixes, and the gate checks them: replay → simulated canary → automatic promote or rollback. People only touch the kill switch (D6, D11).
- **Rule:** the healer may patch the safety layer and the visor (D5a). Those changes are flagged `touches_safety` and always go to the simulated canary first (K17).
- **Rule:** each action type earns autonomy and loses it after a bad fix. The overnight unattended run is the proof *(inferred)*.
- **Demo:** cover the camera or push a bad policy → arm parks or rolls back → root cause on the timeline → AgentMail incident report → hardware fault? Mobilerun orders the part.
- **Research:** pin LeRobot v0.7.0, since it breaks the API ([release](https://github.com/huggingface/lerobot/releases/tag/v0.7.0)).
- **Research:** the SO-101 sim twin is `robotstudio_so101` in MuJoCo Menagerie, and it runs on a Mac ([menagerie](https://github.com/google-deepmind/mujoco_menagerie)).
- **Research:** bring a router, Ethernet and a backup video, because venue Wi-Fi sinks demos ([Nailing the Demo](https://generalrobots.substack.com/p/nailing-the-demo)). Everything else is in [`docs/cloud-platform-planning/README.md`](docs/cloud-platform-planning/README.md).
- **Now:** **Naïve Technician** T0 is built and merged (#65–#72). The 8:44 rehearsal went incident → `waiting` in 38 s: a 27 s Naïve session for $0.08, a real email, and the C018 spare staged at $15.99 ([`runbook.md`](docs/naive-technician/runbook.md)). Next is **fleet learning**, "every fault happens once": draft v1, nothing built yet ([`docs/recursive-self-improvement/`](docs/recursive-self-improvement/README.md)).
- **Done:** Naïve Technician T0 #65 #67 #68 #70 #71 #72, Studio approval #66 #69, plan `eafc667`–`46a247b` · M0 #1–#4 · M1 #5, #8 · M2 #9 · M3 #7, #12, #18 · M4 #10 · M5 #11, #15 · M6 #14 · M7 #16 · M8a #19 · M8b #28 · real device code #38 · replay hardening #53 · B5 sandbox #46 · held-out verify #58 · B8 toolchain #24 · edge bridge #25 · docs #21, #45 · Fleet Doctor #22, #23, #26, #27, #29, #30, #32–#35, #37, #39, #42, #44, #52, #57, #59–#61 · triage #6 · lint #13, #40 · device kinds #17 · decisions D0–D29 locked (D27 deferred; D30 drafted).
- **Blocked:** the 1 PM demo needs a cloud with main plus `TECHNICIAN_MODE` (Kent's `tech` stack or a teammate's), and D30 is still a draft. The rehearsal settled the servo and the live Naïve writes in practice *(inferred)*. Fleet Doctor still waits on the K17 safety and visor path list. B1 and B4 are clear.
- **Asks:** Andre: confirm our STS3215 is the C018 12 V (the rehearsal staged it). Team: who's the technician for the demo email (plus phone for T1)? Ishaan: K17 safety and visor paths and the contracts review (B2, B3).
<!-- romulus-sync:end -->

### Device-side healer (Ishaan)
<!-- ishaan-sync:start -->
_Ishaan's local Claude session refreshes this block about every hour. Last synced: Sun Nov 8, 8:17 AM PST._
- **Now:** pod healer loop handed to Olir's session for bench runs (`firmware/harness/HANDOFF.md`, `8d9a30b`); session "Build the pod healer agent loop and blind eval" stood down at 21:47 with no bench run. Not on the bench yet. The Jetson link is still paused (no USB data or LAN path, session "Identify the connected Jetson").
- **Just finished:** pod healer agent loop and blind eval merged as #41 (`4ee3517`): `agent.py` with Claude and Ollama drivers and a risk gate, `evaluate.py`, tests, README, HANDOFF.md (`72f53c8`); pointed Olir's session to the handoff in CLAUDE.md (`8d9a30b`). Earlier: device code and healer harness #31 (`6c3b006`).
- **Blocked on:** bench runs wait on Olir (SSH to the pod, API key, per the session recap). Still owes Kent the K17 safety and visor path list and the contracts review (B2, B3), per Kent's block.
- **Branch and files:** main checkout on `main` at `78fdaae`, clean, no uncommitted files. `ishaan/pod-agent-loop` (worktree `naive-pod-loop`) is merged. No Claude sessions in the last 2 h.
<!-- ishaan-sync:end -->

**Pod healer agent loop, for Olir's session:** running or benchmarking it on the bench (`firmware/harness/`, merged as #41)? Start with [`firmware/harness/HANDOFF.md`](firmware/harness/HANDOFF.md): bench steps, what's verified, what to report.

## Team status
<!-- team-status:start -->
## Team status
_Last updated: Sun Nov 8, 8:44 AM PST_

**Current focus:** a robotic-arm company. Naïve runs the business and the job board; Hermes on the bench drives the arm through a plugin; an LLM operates the arm, with RunPod for anything power- or VRAM-heavy. P3 is on: the team is on the real arm 7–10 AM, with the 10 AM code freeze and 11 AM stop ahead. James's cloud session wrote [`HANDOFF-LOCAL.md`](prototypes/arm-sim/HANDOFF-LOCAL.md) for the local real-arm session at the bench: run `main`, Mission Control on the Pi next to the arm host, Pi steps R0 then Mac steps R1. Kent's **Naïve Technician** T0 is now fully on `main` (backend #68, page fixes #70, demo runbook #71), and Kent started fleet-learning research.

**Recently done:**
- Kent merged **#68 Naïve Technician T0 backend** `4e18879` (`services/technician.py`, F14 flow test, APIs rows, D30 draft) with rehearsal fixes `aca3986`: shop falls back to `products.json` with a 10-min cache, store re-checked every 20 s, email waits for the first part check, incident page links the case
- Kent merged **#71 Technician demo runbook** [`docs/naive-technician/runbook.md`](docs/naive-technician/runbook.md) and `cloud/scripts/technician_demo.py`, and **#70 page fixes** (honest staged and email states, badge placement)
- James's cloud session pushed **real-arm handoff** `98fd44a`: which code, where things run, bench steps, file ownership; proven in sim only, the real arm has run only the adapter's 5 mock tests
- Kent pushed **fleet-learning research** `bdc25de` and **training dataset ideas** `b6745ea` in [`docs/recursive-self-improvement/`](docs/recursive-self-improvement/README.md), toward turning verified heal traces into training data

**Open questions:**
- Naïve Technician: T0 is merged but Kent's five calls are still open (D30 a person checks out, servo variant C001 7.4 V or C018 12 V, the technician teammate, OK for the live Naïve writes, which cloud runs the 1 PM demo)
- The worker/manager review fixes on `james/arm-sim-ydcsss` aren't merged into `main` yet, so the bench runs without them; James's healer has run against a fake manager only, not a real Hermes yet (`MORNING.md`, "Known gaps")
- Ishaan's pod healer loop (#41) still waits on Olir for bench runs (SSH to the pod, API key); Ishaan still owes Kent the K17 safety and visor path list and the contracts review (B2, B3), so `SAFETY_PATHS_CONFIRMED` stays false
- Team calls still open: Stripe is optional; Monid's job; owners for the unclaimed lines (fleet-ops, Mobilerun key, Stripe payment, AgentMail email); live vs fake work orders for the demo; who drives the real arm. #63 (main → `full-test-2`) is still open though `full-test-2` is merged *(inferred: can be closed)*

**Recent commits (all authors):**
- `6e5be55` Sun 08:43 · kgang · Merge pull request #71 from jamesEmerson112/tech/runbook
- `f771361` Sun 08:42 · Kent Gang · Runbook: shop fallback and 20 s re-check as merged in #68
- `4e18879` Sun 08:42 · kgang · Naïve Technician T0: backend (#68)
- `d40f653` Sun 08:41 · Kent Gang · Naïve Technician: demo helper script and runbook
- `aca3986` Sun 08:40 · Kent Gang · Technician rehearsal fixes: shop falls back to products.json (dollar prices) with a 10-min cache, store re-checked every 20 s for 10 min, email waits for the first part check and says when it isn't staged, part titles from the allowlist, monotonic replay feed, honest email stage, incident page links the case
- `63ba836` Sun 08:40 · kgang · Merge pull request #70 from jamesEmerson112/tech/page-fixes
- `b20f147` Sun 08:40 · Kent Gang · Technician page: honest staged/email states, badge placement
- `4f1752f` Sun 08:40 · Kent Gang · Merge remote-tracking branch 'origin/main' into tech/backend
- `4df51cb` Sun 08:39 · jamesEmerson112 · Update team status and features.txt
- `b6745ea` Sun 08:39 · Kent Gang · training dataset management ideation

**Branches on GitHub:** `main`; `test-4` (no commits of its own); the notice PR branches `claudemd-notice/arm-teleop-recording` (#47), `claudemd-notice/demo` (#48), `claudemd-notice/firmware-harness-connection` (#49), `claudemd-notice/firmware` (#50), `claudemd-notice/local-model-serve` (#51); `firmware-harness-connection` (no commits of its own yet); `cloud/m8-coder-wip` (1 commit, "not for merge"); merged and still on GitHub: `tech/backend` (#68), `tech/runbook` (#71), `tech/page-fixes` (#70), `tech/approval-example` (#66, #69), `tech/adapters` (#67), `tech/page` (#65), `james/arm-sim-ydcsss`, `full-test-3` (#64), `full-test-2` (#62; #63 still open), `doctor/repro-seal` (#57), `james/arm-sim` (#55), `arm/teleop-recording` (Andre, merged into main through #55), `olir/arm-ultrasonic-heal` (#54), `local-model-serve` (#43), `ishaan/pod-agent-loop` (#41), `ishaan/device-healer` (#31), `firmware` and `demo` (Olir)
<!-- team-status:end -->
