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
| `manifest` | `FirmwareManifest`, `FirmwareResponse`, `HarnessMeta`, `firmware_response()`, `manifest_sha256()` |
| `telemetry` | `RecordingMeta`, `RecordingAccepted`, `RecordingKind`, `FLEET_FEATURES` (dataset schema v0.1) |
| `codepatch` | `summarize_code_patch()`, `validate_code_patch()`, `patch_paths()`, `touches_safety()` (fails closed) for the `code_patch` format |
| `rules` | `evaluate(rules, signals)`, `validate_rules()`, `replay_passes()`, `replay_report()`, `skipped_samples()`: the reflex function shared by device and gate |
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

**Tolerance rules.** Models for payloads the device sends keep unknown fields (`extra="allow"`), and the cloud stores every payload raw. Add fields freely; they won't be rejected and they won't be lost. Models for cloud responses ignore unknown fields, so a newer cloud won't break an older device. Timestamps without an offset are read as UTC, and `null` lists/objects read as empty. Open strings: recording `kind`/`format`, signal names, rule actions, incident `kind`, `outcome.suspected_cause` (known: `software`/`hardware`/`unknown`), `outcome.category` (known: `fix-code`/`fix-config`/`ops`/`park`). `outcome` itself is optional, and `agent.actions` may hold strings or dicts. Fixed sets: `health` (`ok`/`degraded`/`failing`), install `type` and `result`, overlay `status`. Strict on purpose: `incident_id` and recording ids are UUIDs (idempotency, D8).

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

Errors are `{"error": "<code>", "detail": "<sentence>"}` (`ErrorResponse`).

**Outbox rule.** Queue installs, incidents, overlay statuses and recordings on disk and send them in order; send an incident before its recordings.
- **Retry with backoff** on connection errors, timeouts, `5xx`, `408` and `429`.
- **Dead-letter on any other `4xx`** (e.g. `422`): move the item to a dead-letter file, log the error `detail`, and carry on with the next item. A payload the cloud rejected once will be rejected forever, and must not block everything queued behind it.

**Verifying a firmware pull:** hash the `manifest` object *as received* (the parsed JSON dict, not a re-dumped model) and compare: `manifest_sha256(resp_json["manifest"]) == resp_json["sha256"]`. The hash is sha256 over canonical JSON (sorted keys, no whitespace, UTF-8). `900` and `900.0` hash differently. On the cloud side, build the body with `firmware_response(manifest)`, which dumps the manifest once and hashes that same dump, so the served manifest and its hash can't diverge. If the hash matches but `FirmwareManifest` rejects the manifest (e.g. a newer rule shape), keep the current version and report `InstallResult(result="invalid", detail=<the error>)`.

## Reflex rules: `rules.evaluate`

Pure, stdlib-only, deterministic. It runs on the device and in the cloud gate's replay, so keep using this function rather than re-implementing it. The full semantics are in the [`rules` module docstring](src/romulus_contracts/rules.py). In short:

- A rule `{"signal": s, "below": x, "above": y, "action": a}` fires when **any** sample of `s` is strictly `< below` or strictly `> above`. Equal doesn't fire. One threshold or both (both = outside the band; `below` must be `< above`).
- Returns every firing rule, sorted by rule name, with the first sample that tripped it.
- Missing signal, empty list, or only unusable samples (`None`, strings, NaN, bools) → doesn't fire, never raises.
- Malformed rules raise `RulesError`. `FirmwareManifest` runs the same check when parsing, so a manifest that parses is always safe to evaluate.
- **Validate merged rules before applying an overlay.** An overlay `diff` holds partial rules (`{"below": 0.35}`). After merging it over the assigned manifest's rules, run `validate_rules(merged)` and swap the merged rules in only if it returns `[]`. Never hand unvalidated rules to the reflex loop, where `evaluate` would raise.
- Replay passes when the incident's `resolving_action` is among the fired actions (`replay_passes`). `replay_report()` returns the same verdict plus what fired and every sample that was ignored and why (`skipped_samples()`: `missing`, `empty`, `null`, `string`, `boolean`, `NaN`, …), and `.why()` gives one sentence for the gate's event, e.g. "replay failed: safe_pose did not fire (fired: nothing); ignored brightness: 2 string".

## Code patches and `touches_safety`

`summarize_code_patch(patch)` reads every path a diff can carry (`diff --git`, `---`/`+++`, rename/copy lines, binary markers, C-quoted names), normalizes `./` and `//`, and lists every problem (unprefixed paths, `a/device/…`, `..` or absolute paths, binary changes, truncated hunks). It never rejects an incident.

**Fail closed:** if a patch has *any* problem, its file list can't be trusted, so treat it as touching the safety layer. `touches_safety(patch, safety_paths)` implements exactly that (K17); use it rather than matching `patch_paths()` against prefixes yourself. Paths under `firmware/` (STM32 C code) are listed with a note, not rejected.

## Examples

[`examples/`](examples/) has one realistic payload per message. Tests validate every file, and the cloud tests use them as fixtures.

