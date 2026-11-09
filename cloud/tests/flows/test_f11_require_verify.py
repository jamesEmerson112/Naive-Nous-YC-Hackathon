"""F11: the gate requires a held-out verify before trusting a coder's code candidate.

The device tests run in the same process as the code under test, which can forge their
result seal; only the held-out verify (a subprocess of the frozen harness, PR #53) is
trusted. So a coder candidate whose base has no `device/verify/` and no CODER_VERIFY_DIR
waits in `replaying` with one `candidate.awaiting_verify` event (no failure, no sticky
status, no retry loop) and replays once verify exists. CODER_ALLOW_UNVERIFIED=true lets the
device tests alone pass it, with a `candidate.unverified` warning. Set-up as in F11.
"""

import sys
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
from romulus_cloud.models import Device, Overlay
from romulus_cloud.services import batch, code_lockin, gate, registry
from romulus_cloud.services.jobs import InlineJobs
from romulus_cloud.services.seed import FIRMWARE_V1
from romulus_cloud.worker import Step, run_tick

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "fixtures"))
import coder_fakes as fakes  # noqa: E402

S = timedelta(seconds=1)
KW = {"actor": "human:test", "actor_class": "human"}
KEY = "sk-test-F11v-SECRET"
NO_VERIFY = "no held-out verify for this code version"


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


def bare_files() -> dict[str, bytes]:
    """The toy device without its `verify/` (like `device/` before `make device-verify`)."""
    return {k: v for k, v in fakes.toy_files().items() if "/verify/" not in k}


@pytest.fixture
def bare_fleet(seeded, settings, monkeypatch):
    monkeypatch.setattr("fake_device.scenarios.D1_CODE_PATCH", fakes.TOY_PATCH)
    store = build_blobstore(settings.blob_dir)
    registry.create_code(seeded, store, registry.build_tarball(bare_files()), rev="bare001", **KW)
    registry.create_firmware(seeded, FIRMWARE_V1 | {"version": 2, "code_rev": "bare001"}, **KW)
    for d in seeded.scalars(select(Device)):
        d.assigned_firmware = 2
    seeded.commit()
    return seeded


def verify_dir(tmp_path: Path) -> Path:
    """A CODER_VERIFY_DIR holding the toy's held-out verify scripts."""
    out = tmp_path / "verify"
    for name, data in fakes.toy_files().items():
        if name.startswith("device/verify/"):
            path = out / name.removeprefix("device/verify/")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
    return out


class Rig:
    def __init__(self, sessions, settings, clock):
        self.jobs = InlineJobs()
        store = build_blobstore(settings.blob_dir)
        sandbox = Sandbox(
            image="romulus-coder:test",
            runner=fakes.FakeDocker(agent=fakes.fix_like_a_careful_engineer),
        )
        coder = LocalAgent(
            base_url=settings.llm_base_url, model="test-model", api_key=KEY, sandbox=sandbox
        )
        self.code = code_lockin.ReplayTools(settings, self.jobs, sandbox, store)
        self.steps = [
            Step("gate.tick", partial(gate.tick, settings=settings, code=self.code)),
            Step(
                "batch.maybe_run",
                partial(
                    batch.maybe_run,
                    settings=settings,
                    tools=batch.Tools(settings, FakeLlm(), coder, store, self.jobs),
                ),
            ),
        ]
        self.sessions, self.clock = sessions, clock

    def work(self, t):
        self.clock.at(t)
        results = run_tick(self.steps, self.sessions, t, {})
        assert set(results.values()) == {"ok"}, results


def _held(arms, tick_arms, now):
    tick_arms(now)
    tick_arms(now + 10 * S)
    for i, step in enumerate(SCENARIOS["code"](arms["sim-3"], 0)):
        step.run(now + (20 + i * 10) * S)


def _code(client):
    (c,) = [c for c in client.get("/v1/candidates").json() if c["type"] == "code"]
    return client.get(f"/v1/candidates/{c['id']}").json()


def _kinds(cand):
    return [e["kind"] for e in cand["events"]]


def test_f11_a_coder_candidate_with_no_held_out_verify_waits_then_replays_once_it_exists(
    client, arms, tick_arms, now, settings, bare_fleet, sessions, clock, db_session, tmp_path
):
    rig = Rig(sessions, settings, clock)
    _held(arms, tick_arms, now)
    rig.work(now + 40 * S)  # the batch: the coder's candidate
    for t in range(41, 200, 7):  # many gate ticks, across a batch interval
        tick_arms(now + t * S)
        rig.work(now + t * S)
    cand = _code(client)
    assert cand["status"] == "replaying" and cand["replay"] is None, [
        (e["kind"], e["why"]) for e in cand["events"]
    ]
    assert _kinds(cand).count("candidate.awaiting_verify") == 1  # once, not once per tick
    (wait,) = [e for e in cand["events"] if e["kind"] == "candidate.awaiting_verify"]
    assert NO_VERIFY in wait["why"] and "make device-verify" in wait["why"]
    assert "candidate.replayed" not in _kinds(cand)
    assert not [k for k in rig.jobs.submitted if k.startswith("coder.replay")]
    ov = db_session.get(Overlay, ("sim-3", arms["sim-3"].device.overlay.overlay_id))
    db_session.refresh(ov)
    assert ov.status == "locked_in"  # not lockin_error / rolled back: nothing sticky

    # `make device-verify` ran: the next tick replays it for real and it passes.
    rig.code.settings = settings.model_copy(update={"coder_verify_dir": str(verify_dir(tmp_path))})
    rig.work(now + 300 * S)
    cand = _code(client)
    assert cand["replay"]["passed"] and not cand["replay"]["stub"], cand["replay"]
    assert "held-out verify scripts" in cand["replay"]["why"]
    assert "unverified" not in cand["replay"]["why"]
    assert cand["status"] == "awaiting_canary"
    assert "candidate.unverified" not in _kinds(cand)


