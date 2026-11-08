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

### Fleet learning is being built now (from Kent, Sun Nov 8 ~9:10 AM)
Plan: [`docs/recursive-self-improvement/`](docs/recursive-self-improvement/README.md). PRs land on `main` from branches `learn/*`; the demo stack is `COMPOSE_PROJECT_NAME=learn` on **:8001** (worktree `.claude/worktrees/learn-stack`), separate from `tech` on :8000.
- **Olir:** please don't land the P0 frozen-ultrasonic rule (`features.txt:32`) by hand before 1 PM. Fleet learning measures the port of the arm-sim watchdog's frozen check (`armsim/level1.py:30-44`) and **stages it for you with the numbers**, because it touches `arm/safe_replay.py` (D25). You review and land it after the demo.
- **James:** fleet learning trains nothing. It labels every heal run by its verifier and exports a standing `trajectories` dataset (Hermes format, `completed` = verdict) as input for your Level 3.
- `cloud/src` merges to `main` don't hot-reload Kent's `tech` demo: that stack mounts from `.claude/worktrees/tech-demo`, pinned at `6e5be55`.

### Romulus: self-healing + fleet layer for the arm company (Kent)
<!-- romulus-sync:start -->
_Kent's session refreshes this every 10 min · Last synced: Sun Nov 8, 9:58 AM PST · write notes outside the markers_

