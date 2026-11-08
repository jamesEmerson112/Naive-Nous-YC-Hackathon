"""Code replay: run the device tests and the held-out verify scripts before and after a patch.

The gate's replay for a code candidate is "device tests + verify scripts" (BUILD §3,
`docs/fault-catalog.md`). **This, not `CodeDraft.passed`, is what the gate trusts**, and the
gate needs **both** parts to pass. For the base code and for base + patch, each in its own
scratch copy and fresh containers with no network:
1. **The held-out `verify/` scripts: the decisive check.** The image's fixed `verify` mode
   runs them in a pytest process that never imports anything from the work dir (isolated
   Python, cwd outside it, importlib mode, no conftest, a clean config, no cache); they read
   the candidate only as data and start it as a subprocess, so code under test never runs
   inside the process that decides. They must pass *every* test the base collected: a
   skipped, deselected or missing verify test is not a pass.
2. **The device's own test command: defense in depth.** The device's tests import code
   under test into the pytest process, so a determined patch could forge their result from
   inside; they still catch honest mistakes and cheap gaming. The trusted run uses the
   base's tests plus only the patch's **code** hunks (`domain.doctor.code_only`): the
   patch's new or changed tests run separately as an **advisory** run that is recorded and
   never decides. The trusted run must report, through the image's result seal (a pytest
   plugin from outside the tree streaming each test's outcome to a pipe the harness owns,
   ended by a marker), the same test ids as the base, all passed (a test the base skipped
   may stay skipped), none missing, and exit 0. Skipped when the command is empty; then
   verify carries the replay alone, and with neither the replay fails.

The verify scripts are the package's `device/verify/` plus any `verify_files` the caller
holds (e.g. from the fault catalog). A patch is refused before it is applied when it has a
symlink, a submodule, a binary change or a path outside `device/`, or touches (at any depth,
any case, read by the contract's parser so both path forms count):
- `verify/`;
- anything that can shadow the test runner: `pytest.py`, `py.py`, a `pytest/` or `_pytest/`
  dir, `sitecustomize.py`, `usercustomize.py`, `*.pth`, `*.dist-info/`, `*.egg-info/`, or a
  module or package named like a standard-library module or one of pytest's dependencies
  (`hashlib.py`, `json/`, `pluggy/`, ...);
- test-runner config: any `conftest.py`, `pytest.ini`, `.pytest.ini`, `pyproject.toml`,
  `setup.cfg`, `tox.ini` (addopts, deselection, plugins);
- `.gitignore` / `.gitattributes` (they could hide a planted file from git's view).
A symlink or nested `.git` found after applying it refuses it too. Changes to existing tests
are flagged for the gate, read from the parsed patch (never from `git status`).

How the tests run (`cloud/coder/run_agent.py`): no outside `PYTHON*`/`PYTEST*` env, and
`python -m pytest` runs as `python -P -m pytest`, so the work dir isn't on `sys.path` before
pytest loads. (Not `PYTHONSAFEPATH=1` in the env: the device's tests start `python app.py`,
which inherits it and then can't import its own modules.) Printed text never decides.

No database, no events: `code_lockin.gate_replay` runs it as a job off the gate's tick
(M8b) and turns the `CodeReplay` into the gate's replay result and an Event.
"""

from __future__ import annotations

import sys
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
from romulus_cloud.domain.doctor import (
    HARNESS_FILES,
    SHADOW_DIRS,
    SHADOW_FILES,
    code_only,
    patch_changes,
)
from romulus_contracts.codepatch import is_under, summarize_code_patch

SHADOW_NAMES = SHADOW_FILES | {"py.py"}
SHADOW_SUFFIXES = (".pth",)
METADATA_DIRS = (".dist-info", ".egg-info")  # entry points here could autoload a plugin
GIT_FILES = frozenset({".gitignore", ".gitattributes"})
# A module or package with one of these names, anywhere on a sys.path the tests use, would
# load instead of the real one (the stdlib, or what pytest itself imports).
PYTEST_DEPS = {"pytest", "_pytest", "pluggy", "iniconfig", "packaging", "py", "exceptiongroup"}
PYTEST_DEPS |= {"tomli"}
RESERVED_MODULES = frozenset(n.casefold() for n in sys.stdlib_module_names | PYTEST_DEPS)
OK_OUTCOMES = ("passed", "skipped")
INCOMPLETE = "pytest started but didn't finish (no end marker)"


