"""services/code_lockin.py and the batch's coder factory (M8b)."""

import sys
from pathlib import Path

import pytest
from pydantic import SecretStr

from romulus_cloud.adapters.blobstore import build_blobstore
from romulus_cloud.adapters.coder import CoderError, FakeCoder, LocalAgent, Sandbox
from romulus_cloud.models import Candidate
from romulus_cloud.services import batch, code_lockin, registry
from romulus_cloud.services.coder_runs import CodeReplay, TestRun, VerifyRun
from romulus_cloud.services.jobs import Done, Failed, InlineJobs, Missing, Pending

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "fixtures"))
import coder_fakes as fakes  # noqa: E402

KW = {"actor": "human:test", "actor_class": "human"}
KEY = "sk-unit-SECRET"
DELETE_SPEC = "diff --git a/SPEC.md b/SPEC.md\ndeleted file mode 100644\n"


# --- plain-data helpers -------------------------------------------------------------------


def test_apply_diff_applies_the_coders_diff_to_the_package():
    base = fakes.toy_files()
    new = code_lockin.apply_diff(base, fakes.TOY_PATCH)
    rule = b"\n        if dist_mm is not None and dist_mm < PROXIMITY_MM:"
    assert rule not in new["device/control/gripper.py"]
    assert rule in base["device/control/gripper.py"]  # the input is untouched
    assert {k for k in new if new[k] != base.get(k)} == {"device/control/gripper.py"}


def test_apply_diff_refuses_a_diff_that_does_not_apply():
    bad = fakes.TOY_PATCH.replace("if release_requested:", "if nothing_like_this:")
    with pytest.raises(CoderError, match="does not apply"):
        code_lockin.apply_diff(fakes.toy_files(), bad)


def test_package_diff_round_trips_with_new_and_deleted_files():
    base = fakes.toy_files()
    new = code_lockin.apply_diff(base, fakes.TOY_PATCH)
    new["device/tests/test_new.py"] = b"def test_new():\n    pass\n"
    del new["device/SPEC.md"]
    diff = code_lockin.package_diff(base, new)
    assert "b/tests/test_new.py" in diff and "deleted file mode" in diff
    assert code_lockin.apply_diff(base, diff) == new


def test_failing_tests_and_summary_read_pytest_output():
    out = (
        "..F\nFAILED tests/test_a.py::test_x - AssertionError\nERROR tests/test_b.py::test_y\n"
        "FAILED tests/test_a.py::test_x - again\n==== 1 failed, 2 passed in 0.12s ====\n"
    )
    failing = ["tests/test_a.py::test_x", "tests/test_b.py::test_y"]
    assert code_lockin.failing_tests(out, "") == failing
    assert code_lockin.test_summary(out) == "1 failed, 2 passed in 0.12s"
    assert code_lockin.test_summary("weird\nlast line") == "last line"
    assert code_lockin.test_summary("") == "no output"


def _replay(patched_verify_passed: int, output: str = "") -> CodeReplay:
    return CodeReplay(
        base=TestRun(0, "4 passed"),
        base_verify=VerifyRun(True, tests=2, passed=1, exit_code=1),
        patched=TestRun(0, "5 passed"),
        patched_verify=VerifyRun(
            True,
            tests=2,
            passed=patched_verify_passed,
            exit_code=0 if patched_verify_passed == 2 else 1,
            output=output,
        ),
    )


def test_a_passing_replay_reads_as_a_real_pass():
    result = code_lockin.to_replay_result(_replay(2), "abc1234", [KEY])
    assert result.passed and not result.stub
    assert result.why.startswith("code replay passed on code abc1234")
    assert "verify 2/2 passed" in result.why


def test_a_failing_replay_names_the_failing_tests_and_redacts():
    out = f"FAILED verify/test_verify_d1.py::test_hand_near - {KEY}\n1 failed, 1 passed"
    result = code_lockin.to_replay_result(_replay(1, out), "abc1234", [KEY])
    assert not result.passed
    assert "failing: verify/test_verify_d1.py::test_hand_near" in result.why
    assert [c.case_id for c in result.cases] == ["verify/test_verify_d1.py::test_hand_near"]
    out = code_lockin.replay_output(_replay(1, out), [KEY])
    assert KEY not in str(out) and "[redacted]" in out["replay_output"]["patched_verify"]


