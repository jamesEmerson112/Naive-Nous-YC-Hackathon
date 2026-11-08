"""F12a · Fleet Doctor L1 (interfaces.md §3, §12): the healer asks mid-heal and gets
hypotheses back; off, frozen, caps and LLM failures each answer one clear non-200 (the
device reads any of them as "no answer"); hypotheses naming a safety path are dropped; the
ask's events land on the incident's story.

Everything goes through the HTTP API with the contract examples as payloads. The LLM is
FakeLlm (DOCTOR_MODE=fake) unless a test swaps in a scripted one through
`app.dependency_overrides[get_doctor_llm]`.
"""

import json
import uuid

import pytest
from sqlalchemy import select

from romulus_cloud.adapters.llm import FAKE_BANNER, FAKE_MODEL, LlmError, LlmReply, LlmTimeout
from romulus_cloud.api.doctor import get_doctor_llm
from romulus_cloud.models import Event

INCIDENT = "6d0a4c2e-9b1f-4e3a-8c5d-7f2e1a0b9c84"  # incident_consult_wanted.json, doctor_ask.json
ASK = "/v1/devices/arm-1/doctor/ask"


@pytest.fixture
def settings(make_settings):
    """The app and the seed share these: the Doctor on, with FakeLlm."""
    return make_settings(doctor_mode="fake")


@pytest.fixture(autouse=True)
def _server_clock(clock):
    """Events are stamped with the route's `now`, which the flows clock controls."""
    return clock


@pytest.fixture
def body(example):
    return example("doctor_ask")


@pytest.fixture
def configure(app):
    """`configure(doctor_daily_usd_cap=...)`: change the running app's settings."""

    def apply(**update):
        app.state.settings = app.state.settings.model_copy(update=update)

    return apply


class ScriptedLlm:
    """Answers with `text`, or raises `error`; remembers each call's limits."""

    model = "scripted-model"

    def __init__(self, text: str = "", error: Exception | None = None, on_call=None) -> None:
        self.text, self.error, self.on_call = text, error, on_call
        self.calls: list[dict] = []

    def complete(self, system, user, *, max_tokens=None, timeout_s=None) -> LlmReply:
        self.calls.append({"max_tokens": max_tokens, "timeout_s": timeout_s})
        if self.on_call:
            self.on_call()
        if self.error:
            raise self.error
        return LlmReply(self.text, self.model, prompt_tokens=100, completion_tokens=50)


@pytest.fixture
def use_llm(app):
    def use(llm):
        app.dependency_overrides[get_doctor_llm] = lambda: llm
        return llm

    yield use
    app.dependency_overrides.pop(get_doctor_llm, None)


def _events(db_session, kind: str, refs: dict | None = None) -> list[Event]:
    stmt = select(Event).where(Event.kind == kind).order_by(Event.ts, Event.recorded_at)
    if refs:
        stmt = stmt.where(Event.refs.contains(refs))
    return list(db_session.scalars(stmt))


def _doctor_events(db_session) -> list[Event]:
    return list(db_session.scalars(select(Event).where(Event.kind.like("doctor.%"))))


# --- the happy path ----------------------------------------------------------------------


def test_ask_returns_advice_and_both_events_land_on_the_story(
    client, seeded, example, body, db_session
):
    reported = client.post("/v1/devices/arm-1/incidents", json=example("incident_consult_wanted"))
    assert reported.status_code == 201

    resp = client.post(ASK, json=body)
    assert resp.status_code == 200, resp.text
    advice = resp.json()
    assert advice["ask_id"].startswith("ask_") and advice["model"] == FAKE_MODEL
    (hyp,) = advice["hypotheses"]  # FakeLlm answers in prose: one hypothesis
    assert hyp["claim"].startswith(FAKE_BANNER) and hyp["how_to_check"] == ""

    story = client.get(f"/v1/incidents/{INCIDENT}/story").json()
    doctor = [s for s in story["steps"] if s["kind"].startswith("doctor.")]
    assert [s["kind"] for s in doctor] == ["doctor.asked", "doctor.advised"]
    for step in doctor:
        assert step["refs"] == {"device": "arm-1", "ask": advice["ask_id"], "incident": INCIDENT}
        assert step["actor"] == "fleet-doctor" and step["actor_class"] == "agent"
    assert [s["label"] for s in doctor] == ["asked the Fleet Doctor", "Fleet Doctor advised"]

    (asked,) = _events(db_session, "doctor.asked")
    assert asked.data["trigger"] == "T4"
    assert asked.data["question"] == body["question"]
    assert asked.data["incident_kind"] == "unexpected_release"
    (advised,) = _events(db_session, "doctor.advised")
    data = advised.data
    assert data["hypotheses"] == advice["hypotheses"]
    assert data["parsed_ok"] is False and data["removed_for_safety"] == 0
    assert data["model"] == FAKE_MODEL and data["usage_estimated"] is True
    assert data["prompt_tokens"] > 0 and data["completion_tokens"] > 0
    assert data["cost_usd"] == pytest.approx(
        (data["prompt_tokens"] + data["completion_tokens"]) / 1000 * 0.001
    )
    assert data["latency_ms"] >= 0 and data["incident_kind"] == "unexpected_release"


