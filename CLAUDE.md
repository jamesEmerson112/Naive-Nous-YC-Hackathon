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
_Kent's session refreshes this every 10 min · Last synced: Sun Nov 8, 3:00 AM PST · write notes outside the markers_

- **Pitch:** we sell uptime: one heal loop at arm, fleet and company scale. Rivals only diagnose ([Alloy](https://theaiinsider.tech/2026/08/13/alloy-robotics-raises-8m-in-new-funding-to-help-engineers-debug-robot-fleets-with-ai-agents/)) or recommend fixes ([Formant F3](https://www.businesswire.com/news/home/20250630008190/en/Formant-F3-Brings-Generative-AI-and-Agentic-Reasoning-to-Robot-Ops)). We close the loop: "PagerDuty with hands" *(inferred)*.
- **Rule:** no LLM in the real-time control loop. Models propose reversible fixes, and the gate checks them: replay → simulated canary → automatic promote or rollback. People only touch the kill switch (D6, D11).
- **Rule:** the healer may patch the safety layer and the visor (D5a). Those changes are flagged `touches_safety` and always go to the simulated canary first (K17).
- **Rule:** each action type earns autonomy and loses it after a bad fix. The overnight unattended run is the proof *(inferred)*.
- **Demo:** cover the camera or push a bad policy → arm parks or rolls back → root cause on the timeline → AgentMail incident report → hardware fault? Mobilerun orders the part.
- **Research:** pin LeRobot v0.7.0, since it breaks the API ([release](https://github.com/huggingface/lerobot/releases/tag/v0.7.0)).
- **Research:** the SO-101 sim twin is `robotstudio_so101` in MuJoCo Menagerie, and it runs on a Mac ([menagerie](https://github.com/google-deepmind/mujoco_menagerie)).
- **Research:** bring a router, Ethernet and a backup video, because venue Wi-Fi sinks demos ([Nailing the Demo](https://generalrobots.substack.com/p/nailing-the-demo)). Everything else is in [`docs/cloud-platform-planning/README.md`](docs/cloud-platform-planning/README.md).
- **Now:** no open PRs. #58 merged, so the gate requires a held-out verify for code candidates, and B5 (#46) is in. Still pending: the live Naïve card and the real-arm run with the team at 9–10 AM (James's [`MORNING.md`](prototypes/arm-sim/MORNING.md)). The overnight run is the fake fleet.
- **Done:** M0 #1–#4 · M1 #5, #8 · M2 #9 · M3 #7, #12, #18 · M4 #10 · M5 #11, #15 · M6 #14 · M7 #16 · M8a #19 · M8b #28 · real device code #38 · replay hardening #53 · B5 sandbox #46 · held-out verify #58 · B8 toolchain #24 · edge bridge #25 · docs #21, #45 · Fleet Doctor plan #22, decisions #23, contracts #26, foundation #27, LLM helper #29, L1 ask #34, L2 consult #35, device reference #32, held-overlay lock-in #39, F12 interop #42, PR 7 evidence #44, tracker #52, #59–#61, repro seal #57, ledger #33, L3 contract draft #30, tracker #37 · triage #6 · lint #13, #40 · device kinds #17 · decisions D0–D28 locked (D27 deferred).
- **Blocked:** only on the K17 safety and visor path list, which also gates the Fleet Doctor. B4 is clear (the live card waits only on Kent's go-ahead, #21), and B1 cleared when Ishaan's device code merged as #31 (`device/`, `harness/`, `bench/`).
- **Asks:** Ishaan: review the contracts (I0: B2, B3) and send the K17 safety and visor paths. James now reaches the Naïve board through a board-only MCP entry, and his sim run logged a `us_frozen` incident in Romulus *(team status)*.
<!-- romulus-sync:end -->

### Device-side healer (Ishaan)
<!-- ishaan-sync:start -->
_Ishaan's local Claude session refreshes this block about every hour. Last synced: Sun Nov 8, 3:50 AM PST._
- **Now:** pod healer loop handed to Olir's session for bench runs (`firmware/harness/HANDOFF.md`, `8d9a30b`); session "Build the pod healer agent loop and blind eval" stood down at 21:47 with no bench run. Not on the bench yet. The Jetson link is still paused (no USB data or LAN path, session "Identify the connected Jetson").
- **Just finished:** pod healer agent loop and blind eval merged as #41 (`4ee3517`): `agent.py` with Claude and Ollama drivers and a risk gate, `evaluate.py`, tests, README, HANDOFF.md (`72f53c8`); pointed Olir's session to the handoff in CLAUDE.md (`8d9a30b`). Earlier: device code and healer harness #31 (`6c3b006`).
- **Blocked on:** bench runs wait on Olir (SSH to the pod, API key, per the session recap). Still owes Kent the K17 safety and visor path list and the contracts review (B2, B3), per Kent's block.
- **Branch and files:** main checkout on `main` at `b66d6fd`, clean, no uncommitted files. `ishaan/pod-agent-loop` (worktree `naive-pod-loop`) is merged.
<!-- ishaan-sync:end -->

**Pod healer agent loop, for Olir's session:** running or benchmarking it on the bench (`firmware/harness/`, merged as #41)? Start with [`firmware/harness/HANDOFF.md`](firmware/harness/HANDOFF.md): bench steps, what's verified, what to report.

## Team status
<!-- team-status:start -->
## Team status
_Last updated: Sun Nov 8, 4:30 AM PST_

**Current focus:** a robotic-arm company. Naïve runs the business and the job board; Hermes on the bench drives the arm through a plugin; an LLM operates the arm, with RunPod for anything power- or VRAM-heavy. P2 overnight runs to 7 AM, where Olir's pod logs under podd are the only real data (Kent's run is the fake fleet), then P3 the real arm 7–10 AM (`features.txt`, `hub/src/content/proposal.ts`). James's session on `james/arm-sim-ydcsss` finished the soak and Mission Control server fixes (4:21 AM) and is now building the worker/manager healer loop and its worker-model bench (WIP); the soak's overnight default is Hermes on the real Naïve board, which hasn't run a soak yet. Next is the Pi with Andre at 7 AM (pin serial ports, teach Andre's four poses, `demo/up.sh --real`). Olir's **#62** (`full-test-2` → main) still awaits review, and #63 (main → `full-test-2`) has conflicts. Kent's Romulus has no open PRs; next are the overnight go/no-go and the live Naïve card.

**Recently done:**
- James's session pushed **soak and Mission Control server fixes** (4:21 AM, `2ae2b91` on `james/arm-sim-ydcsss`, not on main, with tests): "healed" now means the fault landed before the card was done, this card's job noticed it, and the card ended done; plant timing, `busy` slots when another order holds the board, late claims and lost injects are handled; Hermes on the real board is the overnight default (`--board naive --with-dispatcher hermes`). The Mission Control server gained POST guards (JSON, Host/Origin, a token off loopback), request limits and lost-incident recovery after a host restart. Two Linux sim runs with the scripted commander went 5 of 5 done, 4 of 4 healed (`pod_image02` and `pod_image07` by the pod's rollback in 38–48 s, `us_frozen` by a sensor restart in 28 s). Workaround until the host has `reset_scene`: restart the host between orders (`prototypes/arm-sim/SOAK.md`)
- James's session's **worker/manager healer loop** (4:21 AM, `ac94acd`, `7d7b5dd`, WIP snapshots): `armsim/worker.py`, `escalation.py` and `manager.py`; a `hermes-manager/` profile kit (setup and check scripts, the `arm-incident-manager` skill, `arm-escalation/v1` and `arm-fixplan/v1` schemas, four sample packets from the sim); and `workerbench/`, a benchmark for small worker models on arm and pod incidents. Mission Control now has three looks (mission, neon, light). Design: `docs/worker-manager-healer.md` (`8a55f7c`)
- James's session pushed **arm host and board fixes** (3:52–3:53 AM, `b81c864`, `3657d0c` on `james/arm-sim-ydcsss`, not on main): the arm host survives a client hanging up mid-skill (the blocker from review), a silent client and errors in chores, and its state snapshot never waits on the watchdog lock; the board's `move(expect=)` is now compare-and-set, so a card can't be claimed twice. Plus a Mission Control page WIP snapshot (`b1ce2f0`, `armsim/mission/index.html`)
- Olir opened **#62** (`full-test-2` → main, 22 files, 3:41 AM) with the v1.8 near-hold heal test (`5eef70f`): bad image `08-us-near-hold` keeps a reading under 250 mm after a hand leaves, podd's `us_mismatch` rule flags it, the healer agent (qwen3.5:4b) restores known-good firmware, and the arm proves it over three loops (`arm/README.md`, `arm/tests/test_near_hold.py`)

**Open questions:**
- Fleet Doctor follow-ups (#52): B5 has merged, so `DOCTOR_DRAFT=coder` on real device text is no longer waiting on #46 *(inferred: Kent hasn't said it's switched on)*; `SAFETY_PATHS_CONFIRMED` stays false until Ishaan sends the K17 safety and visor path list; `DOCTOR_USD_PER_1K_TOKENS=0.001` is a placeholder. The worker/manager design also leaves extending `DoctorAdvice` with an action field to Kent (`docs/worker-manager-healer.md`)
- Frozen ultrasonic: `firmware/healer/detect.py` still has no frozen or `out_of_range` ultrasonic rule, and Level 2 (ChatTS) also misses a frozen trace without a healthy reference; the doc's cheaper fix is porting `acc_frozen`'s zero-variance test to `us` (`docs/healer-data-levels.md`, `docs/firmware-apis.md` section 6). `303a007` committed `arm/__pycache__/safe_replay.cpython-313.pyc` to main, and `5eef70f` updates it on `full-test-2` *(inferred: should be gitignored)*
- Who drives the real arm: main holds three real-arm drivers (Andre's `arm/prims/`, Olir's `arm/safe_replay.py` from #36, and `RealArm` in `prototypes/arm-sim/` on top of Andre's prims), plus the cm-vs-mm ultrasonic and shared `/dev/ttyACM0` clash from #31. James's session proposes the arm host runs the job (Naïve card → Hermes) and reports to Romulus, with Olir's pod healer fixing only the pod; still to settle with Olir and Andre *(team status)*
- Team calls still open: Stripe is optional, only if we're ahead; Monid's job; owners for the unclaimed lines (fleet-ops, Mobilerun key, Stripe payment, AgentMail email); live vs fake work orders for the demo (live texts a teammate's phone)

**Recent commits (all authors):**
- `983606a` Sun 03:59 · jamesEmerson112 · Update team status and features.txt
- `a1afa64` Sun 03:50 · Ishaan Ranjan · Update Ishaan status in CLAUDE.md
- `8eed1f6` Sun 03:49 · jamesEmerson112 · Update team status and features.txt
- `dd9901b` Sun 03:39 · jamesEmerson112 · Update team status and features.txt
- `ece766c` Sun 03:09 · jamesEmerson112 · Update team status in CLAUDE.md
- `9405050` Sun 02:59 · jamesEmerson112 · Update team status and features.txt
- `cec6fc3` Sun 02:58 · Kent Gang · Update Kent status in CLAUDE.md
- `39a4748` Sun 02:51 · kgang · Merge pull request #61 from jamesEmerson112/doctor/tracker-58
- `4b494a3` Sun 02:50 · Kent Gang · Fleet Doctor tracker: gate requires held-out verify (#58) merged
- `b30d09f` Sun 02:50 · kgang · Romulus: gate requires a held-out verify for code candidates (#58)

**Branches on GitHub:** `main`; the notice PR branches `claudemd-notice/arm-teleop-recording` (#47), `claudemd-notice/demo` (#48), `claudemd-notice/firmware-harness-connection` (#49), `claudemd-notice/firmware` (#50), `claudemd-notice/local-model-serve` (#51); `full-test-2` (Olir, 3 commits ahead of main, v1.8 near-hold heal test, PR #62 open; #63 merges main back into it and has conflicts); `full-test-3` (Olir, same commit as `full-test-2`); `james/arm-sim-ydcsss` (James's session, 23 commits ahead of main: job board, healer Levels 2 and 3a, soak runner, worker/manager design and loop (WIP), workerbench, Mission Control, no PR); `firmware-harness-connection` (no commits of its own yet); `cloud/m8-coder-wip` (1 commit, "not for merge"); merged and still on GitHub: `doctor/repro-seal` (#57), `james/arm-sim` (#55), `arm/teleop-recording` (Andre, merged into main through #55), `olir/arm-ultrasonic-heal` (#54), `local-model-serve` (#43), `ishaan/pod-agent-loop` (#41), `ishaan/device-healer` (#31), `firmware` and `demo` (Olir)
<!-- team-status:end -->
