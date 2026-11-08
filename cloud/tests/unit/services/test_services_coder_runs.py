import sys
from pathlib import Path

import pytest

from romulus_cloud.adapters.coder import CoderError, Sandbox
from romulus_cloud.services.coder_runs import replay_code_patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "fixtures"))
import coder_fakes as fakes  # noqa: E402

BAD_PATCH = fakes.TOY_PATCH.replace("if release_requested:", "if nothing_like_this:")


def replay(docker, patch=fakes.TOY_PATCH, **kw):
    return replay_code_patch(
        fakes.toy_files(), patch, sandbox=Sandbox(image="romulus-coder:test", runner=docker), **kw
    )


def test_device_patch_fixes_what_the_tests_catch():
    docker = fakes.FakeDocker()
    result = replay(docker)

    assert result.caught_fault and result.passed
    assert not result.base.passed and "test_verify_d1" in result.base.output
    assert result.patched.passed
    assert result.summary() == "device tests: base fail (exit 1), patched pass"
    runs = [c["argv"] for c in docker.calls]
    assert len(runs) == 2
    for argv in runs:  # tests only: no agent, no network, no LLM env
        assert argv[-1] == "test"
        assert argv[argv.index("--network") + 1] == "none"
        assert not any(a.startswith("LLM_") for a in argv)


def test_patch_that_does_not_apply():
    result = replay(fakes.FakeDocker(), patch=BAD_PATCH)
    assert result.patched is None and not result.passed
    assert "git apply failed" in result.patch_error
    assert "patch does not apply" in result.summary()


def test_verify_scripts_the_agent_never_saw_are_laid_over_both_runs():
    files = fakes.toy_files()
    verify = files.pop("device/tests/test_verify_d1.py")
    docker = fakes.FakeDocker()
    result = replay_code_patch(
        files,
        fakes.TOY_PATCH,
        sandbox=Sandbox(runner=docker),
        extra_files={"tests/test_verify_d1.py": verify},
    )
    assert result.caught_fault and result.passed


def test_extra_files_must_stay_inside_device():
    with pytest.raises(CoderError, match="inside device/"):
        replay(fakes.FakeDocker(), extra_files={"../evil.py": b"x"})


def test_sandbox_timeout_is_a_failure():
    result = replay(fakes.FakeDocker(timed_out=True))
    assert result.base.timed_out and not result.passed
    assert result.summary().startswith("device tests: base timed out, patched timed out")
