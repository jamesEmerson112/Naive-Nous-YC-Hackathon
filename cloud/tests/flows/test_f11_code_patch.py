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
from romulus_cloud.adapters.coder import CoderError, LocalAgent, Sandbox
from romulus_cloud.adapters.llm import FakeLlm
from romulus_cloud.models import CodeVersion, Device, Overlay
from romulus_cloud.services import batch, code_lockin, gate, registry
from romulus_cloud.services.jobs import InlineJobs, Missing, Pending, ThreadPoolJobs
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
    ):
        self.jobs = jobs if jobs is not None else InlineJobs()
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
    # base, then base + code hunks (decisive), then the draft's own new test (advisory)
    assert modes == ["agent", "verify", "test", "verify", "test", "test"]
    runs = [c["argv"] for c in rig.docker.calls if c["argv"][1] == "run"]
    nets = [a[a.index("--network") + 1] for a in runs]
    assert nets[0].startswith("romulus-coder-net-") and nets[1:] == ["none"] * 5  # B5

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
        assert ov.status == "held", ov.status_reason
        story = client.get(f"/v1/incidents/{ov.incident_id}/story").json()
        (started_step,) = [s for s in story["steps"] if s["kind"] == "coder.started"]
        assert "stays held until the draft is done" in started_step["why"]

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


class BlockedReplay(fakes.FakeDocker):
    """The replay's first container hangs until the test releases it."""

    def __init__(self, **kw):
        super().__init__(**kw)
        self.started, self.release = threading.Event(), threading.Event()

    def __call__(self, argv, **kw):
        if argv[1] == "run" and argv[-1] == "verify":
            self.started.set()
            assert self.release.wait(30), "never released"
        return super().__call__(argv, **kw)


def test_f11_freeze_during_a_replay_holds_the_candidate_until_unfreeze(
    client, arms, tick_arms, now, settings, toy_fleet, sessions, clock
):
    jobs = ThreadPoolJobs(max_workers=1)
    docker = BlockedReplay(agent=fakes.fix_like_a_careful_engineer)
    rig = Rig(sessions, settings, clock, jobs=jobs, runner=docker)
    try:
        _start(tick_arms, now)
        _held(arms, "code", "sim-3", now + 20 * S)
        rig.work(now + 40 * S)  # draft submitted (the agent itself is quick)
        _wait(jobs, code_lockin.draft_key(_overlay_row(client, arms), "toy0001"))
        rig.work(now + 41 * S)  # candidate built
        code = _cands(client)["code"]
        rig.work(now + 42 * S)  # replay submitted; its first container hangs
        assert docker.started.wait(10)

        started = time.monotonic()
        assert (
            client.post("/v1/fleet/freeze", json={"by": "kent", "why": "test"}).status_code == 200
        )
        rig.work(now + 43 * S)
        assert time.monotonic() - started < 5
        assert client.get(f"/v1/candidates/{code['id']}").json()["status"] == "frozen"

        # The replay finishes while frozen: nothing moves, however many ticks.
        docker.release.set()
        _wait(jobs, f"coder.replay:default:{code['id']}")
        for t in (44, 45, 46):
            rig.work(now + t * S)
        frozen = client.get(f"/v1/candidates/{code['id']}").json()
        assert frozen["status"] == "frozen" and frozen["replay"] is None

        # Unfreeze: it restarts from replay, uses the finished result, and waits for B7.
        assert client.post("/v1/fleet/unfreeze", json={"reason": "done"}).status_code == 200
        rig.work(now + 47 * S)
        rig.work(now + 48 * S)
        detail = client.get(f"/v1/candidates/{code['id']}").json()
        assert detail["status"] == "awaiting_canary" and detail["replay"]["passed"]
        assert isinstance(jobs.poll(f"coder.replay:default:{code['id']}"), Missing)  # forgotten
    finally:
        docker.release.set()
        jobs.shutdown()


def _overlay_row(client, arms, name="sim-3"):
    """A stand-in with what `draft_key` reads (the overlay row's fleet and patch)."""

    class Row:
        fleet_id = "default"
        code_patch = arms[name].device.overlay.code_patch

    return Row()


def add_a_verify_script(work: Path) -> None:
    """An agent that fixes the bug but also writes into the held-out verify/ dir."""
    fakes.fix_like_a_careful_engineer(work)
    (work / "verify").mkdir(exist_ok=True)
    (work / "verify" / "test_mine.py").write_text("def test_mine():\n    assert True\n")


