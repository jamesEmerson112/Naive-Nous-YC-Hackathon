"""Coder: turns a device's code patch into a clean, tested change (D21, D22; M8).

Absorbs which coding agent does the work:
- `FakeCoder` (default) passes the device's patch through unchanged and says so, so the
  batch path runs end to end offline.
- `LocalAgent` runs mini-swe-agent in the `coder` sandbox container (`cloud/coder/`) against
  the OpenAI-compatible LLM endpoint (Naïve proxy or RunPod), then runs the device tests in
  the same container and returns the diff, the test output and whether they passed.
- `naive_session` (a Naïve managed session) is a later option, if `local_agent` is weak.

The sandbox sees only a scratch copy of the device code at /work. Its git metadata lives
next to it, outside the mount, so nothing the agent writes can change how the host's
`git diff` behaves (a planted `.git/config` could otherwise run commands on the host).

Known gap: the container's network is not restricted to the LLM endpoint. That needs an
egress allowlist proxy; add it before the coder runs on anything but our own toy or device
code, or as soon as a run is seen reaching another host. Test-only runs (the gate's code
replay) use `--network none`.
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Protocol

from romulus_contracts.codepatch import CODE_ROOT, summarize_code_patch

DEFAULT_IMAGE = "romulus-coder"
DEFAULT_AGENT = "mini-swe-agent"
DEFAULT_TEST_CMD = "python -m pytest -q"
RESULT_MARKER = "ROMULUS_RESULT "
WORKDIR = "/work"
_GIT_ID = ["-c", "user.name=romulus", "-c", "user.email=romulus@localhost"]
# Host git ignores the user's global and system config (hooks, signing, fsmonitor).
_GIT_ENV = {"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}
# Written by test runs; never part of a change.
_EXCLUDES = "__pycache__/\n*.pyc\n.pytest_cache/\n.ruff_cache/\n.mypy_cache/\n"


@dataclass(frozen=True)
class CodeDraft:
    diff: str
    test_output: str
    passed: bool
    notes: str


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


def redact(text: str, secrets: list[str]) -> str:
    for secret in secrets:
        if secret:
            text = text.replace(secret, "[redacted]")
    return text


# --- the scratch repo ---------------------------------------------------------------------


class ScratchRepo:
    """A scratch copy of the device code at `work`, with its git dir beside it (not inside)."""

    def __init__(self, root: Path) -> None:
        self.work = root / "work"
        self.git_dir = root / "repo.git"
        self.base = ""

    @classmethod
    def create(cls, root: Path, files: Mapping[str, bytes]) -> ScratchRepo:
        """Unpack the `device/` part of a code package and commit it as the base."""
        repo = cls(root)
        repo.work.mkdir(parents=True)
        written = 0
        for name, data in files.items():
            rel = device_relative(name)
            if rel is None:
                continue
            path = repo.work / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
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

    def apply(self, patch: str) -> str | None:
        """Apply a unified diff to the work tree. Returns None, or why it doesn't apply."""
        try:
            self.git("apply", "--whitespace=nowarn", "-", stdin=patch)
        except CoderError as exc:
            return str(exc)
        return None


def device_relative(name: str) -> str | None:
    """`device/a/b.py` -> `a/b.py`; None for anything outside `device/` or unsafe."""
    parts = PurePosixPath(name.removeprefix("./")).parts
    if len(parts) < 2 or parts[0] != CODE_ROOT or ".." in parts:
        return None
    return str(PurePosixPath(*parts[1:]))


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


@dataclass
class Sandbox:
    """Runs the `coder` image on a work dir: `docker run --rm` with /work as its only mount."""

    image: str = DEFAULT_IMAGE
    runner: Runner = subprocess_runner
    docker: str = "docker"
    memory: str = "2g"
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
            "--memory", self.memory, "--pids-limit", str(self.pids_limit),
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
        "Don't weaken or delete existing tests. Delete any scratch scripts you made, so only "
        "the fix and its test remain."
    )


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
            repo = ScratchRepo.create(Path(tmp), files)
            run = self.sandbox.run(
                repo.work,
                "agent",
                env=env,
                stdin=build_task_prompt(patch, incident, code_rev),
                # the agent's own wall-clock limit, then the tests, then a margin
                timeout_s=self.time_limit_s + self.test_timeout_s + 60,
                network="bridge",
            )
            diff = repo.diff()
        return self._to_draft(run, diff, incident, code_rev)

    def _to_draft(
        self, run: SandboxRun, diff: str, incident: Mapping[str, Any], code_rev: str
    ) -> CodeDraft:
        secrets = [self.api_key]
        agent, tests = run.agent, run.tests
        notes = [f"local_agent on {code_rev} for incident {incident.get('id', 'unknown')}"]
        if run.result is None:
            why = "timed out" if run.proc.timed_out else f"exited {run.proc.returncode}"
            notes.append(
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
        if diff.strip():
            summary = summarize_code_patch(diff)
            notes.append(f"diff touches {', '.join(summary.paths) or 'nothing readable'}")
            notes += [f"diff problem: {p}" for p in summary.problems]
        else:
            notes.append("the agent changed nothing")
        passed = (
            run.result is not None
            and not run.proc.timed_out
            and not tests.get("timed_out")
            and tests.get("exit_code") == 0
            and bool(diff.strip())
        )
        test_output = tests.get("output")
        if test_output is None:
            test_output = "(tests did not run: the sandbox returned no result)"
        return CodeDraft(
            diff=diff,
            test_output=redact(test_output, secrets),
            passed=passed,
            notes=redact("; ".join(notes) + ".", secrets),
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
