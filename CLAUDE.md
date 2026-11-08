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
_Kent's session refreshes this every 10 min · Last synced: Sat Nov 7, 6:50 PM PST · write notes outside the markers_

- **Pitch:** we sell uptime: one heal loop at arm, fleet and company scale. Rivals only diagnose ([Alloy](https://theaiinsider.tech/2026/08/13/alloy-robotics-raises-8m-in-new-funding-to-help-engineers-debug-robot-fleets-with-ai-agents/)) or recommend fixes ([Formant F3](https://www.businesswire.com/news/home/20250630008190/en/Formant-F3-Brings-Generative-AI-and-Agentic-Reasoning-to-Robot-Ops)). We close the loop: "PagerDuty with hands" *(inferred)*.
- **Rule:** no LLM in the real-time control loop. Models propose reversible fixes, and the gate checks them: replay → simulated canary → automatic promote or rollback. People only touch the kill switch (D6, D11).
- **Rule:** the healer may patch the safety layer and the visor (D5a). Those changes are flagged `touches_safety` and always go to the simulated canary first (K17).
- **Rule:** each action type earns autonomy and loses it after a bad fix. The overnight unattended run is the proof *(inferred)*.
- **Demo:** cover the camera or push a bad policy → arm parks or rolls back → root cause on the timeline → AgentMail incident report → hardware fault? Mobilerun orders the part.
- **Research:** pin LeRobot v0.7.0, since it breaks the API ([release](https://github.com/huggingface/lerobot/releases/tag/v0.7.0)).
- **Research:** the SO-101 sim twin is `robotstudio_so101` in MuJoCo Menagerie, and it runs on a Mac ([menagerie](https://github.com/google-deepmind/mujoco_menagerie)).
- **Research:** bring a router, Ethernet and a backup video, because venue Wi-Fi sinks demos ([Nailing the Demo](https://generalrobots.substack.com/p/nailing-the-demo)). Everything else is in [`docs/cloud-platform-planning/README.md`](docs/cloud-platform-planning/README.md).
- **Now:** three PRs are open. #16 (M7 overnight kit; F10) leads into the 12:30 AM go/no-go. #18 covers gate edge cases and the regression corpus, with K17 failing closed. #19 (M8a) builds the coding-agent sandbox: mini-swe-agent proven on a toy device with a D1-like fault, not yet wired into the gate. The Fleet Doctor is at v2, with D23–D27 awaiting decision ([`docs/fleet-doctor/`](docs/fleet-doctor/README.md)).
- **Done:** M0 #1–#4 · M1 #5, #8 · M2 #9 · M3 #7, #12 · M4 #10 · M5 #11, #15 · M6 #14 · triage #6 · lint #13 · device kinds #17 · decisions D0–D22 locked.
- **Blocked:** B4, a Naïve `proxy:write` key or RunPod endpoint. M0's live card and LLM call wait on it and are about 45 min late *(inferred)*. Also B1 (device code in the repo), B8 (STM32 toolchain; Olir's `firmware/toolkit/` may solve it *(inferred)*) and the K17 path list.
- **Asks:** Ishaan: land `device/` (in progress; healer pick is qwen3.5:4b with thinking on h3, [benchmark](docs/healer-benchmark-progress.md)), review contracts #3 (B2, B3), send the K17 safety and visor paths, co-review D23–D27. James: the RunPod endpoint. Owed to James: a Naïve key with Board scopes.
<!-- romulus-sync:end -->

### Device-side healer (Ishaan)
<!-- ishaan-sync:start -->
_Ishaan's local Claude session refreshes this block about every 30 min. Last synced: Sat Nov 7, 6:42 PM PST._
- **Now:** landing the device code (B1): `edge/` is split into `device/`, `harness/` and `bench/` in his checkout, not committed yet (session "Build the edge test bed (iteration 1)"). Testing is stopped; next is pushing these to main *(inferred from his 18:08–18:09 messages)*.
- **Just finished:** healer benchmark progress doc with final results and the model pick, qwen3.5:4b with thinking on, harness h3 (`835c4f0`). Device test suite: 18 tests pass, and each planted fault turns it red; oracle 6/6 and baseline 0/6 still hold (test-bed session recap). Earlier: model-selection method (`b9b0f89`), `features.txt` claims (`cd54c16`, `e59093f`), device-side answers (`5cea9c2`).
- **Blocked on:** nothing. Owes Kent the safety and visor path list (K17), the `device/` landing (B1, in progress), a review of the code-package and `code_patch` formats in PR #3 (B2, B3), and a co-review of Fleet Doctor D23–D27, per Kent's block.
- **Branch and files:** `main`. Untracked in his checkout: `device/` (README, pytest.ini, `tests/test_spec.py`, `tests/test_classify.py`, conftest), `harness/`, `bench/` (`run_bench.py`, `tasks.py`, README).
<!-- ishaan-sync:end -->

## Team status
<!-- team-status:start -->
_Last updated: Sat Nov 7, 6:48 PM PST_

**Current focus:** a robotic-arm company. Naïve runs the business and the job board; Hermes on the bench drives the arm through a plugin; an LLM operates the arm, with RunPod for anything power- or VRAM-heavy. Kent has three Romulus PRs open: #16 (M7 overnight kit, F10) ahead of the 12:30 AM go/no-go, #18 (gate edge cases, regression replay) and the new #19 (M8a coding-agent sandbox). Ishaan is landing the device code (`device/`, `harness/`, `bench/`; B1). James is running a pi vs Hermes fault lab on Ishaan's six faults *(James's session)*.

**Recently done:**
- Kent merged #17 (`ef7614e`, 6:39 PM): fake arms register as `sim`, an unlabelled device is never treated as `real`, and the incident chain gets readable labels (new flow test F1 device kind)
- Kent opened #19, M8a: a `coder` image with mini-swe-agent 2.4.6, proven on a toy gripper repo with a D1-like fault; not wired into the batch or gate yet, `FakeCoder` stays the default (PR #19)
- Ishaan's device test suite: 18 tests pass and each planted fault turns it red; oracle 6/6, baseline 0/6 (Ishaan's block). Not pushed yet
- James built a fault lab (`prototypes/fault-lab/`, uncommitted): six faults (D1–D3, S1, S2, A5) with a hidden check the agents never see, which also catches the D2 wrong fix. Smoke test on S1: pi and Hermes both fixed it, pi in 41 s for $0.11, Hermes in 23 s. The full run (6 faults × 2 agents × 2) is going *(James's session)*

**Open questions:**
- Kent is blocked on: device code in the repo (B1, Ishaan landing it), the STM32 toolchain in the sandbox (B8), the safety and visor path list from Ishaan (K17, also gates Fleet Doctor), and a Naïve key with `proxy:write` or James's RunPod endpoint (B4), which also holds up M0's live Naïve card and LLM call (Kent's block). Olir's `firmware/toolkit/` may be the quickest fix for B8 *(inferred)*
- The healer's health check doesn't test that the arm lifts, so a wrong D2 fix can pass it (`docs/healer-benchmark-progress.md`). James's fault-lab hidden check catches it; worth reusing before the demo *(inferred)*
- Motor layer for the arm: scripted skills (doable tomorrow), SmolVLA (~50 demos per task + ~4 h A100 training) or Instant Policy (needs a depth camera, noncommercial). A flat sheet of paper is a hard grasp for a two-finger gripper *(James's session)*
- Hermes compaction fails because its summarizer can't load the ChatGPT (`openai-codex`) login; start `hermes gateway ensure` before the demo (`docs/pi-vs-hermes.md`). Should pi drive the arm at all, and does the ultrasonic sensor go through the STM32 or Pi GPIO? Both still team calls

**Recent commits (all authors):**
- `f449f96` Sat 18:42 · Ishaan Ranjan · Update Ishaan status in CLAUDE.md
- `ef7614e` Sat 18:39 · kgang · Romulus: fake arms register as sim; readable chain labels (#17)
- `a9a72ac` Sat 18:38 · jamesEmerson112 · Update team status in CLAUDE.md
- `f462ed2` Sat 18:38 · Kent Gang · Update Kent status in CLAUDE.md
- `7325210` Sat 18:29 · jamesEmerson112 · Update team status and features.txt
- `fcd5bc2` Sat 18:28 · Kent Gang · Update Kent status in CLAUDE.md
- `f177c68` Sat 18:19 · kgang · Romulus M3b: batch, gate, kill switch and worker (flows F3, F4, F5) (#12)
- `0bf3fa7` Sat 18:19 · jamesEmerson112 · Update team status and features.txt
- `ab1c379` Sat 18:18 · Kent Gang · Update Kent status in CLAUDE.md
- `835c4f0` Sat 18:18 · Ishaan Ranjan · Update healer benchmark progress: final results, model pick, new layout

**Branches on GitHub:** `main`, `firmware` (Olir, not merged), `cloud/m7-overnight` (#16 open), `cloud/gate-followups` (#18 open), `cloud/m8a-coder` (#19 open), `cloud/m8-coder-wip`, `cloud/m3b-followup`, `cloud/m3b-batch-gate` (#12 merged, branch not yet deleted); all Kent's
<!-- team-status:end -->
