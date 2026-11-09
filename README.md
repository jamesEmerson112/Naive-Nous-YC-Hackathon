<p align="center">
  <img src="docs/readme/hero.svg" width="100%" alt="Hardware Buddy: robots that fix themselves. A heartbeat line runs green, goes flat and red when the arm stops, Buddy heals it, and it runs green again.">
</p>

<p align="center">
  <b>A self-healing operating system for robots, from a company that mostly runs itself.</b><br>
  Built at Y Combinator for the Naïve Autonomous Company Hackathon, Nov 7 to 8, 2026.
</p>

<p align="center">
  <img alt="Built on the Naïve SDK" src="https://img.shields.io/badge/built_on-Na%C3%AFve_SDK-6D5DF6?style=flat-square">
  <img alt="Local model qwen3.5:4b, 3.3 GB" src="https://img.shields.io/badge/local_model-qwen3.5%3A4b_%C2%B7_3.3_GB-22B07D?style=flat-square">
  <img alt="Hardware SO-101, Pi 5, STM32" src="https://img.shields.io/badge/hardware-SO--101_%C2%B7_Pi_5_%C2%B7_STM32-F08A4B?style=flat-square">
  <img alt="Commander Hermes Agent" src="https://img.shields.io/badge/commander-Hermes_Agent-1F1B2E?style=flat-square">
</p>

<p align="center">
  <a href="#watch-the-demo"><b>Demo</b></a> ·
  <a href="#how-it-works"><b>How it works</b></a> ·
  <a href="#the-numbers"><b>Numbers</b></a> ·
  <a href="#see-it"><b>See it</b></a> ·
  <a href="#run-it"><b>Run it</b></a> ·
  <a href="#team"><b>Team</b></a>
</p>

## Watch the demo

[![Watch the Hardware Buddy demo on Google Drive](docs/readme/video.svg)][demo]

## The problem

The world's 500 biggest companies lose almost **$1.4 trillion a year** to unplanned downtime, about 11% of their revenue ([Siemens, 2024][siemens]). A lot of it is machines breaking where nobody can reach them. Andre saw it with satellites and a ground station on Guam: a short contact window, no internet, and a fault that costs millions because nobody can log in to fix it.

Robots have the same problem. When an arm crashes, someone walks over with a laptop and reads logs, and if the robot is offline it just stays broken. Our bet is that small local models can now do first-line repair on their own, and call a person only when the job needs hands.

## How it works

One heal loop, at three scales. A model proposes a fix that can be undone, plain code checks it, and the fix is kept or rolled back. No model ever sits in the real-time control loop.

### 1. The robot heals itself, offline

<p align="center">
  <img src="docs/readme/heal.svg" width="100%" alt="A real bench run with no internet and nobody touching it: bad firmware at 0 s, rolled back at 46 s, the job runs clean at 72 s, root cause found at 141 s.">
</p>

That's a real run on our bench ([`firmware/harness/RESULTS.md`](firmware/harness/RESULTS.md)). We flashed the sensor pod with v1.7, a release with a bug planted in its distance math, and the arm's obstacle reflex started firing at nothing. The healer, `qwen3.5:4b` running locally through Ollama, rolled the firmware back, re-ran the job cleanly, and traced the bug to one line:

```diff
@@ src/hcsr04.c:122 · the line the healer traced (v1.7 → v1.0) @@
-        out->dist_mm = (uint16_t)(us * 343u + 1000u) / 2000u;   /* us * 0.343 mm/us / 2, rounded */
+        out->dist_mm = (us * 343u + 1000u) / 2000u;   /* us * 0.343 mm/us / 2, rounded */
```

The cast cuts the value to 16 bits before the divide, so something 376 mm away reads as 15 mm.

<details>
<summary><b>Why it's safe to let a model flash firmware</b></summary>
<br>

