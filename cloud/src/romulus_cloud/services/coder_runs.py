"""Code replay: run the device tests and the held-out verify scripts before and after a patch.

The gate's replay for a code candidate is "device tests + verify scripts" (BUILD §3,
`docs/fault-catalog.md`). **This, not `CodeDraft.passed`, is what the gate trusts.** For the
base code and for base + patch, each in its own scratch copy and fresh containers with no
network:
1. the held-out `verify/` scripts, run by the image's fixed `verify` mode (isolated Python,
   `--noconftest`, a clean pytest config, no cache), which must pass *every* test the base
   collected: a skipped, deselected or missing verify test is not a pass;
2. the device's own test command.

The verify scripts are the package's `device/verify/` plus any `verify_files` the caller
holds (e.g. from the fault catalog). A patch that touches `verify/`, or has a symlink, a
submodule, a binary change or a path outside `device/`, is refused before it is applied, and
a symlink or nested `.git` found after applying it refuses it too. Changes to existing tests
or pytest config are flagged for the gate.

Limit: the code under test runs inside the verify process, so a deliberately malicious patch
could still forge its result from within. This stops test gaming (skips, conftest, config);
the canary and a person stop malice.

Not wired into the gate yet (M8b). No database, no events: the caller turns a `CodeReplay`
into a gate decision and an Event.
"""

from __future__ import annotations

import tempfile
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from romulus_cloud.adapters.coder import (
    DEFAULT_TEST_CMD,
    HOLDOUT_DIR,
    CoderError,
    Sandbox,
    SandboxRun,
    ScratchRepo,
    find_escapes,
    harness_flags,
    safe_write,
)
from romulus_contracts.codepatch import is_under, summarize_code_patch


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
class VerifyRun:
    ran: bool
    tests: int = 0
    passed: int = 0
    skipped: int = 0
    exit_code: int | None = None
    output: str = ""
    timed_out: bool = False

    def all_passed(self, expected: int) -> bool:
        """Exactly `expected` tests ran and passed, none skipped, pytest exited 0."""
        return (
            self.ran
            and not self.timed_out
            and self.exit_code == 0
            and self.skipped == 0
            and self.tests == expected
            and self.passed == expected
        )


@dataclass(frozen=True)
class CodeReplay:
    base: TestRun
    base_verify: VerifyRun
    patched: TestRun | None  # None when the patch is refused
    patched_verify: VerifyRun | None
    patch_error: str | None = None
    flags: tuple[str, ...] = field(default_factory=tuple)

    @property
    def expected_verify(self) -> int:
        """How many verify tests must pass: all the base collected from the pristine scripts."""
        return self.base_verify.tests

    @property
    def verify_passed(self) -> bool:
        if self.patched_verify is None:
            return False
        if self.expected_verify == 0:
            return not self.patched_verify.ran  # no verify scripts at all
        return self.patched_verify.all_passed(self.expected_verify)

    @property
    def passed(self) -> bool:
        """What the gate needs: device tests pass and every verify test passes on the patch,
        and nothing touched the verify scripts (belt and braces: such a patch is refused)."""
        touched_verify = any(f.startswith("verify_scripts:") for f in self.flags)
        return (
            self.patched is not None
            and self.patched.passed
            and self.verify_passed
            and not touched_verify
        )

    @property
    def caught_fault(self) -> bool:
        """The base code fails the verify scripts or the tests, so they test the fix."""
        verify_failed = self.expected_verify > 0 and not self.base_verify.all_passed(
            self.expected_verify
        )
        return verify_failed or not self.base.passed

    def summary(self) -> str:
        def tests(run: TestRun | None) -> str:
            if run is None:
                return "not run"
            if run.timed_out:
                return "timed out"
            if run.exit_code is None:
                return "no result"
            return "pass" if run.passed else f"fail (exit {run.exit_code})"

        def verify(run: VerifyRun | None) -> str:
            if run is None:
                return "not run"
            if not run.ran:
                return "none"
            if run.timed_out:
                return "timed out"
            return f"{run.passed}/{self.expected_verify} passed" + (
                f", {run.skipped} skipped" if run.skipped else ""
            )

        text = (
            f"base: tests {tests(self.base)}, verify {verify(self.base_verify)}; "
            f"patched: tests {tests(self.patched)}, verify {verify(self.patched_verify)}"
        )
        if self.patch_error:
            text += f"; patch refused: {self.patch_error}"
        if self.flags:
            text += "; flagged: " + "; ".join(self.flags)
        return text


