# CLAUDE.md — Naive Nous YC Hackathon

## Branch notice for Olir's Claude session (from James, Sat Nov 7, about 11 PM PST)

**`firmware` is 201 commits behind `main`.** Everything below this notice is `main`'s CLAUDE.md at `7c734da` (Sat 10:59 PM). The team status, the claims and the working rules below are current; the copy this branch had was from Sat 2:06 PM. This branch has no commits of its own, so merging main is a fast-forward.

Before you do more work on this branch:
1. Bring in main with `git fetch origin && git merge origin/main`, or start a fresh branch from `origin/main`. If CLAUDE.md conflicts, take main's copy with `git checkout --theirs CLAUDE.md`. That also removes this notice.
2. Read `features.txt` on main and check the claims (`<--- Name`) before starting anything. If someone else has the line, talk to them first.
3. Check these overlaps, found Sat 10:55 PM:
   - **Two layouts for the same arm files.** #36 (merged from `demo`) put byte-identical copies of Andre's `recording_2.json`, `recording_3.json`, `wave.json`, `naive_follower.json` and `prim-record.sh` on main in a flat `arm/` folder, with its own `arm/README.md`. `arm/teleop-recording` keeps the same files under `arm/prims/library/`, `arm/calibration/` and `arm/bin/`. Merging main into that branch conflicts on `arm/README.md` and leaves two copies of each file. Andre and Olir: pick one layout.
   - **Three drivers for the real arm.**
     - Andre's `prims.Arm` (`arm/prims/core.py`, on `arm/teleop-recording`).
     - Olir's `arm/safe_replay.py` and `arm/live_replay.py` (on main, #36).
     - James's simulated arm host and the Hermes `so101-arm` plugin (`prototypes/arm-sim/` on `james/arm-sim`, not pushed yet). James and Andre plan to point it at the real arm on Sunday at 7:30 AM.

     Agree which one drives the demo before extending any of them.
   - **Ultrasonic source and serial ports.** Both were flagged in #31:
     - `device/robot/` reads the ultrasonic in cm from Pi GPIO, while Olir's pod reports mm through `podd`.
     - The arm's servo bus and `podd` both default to `/dev/ttyACM0`.
   - **One ultrasonic heal story.** Olir's `features.txt` claim "Ultrasonic bad image 07 + arm heal test" and James's sim watchdog are two halves of the same demo. The watchdog parks the arm when the distance stays stuck while the arm moves, then resumes after a verified repair. Line the two up.

---

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
_Ishaan's local Claude session refreshes this block about every hour. Last synced: Sat Nov 7, 10:15 PM PST._
- **Now:** pod healer loop handed to Olir's session for bench runs (`firmware/harness/HANDOFF.md`, `8d9a30b`); session "Build the pod healer agent loop and blind eval" stood down at 21:47 with no bench run. Not on the bench yet. The Jetson link is still paused (no USB data or LAN path, session "Identify the connected Jetson").
- **Just finished:** pod healer agent loop and blind eval merged as #41 (`4ee3517`): `agent.py` with Claude and Ollama drivers and a risk gate, `evaluate.py`, tests, README, HANDOFF.md (`72f53c8`); pointed Olir's session to the handoff in CLAUDE.md (`8d9a30b`). Earlier: device code and healer harness #31 (`6c3b006`).
- **Blocked on:** bench runs wait on Olir (SSH to the pod, API key, per the session recap). Still owes Kent the K17 safety and visor path list and the contracts review (B2, B3), per Kent's block.
- **Branch and files:** main checkout on `main` at `8d9a30b`, clean, up to date. `ishaan/pod-agent-loop` (worktree `naive-pod-loop`) is merged.
<!-- ishaan-sync:end -->

**Pod healer agent loop, for Olir's session:** running or benchmarking it on the bench (`firmware/harness/`, merged as #41)? Start with [`firmware/harness/HANDOFF.md`](firmware/harness/HANDOFF.md): bench steps, what's verified, what to report.

## Team status
<!-- team-status:start -->
## Team status
_Last updated: Sat Nov 7, 10:58 PM PST_

