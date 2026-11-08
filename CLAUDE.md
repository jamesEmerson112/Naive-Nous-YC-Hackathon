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
_Kent's session refreshes this every 10 min · Last synced: Sat Nov 7, 7:40 PM PST · write notes outside the markers_

- **Pitch:** we sell uptime: one heal loop at arm, fleet and company scale. Rivals only diagnose ([Alloy](https://theaiinsider.tech/2026/08/13/alloy-robotics-raises-8m-in-new-funding-to-help-engineers-debug-robot-fleets-with-ai-agents/)) or recommend fixes ([Formant F3](https://www.businesswire.com/news/home/20250630008190/en/Formant-F3-Brings-Generative-AI-and-Agentic-Reasoning-to-Robot-Ops)). We close the loop: "PagerDuty with hands" *(inferred)*.
- **Rule:** no LLM in the real-time control loop. Models propose reversible fixes, and the gate checks them: replay → simulated canary → automatic promote or rollback. People only touch the kill switch (D6, D11).
- **Rule:** the healer may patch the safety layer and the visor (D5a). Those changes are flagged `touches_safety` and always go to the simulated canary first (K17).
- **Rule:** each action type earns autonomy and loses it after a bad fix. The overnight unattended run is the proof *(inferred)*.
- **Demo:** cover the camera or push a bad policy → arm parks or rolls back → root cause on the timeline → AgentMail incident report → hardware fault? Mobilerun orders the part.
- **Research:** pin LeRobot v0.7.0, since it breaks the API ([release](https://github.com/huggingface/lerobot/releases/tag/v0.7.0)).
- **Research:** the SO-101 sim twin is `robotstudio_so101` in MuJoCo Menagerie, and it runs on a Mac ([menagerie](https://github.com/google-deepmind/mujoco_menagerie)).
- **Research:** bring a router, Ethernet and a backup video, because venue Wi-Fi sinks demos ([Nailing the Demo](https://generalrobots.substack.com/p/nailing-the-demo)). Everything else is in [`docs/cloud-platform-planning/README.md`](docs/cloud-platform-planning/README.md).
- **Now:** M0–M7 are merged, flows F1–F8 and F10 pass, and F9 and F11 are partial. #19 (M8a coding-agent sandbox) merged at 7:38. #21 (docs: build status, flows tracker, spike results) is open. Next is the live Naïve card, which needs Kent's go-ahead, then the 12:30 AM overnight go/no-go.
- **Done:** M0 #1–#4 · M1 #5, #8 · M2 #9 · M3 #7, #12, #18 · M4 #10 · M5 #11, #15 · M6 #14 · M7 #16 · triage #6 · lint #13 · device kinds #17 · M8a #19 · decisions D0–D22 locked.
- **Blocked:** nothing on B4 any more: the team key already reaches the Naïve proxy, and the live card only waits on Kent's go-ahead (PR #21). Still open: B1 (device code in the repo), B8 (STM32 toolchain; Olir's `firmware/toolkit/` landed on main in #20) and the K17 path list.
- **Asks:** Ishaan: land `device/` (healer pick is qwen3.5:4b with thinking on h3, [benchmark](docs/healer-benchmark-progress.md)), review contracts #3 (B2, B3), send the K17 safety and visor paths, co-review the Fleet Doctor (D23–D27). Owed to James: a Naïve key with Board scopes.
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
_Last updated: Sat Nov 7, 7:39 PM PST_

**Current focus:** a robotic-arm company. Naïve runs the business and the job board; Hermes on the bench drives the arm through a plugin; an LLM operates the arm, with RunPod for anything power- or VRAM-heavy. Every Romulus milestone PR through M8a is merged and no PRs are open. Kent heads into the 12:30 AM overnight go/no-go with build-status docs on `cloud/docs-status`. Olir's firmware is on `main` (#20), and an LLM agent loop for the pod healer is the next missing piece (`firmware/docs/HARNESS.md`). Ishaan is landing the device code (B1). James is writing a phased plan to tomorrow's demo (local note) *(James's session)*.

**Recently done:**
- Kent merged #19, M8a (`3aba2da`, 7:38 PM): a coder sandbox image (mini-swe-agent, then the device tests), LocalAgent returns the diff and test result, offline code replay of base vs base + patch, a toy device with a D1-like fault, and hardening (held-out `verify/`, symlink, escape and key-leak refusal)
- Olir's `firmware` branch merged into `main` as #20 (`0f9cced`, 7:34 PM): 104 files under `firmware/` (pod firmware, podd + 38 heal tools, six bad images, `toolkit/`). New `firmware/docs/HARNESS.md` is a guide for building the agent loop, and `healer/tests/bench.py` holds the regression tests (`ded518a`)
- Pod bench status (`firmware/docs/DEMO.md`): detection for scenarios 01–05 re-verified (bench A1–A5) and the full 01 fix; fixes for 02, 05 and 06 verified earlier. Not yet run: durable fixes for 03/04 (B1, B2), the C1–C3 checks, physical tests and any LLM agent run. Scenario 04 has a serial-bridge problem
- Kent merged #18, gate follow-ups (`2e83133`, 7:27 PM): re-queued overlays survive being superseded, freeze snapshot, `repeat_since` on unfreeze, failed folds bisected once, regression corpus replay, K17 fails closed

**Open questions:**
- Kent is blocked on: device code in the repo (B1, Ishaan landing it), the safety and visor path list from Ishaan (K17), and a Naïve key with `proxy:write` or James's RunPod endpoint (B4), which also holds up M0's live Naïve card (Kent's block). Now that `firmware/toolkit/` is on `main`, it may close B8 (STM32 toolchain in the sandbox) *(inferred)*
- Nobody owns the pod healer's agent loop and blind scenario eval yet; `HARNESS.md` marks both "you build this". Is that Ishaan's device healer, a Hermes plugin, or new work? It is also how the two healers become one demo story *(inferred)*
- Pod triage gap: the rules don't check the ultrasonic for frozen data, so a frozen-echo copy of a real sample raised no finding (`docs/fault-catalog.md`). The device healer's health check doesn't test that the arm lifts, so a wrong D2 fix can pass it; the fault lab's hidden check catches it
- Team calls still open: Stripe direction (bill the robot owner or pay the supplier), Monid's job, and owners for the unclaimed lines (fleet-ops, Mobilerun key, Stripe payment, AgentMail email) *(James's session)*. Is the ultrasonic read through the STM32 pod or Pi GPIO? Should pi drive the arm at all?

**Recent commits (all authors):**
- `3aba2da` Sat 19:38 · kgang · Romulus M8a: coder sandbox and LocalAgent, proven on a toy device (#19)
- `0f9cced` Sat 19:34 · Oliravan Eswaramoorthy · Merge pull request #20 from jamesEmerson112/firmware
- `ded518a` Sat 19:32 · Olir Eswar · docs
- `52f4e40` Sat 19:29 · jamesEmerson112 · Update team status and features.txt
- `0f9c0b5` Sat 19:28 · Kent Gang · Update Kent status in CLAUDE.md
- `2e83133` Sat 19:27 · kgang · Romulus M3 follow-up: gate edge cases + regression corpus replay (#18)
- `f1d9224` Sat 19:25 · Olir Eswar · more firmware
- `557ad01` Sat 19:19 · jamesEmerson112 · Update team status in CLAUDE.md
- `98fe550` Sat 19:11 · jamesEmerson112 · Add fault lab: can pi and Hermes fix Ishaan's six healer faults? Show results in the console
- `cda262d` Sat 19:08 · jamesEmerson112 · Update team status and features.txt

**Branches on GitHub:** `main`, `firmware` (Olir, merged as #20), `cloud/docs-status` (Kent, build status + `features.txt` edits, not merged), `cloud/m8-coder-wip`, `cloud/m3b-followup`. `cloud/m8a-coder` and `cloud/gate-followups` are gone
<!-- team-status:end -->
