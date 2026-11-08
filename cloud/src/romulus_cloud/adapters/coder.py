"""Coder: turns a device's code patch into a clean, tested change (D21, D22; M8).

Absorbs which coding agent does the work:
- `FakeCoder` (default) passes the device's patch through unchanged and says so, so the
  batch path runs end to end offline.
- `LocalAgent` runs mini-swe-agent in the `coder` sandbox container (`cloud/coder/`) against
  the OpenAI-compatible LLM endpoint (Naïve proxy or RunPod), then runs the device tests in
  the same container and returns the diff, the test output and whether they passed.
- `naive_session` (a Naïve managed session) is a later option, if `local_agent` is weak.

**`CodeDraft.passed` is advisory.** The agent controlled the container its tests ran in, so
the gate must decide on `services.coder_runs.replay_code_patch` (fresh containers, no network,
held-out verify scripts) and never on `passed`.

Safety rules here:
- The sandbox sees only a scratch copy of the device code at /work, without `verify/` (held
  out). Its git metadata lives beside it, outside the mount, so nothing the agent writes can
  change how the host's `git diff` behaves (a planted `.git/config` could run commands).
- A diff with a symlink, a submodule, a binary change or a path outside `device/` fails the
  draft (`summarize_code_patch` problems), and so does any symlink or nested `.git` left in
  the work dir. Host-side writes go through `safe_write`, which never follows a symlink.
- The LLM key is passed to `docker run` by name only, redacted from every field returned,
  and its appearance anywhere in the agent's output or diff fails the draft.

Known gap: the agent container's network is not restricted to the LLM endpoint. That needs
an egress allowlist proxy (ideally one that also injects the key, so the container never
holds it); add it before the coder runs on anything but our own toy or device code, or as
soon as a run is seen reaching another host. Test-only runs use `--network none`.
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import uuid
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Protocol

from romulus_contracts.codepatch import CODE_ROOT, summarize_code_patch

DEFAULT_IMAGE = "romulus-coder"
DEFAULT_AGENT = "mini-swe-agent"
DEFAULT_TEST_CMD = "python -m pytest -q"
RESULT_MARKER = "ROMULUS_RESULT "
WORKDIR = "/work"
HOLDOUT_DIR = "verify"
"""Verify scripts under `device/verify/` never reach the agent; only the replay runs them."""

_GIT_ID = ["-c", "user.name=romulus", "-c", "user.email=romulus@localhost"]
# Host git ignores the user's global and system config (hooks, signing, fsmonitor).
_GIT_ENV = {"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}
# Written by test runs; never part of a change.
_EXCLUDES = "__pycache__/\n*.pyc\n.pytest_cache/\n.ruff_cache/\n.mypy_cache/\n"
_PYTEST_CONFIG = {"conftest.py", "pytest.ini", ".pytest.ini", "pyproject.toml", "setup.cfg"}
_PYTEST_CONFIG |= {"tox.ini"}
_STATUS = {"A": "added", "M": "modified", "D": "deleted", "T": "type changed"}


@dataclass(frozen=True)
class CodeDraft:
    diff: str
    test_output: str
    passed: bool  # advisory only: the gate trusts the replay, never this (see module doc)
    notes: str
    flags: tuple[str, ...] = ()  # e.g. "test_harness: conftest.py (added)", for the gate


class Coder(Protocol):
    def draft(
        self,
        patch: str,
        incident: dict[str, Any],
        code_rev: str,
        files: Mapping[str, bytes] | None = None,
    ) -> CodeDraft:
        """Draft a clean change from `patch` (unified diff against `code_rev`) and test it.

        `files` is the code package of `code_rev` (paths start with `device/`).
        """
        ...


class CoderError(Exception):
    """The coder couldn't run (bad input or config), as opposed to a failing draft."""


class FakeCoder:
    """Returns the device's patch unchanged; runs no tests."""

    def draft(
        self,
        patch: str,
        incident: dict[str, Any],
        code_rev: str,
        files: Mapping[str, bytes] | None = None,
    ) -> CodeDraft:
        return CodeDraft(
            diff=patch,
            test_output="(no tests run: fake coder)",
            passed=True,
            notes=(
                f"pass-through: coding agent not wired. Patch against {code_rev} "
                f"from incident {incident.get('id', 'unknown')} returned unchanged."
            ),
        )