@dataclass(frozen=True)
class TestRun:
    __test__ = False  # not a pytest class

    exit_code: int | None  # None: the sandbox returned no result
    output: str
    timed_out: bool = False
    configured: bool = True  # False: no test command, so nothing ran (and nothing failed)
    # The image's result seal: {test id: "passed" | "skipped" | "failed"} from the pytest the
    # command ran. `results is None` <=> it was not a (sealed) pytest run: then only the exit
    # code speaks. `incomplete`: pytest started but never reached its end marker (killed, or
    # the code under test exited early); that is never a pass.
    results: Mapping[str, str] | None = None
    incomplete: bool = False

    @property
    def passed(self) -> bool:
        """Exit 0, pytest (if it ran) reached its end, and the sealed results agree: some
        tests, none failed."""
        if not self.configured:
            return True
        if self.exit_code != 0 or self.timed_out or self.incomplete:
            return False
        r = self.results
        return r is None or (bool(r) and all(o in OK_OUTCOMES for o in r.values()))

    def shortfall(self, base: TestRun) -> str | None:
        """Why this (patched) run doesn't count as passing against the `base` run, by test id,
        or None. Once the base's run was sealed the command is pytest, so this run must be
        sealed too and report every base test id, each passed (or skipped, if the base
        skipped it too): no deselecting, skipping or exiting the suite away."""
        if not self.configured or base.results is None:
            return None
        if self.incomplete:
            return INCOMPLETE
        if self.results is None:
            return "no sealed pytest result (the base run had one): pytest didn't run"
        missing = sorted(set(base.results) - set(self.results))
        if missing:
            return f"{len(missing)} base tests missing: {', '.join(missing[:5])}"
        bad = sorted(
            t
            for t, o in self.results.items()
            if o != "passed" and not (o == "skipped" and base.results.get(t) == "skipped")
        )
        if bad:
            return f"{len(bad)} tests not passed: {', '.join(bad[:5])}"
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
    results: Mapping[str, str] | None = None  # the seal's {test id: outcome}
    incomplete: bool = False  # pytest started (or should have) but never reached its end

    def all_passed(self, expected: int) -> bool:
        """Exactly `expected` tests ran and passed, none skipped, pytest exited 0 and reached
        its end."""
        return (
            self.ran
            and not self.timed_out
            and not self.incomplete
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
    # The patch's own new or changed tests, on base + the whole patch: recorded, never decides.
    advisory: TestRun | None = None

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

    def failing_ids(self) -> list[str]:
        """Test ids the seal reported as not passing on the patched code (device tests, then
        verify); [] when nothing was sealed."""
        found: list[str] = []
        for run in (self.patched, self.patched_verify):
            results = getattr(run, "results", None) or {}
            found += [t for t, o in results.items() if o == "failed"]
        return found

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
            if run.exit_code == 0 and run.incomplete:
                return f"fail ({INCOMPLETE})"
            if run.exit_code == 0 and not run.passed:
                r = run.results or {}
                failed = sum(o not in OK_OUTCOMES for o in r.values())
                return f"fail (exit 0, but the sealed result has {len(r)} tests, {failed} failed)"
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
        if self.advisory is not None:
            text += f"; advisory (the patch's own tests, not decisive): {tests(self.advisory)}"
        if self.patch_error:
            text += f"; patch refused: {self.patch_error}"
        if self.flags:
            text += "; flagged: " + "; ".join(self.flags)
        if not self.checked_anything:
            text += "; nothing checked the patch: no device test command and no verify scripts"
        return text


def test_run_of(run: SandboxRun) -> TestRun:
    """A sandbox `test` run as the replay judges it (exit code + sealed results)."""
    if run.result is None:
        return TestRun(None, run.proc.stderr.strip()[-2000:], timed_out=run.proc.timed_out)
    tests = run.tests
    return TestRun(
        tests.get("exit_code"),
        tests.get("output", ""),
        timed_out=bool(tests.get("timed_out")) or run.proc.timed_out,
        results=_results(tests.get("results")),
        incomplete=isinstance(tests.get("results"), dict)
        and bool(tests["results"].get("incomplete")),
    )


def _results(raw: object) -> dict[str, str] | None:
    """The seal's {test id: outcome}, or None; anything malformed is one failed test."""
    if raw is None:
        return None
    tests = raw.get("tests") if isinstance(raw, dict) else None
    if not isinstance(tests, dict):
        return {"<malformed seal>": "failed"}
    return {str(k): (v if v in OK_OUTCOMES else "failed") for k, v in tests.items()}


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
        results=_results({"tests": v["results"]}) if "results" in v else None,
        incomplete=bool(v.get("incomplete")),
    )


