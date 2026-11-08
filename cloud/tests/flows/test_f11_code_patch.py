"""F11: code patch → coding agent → replay → candidate (flows doc F11, D21, M8b).

The fleet runs the toy device (`tests/fixtures/toy_device`, fault D1 planted) as code
`toy0001`. sim-3 reports D1 with the toy's `code_patch` and later `held`. The coder is a
real `LocalAgent` whose `docker run` is `FakeDocker` (`tests/fixtures/coder_fakes.py`): the
test plays the agent by editing the scratch copy, and the toy suite and the held-out verify
scripts really run on the host. No Docker, no LLM, no network.

Drafts and replays go through `services/jobs.py`: `InlineJobs` finishes them at submit;
`ThreadPoolJobs` with a blocked runner shows the gate ticking while a coder run is in progress.
"""

import io
import sys
import tarfile
import threading
import time
from datetime import timedelta
from functools import partial
from pathlib import Path

import pytest
from pydantic import SecretStr
from sqlalchemy import select

from fake_device.scenarios import SCENARIOS
from romulus_cloud import events
from romulus_cloud.adapters.blobstore import build_blobstore
from romulus_cloud.adapters.coder import LocalAgent, Sandbox
from romulus_cloud.adapters.llm import FakeLlm
from romulus_cloud.models import CodeVersion, Device, Overlay
from romulus_cloud.services import batch, code_lockin, gate, registry
from romulus_cloud.services.jobs import InlineJobs, Pending, ThreadPoolJobs
from romulus_cloud.services.seed import FIRMWARE_V1
from romulus_cloud.worker import Step, run_tick

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "fixtures"))
import coder_fakes as fakes  # noqa: E402

S = timedelta(seconds=1)
KW = {"actor": "human:test", "actor_class": "human"}
KEY = "sk-test-F11-SECRET"
B7 = "no canary device runs real device code (B7)"
VERIFY_TEST = "verify/test_verify_d1.py::test_hand_near_while_holding_keeps_the_gripper_closed"


def add_a_test_only(work: Path) -> None:
    """A weak agent: adds a passing test and fixes nothing (its own tests pass)."""
    (work / "tests" / "test_more.py").write_text("def test_ok():\n    assert True\n")


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
    """Code toy0001 and firmware v2 naming it, assigned to every device; sim-3's D1 patch is
    the toy's."""
    monkeypatch.setattr("fake_device.scenarios.D1_CODE_PATCH", fakes.TOY_PATCH)
    store = build_blobstore(settings.blob_dir)
    package = registry.build_tarball(fakes.toy_files())
    registry.create_code(seeded, store, package, rev="toy0001", **KW)
    registry.create_firmware(seeded, FIRMWARE_V1 | {"version": 2, "code_rev": "toy0001"}, **KW)
    for d in seeded.scalars(select(Device)):
        d.assigned_firmware = 2
    seeded.commit()
    return seeded


class Rig:
    """The worker's gate and batch steps with an injected coder, sandbox and job runner."""

    def __init__(
        self, sessions, settings, clock, *, agent=None, jobs=None, coder=None, runner=None
    ):  # noqa: E501
        self.jobs = jobs or InlineJobs()
        self.docker = runner or fakes.FakeDocker(agent=agent)
        store = build_blobstore(settings.blob_dir)
        sandbox = Sandbox(image="romulus-coder:test", runner=self.docker)
        coder = coder or LocalAgent(
            base_url=settings.llm_base_url, model="test-model", api_key=KEY, sandbox=sandbox
        )
        self.tools = batch.Tools(settings, FakeLlm(), coder, store, self.jobs)
        self.code = code_lockin.ReplayTools(settings, self.jobs, sandbox, store)
        self.steps = [
            Step("gate.tick", partial(gate.tick, settings=settings, code=self.code)),
            Step("batch.maybe_run", partial(batch.maybe_run, settings=settings, tools=self.tools)),
        ]
        self.sessions, self.clock = sessions, clock

    def work(self, t):
        self.clock.at(t)
        results = run_tick(self.steps, self.sessions, t, {})
        assert set(results.values()) == {"ok"}, results


def _start(tick_arms, now):
    tick_arms(now)
    tick_arms(now + 10 * S)


def _held(arms, scenario, name, at):
    for i, step in enumerate(SCENARIOS[scenario](arms[name], 0)):
        step.run(at + i * 10 * S)


def _cands(client):
    return {c["type"]: c for c in client.get("/v1/candidates").json()}


def _overlay(db, arms, name="sim-3"):
    return db.get(Overlay, (name, arms[name].device.overlay.overlay_id))


