# Insights from Human-Computer-Labs (James's earlier robot repo)

- **What this is:** what the SO-101 project can reuse from James's repo [jamesEmerson112/Human-Computer-Labs](https://github.com/jamesEmerson112/Human-Computer-Labs), read on Sat Nov 7, 2026. The repo's last push was Oct 17, 2026.
- **Sources:** paths below are in that repo unless they say otherwise. *(inferred)* marks our own conclusions.

## 1. What the repo is

It's two take-home challenges for a "live character robot" role (`README.md`, `SWE_route/starter_pack/CHALLENGE.md`).

- **SWE route (`SWE_route/simulation/`): a simulated robot body.**
  - The robot is a fictional 5-joint desk lamp, supplied as a URDF.
  - A Rust controller runs the motion and MuJoCo 3.14.0 runs the physics. Three.js draws the lamp in the browser, and the browser talks to the controller over a WebSocket. A Python/viser version is kept as the reference to check against.
  - The lamp has 126 gestures, written as JSON files, including 117 emotions.
  - Every change was benchmarked headless, with each run's settings and file hashes saved.
- **ML route (`ML_route/`): MELD emotion recognition.**
  - Whisper turns speech into text, three frozen encoders turn the text and the voice into features, a small head trained in Rust picks the emotion, and LFM2.5-1.2B writes a short reply.
  - It scores 61.96 weighted F1 on MELD test, with about 2.8B parameters in total.
  - It runs live on one 8 GB GTX 1070 Ti. A Runpod A40 ran the model benchmarks: 7.4 hours for about $4.35.
  - The player page embeds a copy of the lamp, which reacts to each line's emotion.

## 2. What carries over to the SO-101 project

| # | In Human-Computer-Labs | Where it helps here | Fits |
|---|---|---|---|
| 1 | MuJoCo sim with three modes: kinematic, physics with contacts, and default physics (`SWE_route/simulation/README.md`) | A sim twin of the SO-101 using MuJoCo Menagerie's `robotstudio_so101` (`docs/so101-research.md` §2), as the Romulus **sim canary** | features.txt "Sim canary device for Romulus" (unclaimed) |
| 2 | Gestures as small JSON files with a schema (`gestures/nod.json`: offsets, holds, `on_limit: reverse`, `return_to_start`) | A library of named arm moves that the LLM picks by name, while the controller enforces joint limits | Hermes arm plugin tools; "LLM only proposes typed actions" (Romulus design rules in `CLAUDE.md`) |
| 3 | Versioned command protocol (`protocol/commands.schema.json`, `urn:lamp:commands:v1`). Every command has an id and gets an ack. Messages over 16 KiB, duplicate keys and NaN numbers are rejected. **Stop** cancels queued motion and holds at the observed pose | A template for the Hermes arm plugin's tool contract and the `pre_tool_call` safety guard *(inferred)* | Hermes lane |
| 4 | The challenge asks you to "define the action representation and the boundary between model decisions and body execution" (`CHALLENGE.md`). The controller owns timing: 30 Hz control with eight 240 Hz physics steps per tick | The same rule as Romulus: no LLM in the real-time loop. Here the STM32 or a Python loop is the body, and the LLM only picks actions *(inferred)* | Design rule, already agreed |
| 5 | Benchmark discipline: each run records the git revision, machine, background CPU load, a manifest and hashes (`benchmarks/2026-10-13-current-setup/README.md`) | Ishaan's healer benchmark and Kent's replay gate: same habits, so the numbers compare across runs *(inferred)* | Healer benchmark; M3 gate |
| 6 | Parity tests: Rust vs Python motion matches to 1e-9 over 114,582 frames, and the TypeScript lamp matches Python to 1e-12 on all 126 clips (`benchmarks/2026-10-14-native-stack/headless/summary.json`, `README.md`) | `rules.evaluate` must give the same answer on the device and in the cloud's replay. One shared test like this proves it (Kent's question for the device side, `docs/project-overview.md` §17) *(inferred)* | Romulus contracts |
| 7 | Motion audit: tracking error, soft-limit overshoot and contacts for every gesture. Default MuJoCo tracked to 1.03° RMSE (max 4.43°), with 1.03° max overshoot and 0 solver warnings | The same measures work as health checks on the real arm: compare commanded with `present_position` from LeRobot's `sync_read`. A large gap means a stall, overload or bad calibration *(inferred)* | Health checks, tiers 1–3 (Ishaan) |
| 8 | Human review loop: a pass/fail checkbox per animation, exported to JSON. Editing a gesture voids its old pass but keeps the notes | The same shape as Romulus's autonomy ladder (suggest → approve → auto, demoted after a bad fix): a changed fix needs a fresh approval *(inferred)* | Romulus M3 |
| 9 | A kinematic lamp copy built into the page, with no install; it opens by double-click | A recorded-trajectory viewer on Kent's incident story page, or an offline **backup demo** if venue Wi-Fi or the arm fails *(inferred)* | Demo: "Backup demo video" |
| 10 | Small models on small GPUs: Whisper int8, three encoders and LFM2.5-1.2B fit in about 6.2 GB. Clip end to reply takes 1.65 s median on a 1070 Ti | Sizing for the Xavier or RunPod if the demo talks or listens. The Pi 5 has no CUDA GPU, so these wouldn't run there at this speed *(inferred)* | Models; Local model on the Pi |
| 11 | RunPod cost: a 7.4 h A40 benchmark for about $4.35 (`README.md`) | Real evidence that a few hours of RunPod benchmarking is cheap, for the sim or a VLA test (`docs/so101-research.md` §2) *(inferred)* | NVIDIA sim on RunPod |

## 3. What doesn't carry over

- **Different robot.**
  - The lamp is a fictional 5-joint URDF with no real hardware.
  - The SO-101 has 6 real Feetech STS3215 servos on a serial bus, driven through LeRobot 0.7 (`docs/project-overview.md` §5–§6).
  - The gestures' joint names and limits don't map across. The format and the controller pattern do.
- **No firmware.** Nothing in Human-Computer-Labs touches a microcontroller, so Olir's STM32 work is new ground (`firmware` branch in this repo).
- **MuJoCo version.** Human-Computer-Labs pins 3.14.0, while Kent pins 4.0.0 (Romulus notes in `CLAUDE.md`). Reusing its code means checking against 4.0, which shipped Nov 6.
- **Windows and an x86 GPU.** All of its numbers come from a Windows 11 desktop with a GTX 1070 Ti and an A40. None are from a Pi 5 or a Mac.
- **The ML route is about emotion, not the arm.** It matters here only for sizing models, or if the demo gets a voice.

## 4. Suggested next steps *(inferred)*

1. **Sim canary:** load `robotstudio_so101` in MuJoCo and run a few named moves headless. Reuse the lamp's benchmark harness (`lamp_sim/benchmark.py`) and its measures: tracking error, overshoot and real-time factor.
2. **Arm move library:** write 5–10 SO-101 moves as JSON in the gesture format (home, reach, grip, release, park). Expose them to Hermes as tools; free joint targets stay off the menu.
3. **Protocol:** copy the shape of `commands.schema.json` (version, id, ack, stop-and-hold) for the bench-to-arm link and the plugin's tool calls.
4. **Parity test:** add one shared fixture that runs `rules.evaluate` on the device side and in the cloud's replay, and checks the outputs are identical.