def harness_tampering(paths: Iterable[str]) -> list[str]:
    """Paths (relative to `device/`, as the contract's parser reads them) that can change
    how the tests are run or judged, each with why. Any depth, any case."""
    found = []
    for path in paths:
        parts = path.casefold().split("/")
        name, dirs = parts[-1], parts[:-1]
        stem = name.removesuffix(".py") if name.endswith(".py") else None
        if (
            name in SHADOW_NAMES
            or name.endswith(SHADOW_SUFFIXES)
            or SHADOW_DIRS & set(dirs)
            or any(d.endswith(METADATA_DIRS) for d in dirs)
        ):
            found.append(f"{path} (it can shadow the test runner)")
        elif stem in RESERVED_MODULES or RESERVED_MODULES & set(dirs):
            found.append(f"{path} (named like a standard-library or pytest module)")
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
    split_tests: bool = True,
) -> CodeReplay:
    """Run verify + `test_cmd` on the package `files` (paths under `device/`), then on
    files + the patch's code hunks, and the patch's own tests as an advisory run.
    `verify_files` paths are relative to `device/` and must be under verify/. `extra_env`
    adds variables to every container run; it can't override the two this function sets.
    `split_tests=False` runs the whole patch as the patched run (the Fleet Doctor's check,
    which splits the patch itself: its repro step must run the patch's own test)."""
    extra = dict(verify_files or {})
    for name in extra:
        if PurePosixPath(name).parts[:1] != (HOLDOUT_DIR,) or ".." in PurePosixPath(name).parts:
            raise CoderError(f"verify file {name!r} must be under {HOLDOUT_DIR}/")
    env = {
        **dict(extra_env or {}),
        "CODER_TEST_CMD": test_cmd,
        "CODER_TEST_TIMEOUT_S": str(timeout_s),
    }

    kw = {"env": env, "timeout_s": timeout_s + 60, "network": "none"}

    def run_both(repo: ScratchRepo) -> tuple[VerifyRun, TestRun]:
        for name, data in extra.items():
            safe_write(repo.work, name, data)
        verify = _verify_run(sandbox.run(repo.work, "verify", **kw))
        if not test_cmd.strip():
            return verify, TestRun(None, "", configured=False)
        return verify, test_run_of(sandbox.run(repo.work, "test", **kw))

    def applied(name: str, diff: str) -> ScratchRepo | str:
        """A scratch copy of `files` with `diff` applied, or why it can't be."""
        repo = ScratchRepo.create(Path(tmp) / name, files)
        error = repo.apply(diff) if diff.strip() else None
        if error:
            return error
        escapes = find_escapes(repo.work)
        return "; ".join(escapes) if escapes else repo

    with tempfile.TemporaryDirectory(prefix="romulus-replay-", dir=scratch_dir) as tmp:
        base_verify, base = run_both(ScratchRepo.create(Path(tmp) / "base", files))

        def refused(why: str, flags: tuple[str, ...] = ()) -> CodeReplay:
            return CodeReplay(base, base_verify, None, None, patch_error=why, flags=flags)

        why = patch_refusal(patch)
        if why:
            return refused(why)
        full = applied("full", patch)  # the whole patch must apply, tests included
        if isinstance(full, str):
            return refused(full)
        # From the parsed patch, not `git status`: nothing in the tree can hide a change.
        flags = harness_flags(patch_changes(patch))
        code = code_only(patch) if split_tests else patch
        if code.strip() == patch.strip():
            patched_verify, patched = run_both(full)
            return CodeReplay(base, base_verify, patched, patched_verify, flags=flags)
        trusted = applied("code", code)  # base tests + the patch's code hunks only
        if isinstance(trusted, str):
            return refused(f"the patch's code hunks alone don't apply: {trusted}")
        patched_verify, patched = run_both(trusted)
        advisory = test_run_of(sandbox.run(full.work, "test", **kw)) if test_cmd.strip() else None
        return CodeReplay(
            base, base_verify, patched, patched_verify, flags=flags, advisory=advisory
        )
