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

### Fleet learning is built (from Kent, Sun Nov 8 ~10:10 AM)
Built and run live at 10:04 (PRs #74–#92); how to show it: [`runbook.md`](docs/recursive-self-improvement/runbook.md). Plan: [`docs/recursive-self-improvement/`](docs/recursive-self-improvement/README.md). The demo stack is `COMPOSE_PROJECT_NAME=learn` on **:8001** (worktree `.claude/worktrees/learn-stack`), separate from `tech` on :8000.
- **Olir:** please don't land the P0 frozen-ultrasonic rule (`features.txt:32`) by hand before 1 PM. Fleet learning measures the port of the arm-sim watchdog's frozen check (`armsim/level1.py:30-44`) and **stages it for you with the numbers**, because it touches `arm/safe_replay.py` (D25). You review and land it after the demo.
- **James:** fleet learning trains nothing. It labels every heal run by its verifier and exports a standing `trajectories` dataset (Hermes format, `completed` = verdict) as input for your Level 3.
- `cloud/src` merges to `main` don't hot-reload Kent's `tech` demo: that stack mounts from `.claude/worktrees/tech-demo`, pinned at `6e5be55`.

### Romulus: self-healing + fleet layer for the arm company (Kent)
<!-- romulus-sync:start -->
_Kent's session refreshes this every 10 min · Last synced: Sun Nov 8, 10:18 AM PST · write notes outside the markers_

- **Pitch:** we sell uptime: one heal loop at arm, fleet and company scale. Rivals only diagnose ([Alloy](https://theaiinsider.tech/2026/08/13/alloy-robotics-raises-8m-in-new-funding-to-help-engineers-debug-robot-fleets-with-ai-agents/)) or recommend fixes ([Formant F3](https://www.businesswire.com/news/home/20250630008190/en/Formant-F3-Brings-Generative-AI-and-Agentic-Reasoning-to-Robot-Ops)). We close the loop: "PagerDuty with hands" *(inferred)*.
- **Rule:** no LLM in the real-time control loop. Models propose reversible fixes, and the gate checks them: replay → simulated canary → automatic promote or rollback. People only touch the kill switch (D6, D11).
- **Rule:** the healer may patch the safety layer and the visor (D5a). Those changes are flagged `touches_safety` and always go to the simulated canary first (K17).
- **Rule:** each action type earns autonomy and loses it after a bad fix. The overnight unattended run is the proof *(inferred)*.
- **Demo:** cover the camera or push a bad policy → arm parks or rolls back → root cause on the timeline → AgentMail incident report → hardware fault? Mobilerun orders the part.
- **Research:** pin LeRobot v0.7.0, since it breaks the API ([release](https://github.com/huggingface/lerobot/releases/tag/v0.7.0)).
- **Research:** the SO-101 sim twin is `robotstudio_so101` in MuJoCo Menagerie, and it runs on a Mac ([menagerie](https://github.com/google-deepmind/mujoco_menagerie)).
- **Research:** bring a router, Ethernet and a backup video, because venue Wi-Fi sinks demos ([Nailing the Demo](https://generalrobots.substack.com/p/nailing-the-demo)). Everything else is in [`docs/cloud-platform-planning/README.md`](docs/cloud-platform-planning/README.md).
- **Now:** prepping the 11 AM table and the 1 PM stage. **Fleet learning is built** and ran live at 10:04: 2 Naïve sessions, 57 s from lesson to every arm, Naïve Skills v1 → v2 ([`runbook.md`](docs/recursive-self-improvement/runbook.md), #92) on `learn` (:8001). The Naïve Technician runs on `tech` (:8000) from its own [`runbook.md`](docs/naive-technician/runbook.md). No open PRs.
- **Done:** fleet learning #74–#78 #81 #82 #86 #89 #92 #93 · Sunlit Workshop redesign #80 #83–#85 #88 #90 #91 · Naïve Technician T0 #65 #67 #68 #70 #71 #72 #79, Studio approval #66 #69 · M0 #1–#4 · M1 #5, #8 · M2 #9 · M3 #7, #12, #18 · M4 #10 · M5 #11, #15 · M6 #14 · M7 #16 · M8a #19 · M8b #28 · real device code #38 · replay hardening #53 · B5 sandbox #46 · held-out verify #58 · B8 toolchain #24 · edge bridge #25 · docs #21, #45 · Fleet Doctor #22, #23, #26, #27, #29, #30, #32–#35, #37, #39, #42, #44, #52, #57, #59–#61 · triage #6 · lint #13, #40 · device kinds #17 · decisions D0–D29 locked (D27 deferred; D30 drafted).
- **Blocked:** the 1 PM demo needs a cloud with main plus `TECHNICIAN_MODE` (Kent's `tech` stack or a teammate's), and D30 is still a draft. The rehearsal settled the servo and the live Naïve writes in practice *(inferred)*. Fleet Doctor still waits on the K17 safety and visor path list. B1 and B4 are clear.
- **Asks:** Andre: confirm our STS3215 is the C018 12 V (the rehearsal staged it). Team: who's the technician for the demo email (plus phone for T1)? Ishaan: K17 safety and visor paths and the contracts review (B2, B3).
<!-- romulus-sync:end -->

### Device-side healer (Ishaan)
<!-- ishaan-sync:start -->
_Ishaan's local Claude session refreshes this block about every hour. Last synced: Sun Nov 8, 11:12 AM PST._
- **Now:** the judges' video (top 10 get to pitch on stage) on `ishaan/demo-video-animation`: recording the demo takes now from a rough script, per session "Demo video planning and editing" (09:06). Pod healer loop is with Olir for bench runs (`firmware/harness/HANDOFF.md`); the Jetson link is still paused.
- **Just finished:** demo video storyboard v1 and v2 built on the real arm07 bench heal and Andre's pitch scenes (`9f25882`, `6d04a14`, `3ea9b46`), Remotion animatic setup and re-cut recipe (`af02501`), one-page script with the 12-beat story line and every quote (`190d776`). Earlier: pod healer loop and blind eval #41 (`4ee3517`).
- **Blocked on:** bench runs wait on Olir (SSH to the pod, API key). Still owes Kent the K17 safety and visor path list and the contracts review (B2, B3), per Kent's block.
- **Branch and files:** video work on `ishaan/demo-video-animation` (worktree `naive-demo-video-animation`, at `190d776`, pushed, not on `main` *(inferred)*). Main checkout on `main` at `4d1a137`, clean, no uncommitted files.
<!-- ishaan-sync:end -->

**Pod healer agent loop, for Olir's session:** running or benchmarking it on the bench (`firmware/harness/`, merged as #41)? Start with [`firmware/harness/HANDOFF.md`](firmware/harness/HANDOFF.md): bench steps, what's verified, what to report.

## Team status
<!-- team-status:start -->
## Team status
_Last updated: Sun Nov 8, 12:38 PM PST_

**Current focus:** P4: hacking stopped at 11 AM; prepping the judges' table and the 1 PM stage. James's chips generate-and-test on the real arm (`armsim/gentest.py`, Hermes `Astra` designing each trial) is **paused**: the iPhone top-down camera dropped out of macOS (Continuity Camera lost it when the phone moved), so James is re-mounting the phone and re-picking it in Photo Booth before restarting the 1 fps capture; the arm is folded, no alarms. Every grasp so far squeezes to 99% but the bag slips after ~0.5–0.7 s (`ded0a78`). Next ideas for the hit rate (suggested in James's session, not built): friction tape on the jaws, staging the red bag where the recordings grab, and an OpenCV red-bag detector plus the `armsim/eye.py` calibration so Astra plans from centimetres, not guesses. Mission Control has two chips orders: pack the red bag (key 2, default) and pack any bag (key 9) (`557b026`). The fault stories (`a`, `b`, `stall`) haven't run on the real arm; the stall beat is sim-only today. Andre is trimming the Hardware Buddy pitch animation; Kent's fleet learning is built and ran live; Ishaan is recording the judges' video.

**Recently done:**
- James: Mission Control has two chips orders: key 2 packs the red bag (`wave`, the default) and key 9 packs any bag (`wave_any`), with dispatch and mission tests (`557b026`)
- James: gentest squeezes every grasp to 99% closed; Astra measures the bag in each after-frame (size, centre, nearest edge), grabs by its edge, and knows the white labels are part of the bag (`ded0a78`)
- James: gentest targets the red bag (the demo's) and passes every earlier try into Hermes's context, not just the last one (`e71102c`); Mission Control's Commander feed rows expand in place on hover, focus or click (`3adfea5`)
- James: Hermes debugs the chips pick, one test per trial: before each try it gets `context.json` (the recording, base shift, speed, grip data, and where the jaws closed and opened) plus before/after camera frames, says what went wrong and designs the next test (pan within ±10°, speed 0.5–1.0, clamped by the code). It stops when the eye sees the bag in the box, at the cap, or on a servo alarm (`e1a588c`)

**Open questions:**
- Naïve is flaky (429s and an `internal_error` this morning): go with the live board on stage, or the local fallback (Mission Control's button + the Pi's scripted dispatcher, proven at 11:10)? `armsim/hermes_file.py` (Hermes over the file board) isn't built (`HANDOFF-LOCAL.md` §4)
- Real arm: teaching an upright `home` failed (the arm stayed locked), so the stall beat is sim-only; `above_block` and `pick` aren't taught; the ultrasonic sensor is off the arm (podd reads ~659 mm), so story `b` needs it back on the wrist; story `a`'s pod healer needs Olir's `server/start.sh` relay to serve `qwen3.5:4b` to the Pi
- 1 PM demo: which cloud runs `main` with `TECHNICIAN_MODE` (Kent's `tech` stack is pinned at `6e5be55`)? D30 is still a draft; Andre to confirm the STS3215 is the C018 12 V; who is the technician for the demo email (plus phone for T1)? Which video is the backup ("Backup demo video" is unclaimed) *(inferred)*?
- Olir: the P0 frozen-ultrasonic rule waits until after the demo (fleet learning stages it with the #75 numbers); Ishaan still owes Kent the K17 safety and visor paths and the contracts review (B2, B3)

**Recent commits (all authors):**
- `626e20c` Sun 12:29 · jamesEmerson112 · Update team status in CLAUDE.md
- `7e41fa4` Sun 12:25 · jamesEmerson112 · Merge the two chips orders: pack the red bag (key 2, default) and pack any bag (key 9)
- `557b026` Sun 12:24 · jamesEmerson112 · Mission Control: two chips orders, pack the red bag (key 2, wave) and pack any bag (key 9, wave_any)
- `a239a7e` Sun 12:23 · jamesEmerson112 · Merge branch 'main' of https://github.com/jamesEmerson112/Naive-Nous-YC-Hackathon into james/arm-sim
- `ded0a78` Sun 12:23 · jamesEmerson112 · arm-sim: gentest squeezes every grasp to 99% closed; Astra measures the bag, grabs by its edge, knows the white labels
- `0472f14` Sun 12:19 · jamesEmerson112 · Update team status in CLAUDE.md
- `46f0552` Sun 12:09 · jamesEmerson112 · Update team status in CLAUDE.md
- `2247e87` Sun 12:06 · jamesEmerson112 · Merge the commander feed's hover-to-expand
- `3adfea5` Sun 12:04 · jamesEmerson112 · Mission Control: Commander feed rows expand in place on hover, focus or click
- `e71102c` Sun 11:59 · jamesEmerson112 · arm-sim: gentest aims at the red bag (the demo's), and carries every earlier try into Hermes's context

**Branches on GitHub:** `main`; open PR: `main` (#63, into Olir's `full-test-2`); `james/arm-sim-ydcsss` (merged into `main` in `d36ba8b`; nothing ahead); `ishaan/demo-video-animation` (Ishaan's demo video); `claude/hardware-buddy-animation-56b7ff` (#87, closed; `pitch/` is on `main`); merged and still on GitHub: `learn/wrap-up` (#93), `learn/runbook` (#92), `ui/technician-empty` (#91), `ui/learning` (#90), `learn/live-polish` (#89), `ui/polish` (#88), `learn/page-wire` (#86), `learn/review-service` (#82), `ui/fleet` (#85), `learn/page` (#77), `learn/data-spine` (#78), `learn/naive-publish` (#81), `ui/technician` (#83), `ui/incident` (#84), `ui/foundation` (#80), `cloud/technician-live-default` (#79), `learn/review-domain` (#74), `learn/evidence` (#75), `learn/demo-data` (#76), `tech/docs-measured` (#72), `test-4` (#73); the notice PR branches `claudemd-notice/arm-teleop-recording` (#47), `claudemd-notice/demo` (#48), `claudemd-notice/firmware-harness-connection` (#49), `claudemd-notice/firmware` (#50), `claudemd-notice/local-model-serve` (#51); `firmware-harness-connection` (no commits of its own yet); `cloud/m8-coder-wip` (1 commit, "not for merge"); also merged: `tech/backend` (#68), `tech/runbook` (#71), `tech/page-fixes` (#70), `tech/approval-example` (#66, #69), `tech/adapters` (#67), `tech/page` (#65), `full-test-3` (#64), `full-test-2` (#62), `doctor/repro-seal` (#57), `james/arm-sim` (#55), `arm/teleop-recording` (Andre, merged into main through #55), `olir/arm-ultrasonic-heal` (#54), `local-model-serve` (#43), `ishaan/pod-agent-loop` (#41), `ishaan/device-healer` (#31), `firmware` and `demo` (Olir)
<!-- team-status:end -->
