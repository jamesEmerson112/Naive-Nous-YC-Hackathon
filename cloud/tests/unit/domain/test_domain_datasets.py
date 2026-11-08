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


def _rec(rid: str, sha: str) -> dict:
    return {"id": rid, "sha256": sha, "kind": "agent_trace"}


def test_snapshot_manifest_is_order_independent():
    ctx = {"i1": {"overlay_outcome": "held"}}
    a, sha_a = dd.snapshot_manifest(
        "ds_1", 1, {"kind": "x"}, [_rec("r2", "b"), _rec("r1", "a")], ["i2", "i1"], ctx
    )
    b, sha_b = dd.snapshot_manifest(
        "ds_1", 1, {"kind": "x"}, [_rec("r1", "a"), _rec("r2", "b")], ["i1", "i2"], ctx
    )
    assert a == b and sha_a == sha_b and len(sha_a) == 64
    assert [r["id"] for r in a["recordings"]] == ["r1", "r2"]
    assert a["context"] == ctx
    _, other = dd.snapshot_manifest("ds_1", 1, {"kind": "x"}, [_rec("r1", "CHANGED")], [])
    assert other != sha_a
    # Frozen context is part of the hash: a later overlay change makes a different snapshot.
    _, moved = dd.snapshot_manifest(
        "ds_1",
        1,
        {"kind": "x"},
        [_rec("r1", "a"), _rec("r2", "b")],
        ["i1", "i2"],
        {"i1": {"overlay_outcome": "locked_in"}},
    )
    assert moved != sha_a


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
        {"signals": [1, 2]},
        {"code_patch": "--- a/x.py\n+++ b/x.py\n"},
        {"code_patch": True},
        {"resolving_action": "park"},
        {"resolving_action": " Park "},
        {"resolving_action": "replace_part"},
        {"work_order": "wo_1"},
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


# --- redaction: one case per pattern -------------------------------------------------------


@pytest.mark.parametrize(
    "dirty, clean",
    [
        # secret-named keys in text: the whole value, spaces included
        ("OPENAI_API_KEY=sk-abcdefghijklmnopqrstuv", "OPENAI_API_KEY=[REDACTED]"),
        ('{"token": "abc123"}', '{"token": "[REDACTED]"}'),
        ('{"token": "abc def ghi"}', '{"token": "[REDACTED]"}'),
        ('{"client_secret": "a \\" b"}', '{"client_secret": "[REDACTED]"}'),
        ("password: hunter2 and more", "password: [REDACTED]"),
        ('passwd="two words"', 'passwd="[REDACTED]"'),
        ("Authorization: Token abcdef", "Authorization: [REDACTED]"),
        ("cookie: sid=abc; theme=dark", "cookie: [REDACTED]"),
        # bearer headers without a key name
        ("curl -H 'X: Bearer abcdefghijkl'", "curl -H 'X: Bearer [REDACTED]'"),
        ("basic dXNlcjpwYXNzd29yZA==", "basic [REDACTED]"),
        # passwords in URLs
        (
            "postgres://romulus:hunter2@db:5432/romulus",
            "postgres://romulus:[REDACTED]@db:5432/romulus",
        ),
        ("https://kent:p%40ss@example.com/x", "https://kent:[REDACTED]@example.com/x"),
        # Stripe and Naïve keys
        ("use sk_live_51HxYzAbCdEfGh now", "use [REDACTED] now"),
        ("use sk_test_51HxYzAbCdEfGh now", "use [REDACTED] now"),
        ("use rk_live_51HxYzAbCdEfGh now", "use [REDACTED] now"),
        ("use nk_live_abcdefgh12 now", "use [REDACTED] now"),
        ("whsec_abcdefghijklmnop1234", "[REDACTED]"),
        ("mcp_abcdefghijklmn", "[REDACTED]"),
        # other providers
        ("key sk-abcdefghijklmnopqrstuv end", "key [REDACTED] end"),
        ("hf_abcdefghijklmnopqrstu", "[REDACTED]"),
        ("ghp_abcdefghijklmnopqrstu", "[REDACTED]"),
        ("AKIAABCDEFGHIJKLMNOP", "[REDACTED]"),
        ("-----BEGIN RSA PRIVATE KEY-----\nabc\n-----END RSA PRIVATE KEY-----", "[REDACTED]"),
        # home paths and emails
        ("open /Users/kent/git2/device/control/hold.py", "open ~/git2/device/control/hold.py"),
        ("/home/pi/robot/app.py", "~/robot/app.py"),
        ("C:\\Users\\kent\\robot", "~\\robot"),
        ("mail kent@example.com now", "mail [EMAIL] now"),
        # ordinary robot text is left alone
        ("brightness 0.12 below 0.2", "brightness 0.12 below 0.2"),
        ("max_tokens: 512", "max_tokens: 512"),
    ],
)
def test_redact_text(dirty, clean):
    assert dd.redact_text(dirty) == clean


def test_redact_walks_nested_values():
    value = {"a": ["/Users/kent/x", 3, None], "b": {"c": "token=xyz"}}
    assert dd.redact(value) == {"a": ["~/x", 3, None], "b": {"c": "token=[REDACTED]"}}


def test_redact_replaces_values_of_secret_named_keys_at_any_depth():
    value = {
        "api_key": "abc123secret",
        "headers": {"Authorization": "Token abcdef", "Accept": "json"},
        "tool_calls": [
            {"name": "http", "arguments": {"url": "https://x.io", "access_key": "AB CD", "n": 3}},
            {"name": "login", "arguments": {"password": 1234, "user": "kent"}},
        ],
        "conversations": [{"from": "gpt", "value": "ok", "secret": {"nested": "x"}}],
        "token": None,
        "max_tokens": 512,
    }
    assert dd.redact(value) == {
        "api_key": "[REDACTED]",
        "headers": {"Authorization": "[REDACTED]", "Accept": "json"},
        "tool_calls": [
            {
                "name": "http",
                "arguments": {"url": "https://x.io", "access_key": "[REDACTED]", "n": 3},
            },
            {"name": "login", "arguments": {"password": "[REDACTED]", "user": "kent"}},
        ],
        "conversations": [{"from": "gpt", "value": "ok", "secret": "[REDACTED]"}],
        "token": None,
        "max_tokens": 512,
    }
