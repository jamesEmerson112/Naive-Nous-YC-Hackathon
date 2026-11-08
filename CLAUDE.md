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
_Last updated: Sun Nov 8, 3:50 AM PST_

**Current focus:** a robotic-arm company. Naïve runs the business and the job board; Hermes on the bench drives the arm through a plugin; an LLM operates the arm, with RunPod for anything power- or VRAM-heavy. P2 overnight runs to 7 AM, where Olir's pod logs under podd are the only real data (Kent's run is the fake fleet), then P3 the real arm 7–10 AM (`features.txt`, `hub/src/content/proposal.ts`). James's session on `james/arm-sim-ydcsss` has written the worker/manager healer design (3:45 AM) and is finishing the overnight soak runner and the Mission Control page (WIP); next is the Pi with Andre at 7 AM (pin serial ports, teach Andre's four poses, `demo/up.sh --real`). Kent merged #58 at 2:50 AM, so Romulus has no open PRs; next are the overnight go/no-go and the live Naïve card. Olir is on `full-test-2` (3:29 AM, `5eef70f`): the arm heal test on the v1.8 near-hold bad image, then a laptop-side Romulus cloud the Pi's edge bridge reports to over Ethernet.

**Recently done:**
- James's session pushed the **worker/manager healer design** (3:45 AM, `8a55f7c` on `james/arm-sim-ydcsss`, not on main): plain code (level-1 rules, L3a warn-only) watches every tick and parks the arm at once; a worker beside the arm host has the laptop's qwen3.5:4b write the escalation, asks one Hermes `manager` profile (gpt-6-sol, no tools, triage parent + 1–3 `delegate_task` analysts + a critic) over `POST /v1/runs` for one fix plan, checks it in code and runs that one action; if the manager fails, today's scripted heal runs. Naïve keeps the incident card but doesn't host the manager tonight (hosted Hermes `runnable: false`); the worker skips Romulus `doctor/ask`, whose contract has no action field (Kent's lane). Four research reports in `docs/research/` (`docs/worker-manager-healer.md`)
- James's session's **soak runner** (3:46 AM, `be40831`, WIP): an order every 10 minutes, about half with an unannounced fault; a Linux sim test went 4 of 4 (`us_frozen` healed by a sensor restart in 22 s, `pod_image07` by the pod's rollback in 40 s). Known gap: a pick that fails mid-grip drops the block for the rest of the night, since the host has no `reset_scene` yet (`prototypes/arm-sim/SOAK.md`)
- Olir pushed the **v1.8 near-hold heal test** to `full-test-2` (3:29 AM, `5eef70f`, not on main): bad image `08-us-near-hold` keeps a reading under 250 mm after a hand leaves, so the arm's reflex flails; podd's `us_mismatch` rule flags it, the healer agent (qwen3.5:4b) restores known-good firmware, and the arm proves it over three loops (`arm/README.md`, `arm/tests/test_near_hold.py`)
- James's session ran **healer Level 2** on a Runpod L40S (~$0.11, pods terminated) and trained **Level 3a** (0 of 74 healthy runs flagged, every planted fault caught, sim-only) (`6d9df92`, `e7b4062`, `docs/healer-data-levels.md`); Kent merged **#58** at 2:50 AM (`b30d09f`): the gate keeps a code candidate with no held-out verify waiting in `replaying`

**Open questions:**
- Fleet Doctor follow-ups (#52): B5 has merged, so `DOCTOR_DRAFT=coder` on real device text is no longer waiting on #46 *(inferred: Kent hasn't said it's switched on)*; `SAFETY_PATHS_CONFIRMED` stays false until Ishaan sends the K17 safety and visor path list; `DOCTOR_USD_PER_1K_TOKENS=0.001` is a placeholder. The worker/manager design also leaves extending `DoctorAdvice` with an action field to Kent (`docs/worker-manager-healer.md`)
- Frozen ultrasonic: `firmware/healer/detect.py` still has no frozen or `out_of_range` ultrasonic rule, and Level 2 (ChatTS) also misses a frozen trace without a healthy reference; the doc's cheaper fix is porting `acc_frozen`'s zero-variance test to `us` (`docs/healer-data-levels.md`, `docs/firmware-apis.md` section 6). `303a007` committed `arm/__pycache__/safe_replay.cpython-313.pyc` to main, and `5eef70f` updates it on `full-test-2` *(inferred: should be gitignored)*
- Who drives the real arm: main holds three real-arm drivers (Andre's `arm/prims/`, Olir's `arm/safe_replay.py` from #36, and `RealArm` in `prototypes/arm-sim/` on top of Andre's prims), plus the cm-vs-mm ultrasonic and shared `/dev/ttyACM0` clash from #31. James's session proposes the arm host runs the job (Naïve card → Hermes) and reports to Romulus, with Olir's pod healer fixing only the pod; still to settle with Olir and Andre *(team status)*
- Team calls still open: Stripe is optional, only if we're ahead; Monid's job; owners for the unclaimed lines (fleet-ops, Mobilerun key, Stripe payment, AgentMail email); live vs fake work orders for the demo (live texts a teammate's phone)

**Recent commits (all authors):**
- `dd9901b` Sun 03:39 · jamesEmerson112 · Update team status and features.txt
- `ece766c` Sun 03:09 · jamesEmerson112 · Update team status in CLAUDE.md
- `9405050` Sun 02:59 · jamesEmerson112 · Update team status and features.txt
- `cec6fc3` Sun 02:58 · Kent Gang · Update Kent status in CLAUDE.md
- `39a4748` Sun 02:51 · kgang · Merge pull request #61 from jamesEmerson112/doctor/tracker-58
- `4b494a3` Sun 02:50 · Kent Gang · Fleet Doctor tracker: gate requires held-out verify (#58) merged
- `b30d09f` Sun 02:50 · kgang · Romulus: gate requires a held-out verify for code candidates (#58)
- `0060560` Sun 02:49 · jamesEmerson112 · Update team status in CLAUDE.md
- `261ec71` Sun 02:48 · Kent Gang · Update Kent status in CLAUDE.md
- `5d5eaf0` Sun 02:47 · kgang · Merge pull request #60 from jamesEmerson112/doctor/tracker-b5

**Branches on GitHub:** `main`; the notice PR branches `claudemd-notice/arm-teleop-recording` (#47), `claudemd-notice/demo` (#48), `claudemd-notice/firmware-harness-connection` (#49), `claudemd-notice/firmware` (#50), `claudemd-notice/local-model-serve` (#51); `full-test-2` (Olir, 3 commits ahead of main, v1.8 near-hold heal test, no PR); `james/arm-sim-ydcsss` (James's session: job board, healer Levels 2 and 3a, soak runner, worker/manager design, no PR); `firmware-harness-connection` (no commits of its own yet); `cloud/m8-coder-wip` (1 commit, "not for merge"); merged and still on GitHub: `cloud/gate-require-verify` (#58), `doctor/repro-seal` (#57), `james/arm-sim` (#55), `arm/teleop-recording` (Andre, merged into main through #55), `olir/arm-ultrasonic-heal` (#54), `local-model-serve` (#43), `ishaan/pod-agent-loop` (#41), `ishaan/device-healer` (#31), `firmware` and `demo` (Olir)
<!-- team-status:end -->
