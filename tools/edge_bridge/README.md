# edge_bridge

Runs on the Pi next to the sensor pod's healer and reports what it already records to the Romulus cloud: check-ins with the pod's health, one incident per fault with what the healer did, the logs around each fault as evidence, and a readable report of each heal-agent run. The healer itself is unchanged.

## Why it exists

The pod healer (Olir, [`firmware/healer`](../../firmware/healer/README.md)) detects, diagnoses and fixes faults on the STM32 sensor pod, and it records everything locally: podd's JSONL logs and incidents, an audit of every heal-tool call, verified flashes, requests for a person. None of it reached the cloud, and the cloud contract ([APIs doc](../../docs/cloud-platform-planning/cloud-platform-apis.md)) was still waiting on the device side to adopt it.

Rather than ask the device team for more work, the bridge adapts on our side (decision **D29**, [decision log](../../docs/cloud-platform-planning/cloud-platform-decisions.md)). It reads the healer's own records and maps them onto contract v0.1. No contract change was needed, because recording kinds and formats are open strings and every payload keeps extra fields.

```
 STM32 pod ──serial──► podd ──► var/logs/*.jsonl, var/deployments.jsonl, var/notifications.jsonl
                        ▲        var/images/<CRC>/, var/workspace/pod (git)
                 heal tools ◄── the healer's agent                    │ read only
                                                                      ▼
                                              edge_bridge (this) ──► outbox/ ──HTTP──► Romulus cloud
                                                    ▲                                    │
                                     podd socket: status, stats                timeline, datasets,
                                                                               Naïve work orders
```

## What reaches the cloud

