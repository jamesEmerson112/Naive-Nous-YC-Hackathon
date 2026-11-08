# romulus-contracts

The device ↔ cloud contract for Romulus, as a small Python package: Pydantic v2 models for every payload, the shared reflex-rules function, and example payloads. The cloud platform, the fake device and the real Pi visor/healer all import it, so both sides parse the same shapes.

- **Contract version:** `0.1`, **draft** until the device side agrees (handoff I0).
- **Authority:** [`docs/cloud-platform-planning/cloud-platform-apis.md`](../../docs/cloud-platform-planning/cloud-platform-apis.md) §2–3. If this package and that doc disagree, the doc wins and this package is the bug.
- **Dependencies:** `pydantic>=2.9` only. Python 3.11+ (so Raspberry Pi OS Bookworm's 3.11 works).

## Install

On the Pi, straight from GitHub:

```bash
pip install "romulus-contracts @ git+https://github.com/jamesEmerson112/Naive-Nous-YC-Hackathon.git@main#subdirectory=packages/contracts"
```

Pin a commit instead of `main` once v0.1 is agreed. From a checkout of this repo: `pip install ./packages/contracts` (or `pip install -e` while iterating). In the uv workspace it is already installed: `uv sync --all-packages`.

## What's inside

| Module | What |
|---|---|
| `device` | `CheckIn`, `CheckInResponse`, `InstallResult` |
| `incident` | `IncidentReport` (+ `Agent`, `Overlay`, `Outcome`), `IncidentAccepted`, `OverlayStatus` |
| `manifest` | `FirmwareManifest`, `FirmwareResponse`, `HarnessMeta`, `manifest_sha256()` |
| `telemetry` | `RecordingMeta`, `RecordingAccepted`, `RecordingKind`, `FLEET_FEATURES` (dataset schema v0.1) |
| `codepatch` | `validate_code_patch()`, `patch_paths()` for the `code_patch` format |
| `rules` | `evaluate(rules, signals)`, `replay_passes()`: the reflex function shared by device and gate |
| `version` | `CONTRACT_VERSION`, `SUPPORTED_VERSIONS`, `is_supported()` |

```python
from romulus_contracts import CheckIn, IncidentReport, evaluate

checkin = CheckIn.model_validate(payload)  # raises pydantic.ValidationError if wrong
body = checkin.model_dump(mode="json")  # what to POST
fired = evaluate(manifest.rules, {"brightness": [0.41, 0.18]})  # -> [Fired(rule=..., action=...)]
```

JSON Schema for every device-facing model, for validating without Python or constraining a small model's output:

```bash
python -m romulus_contracts.schema            # all models
python -m romulus_contracts.schema CheckIn    # one model
```

**Tolerance rules.** Models for payloads the device sends keep unknown fields (`extra="allow"`), and the cloud stores every payload raw. Add fields freely; they won't be rejected and they won't be lost. Models for cloud responses ignore unknown fields, so a newer cloud won't break an older device. Timestamps without an offset are read as UTC. Recording `kind`/`format`, `metrics`, signal names, rule actions and incident `kind` are open strings. Fixed sets: `health` (`ok`/`degraded`/`failing`), install `type` and `result`, overlay `status`, `suspected_cause`.

## Routes (device-facing)

Base URL `http://<laptop-LAN-IP>:8000`, JSON, no auth on the bench network. Full detail in [APIs §3](../../docs/cloud-platform-planning/cloud-platform-apis.md#3-device-facing-routes).

| Route | Body → response |
|---|---|
| `POST /v1/devices/{device_id}/checkin` (every ~10 s) | `CheckIn` → `CheckInResponse` |
| `GET /v1/firmware/{version}` | → `FirmwareResponse` (`manifest` + `sha256`) |
| `GET /v1/harness/{version}/package` | → `.tar.gz` with `X-Sha256` header (contains `harness.json` = `HarnessMeta`) |
| `GET /v1/code/{rev}/package` | → `.tar.gz` of `device/` with `X-Sha256` header |
| `POST /v1/devices/{device_id}/installs` | `InstallResult` → `204` |
| `POST /v1/devices/{device_id}/incidents` | `IncidentReport` → `201`/`200` `IncidentAccepted` (idempotent on `incident_id`) |
| `POST /v1/devices/{device_id}/overlays/{overlay_id}` | `OverlayStatus` → `204` |
| `POST /v1/devices/{device_id}/recordings` | `RecordingMeta` → `201` `RecordingAccepted` (`upload`: `needed`/`exists`) |
| `PUT /v1/recordings/{recording_id}/blob` | raw bytes + `X-Sha256` → `204`, or `409 sha256_mismatch` |

Errors are `{"error": "<code>", "detail": "<sentence>"}` (`ErrorResponse`). Queue installs, incidents, overlay statuses and recordings on disk and retry with backoff while the cloud is unreachable; send an incident before its recordings.

**Verifying a firmware pull:** hash the `manifest` object *as received* (the parsed JSON dict, not a re-dumped model) and compare: `manifest_sha256(resp_json["manifest"]) == resp_json["sha256"]`. The hash is sha256 over canonical JSON (sorted keys, no whitespace, UTF-8). `900` and `900.0` hash differently.

## Reflex rules: `rules.evaluate`

Pure, stdlib-only, deterministic. It runs on the device and in the cloud gate's replay, so keep using this function rather than re-implementing it. The full semantics are in the [`rules` module docstring](src/romulus_contracts/rules.py). In short:

- A rule `{"signal": s, "below": x, "above": y, "action": a}` fires when **any** sample of `s` is strictly `< below` or strictly `> above`. Equal doesn't fire. One threshold or both (both = outside the band; `below` must be `< above`).
- Returns every firing rule, sorted by rule name, with the first sample that tripped it.
- Missing signal, empty list, or only unusable samples (`None`, strings, NaN, bools) → doesn't fire, never raises.
- Malformed rules raise `RulesError`. `FirmwareManifest` runs the same check when parsing, so a manifest that parses is always safe to evaluate.
- Replay passes when the incident's `resolving_action` is among the fired actions (`replay_passes`).

## Examples

[`examples/`](examples/) has one realistic payload per message. Tests validate every file, and the cloud tests use them as fixtures.

| File | Model | What |
|---|---|---|
| `checkin.json`, `checkin_first_boot.json`, `checkin_degraded_overlay.json` | `CheckIn` | normal; first boot (`running: null`); degraded with an overlay active and tier detail as an extra field |
| `checkin_response.json` | `CheckInResponse` | cloud reply |
| `firmware_response.json` | `FirmwareResponse` | manifest v1 with a correct `sha256` |
| `harness.json` | `HarnessMeta` | `harness.json` from a harness tarball |
| `install_result_firmware.json`, `install_result_code_rolled_back.json` | `InstallResult` | firmware installed; code package rolled back |
| `incident_d1_code_patch.json` | `IncidentReport` | **fault D1** (drop-on-proximity, `fix-code`) with a `code_patch` that removes the rule and adds a regression test |
| `incident_park_hardware.json` | `IncidentReport` | **fault D3**, servo stays silent: `park`, unresolved, `suspected_cause: hardware` (→ work order) |
| `incident_rules_patch.json` | `IncidentReport` | the APIs §2.5 camera-occlusion example: a `rules` overlay (fake fleet path) |
| `overlay_status_held.json`, `overlay_status_reverted.json` | `OverlayStatus` | held; reverted |
| `recording_incident_window.json`, `recording_agent_trace.json`, `recording_episode.json` | `RecordingMeta` | D1's LeRobot window and agent trace; a sampled normal episode |

File paths in the D1 patch (`control/hold.py`, `tests/…`) and incident kinds (`unexpected_release`, `servo_absent`) are illustrative until the device layout and kind names are agreed.

## Open items for I0: proposed, needs device-side agreement

These are the five items from `CLOUD-PLATFORM-BUILD.md` §5, plus the extra fields this package already tolerates. Nothing below is agreed yet.

1. **Health definition.** `health` is coarse: `ok` (all tier 1–3 checks pass), `degraded` (a tier-3 check fails, or an overlay is under watch, but the arm still works), `failing` (a tier 1 or 2 check fails: crash, hang, or the arm is parked). Tier detail goes in an extra field `health_detail` (any JSON, e.g. `{"tier": 3, "check": "spec_hold_violation"}`) or in `metrics`. The canary treats only `ok` as healthy.
2. **What counts as "held".** The device posts `OverlayStatus(status="held")` once the fault hasn't recurred and health stayed `ok` for N minutes after the patch (proposed N = 20, matching the APIs example). `reverted` is posted when the overlay rolls itself back (health failed or the fault came back). The cloud may add a fallback timer by config.
3. **`park` → hardware.** A `park` outcome means hardware: send `outcome.resolved: false`, `outcome.suspected_cause: "hardware"`, `outcome.resolving_action: "park"`, and no overlay. That combination makes a work order. The healer's own outcome travels as the extra field **`outcome.category`**: `fix-code`, `fix-config`, `ops` or `park` (fault catalog). APIs §2.5 has no slot for it yet.
4. **`code_patch` format.** A unified diff against the base `code_rev`, paths relative to `device/` with `a/`/`b/` prefixes (`--- a/control/gripper.py`), `/dev/null` for new or deleted files. `git diff` run inside `device/` produces it. **Send the base as `running.code_rev` in the incident** (proposed: APIs §2.5's example omits it, and the overlay has no base field). `codepatch.validate_code_patch()` checks the format; the cloud stores malformed patches anyway and flags them.
5. **Harness layout.** A `.tar.gz` with `harness.json` (`HarnessMeta`: `version`, `framework`, `knowledge_dir`) at its root and `skills/` as the knowledge dir. The cloud reads only `harness.json` and only adds markdown under `knowledge_dir`, which must be a relative path inside the package. The rest (`prompt.md`, `tools/`, `checks/`, `policy.yaml`) belongs to the device team.

Also proposed, all tolerated today as optional or extra fields:

- `contract_version` on every device request, not just check-ins (BUILD §2 principle 7). Required on `CheckIn` (APIs §2.3); on other requests it rides as an extra field. The server accepts the current and previous version (`is_supported`).
- `occurred_at` on `InstallResult` and `OverlayStatus` (APIs §1 asks for it; their examples omit it). Optional fields in the models.
- `InstallResult` with `type: "code"` and `version: "<code_rev>"` for code-package installs (APIs §2.10 says code installs like a harness, but §2.4 shows only numeric versions).
- Incident extras the healer already has: `tier` (1–3), `log_tail` (recent log lines).
- Recording kind `audit` with format `json` for the audit file (project-overview §8.3).
- Rules: no `enabled`/`priority` keys in v0.1; remove a rule to switch it off.
- Fleet dataset schema v0.1 (`FLEET_FEATURES`): freeze tonight, or postpone explicitly.

## Changing the contract

Any change bumps `CONTRACT_VERSION` (and sets `PREVIOUS_CONTRACT_VERSION` to the old one, so the server keeps accepting it), updates the APIs doc, and goes to the device teammate. Run `uv run pytest packages/contracts` before pushing.
