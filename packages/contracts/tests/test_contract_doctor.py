"""Fleet Doctor contracts (doctor.py and the optional fields it adds, D28).

Device-sent models stay lenient (a rejected payload trips the breaker or gets dead-lettered),
`applied` must name its overlay, and the proposal body hashes the same every time.
"""

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from romulus_contracts import (
    CheckInResponse,
    ConsultRequest,
    DoctorAdvice,
    DoctorAsk,
    IncidentReport,
    Overlay,
    Proposal,
    ProposalPointer,
    ProposalResult,
    proposal_body_and_sha256,
)
from romulus_contracts.manifest import canonical_json
from romulus_contracts.schema import MODELS

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"
INCIDENT_ID = "6d0a4c2e-9b1f-4e3a-8c5d-7f2e1a0b9c84"


def load(name):
    return json.loads((EXAMPLES / name).read_text())


def proposal_body():
    return load("proposal.json")


def incident_body(**extra):
    return {
        "incident_id": INCIDENT_ID,
        "occurred_at": "2026-11-08T03:12:40Z",
        "kind": "unexpected_release",
    } | extra


# --- round trips ---------------------------------------------------------------------


@pytest.mark.parametrize(
    ("model", "name"),
    [
        (DoctorAsk, "doctor_ask.json"),
        (DoctorAdvice, "doctor_advice.json"),
        (Proposal, "proposal.json"),
        (ProposalResult, "proposal_result.json"),
    ],
)
def test_round_trip(model, name):
    parsed = model.model_validate(load(name))
    assert model.model_validate_json(parsed.model_dump_json()) == parsed


def test_new_models_have_json_schemas():
    for name in ("DoctorAsk", "ProposalResult", "DoctorAdvice", "Proposal"):
        assert name in MODELS
        MODELS[name].model_json_schema()


# --- DoctorAsk -----------------------------------------------------------------------


def test_ask_minimal_body_gets_defaults():
    ask = DoctorAsk.model_validate({"trigger": "T7"})
    assert ask.incident_id is None and ask.question == "" and ask.budget_s == 20
    assert ask.context.signals == {} and ask.context.files == [] and ask.context.tier is None


def test_ask_is_lenient_about_nulls_numbers_and_extras():
    ask = DoctorAsk.model_validate(
        {
            "trigger": 4,
            "question": None,
            "budget_s": None,
            "context": {
                "signals": None,
                "log_tail": None,
                "recent_steps": ["read hold.py", {"tool": "verify", "ok": False}],
                "files": None,
                "tier": "3",
                "gpu": "none",
            },
            "healer": "h3",
        }
    )
    assert ask.trigger == "4" and ask.question == "" and ask.budget_s == 20
    assert ask.context.signals == {} and ask.context.log_tail == [] and ask.context.files == []
    assert ask.context.recent_steps[1] == {"tool": "verify", "ok": False}
    assert ask.context.model_extra == {"gpu": "none"} and ask.model_extra == {"healer": "h3"}
    assert DoctorAsk.model_validate({"trigger": "T4", "context": None}).context.log_tail == []


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"trigger": None},
        {"trigger": True},
        {"trigger": "T4", "incident_id": "not-a-uuid"},
        {"trigger": "T4", "budget_s": "soon"},
    ],
)
def test_ask_rejects(body):
    with pytest.raises(ValidationError):
        DoctorAsk.model_validate(body)


# --- ConsultRequest and IncidentReport.consult ---------------------------------------


@pytest.mark.parametrize(
    ("attempts", "expected"),
    [(2, 2), ("3", 3), (2.0, 2), (None, None), ("two", None), (True, None), (1.5, None)],
)
def test_consult_attempts_read_leniently(attempts, expected):
    assert ConsultRequest.model_validate({"attempts": attempts}).attempts == expected


def test_consult_trigger_reads_numbers_as_strings():
    assert ConsultRequest.model_validate({"trigger": 2}).trigger == "2"
    assert ConsultRequest().wanted is False


def test_incident_consult_round_trips():
    consult = {"wanted": True, "trigger": "T2", "attempts": 2}
    inc = IncidentReport.model_validate(incident_body(consult=consult))
    assert inc.consult == ConsultRequest(**consult)
    assert inc.model_dump(mode="json")["consult"] == consult


@pytest.mark.parametrize(("value", "wanted"), [(True, True), (False, False)])
def test_incident_consult_bool_is_shorthand(value, wanted):
    assert IncidentReport.model_validate(incident_body(consult=value)).consult.wanted is wanted