- **Tools, not a shell.** The agent only gets the pod's 38 heal tools, and each one has a risk level: `read`, `runtime`, `code`, `flash` or `notify` ([`firmware/healer/`](firmware/healer/README.md)). The harness caps flashes at 3 per run and refuses paths outside the workspace ([`firmware/harness/`](firmware/harness/README.md)).
- **Every flash can undo itself.** `fw_flash` backs up the chip, programs it, verifies it, watches it, and rolls back on its own if the new image misbehaves.
- **Faults can't be planted from inside.** Fault injection is for the operator only, and `pod_cmd` refuses `inject`.
- **Software gets fixed, hardware gets parked.** For a hardware fault the arm is parked with torque off, a person is told, and no code changes.
- **No hints.** Before the bench run above, [`arm/hint_audit.py`](arm/hint_audit.py) checked everything the agent could read and found nothing that gives the answer away.
</details>

<details>
<summary><b>How we picked the model</b></summary>
<br>

We planted six faults in the robot app: a fake "safety" rule, a missing validity check, dropped servo retries, a config typo, a hang, and a missing servo that has to be parked, not patched. A hidden check the healer never sees scores each one. All runs used harness h3 on a MacBook with Ollama, capped at 30 steps and 15 minutes per task ([`docs/healer-benchmark-progress.md`](docs/healer-benchmark-progress.md), [`bench/`](bench/README.md)).

| Model | Passed | Live demo fault (D1) | Median time per task |
|---|---|---|---|
| **qwen3.5:4b, thinking on** | **10 / 12** | **2 / 2** | 105 s |
| qwen3.5:4b, thinking off | 7 / 10 | 0 / 2 | 63 s |
| qwen3:4b-instruct-2507 | 3 / 6 | 0 / 1 | 104 s |
| granite4.1:3b | 1 / 6 | 0 / 1 | 180 s |

