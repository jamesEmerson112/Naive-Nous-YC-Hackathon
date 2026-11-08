"""Code replay: run the device tests on a code package before and after a patch (M8).

The gate's future replay for a code candidate is "device tests + verify scripts" (BUILD §3,
`docs/fault-catalog.md`): run the test command on (a) the base code and (b) base + patch, in
the `coder` sandbox with no network. `extra_files` (e.g. a fault's verify scripts, which the
agent never saw) are laid over both runs, paths relative to `device/`.

Not wired into the gate yet (M8b). Touches no database and records no events: the caller
turns a `CodeReplay` into a gate decision and an Event.
"""

from __future__ import annotations

import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from romulus_cloud.adapters.coder import (
    DEFAULT_TEST_CMD,
    CoderError,
    Sandbox,
    SandboxRun,
    ScratchRepo,
    device_relative,
)


@dataclass(frozen=True)
class TestRun:
    __test__ = False  # not a pytest class

    exit_code: int | None  # None: the sandbox returned no result
    output: str
    timed_out: bool = False

    @property
    def passed(self) -> bool:
        return self.exit_code == 0 and not self.timed_out


@dataclass(frozen=True)
class CodeReplay:
    base: TestRun
    patched: TestRun | None  # None when the patch doesn't apply
    patch_error: str | None = None

    @property
    def passed(self) -> bool:
        """The patched code passes the tests (what the gate needs)."""
        return self.patched is not None and self.patched.passed

    @property
    def caught_fault(self) -> bool:
        """The tests fail on the base code, so they do test what the patch fixes."""
        return not self.base.passed

    def summary(self) -> str:
        def state(run: TestRun | None) -> str:
            if run is None:
                return "not run"
            if run.timed_out:
                return "timed out"
            if run.exit_code is None:
                return "no result"
            return "pass" if run.passed else f"fail (exit {run.exit_code})"

        tail = f"; patch does not apply: {self.patch_error}" if self.patch_error else ""
        return f"device tests: base {state(self.base)}, patched {state(self.patched)}{tail}"


def _test_run(run: SandboxRun) -> TestRun:
    tests = run.tests
    if run.result is None:
        return TestRun(None, run.proc.stderr.strip()[-2000:], timed_out=run.proc.timed_out)
    return TestRun(
        tests.get("exit_code"),
        tests.get("output", ""),
        timed_out=bool(tests.get("timed_out")) or run.proc.timed_out,
    )


def _overlay(repo: ScratchRepo, extra_files: Mapping[str, bytes]) -> None:
    for name, data in extra_files.items():
        rel = device_relative(f"device/{name}")
        if rel is None:
            raise CoderError(f"extra file {name!r} must stay inside device/")
        path = repo.work / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)


def replay_code_patch(
    files: Mapping[str, bytes],
    patch: str,
    *,
    sandbox: Sandbox,
    test_cmd: str = DEFAULT_TEST_CMD,
    extra_files: Mapping[str, bytes] | None = None,
    timeout_s: int = 300,
    scratch_dir: str | None = None,
) -> CodeReplay:
    """Run `test_cmd` on the package `files` (paths under `device/`), then on files + patch."""
    env = {"CODER_TEST_CMD": test_cmd, "CODER_TEST_TIMEOUT_S": str(timeout_s)}

    def run_tests(repo: ScratchRepo) -> TestRun:
        run = sandbox.run(repo.work, "test", env=env, timeout_s=timeout_s + 60, network="none")
        return _test_run(run)

    with tempfile.TemporaryDirectory(prefix="romulus-replay-", dir=scratch_dir) as tmp:
        base_repo = ScratchRepo.create(Path(tmp) / "base", files)
        _overlay(base_repo, extra_files or {})
        base = run_tests(base_repo)

        patched_repo = ScratchRepo.create(Path(tmp) / "patched", files)
        error = patched_repo.apply(patch)
        if error is not None:
            return CodeReplay(base=base, patched=None, patch_error=error)
        _overlay(patched_repo, extra_files or {})
        return CodeReplay(base=base, patched=run_tests(patched_repo))