def _test_run(run: SandboxRun) -> TestRun:
    if run.result is None:
        return TestRun(None, run.proc.stderr.strip()[-2000:], timed_out=run.proc.timed_out)
    tests = run.tests
    return TestRun(
        tests.get("exit_code"),
        tests.get("output", ""),
        timed_out=bool(tests.get("timed_out")) or run.proc.timed_out,
    )


def _verify_run(run: SandboxRun) -> VerifyRun:
    v = run.verify
    if run.result is None:
        # No result at all: it counts as ran-and-failed, never as "no verify scripts".
        return VerifyRun(True, output=run.proc.stderr.strip()[-2000:], timed_out=True)
    return VerifyRun(
        ran=bool(v.get("ran")),
        tests=int(v.get("tests") or 0),
        passed=int(v.get("passed") or 0),
        skipped=int(v.get("skipped") or 0),
        exit_code=v.get("exit_code"),
        output=v.get("output", ""),
        timed_out=bool(v.get("timed_out")) or run.proc.timed_out,
    )


def _refusal(patch: str) -> str | None:
    summary = summarize_code_patch(patch)
    if summary.problems:
        return "; ".join(summary.problems)
    # Case-insensitive: on macOS and Docker Desktop mounts `VERIFY/x` *is* `verify/x`.
    held = [p for p in summary.paths if is_under(p, HOLDOUT_DIR)]
    if held:
        return f"it touches the held-out {HOLDOUT_DIR}/ scripts: {', '.join(held)}"
    return None


def replay_code_patch(
    files: Mapping[str, bytes],
    patch: str,
    *,
    sandbox: Sandbox,
    test_cmd: str = DEFAULT_TEST_CMD,
    verify_files: Mapping[str, bytes] | None = None,
    timeout_s: int = 300,
    scratch_dir: str | None = None,
) -> CodeReplay:
    """Run verify + `test_cmd` on the package `files` (paths under `device/`), then on
    files + patch. `verify_files` paths are relative to `device/` and must be under verify/."""
    extra = dict(verify_files or {})
    for name in extra:
        if PurePosixPath(name).parts[:1] != (HOLDOUT_DIR,) or ".." in PurePosixPath(name).parts:
            raise CoderError(f"verify file {name!r} must be under {HOLDOUT_DIR}/")
    env = {"CODER_TEST_CMD": test_cmd, "CODER_TEST_TIMEOUT_S": str(timeout_s)}

    def run_both(repo: ScratchRepo) -> tuple[VerifyRun, TestRun]:
        for name, data in extra.items():
            safe_write(repo.work, name, data)
        kw = {"env": env, "timeout_s": timeout_s + 60, "network": "none"}
        verify = _verify_run(sandbox.run(repo.work, "verify", **kw))
        return verify, _test_run(sandbox.run(repo.work, "test", **kw))

    with tempfile.TemporaryDirectory(prefix="romulus-replay-", dir=scratch_dir) as tmp:
        base_verify, base = run_both(ScratchRepo.create(Path(tmp) / "base", files))

        def refused(why: str, flags: tuple[str, ...] = ()) -> CodeReplay:
            return CodeReplay(base, base_verify, None, None, patch_error=why, flags=flags)

        why = _refusal(patch)
        if why:
            return refused(why)
        repo = ScratchRepo.create(Path(tmp) / "patched", files)
        error = repo.apply(patch)
        if error:
            return refused(error)
        escapes = find_escapes(repo.work)
        if escapes:
            return refused("; ".join(escapes))
        flags = harness_flags(repo.changes())
        patched_verify, patched = run_both(repo)
        return CodeReplay(base, base_verify, patched, patched_verify, flags=flags)
