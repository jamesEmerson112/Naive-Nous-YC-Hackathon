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

### Romulus: self-healing + fleet layer for the arm company (Kent)
<!-- romulus-sync:start -->
_Kent's session refreshes this every 10 min · Last synced: Sat Nov 7, 10:10 PM PST · write notes outside the markers_

- **Pitch:** we sell uptime: one heal loop at arm, fleet and company scale. Rivals only diagnose ([Alloy](https://theaiinsider.tech/2026/08/13/alloy-robotics-raises-8m-in-new-funding-to-help-engineers-debug-robot-fleets-with-ai-agents/)) or recommend fixes ([Formant F3](https://www.businesswire.com/news/home/20250630008190/en/Formant-F3-Brings-Generative-AI-and-Agentic-Reasoning-to-Robot-Ops)). We close the loop: "PagerDuty with hands" *(inferred)*.
- **Rule:** no LLM in the real-time control loop. Models propose reversible fixes, and the gate checks them: replay → simulated canary → automatic promote or rollback. People only touch the kill switch (D6, D11).
- **Rule:** the healer may patch the safety layer and the visor (D5a). Those changes are flagged `touches_safety` and always go to the simulated canary first (K17).
- **Rule:** each action type earns autonomy and loses it after a bad fix. The overnight unattended run is the proof *(inferred)*.
- **Demo:** cover the camera or push a bad policy → arm parks or rolls back → root cause on the timeline → AgentMail incident report → hardware fault? Mobilerun orders the part.
- **Research:** pin LeRobot v0.7.0, since it breaks the API ([release](https://github.com/huggingface/lerobot/releases/tag/v0.7.0)).
- **Research:** the SO-101 sim twin is `robotstudio_so101` in MuJoCo Menagerie, and it runs on a Mac ([menagerie](https://github.com/google-deepmind/mujoco_menagerie)).
- **Research:** bring a router, Ethernet and a backup video, because venue Wi-Fi sinks demos ([Nailing the Demo](https://generalrobots.substack.com/p/nailing-the-demo)). Everything else is in [`docs/cloud-platform-planning/README.md`](docs/cloud-platform-planning/README.md).
- **Now:** #38 is the only open PR: it runs Ishaan's real `device/` code through the coder path (B1, B3). The Fleet Doctor has 8 of its 9 PRs merged, behind `doctor_mode=off`. PR 7 (real evidence, draft and check) hasn't started ([plan](docs/fleet-doctor/implementation-plan.md)). Next: P0 Stripe direction with James · P1 live Naïve card · P2 overnight launch by 12:30 AM.
- **Done:** M0 #1–#4 · M1 #5, #8 · M2 #9 · M3 #7, #12, #18 · M4 #10 · M5 #11, #15 · M6 #14 · M7 #16 · M8a #19 · M8b #28 · B8 toolchain #24 · edge bridge #25 · docs #21 · Fleet Doctor plan #22, decisions #23, contracts #26, foundation #27, LLM helper #29, L1 ask #34, L2 consult #35, device reference #32, held-overlay lock-in #39, ledger #33, L3 contract draft #30, tracker #37 · triage #6 · lint #13, #40 · device kinds #17 · decisions D0–D28 locked (D27 deferred).
- **Blocked:** only on the K17 safety and visor path list, which also gates the Fleet Doctor. B4 is clear (the live card waits only on Kent's go-ahead, #21), and B1 cleared when Ishaan's device code merged as #31 (`device/`, `harness/`, `bench/`).
- **Asks:** Ishaan: review the contracts (I0: B2, B3) and send the K17 safety and visor paths. James: decide the Stripe direction with Kent (bill the robot's owner, or pay the supplier). Owed to James: a Naïve key with Board scopes.
<!-- romulus-sync:end -->

### Device-side healer (Ishaan)
<!-- ishaan-sync:start -->
_Ishaan's local Claude session refreshes this block about every hour. Last synced: Sat Nov 7, 9:12 PM PST._
- **Now:** the pod healer agent loop and blind eval (HARNESS.md) on `ishaan/pod-agent-loop`: `firmware/harness/agent.py` with Claude and Ollama drivers, a risk gate and `--base-url` so the demo fallback can go through Kent's server; 12 tests pass (`6915ac8`, `90dbe6e`, session "Build the pod healer agent loop and blind eval", 20:55). No PR yet. The Jetson link is paused: no USB data or LAN path as of 20:10 (session "Identify the connected Jetson").
- **Just finished:** device code (B1), healer harness h1-h3 and benchmark merged as #31 (`6c3b006`); claimed the pod healer agent loop in `features.txt` (`023f30d`). Earlier: benchmark results and model pick, qwen3.5:4b with thinking on, h3 (`835c4f0`).
- **Blocked on:** bench runs for the pod loop need SSH to the pod, Olir's OK and an API key (session recap). The Jetson link (no USB data, no LAN). Still owes Kent the K17 safety and visor path list and the contracts review (B2, B3), per Kent's block.
- **Branch and files:** `ishaan/pod-agent-loop` in worktree `naive-pod-loop` (`firmware/harness/agent.py`, `evaluate.py`, tests, README). Main checkout on `main`, clean, 11 behind origin.
<!-- ishaan-sync:end -->

**Pod healer agent loop, for Olir's session:** running or benchmarking it on the bench (`firmware/harness/`, merged as #41)? Start with [`firmware/harness/HANDOFF.md`](firmware/harness/HANDOFF.md): bench steps, what's verified, what to report.

## Team status
<!-- team-status:start -->
_Last updated: Sat Nov 7, 10:09 PM PST_

**Current focus:** a robotic-arm company. Naïve runs the business and the job board; Hermes on the bench drives the arm through a plugin; an LLM operates the arm, with RunPod for anything power- or VRAM-heavy. Ishaan's pod healer loop merged (#41) and is handed to Olir's session for bench runs (`firmware/harness/HANDOFF.md`). Kent's #38 (real `device/` code in the coder path, B1) is the only open PR; Fleet Doctor has 8 of 9 PRs merged, all off by default. Andre's `arm/teleop-recording` still has no PR.

**Recently done:**
- Ishaan merged #41 (`4ee3517`, 10:04 PM): pod healer agent loop and blind eval in `firmware/harness/` (`agent.py` with Claude and Ollama drivers and a risk gate, `evaluate.py`, 13 offline tests, README, HANDOFF.md). Not yet run on the bench
- Ishaan pointed Olir's session to the handoff in CLAUDE.md (`8d9a30b`, 10:04 PM): bench up → model reachable → smoke run → scenario 05 → baseline on 05, 01, 02, 03, 06 → report
- Kent merged #39 (`945b1a0`, 10:01 PM): Fleet Doctor PR 9, a held Doctor overlay locks in without a second Coder pass (D24) (`services/doctor_lockin.py`, flow test `test_f12b_doctor_lock_in.py`). PR 7 (real evidence, draft and check) has not started
- Kent merged #32 (`67d9c3a`, 9:49 PM): Fleet Doctor PR 5, device reference code in `tools/fake_device` (trigger hook, proposal applier, mark-good). Off by default

**Open questions:**
- Pod healer bench runs: where should Ollama run, on the Pi (3.3 GB pull, speed unknown) or on a laptop over the LAN (HANDOFF.md)? Nothing has been checked on the bench yet: podd, SWD, flashing, the scenarios, the SSH `--target` path, or the `claude` driver against the live API
- Kent is still blocked only on the K17 safety and visor path list from Ishaan (it also gates the Fleet Doctor), plus his own go-ahead for the live Naïve card. Ishaan also still owes the contracts review (B2, B3)
- #31 flagged two clashes: the arm's servo bus and `podd` both default to `/dev/ttyACM0`, and `robot/` reads the ultrasonic in cm from Pi GPIO while Olir's pod reports mm. Should `robot/` follow `arm/safe_replay.py` (`/dev/serial/by-id/`, mm through `podd`)? *(inferred)*
- Team calls still open: Stripe direction (bill the robot owner or pay the supplier), Monid's job, owners for the unclaimed lines (fleet-ops, Mobilerun key, Stripe payment, AgentMail email), and the arm prototypes before Kent's 9–10 AM "real arm + polish" slot. The overnight launch is due by 12:30 AM (Kent's plan)

**Recent commits (all authors):**
- `24d965b` Sat 22:08 · Kent Gang · Update Kent status in CLAUDE.md
- `be5cd12` Sat 22:08 · Kent Gang · Update Kent status in CLAUDE.md
- `8d9a30b` Sat 22:04 · Ishaan Ranjan · Point Olir's session to the pod healer handoff in CLAUDE.md
- `4ee3517` Sun 01:04 · Ishaan Ranjan · Pod healer agent loop and blind eval in firmware/harness (#41)
- `945b1a0` Sat 22:01 · kgang · Fleet Doctor PR 9: held Doctor overlay locks in without a second Coder pass (D24) (#39)
- `5e22672` Sat 21:59 · jamesEmerson112 · Update team status and features.txt
- `c1aec2c` Sat 21:58 · Kent Gang · Update Kent status in CLAUDE.md
- `67d9c3a` Sat 21:49 · kgang · Fleet Doctor PR 5: device reference code in fake_device (trigger hook, proposal applier, mark-good) (#32)
- `eb839ac` Sat 21:49 · jamesEmerson112 · Update team status and features.txt
- `6d3bd65` Sat 21:48 · Kent Gang · Update Kent status in CLAUDE.md

**Branches on GitHub:** `main`; open PR branch `cloud/b1-device-code` (#38, Kent); `arm/teleop-recording` (Andre, no PR); `firmware-harness-connection` (no commits of its own yet); `cloud/m8-coder-wip` (1 commit, "not for merge"); merged and still on GitHub: `ishaan/pod-agent-loop` (#41), `doctor/lock-in` (#39), `ishaan/device-healer` (#31), `firmware` and `demo` (Olir)
<!-- team-status:end -->
