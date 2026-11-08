"""F12b: a held Fleet Doctor overlay locks in without a second Coder pass (PR 9, D24,
interfaces.md §9).

Set-up as in F11: the fleet runs the toy device as code `toy0001`, sim-3 (and sometimes
sim-1) report D1 with the toy's patch and go `held`. The test then makes sim-3's overlay a
Doctor overlay the way the cloud would after `applied`: a `doctor_proposals` row with the
same patch, and `raw.proposal_id` on the overlay. Only a proposal that checks out (same
fleet, same device, applied or held, same patch sha256) skips the Coder; the gate's replay
runs either way.
"""

import io
import sys
import tarfile
from datetime import timedelta
from functools import partial
from pathlib import Path

import pytest
from pydantic import SecretStr
from sqlalchemy import select

from fake_device.scenarios import SCENARIOS
from romulus_cloud import events
from romulus_cloud.adapters.blobstore import build_blobstore
from romulus_cloud.adapters.coder import FakeCoder, LocalAgent, Sandbox
from romulus_cloud.adapters.llm import FakeLlm
from romulus_cloud.models import CodeVersion, Device, DoctorConsult, DoctorProposal, Overlay
from romulus_cloud.services import batch, code_lockin, gate, registry
from romulus_cloud.services.jobs import InlineJobs, Pending
from romulus_cloud.services.seed import FIRMWARE_V1
from romulus_cloud.worker import Step, run_tick

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "fixtures"))
import coder_fakes as fakes  # noqa: E402

S = timedelta(seconds=1)
KW = {"actor": "human:test", "actor_class": "human"}
KEY = "sk-test-F12b-SECRET"
PID = "prop_f12b"
VARIANT = fakes.TOY_PATCH.replace("the spec says keep holding", "the spec says keep the object")


@pytest.fixture
def settings(make_settings):
    return make_settings().model_copy(
        update={
            "coder_provider": "local_agent",
            "llm_base_url": "http://llm.invalid/v1",
            "llm_model": "test-model",
            "llm_api_key": SecretStr(KEY),
        }
    )


@pytest.fixture
def toy_fleet(seeded, settings, monkeypatch):
    monkeypatch.setattr("fake_device.scenarios.D1_CODE_PATCH", fakes.TOY_PATCH)
    store = build_blobstore(settings.blob_dir)
    registry.create_code(
        seeded, store, registry.build_tarball(fakes.toy_files()), rev="toy0001", **KW
    )
    registry.create_firmware(seeded, FIRMWARE_V1 | {"version": 2, "code_rev": "toy0001"}, **KW)
    for d in seeded.scalars(select(Device)):
        d.assigned_firmware = 2
    seeded.commit()
    return seeded


class SpyCoder(FakeCoder):
    """The pass-through coder, counting whose patches it drafted."""

    def __init__(self) -> None:
        self.drafted: list[str] = []

    def draft(self, patch, incident, code_rev, files=None, task=None):
        self.drafted.append(incident["id"])
        return super().draft(patch, incident, code_rev, files, task)


class NeverDone(InlineJobs):
    """Drafts are submitted but never finish (the overlay stays held, run after run)."""

    def submit(self, key, fn):
        self.submitted.append(key)
        self._done[key] = Pending()


class Rig:
    """The worker's gate and batch steps with an injected coder, sandbox and job runner."""

    def __init__(self, sessions, settings, clock, *, agent=None, coder=None, jobs=None):
        self.jobs = jobs if jobs is not None else InlineJobs()
        self.docker = fakes.FakeDocker(agent=agent)
        store = build_blobstore(settings.blob_dir)
        sandbox = Sandbox(image="romulus-coder:test", runner=self.docker)
        coder = coder or LocalAgent(
            base_url=settings.llm_base_url, model="test-model", api_key=KEY, sandbox=sandbox
        )
        self.tools = batch.Tools(settings, FakeLlm(), coder, store, self.jobs)
        code = code_lockin.ReplayTools(settings, self.jobs, sandbox, store)
        self.steps = [
            Step("gate.tick", partial(gate.tick, settings=settings, code=code)),
            Step("batch.maybe_run", partial(batch.maybe_run, settings=settings, tools=self.tools)),
        ]
        self.sessions, self.clock = sessions, clock

    def work(self, t):
        self.clock.at(t)
        results = run_tick(self.steps, self.sessions, t, {})
        assert set(results.values()) == {"ok"}, results

    @property
    def agent_runs(self) -> int:
        return len(
            [c for c in self.docker.calls if c["argv"][1:2] == ["run"] and c["argv"][-1] == "agent"]
        )


def _held(arms, tick_arms, now, *names):
    tick_arms(now)
    tick_arms(now + 10 * S)
    for n, name in enumerate(names):
        for i, step in enumerate(SCENARIOS["code"](arms[name], 0)):
            step.run(now + (20 + 5 * n + 10 * i) * S)