@pytest.mark.parametrize("value", [None, "yes", [1], 7, {"wanted": "please"}])
def test_malformed_consult_reads_as_none_and_keeps_the_incident(value):
    inc = IncidentReport.model_validate(incident_body(consult=value))
    assert inc.consult is None and str(inc.incident_id) == INCIDENT_ID


# --- older payloads without the new fields --------------------------------------------


def test_old_payloads_still_parse():
    assert IncidentReport.model_validate(incident_body()).consult is None
    d1 = IncidentReport.model_validate(load("incident_d1_code_patch.json"))
    assert d1.consult is None and d1.overlay.proposal_id is None
    resp = CheckInResponse.model_validate(load("checkin_response.json"))
    assert resp.proposal is None


def test_overlay_proposal_id():
    ov = Overlay.model_validate({"overlay_id": "ov-d1-0002", "proposal_id": "prop_1"})
    assert ov.proposal_id == "prop_1"


# --- CheckInResponse.proposal --------------------------------------------------------


def test_checkin_response_carries_a_pointer():
    resp = CheckInResponse.model_validate(
        load("checkin_response.json")
        | {
            "proposal": {
                "proposal_id": "prop_1",
                "incident_id": INCIDENT_ID,
                "expires_at": "2026-11-08T04:20:00",
                "sha256": "ignored",
            }
        }
    )
    assert isinstance(resp.proposal, ProposalPointer)
    assert resp.proposal.expires_at == datetime(2026, 11, 8, 4, 20, tzinfo=UTC)
    assert resp.model_dump(mode="json")["proposal"] == {
        "proposal_id": "prop_1",
        "incident_id": INCIDENT_ID,
        "expires_at": "2026-11-08T04:20:00Z",
    }


# --- ProposalResult ------------------------------------------------------------------


@pytest.mark.parametrize("result", ["rejected", "stale", "failed_verify"])
def test_result_without_overlay(result):
    r = ProposalResult.model_validate({"result": result, "reason": None})
    assert r.result == result and r.overlay_id is None and r.reason == ""


def test_applied_with_overlay():
    r = ProposalResult.model_validate({"result": "applied", "overlay_id": "ov-d1-0002", "x": 1})
    assert r.overlay_id == "ov-d1-0002" and r.model_extra == {"x": 1}


@pytest.mark.parametrize(
    "overlay", [{}, {"overlay_id": None}, {"overlay_id": ""}, {"overlay_id": " "}]
)
def test_applied_needs_overlay_id(overlay):
    with pytest.raises(ValidationError, match="overlay_id"):
        ProposalResult.model_validate({"result": "applied"} | overlay)


@pytest.mark.parametrize("result", ["held", "Applied", None])
def test_result_rejects_unknown_values(result):
    with pytest.raises(ValidationError):
        ProposalResult.model_validate({"result": result, "overlay_id": "ov-1"})


# --- Proposal and its hash -----------------------------------------------------------


@pytest.mark.parametrize("missing", ["code_patch", "expires_at", "base", "incident_id"])
def test_proposal_requires(missing):
    body = proposal_body()
    del body[missing]
    with pytest.raises(ValidationError):
        Proposal.model_validate(body)


def test_proposal_base_requires_code_rev():
    body = proposal_body()
    del body["base"]["code_rev"]
    with pytest.raises(ValidationError):
        Proposal.model_validate(body)


def test_proposal_sha256_is_stable_and_matches_the_bytes():
    body, sha = proposal_body_and_sha256(Proposal.model_validate(proposal_body()))
    again, sha_again = proposal_body_and_sha256(Proposal.model_validate(proposal_body()))
    assert (body, sha) == (again, sha_again)
    assert sha == hashlib.sha256(body).hexdigest()
    # The bytes are canonical JSON and parse back to the same proposal.
    assert body == canonical_json(json.loads(body))
    assert Proposal.model_validate_json(body) == Proposal.model_validate(proposal_body())


def test_proposal_sha256_ignores_input_key_order_and_accepts_a_dict():
    raw = proposal_body()
    reordered = dict(reversed(list(raw.items())))
    assert proposal_body_and_sha256(raw) == proposal_body_and_sha256(reordered)
    assert proposal_body_and_sha256(raw) == proposal_body_and_sha256(Proposal.model_validate(raw))


def test_proposal_sha256_changes_with_the_patch():
    raw = proposal_body()
    _, sha = proposal_body_and_sha256(raw)
    _, other = proposal_body_and_sha256(raw | {"code_patch": raw["code_patch"] + "\n"})
    assert sha != other


def test_proposal_body_drops_unknown_fields():
    body, _ = proposal_body_and_sha256(proposal_body() | {"internal_note": "x"})
    assert "internal_note" not in json.loads(body)