| File | Model | What |
|---|---|---|
| `checkin.json`, `checkin_first_boot.json`, `checkin_degraded_overlay.json` | `CheckIn` | normal; first boot (`running: null`); degraded with an overlay active and `health_detail` |
| `checkin_response.json` | `CheckInResponse` | cloud reply |
| `firmware_response.json` | `FirmwareResponse` | manifest v1 with a correct `sha256` |
| `harness.json` | `HarnessMeta` | `harness.json` from a harness tarball |
| `install_result_firmware.json`, `install_result_code_rolled_back.json`, `install_result_invalid.json` | `InstallResult` | firmware installed; code package rolled back; manifest failed validation |
| `incident_d1_code_patch.json` | `IncidentReport` | **fault D1** (drop-on-proximity, `fix-code`) with a `code_patch` that removes the rule and adds a regression test |
| `incident_park_hardware.json` | `IncidentReport` | **fault D3**, servo stays silent: `park`, unresolved, `suspected_cause: hardware` (→ work order) |
| `incident_rules_patch.json` | `IncidentReport` | the APIs §2.5 camera-occlusion example: a `rules` overlay (fake fleet path) |
| `overlay_status_held.json`, `overlay_status_reverted.json` | `OverlayStatus` | held; reverted |
| `recording_incident_window.json`, `recording_agent_trace.json`, `recording_episode.json` | `RecordingMeta` | D1's LeRobot window and agent trace; a sampled normal episode |

File paths in the D1 patch (`control/hold.py`, `tests/…`) and incident kinds (`unexpected_release`, `servo_absent`) are illustrative until the device layout and kind names are agreed.

## Open items for I0: proposed, needs device-side agreement

These are the five items from `CLOUD-PLATFORM-BUILD.md` §5, plus the fields this package proposes beyond the APIs doc. Nothing below is agreed yet.

1. **Health definition.** `health` is coarse: `ok` (all tier 1–3 checks pass), `degraded` (a tier-3 check fails, or an overlay is under watch, but the arm still works), `failing` (a tier 1 or 2 check fails: crash, hang, or the arm is parked). Tier detail goes in the optional field `health_detail` (any JSON, e.g. `{"tier": 3, "check": "spec_hold_violation"}`) or in `metrics`. The canary treats only `ok` as healthy.
2. **What counts as "held".** The device posts `OverlayStatus(status="held")` once the fault hasn't recurred and health stayed `ok` for N minutes after the patch (proposed N = 20, matching the APIs example). `reverted` is posted when the overlay rolls itself back (health failed or the fault came back). `superseded` is posted when a newer firmware or code version installs, passes its health check and replaces the overlay (D7); it is not a failure (added to the v0.1 draft for the fake device's reference visor). The cloud may add a fallback timer by config.
3. **`park` → hardware.** A `park` outcome means hardware: send `outcome.resolved: false`, `outcome.suspected_cause: "hardware"`, `outcome.resolving_action: "park"`, and no overlay. That combination makes a work order. The healer's own outcome travels as the optional field **`outcome.category`**: `fix-code`, `fix-config`, `ops` or `park` (fault catalog). APIs §2.5 has no slot for it yet.
4. **`code_patch` format.** A unified diff against the base `code_rev`, paths relative to `device/` with `a/`/`b/` prefixes (`--- a/control/gripper.py`), `/dev/null` for new or deleted files. `git diff` run inside `device/` produces it. **Send the base as `running.code_rev` in the incident** (proposed: APIs §2.5's example omits it, and the overlay has no base field). `codepatch.summarize_code_patch()` checks the format; the cloud stores malformed patches anyway, flags them, and treats them as touching safety.
5. **Harness layout.** A `.tar.gz` with `harness.json` (`HarnessMeta`: `version`, `framework`, `knowledge_dir`) at its root and `skills/` as the knowledge dir. The cloud reads only `harness.json` and only adds markdown under `knowledge_dir`, which must be a relative path inside the package. The rest (`prompt.md`, `tools/`, `checks/`, `policy.yaml`) belongs to the device team.

6. **STM32 patches under `firmware/`: how are they expressed?** Fault-catalog A2 heals (F1–F11) edit C code under `firmware/`. If that tree lives inside `device/`, a patch is `--- a/firmware/src/main.c` and works today (listed with a note: needs a rebuild and reflash). If it lives outside `device/` (e.g. the repo's top-level `firmware/`), `a/../firmware/…` can't be expressed and is reported as a problem. Options: move it under `device/`, or widen `code_patch` to a second root.

Also proposed, all declared as optional fields so they appear in the JSON Schema:

- `contract_version` on every device request, not just check-ins (BUILD §2 principle 7). Required on `CheckIn` (APIs §2.3); optional on the others. The server accepts the current and previous version (`is_supported`).
- `occurred_at` on `InstallResult` and `OverlayStatus` (APIs §1 asks for it; their examples omit it).
- `InstallResult` with `type: "code"` and `version: "<code_rev>"` for code-package installs (APIs §2.10 says code installs like a harness, but §2.4 shows only numeric versions), and `result: "invalid"` for a manifest or package that failed validation.
- `CheckIn.health_detail`, `Outcome.category`, and the incident fields the healer already has: `tier` (1–3), `log_tail` (recent log lines, text or JSON-lines objects).
- `outcome` optional and `suspected_cause` open, so a reasonable but imperfect incident never wedges the outbox.
- Recording kind `audit` with format `json` for the audit file (project-overview §8.3).
- Rules: no `enabled`/`priority` keys in v0.1; remove a rule to switch it off.
- Fleet dataset schema v0.1 (`FLEET_FEATURES`): freeze tonight, or postpone explicitly.

## Changing the contract

**Policy:** v0.1 is a draft until the I0 handshake; draft changes are additive and don't bump `CONTRACT_VERSION`; after I0, every change bumps it.

After I0, a change bumps `CONTRACT_VERSION` (and sets `PREVIOUS_CONTRACT_VERSION` to the old one, so the server keeps accepting it), updates the APIs doc, and goes to the device teammate. Run `uv run pytest packages/contracts` before pushing.