def _overlay(db, arms, name) -> Overlay:
    return db.get(Overlay, (name, arms[name].device.overlay.overlay_id))


def _propose(db, arms, name, now, *, row=True, to=None, status="applied", patch=None) -> Overlay:
    """Make `name`'s held overlay a Doctor overlay: `raw.proposal_id`, plus (with `row`) the
    proposal it claims to come from, issued `to` a device with `patch`."""
    ov = _overlay(db, arms, name)
    if row:
        db.add(
            DoctorConsult(
                id="cons_f12b", device_id=to or name, incident_id=ov.incident_id, origin="device"
            )
        )
        db.flush()
        db.add(
            DoctorProposal(
                id=PID,
                device_id=to or name,
                consult_id="cons_f12b",
                incident_id=ov.incident_id,
                base={"code_rev": "toy0001"},
                code_patch=patch if patch is not None else ov.code_patch,
                checks={"passed": True, "stub": False, "summary": "5 passed in 0.1s"},
                status=status,
                overlay_id=ov.id,
                expires_at=now + 3600 * S,
            )
        )
    ov.raw = {**(ov.raw or {}), "proposal_id": PID}
    db.commit()
    return ov


def _status(db, ov) -> str:
    db.expire_all()  # the worker's steps wrote it through their own session
    return db.get(Overlay, (ov.device_id, ov.id)).status


def _code_cands(client) -> dict[str, dict]:
    """Code candidates by the device of their first overlay."""
    codes = [c for c in client.get("/v1/candidates").json() if c["type"] == "code"]
    return {c["from_overlays"][0]["device_id"]: c for c in codes}


def _created(db, cand) -> dict:
    (e,) = events.find(db, {"candidate": cand["id"]}, kinds=["candidate.created"])
    return e.data


def test_f12b_a_verified_doctor_overlay_skips_the_coder_and_still_replays(
    client, arms, tick_arms, now, settings, toy_fleet, sessions, clock, db_session, count_events
):
    rig = Rig(sessions, settings, clock, agent=fakes.fix_like_a_careful_engineer)
    _held(arms, tick_arms, now, "sim-3", "sim-1")
    doc_ov = _propose(db_session, arms, "sim-3", now)
    dev_ov = _overlay(db_session, arms, "sim-1")

    # The batch: sim-1's patch goes to the coding agent; sim-3's proposal is its own draft.
    rig.work(now + 40 * S)
    cands = _code_cands(client)
    doctor, coded = cands["sim-3"], cands["sim-1"]
    assert [o["device_id"] for o in doctor["from_overlays"]] == ["sim-3"]  # folds alone
    assert count_events("coder.started") == 1
    assert count_events("coder.started", {"device": "sim-3"}) == 0
    assert [k for k in rig.jobs.submitted if k.startswith("coder.draft")] == [
        code_lockin.draft_key(dev_ov, "toy0001")
    ]
    assert rig.agent_runs == 1
    assert count_events("doctor.declined") == 0

    # The same kind of code version (so the gate replays it for real), the patch applied,
    # and the proposal named in the candidate's why and data.
    cv = db_session.get(CodeVersion, ("default", doctor["code_rev"]))
    assert cv.source == "coder" and cv.parent_rev == "toy0001"
    tar = tarfile.open(
        fileobj=io.BytesIO(client.get(f"/v1/code/{doctor['code_rev']}/package").content),
        mode="r:gz",
    )
    assert (
        b"healer: disabled the drop-on-proximity rule"
        in tar.extractfile("device/control/gripper.py").read()
    )
    assert "the Fleet Doctor's change on code toy0001" in doctor["why"]
    assert f"proposal {PID}" in doctor["why"] and "no second Coder pass (D24)" in doctor["why"]
    assert doctor["stub_stages"] == coded["stub_stages"] == []
    data = _created(db_session, doctor)
    assert data["source"] == "doctor" and data["proposal_id"] == PID
    assert "source" not in _created(db_session, coded)
    assert "the coding agent's change on code toy0001" in coded["why"]
    assert _status(db_session, doc_ov) == "locked_in"

    # The gate: both replay in fresh containers and wait for B7, with the same events.
    rig.work(now + 41 * S)
    details = {k: client.get(f"/v1/candidates/{c['id']}").json() for k, c in cands.items()}
    for d in details.values():
        assert d["status"] == "awaiting_canary"
        assert d["replay"]["passed"] and not d["replay"]["stub"]
        assert f"code replay passed on code {d['code_rev']}" in d["replay"]["why"]
    assert f"coder.replay:default:{doctor['id']}" in rig.jobs.submitted
    kinds = {k: [e["kind"] for e in d["events"]] for k, d in details.items()}
    assert kinds["sim-3"] == kinds["sim-1"], kinds
    assert rig.agent_runs == 1  # the replays started no agent