# --- the process seam: unit tests inject a fake runner, so they never start Docker --------


@dataclass(frozen=True)
class ProcResult:
    returncode: int
    stdout: str
    stderr: str
    timed_out: bool = False


class Runner(Protocol):
    def __call__(
        self,
        argv: list[str],
        *,
        stdin: str | None = None,
        env: Mapping[str, str] | None = None,
        timeout: float | None = None,
    ) -> ProcResult: ...


def subprocess_runner(
    argv: list[str],
    *,
    stdin: str | None = None,
    env: Mapping[str, str] | None = None,
    timeout: float | None = None,
) -> ProcResult:
    try:
        proc = subprocess.run(
            argv,
            input=stdin,
            env=dict(env) if env is not None else None,
            capture_output=True,
            text=True,
            errors="replace",
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        return ProcResult(-1, _text(exc.stdout), _text(exc.stderr), timed_out=True)
    return ProcResult(proc.returncode, proc.stdout, proc.stderr)


def _text(out: str | bytes | None) -> str:
    return out.decode(errors="replace") if isinstance(out, bytes) else (out or "")


def redact(text: str, secrets: Iterable[str]) -> str:
    for secret in secrets:
        if secret:
            text = text.replace(secret, "[redacted]")
    return text


# --- host-side file safety ----------------------------------------------------------------


def device_relative(name: str) -> str | None:
    """`device/a/b.py` -> `a/b.py`; None for anything outside `device/` or unsafe."""
    parts = PurePosixPath(name.removeprefix("./")).parts
    if len(parts) < 2 or parts[0] != CODE_ROOT or ".." in parts or "" in parts:
        return None
    return str(PurePosixPath(*parts[1:]))


def safe_write(root: Path, rel: str, data: bytes) -> None:
    """Write `root/rel` without following any symlink, and never outside `root`."""
    parts = PurePosixPath(rel).parts
    if not parts or PurePosixPath(rel).is_absolute() or ".." in parts:
        raise CoderError(f"refusing to write {rel!r}: not a plain relative path")
    path = root
    for part in parts[:-1]:
        path = path / part
        if path.is_symlink():
            raise CoderError(f"refusing to write {rel!r}: {path.name} is a symlink")
        path.mkdir(exist_ok=True)
    path = path / parts[-1]
    if path.is_symlink():
        raise CoderError(f"refusing to write {rel!r}: it is a symlink")
    if not path.resolve().is_relative_to(root.resolve()):
        raise CoderError(f"refusing to write {rel!r}: it resolves outside the scratch dir")
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0))
    with os.fdopen(fd, "wb") as fh:
        fh.write(data)


def find_escapes(work: Path) -> list[str]:
    """Symlinks and nested `.git` entries anywhere under `work` (none may survive a run)."""
    found = []
    for dirpath, dirnames, filenames in os.walk(work, followlinks=False):
        for name in dirnames + filenames:
            path = Path(dirpath) / name
            rel = path.relative_to(work).as_posix()
            if path.is_symlink():
                found.append(f"{rel} is a symlink")
            elif name == ".git":
                found.append(f"{rel} is a nested git repository")
        dirnames[:] = [d for d in dirnames if d != ".git" and not (Path(dirpath) / d).is_symlink()]
    return found


def harness_flags(changes: Iterable[tuple[str, str]]) -> tuple[str, ...]:
    """Flag a change to test config, an existing test, or the held-out verify scripts."""
    flags = []
    for status, path in changes:
        parts = PurePosixPath(path).parts
        name = parts[-1]
        word = _STATUS.get(status[:1], status)
        is_test = "tests" in parts[:-1] or name.startswith("test_") or name.endswith("_test.py")
        if parts[0] == HOLDOUT_DIR:
            flags.append(f"verify_scripts: {path} ({word})")
        elif name in _PYTEST_CONFIG:
            flags.append(f"test_harness: {path} ({word})")
        elif is_test and status[:1] != "A":
            flags.append(f"existing_test: {path} ({word})")
    return tuple(flags)


# --- the scratch repo ---------------------------------------------------------------------