def test_the_llm_gets_less_time_than_the_device_waits(client, seeded, body, use_llm):
    llm = use_llm(ScriptedLlm('{"hypotheses": [{"claim": "c", "how_to_check": "h"}]}'))
    for budget in (5, 60):
        assert client.post(ASK, json=body | {"budget_s": budget}).status_code == 200
    # min(DOCTOR_ASK_TIMEOUT_S=15, budget_s - 2), with DOCTOR_ASK_MAX_TOKENS=400.
    assert llm.calls == [
        {"max_tokens": 400, "timeout_s": 3},
        {"max_tokens": 400, "timeout_s": 15},
    ]


def test_no_session_is_held_while_the_llm_answers(app, client, seeded, body, use_llm):
    """FR11: every session the route opened is closed before the LLM call (a `SessionDep`
    on the route would also show up here, since it uses the same sessionmaker)."""
    make = app.state.sessionmaker
    made, open_now = [], set()

    def tracked():
        session = make()
        made.append(session)
        open_now.add(id(session))
        close = session.close

        def tracked_close():
            open_now.discard(id(session))
            close()

        session.close = tracked_close
        return session

    app.state.sessionmaker = tracked
    seen = {}
    use_llm(
        ScriptedLlm(
            "[]",
            on_call=lambda: seen.update(
                open=len(open_now), in_tx=[s.in_transaction() for s in made], made=len(made)
            ),
        )
    )
    assert client.post(ASK, json=body).status_code == 200
    assert seen == {"open": 0, "in_tx": [False], "made": 1}
    assert len(made) == 2 and not open_now  # then one write session, closed too


# --- refusals ----------------------------------------------------------------------------


def test_unknown_device_answers_404(client, seeded, body, db_session):
    resp = client.post("/v1/devices/nope/doctor/ask", json=body)
    assert resp.status_code == 404 and resp.json()["error"] == "unknown_device"
    assert _doctor_events(db_session) == []


def test_doctor_off_answers_503(client, seeded, body, configure, db_session):
    configure(doctor_mode="off")
    resp = client.post(ASK, json=body)
    assert resp.status_code == 503 and resp.json()["error"] == "doctor_off"
    assert _doctor_events(db_session) == []


def test_frozen_fleet_answers_409(client, seeded, body, db_session):
    assert client.post("/v1/fleet/freeze", json={"by": "kent", "why": "demo"}).status_code == 200
    resp = client.post(ASK, json=body)
    assert resp.status_code == 409 and resp.json()["error"] == "fleet_frozen"
    assert _doctor_events(db_session) == []


def test_unsupported_contract_version_answers_400(client, seeded, body):
    resp = client.post(ASK, json=body | {"contract_version": "9.9"})
    assert resp.status_code == 400 and resp.json()["error"] == "unsupported_contract_version"
    unversioned = {k: v for k, v in body.items() if k != "contract_version"}
    assert client.post(ASK, json=unversioned).status_code == 200  # optional on this route


# --- caps --------------------------------------------------------------------------------


def test_the_seventh_ask_on_one_incident_is_capped(client, seeded, body, db_session):
    # The incident hasn't been reported yet: the asks still count against it.
    for _ in range(6):
        assert client.post(ASK, json=body).status_code == 200
    resp = client.post(ASK, json=body)
    assert resp.status_code == 429 and resp.json()["error"] == "doctor_cap"
    assert "6 of its 6 asks" in resp.json()["detail"]

    assert len(_events(db_session, "doctor.asked", {"incident": INCIDENT})) == 6
    (blocked,) = _events(db_session, "doctor.blocked_over_cap")
    assert blocked.data["which"] == "asks" and blocked.data["cap_usd"] == 5.0
    assert blocked.data["spent_today_usd"] > 0
    assert blocked.refs["incident"] == INCIDENT and blocked.refs["ask"].startswith("ask_")
    assert "incident_kind" not in blocked.data  # not reported, so not known

    other = body | {"incident_id": str(uuid.uuid4())}  # another incident has its own cap
    assert client.post(ASK, json=other).status_code == 200


