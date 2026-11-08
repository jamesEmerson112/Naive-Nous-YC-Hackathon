"""domain/story.py, Fleet Doctor (PR 6): the hypothesis ledger, lines, status and tie order.

Events are built here in the shapes of docs/fleet-doctor/interfaces.md §8; the emitters
(PR 3, PR 4) are tested against the same shapes on their side.
"""

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from romulus_cloud.domain import story
from romulus_cloud.domain.story import build_story

T0 = datetime(2026, 11, 7, 22, 2, 11, tzinfo=UTC)
INC = "0b6c6f3e-8d2a-4c1b-b5e3-6f7a8b9c0d1e"
DOCTOR_KINDS = (
    "doctor.asked",
    "doctor.advised",
    "doctor.blocked_over_cap",
    "doctor.declined",
    "doctor.consult_opened",
    "doctor.diagnosed",
    "doctor.drafted",
    "doctor.checked",
    "doctor.proposal_discarded",
    "doctor.proposal_offered",
    "doctor.proposal_result",
    "doctor.proposal_expired",
    "doctor.consult_closed",
    "doctor.gave_up",
)


@dataclass
class Ev:
    kind: str
    ts: datetime = T0
    why: str = "because"
    actor: str = "fleet-doctor"
    actor_class: str = "agent"
    refs: dict[str, Any] = field(default_factory=lambda: {"incident": INC, "device": "arm-1"})
    data: Any = None


def _refs(**extra):
    return {"incident": INC, "device": "arm-1", **extra}


def _hyp(claim, how=""):
    return {"claim": claim, "how_to_check": how}


def advised(minutes, *hyps, ask="ask_1", **data):
    return Ev(
        "doctor.advised",
        ts=T0 + timedelta(minutes=minutes),
        refs=_refs(ask=ask),
        data={"hypotheses": list(hyps), "model": "fake-templated", "parsed_ok": True, **data},
    )


def diagnosed(minutes, *hyps, consult="cons_1", **data):
    return Ev(
        "doctor.diagnosed",
        ts=T0 + timedelta(minutes=minutes),
        refs=_refs(consult=consult),
        data={"hypotheses": list(hyps), "model": "fake-templated", **data},
    )


def test_ledger_is_newest_first_with_sources_and_ids():
    evs = [
        advised(1, _hyp("Camera cable loose", "Wiggle it"), _hyp("Lens covered")),
        diagnosed(5, _hyp("Exposure threshold too low", "Read cfg/vision.yaml"), stub=True),
        Ev("incident.reported", data={"kind": "camera_occluded"}),
    ]
    ledger = build_story(evs, INC).ledger
    assert [(h["source"], h["claim"]) for h in ledger] == [
        ("consult", "Exposure threshold too low"),
        ("ask", "Camera cable loose"),  # one event's hypotheses keep their rank
        ("ask", "Lens covered"),
    ]
    first = ledger[0]
    assert first["at"] == T0 + timedelta(minutes=5)
    assert first["consult"] == "cons_1" and "ask" not in first
    assert first["how_to_check"] == "Read cfg/vision.yaml"
    assert first["model"] == "fake-templated" and first["stub"] is True
    assert ledger[1]["ask"] == "ask_1" and ledger[1]["stub"] is False
    assert ledger[2]["how_to_check"] == ""


def test_ledger_reads_only_advised_and_diagnosed():
    evs = [
        Ev("doctor.asked", refs=_refs(ask="ask_1"), data={"hypotheses": [_hyp("not here")]}),
        Ev("doctor.drafted", refs=_refs(consult="c"), data={"hypotheses": [_hyp("nor here")]}),
        Ev("incident.reported", data={"hypotheses": [_hyp("nor here")]}),
    ]
    assert build_story(evs, INC).ledger == []


@pytest.mark.parametrize(
    "hypotheses",
    [None, "Camera cable loose", {"claim": "x"}, 42, [], [None, 3, ["claim"]]],
)
def test_malformed_hypotheses_add_nothing(hypotheses):
    ev = Ev("doctor.advised", refs=_refs(ask="ask_1"), data={"hypotheses": hypotheses})
    s = build_story([ev], INC)
    assert s.ledger == []
    assert s.steps[0].line.startswith("The Fleet Doctor sent 0 hypotheses to arm-1")


def test_items_without_a_claim_are_skipped_and_strings_are_claims():
    ev = diagnosed(
        0,
        {"how_to_check": "no claim"},
        {"claim": "   ", "how_to_check": "blank claim"},
        {"claim": 7},
        "  A bare string claim  ",
        {"claim": "Kept", "how_to_check": ["not", "a", "string"]},
    )
    ledger = build_story([ev], INC).ledger
    assert [(h["claim"], h["how_to_check"]) for h in ledger] == [
        ("A bare string claim", ""),
        ("Kept", ""),
    ]


