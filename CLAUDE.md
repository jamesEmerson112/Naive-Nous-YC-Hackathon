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
_Kent's session refreshes this every 10 min · Last synced: Sat Nov 7, 6:30 PM PST · write notes outside the markers_

- **Pitch:** we sell uptime: one heal loop at arm, fleet and company scale. Rivals only diagnose ([Alloy](https://theaiinsider.tech/2026/08/13/alloy-robotics-raises-8m-in-new-funding-to-help-engineers-debug-robot-fleets-with-ai-agents/)) or recommend fixes ([Formant F3](https://www.businesswire.com/news/home/20250630008190/en/Formant-F3-Brings-Generative-AI-and-Agentic-Reasoning-to-Robot-Ops)). We close the loop: "PagerDuty with hands" *(inferred)*.
- **Rule:** no LLM in the real-time control loop. Models propose reversible fixes, and the gate checks them: replay → simulated canary → automatic promote or rollback. People only touch the kill switch (D6, D11).
- **Rule:** the healer may patch the safety layer and the visor (D5a). Those changes are flagged `touches_safety` and always go to the simulated canary first (K17).
- **Rule:** each action type earns autonomy and loses it after a bad fix. The overnight unattended run is the proof *(inferred)*.
- **Demo:** cover the camera or push a bad policy → arm parks or rolls back → root cause on the timeline → AgentMail incident report → hardware fault? Mobilerun orders the part.
- **Research:** pin LeRobot v0.7.0, since it breaks the API ([release](https://github.com/huggingface/lerobot/releases/tag/v0.7.0)).
- **Research:** the SO-101 sim twin is `robotstudio_so101` in MuJoCo Menagerie, and it runs on a Mac ([menagerie](https://github.com/google-deepmind/mujoco_menagerie)).
- **Research:** bring a router, Ethernet and a backup video, because venue Wi-Fi sinks demos ([Nailing the Demo](https://generalrobots.substack.com/p/nailing-the-demo)). Everything else is in [`docs/cloud-platform-planning/README.md`](docs/cloud-platform-planning/README.md).
- **Now:** PR #16 (M7: overnight kit; F10) is the last open milestone before the 12:30 AM go/no-go. With M3b merged, the hero loop F2→F5 now runs end to end against fakes *(inferred)*. The Fleet Doctor is at v2, P2, with D23–D27 awaiting decision ([`docs/fleet-doctor/`](docs/fleet-doctor/README.md)).
- **Done:** M0 #1–#4 · M1 #5, #8 · M2 #9 · M3 #7, #12 · M4 #10 · M5 #11, #15 · M6 #14 · triage #6 · lint #13 · decisions D0–D22 locked.
- **Blocked:** B4, a Naïve `proxy:write` key or RunPod endpoint. M0's live card and LLM call wait on it and are about 45 min late *(inferred)*. Also B1 (device code in the repo), B8 (STM32 toolchain; Olir's `firmware/toolkit/` may solve it *(inferred)*) and the K17 path list.
- **Asks:** Ishaan: land `device/` (in progress; healer pick is qwen3.5:4b with thinking on h3, [benchmark](docs/healer-benchmark-progress.md)), review contracts #3 (B2, B3), send the K17 safety and visor paths, co-review D23–D27. James: the RunPod endpoint. Owed to James: a Naïve key with Board scopes.
<!-- romulus-sync:end -->

### Device-side healer (Ishaan)
<!-- ishaan-sync:start -->
_Ishaan's local Claude session refreshes this block about every 30 min. Last synced: Sat Nov 7, 6:12 PM PST._
- **Now:** healer model picked, testing stopped. Moving `edge/` into the repo as `device/`, updating `docs/healer-benchmark-progress.md` with the pick, and starting Kent's test suite (session "Build the edge test bed (iteration 1)"). Also reading Kent's Fleet Doctor docs for the upper-level, offline-first-then-sync harness (session "Current idea summary").
- **Just finished:** model pick: qwen3.5:4b with thinking on, 10/12 in confirmation runs, D1 fixed both times; the queued h1/h2/no-skills runs were cancelled before starting (sessions "Current idea summary", "Build the edge test bed (iteration 1)"). Earlier: progress doc and model-selection method (`b9b0f89`), `features.txt` claims (`cd54c16`, `e59093f`), overview and fault catalog (`16c6b9c`, `38771aa`, `325a4bb`), device-side answers (`5cea9c2`).
- **Blocked on:** nothing. Owes Kent the safety and visor path list (K17), the `edge/` to `device/` move (B1, in progress), a review of the code-package and `code_patch` formats in PR #3 (B2, B3), and a co-review of Fleet Doctor D23–D27, per Kent's block.
- **Branch and files:** `main`. `edge/` (robot app, sim, supervisor, healer harnesses h1–h3, `bench/run_bench.py`, `bench/analyze.py`) is still local and untracked; it lands as `device/` *(inferred from the test-bed session recap)*.
<!-- ishaan-sync:end -->

## Team status
<!-- team-status:start -->
_Last updated: Sat Nov 7, 6:28 PM PST_

**Current focus:** a robotic-arm company. Naïve runs the business and the job board; Hermes on the bench drives the arm through a plugin; an LLM operates the arm, with RunPod for anything power- or VRAM-heavy. Kent merged M3b (#12, batch, gate, kill switch, worker), so only PR #16 (M7 overnight kit, F10) is open ahead of the 12:30 AM go/no-go. Ishaan is moving `edge/` into the repo as `device/`. James is picking the arm's motor layer: plan A is Whisper → Hermes picks scripted skills, with SmolVLA or Instant Policy as upgrades *(James's session)*.

**Recently done:**
- Kent merged M3b (#12, `f177c68`, 6:19 PM): batch service, gate, fleet kill switch and worker, with flow tests F3 lock-in, F4 rollback, F5 kill switch and gate invariants (~3K lines in `cloud/`)
- Ishaan picked the healer model (`835c4f0`): **qwen3.5:4b with thinking on, harness h3**, 10/12 trials and the only config to pass D1, the live demo fault (2/2); 3.3 GB, fits the Pi 5 (`docs/healer-benchmark-progress.md`)
- James researched Instant Policy for the motor layer: weights are released but the code ships compiled, there's no training code, the licence is PolyForm Noncommercial, and it needs depth cameras (2× RealSense in the paper) *(James's session, [repo](https://github.com/vv19/instant_policy))*. Fine for a research demo, not for the business *(inferred)*
- Kent merged M6 datasets, snapshots, exports and the regression corpus (#14, `cc175f6`)

**Open questions:**
- Kent is blocked on: device code and tests in `device/` (B1, Ishaan's move in progress), the STM32 toolchain in the sandbox (B8), the safety and visor path list from Ishaan (K17, also gates Fleet Doctor), and a Naïve key with `proxy:write` or James's RunPod endpoint (B4), which probably also holds up M0's live Naïve card and LLM call *(inferred, Kent's block)*. Olir's `firmware/toolkit/` may be the quickest fix for B8 *(inferred)*
- The healer's health check doesn't test that the arm lifts, so a wrong D2 fix can pass it (`docs/healer-benchmark-progress.md`). Worth adding a lift check before the demo *(inferred)*
- Motor layer for the arm: scripted skills (doable tomorrow), SmolVLA (~50 demos per task + ~4 h A100 training) or Instant Policy (needs a depth camera, noncommercial). A flat sheet of paper is a hard grasp for a two-finger gripper *(James's session)*
- Hermes compaction fails because its summarizer can't load the ChatGPT (`openai-codex`) login; start `hermes gateway ensure` before the demo (`docs/pi-vs-hermes.md`). Should pi drive the arm at all, and does the ultrasonic sensor go through the STM32 or Pi GPIO? Both still team calls

**Recent commits (all authors):**
- `f177c68` Sat 18:19 · kgang · Romulus M3b: batch, gate, kill switch and worker (flows F3, F4, F5) (#12)
- `0bf3fa7` Sat 18:19 · jamesEmerson112 · Update team status and features.txt
- `ab1c379` Sat 18:18 · Kent Gang · Update Kent status in CLAUDE.md
- `835c4f0` Sat 18:18 · Ishaan Ranjan · Update healer benchmark progress: final results, model pick, new layout
- `b306df5` Sat 18:12 · Ishaan Ranjan · Update Ishaan status in CLAUDE.md
- `e4fd0b9` Sat 18:09 · jamesEmerson112 · Update team status and features.txt
- `7354d99` Sat 18:08 · Kent Gang · Update Kent status in CLAUDE.md
- `2330efe` Sat 18:08 · Kent Gang · Update Kent status in CLAUDE.md
- `cc175f6` Sat 18:08 · kgang · Romulus M6: datasets, snapshots, exports, regression corpus + F8 (#14)
- `852032e` Sat 18:07 · jamesEmerson112 · Naive console: conversation mode and memory panels for pi vs Hermes; add pi-vs-Hermes results

**Branches on GitHub:** `main`, `firmware` (Olir, not merged), `cloud/m7-overnight` (Kent, PR #16 open), `cloud/m3b-batch-gate` (#12 merged, branch not yet deleted)
<!-- team-status:end -->
