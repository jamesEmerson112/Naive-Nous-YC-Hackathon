"""Coder: turns a device's code patch into a clean, tested change (D21, D22; M8).

Absorbs which coding agent does the work (`CODER_PROVIDER`):
- `fake` (default): FakeCoder passes the device's patch through unchanged, runs nothing and
  says so, so the batch path runs end to end offline.
- `local_agent`: LocalAgent runs `cloud/coder/runner.py` in the `coder` sandbox (one
  `docker run` per call, or `CODER_COMMAND` instead, as tests do). The sandbox applies the
  patch, lets an open-source agent (mini-swe-agent, `CODER_AGENT`) clean it up against the
  LLM endpoint, runs the device tests and verify scripts, and returns the new code tree.
  `test(package)` runs only the checks, offline (code replay).
- `naive_session`: a Naïve managed session, later (D22).
"""

from __future__ import annotations

import base64
import io
import json
import logging
import os
import shlex
import subprocess
import tarfile
import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from romulus_cloud.settings import Settings

log = logging.getLogger(__name__)

DEFAULT_IMAGE = "romulus-coder:0.1"
DEFAULT_AGENT = "mini-swe-agent"
GRACE_S = 30  # the runner stops itself at timeout_s; the subprocess gets this much longer


class CoderError(Exception):
    """The coder could not produce a result (timeout, sandbox failure, malformed output)."""


@dataclass(frozen=True)
class CodeDraft:
    """What a coder made of one patch.

    - `diff`: the clean change, a unified diff relative to `device/`.
    - `test_output`, `passed`: the device tests and verify scripts on the changed code.
    - `files`: the whole changed tree (tarball paths), or None when the coder built no tree
      (FakeCoder: the batch keeps the base code and stores the patch beside it).
    - `agent`: the agent that drafted it; "" means the device's own patch, unchanged.
    """

    diff: str
    test_output: str
    passed: bool
    notes: str
    files: dict[str, bytes] | None = None
    agent: str = ""


@dataclass(frozen=True)
class CheckRun:
    passed: bool
    output: str
    notes: str


class Coder(Protocol):
    def draft(
        self, patch: str, incident: dict[str, Any], code_rev: str, package: bytes | None = None
    ) -> CodeDraft:
        """Draft a clean change from `patch` (unified diff against `code_rev`, whose
        `.tar.gz` is `package`) and test it."""
        ...


