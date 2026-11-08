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
_Kent's session refreshes this every 10 min · Last synced: Sat Nov 7, 9:40 PM PST · write notes outside the markers_

- **Pitch:** we sell uptime: one heal loop at arm, fleet and company scale. Rivals only diagnose ([Alloy](https://theaiinsider.tech/2026/08/13/alloy-robotics-raises-8m-in-new-funding-to-help-engineers-debug-robot-fleets-with-ai-agents/)) or recommend fixes ([Formant F3](https://www.businesswire.com/news/home/20250630008190/en/Formant-F3-Brings-Generative-AI-and-Agentic-Reasoning-to-Robot-Ops)). We close the loop: "PagerDuty with hands" *(inferred)*.
- **Rule:** no LLM in the real-time control loop. Models propose reversible fixes, and the gate checks them: replay → simulated canary → automatic promote or rollback. People only touch the kill switch (D6, D11).
- **Rule:** the healer may patch the safety layer and the visor (D5a). Those changes are flagged `touches_safety` and always go to the simulated canary first (K17).
- **Rule:** each action type earns autonomy and loses it after a bad fix. The overnight unattended run is the proof *(inferred)*.
- **Demo:** cover the camera or push a bad policy → arm parks or rolls back → root cause on the timeline → AgentMail incident report → hardware fault? Mobilerun orders the part.
- **Research:** pin LeRobot v0.7.0, since it breaks the API ([release](https://github.com/huggingface/lerobot/releases/tag/v0.7.0)).
- **Research:** the SO-101 sim twin is `robotstudio_so101` in MuJoCo Menagerie, and it runs on a Mac ([menagerie](https://github.com/google-deepmind/mujoco_menagerie)).
- **Research:** bring a router, Ethernet and a backup video, because venue Wi-Fi sinks demos ([Nailing the Demo](https://generalrobots.substack.com/p/nailing-the-demo)). Everything else is in [`docs/cloud-platform-planning/README.md`](docs/cloud-platform-planning/README.md).
- **Now:** four PRs are open. #38 runs Ishaan's real `device/` code through the coder path (B1). Three are Fleet Doctor work, all off by default: #35 (L2 consults and proposals), #32 (device reference code) and #39 (a held Doctor overlay locks in without a second coder pass, D24). Next: P0 Stripe direction with James · P1 live Naïve card · P2 overnight launch by 12:30 AM.
- **Done:** M0 #1–#4 · M1 #5, #8 · M2 #9 · M3 #7, #12, #18 · M4 #10 · M5 #11, #15 · M6 #14 · M7 #16 · M8a #19 · M8b #28 · B8 toolchain #24 · edge bridge #25 · docs #21 · Fleet Doctor plan #22, decisions #23, contracts #26, foundation #27, LLM helper #29, L1 ask #34, ledger #33, L3 contract draft #30, tracker #37 · triage #6 · lint #13 · device kinds #17 · decisions D0–D28 locked (D27 deferred).
- **Blocked:** only on the K17 safety and visor path list, which also gates the Fleet Doctor. B4 is clear (the live card waits only on Kent's go-ahead, #21), and B1 cleared when Ishaan's device code merged as #31 (`device/`, `harness/`, `bench/`).
- **Asks:** Ishaan: review the contracts (I0: B2, B3) and send the K17 safety and visor paths. James: decide the Stripe direction with Kent (bill the robot's owner, or pay the supplier). Owed to James: a Naïve key with Board scopes.
<!-- romulus-sync:end -->

### Device-side healer (Ishaan)
<!-- ishaan-sync:start -->
_Ishaan's local Claude session refreshes this block about every hour. Last synced: Sat Nov 7, 9:12 PM PST._
- **Now:** the pod healer agent loop and blind eval (HARNESS.md) on `ishaan/pod-agent-loop`: `firmware/harness/agent.py` with Claude and Ollama drivers, a risk gate and `--base-url` so the demo fallback can go through Kent's server; 12 tests pass (`6915ac8`, `90dbe6e`, session "Build the pod healer agent loop and blind eval", 20:55). No PR yet. The Jetson link is paused: no USB data or LAN path as of 20:10 (session "Identify the connected Jetson").
- **Just finished:** device code (B1), healer harness h1-h3 and benchmark merged as #31 (`6c3b006`); claimed the pod healer agent loop in `features.txt` (`023f30d`). Earlier: benchmark results and model pick, qwen3.5:4b with thinking on, h3 (`835c4f0`).
- **Blocked on:** bench runs for the pod loop need SSH to the pod, Olir's OK and an API key (session recap). The Jetson link (no USB data, no LAN). Still owes Kent the K17 safety and visor path list and the contracts review (B2, B3), per Kent's block.
- **Branch and files:** `ishaan/pod-agent-loop` in worktree `naive-pod-loop` (`firmware/harness/agent.py`, `evaluate.py`, tests, README). Main checkout on `main`, clean, 11 behind origin.
<!-- ishaan-sync:end -->

## Team status
<!-- team-status:start -->
_Last updated: Sat Nov 7, 9:39 PM PST_

**Current focus:** a robotic-arm company. Naïve runs the business and the job board; Hermes on the bench drives the arm through a plugin; an LLM operates the arm, with RunPod for anything power- or VRAM-heavy. Olir merged an ultrasonic-gated arm replay demo (`arm/`, #36). Kent has four PRs open: #38 (real `device/` code in the coder path, B1) and Fleet Doctor #35, #32 and #39 (lock-in). Ishaan's pod healer loop (`ishaan/pod-agent-loop`) and Andre's `arm/teleop-recording` still have no PR.

**Recently done:**
- Olir merged #36 (`d43683e`, 9:26 PM; commit `c97b937` "demo work"): `arm/safe_replay.py` starts a recorded SO-101 motion only if the pod's HC-SR04 sees nothing within 200 mm (`--live-check` holds the arm mid-motion), plus `live_replay.py`, `extract_motion.py`, a LeRobot pick-and-place episode and Andre's `wave`, `recording_2` and `recording_3` motions. It also committed `arm/__pycache__/*.pyc`
- Kent merged #37 (`d23711e`, 9:30 PM): Fleet Doctor tracker (PRs 0a, 0b, 1, 2, 3, 6, 8 merged; PR 4 in review; PR 5 review fixes; PR 9 building) and `ToolSpec.grant` in `interfaces.md`
- Kent opened #38 (`cloud/b1-device-code`): packages the repo's `device/` from `git archive`, `make device-check`, `make seed-bench` (`arm-1` real, `sim-1` the one canary), held-out verify from `bench/tasks.py`, and `device/`-prefixed `code_patch` paths (B3). Then #39 (`doctor/lock-in`, 9:33 PM): a held Doctor overlay becomes a code candidate without a second Coder pass, only if its proposal checks out (fail closed, D24)
- Earlier tonight: Kent merged #34 (Fleet Doctor L1 `/doctor/ask`), #33 (timeline ledger) and #28 (M8b coding agent in batch and gate); Ishaan pushed `ishaan/pod-agent-loop` (`6915ac8`, `90dbe6e`)

**Open questions:**
- Kent's only blocker is still the K17 safety and visor path list from Ishaan (it also gates the Fleet Doctor, D25), plus his own go-ahead for the live Naïve card. Ishaan's block (9:12 PM) still owes Kent K17 and the contracts review (B2, B3); his pod-loop bench runs need SSH to the pod, Olir's OK and an API key, and the Jetson link is paused (no USB data or LAN path)
- #31 flagged two clashes: the arm's servo bus and `podd` both default to `/dev/ttyACM0`, and `robot/` reads the ultrasonic in cm from Pi GPIO while Olir's pod reports mm. Olir's `arm/safe_replay.py` already finds the pod by `/dev/serial/by-id/` and reads mm through `podd`; should `robot/` follow it? *(inferred)*
- Arm prototypes for tomorrow (control program with `move_to()`/`grasp()` and a servo-error watchdog, ultrasonic approach-then-grasp, predicted-vs-measured check, frozen-ultrasonic rule, labelled run logger) have no owner in a box section; they need to be ready before Kent's 9–10 AM "real arm + polish" slot. `arm/safe_replay.py` covers part of the ultrasonic gate *(inferred)*
- Team calls still open: Stripe direction (bill the robot owner or pay the supplier), Monid's job, and owners for the unclaimed lines (fleet-ops, Mobilerun key, Stripe payment, AgentMail email). Should pi drive the arm at all?

**Recent commits (all authors):**
- `24e5605` Sat 21:38 · Kent Gang · Update Kent status in CLAUDE.md
- `d23711e` Sat 21:30 · kgang · Fleet Doctor: tracker update (7 PRs merged) and ToolSpec.grant in interfaces (#37)
- `d43683e` Sat 21:26 · Oliravan Eswaramoorthy · Merge pull request #36 from jamesEmerson112/demo
- `b4f1d08` Sat 21:20 · jamesEmerson112 · Update team status and features.txt
- `c97b937` Sat 21:17 · Olir Eswar · demo work
- `85ba262` Sat 21:12 · Ishaan Ranjan · Update Ishaan status in CLAUDE.md
- `a3cd256` Sat 21:06 · jamesEmerson112 · Update team status and features.txt
- `3dc26c6` Sat 20:58 · Kent Gang · Update Kent status in CLAUDE.md
- `b66743c` Sat 20:57 · kgang · Fleet Doctor PR 6: hypothesis ledger on the timeline, doctor metrics in the digest (#33)
- `1c8d6bb` Sat 20:53 · kgang · Fleet Doctor L1: POST /v1/devices/{id}/doctor/ask (PR 3) (#34)

**Branches on GitHub:** `main`; open PR branches `cloud/b1-device-code` (#38), `doctor/lock-in` (#39), `doctor/l2-consult` (#35) and `doctor/device-ref` (#32) (all Kent); `ishaan/pod-agent-loop` (Ishaan, no PR); `arm/teleop-recording` (Andre, 6 commits not on `main`, no PR); `firmware-harness-connection` (new, no commits of its own yet); `ishaan/device-healer` (merged as #31); `cloud/m8-coder-wip` (1 commit, "not for merge"); `firmware` and `demo` (Olir, both fully on `main`)
<!-- team-status:end -->
