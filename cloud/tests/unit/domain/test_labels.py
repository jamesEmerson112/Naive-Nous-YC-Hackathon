"""domain/labels.py: automatic labels (telemetry §5)."""

import uuid

import pytest

from romulus_cloud.domain.labels import auto_labels, split_label
from romulus_contracts import IncidentReport, RecordingMeta


def incident(kind="gripper_stall", outcome=None, running=None):
    body = {"incident_id": str(uuid.uuid4()), "occurred_at": "2026-11-07T22:00:00Z", "kind": kind}
    if outcome is not None:
        body["outcome"] = outcome
    if running is not None:
        body["running"] = running
    return IncidentReport.model_validate(body)


def recording(kind="episode", episode=None, running=None):
    body = {
        "recording_id": str(uuid.uuid4()),
        "kind": kind,
        "format": "lerobot_v3_tar",
        "sha256": "a" * 64,
        "size_bytes": 10,
        "started_at": "2026-11-07T22:00:00Z",
    }
    if episode is not None:
        body["episode"] = episode
    if running is not None:
        body["running"] = running
    return RecordingMeta.model_validate(body)


def test_nothing_in_nothing_out():
    assert auto_labels(None, None) == set()


def test_resolved_software_incident():
    inc = incident(
        outcome={"resolved": True, "suspected_cause": "software", "category": "fix-code"},
        running={"firmware": 8, "harness": 3},
    )
    assert auto_labels(None, inc) == {
        "kind:gripper_stall",
        "outcome:resolved",
        "cause:software",
        "healer:fix-code",
        "firmware:v8",
    }


def test_park_is_hardware_and_escalated():
    inc = incident(outcome={"resolved": False, "category": "park"})
    labels = auto_labels(None, inc, escalated=True)
    assert {"cause:hardware", "outcome:escalated", "healer:park"} <= labels
    assert "outcome:unresolved" not in labels


def test_unresolved_not_escalated():
    labels = auto_labels(None, incident(outcome={"suspected_cause": "hardware"}))
    assert {"outcome:unresolved", "cause:hardware"} <= labels


def test_resolved_wins_over_escalated():
    labels = auto_labels(None, incident(outcome={"resolved": True}), escalated=True)
    assert "outcome:resolved" in labels and "outcome:escalated" not in labels


@pytest.mark.parametrize(
    "outcome", [None, {"suspected_cause": "gremlins"}, {"suspected_cause": ""}]
)
def test_missing_or_unknown_cause_is_unknown(outcome):
    labels = auto_labels(None, incident(outcome=outcome))
    assert "cause:unknown" in labels and "outcome:unresolved" in labels
    assert not any(label.startswith("healer:") for label in labels)


def test_unknown_kind_is_kept_as_sent():
    assert "kind:Weird Kind" in auto_labels(None, incident(kind=" Weird Kind "))


@pytest.mark.parametrize(
    ("status", "label"),
    [
        ("held", "overlay:held"),
        ("REVERTED", "overlay:reverted"),
        ("locked_in", "overlay:locked_in"),
    ],
)
def test_overlay_status(status, label):
    assert auto_labels(None, None, overlay_status=status) == {label}


def test_unknown_overlay_status_is_ignored():
    assert auto_labels(None, None, overlay_status="dropped") == set()


@pytest.mark.parametrize(
    ("success", "expected"),
    [(True, {"episode:success"}), (False, {"episode:failure"}), (None, set())],
)
def test_episode_success(success, expected):
    rec = recording(episode={"task": "pick_cube", "success": success})
    assert auto_labels(rec, None) == expected


def test_recording_without_episode():
    assert auto_labels(recording(kind="log_window"), None) == set()


def test_intervention_flag():
    assert auto_labels(recording(), None, intervention=True) == {"intervention"}


def test_firmware_prefers_recording_then_incident():
    rec = recording(running={"firmware": 9})
    inc = incident(running={"firmware": 8})
    assert "firmware:v9" in auto_labels(rec, inc)
    assert "firmware:v8" in auto_labels(recording(running={"harness": 2}), inc)
    assert not any(x.startswith("firmware:") for x in auto_labels(recording(), incident()))


def test_firmware_zero_is_labelled():
    assert "firmware:v0" in auto_labels(recording(running={"firmware": 0}), None)


def test_deterministic():
    inc = incident(outcome={"category": "park"}, running={"firmware": 1})
    rec = recording(episode={"task": "t", "success": False})
    assert auto_labels(rec, inc, escalated=True) == auto_labels(rec, inc, escalated=True)


def test_split_label():
    assert split_label("kind:gripper_stall") == ("kind", "gripper_stall")
    assert split_label("intervention") == ("intervention", None)
    assert split_label("kind:a:b") == ("kind", "a:b")