def test_missing_or_odd_data_and_refs_do_not_crash():
    for kind in DOCTOR_KINDS:
        for data in (None, {}, {"hypotheses": "x", "model": 3, "stub": "yes", "outcome": 4}):
            s = build_story([Ev(kind, refs={"incident": INC}, data=data)], INC)
            (step,) = s.steps
            assert step.line and step.label and step.doctor is not None
            assert isinstance(s.status, tuple) and isinstance(s.ledger, list)
            s.to_dict()
    # A ledger entry without its id ref still reads.
    ev = Ev("doctor.advised", refs={"incident": INC}, data={"hypotheses": [_hyp("x")]})
    (entry,) = build_story([ev], INC).ledger
    assert entry["ask"] is None and entry["model"] == ""


def test_every_doctor_kind_has_a_label_line_and_stage():
    for kind in DOCTOR_KINDS:
        assert kind in story.LABELS and kind in story.LINES, kind
        assert story.STAGE[kind] == story.DOCTOR_STAGE
        assert kind not in story.NOT_IN_CHAINS


def test_only_doctor_steps_carry_doctor_data():
    evs = [Ev("incident.reported", data={"kind": "x"}), advised(1, _hyp("y"))]
    reported, adv = build_story(evs, INC).steps
    assert reported.doctor is None
    assert adv.doctor["model"] == "fake-templated"


def test_lines_say_what_the_doctor_did_and_announce_stubs():
    def line(kind, refs=None, **data):
        return build_story([Ev(kind, refs=_refs(**(refs or {})), data=data)], INC).steps[0].line

    assert line("doctor.asked", trigger="T2", question="Why is the frame dark?") == (
        "The healer on arm-1 asked the Fleet Doctor for advice (trigger T2): Why is the frame dark?"
    )
    assert line(
        "doctor.advised", hypotheses=[_hyp("a")], model="fake-templated", removed_for_safety=1
    ) == (
        "The Fleet Doctor sent 1 hypothesis to arm-1 (model fake-templated). "
        "1 hypothesis named a safety or visor path and was removed."
    )
    assert "wasn't the JSON asked for" in line("doctor.advised", parsed_ok=False)
    assert line("doctor.blocked_over_cap", which="spend", spent_today_usd=5.01, cap_usd=5.0) == (
        "The Fleet Doctor did not run: it is over its spend cap ($5.01 of $5.00 spent today)."
    )
    assert line("doctor.declined", stage="admit", reason="no_code_rev") == (
        "The Fleet Doctor declined at admit: no code rev."
    )
    assert line("doctor.consult_opened", origin="device", trigger="T2", attempt=2) == (
        "The Fleet Doctor opened a consult on this incident (attempt 2) because the device "
        "asked for one, trigger T2."
    )
    assert line("doctor.diagnosed", hypotheses=[_hyp("a"), _hyp("b")], stub=True) == (
        "The Fleet Doctor diagnosed the incident: 2 hypotheses (stub)."
    )
    assert line("doctor.drafted", paths=["vision/exposure.py"], has_repro_test=True, stub=True) == (
        "The Fleet Doctor drafted a fix touching vision/exposure.py with a reproduction test. "
        "It is a labeled stub patch, not a real fix."
    )
    assert line("doctor.checked", stub=True) == "The Fleet Doctor's checks are a stub: nothing ran."
    assert line("doctor.checked", passed=False, summary="2 device tests failed.") == (
        "The fix failed the Fleet Doctor's checks. 2 device tests failed."
    )
    assert line("doctor.proposal_discarded", reason="touches_safety") == (
        "The Fleet Doctor discarded its fix: it touches the safety layer or visor."
    )
    assert line("doctor.proposal_discarded", reason="safety_paths_unknown").endswith(
        "the safety paths aren't confirmed yet."
    )
    assert line("doctor.proposal_offered", {"proposal": "prop_1"}, stub_checked=True) == (
        "The Fleet Doctor offered fix prop_1 to arm-1. Its checks were a stub."
    )
    assert line(
        "doctor.proposal_result", {"proposal": "prop_1", "overlay": "ov-9"}, result="applied"
    ) == ("arm-1 reported fix prop_1 applied as overlay ov-9.")
    assert line("doctor.proposal_expired", {"proposal": "prop_1"}) == (
        "Fix prop_1 expired before arm-1 applied it."
    )
    assert line("doctor.consult_closed", {"consult": "cons_1"}, outcome="failed_checks") == (
        "The Fleet Doctor closed consult cons_1: failed checks."
    )
    assert line("doctor.gave_up", reason="out of consults", consults_used=2) == (
        "The Fleet Doctor gave up on this incident after 2 consults: out of consults."
    )


