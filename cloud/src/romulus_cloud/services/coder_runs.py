"""Code replay: run the device tests and the held-out verify scripts before and after a patch.

The gate's replay for a code candidate is "device tests + verify scripts" (BUILD §3,
`docs/fault-catalog.md`). **This, not `CodeDraft.passed`, is what the gate trusts.** For the
base code and for base + patch, each in its own scratch copy and fresh containers with no
network:
1. the held-out `verify/` scripts, run by the image's fixed `verify` mode (isolated Python,
   `--noconftest`, a clean pytest config, no cache), which must pass *every* test the base
   collected: a skipped, deselected or missing verify test is not a pass;
2. the device's own test command (skipped when it is empty; then the verify scripts must
   carry the replay alone, and with neither the replay fails: nothing checked the patch).

The verify scripts are the package's `device/verify/` plus any `verify_files` the caller
holds (e.g. from the fault catalog). A patch is refused before it is applied when it has a
symlink, a submodule, a binary change or a path outside `device/`, or touches (at any depth,
any case, read by the contract's parser so both path forms count):
- `verify/`;
- anything that can shadow the test runner: `pytest.py`, `py.py`, a `pytest/` or `_pytest/`
  dir, `sitecustomize.py`, `usercustomize.py`, `*.pth`, `*.dist-info/`, `*.egg-info/`;
- test-runner config: any `conftest.py`, `pytest.ini`, `.pytest.ini`, `pyproject.toml`,
  `setup.cfg`, `tox.ini` (addopts, deselection, plugins);
- `.gitignore` / `.gitattributes` (they could hide a planted file from git's view).
A symlink or nested `.git` found after applying it refuses it too. Changes to existing tests
are flagged for the gate, read from the parsed patch (never from `git status`).

How the tests run (`cloud/coder/run_agent.py`): no outside `PYTHON*`/`PYTEST*` env, and
`python -m pytest` runs as `python -P -m pytest` (verify as `python -I`), so nothing in the
work dir is on `sys.path` before pytest loads. (Not `PYTHONSAFEPATH=1` in the env: the
device's tests start `python app.py`, which inherits it and then can't import its own
modules.) The device test step's pass needs pytest's exit 0
**and**, when the base run produced a JUnit report (written outside the work dir), a clean
report from the patched run with no fewer tests and no more skips than the base. Printed
text never decides anything.

Limit: the code under test runs inside the test process, so a deliberately malicious patch
could still forge its result from within (it can see the report path in its env). This
stops test gaming (skips, conftest, config, shadowing, fake summary lines, an early exit);
the canary and a person stop malice.

No database, no events: `code_lockin.gate_replay` runs it as a job off the gate's tick
(M8b) and turns the `CodeReplay` into the gate's replay result and an Event.
"""

from __future__ import annotations

import tempfile
from collections.abc import Iterable, Mapping
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
from romulus_cloud.domain.doctor import HARNESS_FILES, SHADOW_DIRS, SHADOW_FILES, patch_changes
from romulus_contracts.codepatch import is_under, summarize_code_patch

SHADOW_NAMES = SHADOW_FILES | {"py.py"}
SHADOW_SUFFIXES = (".pth",)
METADATA_DIRS = (".dist-info", ".egg-info")  # entry points here could autoload a plugin
GIT_FILES = frozenset({".gitignore", ".gitattributes"})


