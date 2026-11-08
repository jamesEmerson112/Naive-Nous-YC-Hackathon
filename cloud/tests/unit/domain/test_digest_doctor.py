"""domain/digest.py, Fleet Doctor (PR 6): asks, consults, fixes offered / applied / held,
give-ups, per fault family, and the Doctor's spend. Events in interfaces.md §8 shapes."""

from datetime import UTC, datetime, timedelta

import pytest

from romulus_cloud.domain.digest import EventView, render_text, summarize

T0 = datetime(2026, 11, 8, 0, 30, tzinfo=UTC)
T1 = T0 + timedelta(hours=6)


def doc(kind, minutes=10, refs=None, **data):
    return EventView(
        "fleet-doctor", "agent", kind, T0 + timedelta(minutes=minutes), "why", refs or {}, data
    )


def _night():
    cam = {"incident_kind": "camera_occluded"}
    grip = {"incident_kind": "unexpected_release"}
    return [
        doc("doctor.asked", **cam, trigger="T2"),
        doc("doctor.advised", **cam, cost_usd=0.002),
        doc("doctor.asked", **cam),
        doc("doctor.asked", **grip),
        doc("doctor.consult_opened", **cam, attempt=1),
        doc("doctor.diagnosed", **cam, cost_usd=0.01),
        doc("doctor.proposal_offered", **cam),
        doc("doctor.proposal_result", **cam, result="applied"),
        doc("doctor.consult_closed", **cam, outcome="held"),
        doc("doctor.consult_opened", **grip, attempt=1),
        doc("doctor.consult_closed", **grip, outcome="failed_checks"),
        doc("doctor.proposal_result", **grip, result="rejected"),
        doc("doctor.gave_up", **grip),
        doc("doctor.asked"),  # no incident kind
    ]


def test_counts_in_total():
    d = summarize(_night(), T0, T1)
    assert d.doctor_asks == 4
    assert d.doctor_consults == 2
    assert d.doctor_proposals_offered == 1
    assert d.doctor_proposals_applied == 1  # `rejected` doesn't count
    assert d.doctor_consults_held == 1  # `failed_checks` doesn't count
    assert d.doctor_gave_up == 1
    assert d.doctor_active


def test_counts_per_fault_family():
    d = summarize(_night(), T0, T1)
    assert d.doctor_by_kind == {
        "camera_occluded": {
            "asks": 2,
            "consults": 1,
            "proposals_offered": 1,
            "proposals_applied": 1,
            "consults_held": 1,
            "gave_up": 0,
        },
        "unexpected_release": {
            "asks": 1,
            "consults": 1,
            "proposals_offered": 0,
            "proposals_applied": 0,
            "consults_held": 0,
            "gave_up": 1,
        },
        "unknown": {
            "asks": 1,
            "consults": 0,
            "proposals_offered": 0,
            "proposals_applied": 0,
            "consults_held": 0,
            "gave_up": 0,
        },
    }


def test_family_falls_back_to_unknown_and_uncounted_kinds_add_no_family():
    evs = [
        doc("doctor.asked", incident_kind=""),
        doc("doctor.asked", incident_kind=7),
        doc("doctor.diagnosed", incident_kind="only_diagnosed", cost_usd=0.5),
        EventView("fleet-doctor", "agent", "doctor.asked", T0, "why", {}, None),
    ]
    d = summarize(evs, T0, T1)
    assert list(d.doctor_by_kind) == ["unknown"] and d.doctor_by_kind["unknown"]["asks"] == 3


def test_spend_sums_only_non_negative_numbers():
    evs = [
        doc("doctor.advised", cost_usd=0.1),
        doc("doctor.diagnosed", cost_usd=0.2),
        doc("doctor.drafted", cost_usd=3),
        doc("doctor.advised", cost_usd=-5),  # can't offset real spend
        doc("doctor.advised", cost_usd="0.5"),  # a string isn't a number
        doc("doctor.advised", cost_usd=True),
        doc("doctor.advised", cost_usd=None),
        doc("doctor.advised", cost_usd=float("nan")),
        doc("doctor.advised", cost_usd={"usd": 1}),
        doc("doctor.advised"),
        EventView("worker", "automation", "coder.drafted", T0, "why", {}, {"cost_usd": 9}),
    ]
    assert summarize(evs, T0, T1).doctor_spend_usd == 3.3


@pytest.mark.parametrize(
    "costs",
    [
        [1e308, 1e308],  # each finite, the total overflows
        [10**400],  # too big for a float: counts as infinity, as in doctor_spend
        [float("inf"), 0.5],
    ],
)
def test_a_total_that_is_not_finite_is_unknown_never_infinity(costs):
    evs = [doc("doctor.diagnosed", cost_usd=c) for c in costs] + [doc("doctor.asked")]
    d = summarize(evs, T0, T1)
    assert d.doctor_spend_usd is None and d.doctor_asks == 1
    line = next(x for x in render_text(d).splitlines() if x.startswith("Fleet Doctor:"))
    assert line.endswith("spend unknown (a cost too large to add up; the spend cap refuses it too)")


def test_window_bounds_apply_to_doctor_counts():
    evs = [
        doc("doctor.asked", minutes=-1, cost_usd=1.0),
        doc("doctor.asked", minutes=0, cost_usd=0.25),
        doc("doctor.asked", minutes=6 * 60, cost_usd=1.0),
    ]
    d = summarize(evs, T0, T1)
    assert d.doctor_asks == 1 and d.doctor_spend_usd == 0.25


def test_render_adds_one_doctor_line():
    text = render_text(summarize(_night(), T0, T1))
    lines = [line for line in text.splitlines() if line.startswith("Fleet Doctor:")]
    assert lines == [
        "Fleet Doctor: 4 asks · 2 consults · fixes 1 offered, 1 applied, 1 held · 1 gave up · "
        "spend $0.0120"
    ]
    assert text.index("Kill switch:") < text.index("Fleet Doctor:")


def test_no_doctor_events_leave_the_digest_unchanged():
    events = [
        EventView("device:arm-1", "agent", "incident.reported", T0, "why"),
        EventView("gate", "automation", "candidate.promoted", T0, "why"),
    ]
    d = summarize(events, T0, T1)
    assert not d.doctor_active and d.doctor_by_kind == {} and d.doctor_spend_usd == 0
    assert d.doctor_asks == d.doctor_consults == d.doctor_consults_held == 0
    text = render_text(d)
    assert "Fleet Doctor" not in text
    assert text.splitlines()[-1] == "Kill switch: 0 freeze(s)"


def test_doctor_events_are_not_actions_yet():
    d = summarize(_night(), T0, T1)
    assert d.actions == 0 and d.no_human_share is None