def test_f11_allow_unverified_passes_on_device_tests_with_a_loud_warning(
    client, arms, tick_arms, now, settings, bare_fleet, sessions, clock, db_session
):
    rig = Rig(sessions, settings.model_copy(update={"coder_allow_unverified": True}), clock)
    _held(arms, tick_arms, now)
    rig.work(now + 40 * S)
    rig.work(now + 41 * S)
    cand = _code(client)
    assert cand["replay"]["passed"], cand["replay"]
    assert cand["replay"]["why"].startswith("unverified: decided by in-process tests only")
    kinds = _kinds(cand)
    assert "candidate.awaiting_verify" not in kinds
    (warn,) = [e for e in cand["events"] if e["kind"] == "candidate.unverified"]
    assert "WARNING unverified: decided by in-process tests only" in warn["why"]
    (row,) = events.find(db_session, {"candidate": cand["id"]}, kinds=["candidate.unverified"])
    assert row.data["level"] == "warning"


def test_f11_the_default_verify_dir_missing_waits_and_never_rolls_back(
    client,
    arms,
    tick_arms,
    now,
    settings,
    bare_fleet,
    sessions,
    clock,
    db_session,
    monkeypatch,
    tmp_path,
):
    # `.env.example`'s default, before anyone ran `make device-verify`: the dir doesn't exist.
    monkeypatch.chdir(tmp_path)
    default = settings.model_copy(update={"coder_verify_dir": "data/verify/device"})
    rig = Rig(sessions, default, clock)
    _held(arms, tick_arms, now)
    for t in (40, 41, 48, 55):
        rig.work(now + t * S)
    cand = _code(client)
    assert cand["status"] == "replaying" and cand["replay"] is None, cand["events"][-1]
    (wait,) = [e for e in cand["events"] if e["kind"] == "candidate.awaiting_verify"]
    assert "no held-out verify at `data/verify/device`" in wait["why"]
    assert "run `make device-verify`" in wait["why"]
    assert "candidate.rolled_back" not in _kinds(cand)
    ov = db_session.get(Overlay, ("sim-3", arms["sim-3"].device.overlay.overlay_id))
    db_session.refresh(ov)
    assert ov.status == "locked_in"


def test_f11_a_parked_candidate_says_why_again_after_an_unfreeze(
    client, arms, tick_arms, now, settings, bare_fleet, sessions, clock
):
    rig = Rig(sessions, settings, clock)
    _held(arms, tick_arms, now)
    rig.work(now + 40 * S)
    rig.work(now + 41 * S)
    assert client.post("/v1/fleet/freeze", json={"by": "ci", "why": "test"}).is_success
    rig.work(now + 50 * S)
    assert _code(client)["status"] == "frozen"
    assert client.post("/v1/fleet/unfreeze", json={"by": "ci", "why": "test"}).is_success
    for t in (60, 61, 70, 80):  # unfrozen (restarted), then parked again
        rig.work(now + t * S)
    cand = _code(client)
    waits = ("candidate.restarted", "candidate.awaiting_verify")
    kinds = [k for k in _kinds(cand) if k in waits]
    assert kinds == [
        "candidate.awaiting_verify",
        "candidate.restarted",
        "candidate.awaiting_verify",
    ]
    assert cand["events"][-1]["kind"] == "candidate.awaiting_verify"


@pytest.mark.parametrize("error", [PermissionError, FileNotFoundError])
def test_f11_an_unreadable_verify_dir_waits_and_the_tick_goes_on(
    client,
    arms,
    tick_arms,
    now,
    settings,
    bare_fleet,
    sessions,
    clock,
    db_session,
    monkeypatch,
    tmp_path,
    error,
):
    # e.g. a permission-denied file, or one deleted while `make device-verify` regenerates
    def unreadable(directory):
        raise error(13, "unreadable", f"{directory}/test_bench_verify.py")

    monkeypatch.setattr(code_lockin, "verify_files", unreadable)
    vdir = str(verify_dir(tmp_path))
    rig = Rig(sessions, settings.model_copy(update={"coder_verify_dir": vdir}), clock)
    _held(arms, tick_arms, now)
    for t in (40, 41, 48, 55):  # `work` asserts every step of every tick finished "ok"
        rig.work(now + t * S)
    cand = _code(client)
    assert cand["status"] == "replaying" and cand["replay"] is None, cand["events"][-1]
    (wait,) = [e for e in cand["events"] if e["kind"] == "candidate.awaiting_verify"]
    assert f"configured verify dir `{vdir}` could not be read ({error.__name__})" in wait["why"]
    assert "candidate.rolled_back" not in _kinds(cand) and "candidate.error" not in _kinds(cand)
    ov = db_session.get(Overlay, ("sim-3", arms["sim-3"].device.overlay.overlay_id))
    db_session.refresh(ov)
    assert ov.status == "locked_in"
    # The other candidate from the same overlay (its harness skill) still moves on.
    (harness,) = [c for c in client.get("/v1/candidates").json() if c["type"] == "harness"]
    assert harness["status"] != "replaying" and harness["replay"]["passed"], harness