| The healer recorded | The cloud gets |
|---|---|
| podd `status`: link state, open incidents, last boot | a **check-in** every 10 s: `kind: real`, `health` (`failing` if podd is down, the link is down, silent or garbled, or a critical incident is open; `degraded` for a warning), `running.mcu_build` such as `v1.1@32ACFDAF`, and `pod_*` metrics |
| the podd incidents of one fault (scenario 01 opens `crash`, `reboot_loop` and `safe_mode` together) | **one incident**, once the fault is over (below), named after its most severe incident |
| heal-tool calls that change something, agent notes | `agent.actions` and `agent.reasoning` |
| a verified flash, a runtime command the pod accepted, a request for a person | the `outcome`: `fw_flash` (`fix-code`) or `fw_rollback` (`ops`), `pod_cmd: …` (`ops`), or `suspected_cause: hardware` (unless the healer's own fix then resolved it). Still unresolved with a hardware cause, the cloud **opens a Naïve work order** |
| the flashed image's source diff, the workspace commits | `firmware_patch`, **as evidence only** (D29): never an `overlay`, so the gate and the coding agent never try to roll a pod patch out to the arms |
| telemetry, health and event logs from 30 s before to 10 s after | a **`log_window`** recording (JSONL) |
| the heal-tool calls and notes during the fault | an **`agent_trace`** recording (JSONL: tool calls and arguments; no model messages) |
| a heal-agent run in `firmware/harness/runs/<run>/` (`transcript.jsonl`, and `score.json` once `arm/heal_test.py` has checked the bench) | an **agent run report** (APIs §3.11, `PUT /v1/devices/pod-1/agent-runs/<run>`): the outcome (fixed, broken or unknown, and what that rests on), every firmware image the run touched, requests for a person or a stronger model, a write-up, and every step with the model's thinking. Sent while the run goes on and again whenever its files change. Tied to the run's incident through podd's incident ids at its start; a `--sim` rehearsal is tied to none and shows on the dashboard's home page instead |

The exact rules are in one place, the docstring of [`translate.py`](src/edge_bridge/translate.py); for agent runs, the docstring of [`agent_runs.py`](src/edge_bridge/agent_runs.py).

**When a fault is reported.** The cloud's incidents can't be amended once sent (idempotent on `incident_id`), so the bridge waits until the story is over ([`faults.py`](src/edge_bridge/faults.py)):
- **resolved:** every podd incident resolved, and 15 s passed with no new heal-tool call;
- **handed off:** still open, the healer asked a person to act (`notify_human` with instructions), and 60 s passed quietly (`--quiet`);
- **timed out:** still open after 15 min (`--max-open`).

A flash in progress (podd's `flash_start` without its `flash_done`) holds the report, because a heal tool's audit record is written only when the call returns. Once ready, a fault is final: anything later belongs to the next fault or to none, so the report is the same however late the bridge ticks. The incident's MCU build is the one running when the fault opened, read from the pod's boot records in time order.

**Agent runs are different:** a run report can change, so it isn't held back. Each tick (after a successful check-in), every run whose `transcript.jsonl` or `score.json` changed since the cloud last accepted it is rebuilt whole and sent again. It skips the outbox, since an unaccepted report is simply rebuilt next tick. Runs that started before the bridge's first start (minus `--backfill-hours`) are left alone. When the bridge can't name the run's incident (an older transcript, or a fault it didn't see), it sends a time window and the cloud ties the incident that occurred in it, even one that arrives later.

## Run it on the Pi

```bash
# once, from any machine that reaches the cloud: tell the cloud the pod isn't an arm
curl -X POST http://<laptop-LAN-IP>:8000/v1/devices -H 'Content-Type: application/json' \
  -d '{"id": "pod-1", "kind": "real", "device_class": "sensor_pod", "by": "kent"}'

# on the Pi, from the repo root, with podd running (firmware/healer/bin/podd)
uv sync --all-packages      # Python 3.12 (uv python install 3.12 if the OS has 3.11)
uv run python -m edge_bridge --url http://<laptop-LAN-IP>:8000 --device-id pod-1
```

| Flag | Default | What |
|---|---|---|
| `--healer` | `$HEALER_DIR`, else `firmware/healer` | the healer folder (`tools.json`, `var/`) |
| `--var` | `$HEALER_VAR`, else `<healer>/var` | the healer's `var/`, as in `healer/config.py` |
| `--url` | `$CLOUD_URL`, else `http://localhost:8000` | the cloud |
| `--device-id` | `$POD_DEVICE_ID`, else `pod-1` | the pod's id in the cloud |
| `--runs` | `$HARNESS_RUNS`, else `<healer>/../harness/runs` | the heal agent's run folders |
| `--state-dir` | `data/edge_bridge` | `<dir>/<device-id>/state.json` (cursors, faults, and which run reports the cloud accepted) and `outbox/`; survives restarts |
| `--backfill-hours` | `0` | on the first start, also report faults from this many hours back |
| `--quiet`, `--max-open` | `60`, `900` | the handed-off and timed-out waits above, in seconds |

Skipping the `curl` still works: the pod registers itself as real hardware on its first check-in, but as an arm (`so101`), and the bridge warns once that it was assigned arm firmware.

**Safe to run alongside the healer.** The bridge only reads files (the healer's `var/` and the harness's run folders) and asks podd for `status` and `stats`. It never calls a heal tool and never opens the serial port. It never installs anything either; the cloud assigns sensor pods no firmware (the arm healer's harness may still be assigned, because harness versions aren't per device class; the bridge ignores it).

**Offline and restarts.** Reports go through `fake_device`'s on-disk outbox (D8): queued in order, retried with backoff, never lost. Ids are UUIDv5s of podd's own ids, so a report sent twice after a crash is harmless.

## Code

| Module | What | Pure? |
|---|---|---|
| [`podd.py`](src/edge_bridge/podd.py) | reads the healer: JSONL files (with cursors and rotated logs), podd's socket, the image registry, the workspace git log, the harness's run folders | I/O, read only |
| [`faults.py`](src/edge_bridge/faults.py) | groups podd incidents into faults; decides when one is ready | yes |
| [`translate.py`](src/edge_bridge/translate.py) | healer records → `CheckIn`, `IncidentReport`, `RecordingMeta` | yes |
| [`agent_runs.py`](src/edge_bridge/agent_runs.py) | one run's `transcript.jsonl` + `score.json` → `AgentRunReport`: outcome, firmware history, help requests, write-up, steps | yes |
| [`bridge.py`](src/edge_bridge/bridge.py) | one `tick(now)`: read, group, report, check in, drain the outbox, send changed run reports | wires the above |
| [`fake_healer.py`](src/edge_bridge/fake_healer.py) | a healer `var/` written the way Olir's code writes it, a fake podd socket, scenarios 01 and 06, and harness run folders; for tests | test helper |
| [`__main__.py`](src/edge_bridge/__main__.py) | the CLI; the only real sleeps | |

It talks to the cloud only through `fake_device`'s `DeviceClient` and `Outbox`, the reference device client ([`tools/fake_device`](../fake_device/README.md)), so the contract stays the single interface.

**Tests:** `uv run pytest tools/edge_bridge` (no database), and `cloud/tests/flows/test_interop_edge_bridge.py` runs the bridge against the real app: the pod registers, scenario 01 lands as one incident with both recordings in its story, and scenario 06 becomes a work order. `cloud/tests/flows/test_f13_agent_runs.py` covers the cloud side of agent runs: upserts, links by id and by time window, events and pages.

## Not done yet

- **Ishaan's arm healer.** Its code isn't in the repo yet. His docs say its report follows our contract (`docs/project-overview.md` §8.1), so it may need no bridge at all. One sample incident folder from it would settle that.
- **`DeviceClient` and `Outbox` still live in `fake_device`.** Moving them into a small device-client package would be tidier, but it waits until the Fleet Doctor work in `fake_device` lands.
- **A podd restart** forgets its open incidents without writing resolve records. Their fault is then reported as timed out after `--max-open`.
