"""Entrypoint of the `coder` sandbox (D21, D22; M8): run the agent on /work, then the tests.

Three modes:
- `agent` (default): read the task from stdin, run mini-swe-agent on /work with a step and
  time limit, then run the device test command.
- `test`: run only the device test command (the gate's code replay; no LLM, no network).
  `python -m` runs get `-P` (`pin_pytest`), so /work can't shadow pytest. Any pytest it
  starts writes a JUnit report to a fresh dir outside /work (via PYTEST_ADDOPTS), and the
  counts come back beside the exit code, so a printed "N passed" or an early `os._exit(0)`
  can't pass for a test run.
- `verify`: run the held-out `verify/` scripts with a fixed command the code under test
  can't configure: isolated Python, no conftest, a clean pytest config, no cache, and the
  pass/fail/skip counts from a JUnit report.

Every mode ends stdout with one line, `ROMULUS_RESULT <json>`. Agent logs go to stderr.

Env: LLM_BASE_URL, LLM_MODEL, LLM_API_KEY (OpenAI-compatible endpoint), CODER_TEST_CMD
(default `python -m pytest -q`), CODER_STEP_LIMIT, CODER_TIME_LIMIT_S, CODER_TEST_TIMEOUT_S,
CODER_MAX_TOKENS, CODER_LLM_TIMEOUT_S.

The key: this process makes itself non-dumpable first (PR_SET_DUMPABLE=0), so the agent's
commands, which run as the same uid, can't read it from /proc/1/environ or /proc/1/mem. It
then pops the LLM_* variables, so no child process inherits them. The host also redacts the
key from everything it gets back and fails the draft if the key shows up at all.
"""

import ctypes
import json
import os
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
from pathlib import Path

WORK = "/work"
VERIFY_DIR = "verify"
MARKER = "ROMULUS_RESULT "
OUTPUT_CAP = 20_000  # characters of test output kept (head and tail)
PR_SET_DUMPABLE = 4
# mini-swe-agent v2 asks for ```mswea_bash_command fences; models trained on v1 (and gpt-oss
# in our first dry run: RepeatedFormatError) write ```bash. Accept the usual shell fences.
ACTION_REGEX = r"```(?:mswea_bash_command|bash|sh|shell)[ \t]*\n(.*?)\n```"


def make_undumpable() -> bool:
    """Deny same-uid processes /proc/<pid>/environ and /proc/<pid>/mem. Linux only."""
    if not sys.platform.startswith("linux"):
        return False
    libc = ctypes.CDLL(None, use_errno=True)
    return libc.prctl(PR_SET_DUMPABLE, 0, 0, 0, 0) == 0


def _cap(text: str) -> str:
    if len(text) <= OUTPUT_CAP:
        return text
    half = OUTPUT_CAP // 2
    return f"{text[:half]}\n... [{len(text) - OUTPUT_CAP} characters cut] ...\n{text[-half:]}"


def _clean_env() -> dict[str, str]:
    """The env every test or verify command gets: no LLM_*, no PYTEST*, and no PYTHON* from
    outside (PYTHONPATH, PYTHONSTARTUP, PYTHONSAFEPATH, ...), only the image's fixed ones.
    PYTHONSAFEPATH is dropped on purpose: children inherit it, and the device's tests start
    `python app.py`, which must find its sibling modules (`pin_pytest` protects pytest)."""
    env = {k: v for k, v in os.environ.items() if not k.startswith(("LLM_", "PYTEST", "PYTHON"))}
    return {**env, **SAFE_PYTHON_ENV}


SAFE_PYTHON_ENV = {
    "PYTHONNOUSERSITE": "1",
    "PYTHONDONTWRITEBYTECODE": "1",
    "PYTHONUNBUFFERED": "1",
}


def pin_pytest(cmd: str) -> str:
    """`python -m pytest` -> `python -P -m pytest` (any `python[3[.x]] -m` in the command):
    `-m` would put the cwd, /work, first on sys.path, so a planted `pytest.py` or `pytest/`
    would run instead of the pinned pytest in site-packages. `-P` applies to that one
    interpreter, not to the processes the tests start. A bare `pytest` (the console script
    in /usr/local/bin) is already safe."""
    import re  # here, not at the top: keeps this module's import block as it is

    return re.sub(r"(?<![^\s;&|(])((?:\S*/)?python3?(?:\.\d+)?)(?=\s+-m\s)", r"\1 -P", cmd)


def junit_counts(report: Path) -> dict | None:
    """tests/failures/errors/skipped summed over a JUnit report's suites; None without one."""
    counts = {"tests": 0, "failures": 0, "errors": 0, "skipped": 0}
    try:
        root = ET.parse(report).getroot()
        for suite in root.iter("testsuite"):
            for key in counts:
                counts[key] += int(suite.get(key, 0))
    except (OSError, ET.ParseError, ValueError):
        return None
    return counts


