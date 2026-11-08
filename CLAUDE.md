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
_Kent's session refreshes this every 10 min · Last synced: Sat Nov 7, 6:10 PM PST · write notes outside the markers_

- **Pitch:** we sell uptime: one heal loop at arm, fleet and company scale. Rivals only diagnose ([Alloy](https://theaiinsider.tech/2026/08/13/alloy-robotics-raises-8m-in-new-funding-to-help-engineers-debug-robot-fleets-with-ai-agents/)) or recommend fixes ([Formant F3](https://www.businesswire.com/news/home/20250630008190/en/Formant-F3-Brings-Generative-AI-and-Agentic-Reasoning-to-Robot-Ops)). We close the loop: "PagerDuty with hands" *(inferred)*.
- **Rule:** no LLM in the real-time control loop. Models propose reversible fixes, and the gate checks them: replay → simulated canary → automatic promote or rollback. People only touch the kill switch (D6, D11).
- **Rule:** the healer may patch the safety layer and the visor (D5a). Those changes are flagged `touches_safety` and always go to the simulated canary first (K17).
- **Rule:** each action type earns autonomy and loses it after a bad fix. The overnight unattended run is the proof *(inferred)*.
- **Demo:** cover the camera or push a bad policy → arm parks or rolls back → root cause on the timeline → AgentMail incident report → hardware fault? Mobilerun orders the part.
- **Research:** pin LeRobot v0.7.0, since it breaks the API ([release](https://github.com/huggingface/lerobot/releases/tag/v0.7.0)).
- **Research:** the SO-101 sim twin is `robotstudio_so101` in MuJoCo Menagerie, and it runs on a Mac ([menagerie](https://github.com/google-deepmind/mujoco_menagerie)).
- **Research:** bring a router, Ethernet and a backup video, because venue Wi-Fi sinks demos ([Nailing the Demo](https://generalrobots.substack.com/p/nailing-the-demo)). Everything else is in [`docs/cloud-platform-planning/README.md`](docs/cloud-platform-planning/README.md).
- **Now:** PRs #12 (M3b: batch, gate, kill switch; F3–F5) and #14 (M6: datasets, labels, exports; F8) are open. The Fleet Doctor proposal is at v2 after a red-team review ([`docs/fleet-doctor/`](docs/fleet-doctor/README.md)): a P2 add-on that stays out of safety, with live sessions deferred (D23–D27, awaiting decision).
- **Done:** M0 #1–#4 · M1 #5, #8 · M2 #9 · M3a #7 · M4 #10 · M5 #11, #15 · triage #6 · lint #13 · decisions D0–D22 locked.
- **Blocked:** B4, a Naïve `proxy:write` key or RunPod endpoint. M0's live card and LLM call wait on it and are about 45 min late *(inferred)*. Also B1 (device code in the repo), B8 (STM32 toolchain; Olir's `firmware/toolkit/` may solve it *(inferred)*) and the K17 path list.
- **Asks:** Ishaan: move `edge/` to `device/`, review contracts #3 (B2, B3), send the safety and visor paths, and co-review the Fleet Doctor (D23–D27). James: the RunPod endpoint. Owed to James: a Naïve key with Board scopes.
<!-- romulus-sync:end -->

### Device-side healer (Ishaan)
<!-- ishaan-sync:start -->
_Ishaan's local Claude session refreshes this block about every 30 min. Last synced: Sat Nov 7, 5:42 PM PST._
- **Now:** confirmation runs for the healer model pick: 18 trials each for qwen3.5:4b thinking on and off, plus 5 extra D1 trials, then the leader pick and an update to `docs/healer-benchmark-progress.md` (session "Build the edge test bed (iteration 1)").
- **Just finished:** first-pass screen on harness h3: qwen3.5:4b fixed 6/6 with thinking, 5/6 without (missed D1), qwen3:4b-instruct-2507 3/6, granite4.1:3b 1/6 (session "Current idea summary"); pushed the progress doc and model-selection method (`b9b0f89`). Earlier: `features.txt` claims (`cd54c16`, `e59093f`), overview and fault catalog (`16c6b9c`, `38771aa`, `325a4bb`), device-side answers (`5cea9c2`).
- **Blocked on:** nothing. Owes Kent the safety and visor path list (K17), the `edge/` to `device/` move after the tests (B1), and a review of the code-package and `code_patch` formats in PR #3 (B2, B3), per Kent's block.
- **Branch and files:** `main`. `edge/` (robot app, sim, supervisor, healer harnesses h1–h3, `bench/run_bench.py`, `bench/analyze.py`) stays local until its own checks pass.
<!-- ishaan-sync:end -->

## Team status
<!-- team-status:start -->
_Last updated: Sat Nov 7, 5:58 PM PST_

**Current focus:** a robotic-arm company. Naïve runs the business and the job board; Hermes on the bench drives the arm through a plugin; an LLM operates the arm, with RunPod for anything power- or VRAM-heavy. Kent merged M5 (#11) and has #12 (M3b batch, gate, kill switch, F3–F5) and #14 (M6 datasets, snapshots, exports, F8) open, while drafting the Fleet Doctor proposal (D23–D27). M0's live Naïve card and LLM call are still missing. Ishaan is running 18-trial confirmations for the healer model pick. James is researching robot-learning options (e.g. Instant Policy, which needs depth cameras the bench doesn't have) *(James's session)*.

**Recently done:**
- Kent merged PR #11 (`8bca41b`, 5:55 PM): M5 work orders → Naïve cards, with an outbox, spend caps, the resolve route and flow test F6. Follow-ups #15 (`62bfde2`, 5:58 PM: case-insensitive placeholder phone check, card change detection without `updated_at`) and #13 (`d61aa31`: ruff scoped to our packages)
- Kent opened PR #14 (`cloud/m6-datasets`, 5:57 PM): M6 datasets, snapshots, exports, regression corpus and flow F8
- Kent pushed Fleet Doctor drafts in `docs/fleet-doctor/` (`d93ed05`, 5:48 PM): concept, architecture and research, a proposal asking for decisions D23–D27 (Kent's block)
- James pushed `docs/human-computer-labs-insights.md` (`0aafce0`, 5:43 PM): what his earlier robot repo offers the SO-101 (MuJoCo sim twin, JSON gesture library, versioned command protocol with acks and stop, benchmark and parity-test habits)

**Open questions:**
- Kent is blocked on: device code and tests in `device/` (B1), the STM32 toolchain in the sandbox (B8), the list of safety and visor paths from Ishaan (K17), and either a Naïve key with `proxy:write` or James's RunPod endpoint (B4). B4 probably also holds up M0's live Naïve card and LLM call, now about 45 min late *(inferred, Kent's block)*. Olir's `firmware/toolkit/` may be the quickest fix for B8 *(inferred, James's session)*
- Where does the ultrasonic sensor plug in? Olir's `firmware` branch reads it on the STM32 and streams CSV to the Pi over `/dev/ttyACM0`; the overview and fault catalog now say so (`dcd291f`), but the hub may still assume Pi GPIO. Team call, with Ishaan's input *(James's session)*
- Ishaan plans to move `edge/` into the repo as `device/` once the model tests finish, which would settle B1. He still owes Kent a review of the code-package and `code_patch` formats in PR #3 (B2, B3)
- The current Naïve key covers agents and sessions only. A key with `proxy:write` would let Hermes run the same models as pi through Naïve's proxy; one with Board scopes would let Hermes pick up and close cards (Kent owes James the Board key). Pi heal time is still unmeasured (needs the Pi's tokens per second)

**Recent commits (all authors):**
- `62bfde2` Sat 17:58 · kgang · Work orders: case-insensitive placeholder phone check; detect card changes without updated_at (#15)
- `8bca41b` Sat 17:55 · kgang · Romulus M5: work orders → Naïve cards (outbox, spend caps, resolve route) + F6 (#11)
- `d61aa31` Sat 17:53 · kgang · Romulus: scope ruff to our Python packages (#13)
- `d64b809` Sat 17:49 · Kent Gang · Update Kent status in CLAUDE.md
- `d93ed05` Sat 17:48 · Kent Gang · Add Fleet Doctor concept, architecture and research drafts
- `6bf0d03` Sat 17:49 · jamesEmerson112 · Update team status and features.txt
- `0aafce0` Sat 17:43 · jamesEmerson112 · Add insights from Human-Computer-Labs for the SO-101 project
- `dcd291f` Sat 17:35 · jamesEmerson112 · Sync notes with Olir's firmware branch (toolkit, HC-SR04 on STM32)
- `a9c8f95` Sat 17:42 · Ishaan Ranjan · Update Ishaan status in CLAUDE.md
- `4b45b33` Sat 17:39 · jamesEmerson112 · Update team status and features.txt

**Branches on GitHub:** `main`, `firmware` (Olir, not merged), `cloud/m3b-batch-gate` (Kent, PR #12 open), `cloud/m6-datasets` (Kent, PR #14 open), `cloud/m5-workorders` (Kent, merged as #11)
<!-- team-status:end -->