def test_verify_dir_maps_files_under_verify(tmp_path):
    (tmp_path / "d1").mkdir()
    (tmp_path / "d1" / "test_d1.py").write_text("def test_x():\n    pass\n")
    (tmp_path / "__pycache__").mkdir()
    (tmp_path / "__pycache__" / "x.pyc").write_bytes(b"junk")
    assert set(code_lockin.verify_files(str(tmp_path))) == {"verify/d1/test_d1.py"}
    assert code_lockin.verify_files("") == {}
    with pytest.raises(CoderError, match="not a directory"):
        code_lockin.verify_files(str(tmp_path / "missing"))


# --- the gate's side ----------------------------------------------------------------------


class StuckJobs:
    def __init__(self, status=None):
        self.status, self.submitted = status or Pending(), []

    def submit(self, key, fn):
        self.submitted.append(key)

    def poll(self, key):
        return self.status if self.submitted else Missing()

    def forget(self, key):
        self.submitted = [k for k in self.submitted if k != key]


@pytest.fixture
def code_versions(db_session, settings):
    store = build_blobstore(settings.blob_dir)
    base = registry.build_tarball(fakes.toy_files())
    registry.create_code(db_session, store, base, rev="toy0001", **KW)
    fixed = registry.build_tarball(code_lockin.apply_diff(fakes.toy_files(), fakes.TOY_PATCH))
    registry.create_code(
        db_session, store, fixed, rev="fix0001", source="coder", parent_rev="toy0001", **KW
    )
    registry.create_code(db_session, store, base, rev="pass001", source="passthrough", **KW)
    db_session.flush()
    return store


def _cand(rev):
    return Candidate(id=f"cand_{rev}", fleet_id="default", type="code", code_rev=rev)


def _tools(settings, store, jobs, **update):
    s = settings.model_copy(update={"llm_api_key": SecretStr(KEY), **update})
    return code_lockin.ReplayTools(s, jobs, Sandbox(runner=fakes.FakeDocker()), store)


def test_passthrough_code_keeps_the_labeled_stub(db_session, settings, code_versions):
    jobs = StuckJobs()
    tools = _tools(settings, code_versions, jobs)
    result = code_lockin.gate_replay(db_session, _cand("pass001"), tools)
    assert result.stub and "device tests not wired (M8)" in result.why
    assert jobs.submitted == []
    assert code_lockin.is_passthrough(db_session, _cand("pass001"))
    assert code_lockin.is_passthrough(db_session, _cand("nope"))  # unknown: fail closed
    assert not code_lockin.is_passthrough(db_session, _cand("fix0001"))


def test_no_test_command_still_runs_the_verify_scripts(db_session, settings, code_versions):
    jobs = InlineJobs()
    tools = _tools(settings, code_versions, jobs, coder_test_cmd=" ")
    result = code_lockin.gate_replay(db_session, _cand("fix0001"), tools)
    assert result.passed and not result.stub, result.why
    assert "tests none (CODER_TEST_CMD is empty)" in result.why
    assert "verify 2/2 passed" in result.why


def test_no_test_command_and_no_verify_scripts_fails_closed(db_session, settings):
    store = build_blobstore(settings.blob_dir)
    bare = {k: v for k, v in fakes.toy_files().items() if "/verify/" not in k}
    registry.create_code(db_session, store, registry.build_tarball(bare), rev="bare001", **KW)
    fixed = registry.build_tarball(code_lockin.apply_diff(bare, fakes.TOY_PATCH))
    registry.create_code(
        db_session, store, fixed, rev="bfix001", source="coder", parent_rev="bare001", **KW
    )
    jobs = StuckJobs()
    tools = _tools(settings, store, jobs, coder_test_cmd="")
    result = code_lockin.gate_replay(db_session, _cand("bfix001"), tools)
    assert not result.passed and not result.stub
    assert "nothing can check the coder's change" in result.why
    assert jobs.submitted == []