class ScratchRepo:
    """A scratch copy of the device code at `work`, with its git dir beside it (not inside)."""

    def __init__(self, root: Path) -> None:
        self.work = root / "work"
        self.git_dir = root / "repo.git"
        self.base = ""

    @classmethod
    def create(
        cls, root: Path, files: Mapping[str, bytes], exclude: Iterable[str] = ()
    ) -> ScratchRepo:
        """Unpack the `device/` part of a code package (minus `exclude` top-level dirs) and
        commit it as the base."""
        repo = cls(root)
        repo.work.mkdir(parents=True)
        skip = set(exclude)
        written = 0
        for name, data in files.items():
            rel = device_relative(name)
            if rel is None or PurePosixPath(rel).parts[0] in skip:
                continue
            safe_write(repo.work, rel, data)
            written += 1
        if written == 0:
            raise CoderError(f"the code package has no files under {CODE_ROOT}/")
        repo.git("init", "-q")
        (repo.git_dir / "info").mkdir(exist_ok=True)
        (repo.git_dir / "info" / "exclude").write_text(_EXCLUDES)
        repo.git("add", "-A")
        repo.git(*_GIT_ID, "commit", "-q", "--no-verify", "-m", "base")
        repo.base = repo.git("rev-parse", "HEAD").strip()
        return repo

    def git(self, *args: str, stdin: str | None = None) -> str:
        proc = subprocess.run(
            ["git", f"--git-dir={self.git_dir}", f"--work-tree={self.work}", *args],
            input=stdin,
            cwd=self.work,
            env={**os.environ, **_GIT_ENV},
            capture_output=True,
            text=True,
            errors="replace",
        )
        if proc.returncode != 0:
            raise CoderError(f"git {args[0]} failed: {proc.stderr.strip()[:500]}")
        return proc.stdout

    def diff(self) -> str:
        """Everything changed since the base, new files included: a `code_patch`-style diff."""
        self.git("add", "-A")
        return self.git("diff", "--cached", "--no-color", "--no-ext-diff", self.base)

    def changes(self) -> list[tuple[str, str]]:
        """(status letter, path) for each file changed since the base."""
        self.git("add", "-A")
        out = self.git("diff", "--cached", "--name-status", "--no-renames", "-z", self.base)
        fields = out.split("\0")
        return [(fields[i], fields[i + 1]) for i in range(0, len(fields) - 1, 2)]

    def apply(self, patch: str) -> str | None:
        """Apply a unified diff to the work tree. Returns None, or why it doesn't apply."""
        try:
            self.git("apply", "--whitespace=nowarn", "-", stdin=patch)
        except CoderError as exc:
            return str(exc)
        return None


# --- the sandbox container ----------------------------------------------------------------


@dataclass(frozen=True)
class SandboxRun:
    result: dict[str, Any] | None  # the container's ROMULUS_RESULT, or None if it never came
    proc: ProcResult

    @property
    def tests(self) -> dict[str, Any]:
        return (self.result or {}).get("tests") or {}

    @property
    def agent(self) -> dict[str, Any]:
        return (self.result or {}).get("agent") or {}

    @property
    def verify(self) -> dict[str, Any]:
        return (self.result or {}).get("verify") or {}


@dataclass
class Sandbox:
    """Runs the `coder` image on a work dir: `docker run --rm` with /work as its only mount."""

    image: str = DEFAULT_IMAGE
    runner: Runner = subprocess_runner
    docker: str = "docker"
    memory: str = "2g"
    cpus: str = "2"
    pids_limit: int = 512

    def run(
        self,
        work: Path,
        mode: str,
        *,
        env: Mapping[str, str],
        timeout_s: float,
        network: str,
        stdin: str | None = None,
    ) -> SandboxRun:
        name = f"romulus-coder-{uuid.uuid4().hex[:12]}"
        argv = [
            self.docker, "run", "--rm", "-i", "--name", name,
            "--network", network,
            "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
            "--memory", self.memory, "--memory-swap", self.memory, "--cpus", self.cpus,
            "--pids-limit", str(self.pids_limit),
            "-v", f"{work.resolve()}:{WORKDIR}",
        ]  # fmt: skip
        if hasattr(os, "getuid"):  # files the agent writes stay owned by the host user
            argv += ["--user", f"{os.getuid()}:{os.getgid()}"]
        for key in sorted(env):  # names only: values (the API key) never appear in argv
            argv += ["-e", key]
        argv += [self.image, mode]
        proc = self.runner(argv, stdin=stdin, env={**os.environ, **env}, timeout=timeout_s)
        if proc.timed_out:
            self.runner([self.docker, "kill", name], timeout=30)
        return SandboxRun(_parse_result(proc.stdout), proc)


