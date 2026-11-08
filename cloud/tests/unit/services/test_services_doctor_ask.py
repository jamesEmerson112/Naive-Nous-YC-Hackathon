"""services/doctor_ask: the L1 handler logic (interfaces.md §3, §6.6). The HTTP behavior is
in tests/flows/test_f12a_doctor_ask.py; this pins FR11 and the order of the checks."""

import uuid

import pytest
from sqlalchemy import func, select

from romulus_cloud.adapters.blobstore import build_blobstore
from romulus_cloud.adapters.llm import LlmReply
from romulus_cloud.errors import ApiError
from romulus_cloud.models import Event
from romulus_cloud.services import doctor_ask, fleet
from romulus_cloud.services.seed import seed
from romulus_contracts import DoctorAsk


class TrackedSessions:
    """A sessionmaker that knows which of its sessions are still open."""

    def __init__(self, make) -> None:
        self.make, self.made, self.open = make, [], set()

    def __call__(self):
        session = self.make()
        self.made.append(session)
        self.open.add(id(session))
        close = session.close

        def tracked_close():
            self.open.discard(id(session))
            close()

        session.close = tracked_close
        return session

    def held(self) -> list[bool]:
        """For each session made: is it still open, or still holding a transaction?"""
        return [id(s) in self.open or s.in_transaction() for s in self.made]


class ProbeLlm:
    """Records, at the moment of the call, what the sessionmaker looked like and how many
    `doctor.asked` events were already committed."""

    model = "probe"

    def __init__(self, tracked: TrackedSessions) -> None:
        self.tracked, self.seen, self.asked = tracked, None, None

    def complete(self, system, user, *, max_tokens=None, timeout_s=None) -> LlmReply:
        self.seen = self.tracked.held()
        with self.tracked.make() as look:  # untracked: the probe's own look at the DB
            kind = Event.kind == "doctor.asked"
            self.asked = look.scalar(select(func.count()).select_from(Event).where(kind))
        return LlmReply('{"hypotheses": [{"claim": "c"}]}', self.model, 10, 5)


@pytest.fixture
def fleet_seeded(db_session, settings):
    seed(db_session, build_blobstore(settings.blob_dir))
    db_session.commit()
    return db_session


def _ask(sessions, settings, now, llm, device_id="arm-1", **over):
    body = DoctorAsk.model_validate({"trigger": "T4", "incident_id": str(uuid.uuid4()), **over})
    return doctor_ask.ask(sessions, device_id, body, {}, now, settings=settings, llm=llm)


def _error(fn) -> tuple[int, str]:
    with pytest.raises(ApiError) as err:
        fn()
    return err.value.status, err.value.error


def test_the_llm_call_runs_with_no_session_open(fleet_seeded, sessions, make_settings, now):
    tracked = TrackedSessions(sessions)
    llm = ProbeLlm(tracked)
    advice = _ask(tracked, make_settings(doctor_mode="fake"), now, llm)
    assert advice.hypotheses[0].claim == "c"
    assert llm.seen == [False]  # the read session was made, and closed, before the call
    assert llm.asked == 1  # and `doctor.asked` was committed before it
    assert tracked.held() == [False, False]  # then the write session, closed too


def test_checks_run_in_order(fleet_seeded, sessions, make_settings, now):
    """404 unknown device, 503 off, 409 frozen, 502 unconfigured; the LLM is never called."""
    on, off = make_settings(doctor_mode="fake"), make_settings(doctor_mode="off")
    broken = doctor_ask.Unconfigured("live LLM needs LLM_MODEL")
    llm = ProbeLlm(TrackedSessions(sessions))
    assert _error(lambda: _ask(sessions, off, now, None, device_id="nope")) == (
        404,
        "unknown_device",
    )
    assert _error(lambda: _ask(sessions, off, now, None)) == (503, "doctor_off")
    assert _error(lambda: _ask(sessions, on, now, broken)) == (502, "doctor_unavailable")

    fleet.freeze(fleet_seeded, "kent", "test", now)
    fleet_seeded.commit()
    assert _error(lambda: _ask(sessions, on, now, broken)) == (409, "fleet_frozen")
    assert _error(lambda: _ask(sessions, on, now, llm)) == (409, "fleet_frozen")
    assert llm.seen is None  # never called


def test_a_bad_price_refuses_before_spending(fleet_seeded, sessions, make_settings, now):
    settings = make_settings(doctor_mode="fake", doctor_usd_per_1k_tokens=float("nan"))
    llm = ProbeLlm(TrackedSessions(sessions))
    with pytest.raises(ApiError) as err:
        _ask(sessions, settings, now, llm)
    assert (err.value.status, err.value.error) == (429, "doctor_cap")
    assert "can't price the call" in err.value.detail
    assert llm.seen is None