def test_f11_a_coder_diff_touching_verify_is_refused_before_anything_is_built(
    client, arms, tick_arms, now, settings, toy_fleet, sessions, clock, db_session
):
    rig = Rig(sessions, settings, clock, agent=add_a_verify_script)
    _start(tick_arms, now)
    _held(arms, "code", "sim-3", now + 20 * S)
    rig.work(now + 40 * S)
    assert client.get("/v1/candidates").json() == []
    ov = _overlay(db_session, arms)
    assert ov.status == "lockin_error"
    assert "diff is refused" in ov.status_reason and "held-out verify/" in ov.status_reason
    coded = db_session.scalars(select(CodeVersion).where(CodeVersion.source == "coder")).all()
    assert coded == []
    assert [f["version"] for f in client.get("/v1/firmware").json()] == [1, 2]


def test_f11_touches_safety_is_computed_on_the_coders_diff(
    client, arms, tick_arms, now, settings, toy_fleet, sessions, clock
):
    # The device's patch touches only control/; the coder's diff also adds tests/.
    guarded = settings.model_copy(update={"safety_paths": ["tests"]})
    rig = Rig(sessions, guarded, clock, agent=fakes.fix_like_a_careful_engineer)
    _start(tick_arms, now)
    _held(arms, "code", "sim-3", now + 20 * S)
    rig.work(now + 40 * S)
    code = _cands(client)["code"]
    assert code["touches_safety"] is True


VARIANT = fakes.TOY_PATCH.replace("the spec says keep holding", "the spec says keep the object")


def test_f11_identical_patches_share_one_draft_and_one_candidate(
    client, arms, tick_arms, now, settings, toy_fleet, sessions, clock, db_session, count_events
):
    rig = Rig(sessions, settings, clock, agent=fakes.fix_like_a_careful_engineer)
    _start(tick_arms, now)
    _held(arms, "code", "sim-3", now + 20 * S)
    _held(arms, "code", "sim-1", now + 25 * S)
    rig.work(now + 40 * S)
    code = _cands(client)["code"]
    assert {o["device_id"] for o in code["from_overlays"]} == {"sim-1", "sim-3"}
    assert count_events("coder.started") == 1
    assert len([k for k in rig.jobs.submitted if k.startswith("coder.draft")]) == 1
    assert {_overlay(db_session, arms, n).status for n in ("sim-1", "sim-3")} == {"locked_in"}


def test_f11_over_the_draft_budget_an_overlay_waits_held(
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
    monkeypatch,
):
    one = settings.model_copy(update={"coder_max_drafts_per_hour": 1})
    rig = Rig(sessions, one, clock, agent=fakes.fix_like_a_careful_engineer)
    _start(tick_arms, now)
    _held(arms, "code", "sim-3", now + 20 * S)
    monkeypatch.setattr("fake_device.scenarios.D1_CODE_PATCH", VARIANT)
    _held(arms, "code", "sim-1", now + 25 * S)
    rig.work(now + 40 * S)

    waiting = _overlay(db_session, arms, "sim-1")
    assert waiting.status == "held"  # not sticky
    assert count_events("coder.started") == 1
    reasons = events.find(db_session, {"overlay": waiting.id}, kinds=["overlay.deferred"])
    assert len(reasons) == 1 and "coder budget" in reasons[0].why
    for t in (200, 400):  # still over budget: no new draft, no new event
        client.post("/v1/batch/run", json={"by": "kent"})
        rig.work(now + t * S)
    assert count_events("coder.started") == 1
    assert len(events.find(db_session, {"overlay": waiting.id}, kinds=["overlay.deferred"])) == 1

    # An hour later the budget frees up and it is drafted.
    client.post("/v1/batch/run", json={"by": "kent"})
    rig.work(now + 3700 * S)
    assert count_events("coder.started") == 2
    db_session.expire_all()
    assert _overlay(db_session, arms, "sim-1").status == "locked_in"


def test_f11_a_worker_that_cant_run_the_coder_leaves_code_overlays_held(
    client, arms, tick_arms, now, settings, toy_fleet, sessions, clock, db_session, count_events
):
    rig = Rig(sessions, settings, clock, agent=fakes.fix_like_a_careful_engineer)
    rig.tools.coder_blocked = code_lockin.coder_blocker(settings, docker="no-such-docker")
    assert "make worker-host" in rig.tools.coder_blocked
    _start(tick_arms, now)
    _held(arms, "code", "sim-3", now + 20 * S)
    for t in (40, 200):
        client.post("/v1/batch/run", json={"by": "kent"})
        rig.work(now + t * S)
    assert _overlay(db_session, arms).status == "held"
    assert "code" not in _cands(client)
    assert count_events("coder.started") == 0 and count_events("batch.error") == 0
    assert rig.jobs.submitted == [] and rig.docker.calls == []


