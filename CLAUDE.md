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
_Kent's session refreshes this every 10 min · Last synced: Sat Nov 7, 8:10 PM PST · write notes outside the markers_

- **Pitch:** we sell uptime: one heal loop at arm, fleet and company scale. Rivals only diagnose ([Alloy](https://theaiinsider.tech/2026/08/13/alloy-robotics-raises-8m-in-new-funding-to-help-engineers-debug-robot-fleets-with-ai-agents/)) or recommend fixes ([Formant F3](https://www.businesswire.com/news/home/20250630008190/en/Formant-F3-Brings-Generative-AI-and-Agentic-Reasoning-to-Robot-Ops)). We close the loop: "PagerDuty with hands" *(inferred)*.
- **Rule:** no LLM in the real-time control loop. Models propose reversible fixes, and the gate checks them: replay → simulated canary → automatic promote or rollback. People only touch the kill switch (D6, D11).
- **Rule:** the healer may patch the safety layer and the visor (D5a). Those changes are flagged `touches_safety` and always go to the simulated canary first (K17).
- **Rule:** each action type earns autonomy and loses it after a bad fix. The overnight unattended run is the proof *(inferred)*.
- **Demo:** cover the camera or push a bad policy → arm parks or rolls back → root cause on the timeline → AgentMail incident report → hardware fault? Mobilerun orders the part.
- **Research:** pin LeRobot v0.7.0, since it breaks the API ([release](https://github.com/huggingface/lerobot/releases/tag/v0.7.0)).
- **Research:** the SO-101 sim twin is `robotstudio_so101` in MuJoCo Menagerie, and it runs on a Mac ([menagerie](https://github.com/google-deepmind/mujoco_menagerie)).
- **Research:** bring a router, Ethernet and a backup video, because venue Wi-Fi sinks demos ([Nailing the Demo](https://generalrobots.substack.com/p/nailing-the-demo)). Everything else is in [`docs/cloud-platform-planning/README.md`](docs/cloud-platform-planning/README.md).
- **Now:** five PRs are open. #28 (M8b) wires the coding agent into the batch and gate (F11). #25 is the edge bridge: Olir's pod healer reports to the cloud with no device changes (D29). #26 and #27 are the Fleet Doctor contracts and foundation, behind `doctor_mode=off`. #24 puts the STM32 toolchain in the sandbox (B8). Coming up: the live Naïve card on Kent's go-ahead, and the 12:30 AM go/no-go.
- **Done:** M0 #1–#4 · M1 #5, #8 · M2 #9 · M3 #7, #12, #18 · M4 #10 · M5 #11, #15 · M6 #14 · M7 #16 · M8a #19 · docs #21 · Fleet Doctor plan #22 and decisions #23 · triage #6 · lint #13 · device kinds #17 · decisions D0–D28 locked (D27 deferred).
- **Blocked:** not on B4: the team key reaches the Naïve proxy, and the live card waits only on Kent's go-ahead (#21). Still open: B1 (device code in the repo; Ishaan is held up by the Jetson link) and the K17 path list, which also gates the Fleet Doctor. B8 closes with #24.
- **Asks:** Ishaan: land `device/` (healer pick is qwen3.5:4b with thinking on h3, [benchmark](docs/healer-benchmark-progress.md)), review contracts #3 (B2, B3), send the K17 safety and visor paths. Until those paths arrive, the Fleet Doctor is blocked too (D25). Owed to James: a Naïve key with Board scopes.
<!-- romulus-sync:end -->

### Device-side healer (Ishaan)
<!-- ishaan-sync:start -->
_Ishaan's local Claude session refreshes this block about every hour. Last synced: Sat Nov 7, 8:12 PM PST._
- **Now:** getting the Jetson edge box talking to the Mac. It is in the right USB-C port and the Mac saw the replug at 7:45 PM, but no USB device has come up and no prompt shows; either Linux with USB device mode isn't up on the Jetson or the cable carries no data, and there is no LAN path yet (session "Identify the connected Jetson", 20:10). The device code (B1) is still split into `device/`, `harness/` and `bench/` in his checkout and not pushed yet.
- **Just finished:** healer benchmark progress doc with final results and the model pick, qwen3.5:4b with thinking on, harness h3 (`835c4f0`). Device test suite: 18 tests pass, and each planted fault turns it red; oracle 6/6 and baseline 0/6 still hold (test-bed session recap). Earlier: model-selection method (`b9b0f89`), `features.txt` claims (`cd54c16`, `e59093f`), device-side answers (`5cea9c2`).
- **Blocked on:** the Jetson link (no USB data, no LAN). Owes Kent the safety and visor path list (K17), the `device/` landing (B1), a review of the code-package and `code_patch` formats in PR #3 (B2, B3), and a co-review of Fleet Doctor D23–D27, per Kent's block.
- **Branch and files:** `main`. Untracked in his checkout: `device/` (README, pytest.ini, `tests/test_spec.py`, `tests/test_classify.py`, conftest), `harness/`, `bench/` (`run_bench.py`, `tasks.py`, README).
<!-- ishaan-sync:end -->

## Team status
<!-- team-status:start -->
_Last updated: Sat Nov 7, 8:10 PM PST_

**Current focus:** a robotic-arm company. Naïve runs the business and the job board; Hermes on the bench drives the arm through a plugin; an LLM operates the arm, with RunPod for anything power- or VRAM-heavy. Kent has five PRs open since 7:58 PM and none merged yet: B8 STM32 toolchain (#24), the edge bridge (#25), Fleet Doctor contracts (#26) and foundation (#27), and the M8b coding agent wired into batch and gate (#28). James paused after publishing Stack Map v7 and plans arm prototypes tonight without the real arm (MuJoCo twin, Kent's fake device, Olir's sensor pod), with the red follower arm plugged in tomorrow *(James's session)*. Andre is calibrating the arm; Ishaan is still blocked on the Jetson link, and the device code (B1) is not pushed.

**Recently done:**
- Kent opened #24 (`cloud/b8-stm32-toolchain`, 7:58 PM): the coder image gets the STM32 toolchain from `firmware/toolkit/setup.sh`, plus an offline firmware build check and its tests. This should close blocker B8 once merged *(inferred)*
- Kent opened #25 (`edge/bridge`, 8:00 PM): `tools/edge_bridge` reports the pod healer's records (podd logs, heal tools) to the cloud with no device-side changes; it adds an interop test against the real app and decision D29
- Kent opened #26 (7:59 PM) and #27 (8:03 PM): Fleet Doctor contracts (ask, advice, proposal, proposal result; no version bump per D28), then the foundation (settings, tables, domain rules, jobs seam, `Llm.complete`, stubs)
- Kent opened #28 (`cloud/m8b-wiring`, 8:04 PM): Romulus M8b wires the coding agent into batch and gate, with an F11 code-patch flow test. James's session published Stack Map v7 with everyone's status as of 8:00 PM

**Open questions:**
- Kent still waits on: device code in the repo (B1; Ishaan is blocked on the Jetson link: macOS's USB accessory check blocks its data and there is no LAN path), the K17 safety and visor path list, and Kent's go-ahead for the live Naïve card
- Nobody owns the pod healer's agent loop and blind scenario eval yet; `HARNESS.md` marks both "you build this". Is that Ishaan's device healer, a Hermes plugin, or new work? Fleet Doctor's L3 agent loop location (D27) is deferred too
- Arm prototypes for tomorrow (control program with a servo error-bit watchdog, approach-then-grasp with the ultrasonic, predicted-vs-measured check, frozen-ultrasonic rule, labelled run logger) have no `features.txt` lines or owners yet, and they need to be ready before Kent's 9–10 AM "real arm + polish" slot. James's overview fixes `79f938b` and `952cf79` are committed locally but not on `main` *(James's session, inferred)*
- Team calls still open: Stripe direction (bill the robot owner or pay the supplier), Monid's job, and owners for the unclaimed lines (fleet-ops, Mobilerun key, Stripe payment, AgentMail email) *(James's session)*. Is the ultrasonic read through the STM32 pod or Pi GPIO? Should pi drive the arm at all?

**Recent commits (all authors):**
- `b11f910` Sat 19:59 · jamesEmerson112 · Update team status in CLAUDE.md
- `02ffd52` Sat 19:58 · Kent Gang · Update Kent status in CLAUDE.md
- `39dd2e5` Sat 19:54 · kgang · Romulus docs: build status, flows tracker, spike results (#21)
- `fbeb4bb` Sat 19:49 · jamesEmerson112 · Update team status in CLAUDE.md
- `563c34d` Sat 19:48 · Kent Gang · Update Kent status in CLAUDE.md
- `071f91e` Sat 19:48 · Kent Gang · Claim Fleet Doctor build lines in features.txt
- `78fe531` Sat 19:48 · kgang · Fleet Doctor: log decisions D23–D28; optional contract additions don't bump the version (#23)
- `7bcd632` Sat 19:46 · kgang · Fleet Doctor: implementation plan and interfaces (where each route, model, table, setting and seam lives) (#22)
- `b2391ef` Sat 19:45 · Kent Gang · Claim the bench-to-cloud bridge and the pod's incident outbox in features.txt
- `2486475` Sat 19:42 · Ishaan Ranjan · Update Ishaan status in CLAUDE.md

**Branches on GitHub:** `main`; Kent's open PR branches `cloud/b8-stm32-toolchain` (#24), `edge/bridge` (#25), `doctor/contracts` (#26), `doctor/foundation` (#27), `cloud/m8b-wiring` (#28); `cloud/m8-coder-wip` (1 commit, "not for merge"); `firmware` and `demo` (Olir, both fully on `main`). `cloud/docs-status` is gone after #21 merged
<!-- team-status:end -->
