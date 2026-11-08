"""services/doctor_ask: the L1 handler logic (interfaces.md §3, §6.6). The HTTP behavior is
in tests/flows/test_f12a_doctor_ask.py; this pins FR11 and the order of the checks."""

import uuid

import pytest

from romulus_cloud.adapters.blobstore import build_blobstore
from romulus_cloud.adapters.llm import LlmReply
from romulus_cloud.errors import ApiError
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
    """Records what the sessionmaker looked like at the moment of the call."""

    model = "probe"

    def __init__(self, tracked: TrackedSessions) -> None:
        self.tracked, self.seen = tracked, None

    def complete(self, system, user, *, max_tokens=None, timeout_s=None) -> LlmReply:
        self.seen = self.tracked.held()
        return LlmReply('{"hypotheses": [{"claim": "c"}]}', self.model, 10, 5)


@pytest.fixture
def fleet_seeded(db_session, settings):
    seed(db_session, build_blobstore(settings.blob_dir))
    db_session.commit()
    return db_session


def _ask(sessions, settings, now, llm, device_id="arm-1", **over):
    body = DoctorAsk.model_validate({"trigger": "T4", "incident_id": str(uuid.uuid4()), **over})
    return doctor_ask.ask(sessions, device_id, body, {}, now, settings=settings, llm=llm)


def test_the_llm_call_runs_with_no_session_open(fleet_seeded, sessions, make_settings, now):
    tracked = TrackedSessions(sessions)
    llm = ProbeLlm(tracked)
    advice = _ask(tracked, make_settings(doctor_mode="fake"), now, llm)
    assert advice.hypotheses[0].claim == "c"
    assert llm.seen == [False]  # the read session was made, and closed, before the call
    assert tracked.held() == [False, False]  # then the write session, closed too


def test_checks_run_in_order_unknown_device_off_frozen(fleet_seeded, sessions, make_settings, now):
    on, off = make_settings(doctor_mode="fake"), make_settings(doctor_mode="off")
    llm = ProbeLlm(TrackedSessions(sessions))
    with pytest.raises(ApiError) as err:
        _ask(sessions, off, now, None, device_id="nope")
    assert (err.value.status, err.value.error) == (404, "unknown_device")
    with pytest.raises(ApiError) as err:
        _ask(sessions, off, now, None)
    assert (err.value.status, err.value.error) == (503, "doctor_off")

    fleet.freeze(fleet_seeded, "kent", "test", now)
    fleet_seeded.commit()
    with pytest.raises(ApiError) as err:
        _ask(sessions, on, now, llm)
    assert (err.value.status, err.value.error) == (409, "fleet_frozen")
    assert llm.seen is None  # never called


def test_a_bad_price_refuses_before_spending(fleet_seeded, sessions, make_settings, now):
    settings = make_settings(doctor_mode="fake", doctor_usd_per_1k_tokens=float("nan"))
    llm = ProbeLlm(TrackedSessions(sessions))
    with pytest.raises(ApiError) as err:
        _ask(sessions, settings, now, llm)
    assert (err.value.status, err.value.error) == (429, "doctor_cap")
    assert "can't price the call" in err.value.detail
    assert llm.seen is None