def _run(argv, *, shell: bool, cwd: str, timeout_s: int, env=None) -> tuple[int, str, bool]:
    """(exit code, capped output, timed out); never raises."""
    try:
        proc = subprocess.run(
            argv,
            shell=shell,
            cwd=cwd,
            env=env if env is not None else _clean_env(),
            capture_output=True,
            text=True,
            errors="replace",
            timeout=timeout_s,
        )
        return proc.returncode, _cap(proc.stdout + proc.stderr), False
    except subprocess.TimeoutExpired as exc:
        out = exc.stdout or ""
        out = out.decode(errors="replace") if isinstance(out, bytes) else out
        return -1, _cap(out), True


def run_tests(cmd: str, timeout_s: int, work: str | None = None) -> dict:
    """Run the device's test command in the work dir. `report` is the JUnit counts of the
    (last) pytest it ran, written outside the work dir, or None if no report appeared (not
    a pytest command, or pytest never got to the end)."""
    work = work or WORK
    with tempfile.TemporaryDirectory(prefix="romulus-tests-") as tmp:
        if Path(tmp).resolve().is_relative_to(Path(work).resolve()):
            raise RuntimeError(f"report dir {tmp} is inside the work dir")
        report = Path(tmp) / f"{os.urandom(8).hex()}.xml"
        env = {**_clean_env(), "PYTEST_ADDOPTS": f"-p no:cacheprovider --junitxml={report}"}
        code, output, timed_out = _run(
            pin_pytest(cmd), shell=True, cwd=work, timeout_s=timeout_s, env=env
        )
        counts = junit_counts(report)
    return {
        "cmd": cmd,
        "exit_code": code,
        "output": output,
        "timed_out": timed_out,
        "report": counts,
    }


def run_verify(timeout_s: int, work: str | None = None, python: str = "python") -> dict:
    """Run `verify/` as its own pytest call that ignores the code's conftest and config."""
    work = work or WORK
    if not (Path(work) / VERIFY_DIR).is_dir():
        return {"ran": False, "tests": 0, "passed": 0, "exit_code": None, "output": ""}
    tmp = Path(tempfile.mkdtemp(prefix="romulus-verify-"))
    ini, report = tmp / "verify.ini", tmp / "verify.xml"
    # -I: no cwd on sys.path (a planted pytest.py can't shadow pytest), no PYTHON* env, no
    # user site. The code dir is added by the clean ini instead, after pytest has loaded.
    ini.write_text(f"[pytest]\npythonpath = {work}\n")
    argv = [
        python, "-I", "-m", "pytest", VERIFY_DIR,
        f"--rootdir={work}", "-c", str(ini), "--noconftest", "-p", "no:cacheprovider",
        "-q", "-rA", f"--junitxml={report}",
    ]  # fmt: skip
    code, output, timed_out = _run(argv, shell=False, cwd=work, timeout_s=timeout_s)
    # No report: zero tests, so it can't count as a pass.
    counts = junit_counts(report) or {"tests": 0, "failures": 0, "errors": 0, "skipped": 0}
    passed = counts["tests"] - counts["failures"] - counts["errors"] - counts["skipped"]
    return {
        "ran": True,
        **counts,
        "passed": max(passed, 0),
        "exit_code": code,
        "timed_out": timed_out,
        "output": output,
    }