def _parse_result(stdout: str) -> dict[str, Any] | None:
    for line in reversed(stdout.splitlines()):
        if line.startswith(RESULT_MARKER):
            try:
                return json.loads(line[len(RESULT_MARKER) :])
            except json.JSONDecodeError:
                return None
    return None


# --- LocalAgent ---------------------------------------------------------------------------


def build_task_prompt(patch: str, incident: Mapping[str, Any], code_rev: str) -> str:
    """The agent's task: the incident, the device's own patch as a hint, and the rules."""
    facts = {
        k: incident[k]
        for k in ("id", "device_id", "fault_kind", "kind", "summary", "explanation")
        if incident.get(k)
    }
    return (
        f"A robot arm's on-device healer fixed a fault by patching its code (code version "
        f"{code_rev}). The device code is in {WORKDIR} (the current directory); it is not a git "
        "repository, and your changes are collected from the files when you finish.\n\n"
        f"## Incident\n{json.dumps(facts, indent=2, default=str)}\n\n"
        "## The device's own patch (a hint: blunt, written under pressure on the device)\n"
        f"```diff\n{patch.strip()}\n```\n\n"
        "## Your job\n"
        "Write a clean, minimal fix for the same fault, the way a careful engineer would: "
        "read any SPEC.md first, keep the intended behaviour, remove dead code rather than "
        "commenting it out, and add a regression test next to the existing tests that "
        f"fails without your fix. Run the tests (`{DEFAULT_TEST_CMD}`) and make them pass. "
        "Don't weaken or delete existing tests or change the test configuration. Don't create "
        "symlinks. Delete any scratch scripts you made, so only the fix and its test remain."
    )


@dataclass(frozen=True)
class _Inspection:
    """What the host found in the work dir after the agent ran."""

    diff: str
    changes: list[tuple[str, str]]
    escapes: list[str]
    error: str = ""


