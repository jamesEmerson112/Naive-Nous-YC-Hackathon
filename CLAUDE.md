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
_Kent's session refreshes this every 10 min · Last synced: Sun Nov 8, 7:32 AM PST · write notes outside the markers_

- **Pitch:** we sell uptime: one heal loop at arm, fleet and company scale. Rivals only diagnose ([Alloy](https://theaiinsider.tech/2026/08/13/alloy-robotics-raises-8m-in-new-funding-to-help-engineers-debug-robot-fleets-with-ai-agents/)) or recommend fixes ([Formant F3](https://www.businesswire.com/news/home/20250630008190/en/Formant-F3-Brings-Generative-AI-and-Agentic-Reasoning-to-Robot-Ops)). We close the loop: "PagerDuty with hands" *(inferred)*.
- **Rule:** no LLM in the real-time control loop. Models propose reversible fixes, and the gate checks them: replay → simulated canary → automatic promote or rollback. People only touch the kill switch (D6, D11).
- **Rule:** the healer may patch the safety layer and the visor (D5a). Those changes are flagged `touches_safety` and always go to the simulated canary first (K17).
- **Rule:** each action type earns autonomy and loses it after a bad fix. The overnight unattended run is the proof *(inferred)*.
- **Demo:** cover the camera or push a bad policy → arm parks or rolls back → root cause on the timeline → AgentMail incident report → hardware fault? Mobilerun orders the part.
- **Research:** pin LeRobot v0.7.0, since it breaks the API ([release](https://github.com/huggingface/lerobot/releases/tag/v0.7.0)).
- **Research:** the SO-101 sim twin is `robotstudio_so101` in MuJoCo Menagerie, and it runs on a Mac ([menagerie](https://github.com/google-deepmind/mujoco_menagerie)).
- **Research:** bring a router, Ethernet and a backup video, because venue Wi-Fi sinks demos ([Nailing the Demo](https://generalrobots.substack.com/p/nailing-the-demo)). Everything else is in [`docs/cloud-platform-planning/README.md`](docs/cloud-platform-planning/README.md).
- **Now:** sprint proposal **Naïve Technician** ([`docs/naive-technician/`](docs/naive-technician/README.md)): when the healer gives up on a hardware fault, a Naïve agent returns a typed report and Romulus's gate emails the technician and builds a cart link a person approves. Nothing built yet; G1 is at 8:30 (one live session, valid report in ≤ 5 min and ≤ $3). The real-arm run with the team is 7–10 AM.
- **Done:** Naïve Technician plan `eafc667`, `8f25900`, `c5a1fa1` · M0 #1–#4 · M1 #5, #8 · M2 #9 · M3 #7, #12, #18 · M4 #10 · M5 #11, #15 · M6 #14 · M7 #16 · M8a #19 · M8b #28 · real device code #38 · replay hardening #53 · B5 sandbox #46 · held-out verify #58 · B8 toolchain #24 · edge bridge #25 · docs #21, #45 · Fleet Doctor #22, #23, #26, #27, #29, #30, #32–#35, #37, #39, #42, #44, #52, #57, #59–#61 · triage #6 · lint #13, #40 · device kinds #17 · decisions D0–D29 locked (D27 deferred; D30 drafted).
- **Blocked:** Naïve Technician waits on Kent's four calls (D30 purchase approval, servo variant, technician teammate, OK for live Naïve writes; README). Fleet Doctor still waits on the K17 safety and visor path list. B1 and B4 are clear.
- **Asks:** Andre: is our STS3215 the C001 7.4 V or the C018 12 V? Team: who'll play the technician (email, plus phone for T1)? Ishaan: K17 safety and visor paths and the contracts review (B2, B3).
<!-- romulus-sync:end -->

### Device-side healer (Ishaan)
<!-- ishaan-sync:start -->
_Ishaan's local Claude session refreshes this block about every hour. Last synced: Sun Nov 8, 6:19 AM PST._
- **Now:** pod healer loop handed to Olir's session for bench runs (`firmware/harness/HANDOFF.md`, `8d9a30b`); session "Build the pod healer agent loop and blind eval" stood down at 21:47 with no bench run. Not on the bench yet. The Jetson link is still paused (no USB data or LAN path, session "Identify the connected Jetson").
- **Just finished:** pod healer agent loop and blind eval merged as #41 (`4ee3517`): `agent.py` with Claude and Ollama drivers and a risk gate, `evaluate.py`, tests, README, HANDOFF.md (`72f53c8`); pointed Olir's session to the handoff in CLAUDE.md (`8d9a30b`). Earlier: device code and healer harness #31 (`6c3b006`).
- **Blocked on:** bench runs wait on Olir (SSH to the pod, API key, per the session recap). Still owes Kent the K17 safety and visor path list and the contracts review (B2, B3), per Kent's block.
- **Branch and files:** main checkout on `main` at `a1afa64`, clean, no uncommitted files. `ishaan/pod-agent-loop` (worktree `naive-pod-loop`) is merged.
<!-- ishaan-sync:end -->

**Pod healer agent loop, for Olir's session:** running or benchmarking it on the bench (`firmware/harness/`, merged as #41)? Start with [`firmware/harness/HANDOFF.md`](firmware/harness/HANDOFF.md): bench steps, what's verified, what to report.

## Team status
<!-- team-status:start -->
## Team status
_Last updated: Sun Nov 8, 7:33 AM PST_

**Current focus:** a robotic-arm company. Naïve runs the business and the job board; Hermes on the bench drives the arm through a plugin; an LLM operates the arm, with RunPod for anything power- or VRAM-heavy. P3 is on: the team is on the real arm 7–10 AM (Pi with Andre: pin serial ports, teach Andre's four poses, `demo/up.sh --real`, per James's [`MORNING.md`](prototypes/arm-sim/MORNING.md)), with the 10 AM code freeze and 11 AM stop ahead (`features.txt`, `hub/src/content/proposal.ts`). Kent pushed a new sprint proposal at 7:18–7:21 AM, the **Naïve Technician** (`docs/naive-technician/`): when Romulus hits a hardware fault with no catalogued part, a Naïve agent writes a typed diagnostic report, Romulus's gate emails a technician and builds a price-checked cart link a person approves. Status per its README: proposal, nothing built yet. James's session on `james/arm-sim-ydcsss` last pushed at 5:15 AM (`ff1c20d`).

**Recently done:**
- Kent pushed the **Naïve Technician plan** (`eafc667`, `8f25900`, `c5a1fa1`): `README.md` (the loop and four decisions for Kent: D30 human approves the purchase, which STS3215 variant the arm has, which teammate plays the technician, OK for the live Naïve writes), `design.md` (trigger, report schema, gate, data model, API, tests), `build-plan.md` (tiers T0 email + cart, T1 Twilio call, T2 polish; gates G1 8:30, G2, G3; lanes A–D, arm needed for about 10 min near 9:30) and `demo-clip.md` (the 10–15 s video beat). The README links a `review.md` that isn't in the repo yet
- Olir merged **#64** (`full-test-3`, 5:16 AM) and **#62** (`full-test-2`): heal-agent run reports go to main. `tools/edge_bridge/.../agent_runs.py` turns a harness run into an `AgentRunReport` (`PUT /v1/devices/{id}/agent-runs/{run_id}`, table `agent_runs`, APIs doc §3.11), the Romulus dashboard gains a run page, and `naive/` checks the Naïve account read-only with the official TypeScript SDK. His `84705f9` note: read what an agent did from `agent_runs`, test with `edge_bridge.fake_healer`, never `make reset` the laptop cloud
- James's session pushed **Mission Control UI round 1 and the redo loop** (5:15 AM, `ff1c20d` on `james/arm-sim-ydcsss`, not on main): three looks, a real-arm twin, a four-step story banner and a plain-English feed; `armsim/redo.py` handles a wrong action (`grasp_missed`) with at most 2 bounded redos before a person
- James's session's **worker-model bench on one RunPod GPU** (`workerbench/runpod_wb.py`, `RESULTS.md`, interim): `qwen3.5:4b` got 0.579 gap recall and 4 of 5 real alarms but 25 of 60 healthy false alarms; `lfm3:1.7b` got 0.426 and 0 of 5. Neither passes the gates yet

**Open questions:**
- Naïve Technician: Kent's four decisions (D30, servo variant C001 7.4 V or C018 12 V, the technician teammate, the live Naïve writes) are open per its README, and Kent's 7:30 block now asks Andre which STS3215 variant the arm has and the team who plays the technician (email, plus phone for T1); G1 at 8:30 needs one live session that returns a valid report in ≤ 5 min and ≤ $3. Its trigger rests on a catalogue gap: real devices send `joint_stall` and `us_no_echo` while `cloud/config/parts.yaml` lists `servo_absent` and `ultrasonic_dead` (`docs/naive-technician/README.md`)
- Fleet Doctor follow-ups (#52): `SAFETY_PATHS_CONFIRMED` stays false until Ishaan sends the K17 safety and visor path list; `DOCTOR_USD_PER_1K_TOKENS=0.001` is a placeholder. Olir's note points Fleet Doctor evidence at `agent_runs` (`cloud/CLAUDE.md`)
- Frozen ultrasonic: `firmware/healer/detect.py` still has no frozen or `out_of_range` ultrasonic rule; the doc's cheaper fix is porting `acc_frozen`'s zero-variance test to `us` (`docs/healer-data-levels.md`). Who drives the real arm (Andre's `arm/prims/`, Olir's `arm/safe_replay.py`, `RealArm` in `prototypes/arm-sim/`) is still to settle with Olir and Andre *(team status)*
- Team calls still open: Stripe is optional; Monid's job; owners for the unclaimed lines (fleet-ops, Mobilerun key, Stripe payment, AgentMail email); live vs fake work orders for the demo. #63 (main → `full-test-2`) is still open though `full-test-2` is merged *(inferred: can be closed)*

**Recent commits (all authors):**
- `5657970` Sun 07:30 · Kent Gang · Update Kent status in CLAUDE.md
- `c584d28` Sun 07:30 · jamesEmerson112 · Update team status and features.txt
- `c5a1fa1` Sun 07:21 · Kent Gang · a couple more naive technician planning docs
- `8f25900` Sun 07:19 · Kent Gang · naive technician design doc
- `eafc667` Sun 07:18 · Kent Gang · spec for naive technician
- `76fe8d8` Sun 06:29 · jamesEmerson112 · Update team status in CLAUDE.md
- `c5250b9` Sun 06:19 · Ishaan Ranjan · Update Ishaan status in CLAUDE.md
- `8fb8ec4` Sun 05:19 · jamesEmerson112 · Update team status and features.txt
- `695c901` Sun 05:16 · Oliravan Eswaramoorthy · Merge pull request #64 from jamesEmerson112/full-test-3
- `8059bef` Sun 05:15 · Oliravan Eswaramoorthy · Merge branch 'main' into full-test-3

**Branches on GitHub:** `main`; the notice PR branches `claudemd-notice/arm-teleop-recording` (#47), `claudemd-notice/demo` (#48), `claudemd-notice/firmware-harness-connection` (#49), `claudemd-notice/firmware` (#50), `claudemd-notice/local-model-serve` (#51); `james/arm-sim-ydcsss` (James's session, 23 commits ahead of main: job board, healer Levels 2 and 3a, soak runner, worker/manager loop, redo loop, workerbench, Mission Control, no PR); `firmware-harness-connection` (no commits of its own yet); `cloud/m8-coder-wip` (1 commit, "not for merge"); merged and still on GitHub: `full-test-3` (#64), `full-test-2` (#62; #63 still open), `doctor/repro-seal` (#57), `james/arm-sim` (#55), `arm/teleop-recording` (Andre, merged into main through #55), `olir/arm-ultrasonic-heal` (#54), `local-model-serve` (#43), `ishaan/pod-agent-loop` (#41), `ishaan/device-healer` (#31), `firmware` and `demo` (Olir)
<!-- team-status:end -->
