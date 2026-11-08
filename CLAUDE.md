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
_Kent's session refreshes this every 10 min · Last synced: Sat Nov 7, 8:50 PM PST · write notes outside the markers_

- **Pitch:** we sell uptime: one heal loop at arm, fleet and company scale. Rivals only diagnose ([Alloy](https://theaiinsider.tech/2026/08/13/alloy-robotics-raises-8m-in-new-funding-to-help-engineers-debug-robot-fleets-with-ai-agents/)) or recommend fixes ([Formant F3](https://www.businesswire.com/news/home/20250630008190/en/Formant-F3-Brings-Generative-AI-and-Agentic-Reasoning-to-Robot-Ops)). We close the loop: "PagerDuty with hands" *(inferred)*.
- **Rule:** no LLM in the real-time control loop. Models propose reversible fixes, and the gate checks them: replay → simulated canary → automatic promote or rollback. People only touch the kill switch (D6, D11).
- **Rule:** the healer may patch the safety layer and the visor (D5a). Those changes are flagged `touches_safety` and always go to the simulated canary first (K17).
- **Rule:** each action type earns autonomy and loses it after a bad fix. The overnight unattended run is the proof *(inferred)*.
- **Demo:** cover the camera or push a bad policy → arm parks or rolls back → root cause on the timeline → AgentMail incident report → hardware fault? Mobilerun orders the part.
- **Research:** pin LeRobot v0.7.0, since it breaks the API ([release](https://github.com/huggingface/lerobot/releases/tag/v0.7.0)).
- **Research:** the SO-101 sim twin is `robotstudio_so101` in MuJoCo Menagerie, and it runs on a Mac ([menagerie](https://github.com/google-deepmind/mujoco_menagerie)).
- **Research:** bring a router, Ethernet and a backup video, because venue Wi-Fi sinks demos ([Nailing the Demo](https://generalrobots.substack.com/p/nailing-the-demo)). Everything else is in [`docs/cloud-platform-planning/README.md`](docs/cloud-platform-planning/README.md).
- **Now:** building the Fleet Doctor in three open PRs, all off by default: #34 (L1 `POST /v1/devices/{id}/doctor/ask`), #32 (device reference code: trigger hook, proposal applier, mark-good) and #33 (hypothesis ledger on the timeline, metrics in the digest). Next: P0 Stripe direction with James · P1 live Naïve card · P2 overnight launch by 12:30 AM.
- **Done:** M0 #1–#4 · M1 #5, #8 · M2 #9 · M3 #7, #12, #18 · M4 #10 · M5 #11, #15 · M6 #14 · M7 #16 · M8a #19 · M8b #28 · B8 toolchain #24 · edge bridge #25 · docs #21 · Fleet Doctor plan #22, decisions #23, contracts #26, foundation #27, LLM helper #29, L3 contract draft #30 · triage #6 · lint #13 · device kinds #17 · decisions D0–D28 locked (D27 deferred).
- **Blocked:** only on the K17 safety and visor path list, which also gates the Fleet Doctor. B4 is clear (the live card waits only on Kent's go-ahead, #21), and B1 cleared when Ishaan's device code merged as #31 (`device/`, `harness/`, `bench/`).
- **Asks:** Ishaan: review the contracts (I0: B2, B3) and send the K17 safety and visor paths. James: decide the Stripe direction with Kent (bill the robot's owner, or pay the supplier). Owed to James: a Naïve key with Board scopes.
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
_Last updated: Sat Nov 7, 8:48 PM PST_

**Current focus:** a robotic-arm company. Naïve runs the business and the job board; Hermes on the bench drives the arm through a plugin; an LLM operates the arm, with RunPod for anything power- or VRAM-heavy. Kent merged M8b (#28) at 8:39 PM and opened three Fleet Doctor PRs (#32 device reference, #33 timeline ledger, #34 L1 ask) at 8:39–8:41 PM; `doctor/l2-consult` is pushed with no PR yet. Ishaan is on the pod healer agent loop and blind eval; Andre is calibrating the arm on `arm/teleop-recording` (no PR yet).

**Recently done:**
- Kent merged #28 (`72e54c6`, 8:39 PM): M8b wires the coding agent into batch and the gate (F11): `services/code_lockin.py`, a coder job in `services/jobs.py`, a host worker (`compose.host-worker.yaml` replaces `compose.coder.yaml`) and a 525-line F11 flow test
- Kent opened #32, #33 and #34 (Fleet Doctor PR 5 device reference in `fake_device`, PR 6 hypothesis ledger + digest metrics, PR 3 `POST /v1/devices/{id}/doctor/ask`); all three are open, not merged
- Kent merged #30 (`ab18e33`, 8:30 PM): the typed L3 device-tool contract in `packages/contracts/tools.py` (8 read tools, 5 act tools, no raw shell). Not wired yet; D27 stays deferred
- Ishaan merged #31 (`6c3b006`, 8:28 PM): `device/` (Pi app, supervisor tiers 1–3, fake arm and sensor, 18 tests), healer harnesses h1–h3 and the benchmark. Nothing has run on the Pi or the real arm yet

**Open questions:**
- Kent's only blocker is the K17 safety and visor path list from Ishaan (it also gates the Fleet Doctor, D25), plus his own go-ahead for the live Naïve card. Is the Jetson link still blocked? Ishaan's block is from 8:12 PM, and Kent's (8:40 PM) still lists #28 as open
- #31 flags two clashes: the ultrasonic is on Olir's pod in mm while `robot/` still reads cm from Pi GPIO, and the arm's servo bus and `podd` both default to `/dev/ttyACM0`. Who picks the `/dev/serial/by-id/` paths and the one ultrasonic source?
- Arm prototypes for tomorrow have no owner in a box section yet, and they need to be ready before Kent's 9–10 AM "real arm + polish" slot
- Team calls still open: Stripe direction (bill the robot owner or pay the supplier), Monid's job, and owners for the unclaimed lines (fleet-ops, Mobilerun key, Stripe payment, AgentMail email) *(James's session)*. Should pi drive the arm at all?

**Recent commits (all authors):**
- `72e54c6` Sat 20:39 · kgang · Romulus M8b: coding agent wired into batch and gate (F11) (#28)
- `90f284a` Sat 20:39 · jamesEmerson112 · Update team status and features.txt
- `8440ab0` Sat 20:38 · Kent Gang · Update Kent status in CLAUDE.md
- `023f30d` Sat 20:37 · Ishaan Ranjan · features.txt: Ishaan claims the pod healer agent loop + blind eval
- `ab18e33` Sat 20:30 · kgang · Fleet Doctor L3: typed device-tool contract (draft, not wired; PR 8, D27 deferred) (#30)
- `3bd1ea7` Sat 20:29 · Kent Gang · Update Kent status in CLAUDE.md
- `9690424` Sat 20:29 · jamesEmerson112 · Update team status and features.txt
- `44df18d` Sat 20:29 · Kent Gang · Update Kent status in CLAUDE.md
- `6c3b006` Sat 20:28 · Ishaan Ranjan · Device code (B1), healer harness h1-h3 and benchmark (#31)
- `c53d301` Sat 20:25 · Kent Gang · features.txt: edge bridge (#25) finished

**Branches on GitHub:** `main`; open PR branches `doctor/device-ref` (#32), `doctor/timeline` (#33), `doctor/l1-ask` (#34) (all Kent); `doctor/l2-consult` (Kent, no PR); `cloud/m8b-wiring` (Kent, merged as #28); `doctor/l3-contract` (Kent, merged as #30); `ishaan/device-healer` (Ishaan, merged as #31); `arm/teleop-recording` (Andre, no PR); `cloud/m8-coder-wip` (1 commit, "not for merge"); `firmware` and `demo` (Olir, both fully on `main`)
<!-- team-status:end -->