def test_f11_with_no_test_command_the_verify_scripts_still_replay(
    client, arms, tick_arms, now, settings, toy_fleet, sessions, clock
):
    no_cmd = settings.model_copy(update={"coder_test_cmd": ""})
    rig = Rig(sessions, no_cmd, clock, agent=fakes.fix_like_a_careful_engineer)
    _start(tick_arms, now)
    _held(arms, "code", "sim-3", now + 20 * S)
    rig.work(now + 40 * S)
    code = _cands(client)["code"]
    rig.work(now + 41 * S)
    detail = client.get(f"/v1/candidates/{code['id']}").json()
    assert detail["status"] == "awaiting_canary" and not detail["replay"]["stub"]
    assert "tests none (CODER_TEST_CMD is empty)" in detail["replay"]["why"]
    assert "verify 2/2 passed" in detail["replay"]["why"]
    replay_modes = [c["argv"][-1] for c in rig.docker.calls if c["argv"][1] == "run"][1:]
    assert replay_modes == ["verify", "verify"]


# --- a provider blip is retried, bounded (domain/coder_retry, PR #38) ----------------------

AT_CAPACITY = {
    "agent": "mini-swe-agent",
    "version": "2.4.6",
    "model": "test-model",
    "exit_status": "RateLimitError",
    "error": "litellm.RateLimitError: OpenAIException - the service is at capacity",
    "steps": 1,
    "prompt_tokens": 0,
    "completion_tokens": 0,
    "seconds": 3.0,
}


class Flaky:
    """The agent: the provider answers 429 (0 tokens) `blips` times, then the agent fixes it.
    FakeDocker reads `agent_result` after calling the agent, so each call sets it."""

    def __init__(self, docker: fakes.FakeDocker, blips: int) -> None:
        self.docker, self.blips, self.ok = docker, blips, dict(docker.agent_result)

    def __call__(self, work: Path) -> None:
        if self.blips > 0:
            self.blips -= 1
            self.docker.agent_result = AT_CAPACITY
            return
        self.docker.agent_result = self.ok
        fakes.fix_like_a_careful_engineer(work)


def _flaky_rig(sessions, settings, clock, blips):
    docker = fakes.FakeDocker()
    docker.agent = Flaky(docker, blips)
    return Rig(sessions, settings, clock, runner=docker)


def _deferrals(db_session, ov):
    found = events.find(db_session, {"overlay": ov.id}, kinds=["overlay.deferred"])
    return [e for e in found if e.data.get("reason") == batch.CODER_UNAVAILABLE]


def test_f11_a_429_defers_then_the_retry_after_backoff_succeeds(
    client, arms, tick_arms, now, settings, toy_fleet, sessions, clock, db_session, count_events
):
    rig = _flaky_rig(sessions, settings, clock, blips=1)
    _start(tick_arms, now)
    _held(arms, "code", "sim-3", now + 20 * S)

    rig.work(now + 40 * S)  # attempt 1: the proxy is at capacity
    ov = _overlay(db_session, arms)
    assert ov.status == "held" and "code" not in _cands(client)
    (deferred,) = _deferrals(db_session, ov)
    assert "coder unavailable (rate limited), retry after 22:02 UTC (attempt 1 of 5)" in (
        deferred.why
    )
    assert deferred.data["no_tokens"] is True
    assert batch._drafts_this_hour(db_session, "default", now + 41 * S) == 0  # cost nothing

    client.post("/v1/batch/run", json={"by": "kent"})
    rig.work(now + 100 * S)  # before retry_at: no new draft
    assert count_events("coder.started") == 1 and len(_deferrals(db_session, ov)) == 1

    client.post("/v1/batch/run", json={"by": "kent"})
    rig.work(now + 170 * S)  # after the 2 min backoff: redrafted, and this time it works
    code = _cands(client)["code"]
    assert count_events("coder.started") == 2
    rig.work(now + 171 * S)
    assert client.get(f"/v1/candidates/{code['id']}").json()["status"] == "awaiting_canary"
    db_session.expire_all()
    assert _overlay(db_session, arms).status == "locked_in"


