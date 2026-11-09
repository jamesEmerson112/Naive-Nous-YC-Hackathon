# Naive Nous YC Hackathon

A robotic-arm company that heals itself. When something breaks on a robot (an SO-101 arm on a Raspberry Pi 5, or the STM32 sensor pod next to it), a small healer on the device diagnoses and fixes it, or parks the hardware and asks for a person. The **Romulus** cloud collects every incident, turns fixes that hold into new versions for the whole fleet behind a replay and a canary, and hands hardware faults to Naïve agents as work orders. Built for the Naïve Autonomous Company Hackathon and the Hermes Agent Plugin Hackathon ([`docs/hackathons.md`](docs/hackathons.md)).

Team context, working rules and live status: [`CLAUDE.md`](CLAUDE.md). The whole story: [`docs/project-overview.md`](docs/project-overview.md).

## On the real arm
On Nov 8 an LLM planner, Astra through Hermes, ran 102 pick-and-place tries on the real SO-101, judging each from the bench camera. The red bag never made it into the box. One try lifted three bags for 20.7 s, though, and the charts show why the rest slipped: [the full write-up](prototypes/arm-sim/README.md#the-real-arm-experiment-astra-picks-up-the-red-bag-nov-8).

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="prototypes/arm-sim/media/gentest/timeline-dark.svg">
  <img alt="Timeline of 102 tries on the real arm: grip time per try by recording, probes below, and seven changes to the method" src="prototypes/arm-sim/media/gentest/timeline-light.svg">
</picture>

<img alt="The 13:50 try: the jaws close on the pile and the arm lifts three bags" src="prototypes/arm-sim/media/gentest/carry-20261108-135027.gif" width="400">

## How the pieces fit

```
 on the bench (Pi 5)                                    on the laptop (Docker)           online

 SO-101 arm ── arm healer (Ishaan; device/, not in the repo yet) ─┐
                                                                  │  device API
 STM32 pod ── pod healer (Olir; firmware/healer) ── edge bridge ──┼─ (packages/contracts) ─► Romulus cloud (cloud/) ──► Naïve
                                                 (tools/edge_bridge)                         timeline, gate,         work orders,
 fake arms (tools/fake_device): development, tests, overnight run ┘                          datasets, kill switch   agents
```

- **Every device speaks one contract**, the device API in [`packages/contracts`](packages/contracts/README.md) ([APIs doc](docs/cloud-platform-planning/cloud-platform-apis.md)): check in every 10 s, report each incident with what the healer did, upload evidence, pull assigned versions.
- **The sensor pod gets there through the edge bridge.** Olir's healer records everything locally and never calls the cloud; [`tools/edge_bridge`](tools/edge_bridge/README.md) reads those records on the Pi and reports them, so the healer needs no changes (decision D29). It's tested against the real cloud app but hasn't run on the bench yet.
- **The cloud closes the loop**: fixes that hold become firmware and harness versions, replayed against past incidents and tried on a simulated canary before the fleet gets them ([`cloud/README.md`](cloud/README.md)).

## What's where

| Folder | What | Owner |
|---|---|---|
| [`cloud/`](cloud/README.md) | Romulus cloud platform: device API, registry, batch job, gate, telemetry and datasets, work orders, timeline | Kent |
| [`packages/contracts/`](packages/contracts/README.md) | `romulus-contracts`: the device ↔ cloud payloads, shared by both sides | Kent |
| [`tools/edge_bridge/`](tools/edge_bridge/README.md) | Runs next to the pod healer on the Pi and reports its incidents, logs and agent actions to the cloud | Kent |
| [`tools/fake_device/`](tools/fake_device/README.md) | N fake arms against the cloud, and the reference device client and outbox | Kent |
| [`firmware/`](firmware/README.md) | STM32 sensor pod: firmware with self-diagnostics, and the pod healer (podd, triage rules, 38 heal tools, six known-bad demo images) | Olir |
| [`hub/`](hub/) | Source of the Arm Company Stack Map artifact | James |
| [`prototypes/`](prototypes/) | The fault lab (can pi and Hermes fix the healer's faults?) and a Naïve console | James |
| [`docs/`](docs/) | Research, the fault catalog, cloud planning, sponsor notes, raw doc dumps | everyone |

The arm healer and its benchmark (`device/`, `harness/`, `bench/`) are Ishaan's and haven't landed yet ([`docs/healer-benchmark-progress.md`](docs/healer-benchmark-progress.md)).

## Run it

Needs Docker and [uv](https://docs.astral.sh/uv/).

```bash
uv sync --all-packages
make up && make seed                                    # the cloud on :8000, with arm-1 and sim-1…3
uv run python -m fake_device --n 3 --scenario all       # fake arms through every scripted fault
open http://localhost:8000/                             # incident timeline, fleet, kill switch
make test                                               # every Python test (throwaway Postgres)
```

With the pod on the bench, start podd (`firmware/healer/bin/podd`) on the Pi, then the bridge: `uv run python -m edge_bridge --url http://<laptop-LAN-IP>:8000` ([setup](tools/edge_bridge/README.md#run-it-on-the-pi)).