- **Pitch:** we sell uptime: one heal loop at arm, fleet and company scale. Rivals only diagnose ([Alloy](https://theaiinsider.tech/2026/08/13/alloy-robotics-raises-8m-in-new-funding-to-help-engineers-debug-robot-fleets-with-ai-agents/)) or recommend fixes ([Formant F3](https://www.businesswire.com/news/home/20250630008190/en/Formant-F3-Brings-Generative-AI-and-Agentic-Reasoning-to-Robot-Ops)). We close the loop: "PagerDuty with hands" *(inferred)*.
- **Rule:** no LLM in the real-time control loop. Models propose reversible fixes, and the gate checks them: replay → simulated canary → automatic promote or rollback. People only touch the kill switch (D6, D11).
- **Rule:** the healer may patch the safety layer and the visor (D5a). Those changes are flagged `touches_safety` and always go to the simulated canary first (K17).
- **Rule:** each action type earns autonomy and loses it after a bad fix. The overnight unattended run is the proof *(inferred)*.
- **Demo:** cover the camera or push a bad policy → arm parks or rolls back → root cause on the timeline → AgentMail incident report → hardware fault? Mobilerun orders the part.
- **Research:** pin LeRobot v0.7.0, since it breaks the API ([release](https://github.com/huggingface/lerobot/releases/tag/v0.7.0)).
- **Research:** the SO-101 sim twin is `robotstudio_so101` in MuJoCo Menagerie, and it runs on a Mac ([menagerie](https://github.com/google-deepmind/mujoco_menagerie)).
- **Research:** bring a router, Ethernet and a backup video, because venue Wi-Fi sinks demos ([Nailing the Demo](https://generalrobots.substack.com/p/nailing-the-demo)). Everything else is in [`docs/cloud-platform-planning/README.md`](docs/cloud-platform-planning/README.md).
- **Now:** last PR before the 10 AM freeze is #86 (wire the `/learning` page to runs, review checks and R2 events). Fleet learning is otherwise merged end to end: labelled runs → `trajectories` export (#78), the cluster review service with the skill lane through the gate (#82), the promoted playbook mirrored to Naïve Skills (#81) and the `/learning` page (#77). The Sunlit Workshop redesign covers Technician, incident, run, Data and Fleet (#80, #83–#85).
- **Done:** fleet learning #74–#78 #81 #82 · Sunlit Workshop redesign #80 #83 #84 #85 · Naïve Technician T0 #65 #67 #68 #70 #71 #72 #79, Studio approval #66 #69 · M0 #1–#4 · M1 #5, #8 · M2 #9 · M3 #7, #12, #18 · M4 #10 · M5 #11, #15 · M6 #14 · M7 #16 · M8a #19 · M8b #28 · real device code #38 · replay hardening #53 · B5 sandbox #46 · held-out verify #58 · B8 toolchain #24 · edge bridge #25 · docs #21, #45 · Fleet Doctor #22, #23, #26, #27, #29, #30, #32–#35, #37, #39, #42, #44, #52, #57, #59–#61 · triage #6 · lint #13, #40 · device kinds #17 · decisions D0–D29 locked (D27 deferred; D30 drafted).
- **Blocked:** the 1 PM demo needs a cloud with main plus `TECHNICIAN_MODE` (Kent's `tech` stack or a teammate's), and D30 is still a draft. The rehearsal settled the servo and the live Naïve writes in practice *(inferred)*. Fleet Doctor still waits on the K17 safety and visor path list. B1 and B4 are clear.
- **Asks:** Andre: confirm our STS3215 is the C018 12 V (the rehearsal staged it). Team: who's the technician for the demo email (plus phone for T1)? Ishaan: K17 safety and visor paths and the contracts review (B2, B3).
<!-- romulus-sync:end -->

### Device-side healer (Ishaan)
<!-- ishaan-sync:start -->
_Ishaan's local Claude session refreshes this block about every hour. Last synced: Sun Nov 8, 9:11 AM PST._
- **Now:** the judges' video (top 10 get to pitch on stage) on `ishaan/demo-video-animation`: recording the demo takes now from a rough script, per session "Demo video planning and editing" (09:06). Pod healer loop is with Olir for bench runs (`firmware/harness/HANDOFF.md`); the Jetson link is still paused.
- **Just finished:** demo video storyboard v1 and v2 built on the real arm07 bench heal and Andre's pitch scenes (`9f25882`, `6d04a14`, `3ea9b46`), Remotion animatic setup and re-cut recipe (`af02501`), one-page script with the 12-beat story line and every quote (`190d776`). Earlier: pod healer loop and blind eval #41 (`4ee3517`).
- **Blocked on:** bench runs wait on Olir (SSH to the pod, API key). Still owes Kent the K17 safety and visor path list and the contracts review (B2, B3), per Kent's block.
- **Branch and files:** video work on `ishaan/demo-video-animation` (worktree `naive-demo-video-animation`, at `190d776`, pushed, not on `main` *(inferred)*). Main checkout on `main` at `81fda0b`, clean, 50 behind origin.
<!-- ishaan-sync:end -->

**Pod healer agent loop, for Olir's session:** running or benchmarking it on the bench (`firmware/harness/`, merged as #41)? Start with [`firmware/harness/HANDOFF.md`](firmware/harness/HANDOFF.md): bench steps, what's verified, what to report.

## Team status
<!-- team-status:start -->
## Team status
_Last updated: Sun Nov 8, 9:59 AM PST_

**Current focus:** P3 → P4 at the 10 AM code freeze. On the Pi, James is taking the real arm to a wave-only run: a `poses.json` built from a live read of the arm's folded pose, a guard in `RealArm.goto` that refuses the pick poses before any servo command, and iPhone camera snapshots before and after each move to check the arm did what was asked. Kent's fleet learning and Sunlit Workshop redesign are fully on `main`; Andre opened **#87** (Hardware Buddy pitch animation, three styles); Ishaan is recording the judges' demo video.

**Recently done:**
- Kent merged **#82** `a6b2c2b` (cluster review service: the skill lane through the gate, a Naïve error never sticks the lane, review and Naïve Skills publish each under a savepoint) and **#86** `8be03db` (the `/learning` page wired to labelled runs, review checks and R2 events, with a Recent reviews row and a Naïve Skills chip)
- Kent merged **#85** `dd626b1` (Fleet page redesign); the Sunlit Workshop redesign now covers Technician, incident, run, Data and Fleet (#80, #83–#85)
- Fleet learning is merged end to end: labelled runs → `trajectories` export (#78), cluster review (#74, #82), frozen-echo probe (#75), playbook mirrored to Naïve Skills (#81), `/learning` page (#77, #86)
- Andre opened PR **#87** from `claude/hardware-buddy-animation-56b7ff` (pitch animation, three styles)

**Open questions:**
- Real arm: James is going wave only, since there's no committed `poses.json` with the pick poses; does Andre still have recorded pick poses to add after the freeze, or is the demo wave only? *(inferred)*
- 1 PM demo: which cloud runs `main` (Kent's `tech` stack is pinned at `6e5be55`, so `cloud/src` merges don't reach it)? D30 is still a draft; Andre to confirm the STS3215 is the C018 12 V; who is the technician for the demo email (plus phone for T1)?
- Olir: Kent asks that the P0 frozen-ultrasonic rule not land by hand before 1 PM; fleet learning stages it with the #75 numbers for Olir to review after the demo (touches `arm/safe_replay.py`, D25)
- Still open: which demo video is the backup for 1 PM (the "Backup demo video" line is unclaimed) *(inferred)*; the worker/manager review fixes on `james/arm-sim-ydcsss` aren't on `main`; Ishaan owes Kent the K17 safety and visor paths and the contracts review (B2, B3); #63 is still open though `full-test-2` is merged *(inferred: can be closed)*

**Recent commits (all authors):**
- `8be03db` Sun 09:57 · kgang · Merge pull request #86 from jamesEmerson112/learn/page-wire
- `f923b5c` Sun 09:57 · Kent Gang · Update Kent status in CLAUDE.md
- `c2c4824` Sun 09:56 · Kent Gang · Fleet learning page test: date the probe before the recorded session
- `ef68fa9` Sun 09:55 · Kent Gang · Fleet learning page: tests for the switcher, the mirror chip and runs.load evidence
- `0ba4862` Sun 09:55 · Kent Gang · Fleet learning page: Recent reviews row, Naïve Skills chip, evidence verifiers
- `f71fc93` Sun 09:55 · Kent Gang · Fleet learning page: wire labelled runs, review checks and R2's events
- `a6b2c2b` Sun 09:55 · kgang · Merge pull request #82 from jamesEmerson112/learn/review-service
- `78d28ce` Sun 09:54 · Kent Gang · learning.tick: review and the Naive Skills publish each under a savepoint, so neither can roll back the dataset step
- `c91a9dc` Sun 09:54 · Kent Gang · Cluster review: a Naive error never sticks the lane; per-database case-id salt
- `ccfa031` Sun 09:42 · Kent Gang · .env.example: cluster review settings and the page lane's REVIEW_FIXTURE and PROBE_DIR

**Branches on GitHub:** `main`; open PRs: `claude/hardware-buddy-animation-56b7ff` (#87, Andre's pitch animations), `main` (#63); `ishaan/demo-video-animation` (Ishaan's demo video); merged and still on GitHub: `learn/review-service` (#82), `ui/fleet` (#85), `learn/page` (#77), `learn/data-spine` (#78), `learn/naive-publish` (#81), `ui/technician` (#83), `ui/incident` (#84), `ui/foundation` (#80), `cloud/technician-live-default` (#79), `learn/review-domain` (#74), `learn/evidence` (#75), `learn/demo-data` (#76), `tech/docs-measured` (#72), `test-4` (#73); the notice PR branches `claudemd-notice/arm-teleop-recording` (#47), `claudemd-notice/demo` (#48), `claudemd-notice/firmware-harness-connection` (#49), `claudemd-notice/firmware` (#50), `claudemd-notice/local-model-serve` (#51); `firmware-harness-connection` (no commits of its own yet); `cloud/m8-coder-wip` (1 commit, "not for merge"); also merged: `tech/backend` (#68), `tech/runbook` (#71), `tech/page-fixes` (#70), `tech/approval-example` (#66, #69), `tech/adapters` (#67), `tech/page` (#65), `james/arm-sim-ydcsss`, `full-test-3` (#64), `full-test-2` (#62; #63 still open), `doctor/repro-seal` (#57), `james/arm-sim` (#55), `arm/teleop-recording` (Andre, merged into main through #55), `olir/arm-ultrasonic-heal` (#54), `local-model-serve` (#43), `ishaan/pod-agent-loop` (#41), `ishaan/device-healer` (#31), `firmware` and `demo` (Olir)
<!-- team-status:end -->