def run_agent(task: str) -> dict:
    """Run mini-swe-agent on /work. Returns exit status, steps and token counts."""
    api_key = os.environ.pop("LLM_API_KEY", "")
    base_url = os.environ.pop("LLM_BASE_URL", "")
    model = os.environ.pop("LLM_MODEL", "")
    # mini-swe-agent retries a failed call 10 times with up to 60 s waits; cap it, so the
    # wall-time limit really ends the run.
    os.environ["MSWEA_MODEL_RETRY_STOP_AFTER_ATTEMPT"] = "2"

    import yaml
    from minisweagent import __version__, package_dir
    from minisweagent.agents.default import DefaultAgent
    from minisweagent.environments.local import LocalEnvironment
    from minisweagent.models.litellm_textbased_model import LitellmTextbasedModel

    info = {"agent": "mini-swe-agent", "version": __version__, "model": model}
    if not (base_url and model):
        return {**info, "exit_status": "ConfigError", "error": "LLM_BASE_URL and LLM_MODEL"}

    # The stock text-based config: one fenced command per turn, which works with any chat
    # model behind an OpenAI-compatible proxy (no tool-call support needed).
    config = yaml.safe_load((Path(package_dir) / "config" / "default.yaml").read_text())
    agent_cfg = {
        **config["agent"],
        "step_limit": int(os.environ.get("CODER_STEP_LIMIT", "30")),
        "wall_time_limit_seconds": int(os.environ.get("CODER_TIME_LIMIT_S", "600")),
        "cost_limit": 0,  # the proxy's models aren't in litellm's price list; steps cap spend
        "max_consecutive_format_errors": 5,
    }
    model_obj = LitellmTextbasedModel(
        # "openai/<id>": litellm's generic OpenAI-compatible route; it sends <id> unchanged.
        model_name=f"openai/{model}",
        model_kwargs={
            **config["model"].get("model_kwargs", {}),
            "api_base": base_url,
            "api_key": api_key,
            # Always cap output: the Naïve proxy otherwise reserves the model's full limit.
            "max_tokens": int(os.environ.get("CODER_MAX_TOKENS", "4000")),
            "timeout": int(os.environ.get("CODER_LLM_TIMEOUT_S", "120")),
            "num_retries": 0,  # mini-swe-agent's own retry (capped above) is the only one
        },
        observation_template=config["model"]["observation_template"],
        format_error_template=config["model"]["format_error_template"],
        action_regex=ACTION_REGEX,
        cost_tracking="ignore_errors",
    )
    env = LocalEnvironment(cwd=WORK, timeout=120, env=config.get("environment", {}).get("env", {}))
    agent = DefaultAgent(model_obj, env, **agent_cfg)
    started = time.monotonic()
    error = ""
    try:
        result = agent.run(task)
        exit_status = result.get("exit_status", "")
    except Exception as exc:  # the tests still run, and the host sees what went wrong
        exit_status = type(exc).__name__
        error = str(exc)[:500]
    prompt_tokens = completion_tokens = 0
    last_reply = finish_reason = ""
    for msg in agent.messages:
        extra = msg.get("extra") or {}
        response = extra.get("response") if isinstance(extra.get("response"), dict) else {}
        usage = response.get("usage") or {}
        prompt_tokens += usage.get("prompt_tokens") or 0
        completion_tokens += usage.get("completion_tokens") or 0
        if response.get("choices"):
            finish_reason = str(response["choices"][0].get("finish_reason") or "")
        # A reply that failed to parse is kept only in the format-error message's extra.
        if "model_response" in extra:
            last_reply = str(extra["model_response"] or "")
        elif msg.get("role") == "assistant":
            last_reply = str(msg.get("content") or "")
    failed = exit_status != "Submitted"
    return {
        # Only on failure: what the model last said, to tell a format problem from a bad fix.
        "last_reply": last_reply[-600:] if failed else "",
        "last_finish_reason": finish_reason,
        **info,
        "exit_status": exit_status,
        "error": error,
        "steps": agent.n_calls,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "seconds": round(time.monotonic() - started, 1),
    }


def main() -> int:
    undumpable = make_undumpable()
    mode = sys.argv[1] if len(sys.argv) > 1 else "agent"
    if mode not in ("agent", "test", "verify"):
        print(f"usage: run_agent.py [agent|test|verify], got {mode!r}", file=sys.stderr)
        return 2
    timeout_s = int(os.environ.get("CODER_TEST_TIMEOUT_S", "300"))
    result: dict = {"mode": mode}
    if mode == "agent":
        if not undumpable and sys.platform.startswith("linux"):
            # Fail closed: without it the agent could read the key from /proc/1/environ.
            result["agent"] = {"exit_status": "SandboxError", "error": "prctl failed"}
            os.environ.pop("LLM_API_KEY", None)
        else:
            result["agent"] = run_agent(sys.stdin.read())
    cmd = os.environ.get("CODER_TEST_CMD") or "python -m pytest -q"
    if mode != "agent" and not undumpable and sys.platform.startswith("linux"):
        # Fail closed: the code under test could write a forged result line to our stdout
        # through /proc/1/fd/1. A verify with no report counts as ran-and-failed.
        why = "prctl(PR_SET_DUMPABLE) failed, so the result can't be trusted"
        if mode == "verify":
            result["verify"] = {"ran": True, "tests": 0, "passed": 0, "exit_code": None,
                                "output": why}  # fmt: skip
        else:
            result["tests"] = {"cmd": cmd, "exit_code": None, "output": why, "report": None}
    elif mode == "verify":
        result["verify"] = run_verify(timeout_s)
    else:
        result["tests"] = run_tests(cmd, timeout_s)
    sys.stdout.write("\n" + MARKER + json.dumps(result) + "\n")
    sys.stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