def test_f11_five_429s_in_a_row_become_sticky_with_every_reason(
    client, arms, tick_arms, now, settings, toy_fleet, sessions, clock, db_session, count_events
):
    rig = _flaky_rig(sessions, settings, clock, blips=99)
    _start(tick_arms, now)
    _held(arms, "code", "sim-3", now + 20 * S)
    # Backoff 2, 4, 8, 16 min: each run lands just after the previous retry_at.
    for t in (40, 170, 420, 910, 1880):
        client.post("/v1/batch/run", json={"by": "kent"})
        rig.work(now + t * S)

    ov = _overlay(db_session, arms)
    assert ov.status == "lockin_error"
    assert "unavailable 5 times in a row" in ov.status_reason
    for i in range(1, 6):
        assert f"attempt {i}: the coding agent's draft did not pass" in ov.status_reason
    assert ov.status_reason.count("exit RateLimitError") == 5
    assert [e.data["attempt"] for e in _deferrals(db_session, ov)] == [1, 2, 3, 4]
    assert count_events("coder.started") == 5
    # Zero-token retries never used the hourly budget.
    assert batch._drafts_this_hour(db_session, "default", now + 1881 * S) == 0
    client.post("/v1/batch/run", json={"by": "kent"})
    rig.work(now + 4000 * S)  # sticky: never drafted again
    assert count_events("coder.started") == 5


def test_f11_a_shared_draft_is_refunded_once_and_its_backoff_is_shared(
    client, arms, tick_arms, now, settings, toy_fleet, sessions, clock, db_session, count_events
):
    """Reviewer finding 1a: sim-1 and sim-3 send the same patch, so they share one job. Its
    0-token 429 gives back one budget slot, not two, and both wait on the job's backoff."""
    rig = _flaky_rig(sessions, settings, clock, blips=1)
    _start(tick_arms, now)
    _held(arms, "code", "sim-3", now + 20 * S)
    _held(arms, "code", "sim-1", now + 21 * S)
    rig.work(now + 40 * S)
    assert count_events("coder.started") == 1 and len(rig.jobs.submitted) == 1  # one job
    deferred = _deferrals(db_session, _overlay(db_session, arms, "sim-3"))
    deferred += _deferrals(db_session, _overlay(db_session, arms, "sim-1"))
    assert [e.data["attempt"] for e in deferred] == [1, 1]  # two overlays, one job attempt
    assert len({e.data["job"] for e in deferred}) == 1
    assert batch._drafts_this_hour(db_session, "default", now + 41 * S) == 0
    # The reviewer's case: one other real draft started this hour still counts.
    events.record(
        db_session, "batch", "agent", "coder.started", {"device": "sim-2"}, "another draft",
        data={"job": "other"}, ts=now + 41 * S,
    )  # fmt: skip
    assert batch._drafts_this_hour(db_session, "default", now + 42 * S) == 1


def test_f11_a_later_overlay_with_the_same_patch_inherits_the_backoff(
    client, arms, tick_arms, now, settings, toy_fleet, sessions, clock, db_session, count_events
):
    rig = _flaky_rig(sessions, settings, clock, blips=1)
    _start(tick_arms, now)
    _held(arms, "code", "sim-3", now + 20 * S)
    rig.work(now + 40 * S)  # the job blips: retry after now + 160 s
    _held(arms, "code", "sim-1", now + 60 * S)  # same patch, arrives during the backoff
    client.post("/v1/batch/run", json={"by": "kent"})
    rig.work(now + 100 * S)
    assert count_events("coder.started") == 1  # not resubmitted early
    assert _overlay(db_session, arms, "sim-1").status == "held"
    client.post("/v1/batch/run", json={"by": "kent"})
    rig.work(now + 170 * S)  # after the backoff: one retry serves both
    assert "code" in _cands(client)


class _ConnectionDrops:
    """A coder whose job dies with a connection error: no tokens count is known."""

    def draft(self, patch, incident, code_rev, files=None, task=None):
        raise CoderError("litellm.APIConnectionError: OpenAIException - Connection error.")


def test_f11_an_unknown_token_count_is_retried_but_not_refunded(
    client, arms, tick_arms, now, settings, toy_fleet, sessions, clock, db_session
):
    """Reviewer finding 1b: a failure with tokens unknown (None) may have spent tokens."""
    rig = Rig(sessions, settings, clock, coder=_ConnectionDrops())
    _start(tick_arms, now)
    _held(arms, "code", "sim-3", now + 20 * S)
    rig.work(now + 40 * S)
    ov = _overlay(db_session, arms)
    (deferred,) = _deferrals(db_session, ov)
    assert ov.status == "held" and deferred.data["label"] == "connection or timeout"
    assert deferred.data["no_tokens"] is False
    assert batch._drafts_this_hour(db_session, "default", now + 41 * S) == 1
