"""The verdict table (design §2): one case per row, first matching row wins."""

from datetime import UTC, datetime

import pytest

from romulus_cloud.domain import runs as dr
from romulus_cloud.domain.runs import EXCLUDED, FAIL, PASS, UNVERIFIED, LabelledRun, verdict

FIXED = {"sim": False, "state": "finished", "status": "fixed", "verification": None}


def _run(**kw):
    return FIXED | kw


@pytest.mark.parametrize(
    "args, kw, expected, words",
    [
        # excluded
        ((_run(sim=True),), {}, EXCLUDED, "rehearsal"),
        ((_run(state="running", status="working"),), {}, EXCLUDED, "still running"),
        ((None, {"work_order": "wo_1", "resolved": False}), {}, EXCLUDED, "work order wo_1"),
        ((None, {"resolving_action": "park", "resolved": False}), {}, EXCLUDED, "park"),
        ((None, {"suspected_cause": "hardware"}), {}, EXCLUDED, "hardware"),
        # fail
        (
            (_run(verification={"passed": False, "final_loops": {"check": "exit 4"}}),),
            {},
            FAIL,
            "bench check failed: exit 4",
        ),
        (
            (_run(status="broken", outcome={"headline": "Still on the faulty image"}),),
            {},
            FAIL,
            "left it broken: Still on the faulty image",
        ),
        (
            (None, {"resolved": True}, {"id": "ov-1", "status": "lockin_rolled_back"}),
            {},
            FAIL,
            "lock-in of overlay ov-1 rolled back",
        ),
        (
            (None, {"resolved": True}, {"id": "ov-1", "status": "reverted"}),
            {},
            FAIL,
            "reverted overlay ov-1",
        ),
        (
            (
                None,
                {"resolved": True},
                {"id": "ov-1", "status": "locked_in"},
                [{"id": "cand_1", "type": "firmware", "version": 2, "status": "rolled_back"}],
            ),
            {},
            FAIL,
            "gate rolled back firmware v2 (cand_1)",
        ),
        (
            (None, {"resolved": True, "kind": "gripper_stall", "device_id": "sim-1"}),
            {"recurred_after_s": 240},
            FAIL,
            "gripper_stall came back on sim-1 4 min later",
        ),
        # pass
        ((_run(verification={"passed": True}),), {}, PASS, "bench check passed"),
        (
            (
                None,
                {"resolved": True},
                {"id": "ov-1", "status": "locked_in"},
                [{"id": "cand_1", "type": "harness", "version": 4, "status": "promoted"}],
            ),
            {},
            PASS,
            "gate promoted harness v4 (cand_1)",
        ),
        (
            (None, {"resolved": True}, {"id": "ov-2", "status": "held"}),
            {},
            PASS,
            "overlay ov-2 held on device",
        ),
        # unverified
        ((_run(),), {}, UNVERIFIED, "device says fixed; nothing checked it"),
        (
            (None, {"resolved": True}, {"id": "ov-3", "status": "active"}),
            {},
            UNVERIFIED,
            "resolved",
        ),
        ((_run(status="unknown"),), {}, UNVERIFIED, "nothing checked it"),
    ],
)
def test_each_row_of_the_verdict_table(args, kw, expected, words):
    got, why = verdict(*args, **kw)
    assert got == expected and words in why, (got, why)


def test_first_matching_row_wins():
    # A rehearsal whose bench check passed is still a rehearsal.
    assert verdict(_run(sim=True, verification={"passed": True}))[0] == EXCLUDED
    # A bench pass with a rolled-back candidate is a fail: fail rows come before pass rows.
    cands = [{"id": "c", "type": "firmware", "version": 3, "status": "rolled_back"}]
    assert verdict(_run(verification={"passed": True}), {}, None, cands)[0] == FAIL
    # A recurrence outside the window doesn't count.
    late = dr.REPEAT_WINDOW_S + 1
    assert verdict(None, {"resolved": True}, None, recurred_after_s=late)[0] == UNVERIFIED


def test_a_device_that_says_fixed_is_never_a_pass_on_its_own():
    """The bridge's own device check (`verified_by: device_check`) is not independent."""
    run = _run(outcome={"status": "fixed", "verified_by": "device_check"})
    assert verdict(run) == (UNVERIFIED, "device says fixed; nothing checked it")


def test_labelled_run_to_dict_is_plain_json():
    lr = LabelledRun(
        id="run:pod-1/r1",
        device_id="pod-1",
        device_class="sensor_pod",
        device_kind="sim",
        kind="crash",
        started_at=datetime(2026, 11, 8, 12, 0, tzinfo=UTC),
        model="qwen3.5:4b",
        versions={"firmware": 1},
        steps=({"thought": "t", "tool": "triage", "args": {}, "effect": "ok", "ok": True},),
        verdict=PASS,
        verifier="bench check passed",
        incident_id="i1",
        run_id="r1",
    )
    d = lr.to_dict()
    assert d["started_at"] == "2026-11-08T12:00:00+00:00" and isinstance(d["steps"], list)
    assert lr.sim_device and dr.counts([lr]) == {PASS: 1, FAIL: 0, UNVERIFIED: 0, EXCLUDED: 0}
