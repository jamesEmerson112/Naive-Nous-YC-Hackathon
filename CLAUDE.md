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
_Kent's session refreshes this every 10 min · Last synced: Sat Nov 7, 7:30 PM PST · write notes outside the markers_

- **Pitch:** we sell uptime: one heal loop at arm, fleet and company scale. Rivals only diagnose ([Alloy](https://theaiinsider.tech/2026/08/13/alloy-robotics-raises-8m-in-new-funding-to-help-engineers-debug-robot-fleets-with-ai-agents/)) or recommend fixes ([Formant F3](https://www.businesswire.com/news/home/20250630008190/en/Formant-F3-Brings-Generative-AI-and-Agentic-Reasoning-to-Robot-Ops)). We close the loop: "PagerDuty with hands" *(inferred)*.
- **Rule:** no LLM in the real-time control loop. Models propose reversible fixes, and the gate checks them: replay → simulated canary → automatic promote or rollback. People only touch the kill switch (D6, D11).
- **Rule:** the healer may patch the safety layer and the visor (D5a). Those changes are flagged `touches_safety` and always go to the simulated canary first (K17).
- **Rule:** each action type earns autonomy and loses it after a bad fix. The overnight unattended run is the proof *(inferred)*.
- **Demo:** cover the camera or push a bad policy → arm parks or rolls back → root cause on the timeline → AgentMail incident report → hardware fault? Mobilerun orders the part.
- **Research:** pin LeRobot v0.7.0, since it breaks the API ([release](https://github.com/huggingface/lerobot/releases/tag/v0.7.0)).
- **Research:** the SO-101 sim twin is `robotstudio_so101` in MuJoCo Menagerie, and it runs on a Mac ([menagerie](https://github.com/google-deepmind/mujoco_menagerie)).
- **Research:** bring a router, Ethernet and a backup video, because venue Wi-Fi sinks demos ([Nailing the Demo](https://generalrobots.substack.com/p/nailing-the-demo)). Everything else is in [`docs/cloud-platform-planning/README.md`](docs/cloud-platform-planning/README.md).
- **Now:** all core milestones M0–M7 are merged, and the 12:30 AM overnight go/no-go is next. #19 (M8a coding-agent sandbox) is the only open PR and isn't wired into the gate yet. The Fleet Doctor is at v2, with D23–D27 awaiting decision ([`docs/fleet-doctor/`](docs/fleet-doctor/README.md)).
- **Done:** M0 #1–#4 · M1 #5, #8 · M2 #9 · M3 #7, #12, #18 · M4 #10 · M5 #11, #15 · M6 #14 · M7 #16 · triage #6 · lint #13 · device kinds #17 · decisions D0–D22 locked.
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
_Last updated: Sat Nov 7, 7:28 PM PST_

**Current focus:** a robotic-arm company. Naïve runs the business and the job board; Hermes on the bench drives the arm through a plugin; an LLM operates the arm, with RunPod for anything power- or VRAM-heavy. Kent merged #18 and heads into the 12:30 AM overnight go/no-go with only #19 (M8a coding-agent sandbox) open. Ishaan is landing the device code (`device/`, `harness/`, `bench/`; B1). Olir keeps hardening the pod healer on `firmware`. James is working through the healing story (watchdog code wakes the healer; the healer fixes causes, not positions) for the briefing page.

**Recently done:**
- Kent merged #18, gate follow-ups (`2e83133`, 7:27 PM): re-queued overlays survive being superseded, freeze snapshot, repeat-incident check on unfreeze (`repeat_since`), failed folds bisected once, regression corpus replay (`test_f4_corpus_regression.py`), K17 fails closed
- Olir pushed `f1d9224` to `firmware` (7:25 PM): the reboot-loop rule now counts only abnormal boots (crash/watchdog streak, por/lpwr), clock-drift check uses a median over the current run, lines cut short by a reset are dropped via required fields per type, and the line parser no longer uses a backtracking regex
- James pushed the fault lab (`98fe550`, 7:11 PM): Ishaan's six healer faults (D1–D3, S1, S2, A5) planted in a stand-in app under `prototypes/fault-lab/`, with hidden checks. On gpt-6-sol, pi and Hermes each fixed 12 of 12 and Hermes was faster on 5 of 6. Results are in the Naïve console ("Can it fix the fault?") and `docs/pi-vs-hermes.md` §7
- James's "Self-Healing Arm Briefing" page: problem, demo in five steps, available data and gaps (sim vs bench), three levels, MuJoCo vs NVIDIA Isaac, GR00T. It is private and has no visual check yet; the ultrasonic-sample note is still local only (`79f938b`) *(James's session)*

**Open questions:**
- Kent is blocked on: device code in the repo (B1, Ishaan landing it), the STM32 toolchain in the sandbox (B8), the safety and visor path list from Ishaan (K17), and a Naïve key with `proxy:write` or James's RunPod endpoint (B4), which also holds up M0's live Naïve card and LLM call (Kent's block). Olir's `toolkit/` is now on the `firmware` branch and may solve B8 *(inferred)*
- Pod triage gap: the rules check the accelerometer for frozen data but not the ultrasonic, so a frozen-echo copy of a real sample raised no finding (`docs/fault-catalog.md`). Separately, the healer's health check doesn't test that the arm lifts, so a wrong D2 fix can pass it; the fault lab's hidden check catches it
- Two healers (Olir's firmware healer in `firmware/healer/`, Ishaan's device healer) are separate code; the demo needs one story for both *(inferred, `docs/project-overview.md`)*. Is the ultrasonic read through the STM32 pod or Pi GPIO? Should pi drive the arm at all? Team calls
- Sim use for tomorrow: a twin beside the real arm or the sim canary is doable; the 1,000-copy fault factory for a level-3 model is pitch only *(James's session, inferred)*. Motor layer: scripted skills, SmolVLA (~50 demos per task + ~4 h A100) or Instant Policy. Hermes compaction still needs `hermes gateway ensure` (`docs/pi-vs-hermes.md`)

**Recent commits (all authors):**
- `2e83133` Sat 19:27 · kgang · Romulus M3 follow-up: gate edge cases + regression corpus replay (#18)
- `557ad01` Sat 19:19 · jamesEmerson112 · Update team status in CLAUDE.md
- `98fe550` Sat 19:11 · jamesEmerson112 · Add fault lab: can pi and Hermes fix Ishaan's six healer faults? Show results in the console
- `cda262d` Sat 19:08 · jamesEmerson112 · Update team status and features.txt
- `f19cdff` Sat 19:08 · Kent Gang · Update Kent status in CLAUDE.md
- `7dd4606` Sat 19:01 · kgang · Romulus M7: overnight kit (chaos schedule, digest, disk budget, overnight flag, preflight) + F10 (#16)
- `e64ed72` Sat 18:59 · jamesEmerson112 · Update team status and features.txt
- `fd1f4fd` Sat 18:54 · jamesEmerson112 · Sync notes with Olir's sensor pod, podd healer tools and demo images (69fc292)
- `a2d6676` Sat 18:49 · jamesEmerson112 · Update team status and features.txt
- `219a00c` Sat 18:48 · Kent Gang · Update Kent status in CLAUDE.md

**Branches on GitHub:** `main`, `firmware` (Olir, `f1d9224`, not merged), `cloud/m8a-coder` (#19 open), `cloud/gate-followups` (#18 merged, not yet deleted), `cloud/m8-coder-wip`, `cloud/m3b-followup`; all Kent's except `firmware`
<!-- team-status:end -->
