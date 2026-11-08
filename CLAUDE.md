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
_Kent's session refreshes this every 10 min · Last synced: Sun Nov 8, 1:50 AM PST · write notes outside the markers_

- **Pitch:** we sell uptime: one heal loop at arm, fleet and company scale. Rivals only diagnose ([Alloy](https://theaiinsider.tech/2026/08/13/alloy-robotics-raises-8m-in-new-funding-to-help-engineers-debug-robot-fleets-with-ai-agents/)) or recommend fixes ([Formant F3](https://www.businesswire.com/news/home/20250630008190/en/Formant-F3-Brings-Generative-AI-and-Agentic-Reasoning-to-Robot-Ops)). We close the loop: "PagerDuty with hands" *(inferred)*.
- **Rule:** no LLM in the real-time control loop. Models propose reversible fixes, and the gate checks them: replay → simulated canary → automatic promote or rollback. People only touch the kill switch (D6, D11).
- **Rule:** the healer may patch the safety layer and the visor (D5a). Those changes are flagged `touches_safety` and always go to the simulated canary first (K17).
- **Rule:** each action type earns autonomy and loses it after a bad fix. The overnight unattended run is the proof *(inferred)*.
- **Demo:** cover the camera or push a bad policy → arm parks or rolls back → root cause on the timeline → AgentMail incident report → hardware fault? Mobilerun orders the part.
- **Research:** pin LeRobot v0.7.0, since it breaks the API ([release](https://github.com/huggingface/lerobot/releases/tag/v0.7.0)).
- **Research:** the SO-101 sim twin is `robotstudio_so101` in MuJoCo Menagerie, and it runs on a Mac ([menagerie](https://github.com/google-deepmind/mujoco_menagerie)).
- **Research:** bring a router, Ethernet and a backup video, because venue Wi-Fi sinks demos ([Nailing the Demo](https://generalrobots.substack.com/p/nailing-the-demo)). Everything else is in [`docs/cloud-platform-planning/README.md`](docs/cloud-platform-planning/README.md).
- **Now:** #46 (B5: the coding-agent sandbox reaches only the LLM, key held by a proxy) is the only open PR. #53 merged the hardened code replay, with a sealed device-test result that can't be gamed (K6), and #56 was closed at the same time. The overnight run is the fake fleet, and the live Naïve card is still pending.
- **Done:** M0 #1–#4 · M1 #5, #8 · M2 #9 · M3 #7, #12, #18 · M4 #10 · M5 #11, #15 · M6 #14 · M7 #16 · M8a #19 · M8b #28 · real device code #38 · replay hardening #53 · B8 toolchain #24 · edge bridge #25 · docs #21, #45 · Fleet Doctor plan #22, decisions #23, contracts #26, foundation #27, LLM helper #29, L1 ask #34, L2 consult #35, device reference #32, held-overlay lock-in #39, F12 interop #42, PR 7 evidence #44, tracker #52, ledger #33, L3 contract draft #30, tracker #37 · triage #6 · lint #13, #40 · device kinds #17 · decisions D0–D28 locked (D27 deferred).
- **Blocked:** only on the K17 safety and visor path list, which also gates the Fleet Doctor. B4 is clear (the live card waits only on Kent's go-ahead, #21), and B1 cleared when Ishaan's device code merged as #31 (`device/`, `harness/`, `bench/`).
- **Asks:** Ishaan: review the contracts (I0: B2, B3) and send the K17 safety and visor paths. James now reaches the Naïve board through a board-only MCP entry, and his sim run logged a `us_frozen` incident in Romulus *(team status)*.
<!-- romulus-sync:end -->

### Device-side healer (Ishaan)
<!-- ishaan-sync:start -->
_Ishaan's local Claude session refreshes this block about every hour. Last synced: Sun Nov 8, 12:01 AM PST._
- **Now:** pod healer loop handed to Olir's session for bench runs (`firmware/harness/HANDOFF.md`, `8d9a30b`); session "Build the pod healer agent loop and blind eval" stood down at 21:47 with no bench run. Not on the bench yet. The Jetson link is still paused (no USB data or LAN path, session "Identify the connected Jetson").
- **Just finished:** pod healer agent loop and blind eval merged as #41 (`4ee3517`): `agent.py` with Claude and Ollama drivers and a risk gate, `evaluate.py`, tests, README, HANDOFF.md (`72f53c8`); pointed Olir's session to the handoff in CLAUDE.md (`8d9a30b`). Earlier: device code and healer harness #31 (`6c3b006`).
- **Blocked on:** bench runs wait on Olir (SSH to the pod, API key, per the session recap). Still owes Kent the K17 safety and visor path list and the contracts review (B2, B3), per Kent's block.
- **Branch and files:** main checkout on `main` at `9c61066`, clean, no uncommitted files. `ishaan/pod-agent-loop` (worktree `naive-pod-loop`) is merged.
<!-- ishaan-sync:end -->

**Pod healer agent loop, for Olir's session:** running or benchmarking it on the bench (`firmware/harness/`, merged as #41)? Start with [`firmware/harness/HANDOFF.md`](firmware/harness/HANDOFF.md): bench steps, what's verified, what to report.

## Team status
<!-- team-status:start -->
## Team status
_Last updated: Sun Nov 8, 1:40 AM PST_

**Current focus:** a robotic-arm company. Naïve runs the business and the job board; Hermes on the bench drives the arm through a plugin; an LLM operates the arm, with RunPod for anything power- or VRAM-heavy. P2 overnight runs to 7 AM, where Olir's pod logs under podd are the only real data (Kent's run is the fake fleet), then P3 the real arm 7–10 AM (`features.txt`, `hub/src/content/proposal.ts`). James merged the demo branch (#55) into main at 1:32 AM, so the sim loop, the real-arm adapter and Andre's `arm/prims/` are now on main; next for James is the Pi with Andre (pin serial ports, teach Andre's four poses, `demo/up.sh --real`). Kent's next steps: the overnight go/no-go and the live Naïve card; his open PRs are #46 (B5), #53 (replay hardening, K6) and #56 (Doctor repro reads the seal, stacked on #53). Olir still has the frozen-ultrasonic rule and a review of the firmware doc.

**Recently done:**
- James merged #55 into main (1:32 AM, `0871be7`, from `james/arm-sim`): the whole heal loop in sim on one laptop (virtual pod speaking the STM32 line format to Olir's unchanged `podd`, watchdog host, Romulus reporting, `RealArm` over Andre's `prims.Arm`, one command per story, Hermes `so101-arm` plugin), plus Andre's `arm/` (prims, recording, calibration `naive_follower.json`, wave and two recordings) and the Naïve console README with screenshots. Final regression (`prototypes/arm-sim/MORNING.md`): stories A (images 07 and 02), B and stall pass; fault factory 1,000 runs, 0 of 142 healthy flagged, 115 of 115 dead servos caught, other planted faults caught in 23–54%
- Kent opened #56 (1:25 AM, `doctor/repro-seal`, `c127206`): the Fleet Doctor's repro steps take failed and passed tests only from a complete result seal and fail closed when unsealed. It closes a forger he confirmed against #53, where a repro test printed a fake pytest summary and called `os._exit(0)` so a no-op fix reached `applied/waiting`. Retargets to main once #53 merges
- Kent pushed a second fix round on #53 (1:16 AM, `70d7b74`): the seal now writes a start marker, so a pytest run that exits early without an end marker counts as incomplete and `TestRun.passed` / `VerifyRun.all_passed` are False. This follows `18277d5` (12:59 AM: verify never imports the candidate)
- Olir merged #54 (12:25 AM, `d5f99d6`, `303a007`): the arm heal scenario. Bad firmware v1.7 makes the SO-101 flail (obstacle reflex, exit 4); the agent rolls back, runs the job itself and checks the motion. On the real pod and real arm, qwen3.5:4b passed all 11 checks: rollback on turn 3, its own job on turn 4, 663 of 663 frames (`firmware/harness/RESULTS.md`, 12:05 AM run)

**Open questions:**
- Fleet Doctor follow-ups (#52): keep `DOCTOR_DRAFT=coder` off on real device text until B5 (#46) merges; `SAFETY_PATHS_CONFIRMED` stays false, discarding every proposal, until Ishaan sends the K17 safety and visor path list; `DOCTOR_USD_PER_1K_TOKENS=0.001` is a placeholder; #53 waits on a re-review of the fix rounds (`18277d5`, `70d7b74`), and #56 waits on #53
- Pod healer: bench runs now pass with the Ollama driver (`RESULTS.md`), but `firmware/healer/detect.py` still has no frozen or `out_of_range` ultrasonic rule, so a stuck distance fires nothing; porting `acc_frozen`'s zero-variance test to `us` would cover it (`docs/firmware-apis.md` section 6) *(inferred)*. `303a007` also committed `arm/__pycache__/safe_replay.cpython-313.pyc` to main *(inferred: should be gitignored)*
- Who drives the real arm: with #55 on main, main now holds three real-arm drivers (Andre's `arm/prims/`, Olir's `arm/safe_replay.py` from #36, and `RealArm` in `prototypes/arm-sim/` on top of Andre's prims), plus the cm-vs-mm ultrasonic and shared `/dev/ttyACM0` clash from #31. James's session proposes the arm host runs the job (Naïve card → Hermes) and reports to Romulus, with Olir's pod healer fixing only the pod (no own `arm_run`); still to settle with Olir and Andre *(team status)*
- Team calls still open: Stripe is optional, only if we're ahead; Monid's job; owners for the unclaimed lines (fleet-ops, Mobilerun key, Stripe payment, AgentMail email); live vs fake work orders for the demo (live texts a teammate's phone)

**Recent commits (all authors):**
- `0871be7` Sun 01:32 · jamesEmerson112 · Merge pull request #55 from jamesEmerson112/james/arm-sim
- `4afd6c5` Sun 01:29 · jamesEmerson112 · Update team status and features.txt
- `07b52e6` Sun 01:28 · Kent Gang · Update Kent status in CLAUDE.md
- `dc56bf0` Sun 01:27 · jamesEmerson112 · arm-sim: final regression numbers in the morning brief
- `5393d64` Sun 01:23 · jamesEmerson112 · arm-sim S5 docs: README for the whole loop, the morning brief; Stack Map: S5 done
- `7d193f8` Sun 01:21 · jamesEmerson112 · arm-sim: story A defaults to bad image 07, the bench demo's fault (#54); up.sh runs on macOS bash 3.2
- `c87098e` Sun 01:18 · jamesEmerson112 · Update team status in CLAUDE.md
- `3a32db0` Sun 01:18 · Kent Gang · Update Kent status in CLAUDE.md
- `78df64d` Sun 01:18 · jamesEmerson112 · Merge remote-tracking branch 'origin/main' into james/arm-sim
- `6da7449` Sun 01:16 · jamesEmerson112 · arm-sim S5: one command per story, start/stop scripts, recordings for the backup video

**Branches on GitHub:** `main`; open PR branches `cloud/b5-egress` (#46, Kent), `cloud/replay-hardening` (#53, Kent), `doctor/repro-seal` (#56, Kent, stacked on #53) and the notice PR branches `claudemd-notice/arm-teleop-recording` (#47), `claudemd-notice/demo` (#48), `claudemd-notice/firmware-harness-connection` (#49), `claudemd-notice/firmware` (#50), `claudemd-notice/local-model-serve` (#51); `firmware-harness-connection` (no commits of its own yet); `cloud/m8-coder-wip` (1 commit, "not for merge"); merged and still on GitHub: `james/arm-sim` (#55), `arm/teleop-recording` (Andre, merged into main through #55), `olir/arm-ultrasonic-heal` (#54), `local-model-serve` (#43), `ishaan/pod-agent-loop` (#41), `ishaan/device-healer` (#31), `firmware` and `demo` (Olir)
<!-- team-status:end -->