def _closed(minutes, outcome):
    return Ev(
        "doctor.consult_closed",
        ts=T0 + timedelta(minutes=minutes),
        refs=_refs(consult="c"),
        data={"outcome": outcome},
    )


def _opened(minutes):
    return Ev("doctor.consult_opened", ts=T0 + timedelta(minutes=minutes), refs=_refs(consult="c"))


REPORTED = Ev("incident.reported", data={"kind": "camera_occluded"})


@pytest.mark.parametrize(
    "outcome,expected",
    [
        ("gave_up", ("Fleet Doctor gave up", "bad")),
        ("discarded", ("Fleet Doctor fix discarded", "bad")),
        # Nothing was applied and kept, so the incident isn't fixed: never info.
        ("advised", ("Fleet Doctor advised only", "bad")),
        ("declined", ("Fleet Doctor declined", "bad")),
        ("stale", ("Fleet Doctor fix stale", "bad")),
        ("rejected", ("Fleet Doctor fix rejected", "bad")),
        ("expired", ("Fleet Doctor fix expired", "bad")),
        ("something_new", ("consult closed: something new", "info")),
        (None, ("Fleet Doctor consult closed", "info")),
    ],
)
def test_a_closed_consult_picks_its_status_from_the_outcome(outcome, expected):
    assert build_story([REPORTED, _opened(1), _closed(2, outcome)], INC).status == expected


def test_a_held_close_sets_no_status():
    held = Ev("overlay.held", ts=T0 + timedelta(minutes=2), refs=_refs(overlay="ov-9"))
    s = build_story([REPORTED, _opened(1), held, _closed(3, "held")], INC)
    assert s.status == ("overlay held", "good")


def test_a_held_close_after_lock_in_keeps_the_gate_status():
    evs = [
        REPORTED,
        _opened(1),
        Ev("overlay.held", ts=T0 + timedelta(minutes=2), refs=_refs(overlay="ov-9")),
        Ev("overlay.locked_in", ts=T0 + timedelta(minutes=3), refs=_refs(overlay="ov-9")),
        Ev("candidate.created", ts=T0 + timedelta(minutes=3), refs=_refs(candidate="cand_1")),
        Ev("candidate.promoted", ts=T0 + timedelta(minutes=4), refs=_refs(candidate="cand_1")),
        _closed(5, "held"),  # the tick saw the held overlay after the gate moved on
    ]
    assert build_story(evs, INC).status == ("in the fleet", "good")


def test_doctor_statuses_never_cover_a_work_order_that_needs_a_person():
    wo = Ev(
        "workorder.created",
        ts=T0 + timedelta(minutes=1),
        refs=_refs(work_order="wo_1", status="needs_human"),
        data={"part_name": "gripper servo", "quantity": 1},
    )
    needs_person = ("work order needs a person", "bad")
    assert build_story([REPORTED, wo, _opened(2)], INC).status == needs_person
    offered = Ev("doctor.proposal_offered", ts=T0 + timedelta(minutes=3), refs=_refs(consult="c"))
    assert build_story([REPORTED, wo, _opened(2), offered], INC).status == needs_person
    assert build_story([REPORTED, wo, _opened(2), _closed(3, "gave_up")], INC).status == (
        needs_person
    )
    # Once the order moves on (not a Doctor step), the Doctor's status shows again.
    posted = Ev(
        "workorder.card_posted",
        ts=T0 + timedelta(minutes=4),
        refs=_refs(work_order="wo_1", status="posted"),
    )
    assert build_story([REPORTED, wo, posted, _opened(5)], INC).status == (
        "Fleet Doctor consulting",
        "wait",
    )


def test_doctor_statuses_never_cover_a_frozen_candidate():
    frozen = Ev("candidate.frozen", ts=T0 + timedelta(minutes=1), refs=_refs(candidate="c1"))
    assert build_story([REPORTED, frozen, _opened(2)], INC).status == ("frozen", "bad")


def test_ledger_text_is_capped_for_display():
    long_claim, long_how = "c" * 2000, "h" * 600
    ev = diagnosed(0, _hyp(long_claim, long_how), _hyp("short", "fine"))
    first, second = build_story([ev], INC).ledger
    cap = story.LEDGER_TEXT_MAX
    assert len(first["claim"]) == cap and first["claim"].endswith("…")
    assert len(first["how_to_check"]) == cap and first["how_to_check"].endswith("…")
    assert (second["claim"], second["how_to_check"]) == ("short", "fine")
    exact = diagnosed(0, _hyp("x" * cap))
    assert build_story([exact], INC).ledger[0]["claim"] == "x" * cap


