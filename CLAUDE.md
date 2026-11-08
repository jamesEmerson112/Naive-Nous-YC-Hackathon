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
_Kent's session refreshes this every 10 min · Last synced: Sun Nov 8, 8:38 AM PST · write notes outside the markers_

- **Pitch:** we sell uptime: one heal loop at arm, fleet and company scale. Rivals only diagnose ([Alloy](https://theaiinsider.tech/2026/08/13/alloy-robotics-raises-8m-in-new-funding-to-help-engineers-debug-robot-fleets-with-ai-agents/)) or recommend fixes ([Formant F3](https://www.businesswire.com/news/home/20250630008190/en/Formant-F3-Brings-Generative-AI-and-Agentic-Reasoning-to-Robot-Ops)). We close the loop: "PagerDuty with hands" *(inferred)*.
- **Rule:** no LLM in the real-time control loop. Models propose reversible fixes, and the gate checks them: replay → simulated canary → automatic promote or rollback. People only touch the kill switch (D6, D11).
- **Rule:** the healer may patch the safety layer and the visor (D5a). Those changes are flagged `touches_safety` and always go to the simulated canary first (K17).
- **Rule:** each action type earns autonomy and loses it after a bad fix. The overnight unattended run is the proof *(inferred)*.
- **Demo:** cover the camera or push a bad policy → arm parks or rolls back → root cause on the timeline → AgentMail incident report → hardware fault? Mobilerun orders the part.
- **Research:** pin LeRobot v0.7.0, since it breaks the API ([release](https://github.com/huggingface/lerobot/releases/tag/v0.7.0)).
- **Research:** the SO-101 sim twin is `robotstudio_so101` in MuJoCo Menagerie, and it runs on a Mac ([menagerie](https://github.com/google-deepmind/mujoco_menagerie)).
- **Research:** bring a router, Ethernet and a backup video, because venue Wi-Fi sinks demos ([Nailing the Demo](https://generalrobots.substack.com/p/nailing-the-demo)). Everything else is in [`docs/cloud-platform-planning/README.md`](docs/cloud-platform-planning/README.md).
- **Now:** **Naïve Technician** T0 backend in review as #68 (APIs rows, D30 draft, F14 flow test). The live spike returned a valid report in 29.6 s for $0.072 (#67), well inside G1's 10 min *(inferred: G1 passes)*. Also started fleet-learning research, which turns verified heal traces into training data ([`docs/recursive-self-improvement/research.md`](docs/recursive-self-improvement/research.md)).
- **Done:** Naïve Technician plan `eafc667`–`c5a1fa1`, v2 `46a247b`, case page #65, Studio approval #66 #69, session + shop adapters and live spike #67 · M0 #1–#4 · M1 #5, #8 · M2 #9 · M3 #7, #12, #18 · M4 #10 · M5 #11, #15 · M6 #14 · M7 #16 · M8a #19 · M8b #28 · real device code #38 · replay hardening #53 · B5 sandbox #46 · held-out verify #58 · B8 toolchain #24 · edge bridge #25 · docs #21, #45 · Fleet Doctor #22, #23, #26, #27, #29, #30, #32–#35, #37, #39, #42, #44, #52, #57, #59–#61 · triage #6 · lint #13, #40 · device kinds #17 · decisions D0–D29 locked (D27 deferred; D30 drafted).
- **Blocked:** Naïve Technician waits on Kent's five calls (D30 a person checks out, servo variant, technician teammate, OK for live Naïve writes, which cloud runs the 1 PM demo; README). Fleet Doctor still waits on the K17 safety and visor path list. B1 and B4 are clear.
- **Asks:** Andre: is our STS3215 the C001 7.4 V or the C018 12 V? Team: who'll play the technician (email, plus phone for T1)? Ishaan: K17 safety and visor paths and the contracts review (B2, B3).
<!-- romulus-sync:end -->

### Device-side healer (Ishaan)
<!-- ishaan-sync:start -->
_Ishaan's local Claude session refreshes this block about every hour. Last synced: Sun Nov 8, 8:17 AM PST._
- **Now:** pod healer loop handed to Olir's session for bench runs (`firmware/harness/HANDOFF.md`, `8d9a30b`); session "Build the pod healer agent loop and blind eval" stood down at 21:47 with no bench run. Not on the bench yet. The Jetson link is still paused (no USB data or LAN path, session "Identify the connected Jetson").
- **Just finished:** pod healer agent loop and blind eval merged as #41 (`4ee3517`): `agent.py` with Claude and Ollama drivers and a risk gate, `evaluate.py`, tests, README, HANDOFF.md (`72f53c8`); pointed Olir's session to the handoff in CLAUDE.md (`8d9a30b`). Earlier: device code and healer harness #31 (`6c3b006`).
- **Blocked on:** bench runs wait on Olir (SSH to the pod, API key, per the session recap). Still owes Kent the K17 safety and visor path list and the contracts review (B2, B3), per Kent's block.
- **Branch and files:** main checkout on `main` at `78fdaae`, clean, no uncommitted files. `ishaan/pod-agent-loop` (worktree `naive-pod-loop`) is merged. No Claude sessions in the last 2 h.
<!-- ishaan-sync:end -->

**Pod healer agent loop, for Olir's session:** running or benchmarking it on the bench (`firmware/harness/`, merged as #41)? Start with [`firmware/harness/HANDOFF.md`](firmware/harness/HANDOFF.md): bench steps, what's verified, what to report.

## Team status
<!-- team-status:start -->
## Team status
_Last updated: Sun Nov 8, 8:28 AM PST_

**Current focus:** a robotic-arm company. Naïve runs the business and the job board; Hermes on the bench drives the arm through a plugin; an LLM operates the arm, with RunPod for anything power- or VRAM-heavy. P3 is on: the team is on the real arm 7–10 AM (Pi with Andre: pin serial ports, teach Andre's four poses, `demo/up.sh --real`), with the 10 AM code freeze and 11 AM stop ahead. James's morning steps are in [`MORNING.md`](prototypes/arm-sim/MORNING.md) (revert the Hermes compaction setting, set up the Hermes manager profile, run the loop with the real manager); no James commits since 7:49. Kent is building **Naïve Technician** T0: the case page (#65), the Studio approval example (#66, #69) and the Naïve session + shop adapters with a live spike (#67) are merged; the backend is in review as #68 (`tech/backend`). The 29.6 s spike is well inside G1's 10 min *(inferred by Kent: G1 passes)*. Gates: G1 spike 8:45, go or break-glass 9:00, guaranteed sim clip 9:15, arm slot 9:30.

**Recently done:**
- Kent merged **#65 Naïve Technician T0 case page** (`3bdd637`, `f994212`, `2d30c76`): verdict, part card with one-click approve, email card and live feed (`cloud/src/romulus_cloud/web/templates/technician.html`, `partials/technician_case.html`, tests in `cloud/tests/unit/web/test_technician_template.py`); polling pauses during approve, source links are https-only
- Kent merged **#66 and #69 approval in the Naïve Studio** (`60a7790`, `e66f69b`, `53cbdf1`, `978bcd8`, `a032496`): a working example of approving the staged part in the Studio (`cloud/scripts/technician_approval_example.py`, [`approval-in-studio.md`](docs/naive-technician/approval-in-studio.md)). Session 1 was answered **Approve & open cart** on the Studio card at 16:24Z, the watcher caught it and the agent replied `DECISION: approve`; two sessions cost about $0.002
- Kent merged **#67 Naïve session + shop adapters, live spike** (`cb3372c`…`46217e0`, `070becb`): `cloud/src/romulus_cloud/adapters/naive.py` and `shop.py`, `cloud/scripts/technician_spike.py`, recorded sessions in `cloud/tests/fixtures/technician/`. The first live session fixture is sonnet-5.5, 29.6 s, $0.072, with a valid report (`a17e257`), inside G1's ≤ 10 min run
- Kent opened **#68 Naïve Technician T0: backend** (`tech/backend`: APIs §4.4/§4.5/§5 rows, D30 draft, F14 flow test, email after a quick approve); not merged yet

**Open questions:**
- Naïve Technician: Kent's five calls are still open (D30 a person checks out, servo variant C001 7.4 V or C018 12 V, the technician teammate, OK for the live Naïve writes, which cloud runs the 1 PM demo). Kent asks Andre for the servo variant and the team for a technician (email, plus phone for T1)
- James's worker → manager healer has run against a fake manager only, not a real Hermes yet (`MORNING.md`, "Known gaps"). Wrong moves don't show on the heal timeline; Level 3 is not in the loop (L3b)
- Ishaan's pod healer loop (#41) still waits on Olir for bench runs (SSH to the pod, API key); Ishaan still owes Kent the K17 safety and visor path list and the contracts review (B2, B3), so `SAFETY_PATHS_CONFIRMED` stays false
- Team calls still open: Stripe is optional; Monid's job; owners for the unclaimed lines (fleet-ops, Mobilerun key, Stripe payment, AgentMail email); live vs fake work orders for the demo; who drives the real arm. #63 (main → `full-test-2`) is still open though `full-test-2` is merged *(inferred: can be closed)*

**Recent commits (all authors):**
- `477f1ac` Sun 08:27 · Kent Gang · Update Kent status in CLAUDE.md
- `a032496` Sun 08:25 · kgang · Merge pull request #69 from jamesEmerson112/tech/approval-example
- `978bcd8` Sun 08:24 · Kent Gang · approval-in-studio: session 1 answered on the Studio card
- `1108650` Sun 08:19 · jamesEmerson112 · Update team status in CLAUDE.md
- `070becb` Sun 08:18 · kgang · Naïve Technician T0: Naïve session + shop adapters, live spike (#67)
- `46217e0` Sun 08:18 · Kent Gang · Technician: second live fixture (agent v3, caption verdict, visible research); spike verdict maxLength 80
- `81fda0b` Sun 08:17 · Ishaan Ranjan · Update Ishaan status in CLAUDE.md
- `102167e` Sun 08:16 · Kent Gang · Update Kent status in CLAUDE.md
- `53cbdf1` Sun 08:16 · kgang · Naïve Technician: approval in the Naïve Studio example (#66)
- `e66f69b` Sun 08:16 · Kent Gang · Make technician_approval_example.py ruff-clean

**Branches on GitHub:** `main`; `tech/backend` (Kent, #68 open); `test-4` (no commits of its own); the notice PR branches `claudemd-notice/arm-teleop-recording` (#47), `claudemd-notice/demo` (#48), `claudemd-notice/firmware-harness-connection` (#49), `claudemd-notice/firmware` (#50), `claudemd-notice/local-model-serve` (#51); `firmware-harness-connection` (no commits of its own yet); `cloud/m8-coder-wip` (1 commit, "not for merge"); merged and still on GitHub: `tech/adapters` (#67), `tech/page` (#65), `james/arm-sim-ydcsss`, `full-test-3` (#64), `full-test-2` (#62; #63 still open), `doctor/repro-seal` (#57), `james/arm-sim` (#55), `arm/teleop-recording` (Andre, merged into main through #55), `olir/arm-ultrasonic-heal` (#54), `local-model-serve` (#43), `ishaan/pod-agent-loop` (#41), `ishaan/device-healer` (#31), `firmware` and `demo` (Olir)
<!-- team-status:end -->