def test_a_blocked_worker_waits_instead_of_submitting(db_session, settings, code_versions):
    jobs = StuckJobs()
    tools = _tools(settings, code_versions, jobs)
    tools.blocked = "no docker here"
    assert code_lockin.gate_replay(db_session, _cand("fix0001"), tools) is None
    assert jobs.submitted == []


def test_coder_blocker_needs_docker_only_for_local_agent(make_settings):
    fake = make_settings()
    assert code_lockin.coder_blocker(fake, docker="no-such-docker") is None
    live = fake.model_copy(update={"coder_provider": "local_agent"})
    why = code_lockin.coder_blocker(live, docker="no-such-docker")
    assert "no `docker` CLI" in why and "make worker-host" in why
    assert code_lockin.coder_blocker(live, docker=sys.executable) is None


def test_applying_a_replay_forgets_the_job(db_session, settings, code_versions):
    jobs = InlineJobs()
    tools = _tools(settings, code_versions, jobs)
    cand = _cand("fix0001")
    assert code_lockin.gate_replay(db_session, cand, tools).passed
    out = code_lockin.gate_replay_applied(cand, tools)
    assert "replay_output" in out
    assert isinstance(jobs.poll(code_lockin.replay_key(cand)), Missing)


def test_draft_key_is_the_code_version_and_the_patch_hash():
    class Ov:
        fleet_id = "default"

        def __init__(self, patch):
            self.code_patch = patch

    a, b = Ov(fakes.TOY_PATCH), Ov(fakes.TOY_PATCH)
    assert code_lockin.draft_key(a, "toy0001") == code_lockin.draft_key(b, "toy0001")
    assert code_lockin.draft_key(a, "toy0001") != code_lockin.draft_key(a, "toy0002")
    assert code_lockin.draft_key(a, "r") != code_lockin.draft_key(Ov(fakes.TOY_PATCH + " "), "r")


def test_a_running_replay_returns_none_so_the_gate_waits(db_session, settings, code_versions):
    jobs = StuckJobs()
    tools = _tools(settings, code_versions, jobs)
    assert code_lockin.gate_replay(db_session, _cand("fix0001"), tools) is None
    assert code_lockin.gate_replay(db_session, _cand("fix0001"), tools) is None
    assert jobs.submitted == ["coder.replay:default:cand_fix0001"]  # submitted once


def test_a_replay_that_could_not_run_fails_with_the_reason(db_session, settings, code_versions):
    jobs = StuckJobs(Failed(f"RuntimeError: docker gone {KEY}"))
    result = code_lockin.gate_replay(
        db_session, _cand("fix0001"), _tools(settings, code_versions, jobs)
    )
    assert not result.passed and "code replay could not run" in result.why
    assert KEY not in result.why


def test_an_inline_replay_runs_base_and_patched(db_session, settings, code_versions):
    jobs = InlineJobs()
    result = code_lockin.gate_replay(
        db_session, _cand("fix0001"), _tools(settings, code_versions, jobs)
    )
    assert result.passed, result.why
    assert isinstance(jobs.poll("coder.replay:default:cand_fix0001"), Done)


# --- the batch's coder factory ------------------------------------------------------------


def test_build_coder_follows_the_setting(make_settings):
    assert isinstance(batch._build_coder(make_settings()), FakeCoder)
    live = make_settings().model_copy(
        update={
            "coder_provider": "local_agent",
            "llm_base_url": "http://llm.invalid/v1",
            "llm_model": "m",
            "coder_step_limit": 15,
            "coder_scratch_dir": "/tmp/x",
        }
    )
    agent = batch._build_coder(live)
    assert isinstance(agent, LocalAgent)
    assert agent.step_limit == 15 and agent.scratch_dir == "/tmp/x"
    broken = batch._build_coder(live.model_copy(update={"llm_base_url": ""}))
    with pytest.raises(CoderError, match="CODER_PROVIDER=local_agent: .*LLM_BASE_URL"):
        broken.draft("", {}, "rev")
    stub = batch._build_coder(live.model_copy(update={"coder_provider": "naive_session"}))
    with pytest.raises(CoderError, match="M8 stub"):
        stub.draft("", {}, "rev")