def test_f11_a_d1_code_patch_becomes_a_replayed_code_candidate_that_waits_for_b7(
    client, arms, tick_arms, now, settings, toy_fleet, sessions, clock, db_session, count_events
):
    rig = Rig(sessions, settings, clock, agent=fakes.fix_like_a_careful_engineer)
    _start(tick_arms, now)
    _held(arms, "code", "sim-3", now + 20 * S)

    # 1-3. The batch drafts with the coding agent; its diff becomes a new code version
    # (source coder, child of toy0001) and a firmware manifest naming it.
    rig.work(now + 40 * S)
    code = _cands(client)["code"]
    assert code["code_rev"] not in (None, "toy0001") and code["stub_stages"] == []
    cv = db_session.get(CodeVersion, ("default", code["code_rev"]))
    assert cv.source == "coder" and cv.parent_rev == "toy0001"
    assert "the coding agent's change on code toy0001" in code["why"]
    assert "7 steps (limit 30)" in code["why"] and "advisory" in code["why"]
    assert "Its own test run (advisory): 5 passed" in code["why"]
    assert KEY not in code["why"] and not code["touches_safety"]
    pkg = client.get(f"/v1/code/{code['code_rev']}/package")
    tar = tarfile.open(fileobj=io.BytesIO(pkg.content), mode="r:gz")
    assert b"< PROXIMITY_MM" not in tar.extractfile("device/control/gripper.py").read()
    assert "device/tests/test_regression_d1.py" in tar.getnames()
    assert "device/verify/test_verify_d1.py" in tar.getnames()  # held out of the agent only
    assert rig.docker.saw_verify_in_work is False
    fw = client.get(f"/v1/firmware/{code['version']}").json()["manifest"]
    assert fw["code_rev"] == code["code_rev"] and fw["rules"] == FIRMWARE_V1["rules"]

    # 4. The gate replays it (device tests + held-out verify, fresh containers), then stops
    # at awaiting_canary: no canary runs real code (B7).
    rig.work(now + 41 * S)
    detail = client.get(f"/v1/candidates/{code['id']}").json()
    assert detail["status"] == "awaiting_canary"
    assert detail["replay"]["passed"] and not detail["replay"]["stub"]
    assert f"code replay passed on code {code['code_rev']}" in detail["replay"]["why"]
    assert "base: tests pass, verify 1/2 passed" in detail["replay"]["why"]  # fault caught
    assert "patched: tests pass, verify 2/2 passed" in detail["replay"]["why"]
    (replayed,) = events.find(db_session, {"candidate": code["id"]}, kinds=["candidate.replayed"])
    assert "2 passed" in replayed.data["replay_output"]["patched_verify"]
    assert KEY not in str(replayed.data)
    (waiting,) = [e for e in detail["events"] if e["kind"] == "candidate.awaiting_canary"]
    assert B7 in waiting["why"] and "pass-through" not in waiting["why"]
    modes = [c["argv"][-1] for c in rig.docker.calls if c["argv"][1] == "run"]
    assert modes == ["agent", "verify", "test", "verify", "test"]
    assert all("--network" in c["argv"] for c in rig.docker.calls if c["argv"][-1] != "agent")

    # It never reaches a device; the overlay is locked in.
    for t in (50, 90, 130, 165, 400):
        tick_arms(now + t * S)
        rig.work(now + t * S + S)
    assert client.get(f"/v1/candidates/{code['id']}").json()["status"] == "awaiting_canary"
    assert all(d["assigned"]["firmware"] == 2 for d in client.get("/v1/devices").json())
    assert count_events("candidate.awaiting_canary", {"candidate": code["id"]}) == 1
    assert _overlay(db_session, arms).status == "locked_in"
    assert rig.jobs.submitted == [
        code_lockin.draft_key(_overlay(db_session, arms), "toy0001"),
        f"coder.replay:default:{code['id']}",
    ]


def test_f11_a_draft_whose_replay_fails_is_rolled_back_with_the_tests_named(
    client, arms, tick_arms, now, settings, toy_fleet, sessions, clock, db_session, count_events
):
    rig = Rig(sessions, settings, clock, agent=add_a_test_only)
    _start(tick_arms, now)
    _held(arms, "code", "sim-3", now + 20 * S)
    rig.work(now + 40 * S)  # the draft passes its own tests (advisory) → a candidate
    code = _cands(client)["code"]
    rig.work(now + 41 * S)  # the replay's held-out verify scripts catch it

    detail = client.get(f"/v1/candidates/{code['id']}").json()
    assert detail["status"] == "rolled_back"
    assert "code replay failed" in detail["verdict"]["why"]
    assert VERIFY_TEST in detail["verdict"]["why"]
    assert any(c["case_id"] == VERIFY_TEST for c in detail["replay"]["cases"])
    ov = _overlay(db_session, arms)
    assert ov.status == "lockin_rolled_back" and VERIFY_TEST in ov.status_reason

    # Sticky: later runs never draft it again.
    for t in (200, 400):
        client.post("/v1/batch/run", json={"by": "kent"})
        rig.work(now + t * S)
    assert len([k for k in rig.jobs.submitted if k.startswith("coder.draft")]) == 1
    assert count_events("overlay.lockin_rolled_back", {"overlay": ov.id}) == 1
    assert arms["sim-3"].device.overlay is not None  # the device keeps its local fix