@dataclass(frozen=True)
class TestRun:
    __test__ = False  # not a pytest class

    exit_code: int | None  # None: the sandbox returned no result
    output: str
    timed_out: bool = False
    configured: bool = True  # False: no test command, so nothing ran (and nothing failed)
    # JUnit counts (tests/failures/errors/skipped) of the pytest the command ran, written
    # outside the work dir; None when there was none (not pytest, or it never finished).
    report: Mapping[str, int] | None = None

    @property
    def passed(self) -> bool:
        """Exit 0, and a report (if any) agrees: some tests, none failed or errored."""
        if not self.configured:
            return True
        if self.exit_code != 0 or self.timed_out:
            return False
        r = self.report
        return r is None or (r["tests"] > 0 and r["failures"] == 0 and r["errors"] == 0)

    def shortfall(self, base: TestRun) -> str | None:
        """Why this (patched) run doesn't count as passing against the `base` run's report,
        or None. Once the base wrote a JUnit report the command is pytest, so this run must
        write one too, with no fewer tests and no more skips (no deselecting or skipping
        the suite away, no exiting before pytest's summary)."""
        if not self.configured or base.report is None:
            return None
        r, b = self.report, base.report
        if r is None:
            return "no pytest report (the base run wrote one): pytest didn't finish"
        if r["tests"] < b["tests"]:
            return f"{r['tests']} tests ran, fewer than the base's {b['tests']}"
        if r["skipped"] > b["skipped"]:
            return f"{r['skipped']} tests skipped, more than the base's {b['skipped']}"
        return None


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
    def checked_anything(self) -> bool:
        """Device tests ran or there were verify scripts: without either nothing was checked."""
        return self.base.configured or self.expected_verify > 0

    @property
    def tests_passed(self) -> bool:
        """The patched device tests pass and match the base's pytest report (`shortfall`)."""
        return (
            self.patched is not None
            and self.patched.passed
            and self.patched.shortfall(self.base) is None
        )

    @property
    def passed(self) -> bool:
        """What the gate needs: device tests pass and every verify test passes on the patch,
        something was actually checked, and nothing touched the verify scripts (belt and
        braces: such a patch is refused). Fails closed."""
        touched_verify = any(f.startswith("verify_scripts:") for f in self.flags)
        return (
            self.tests_passed
            and self.verify_passed
            and self.checked_anything
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
            if not run.configured:
                return "none (CODER_TEST_CMD is empty)"
            if run.timed_out:
                return "timed out"
            if run.exit_code is None:
                return "no result"
            if run.exit_code == 0 and not run.passed:
                r = run.report or {}
                return (
                    f"fail (exit 0, but the pytest report says {r.get('tests', 0)} tests, "
                    f"{r.get('failures', 0)} failed, {r.get('errors', 0)} errors)"
                )
            if run.passed and run is self.patched:
                short = run.shortfall(self.base)
                if short:
                    return f"fail ({short})"
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
        if not self.checked_anything:
            text += "; nothing checked the patch: no device test command and no verify scripts"
        return text


def _test_run(run: SandboxRun) -> TestRun:
    if run.result is None:
        return TestRun(None, run.proc.stderr.strip()[-2000:], timed_out=run.proc.timed_out)
    tests = run.tests
    return TestRun(
        tests.get("exit_code"),
        tests.get("output", ""),
        timed_out=bool(tests.get("timed_out")) or run.proc.timed_out,
        report=_report(tests.get("report")),
    )


def _report(raw: object) -> dict[str, int] | None:
    """The image's JUnit counts, or None; a malformed report counts as zero tests."""
    if raw is None:
        return None
    keys = ("tests", "failures", "errors", "skipped")
    try:
        return {k: int(raw.get(k) or 0) for k in keys}  # type: ignore[attr-defined]
    except (AttributeError, TypeError, ValueError):
        return dict.fromkeys(keys, 0)


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


def harness_tampering(paths: Iterable[str]) -> list[str]:
    """Paths (relative to `device/`, as the contract's parser reads them) that can change
    how the tests are run or judged, each with why. Any depth, any case."""
    found = []
    for path in paths:
        parts = path.casefold().split("/")
        name, dirs = parts[-1], parts[:-1]
        if (
            name in SHADOW_NAMES
            or name.endswith(SHADOW_SUFFIXES)
            or SHADOW_DIRS & set(dirs)
            or any(d.endswith(METADATA_DIRS) for d in dirs)
        ):
            found.append(f"{path} (it can shadow the test runner)")
        elif name in HARNESS_FILES:
            found.append(f"{path} (test-runner config)")
        elif name in GIT_FILES:
            found.append(f"{path} (it can hide files from git)")
    return found


def patch_refusal(patch: str) -> str | None:
    """Why a patch can't be replayed at all: format problems, it touches `verify/`, or it
    touches the test harness (`harness_tampering`)."""
    summary = summarize_code_patch(patch)
    if summary.problems:
        return "; ".join(summary.problems)
    # Case-insensitive: on macOS and Docker Desktop mounts `VERIFY/x` *is* `verify/x`.
    held = [p for p in summary.paths if is_under(p, HOLDOUT_DIR)]
    if held:
        return f"it touches the held-out {HOLDOUT_DIR}/ scripts: {', '.join(held)}"
    tampering = harness_tampering(summary.paths)
    if tampering:
        return "it touches the test harness: " + ", ".join(tampering)
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
    extra_env: Mapping[str, str] | None = None,
) -> CodeReplay:
    """Run verify + `test_cmd` on the package `files` (paths under `device/`), then on
    files + patch. `verify_files` paths are relative to `device/` and must be under verify/.
    `extra_env` adds variables to every container run; it can't override the two this
    function sets. (The image drops any `PYTHON*` from it for the test commands.)"""
    extra = dict(verify_files or {})
    for name in extra:
        if PurePosixPath(name).parts[:1] != (HOLDOUT_DIR,) or ".." in PurePosixPath(name).parts:
            raise CoderError(f"verify file {name!r} must be under {HOLDOUT_DIR}/")
    env = {
        **dict(extra_env or {}),
        "CODER_TEST_CMD": test_cmd,
        "CODER_TEST_TIMEOUT_S": str(timeout_s),
    }

    def run_both(repo: ScratchRepo) -> tuple[VerifyRun, TestRun]:
        for name, data in extra.items():
            safe_write(repo.work, name, data)
        kw = {"env": env, "timeout_s": timeout_s + 60, "network": "none"}
        verify = _verify_run(sandbox.run(repo.work, "verify", **kw))
        if not test_cmd.strip():
            return verify, TestRun(None, "", configured=False)
        return verify, _test_run(sandbox.run(repo.work, "test", **kw))

    with tempfile.TemporaryDirectory(prefix="romulus-replay-", dir=scratch_dir) as tmp:
        base_verify, base = run_both(ScratchRepo.create(Path(tmp) / "base", files))

        def refused(why: str, flags: tuple[str, ...] = ()) -> CodeReplay:
            return CodeReplay(base, base_verify, None, None, patch_error=why, flags=flags)

        why = patch_refusal(patch)
        if why:
            return refused(why)
        repo = ScratchRepo.create(Path(tmp) / "patched", files)
        error = repo.apply(patch)
        if error:
            return refused(error)
        escapes = find_escapes(repo.work)
        if escapes:
            return refused("; ".join(escapes))
        # From the parsed patch, not `git status`: nothing in the tree can hide a change.
        flags = harness_flags(patch_changes(patch))
        patched_verify, patched = run_both(repo)
        return CodeReplay(base, base_verify, patched, patched_verify, flags=flags)
