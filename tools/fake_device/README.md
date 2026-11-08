# fake_device

Acts like N robot arms against the Romulus cloud's device API ([APIs §3](../../docs/cloud-platform-planning/cloud-platform-apis.md#3-device-facing-routes)). It depends only on `romulus-contracts` and `httpx` (plus `mcap` to write synthetic log files). That's deliberate: if the fake can do everything, the contracts are enough for the real device. It has three uses:

1. **The fake fleet** for the demo and the overnight run (`python -m fake_device`).
2. **The driver for the cloud's flow tests.** `FakeArm` takes any `httpx.Client`, including FastAPI's `TestClient`.
3. **A reference visor and outbox** for the Pi app (see the last section).

## Run it

```bash
uv sync --all-packages
uv run python -m fake_device --n 4 --url http://localhost:8000 --scenario all
```

| Flag | Default | What |
|---|---|---|
| `--n` | `4` | number of arms, named `sim-1` … `sim-N` |
| `--url` | `$CLOUD_URL`, else `http://localhost:8000` | cloud base URL (Compose sets `CLOUD_URL=http://api:8000`) |
| `--interval` | `10` | seconds between check-ins |
| `--scenario` | none | `rules`, `code`, `bad`, `bad-late`, `park` or `all`; repeatable; each runs once |
| `--canary` | `sim-2` | the device that runs `bad` and `bad-late` (matches `make seed`; the cloud decides the real canary role) |
| `--hold-after` | `1200` | seconds from an incident to its overlay's `held` report |
| `--state-dir` | `data/fake_device` | per device: `outbox/` and the visor's `visor.json`; survives restarts |
| `--seed` | `0` | seed for the metrics noise |
| `--ticks` | forever | stop after this many check-in rounds |

Without `--scenario` the arms just check in and follow their assignments (flow F1). `bad` and `bad-late` run on the canary, and the others are spread over the other devices.

## Modules

