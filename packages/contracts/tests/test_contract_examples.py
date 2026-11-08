"""Every file in packages/contracts/examples parses into its model and agrees with the docs.

The examples are fixtures for cloud tests and the handoff to the device side, so they
must stay valid.
"""

import json
from pathlib import Path

import pytest
from romulus_contracts import (
    CheckIn,
    CheckInResponse,
    FirmwareResponse,
    HarnessMeta,
    IncidentReport,
    InstallResult,
    OverlayStatus,
    RecordingMeta,
    evaluate,
    manifest_sha256,
    replay_passes,
)
from romulus_contracts.codepatch import patch_paths, validate_code_patch
from romulus_contracts.telemetry import FLEET_FEATURES, FLEET_SCHEMA_VERSION
from romulus_contracts.version import is_supported

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"

# Longest matching filename prefix wins.
MODEL_BY_PREFIX = {
    "checkin_response": CheckInResponse,
    "checkin": CheckIn,
    "firmware_response": FirmwareResponse,
    "harness": HarnessMeta,
    "install_result": InstallResult,
    "incident": IncidentReport,
    "overlay_status": OverlayStatus,
    "recording": RecordingMeta,
}


def load(name):
    return json.loads((EXAMPLES / name).read_text())


def model_for(name):
    matches = [p for p in MODEL_BY_PREFIX if name.startswith(p)]
    assert matches, f"no model for example {name}; add it to MODEL_BY_PREFIX"
    return MODEL_BY_PREFIX[max(matches, key=len)]


ALL = sorted(p.name for p in EXAMPLES.glob("*.json"))


def test_examples_exist():
    assert len(ALL) >= 10


@pytest.mark.parametrize("name", ALL)
def test_example_validates_and_round_trips(name):
    model = model_for(name)
    raw = load(name)
    parsed = model.model_validate(raw)
    assert model.model_validate(parsed.model_dump(mode="json")) == parsed


@pytest.mark.parametrize("name", [n for n in ALL if model_for(n) is not CheckInResponse])
def test_device_examples_carry_supported_contract_version_when_present(name):
    version = load(name).get("contract_version")
    assert version is None or is_supported(version)


def test_firmware_response_hash_matches_manifest():
    raw = load("firmware_response.json")
    assert manifest_sha256(raw["manifest"]) == raw["sha256"]
    FirmwareResponse.model_validate(raw)


def test_d1_incident_code_patch_is_well_formed():
    inc = IncidentReport.model_validate(load("incident_d1_code_patch.json"))
    assert validate_code_patch(inc.overlay.code_patch) == []
    assert patch_paths(inc.overlay.code_patch) == [
        "control/hold.py",
        "tests/test_hold_proximity.py",
    ]
    assert inc.outcome.model_extra["category"] == "fix-code"
    assert inc.running.code_rev == "c0de001"  # the patch's base code_rev
    assert inc.overlay.diff == {}


def test_d1_recordings_link_back_to_incident():
    inc = IncidentReport.model_validate(load("incident_d1_code_patch.json"))
    for name in ("recording_incident_window.json", "recording_agent_trace.json"):
        rec = RecordingMeta.model_validate(load(name))
        assert rec.incident_id == inc.incident_id
        assert rec.recording_id in inc.recordings


def test_park_incident_is_unresolved_hardware():
    inc = IncidentReport.model_validate(load("incident_park_hardware.json"))
    assert inc.overlay is None
    assert inc.outcome.resolved is False
    assert inc.outcome.suspected_cause == "hardware"
    assert inc.outcome.model_extra["category"] == "park"


def test_rules_incident_replays_against_merged_candidate():
    inc = IncidentReport.model_validate(load("incident_rules_patch.json"))
    manifest = load("firmware_response.json")["manifest"]
    candidate = {name: dict(rule) for name, rule in manifest["rules"].items()}
    for name, patch in inc.overlay.diff["rules"].items():
        candidate[name] |= patch
    assert replay_passes(candidate, inc.signals, inc.outcome.resolving_action)
    assert evaluate(candidate, inc.signals)[0].value == 0.18


def test_lerobot_recordings_use_the_fleet_schema():
    for name in ALL:
        if model_for(name) is RecordingMeta:
            rec = RecordingMeta.model_validate(load(name))
            if rec.format == "lerobot_v3_tar":
                assert rec.streams == list(FLEET_FEATURES), name
                assert rec.schema_version == FLEET_SCHEMA_VERSION, name


def test_both_overlay_statuses_have_examples():
    statuses = {OverlayStatus.model_validate(load(n)).status for n in ALL if "overlay_status" in n}
    assert statuses == {"held", "reverted"}