class FakeCoder:
    """Returns the device's patch unchanged; runs no tests."""

    def draft(
        self, patch: str, incident: dict[str, Any], code_rev: str, package: bytes | None = None
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


class LocalAgent:
    """Runs the coder sandbox once per call, with a timeout and step and token caps.

    The LLM key reaches the sandbox only through the environment (`docker run -e
    LLM_API_KEY` names the variable; the value never appears in argv), only in draft mode,
    and is scrubbed from any error text.
    """

    def __init__(
        self,
        *,
        image: str = DEFAULT_IMAGE,
        agent: str = DEFAULT_AGENT,
        llm_base_url: str = "",
        llm_model: str = "",
        llm_api_key: str = "",
        test_cmd: str = "",
        verify_glob: str = "",
        command: list[str] | None = None,
        network: str = "bridge",
        timeout_s: int = 300,
        max_steps: int = 15,
        max_tokens: int = 2000,
        cost_limit_usd: float = 1.0,
    ) -> None:
        self.image, self.agent, self.command, self.network = image, agent, command, network
        self.llm_base_url, self.llm_model, self._key = llm_base_url, llm_model, llm_api_key
        self.test_cmd, self.verify_glob = test_cmd, verify_glob
        self.timeout_s, self.max_steps, self.max_tokens = timeout_s, max_steps, max_tokens
        self.cost_limit_usd = cost_limit_usd

    @property
    def runs_checks(self) -> bool:
        return bool(self.test_cmd or self.verify_glob)

    def draft(
        self, patch: str, incident: dict[str, Any], code_rev: str, package: bytes | None = None
    ) -> CodeDraft:
        if not package:
            raise CoderError(f"no code package for {code_rev}; the coder needs the base code")
        out = self._run(
            "draft",
            {
                **self._request(package),
                "patch": patch,
                "incident": incident,
                "code_rev": code_rev,
                "agent": self.agent,
                "max_steps": self.max_steps,
                "max_tokens": self.max_tokens,
                "cost_limit": self.cost_limit_usd,
            },
        )
        _expect(out, {"diff": str, "test_output": str, "passed": bool, "notes": str})
        _expect(out, {"agent": str, "package_b64": str})
        files = _untar(out["package_b64"]) if out["package_b64"] else None
        if out["passed"] and not files:
            raise CoderError("coder returned malformed output: it passed but sent no code tree")
        return CodeDraft(
            diff=out["diff"],
            test_output=self._scrub(out["test_output"]),
            passed=out["passed"],
            notes=self._scrub(out["notes"]),
            files=files,
            agent=out["agent"],
        )

    def test(self, package: bytes) -> CheckRun:
        """Run the device tests and verify scripts on `package`, offline."""
        out = self._run("test", self._request(package))
        _expect(out, {"test_output": str, "passed": bool, "notes": str})
        return CheckRun(out["passed"], self._scrub(out["test_output"]), self._scrub(out["notes"]))

    # --- running the sandbox -------------------------------------------------------------

    def _request(self, package: bytes) -> dict[str, Any]:
        return {
            "package_b64": base64.b64encode(package).decode(),
            "test_cmd": self.test_cmd,
            "verify_glob": self.verify_glob,
            "timeout_s": self.timeout_s,
        }

    def _argv(self, mode: str, name: str) -> list[str]:
        if self.command:
            return [*self.command, mode]
        argv = ["docker", "run", "--rm", "-i", "--name", name]
        argv += ["--network", self.network if mode == "draft" else "none"]
        argv += ["--memory", "2g", "--cpus", "2", "--pids-limit", "256"]
        argv += ["--cap-drop", "ALL", "--security-opt", "no-new-privileges"]
        if mode == "draft":
            argv += ["-e", "LLM_API_KEY", "-e", "LLM_BASE_URL", "-e", "LLM_MODEL"]
        return [*argv, self.image, mode]

    def _env(self, mode: str) -> dict[str, str]:
        env = {k: v for k, v in os.environ.items() if not k.startswith("LLM_")}
        if mode == "draft":
            env |= {
                "LLM_API_KEY": self._key,
                "LLM_BASE_URL": self.llm_base_url,
                "LLM_MODEL": self.llm_model,
            }
        return env

    def _run(self, mode: str, request: dict[str, Any]) -> dict[str, Any]:
        name = f"romulus-coder-{uuid.uuid4().hex[:12]}"
        argv = self._argv(mode, name)
        try:
            proc = subprocess.run(
                argv,
                input=json.dumps(request, default=str).encode(),
                capture_output=True,
                env=self._env(mode),
                timeout=self.timeout_s + GRACE_S,
            )
        except subprocess.TimeoutExpired as exc:
            if not self.command:  # the docker CLI died; make sure the container does too
                subprocess.run(["docker", "rm", "-f", name], capture_output=True, check=False)
            raise CoderError(f"coder timed out after {self.timeout_s + GRACE_S} s") from exc
        except OSError as exc:
            raise CoderError(f"could not start the coder sandbox ({argv[0]}): {exc}") from exc
        stderr = self._scrub(proc.stderr.decode(errors="replace"))
        lines = [ln for ln in proc.stdout.decode(errors="replace").splitlines() if ln.strip()]
        try:
            out = json.loads(lines[-1])
        except (IndexError, json.JSONDecodeError):
            out = None
        if not isinstance(out, dict):
            raise CoderError(
                f"coder returned malformed output (exit {proc.returncode}); stderr tail: "
                f"{stderr[-500:].strip() or '(empty)'}"
            )
        if "error" in out:
            raise CoderError(f"coder sandbox failed: {self._scrub(str(out['error']))}")
        log.info("coder %s finished", mode, extra={"exit": proc.returncode})
        return out

    def _scrub(self, text: str) -> str:
        return text.replace(self._key, "***") if self._key else text


def _expect(out: dict[str, Any], fields: dict[str, type]) -> None:
    for key, kind in fields.items():
        if not isinstance(out.get(key), kind):
            raise CoderError(f"coder returned malformed output: {key!r} is not a {kind.__name__}")


def _untar(package_b64: str) -> dict[str, bytes]:
    try:
        data = base64.b64decode(package_b64, validate=True)
        files: dict[str, bytes] = {}
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
            for m in tar.getmembers():
                if m.isfile():
                    files[m.name.removeprefix("./")] = tar.extractfile(m).read()  # type: ignore[union-attr]
        return files
    except (ValueError, tarfile.TarError, OSError, EOFError) as exc:
        msg = f"coder returned malformed output: the code tree is unreadable ({exc})"
        raise CoderError(msg) from exc


def build_coder(settings: Settings) -> Coder:
    """Factory: CODER_PROVIDER picks the coder; the LLM settings are the batch's (D22)."""
    provider = settings.coder_provider
    if provider == "fake":
        return FakeCoder()
    if provider == "local_agent":
        return LocalAgent(
            image=settings.coder_image or DEFAULT_IMAGE,
            agent=settings.coder_agent or DEFAULT_AGENT,
            llm_base_url=settings.llm_base_url,
            llm_model=settings.llm_model,
            llm_api_key=settings.llm_api_key.get_secret_value(),
            test_cmd=settings.coder_test_cmd,
            verify_glob=settings.coder_verify_glob,
            command=shlex.split(settings.coder_command) or None,
            network=settings.coder_network,
            timeout_s=settings.coder_timeout_s,
            max_steps=settings.coder_max_steps,
            max_tokens=settings.coder_max_tokens,
            cost_limit_usd=settings.coder_cost_limit_usd,
        )
    if provider == "naive_session":
        raise NotImplementedError(
            "CODER_PROVIDER=naive_session is an M8 stub: Naïve managed sessions are not wired yet"
        )
    raise ValueError(f"unknown CODER_PROVIDER {provider!r}")