def test_status_while_a_consult_is_open_and_after_an_ask():
    reported = Ev("incident.reported", data={"kind": "camera_occluded"})
    asked = Ev("doctor.asked", ts=T0 + timedelta(minutes=1), refs=_refs(ask="a"))
    assert build_story([reported, asked, advised(1, _hyp("x"))], INC).status == ("open", "bad")
    opened = Ev("doctor.consult_opened", ts=T0 + timedelta(minutes=2), refs=_refs(consult="c"))
    diag = diagnosed(3, _hyp("x"))
    s = build_story([reported, opened, diag], INC)
    assert s.status == ("Fleet Doctor consulting", "wait")
    offered = Ev(
        "doctor.proposal_offered",
        ts=T0 + timedelta(minutes=4),
        refs=_refs(consult="c", proposal="p"),
    )
    assert build_story([reported, opened, diag, offered], INC).status == (
        "Fleet Doctor fix offered",
        "wait",
    )


def test_same_ts_doctor_events_keep_their_write_order_after_the_heal():
    """One tick can open, refuse and close a consult, or close attempt 1 and open attempt 2,
    all at one `now`: written order wins among doctor kinds."""
    evs = [
        Ev("doctor.consult_opened", refs=_refs(consult="c1"), data={"attempt": 1}),
        Ev("doctor.blocked_over_cap", refs=_refs(consult="c1")),
        Ev("doctor.consult_closed", refs=_refs(consult="c1"), data={"outcome": "failed_checks"}),
        Ev("doctor.consult_opened", refs=_refs(consult="c2"), data={"attempt": 2}),
        Ev("workorder.created", refs=_refs(work_order="wo_1", status="pending")),
        Ev("incident.agent_acted", actor_class="agent"),
        Ev("incident.reported", data={"kind": "camera_occluded"}),
    ]
    steps = build_story(evs, INC).steps
    assert [(s.kind, s.refs.get("consult")) for s in steps] == [
        ("incident.reported", None),
        ("incident.agent_acted", None),
        ("doctor.consult_opened", "c1"),
        ("doctor.blocked_over_cap", "c1"),
        ("doctor.consult_closed", "c1"),
        ("doctor.consult_opened", "c2"),
        ("workorder.created", None),
    ]


def test_a_doctor_fix_overlay_reads_after_its_result_but_the_heal_overlay_stays_first():
    """`record_result` writes `doctor.proposal_result`, then `overlay.applied`, with one `ts`
    (the device's `occurred_at`). The Doctor's overlay keeps that order; the device's own
    heal overlay (no `proposal` ref) still sorts with the heal, before doctor kinds."""
    evs = [
        Ev("doctor.proposal_result", refs=_refs(proposal="p", overlay="ov-doc")),
        Ev("overlay.applied", refs=_refs(proposal="p", overlay="ov-doc"), actor="device:arm-1"),
        Ev("doctor.consult_opened", refs=_refs(consult="c1")),
        Ev("overlay.applied", refs=_refs(overlay="ov-heal"), actor="device:arm-1"),
    ]
    steps = build_story(evs, INC).steps
    assert [(s.kind, s.refs.get("overlay")) for s in steps] == [
        ("overlay.applied", "ov-heal"),
        ("doctor.proposal_result", "ov-doc"),
        ("overlay.applied", "ov-doc"),
        ("doctor.consult_opened", None),
    ]


def test_doctor_refs_appear_in_steps_and_the_chain():
    evs = [
        Ev("incident.reported", data={"kind": "camera_occluded"}),
        diagnosed(1, _hyp("x"), stub=True),
        Ev(
            "doctor.proposal_result",
            ts=T0 + timedelta(minutes=2),
            actor="device:arm-1",
            refs=_refs(consult="cons_1", proposal="prop_1", overlay="ov-9"),
            data={"result": "applied"},
        ),
    ]
    s = build_story(evs, INC)
    assert s.steps[2].refs == _refs(consult="cons_1", proposal="prop_1", overlay="ov-9")
    assert s.steps[2].evidence is None
    assert s.chain == "camera_occluded → diagnosed (stub) → fix applied"


def test_to_dict_adds_the_ledger_and_keeps_step_shapes():
    d = build_story([advised(1, _hyp("Camera cable loose", "Wiggle it"))], INC).to_dict()
    assert d["ledger"] == [
        {
            "at": "2026-11-07T22:03:11+00:00",
            "source": "ask",
            "claim": "Camera cable loose",
            "how_to_check": "Wiggle it",
            "ask": "ask_1",
            "model": "fake-templated",
            "stub": False,
        }
    ]
    keys = {"at", "actor", "actor_class", "kind", "line", "why", "label", "refs"}
    assert set(d["steps"][0]) == keys
    assert build_story([], INC).to_dict()["ledger"] == []