def test_the_daily_spend_cap_blocks_the_next_ask(client, seeded, body, configure, db_session):
    configure(doctor_daily_usd_cap=1e-9)
    assert client.post(ASK, json=body).status_code == 200  # nothing spent yet: allowed
    resp = client.post(ASK, json=body)
    assert resp.status_code == 429 and resp.json()["error"] == "doctor_cap"
    (blocked,) = _events(db_session, "doctor.blocked_over_cap")
    assert blocked.data["which"] == "spend" and blocked.data["cap_usd"] == 1e-9
    (advised,) = _events(db_session, "doctor.advised")
    assert blocked.data["spent_today_usd"] == pytest.approx(advised.data["cost_usd"])


def test_an_ask_with_no_incident_counts_toward_the_device_cap(
    client, seeded, body, configure, db_session
):
    configure(doctor_asks_per_device_day=2)
    loose = {k: v for k, v in body.items() if k != "incident_id"} | {"question": "x" * 500}
    for _ in range(2):
        assert client.post(ASK, json=loose).status_code == 200
    resp = client.post(ASK, json=loose)
    assert resp.status_code == 429 and "2 of its 2 asks today" in resp.json()["detail"]

    asked = _events(db_session, "doctor.asked", {"device": "arm-1"})
    assert len(asked) == 2
    assert all("incident" not in e.refs and len(e.data["question"]) == 200 for e in asked)
    (blocked,) = _events(db_session, "doctor.blocked_over_cap")
    assert blocked.data["which"] == "asks" and "incident" not in blocked.refs
    # The device-day cap is for asks with no incident; an incident ask still goes through.
    assert client.post(ASK, json=body).status_code == 200


# --- what the LLM says, or doesn't ---------------------------------------------------------


def test_hypotheses_naming_a_safety_path_are_removed_and_counted(
    client, seeded, body, use_llm, db_session
):
    answer = {
        "hypotheses": [
            {"claim": "The clamp in visor/limits.py is too tight", "how_to_check": "raise it"},
            {
                "claim": "step() opens the gripper on the ultrasonic reading while HOLDING",
                "how_to_check": "Remove the release branch and run d1_hand_trace",
            },
        ]
    }
    use_llm(ScriptedLlm("Here you go:\n```json\n" + json.dumps(answer) + "\n```"))
    resp = client.post(ASK, json=body)
    assert resp.status_code == 200
    assert [h["claim"] for h in resp.json()["hypotheses"]] == [answer["hypotheses"][1]["claim"]]
    assert resp.json()["model"] == "scripted-model"

    (advised,) = _events(db_session, "doctor.advised")
    data = advised.data
    assert data["removed_for_safety"] == 1 and data["parsed_ok"] is True
    assert data["hypotheses"] == resp.json()["hypotheses"]
    assert (data["prompt_tokens"], data["completion_tokens"]) == (100, 50)
    assert data["usage_estimated"] is False
    assert data["cost_usd"] == pytest.approx(150 / 1000 * 0.001)
    assert "1 named a safety or visor path" in advised.why


def test_an_llm_timeout_answers_504_and_records_a_decline(
    client, seeded, body, use_llm, db_session
):
    use_llm(ScriptedLlm(error=LlmTimeout("read timed out")))
    resp = client.post(ASK, json=body)
    assert resp.status_code == 504 and resp.json()["error"] == "doctor_timeout"

    (asked,) = _events(db_session, "doctor.asked")
    (declined,) = _events(db_session, "doctor.declined")
    assert declined.data["reason"] == "llm timeout" and declined.data["stage"] == "ask"
    assert declined.refs == asked.refs and declined.refs["incident"] == INCIDENT
    assert _events(db_session, "doctor.advised") == []
    assert "cost_usd" not in declined.data  # nothing came back to price


def test_an_llm_error_answers_502(client, seeded, body, use_llm, db_session):
    use_llm(ScriptedLlm(error=LlmError("proxy said 500")))
    resp = client.post(ASK, json=body)
    assert resp.status_code == 502 and resp.json()["error"] == "doctor_unavailable"
    (declined,) = _events(db_session, "doctor.declined")
    assert declined.data["reason"] == "llm error" and "proxy said 500" in declined.data["error"]