At 3.3 GB, `qwen3.5:4b` fits on the edge box. For comparison, the big cloud agents (Naïve's `pi` and Hermes, both on gpt-6-sol) fixed 12 of 12 on a stand-in version of the same six faults ([`prototypes/fault-lab/`](prototypes/fault-lab/README.md)).
</details>

### 2. When it needs hands, a Naïve agent takes the case

<p align="center">
  <img src="docs/readme/technician.svg" width="100%" alt="A live run: the arm jams and the healer parks it, a Naïve agent diagnoses it, plain code emails a technician and stages the spare part, and a person approves the purchase. 38 s to a case, $0.08 per case, 1 human click.">
</p>

A robot can't pull out its own jam. When the healer parks the arm for a hardware fault, Romulus, our cloud, opens a work order and the **Naïve Technician** takes it ([`docs/naive-technician/`](docs/naive-technician/README.md)). A Naïve agent diagnoses the fault, plain code emails a technician what to check and stages the spare part, and a person approves the purchase. That's the one step we keep human.

### 3. Across the fleet, every fault happens once

<p align="center">
  <img src="docs/readme/fleet.svg" width="100%" alt="A live run: a fix fails on one robot, a Naïve agent writes a skill, the gate replays it and tries it on a simulated canary, and it is promoted to the fleet 57 s after the agent session opened, with 0 human clicks.">
</p>

Back online, every robot syncs its heal runs to Romulus. When one kind of fault keeps failing across the fleet, a Naïve agent reviews the cluster and proposes a skill or a rule. It ships only through the gate: a replay against past incidents, then a canary on a simulated arm ([`docs/recursive-self-improvement/`](docs/recursive-self-improvement/README.md)).

<details>
<summary><b>Where the agents' autonomy stops</b></summary>
<br>

| Change | Decided by | Human clicks |
|---|---|---|
| Dataset snapshots and training exports | plain code | 0 |
| A new rule or skill that doesn't touch safety | the gate: replay, simulated canary, promote or roll back | 0 |
| Anything that touches safety | its owner, with the measured evidence attached | 1 review |
| Spending money | a person | 1 click |

The same morning, an agent proposed a frozen-sensor rule. Offline it caught 120 of 120 sensor freezes with no new false alarms, but it touches the arm's start gate, so it was staged for Olir to review instead of shipping. A rolled-back proposal pauses reviews for that fault until a person clears it.

</details>

## The numbers

<p align="center">
  <img src="docs/readme/stats.svg" width="100%" alt="141 s for a real arm to heal its own firmware, offline. 10 of 12 planted-fault runs fixed by a 3.3 GB local model. 57 s from a Naïve agent session to a fleet-wide fix. $0.08 for a Naïve agent session that diagnoses a fault.">
</p>

<details>
<summary><b>Where each number comes from</b></summary>
<br>

| What | Number | Source |
|---|---|---|
| Real arm on bad firmware, healed with no internet and no humans | 141 s (rollback verified at 46 s, job re-run clean at 72 s) | [`firmware/harness/RESULTS.md`](firmware/harness/RESULTS.md) |
| Local model on the six-fault benchmark | 10 of 12 heals, `qwen3.5:4b`, 3.3 GB | [`docs/healer-benchmark-progress.md`](docs/healer-benchmark-progress.md) |
| Heal tools on the sensor pod | 38: 25 read, 4 runtime, 6 code, 2 flash, 1 notify | [`firmware/healer/tools.json`](firmware/healer/tools.json) |
| Naïve Technician, live rehearsal | 38 s to a case, one 27 s session, $0.08, 1 click | [`docs/naive-technician/README.md`](docs/naive-technician/README.md) |
| Fleet learning, live run | 57 s from the agent session to fleet-wide promotion, 3¢, 0 clicks | [`docs/recursive-self-improvement/runbook.md`](docs/recursive-self-improvement/runbook.md) |
| Frozen-sensor rule, offline probe | 0 → 120 of 120 freezes caught, 0 new false alarms | [`docs/recursive-self-improvement/runbook.md`](docs/recursive-self-improvement/runbook.md) |

</details>

## See it

<p align="center">
  <img src="prototypes/arm-sim/media/gentest/carry-20261108-135027.gif" width="480" alt="The real SO-101 at YC lifting three bags of chips">
  <br><sub>The real SO-101 at YC on Nov 8. An LLM planned 96 of its 102 tries that day.</sub>
</p>

Mission Control shows the whole loop live, on the MuJoCo twin of the arm or on the real one ([`prototypes/arm-sim/`](prototypes/arm-sim/README.md)). The recordings are folded below.

<details>
<summary><b>Mission Control: a wrong move healed in 28 s, and a dead elbow handed to a person</b></summary>
<br>

**Mission Control watches it live** ([`armsim.mission`](prototypes/arm-sim/README.md#mission-control-in-the-sim)). Below, two faults are planted from its presenter buttons: the arm heals the first by itself and hands the second to a person. Two more self-heals (a bad pod image and a stuck sensor) and a run with nothing broken are folded under them.

*A wrong move, healed in 28 s.* The block is bumped 30 mm, so the grip closes on nothing.
1. The arm reverses: jaws open, backed off.
2. The manager answers in 500 ms: relocate, then redo. (Here it's a scripted stand-in; the real one is a Hermes profile.) Plain code approves the plan, since the shift stays inside ±40 mm.
3. The wrist sensor finds the block 30 mm off, and the arm redoes the grip.

<img alt="Mission Control during a wrong move: WORKING at the grip, then WRONG MOVE: missed grasp with the strip wrong move, reversed, corrected, redone; the wrist sensor scans and finds the block +30 mm from where the arm expected it, the arm redoes the approach and grip, the header reads REDONE in 28 s, and the feed logs the order done in 46 s, healed grasp missed" src="prototypes/arm-sim/media/sim/mission-control-wrong-move.gif" width="800">

*A dead elbow, handed to a person.* The elbow servo dies, and then an order comes in.
1. The watchdog parks the arm.
2. The checked retry at home fails, so the job goes to a person with a work order.
3. Once a person fixes the elbow, the check at home passes.

<img alt="Mission Control with a dead elbow: the order starts, the header turns to PARKED: elbow stalled, then HEALING while the arm retries at home, then NEEDS A PERSON with the card in Blocked and a work order opened, and finally FIXED by a person, checked at home" src="prototypes/arm-sim/media/sim/mission-control-stall.gif" width="800">

<details><summary>A bad pod firmware image (07, the bench demo's fault), healed in 20.6 s: the distance reads 98 mm short of the arm's kinematics, the arm parks, and the pod's firmware is rolled back</summary>

<img alt="Mission Control with bad pod image 07: the sensor pod shows v1.7, bad image 07, with its distance math cut to 16 bits; at the order the header turns to PARKED: sensor offset, reads 98 mm short against the arm's kinematics, and podd flags us_mismatch; then HEALING while the pod rolls back to v1.1, known good, a check at home, HEALED in 20.6 s with the job resumed, and the order done in 34 s" src="prototypes/arm-sim/media/sim/mission-control-pod-image.gif" width="800">

In the sim, a stand-in does the pod healer's rollback ([story a](prototypes/arm-sim/README.md#everything-together-in-one-place)).
</details>

<details><summary>A stuck sensor, healed in 6.5 s: the distance reading freezes mid-job, the arm parks, and a scripted heal restarts the sensor through podd</summary>

<img alt="Mission Control with a stuck sensor: WORKING, then the stuck sensor is planted while the watchdog watches; during the release the header turns to PARKED: stuck sensor, the ultrasonic output frozen at 39 identical readings, then HEALING with the fallback script restarting the sensor, then HEALED in 6.5 s with no person needed, and the order finishes in 22 s" src="prototypes/arm-sim/media/sim/mission-control-stuck-sensor.gif" width="800">
</details>

<details><summary>The same screen when nothing is broken: an order runs itself in 15 s</summary>

<img alt="Mission Control during a pick order: the header goes from idle to queued to working, the twin goes home, reaches over the block and approaches it while the wrist distance counts down, the card moves to Done, and the commander feed logs each step" src="prototypes/arm-sim/media/sim/mission-control-pick.gif" width="800">
</details>

</details>

<details>
<summary><b>The simulator stories: a bad pod image, a stuck sensor, a dead elbow</b></summary>
<br>

**In the simulator, the arm heals itself.** Plain-code checks catch each fault and park the arm, with no model in the loop. Then the arm is healed or handed to a person ([the stories](prototypes/arm-sim/README.md#everything-together-in-one-place), in the MuJoCo twin of the SO-101):

<p>
  <img alt="Story a: a bad sensor-pod image; the distance disagrees with the arm's pose, the arm parks, and it resumes once the pod is healed" src="prototypes/arm-sim/media/sim/story-a.gif" width="270">
  <img alt="Story b: a stuck sensor; the arm parks, the sensor restarts through podd, and the job retries" src="prototypes/arm-sim/media/sim/story-b.gif" width="270">
  <img alt="The stall story: the elbow stops 25 degrees short of its target, the arm parks, and the incident goes to a person" src="prototypes/arm-sim/media/sim/story-stall.gif" width="270">
</p>

- **a:** a bad pod image (left)
- **b:** a stuck sensor (middle)
- **stall:** a dead elbow servo, handed to a person (right)

</details>

<details>
<summary><b>The real-arm experiment: an LLM planned 96 of 102 tries</b></summary>
<br>

**On the real arm, an LLM ran the experiments.** On Nov 8 an LLM planner, Astra through Hermes, planned 96 of 102 pick-and-place tries on the real SO-101 and judged them from the bench camera. The red bag never made it into the box. One try lifted three bags for 20.7 s, though, and the charts show why the rest slipped: [the full write-up](prototypes/arm-sim/README.md#the-real-arm-experiment-astra-picks-up-the-red-bag-nov-8).

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="prototypes/arm-sim/media/gentest/timeline-dark.svg">
  <img alt="Timeline of 102 tries on the real arm: grip time per try by recording, probes below, and seven changes to the method" src="prototypes/arm-sim/media/gentest/timeline-light.svg">
</picture>

<p>
  <img alt="The 13:50 try: the jaws close on the pile and the arm lifts three bags" src="prototypes/arm-sim/media/gentest/carry-20261108-135027.gif" width="400">
  <img alt="The 14:00 try, frame by frame: the jaws close on the red bag, come up empty, and the arm swings over the box anyway" src="prototypes/arm-sim/media/gentest/try-140030.gif" width="400">
</p>

- **The 13:50 try (left):** three bags held for 20.7 s.
- **The 14:00 try (right):** frame by frame; the bag slips out at the lift.

</details>

<details>
<summary><b>Andre's pitch animation</b></summary>
<br>

**The pitch.** Hardware Buddy: self-healing hardware for robotics and manufacturing. This is Andre's pitch animation ([`pitch/`](pitch/storyboard.md)), the part before the live demo:

<img alt="The Hardware Buddy pitch: $1.4 trillion lost every year to unplanned downtime; a site out of reach with no link; Hardware Buddy, local and cloud, syncing when online; today's fault-ticket-technician loop; the live demo, a chip-packing line; hardware has to get smarter" src="docs/media/hardware-buddy-pitch.gif" width="640">

</details>

## How the pieces fit

<p align="center">
  <img src="docs/readme/stack.svg" width="100%" alt="Three layers. On the bench, offline: the sensor pod and its local healer, the SO-101 arm with a watchdog, and Hermes running Naïve job cards. Incidents and heal runs go up to Romulus, our cloud; work orders and failure clusters go up to Naïve agents. Skills and rules come back down through the gate, and new versions go back to the bench.">
</p>

In the simulator, one job ran end to end with nobody typing after the order. A card on the Naïve board went to Hermes, which drives the arm one named skill at a time. When the wrist sensor froze, the watchdog parked the arm. Hermes restarted the sensor, finished the job and closed the card, and Romulus logged the incident.

<details>
<summary><b>Three animated loops: that job, a real-arm try planned by an LLM, and fleet learning</b></summary>
<br>

**One job, end to end, with nobody typing after the order.** An order becomes a card on the Naïve board, and the dispatcher hands it to Hermes, which runs the arm one skill at a time through the [`so101-arm` plugin](prototypes/arm-sim/hermes-plugin/so101-arm/__init__.py) and never sends joint angles. The arm's readings go to plain code next to it:
1. When the wrist distance freezes, the watchdog parks the arm, and a guard refuses Hermes's next move.
2. Hermes reads the arm and calls `arm_restart_sensor`, and the watchdog checks the fix at home.
3. Hermes finishes the job and closes the card, and Romulus gets the incident with its log window.

<img alt="Animated diagram of one self-healing job, in four lanes: the arm, plain code next to it, the agents and Romulus. An ARM JOB card lands on the Naïve board, the dispatcher claims it and Hermes takes it; Hermes calls arm_go_home and arm_reach while the wrist distance streams through podd; the reading sticks at 130 mm while the prediction moves, the watchdog parks the arm and the check-ins turn to health failing; Hermes gets parked back, reads arm_status and calls arm_restart_sensor; podd turns the sensor off and on, the watchdog checks it at home and the arm is healed; Hermes redoes reach, approach, grip, lift, release and go_home, posts RESULT: and the card moves to done; Romulus records the us_stuck incident as resolved with its log window, and no work order is needed. A badge reads people after the order: 0" src="prototypes/arm-sim/media/autonomous-loop.gif" width="800">

It's a drawing ([`armsim/loopgif.py`](prototypes/arm-sim/armsim/loopgif.py)) of what happened in real runs:
- **In the sim,** a Naïve card drove Hermes through a stuck sensor: Hermes healed it, finished the job, commented `RESULT:` and the card went to done ([`MORNING.md`](prototypes/arm-sim/MORNING.md), story `b --hermes`). The readings on screen are rows from the sim's stuck-sensor packet ([`esc-us_stuck.json`](prototypes/arm-sim/hermes-manager/samples/esc-us_stuck.json)).
- **On the real arm,** a Naïve card + Hermes job ran with no fault: done 107 s after the claim, 25 tool calls, nobody typing ([`HANDOFF-LOCAL.md`](prototypes/arm-sim/HANDOFF-LOCAL.md), Sun 10:27 AM).
- **Romulus's database from the hackathon** (kept local, like the rest of the data) holds 20 incidents. 17 were resolved with no person: distance faults on the sim twin (stuck, frozen, mismatched), and bad-image and link faults on the sensor pod. The other 3 were the sim twin's dead elbow, each handed to a person as a `needs_human` work order.

**In the Nov 8 experiment on the real arm, an LLM decided the tries.** Astra (`gpt-6-astra` through Hermes) planned 96 of the 102 tries from the try log and the bench camera, its only eye. Here is one of them, the 13:50 three-bag carry, with its values from the try log ([the write-up](prototypes/arm-sim/README.md#the-real-arm-experiment-astra-picks-up-the-red-bag-nov-8)):

<img alt="Animated diagram of one real-arm try in four lanes: the bench, the Pi, the Mac and the agent. Astra via Hermes reads the try log and the camera frames with their labelled grid, and writes the next test as JSON: recording_2, pan -4, speed 0.5, planned in 24.1 s. gentest on the Mac checks pan and speed and starts the try on the Pi over SSH. The arm replays recording_2, the jaws close on the pile at 16.7 s and squeeze to 99%, and three bags stay in the jaws for 20.7 s until the gripper reports alarm 32. The Pi writes the result to the try log, and Astra judges the try UNSURE and plans a probe at pan -2, which it calls GO. A badge reads Astra planned 96/102" src="prototypes/arm-sim/media/experiment-loop.gif" width="800">

**Fleet learning closes the loop with a Naïve agent.** Romulus labels every heal run by what checked it. When a fault has no working fix in the fleet, Romulus opens a Naïve agent session that proposes a skill or a rule, and the gate ships it, except safety code, which waits for its engineer. This is the live run on Nov 8 at 10:04 AM, with its measured times ([`runbook.md`](docs/recursive-self-improvement/runbook.md)). Both faults in it were seeded in sim, the real arm-1 stayed queued, and nothing was trained:

<img alt="Animated diagram of fleet learning's live run, in four lanes: the fleet, Romulus, Naïve and a person. Romulus labels 9 heal runs, 4 of them by a check (2 passed, 2 failed). A servo-read fix failed on sim-1 and held on sim-3, so Romulus opens a session for the Naïve agent fleet-doctor, which writes the lesson as a skill in 22 s for $0.030. The gate builds harness v2 and runs it on the canary sim-2 for 30 s. A second session proposes a frozen-sensor rule; it touches safety code, so it is staged for Olir with the probe's numbers: 120 of 120 freezes caught, no false alarms on 410 healthy windows. The skill is promoted 57 s after the first session, the sims run harness v2, the real arm-1 is queued, Naïve Skills gets healer-playbook v2, and the board gets a comment: 0 human clicks" src="prototypes/arm-sim/media/fleet-loop.gif" width="800">

</details>

<details>
<summary><b>How data moves between the layers</b></summary>
<br>

- **Every device speaks one contract**, the device API in [`packages/contracts`](packages/contracts/README.md) ([APIs doc](docs/cloud-platform-planning/cloud-platform-apis.md)): check in every 10 s, report each incident with what the healer did, upload evidence, pull assigned versions.
- **The sensor pod gets there through the edge bridge.** Olir's healer records everything locally and never calls the cloud; [`tools/edge_bridge`](tools/edge_bridge/README.md) reads those records on the Pi and reports them, so the healer needs no changes (decision D29). It's tested against the real cloud app, and the arm-sim demo starts it beside Olir's real `podd` ([`demo/up.sh`](prototypes/arm-sim/demo/up.sh)). Nothing in the repo records a run on the bench Pi *(inferred)*.
- **The cloud closes the loop**: fixes that hold become firmware and harness versions, replayed against past incidents and tried on a simulated canary before the fleet gets them ([`cloud/README.md`](cloud/README.md)).
- **The arm-sim prototype runs the whole loop on one laptop** ([`prototypes/arm-sim`](prototypes/arm-sim/README.md)); the sim and Mission Control GIFs above come from it. It has a MuJoCo twin of the SO-101 with its watchdog, a virtual sensor pod read by Olir's real `podd`, Mission Control, a dispatcher that takes orders from a board (a local file or a Naïve card), and a Hermes manager. Its arm reporter checks in with Romulus like any device, and the same code drives the real arm (`--arm real --pod real`).

</details>

## What's where

| Folder | What's in it | Who |
|---|---|---|
| [`firmware/`](firmware/README.md) | The sensor pod firmware and its healer: podd, the 38 heal tools, the agent loop, and [eight known-bad images](firmware/demo/bad-images/) | Olir, Ishaan |
| [`arm/`](arm/README.md) | The real SO-101: setup, taught recordings, ultrasonic-gated replay, and the bench heal test | Olir, Andre |
| [`device/`](device/README.md), [`harness/`](harness/), [`bench/`](bench/README.md) | The arm healer, its harnesses, and the benchmark that picked the model | Ishaan |
| [`server/`](server/README.md) | Serves the healer's model from a laptop to the Pi | Olir |
| [`cloud/`](cloud/README.md) | Romulus: device API, gate and canary, timeline, work orders, the Technician, fleet learning | Kent |
| [`packages/contracts/`](packages/contracts/README.md) | The device ↔ cloud payloads | Kent |
| [`tools/`](tools/edge_bridge/README.md) | The edge bridge, and fake arms for development | Kent |
| [`naive/`](naive/README.md) | The Naïve TypeScript SDK client | Olir |
| [`prototypes/`](prototypes/) | [`arm-sim`](prototypes/arm-sim/README.md) (the twin, Mission Control, the Hermes plugin, the Astra experiment), the [fault lab](prototypes/fault-lab/README.md), and a [Naïve console](prototypes/naive-console/README.md) | James |
| [`hub/`](hub/) | The Arm Company Stack Map | James |
| [`pitch/`](pitch/storyboard.md) | The pitch animations | Andre |
| [`docs/`](docs/) | Research, the [fault catalog](docs/fault-catalog.md), and the plans | everyone |
| [`docs/readme/`](docs/readme/build.py) | This page's art, drawn by one standard-library script | Ishaan |

## Run it

Needs Docker and [uv](https://docs.astral.sh/uv/).

```bash
uv sync --all-packages
# the cloud on :8000, with arm-1 and sim-1…3
make up && make seed
# fake arms through every scripted fault
uv run python -m fake_device --n 3 --scenario all
# incident timeline, fleet, kill switch
open http://localhost:8000/
# every Python test, on a throwaway Postgres
make test
```

| To run | Start here |
|---|---|
| The pod healer on the bench (Pi, pod, local model) | [`firmware/harness/HANDOFF.md`](firmware/harness/HANDOFF.md) |
| The model relay from a laptop to the Pi | [`server/README.md`](server/README.md) |
| Fleet learning, live | [`docs/recursive-self-improvement/runbook.md`](docs/recursive-self-improvement/runbook.md) |
| The Naïve Technician, live | [`docs/naive-technician/runbook.md`](docs/naive-technician/runbook.md) |
| The arm twin, Mission Control and the Hermes plugin | [`prototypes/arm-sim/README.md`](prototypes/arm-sim/README.md) |
| The healer benchmark | [`bench/README.md`](bench/README.md) |
| The overnight unattended run | [`cloud/OVERNIGHT.md`](cloud/OVERNIGHT.md) |

With the pod on the bench, start podd (`firmware/healer/bin/podd`) on the Pi, then the bridge: `uv run python -m edge_bridge --url http://<laptop-LAN-IP>:8000` ([setup](tools/edge_bridge/README.md#run-it-on-the-pi)). To redraw the art on this page, run `python3 docs/readme/build.py`.

<details>
<summary><b>The arm in the simulator, with Mission Control</b></summary>
<br>

**The arm in the simulator, with Mission Control** (no hardware, no Docker; this is how the Mission Control GIFs above were made):

```bash
cd prototypes/arm-sim
./setup.sh                                                          # MuJoCo Menagerie's SO-101 model into vendor/
uv venv --python 3.12 .venv && uv pip install --python .venv/bin/python -r requirements.txt
PYTHONPATH=. .venv/bin/python -m armsim poses                       # the sim's taught poses
PYTHONPATH=. .venv/bin/python -m armsim.host --realtime --pod sim --var "${TMPDIR:-/tmp}/armsim-pod" &   # the sim arm, paced to wall-clock time, with a virtual sensor pod and Olir's podd
PYTHONPATH=. .venv/bin/python -m armsim.mission --board file &      # Mission Control on http://localhost:8765
PYTHONPATH=. .venv/bin/python -m armsim.dispatch --board file --exec scripted &   # runs the orders
```

In Mission Control:
- **1** places an order.
- **3** plants a stuck sensor, and **4** a bad pod image (07). Both use the virtual sensor pod (`--pod sim` above). Keep its `--var` folder's path short, since podd's socket path has a length limit.
- **5** plants a dead elbow.
- **6** bumps the block, so the next grasp misses. The dispatcher heals that only when started with `--manager fake --wrong-action redo`.
- **7** is "a person fixed it".
- **V** switches between the twin and the split view.

More in [`prototypes/arm-sim/README.md`](prototypes/arm-sim/README.md).

</details>

## What's real, and what's next

- **Real hardware.** The firmware heal ran on the real SO-101 and STM32 pod. The model ran on a laptop wired straight to the Pi with no internet, because the Pi 5 is too slow for it ([`server/`](server/README.md)).
- **Real Naïve.** The Technician and fleet-learning runs used live Naïve agent sessions, a real email and a real store cart, on incidents from our simulated fleet. The real arm was queued for the fleet update, not updated.
- **The twin and the real arm.** The Mission Control heals ran on the MuJoCo twin. On the real arm, Hermes ran a Naïve card end to end with no fault, and an LLM planned 96 of 102 pick-and-place tries.
- **Next.** Put the model on the robot itself (our Jetson AGX Xavier), run fleet learning on real hardware, and ship the Hermes plugin for the Hermes Agent Plugin Hackathon.

## Team

| Who | What they built |
|---|---|
| **Andre** | Hardware: assembling and calibrating the arm, LeRobot teleop and recorded motions. The pitch animations, and the Guam story |
| **Ishaan** | The arm healer and the benchmark that picked the model, the pod healer's agent loop and blind eval, the demo video, this README |
| **James** | The MuJoCo twin and Mission Control, the Hermes `so101-arm` plugin, the watchdog and fault factory, the Nov 8 real-arm experiment with Astra, the loop animations, the stack map |
| **Kent** | Romulus: the device API, gate and canary, datasets and timeline, the Naïve Technician, fleet learning, the edge bridge, Fleet Doctor |
| **Olir** | The STM32 sensor pod, podd and the 38 heal tools, the known-bad firmware images, the model relay, the Naïve SDK client, and the real bench heal |

How we work together, and the team's last status (Nov 9): [`CLAUDE.md`](CLAUDE.md). The whole story, start to finish: [`docs/project-overview.md`](docs/project-overview.md).

<p align="center">
  <br>
  <img src="docs/readme/buddy.svg" width="88" alt="The Buddy chip">
  <br>
  <b>Software is automating everything. Hardware has to get smarter.</b>
</p>

[demo]: https://drive.google.com/file/d/17YgE5M9upMFbYnxRo5n2TOmJe6rmUJpD/view?usp=sharing
[siemens]: https://assets.new.siemens.com/siemens/assets/api/uuid:1b43afb5-2d07-47f7-9eb7-893fe7d0bc59/TCOD-2024_original.pdf