@dataclass
class LocalAgent:
    """mini-swe-agent in the `coder` sandbox, then the device tests in the same container."""

    base_url: str
    model: str
    api_key: str
    sandbox: Sandbox
    test_cmd: str = DEFAULT_TEST_CMD
    step_limit: int = 30
    time_limit_s: int = 600
    test_timeout_s: int = 300
    max_tokens: int = 4000
    scratch_dir: str | None = None  # where scratch copies go (Docker must be able to mount it)

    def draft(
        self,
        patch: str,
        incident: dict[str, Any],
        code_rev: str,
        files: Mapping[str, bytes] | None = None,
    ) -> CodeDraft:
        if files is None:
            raise CoderError(f"LocalAgent needs the code package of {code_rev} (files=...)")
        env = {
            "LLM_BASE_URL": self.base_url,
            "LLM_MODEL": self.model,
            "LLM_API_KEY": self.api_key,
            "CODER_TEST_CMD": self.test_cmd,
            "CODER_STEP_LIMIT": str(self.step_limit),
            "CODER_TIME_LIMIT_S": str(self.time_limit_s),
            "CODER_TEST_TIMEOUT_S": str(self.test_timeout_s),
            "CODER_MAX_TOKENS": str(self.max_tokens),
        }
        with tempfile.TemporaryDirectory(prefix="romulus-coder-", dir=self.scratch_dir) as tmp:
            repo = ScratchRepo.create(Path(tmp), files, exclude=[HOLDOUT_DIR])
            run = self.sandbox.run(
                repo.work,
                "agent",
                env=env,
                stdin=build_task_prompt(patch, incident, code_rev),
                # the agent's own wall-clock limit, then the tests, then a margin
                timeout_s=self.time_limit_s + self.test_timeout_s + 60,
                network="bridge",
            )
            escapes = find_escapes(repo.work)
            try:
                found = _Inspection(repo.diff(), repo.changes(), escapes)
            except CoderError as exc:  # e.g. a nested repo git can't add
                found = _Inspection("", [], escapes, error=str(exc))
        return self._to_draft(run, found, incident, code_rev)

    def _to_draft(
        self, run: SandboxRun, found: _Inspection, incident: Mapping[str, Any], code_rev: str
    ) -> CodeDraft:
        secrets = [self.api_key]
        agent, tests, diff = run.agent, run.tests, found.diff
        failures: list[str] = []
        notes = [f"local_agent on {code_rev} for incident {incident.get('id', 'unknown')}"]
        notes.append("verify/ held out of the agent's copy (the replay runs it)")
        if run.result is None:
            why = "timed out" if run.proc.timed_out else f"exited {run.proc.returncode}"
            failures.append(
                f"sandbox {why} without a result; stderr tail: {run.proc.stderr.strip()[-400:]!r}"
            )
        if agent:
            notes.append(
                f"{agent.get('agent', DEFAULT_AGENT)} {agent.get('version', '?')}, model "
                f"{agent.get('model') or self.model}: {agent.get('steps', '?')} steps "
                f"(limit {self.step_limit}), exit {agent.get('exit_status') or 'unknown'}, "
                f"tokens {agent.get('prompt_tokens', '?')} in / "
                f"{agent.get('completion_tokens', '?')} out, {agent.get('seconds', '?')} s"
            )
            if agent.get("error"):
                notes.append(f"agent error: {agent['error']}")
            if agent.get("last_reply"):
                notes.append(
                    f"model's last reply (finish_reason "
                    f"{agent.get('last_finish_reason') or '?'}): {agent['last_reply']!r}"
                )
        if tests:
            state = "timed out" if tests.get("timed_out") else f"exit {tests.get('exit_code')}"
            notes.append(f"tests `{tests.get('cmd', self.test_cmd)}`: {state}")
            if tests.get("timed_out") or tests.get("exit_code") != 0:
                failures.append("the device tests did not pass")
        if found.error:
            failures.append(f"could not collect the diff: {found.error}")
        failures += [f"work dir: {e}" for e in found.escapes]
        if diff.strip():
            summary = summarize_code_patch(diff)
            notes.append(f"diff touches {', '.join(summary.paths) or 'nothing readable'}")
            failures += [f"diff problem: {p}" for p in summary.problems]
        elif not found.error:
            failures.append("the agent changed nothing")
        flags = harness_flags(found.changes)
        if flags:
            notes.append("flagged for review: " + "; ".join(flags))
        everything = run.proc.stdout + run.proc.stderr + diff
        if any(s and s in everything for s in secrets):
            failures.append(
                "the LLM key appeared in the agent's output or diff (redacted); rotate it"
            )
        passed = not failures
        test_output = tests.get("output")
        if test_output is None:
            test_output = "(tests did not run: the sandbox returned no result)"
        if failures:
            notes.append("FAILED: " + "; ".join(failures))
        notes.append("advisory only: the gate decides on the replay")
        return CodeDraft(
            diff=redact(diff, secrets),
            test_output=redact(test_output, secrets),
            passed=passed,
            notes=redact("; ".join(notes) + ".", secrets),
            flags=tuple(redact(f, secrets) for f in flags),
        )


def build_coder(
    provider: str,
    agent: str | None = None,
    image: str | None = None,
    *,
    base_url: str = "",
    model: str = "",
    api_key: str = "",
    test_cmd: str = DEFAULT_TEST_CMD,
    step_limit: int = 30,
    time_limit_s: int = 600,
    runner: Runner | None = None,
) -> Coder:
    """Factory called with CODER_PROVIDER, CODER_AGENT, CODER_IMAGE (+ the LLM endpoint)."""
    if provider == "fake":
        return FakeCoder()
    if provider == "local_agent":
        if (agent or DEFAULT_AGENT) != DEFAULT_AGENT:
            raise CoderError(f"CODER_AGENT={agent!r}: only {DEFAULT_AGENT} is built")
        if not (base_url and model):
            raise CoderError("CODER_PROVIDER=local_agent needs LLM_BASE_URL and LLM_MODEL")
        return LocalAgent(
            base_url=base_url,
            model=model,
            api_key=api_key,
            sandbox=Sandbox(image=image or DEFAULT_IMAGE, runner=runner or subprocess_runner),
            test_cmd=test_cmd or DEFAULT_TEST_CMD,
            step_limit=step_limit,
            time_limit_s=time_limit_s,
        )
    if provider == "naive_session":
        raise NotImplementedError(
            "CODER_PROVIDER=naive_session is an M8 stub: Naïve managed sessions are not wired yet"
        )
    raise ValueError(f"unknown CODER_PROVIDER {provider!r}")
