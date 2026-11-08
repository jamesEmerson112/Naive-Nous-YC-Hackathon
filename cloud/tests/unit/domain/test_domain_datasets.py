"""Unit tests for `domain/datasets.py`: filters, snapshot manifests, regression cases,
trace conversion, redaction and byte-identical files."""

import json
from datetime import UTC, datetime

import pytest

from romulus_cloud.domain import datasets as dd

AT = datetime(2026, 11, 7, 22, 0, tzinfo=UTC)


# --- filters ---------------------------------------------------------------------------------


def test_parse_filter_normalizes_and_accepts_empty():
    assert dd.parse_filter(None) == dd.Filter()
    f = dd.parse_filter(
        {
            "kind": "agent_trace",
            "labels": ["b", "a ", "a"],
            "device": " arm-1 ",
            "since": "2026-11-07T21:00:00Z",
        }
    )
    assert f.kinds == ("agent_trace",) and f.labels == ("a", "b") and f.device == "arm-1"
    assert f.since == datetime(2026, 11, 7, 21, 0, tzinfo=UTC) and f.recording_only


@pytest.mark.parametrize(
    "raw, words",
    [
        ({"kinds": "x"}, "unknown filter keys"),
        ({"labels": [1]}, "labels"),
        ({"since": "yesterday"}, "not an ISO"),
        ({"since": "2026-11-07T21:00:00"}, "time zone"),
        ({"since": "2026-11-08T00:00:00Z", "until": "2026-11-07T00:00:00Z"}, "after"),
        ({"device": ""}, "device"),
    ],
)
def test_parse_filter_refuses_bad_input(raw, words):
    with pytest.raises(ValueError, match=words):
        dd.parse_filter(raw)


def test_matches_recording():
    rec = {"kind": "agent_trace", "device_id": "arm-1", "at": AT}
    f = dd.parse_filter({"kind": ["agent_trace", "log_window"], "labels": ["kind:x"]})
    assert dd.matches_recording(f, rec, ["kind:x", "other"])
    assert not dd.matches_recording(f, rec, ["other"])
    assert not dd.matches_recording(dd.parse_filter({"device": "sim-1"}), rec, [])
    assert not dd.matches_recording(dd.parse_filter({"kind": "audit"}), rec, [])
    window = dd.parse_filter({"since": "2026-11-07T21:00:00Z", "until": "2026-11-07T22:00:00Z"})
    assert dd.matches_recording(window, rec, [])  # until is inclusive
    assert not dd.matches_recording(window, rec | {"at": AT.replace(hour=23)}, [])
    assert not dd.matches_recording(window, rec | {"at": None}, [])
    assert dd.matches_recording(dd.Filter(), rec | {"at": None}, [])


def test_matches_incident_only_without_recording_only_fields():
    inc = {"device_id": "arm-1", "occurred_at": AT}
    assert dd.matches_incident(dd.Filter(), inc)
    assert dd.matches_incident(dd.parse_filter({"device": "arm-1"}), inc)
    assert not dd.matches_incident(dd.parse_filter({"device": "sim-1"}), inc)
    assert not dd.matches_incident(dd.parse_filter({"kind": "agent_trace"}), inc)
    assert not dd.matches_incident(dd.parse_filter({"until": "2026-11-07T21:00:00Z"}), inc)


# --- snapshots -------------------------------------------------------------------------------


def test_snapshot_manifest_is_order_independent():
    a, sha_a = dd.snapshot_manifest(
        "ds_1", 1, {"kind": "x"}, [("r2", "b"), ("r1", "a")], ["i2", "i1"]
    )
    b, sha_b = dd.snapshot_manifest(
        "ds_1", 1, {"kind": "x"}, [("r1", "a"), ("r2", "b")], ["i1", "i2"]
    )
    assert a == b and sha_a == sha_b and len(sha_a) == 64
    assert a["recordings"] == [{"id": "r1", "sha256": "a"}, {"id": "r2", "sha256": "b"}]
    _, other = dd.snapshot_manifest("ds_1", 1, {"kind": "x"}, [("r1", "CHANGED")], [])
    assert other != sha_a


# --- regression cases ------------------------------------------------------------------------

RESOLVED = {
    "incident_id": "i1",
    "kind": "camera_occluded",
    "resolved": True,
    "resolving_action": "safe_pose",
    "signals": {"brightness": [0.4, 0.1]},
    "code_patch": None,
}


def test_regression_case_from_resolved_incident():
    assert dd.regression_case(RESOLVED) == {
        "incident_id": "i1",
        "kind": "camera_occluded",
        "signals": {"brightness": [0.4, 0.1]},
        "expected_action": "safe_pose",
    }


@pytest.mark.parametrize(
    "change",
    [
        {"resolved": False},
        {"resolved": None},
        {"resolving_action": ""},
        {"resolving_action": None},
        {"signals": {}},
        {"signals": None},
        {"code_patch": "--- a/x.py\n+++ b/x.py\n"},
    ],
)
def test_regression_case_skips_what_replay_cannot_check(change):
    assert dd.regression_case(RESOLVED | change) is None


# --- traces ----------------------------------------------------------------------------------


def test_sharegpt_maps_roles_and_keeps_bad_lines():
    text = "\n".join(
        [
            json.dumps({"role": "user", "content": "fix it"}),
            json.dumps({"role": "assistant", "content": "ok", "tool_calls": [{"name": "park"}]}),
            json.dumps({"role": "tool", "name": "park", "content": {"ok": True}}),
            "not json",
            "",
            json.dumps([1, 2]),
        ]
    )
    assert dd.sharegpt(text) == [
        {"from": "human", "value": "fix it"},
        {"from": "gpt", "value": "ok", "tool_calls": [{"name": "park"}]},
        {"from": "tool", "value": '{"ok": true}', "name": "park"},
        {"from": "raw", "value": "not json"},
        {"from": "raw", "value": "[1, 2]"},
    ]


@pytest.mark.parametrize(
    "dirty, clean",
    [
        ("OPENAI_API_KEY=sk-abcdefghijklmnopqrstuv", "OPENAI_API_KEY=[REDACTED]"),
        ('{"token": "abc123"}', '{"token": "[REDACTED]"}'),
        ("password: hunter2", "password: [REDACTED]"),
        ("Authorization: Bearer abcdefghijkl", "Authorization: Bearer [REDACTED]"),
        ("key sk-abcdefghijklmnopqrstuv end", "key [REDACTED] end"),
        ("hf_abcdefghijklmnopqrstu", "[REDACTED]"),
        ("AKIAABCDEFGHIJKLMNOP", "[REDACTED]"),
        ("open /Users/kent/git2/device/control/hold.py", "open ~/git2/device/control/hold.py"),
        ("/home/pi/robot/app.py", "~/robot/app.py"),
        ("mail kent@example.com now", "mail [EMAIL] now"),
        ("brightness 0.12 below 0.2", "brightness 0.12 below 0.2"),
    ],
)
def test_redact_text(dirty, clean):
    assert dd.redact_text(dirty) == clean


def test_redact_walks_nested_values():
    value = {"a": ["/Users/kent/x", 3, None], "b": {"c": "token=xyz"}}
    assert dd.redact(value) == {"a": ["~/x", 3, None], "b": {"c": "token=[REDACTED]"}}