def test_f12b_a_doctor_overlay_uses_no_coder_budget_and_keeps_touches_safety(
    client, arms, tick_arms, now, settings, toy_fleet, sessions, clock, db_session, count_events
):
    none_left = settings.model_copy(
        update={"coder_max_drafts_per_hour": 0, "safety_paths": ["control"]}
    )
    rig = Rig(sessions, none_left, clock, agent=fakes.fix_like_a_careful_engineer)
    _held(arms, tick_arms, now, "sim-3")
    ov = _propose(db_session, arms, "sim-3", now)
    rig.work(now + 40 * S)

    doctor = _code_cands(client)["sim-3"]
    assert _status(db_session, ov) == "locked_in"
    assert count_events("coder.started") == 0 and rig.jobs.submitted == []
    assert count_events("overlay.deferred", {"device": "sim-3", "overlay": ov.id}) == 0
    assert doctor["touches_safety"] is True  # from the patch (control/), as for any code


@pytest.mark.parametrize(
    ("case", "kwargs", "said"),
    [
        ("forged", {"row": False}, f"there is no proposal {PID} in this fleet"),
        ("other device", {"to": "sim-1"}, f"proposal {PID} was issued to sim-1, not sim-3"),
        ("patch mismatch", {"patch": VARIANT}, f"is not proposal {PID}'s"),
        ("still offered", {"status": "offered"}, f"proposal {PID} is offered, not applied"),
    ],
)
def test_f12b_an_unverified_proposal_goes_through_the_coder(
    case,
    kwargs,
    said,
    client,
    arms,
    tick_arms,
    now,
    settings,
    toy_fleet,
    sessions,
    clock,
    db_session,
    count_events,
):
    rig = Rig(sessions, settings, clock, agent=fakes.fix_like_a_careful_engineer)
    _held(arms, tick_arms, now, "sim-3")
    ov = _propose(db_session, arms, "sim-3", now, **kwargs)
    rig.work(now + 40 * S)

    code = _code_cands(client)["sim-3"]
    assert count_events("coder.started", {"device": "sim-3", "overlay": ov.id}) == 1
    assert rig.jobs.submitted[0] == code_lockin.draft_key(ov, "toy0001") and rig.agent_runs == 1
    assert "the coding agent's change" in code["why"] and PID not in code["why"]
    data = _created(db_session, code)
    assert "source" not in data and "proposal_id" not in data
    (declined,) = events.find(
        db_session, {"device": "sim-3", "overlay": ov.id}, kinds=["doctor.declined"]
    )
    assert declined.refs["incident"] == ov.incident_id
    assert declined.data["stage"] == "lock_in" and declined.data["proposal_id"] == PID
    assert declined.data["reason"].startswith("lock-in: proposal not verified (")
    assert said in declined.data["reason"], declined.data["reason"]


def test_f12b_an_unverified_proposal_is_reported_once_while_its_draft_runs(
    client, arms, tick_arms, now, settings, toy_fleet, sessions, clock, db_session, count_events
):
    rig = Rig(sessions, settings, clock, jobs=NeverDone())
    _held(arms, tick_arms, now, "sim-3")
    ov = _propose(db_session, arms, "sim-3", now, row=False)
    for t in (40, 200, 400):
        client.post("/v1/batch/run", json={"by": "kent"})
        rig.work(now + t * S)
    assert _status(db_session, ov) == "held"
    assert count_events("doctor.declined", {"device": "sim-3", "overlay": ov.id}) == 1


def test_f12b_passthrough_lane_skips_the_fake_coder_for_a_verified_proposal(
    client, arms, tick_arms, now, make_settings, toy_fleet, sessions, clock, db_session
):
    fake = make_settings()  # CODER_PROVIDER=fake, the default and the overnight setting
    spy = SpyCoder()
    rig = Rig(sessions, fake, clock, coder=spy)
    _held(arms, tick_arms, now, "sim-3", "sim-1")
    doc_ov = _propose(db_session, arms, "sim-3", now)
    dev_ov = _overlay(db_session, arms, "sim-1")
    rig.work(now + 40 * S)
    rig.work(now + 41 * S)

    # One pass-through candidate folds both; only sim-1's patch went to the coder.
    (code,) = {c["id"]: c for c in _code_cands(client).values()}.values()
    assert {o["device_id"] for o in code["from_overlays"]} == {"sim-1", "sim-3"}
    assert spy.drafted == [dev_ov.incident_id]
    assert doc_ov.incident_id not in spy.drafted
    assert f"proposal {PID}" in code["why"] and "no second Coder pass (D24)" in code["why"]
    assert _created(db_session, code)["proposal_ids"] == [PID]
    detail = client.get(f"/v1/candidates/{code['id']}").json()
    assert detail["stub_stages"] == ["draft_code", "replay"]
    assert detail["status"] == "awaiting_canary" and detail["replay"]["stub"]
    assert rig.jobs.submitted == [] and rig.docker.calls == []