def test_f11_a_coder_error_builds_nothing_and_is_not_retried(
    client, arms, tick_arms, now, settings, toy_fleet, sessions, clock, db_session, count_events
):
    broken = batch._build_coder(settings.model_copy(update={"llm_base_url": ""}))
    rig = Rig(sessions, settings, clock, coder=broken)
    _start(tick_arms, now)
    _held(arms, "code", "sim-3", now + 20 * S)
    rig.work(now + 40 * S)

    assert client.get("/v1/candidates").json() == []
    ov = _overlay(db_session, arms)
    assert ov.status == "lockin_error"
    assert "the coding agent could not run" in ov.status_reason
    assert "needs LLM_BASE_URL and LLM_MODEL" in ov.status_reason
    for t in (200, 400):
        client.post("/v1/batch/run", json={"by": "kent"})
        rig.work(now + t * S)
    assert len(rig.jobs.submitted) == 1
    assert count_events("batch.error", {"overlay": ov.id}) == 1
    assert rig.docker.calls == []


def test_f11_a_failing_draft_builds_nothing(
    client, arms, tick_arms, now, settings, toy_fleet, sessions, clock, db_session
):
    rig = Rig(sessions, settings, clock, agent=None)  # the agent changes nothing
    _start(tick_arms, now)
    _held(arms, "code", "sim-3", now + 20 * S)
    rig.work(now + 40 * S)
    assert client.get("/v1/candidates").json() == []
    ov = _overlay(db_session, arms)
    assert ov.status == "lockin_error"
    assert "draft did not pass" in ov.status_reason
    assert "the agent changed nothing" in ov.status_reason


def test_f11_fake_coder_keeps_the_labeled_passthrough(
    client, arms, tick_arms, now, make_settings, toy_fleet, sessions, clock
):
    fake = make_settings()  # CODER_PROVIDER=fake, the default and the overnight setting
    rig = Rig(sessions, fake, clock, coder=batch._build_coder(fake))
    _start(tick_arms, now)
    _held(arms, "code", "sim-3", now + 20 * S)
    rig.work(now + 40 * S)
    rig.work(now + 41 * S)
    code = client.get(f"/v1/candidates/{_cands(client)['code']['id']}").json()
    assert code["stub_stages"] == ["draft_code", "replay"]
    assert "pass-through: coding agent not wired" in code["why"]
    assert code["status"] == "awaiting_canary" and code["replay"]["stub"]
    (waiting,) = [e for e in code["events"] if e["kind"] == "candidate.awaiting_canary"]
    assert "pass-through code candidate" in waiting["why"]
    assert rig.jobs.submitted == [] and rig.docker.calls == []


class BlockedDocker(fakes.FakeDocker):
    """The agent's container hangs until the test releases it."""

    def __init__(self, **kw):
        super().__init__(**kw)
        self.started, self.release = threading.Event(), threading.Event()

    def __call__(self, argv, **kw):
        if argv[1] == "run" and argv[-1] == "agent":
            self.started.set()
            assert self.release.wait(30), "never released"
        return super().__call__(argv, **kw)


def _wait(jobs, key, timeout=30):
    deadline = time.monotonic() + timeout
    while isinstance(jobs.poll(key), Pending):
        assert time.monotonic() < deadline, f"job {key} still pending"
        time.sleep(0.05)


def test_f11_the_gate_keeps_ticking_while_the_coding_agent_runs(
    client, arms, tick_arms, now, settings, toy_fleet, sessions, clock, db_session
):
    jobs = ThreadPoolJobs(max_workers=1)
    docker = BlockedDocker(agent=fakes.fix_like_a_careful_engineer)
    rig = Rig(sessions, settings, clock, jobs=jobs, runner=docker)
    try:
        _start(tick_arms, now)
        _held(arms, "code", "sim-3", now + 20 * S)
        _held(arms, "rules", "sim-1", now + 20 * S)

        # The batch submits the draft and moves on: the rules fix is locked in, the code
        # overlay stays held with a why.
        started = time.monotonic()
        rig.work(now + 40 * S)
        assert docker.started.wait(10)
        assert set(_cands(client)) == {"firmware", "harness"}
        ov = _overlay(db_session, arms)
        assert ov.status == "held"
        story = client.get(f"/v1/incidents/{ov.incident_id}/story").json()
        (deferred,) = [s for s in story["steps"] if s["kind"] == "overlay.deferred"]
        assert "while the coding agent drafts" in deferred["why"]

        # While the agent is stuck, the gate keeps moving the rules candidate to its canary.
        rig.work(now + 41 * S)
        rig.work(now + 42 * S)
        assert time.monotonic() - started < 10
        assert _cands(client)["firmware"]["status"] == "canary"
        assert "code" not in _cands(client) and ov.status == "held"

        # Released: the next tick's batch picks up the finished draft (no timer wait), then
        # the gate's replay runs as a job too.
        docker.release.set()
        _wait(jobs, code_lockin.draft_key(ov, "toy0001"))
        rig.work(now + 43 * S)
        code = _cands(client)["code"]
        assert code["status"] == "replaying"
        rig.work(now + 44 * S)  # submits the replay; the tick doesn't wait for it
        _wait(jobs, f"coder.replay:default:{code['id']}")
        rig.work(now + 45 * S)
        assert client.get(f"/v1/candidates/{code['id']}").json()["status"] == "awaiting_canary"
    finally:
        docker.release.set()
        jobs.shutdown()