| Module | What |
|---|---|
| `client.py` | `DeviceClient(http, device_id)`: one method per route, contract models in and out, sha256 checks on every pull (`HashMismatch`), manifest and `harness.json` validation (`InvalidPackage`). No retries. |
| `outbox.py` | `Outbox(dir)`: on-disk queue of installs, incidents, overlay statuses and recordings. Retries connection errors, 5xx, 408 and 429 with backoff; moves any other 4xx (or an unreadable entry, or a recording whose blob is gone) to `dead/` with the error and carries on; strict order. Crash-safe (fsync + rename; blob written first, deleted last); entry names are unique, so several processes can add to one directory. |
| `visor.py` | `Visor(client, device, outbox, state_path).tick(now)`: check in, pull → verify → install → health check → keep or roll back, honor `frozen` and `drop_overlay`, drain the outbox. Keeps `last_good` and a pending-install marker in `state_path`, and finishes an install a crash interrupted before anything else. Talks to the device through a 5-method `Device` protocol. |
| `sim.py` | `SimDevice`: fake versions, overlay, health and metrics. Its reflexes run the real `romulus_contracts.evaluate` over the manifest's rules merged with the overlay. |
| `recordings.py` | Small synthetic evidence: a real MCAP log window, a JSONL agent trace, a placeholder `lerobot_v3_tar`. |
| `scenarios.py` | Scripted scenarios as lists of `Step(at_s, name, run)`. Nothing sleeps or reads the clock; you pass `now`. |
| `arm.py` | `FakeArm`: `SimDevice` + `DeviceClient` + `Outbox` + `Visor`, wired together. |
| `fake_cloud.py` | `FakeCloud`: an in-memory device API on `httpx.MockTransport`, for tests (ours, and the Pi app's). |
| `__main__.py` | The CLI. The only place with real sleeps. |

## Scenarios

| Name | What the device sends | Flows |
|---|---|---|
| `rules` | `camera_occluded` incident: `safe_pose`, an overlay with a `rules` diff raising `camera_occluded.below`, the signals, three recordings. `held` after `--hold-after`. | F2, F3, F7 |
| `code` | Fault **D1** (drop-on-proximity): overlay with the `code_patch` from the contracts example, `running.code_rev` as its base, `log_tail`, MCAP log window and JSONL agent trace. `held` later. | F11 |
| `bad` | Arms bad-candidate mode, then runs `rules`. The next firmware the device installs fails its health check, so the visor rolls back and reports `rolled_back` (the gate treats that as a canary failure). One version only. | F4 |
| `bad-late` | Same, but the next firmware passes its install check, then the device checks in `failing` and repeats `camera_occluded` once, until the gate assigns its last good version. | F4 |
| `park` | Fault **D3**-style `servo_absent`: `park`, `resolved: false`, `suspected_cause: hardware`, no overlay. The arm then checks in `failing`. | F6 |

## Using it from the cloud's flow tests

```python
from fastapi.testclient import TestClient
from fake_device.arm import FakeArm
from fake_device.scenarios import rules_patch

arm = FakeArm(TestClient(app), "sim-1", tmp_path / "sim-1")
arm.tick(now)  # check in, install v1, report installed
fault, held = rules_patch(arm)
fault.run(now)  # incident + recordings
held.run(now + timedelta(minutes=20))
```

`arm.device` exposes the simulated state (`bad_versions`, `bad_next`, `parked`, `overlay`) for tests that need a specific failure.

## Adopting this as the Pi visor and outbox (for Ishaan)

`client.py`, `outbox.py` and `visor.py` use only the standard library, `httpx` and `romulus-contracts`, and run on Python 3.11 (Raspberry Pi OS Bookworm). Copy the three files into the Pi app, or install this package.

1. **Implement `visor.Device`** over the real app. It's five methods:
   - `checkin_payload()`: build a `CheckIn` from what's running (`running`, `overlay_id`, `health`, `metrics`).
   - `install(kind, version, payload)`: `kind` is `firmware` (payload: the `FirmwareManifest`), `code` (the `.tar.gz` of `device/`) or `harness` (the harness `.tar.gz`). Unpack into `versions/<kind>/<version>/`, switch a `current` symlink, restart what needs restarting, and keep the previous version on disk.
   - `rollback(kind, version)`: switch `current` to `version`, the last good one, which is still on disk (`None` means nothing installed).
   - `health_check()`: run the tier 1–3 checks and return `ok`, `degraded` or `failing`. The install is kept when health is no worse than just before it.
   - `drop_overlay()`: remove the healer's local patch.
2. **Run the loop:** `Visor(DeviceClient(httpx.Client(base_url=URL, timeout=5), DEVICE_ID), device, Outbox("/var/lib/robot/outbox"), "/var/lib/robot/visor.json").tick(datetime.now(UTC))` every ~10 s. Keep `visor.json` on the same persistent disk as the versions.
3. **Have the healer write to the same outbox directory** (its own `Outbox` instance, even in another process, is fine; let only the visor call `drain`): `outbox.add_incident(...)`, then `outbox.add_recording(meta, data)` for each file, then `outbox.add_overlay_status(overlay_id, OverlayStatus(status="held"))` later. Make the incident and recording ids (`uuid.uuid4()`) when you create the report, not when you send it, so a resend is harmless.
4. **Test it without the cloud:** `FakeCloud.seeded()` gives you an in-memory cloud with firmware v1, harness v1 and code `c0de001` assigned to `sim-1…3`, and records every request.

What the visor does on its own: the hash check (`hash_mismatch`), content validation (`invalid`), the health check and rollback (D7); dropping the overlay once a new firmware or code version has passed its health check, and reporting that as overlay status `reverted` with `cause: superseded` (D7); keeping the overlay on rollback; after a crash or power cut mid-install, health-checking or rolling back to `last_good` on the next start; not retrying a version that failed until the cloud changes the assignment; still updating the harness when a firmware is rejected or frozen out; installing only a move back to the last good version while `frozen`; and when the cloud is unreachable, staying on the current version with reports queued (D8).
