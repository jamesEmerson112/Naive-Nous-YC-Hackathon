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
_Kent's session refreshes this every 10 min · Last synced: Sat Nov 7, 11:00 PM PST · write notes outside the markers_

- **Pitch:** we sell uptime: one heal loop at arm, fleet and company scale. Rivals only diagnose ([Alloy](https://theaiinsider.tech/2026/08/13/alloy-robotics-raises-8m-in-new-funding-to-help-engineers-debug-robot-fleets-with-ai-agents/)) or recommend fixes ([Formant F3](https://www.businesswire.com/news/home/20250630008190/en/Formant-F3-Brings-Generative-AI-and-Agentic-Reasoning-to-Robot-Ops)). We close the loop: "PagerDuty with hands" *(inferred)*.
- **Rule:** no LLM in the real-time control loop. Models propose reversible fixes, and the gate checks them: replay → simulated canary → automatic promote or rollback. People only touch the kill switch (D6, D11).
- **Rule:** the healer may patch the safety layer and the visor (D5a). Those changes are flagged `touches_safety` and always go to the simulated canary first (K17).
- **Rule:** each action type earns autonomy and loses it after a bad fix. The overnight unattended run is the proof *(inferred)*.
- **Demo:** cover the camera or push a bad policy → arm parks or rolls back → root cause on the timeline → AgentMail incident report → hardware fault? Mobilerun orders the part.
- **Research:** pin LeRobot v0.7.0, since it breaks the API ([release](https://github.com/huggingface/lerobot/releases/tag/v0.7.0)).
- **Research:** the SO-101 sim twin is `robotstudio_so101` in MuJoCo Menagerie, and it runs on a Mac ([menagerie](https://github.com/google-deepmind/mujoco_menagerie)).
- **Research:** bring a router, Ethernet and a backup video, because venue Wi-Fi sinks demos ([Nailing the Demo](https://generalrobots.substack.com/p/nailing-the-demo)). Everything else is in [`docs/cloud-platform-planning/README.md`](docs/cloud-platform-planning/README.md).
- **Now:** two PRs are open. #44 is Fleet Doctor PR 7, the last of its 9: real evidence, coder draft, sandbox check, consult traces. #46 (B5) limits the coding-agent sandbox to the LLM only, with the key held by a proxy. M8 is fully merged (#24, #28, #38). Next: P0 Stripe direction with James · P1 live Naïve card · P2 overnight launch by 12:30 AM.
- **Done:** M0 #1–#4 · M1 #5, #8 · M2 #9 · M3 #7, #12, #18 · M4 #10 · M5 #11, #15 · M6 #14 · M7 #16 · M8a #19 · M8b #28 · real device code #38 · B8 toolchain #24 · edge bridge #25 · docs #21, #45 · Fleet Doctor plan #22, decisions #23, contracts #26, foundation #27, LLM helper #29, L1 ask #34, L2 consult #35, device reference #32, held-overlay lock-in #39, F12 interop #42, ledger #33, L3 contract draft #30, tracker #37 · triage #6 · lint #13, #40 · device kinds #17 · decisions D0–D28 locked (D27 deferred).
- **Blocked:** only on the K17 safety and visor path list, which also gates the Fleet Doctor. B4 is clear (the live card waits only on Kent's go-ahead, #21), and B1 cleared when Ishaan's device code merged as #31 (`device/`, `harness/`, `bench/`).
- **Asks:** Ishaan: review the contracts (I0: B2, B3) and send the K17 safety and visor paths. James: decide the Stripe direction with Kent (bill the robot's owner, or pay the supplier). Owed to James: a Naïve key with Board scopes.
<!-- romulus-sync:end -->

### Device-side healer (Ishaan)
<!-- ishaan-sync:start -->
_Ishaan's local Claude session refreshes this block about every hour. Last synced: Sat Nov 7, 11:16 PM PST._
- **Now:** pod healer loop handed to Olir's session for bench runs (`firmware/harness/HANDOFF.md`, `8d9a30b`); session "Build the pod healer agent loop and blind eval" stood down at 21:47 with no bench run. Not on the bench yet. The Jetson link is still paused (no USB data or LAN path, session "Identify the connected Jetson").
- **Just finished:** pod healer agent loop and blind eval merged as #41 (`4ee3517`): `agent.py` with Claude and Ollama drivers and a risk gate, `evaluate.py`, tests, README, HANDOFF.md (`72f53c8`); pointed Olir's session to the handoff in CLAUDE.md (`8d9a30b`). Earlier: device code and healer harness #31 (`6c3b006`).
- **Blocked on:** bench runs wait on Olir (SSH to the pod, API key, per the session recap). Still owes Kent the K17 safety and visor path list and the contracts review (B2, B3), per Kent's block.
- **Branch and files:** main checkout on `main` at `4594267`, clean, no uncommitted files. `ishaan/pod-agent-loop` (worktree `naive-pod-loop`) is merged.
<!-- ishaan-sync:end -->

**Pod healer agent loop, for Olir's session:** running or benchmarking it on the bench (`firmware/harness/`, merged as #41)? Start with [`firmware/harness/HANDOFF.md`](firmware/harness/HANDOFF.md): bench steps, what's verified, what to report.

## Team status
<!-- team-status:start -->
## Team status
_Last updated: Sat Nov 7, 11:10 PM PST_

**Current focus:** a robotic-arm company. Naïve runs the business and the job board; Hermes on the bench drives the arm through a plugin; an LLM operates the arm, with RunPod for anything power- or VRAM-heavy. Kent has two PRs open: #44 (Fleet Doctor PR 7, the last of 9, with a review fix pushed at 10:58 PM) and #46 (B5, coder sandbox reaches only the LLM). James opened five `CLAUDE.md` notice PRs (#47–#51) asking stale branches to merge main. Olir has the pod healer bench runs and the bad-ultrasonic arm heal test. The overnight launch is due by 12:30 AM (Kent's plan).

**Recently done:**
- James opened #47–#51 (11:02 PM), one per stale branch: `arm/teleop-recording` (Andre, 59 behind), `demo` (200), `firmware-harness-connection` (32), `firmware` (201), `local-model-serve` (20, all Olir). Each PR changes only `CLAUDE.md`, replacing it with main's copy plus a notice listing tonight's overlaps; nothing went to main (James's session)
- Kent pushed a review fix to #44 (`6c04704`, 10:58 PM): the sandbox check now requires the repro test to fail on behaviour before the patch and pass after; a patch that edits existing tests fails without a sandbox run
- Kent opened #46 (`cloud/b5-egress`): `cloud/coder/llm_proxy.py` holds the real key and forwards only `POST /v1/chat/completions` with a per-job token; the agent runs on an `--internal` Docker network per run; no open-network mode (fail closed)
- James's P1 prototypes run in simulation (local branch `james/arm-sim`, not pushed): arm host, `so101-arm` Hermes plugin with a `pre_tool_call` guard, approach-then-grasp, a level-1 watchdog, a fault factory, a ChatTS level-2 test on RunPod (pod terminated). Sim only (James's session)

**Open questions:**
- Pod healer bench runs: the model runs on the laptop over Ethernet (#43), but nothing has been checked on the bench yet: `pi_check.py`, podd, SWD, flashing, the scenarios, the SSH `--target` path, or the `claude` driver against the live API (HANDOFF.md, Ishaan's block)
- Kent is still blocked only on the K17 safety and visor path list from Ishaan (it also gates the Fleet Doctor), plus his own go-ahead for the live Naïve card. Ishaan also still owes the contracts review (B2, B3)
- Overlaps the notice PRs flag: Andre's arm files exist twice (his branch and a flat `arm/` on main from #36, with a different `arm/README.md`); three real-arm drivers (Andre's `arm/prims/`, Olir's `arm/safe_replay.py`, James's `prototypes/arm-sim/`); the cm-vs-mm ultrasonic and shared `/dev/ttyACM0` clash from #31. Which driver wins? *(inferred)*
- Team calls still open: Stripe direction (bill the robot owner or pay the supplier), Monid's job, owners for the unclaimed lines (fleet-ops, Mobilerun key, Stripe payment, AgentMail email), and when `james/arm-sim` gets pushed for Kent's 9–10 AM "real arm + polish" slot

**Recent commits (all authors):**
- `7c734da` Sat 22:59 · jamesEmerson112 · Update team status and features.txt
- `b2dd790` Sat 22:58 · Kent Gang · Update Kent status in CLAUDE.md
- `1f043d6` Sat 22:38 · jamesEmerson112 · Update team status in CLAUDE.md
- `612ca01` Sat 22:38 · Kent Gang · Update Kent status in CLAUDE.md
- `5ce8590` Sat 22:29 · jamesEmerson112 · Update team status in CLAUDE.md
- `fba444e` Sat 22:29 · jamesEmerson112 · Update team status and features.txt
- `cd261a2` Sat 22:28 · Olir Eswar · Claim pod healer bench runs and the ultrasonic arm heal test in features.txt
- `ccb95fb` Sat 22:28 · kgang · Romulus docs: M8 status after #24, #28, #38 (#45)
- `625c345` Sat 22:28 · Kent Gang · Update Kent status in CLAUDE.md
- `c8b58cb` Sat 22:20 · kgang · Romulus B1: real device code in the coder path (#38)

**Branches on GitHub:** `main`; open PR branches `cloud/b5-egress` (#46, Kent) and `doctor/evidence-real` (#44, Kent); notice PR branches `claudemd-notice/arm-teleop-recording` (#47), `claudemd-notice/demo` (#48), `claudemd-notice/firmware-harness-connection` (#49), `claudemd-notice/firmware` (#50), `claudemd-notice/local-model-serve` (#51); `arm/teleop-recording` (Andre, no PR); `firmware-harness-connection` (no commits of its own yet); `cloud/m8-coder-wip` (1 commit, "not for merge"); merged and still on GitHub: `local-model-serve` (#43), `ishaan/pod-agent-loop` (#41), `ishaan/device-healer` (#31), `firmware` and `demo` (Olir)
<!-- team-status:end -->
