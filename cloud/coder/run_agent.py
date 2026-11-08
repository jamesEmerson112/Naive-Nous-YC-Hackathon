"""Entrypoint of the `coder` sandbox (D21, D22; M8): run the agent on /work, then the tests.

Three modes:
- `agent` (default): read the task from stdin, run mini-swe-agent on /work with a step and
  time limit, then run the device test command.
- `test`: run only the device test command (the gate's code replay; no LLM, no network).
  `python -m pytest` runs as `python -P <launcher>` (`pin_pytest`), so /work can't shadow
  pytest, with the result seal (`romulus_seal`, from outside /work), which streams each
  test's outcome to a pipe this process owns and ends with a marker, so a printed "N
  passed", an early `os._exit(0)` or an `atexit` forger can't pass for a test run.
- `verify`: run the held-out `verify/` scripts in a pytest process that never imports
  anything from /work (isolated Python, cwd outside it, importlib mode, no conftest, a clean
  config, no cache), with the counts from the seal. The scripts start the code under test
  as a subprocess; this is the replay's decisive check.

Every mode ends stdout with one line, `ROMULUS_RESULT <json>`. Agent logs go to stderr.

Env: LLM_BASE_URL, LLM_MODEL, LLM_API_KEY (OpenAI-compatible endpoint; for agent runs the
worker sets the base URL to the egress proxy, `llm_proxy.py`, and the key to a per-job token,
so the real key never enters this container: B5), CODER_TEST_CMD
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
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.parse import urlsplit

WORK = "/work"
VERIFY_DIR = "verify"
MARKER = "ROMULUS_RESULT "
OUTPUT_CAP = 20_000  # characters of test output kept (head and tail)
PR_SET_DUMPABLE = 4
# mini-swe-agent v2 asks for ```mswea_bash_command fences; models trained on v1 (and gpt-oss
# in our first dry run: RepeatedFormatError) write ```bash. Accept the usual shell fences.
ACTION_REGEX = r"```(?:mswea_bash_command|bash|sh|shell)[ \t]*\n(.*?)\n```"


def wait_for_endpoint(base_url: str, timeout_s: float = 15.0) -> bool:
    """Wait until the LLM endpoint (the egress proxy, started just before us) accepts TCP."""
    parts = urlsplit(base_url)
    if not parts.hostname:
        return False
    port = parts.port or (443 if parts.scheme == "https" else 80)
    deadline = time.monotonic() + timeout_s
    while True:
        try:
            socket.create_connection((parts.hostname, port), timeout=2).close()
            return True
        except OSError:
            if time.monotonic() >= deadline:
                return False
            time.sleep(0.2)


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


def pin_pytest(cmd: str, launcher: str) -> str:
    """`python [flags] -m pytest ...` -> `python [flags] -P <launcher> ...`, wherever it is in
    the command. `-m` would put the cwd, /work, first on sys.path, so a planted `pytest.py`
    would run instead of the pinned pytest in site-packages; `-P` applies to that one
    interpreter, not to the processes the tests start. The launcher (`LAUNCHER_SOURCE`) runs
    pytest with the result seal. Any other way of starting pytest runs unsealed, and the
    replay then has only its exit code (the base's too, so nothing is compared)."""
    import re  # here, not at the top: keeps this module's import block as it is

    pattern = r"(?<![^\s;&|(])((?:\S*/)?python3?(?:\.\d+)?)((?:\s+-[A-Za-z]+)*?)\s+-m\s+pytest"
    pattern += r"(?=$|[\s;&|)])"
    return re.sub(pattern, lambda m: f"{m[1]}{m[2]} -P {launcher}", cmd)


# The result seal: a pytest plugin, loaded from outside /work, that streams each test's
# outcome to a pipe this process owns, then an end marker. Nothing in /work can choose where
# it goes, and lines after the marker (an `atexit` forger) are ignored.
SEAL_MODULE = "romulus_seal"
SEAL_FD_ENV = "ROMULUS_SEAL_FD"
SEAL_SOURCE = """\
# Romulus result seal (written by run_agent.py): each test's outcome to the harness's pipe.
import ctypes
import json
import os
import sys

_raw = os.environ.pop("ROMULUS_SEAL_FD", "")  # gone before any test code can read it
_fd = int(_raw) if _raw.isdigit() else None
if _fd is not None:
    try:
        os.set_inheritable(_fd, False)  # processes the tests start never get it
    except OSError:
        _fd = None
if sys.platform.startswith("linux"):
    try:  # other processes of this uid (the app under test) can't open /proc/<pid>/fd/<n>
        ctypes.CDLL(None).prctl(4, 0, 0, 0, 0)
    except Exception:
        pass


def _send(obj):
    if _fd is not None:
        try:
            os.write(_fd, (json.dumps(obj) + "\\n").encode())
        except OSError:
            pass


_send({"start": True})  # pytest is starting: from here on, no end marker = didn't finish


def pytest_collectreport(report):
    if report.failed:
        _send({"id": "collect:" + (report.nodeid or "."), "outcome": "failed"})


def pytest_runtest_logreport(report):
    if report.when == "call" or report.outcome != "passed":
        _send({"id": report.nodeid, "outcome": report.outcome})


def pytest_sessionfinish(session, exitstatus):
    _send({"end": True, "exitstatus": int(exitstatus)})
"""
# Runs pytest with the seal, never adding anything from /work to sys.path: its own dir only
# while it imports the seal. `python -P` / `python -I` keep the script's dir off sys.path.
LAUNCHER = "romulus_pytest.py"
LAUNCHER_SOURCE = """\
import os
import sys

_here = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _here)
import romulus_seal  # noqa: E402

sys.path.remove(_here)
import pytest  # noqa: E402

sys.exit(pytest.main([*sys.argv[1:], "-p", "no:cacheprovider"], plugins=[romulus_seal]))
"""
_RANK = {"passed": 0, "skipped": 1, "failed": 2}


def parse_seal(lines: list[bytes]) -> dict | None:
    """What the seal said, up to its first end marker (anything after it is ignored):
    - None: no start marker, so this was not a sealed pytest run;
    - {"tests": {node id: worst outcome}, "exitstatus"}: pytest started and finished;
    - the same with "incomplete": True and no exitstatus: pytest started but never reached
      its end (it was killed, or the code under test exited early)."""
    results: dict[str, str] = {}
    started = False
    for raw in lines:
        try:
            item = json.loads(raw)
        except ValueError:
            continue
        if not isinstance(item, dict):
            continue
        if item.get("start") is True:
            started = True
            continue
        if item.get("end") is True:
            return {"tests": results, "exitstatus": item.get("exitstatus")}
        outcome = item.get("outcome") if item.get("outcome") in _RANK else "failed"
        key = str(item.get("id"))
        if _RANK[outcome] >= _RANK[results.get(key, "passed")]:
            results[key] = outcome
    return {"tests": results, "exitstatus": None, "incomplete": True} if started else None


def write_seal(plugin_dir: Path) -> str:
    """Write the seal and its launcher to `plugin_dir` (outside /work); the launcher's path."""
    (plugin_dir / f"{SEAL_MODULE}.py").write_text(SEAL_SOURCE)
    (plugin_dir / LAUNCHER).write_text(LAUNCHER_SOURCE)
    return str(plugin_dir / LAUNCHER)


def _run_sealed(argv, *, shell: bool, cwd: str, timeout_s: int, env: dict):
    """`_run` with the seal's pipe: (exit code, output, timed out, parse_seal result or None).
    Only the pipe's fd number goes in the env (the seal pops it before any test code runs)."""
    import threading  # here, not at the top: keeps this module's import block as it is

    read_fd, write_fd = os.pipe()
    lines: list[bytes] = []

    def read() -> None:
        with os.fdopen(read_fd, "rb") as stream:
            for line in stream:
                lines.append(line)
                if b'"end": true' in line and parse_seal(lines) is not None:
                    return  # anything after the first marker is ignored

    reader = threading.Thread(target=read, daemon=True)
    reader.start()
    env = {**env, SEAL_FD_ENV: str(write_fd)}
    try:
        code, output, timed_out = _run(
            argv, shell=shell, cwd=cwd, timeout_s=timeout_s, env=env, pass_fds=(write_fd,)
        )
    finally:
        os.close(write_fd)
    reader.join(timeout=10)  # a leftover process holding the pipe can't stall us
    return code, output, timed_out, parse_seal(list(lines))


def _run(
    argv, *, shell: bool, cwd: str, timeout_s: int, env=None, pass_fds=()
) -> tuple[int, str, bool]:
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
            pass_fds=pass_fds,
        )
        return proc.returncode, _cap(proc.stdout + proc.stderr), False
    except subprocess.TimeoutExpired as exc:
        out = exc.stdout or ""
        out = out.decode(errors="replace") if isinstance(out, bytes) else out
        return -1, _cap(out), True