**Current focus:** a robotic-arm company. Naïve runs the business and the job board; Hermes on the bench drives the arm through a plugin; an LLM operates the arm, with RunPod for anything power- or VRAM-heavy. Kent opened #46 (B5, 10:57 PM): the coding agent's sandbox now reaches only the LLM, through a key-holding proxy; #44 (Fleet Doctor PR 7, the last of 9) is still open. James's P1 arm prototypes work in the MuJoCo sim but aren't pushed yet. Olir has the pod healer bench runs (`firmware/harness/HANDOFF.md`) and the arm heal test with bad ultrasonic image 07. The overnight launch is due by 12:30 AM (Kent's plan).

**Recently done:**
- Kent opened #46 (`cloud/b5-egress`): `cloud/coder/llm_proxy.py` holds the real key and forwards only `POST /v1/chat/completions` with a per-job token; `adapters/coder_egress.py` puts the agent on an `--internal` Docker network per run and tears it down after; no open-network mode (fail closed). Why: #44 feeds the agent untrusted log text (PR body)
- James made an 81-second animated explainer of the self-healing demo (private claude.ai artifact "Self-Healing SO-101"). Its 49 mm / 30 mm numbers are illustrative; tonight's sim run gave 130 mm / 29 mm (James's session)
- Kent merged #38 (`c8b58cb`, 10:20 PM): real device code in the coder path (B1, B3), then #45 (`ccb95fb`, 10:28 PM), M8 status docs
- James's P1 prototypes run in simulation (local branch `james/arm-sim`, not pushed): arm host, `so101-arm` Hermes plugin with a `pre_tool_call` guard, approach-then-grasp, a level-1 watchdog, a fault factory, a ChatTS level-2 test on RunPod (pod terminated). Sim only; the real arm still needs checking (James's session)

**Open questions:**
- Pod healer bench runs: the model runs on the laptop over Ethernet (#43), but nothing has been checked on the bench yet: `pi_check.py`, podd, SWD, flashing, the scenarios, the SSH `--target` path, or the `claude` driver against the live API (HANDOFF.md, Ishaan's block)
- Kent is still blocked only on the K17 safety and visor path list from Ishaan (it also gates the Fleet Doctor), plus his own go-ahead for the live Naïve card. Ishaan also still owes the contracts review (B2, B3)
- #31 flagged two clashes: the arm's servo bus and `podd` both default to `/dev/ttyACM0`, and `robot/` reads the ultrasonic in cm from Pi GPIO while Olir's pod reports mm. Should `robot/` follow `arm/safe_replay.py` (`/dev/serial/by-id/`, mm through `podd`)? *(inferred)*
- Team calls still open: Stripe direction (bill the robot owner or pay the supplier), Monid's job, owners for the unclaimed lines (fleet-ops, Mobilerun key, Stripe payment, AgentMail email), and when `james/arm-sim` gets pushed for Kent's 9–10 AM "real arm + polish" slot

**Recent commits (all authors):**
- `1f043d6` Sat 22:38 · jamesEmerson112 · Update team status in CLAUDE.md
- `612ca01` Sat 22:38 · Kent Gang · Update Kent status in CLAUDE.md
- `5ce8590` Sat 22:29 · jamesEmerson112 · Update team status in CLAUDE.md
- `fba444e` Sat 22:29 · jamesEmerson112 · Update team status and features.txt
- `cd261a2` Sat 22:28 · Olir Eswar · Claim pod healer bench runs and the ultrasonic arm heal test in features.txt
- `ccb95fb` Sat 22:28 · kgang · Romulus docs: M8 status after #24, #28, #38 (#45)
- `625c345` Sat 22:28 · Kent Gang · Update Kent status in CLAUDE.md
- `c8b58cb` Sat 22:20 · kgang · Romulus B1: real device code in the coder path (#38)
- `dd40482` Sat 22:19 · jamesEmerson112 · Update team status and features.txt
- `22ff5a8` Sat 22:18 · Kent Gang · Update Kent status in CLAUDE.md

**Branches on GitHub:** `main`; open PR branches `cloud/b5-egress` (#46, Kent) and `doctor/evidence-real` (#44, Kent); `arm/teleop-recording` (Andre, no PR); `firmware-harness-connection` (no commits of its own yet); `cloud/m8-coder-wip` (1 commit, "not for merge"); merged and still on GitHub: `local-model-serve` (#43), `ishaan/pod-agent-loop` (#41), `ishaan/device-healer` (#31), `firmware` and `demo` (Olir). `cloud/b1-device-code` (#38) has been deleted
<!-- team-status:end -->