def run_tests(cmd: str, timeout_s: int, work: str | None = None) -> dict:
    """Run the device's test command in the work dir. `results` is `parse_seal`'s reading of
    the pytest it ran: None when it ran no sealed pytest, `incomplete` when pytest started
    but never reached its end."""
    work = work or WORK
    with tempfile.TemporaryDirectory(prefix="romulus-seal-") as tmp:
        if Path(tmp).resolve().is_relative_to(Path(work).resolve()):
            raise RuntimeError(f"seal dir {tmp} is inside the work dir")
        launcher = write_seal(Path(tmp))
        code, output, timed_out, results = _run_sealed(
            pin_pytest(cmd, launcher), shell=True, cwd=work, timeout_s=timeout_s,
            env=_clean_env(),
        )  # fmt: skip
    return {
        "cmd": cmd,
        "exit_code": code,
        "output": output,
        "timed_out": timed_out,
        "results": results,
    }


def run_verify(timeout_s: int, work: str | None = None, python: str = "python") -> dict:
    """Run `verify/` in a pytest process that never imports anything from the work dir.

    The work dir is never on its sys.path (`-I`, `--import-mode=importlib`, no `pythonpath`,
    cwd outside it), no conftest or config from it applies, and the counts come from the
    seal. The verify scripts reach the code under test only as data (paths, hashes) and by
    starting it as a subprocess, so nothing in the patch can run inside this process."""
    work = work or WORK
    if not (Path(work) / VERIFY_DIR).is_dir():
        return {"ran": False, "tests": 0, "passed": 0, "exit_code": None, "output": ""}
    with tempfile.TemporaryDirectory(prefix="romulus-verify-") as tmp:
        ini = Path(tmp) / "verify.ini"
        ini.write_text("[pytest]\n")
        # -I: no cwd or script dir on sys.path, no PYTHON* env, no user site.
        argv = [
            python, "-I", write_seal(Path(tmp)), str(Path(work) / VERIFY_DIR),
            f"--rootdir={work}", "-c", str(ini), "--noconftest", "--import-mode=importlib",
            "-q", "-rA",
        ]  # fmt: skip
        code, output, timed_out, sealed = _run_sealed(
            argv, shell=False, cwd=tmp, timeout_s=timeout_s, env=_clean_env()
        )
    results = (sealed or {"tests": {}})["tests"]  # no marker: zero tests
    outcomes = list(results.values())
    return {
        "ran": True,
        "results": results,
        "incomplete": sealed is None or bool(sealed.get("incomplete")),
        "tests": len(outcomes),
        "passed": outcomes.count("passed"),
        "failures": outcomes.count("failed"),
        "errors": 0,
        "skipped": outcomes.count("skipped"),
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
    wait_for_endpoint(base_url)  # if it never comes up, the first call fails and says so

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
            result["tests"] = {"cmd": cmd, "exit_code": None, "output": why, "results": None}
    elif mode == "verify":
        result["verify"] = run_verify(timeout_s)
    else:
        result["tests"] = run_tests(cmd, timeout_s)
    sys.stdout.write("\n" + MARKER + json.dumps(result) + "\n")
    sys.stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
