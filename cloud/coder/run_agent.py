"""Entrypoint of the `coder` sandbox (D21, D22; M8): run the agent on /work, then the tests.

Two modes:
- `agent` (default): read the task from stdin, run mini-swe-agent on /work with a step and
  time limit, then run the test command.
- `test`: run only the test command (the gate's code replay; no LLM, no network needed).

Either way the last stdout line is `ROMULUS_RESULT <json>` with the agent's exit status,
steps and token counts and the test command's exit code and output. Everything else
(agent logs) goes to stderr.

Env: LLM_BASE_URL, LLM_MODEL, LLM_API_KEY (OpenAI-compatible endpoint), CODER_TEST_CMD
(default `python -m pytest -q`), CODER_STEP_LIMIT, CODER_TIME_LIMIT_S, CODER_TEST_TIMEOUT_S,
CODER_MAX_TOKENS. The key is removed from the environment before the agent's shell starts,
so the agent's commands (and the model, through their output) never see it.
"""

import json
import os
import subprocess
import sys
import time
from pathlib import Path

WORK = "/work"
MARKER = "ROMULUS_RESULT "
OUTPUT_CAP = 20_000  # characters of test output kept (head and tail)
# mini-swe-agent v2 asks for ```mswea_bash_command fences; models trained on v1 (and gpt-oss
# in our first dry run: RepeatedFormatError) write ```bash. Accept the usual shell fences.
ACTION_REGEX = r"```(?:mswea_bash_command|bash|sh|shell)[ \t]*\n(.*?)\n```"


def _cap(text: str) -> str:
    if len(text) <= OUTPUT_CAP:
        return text
    half = OUTPUT_CAP // 2
    return f"{text[:half]}\n... [{len(text) - OUTPUT_CAP} characters cut] ...\n{text[-half:]}"


def run_tests(cmd: str, timeout_s: int) -> dict:
    """Run the device's test command in /work; never raises."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("LLM_")}
    try:
        proc = subprocess.run(
            cmd,
            shell=True,
            cwd=WORK,
            env=env,
            capture_output=True,
            text=True,
            errors="replace",
            timeout=timeout_s,
        )
        return {
            "cmd": cmd,
            "exit_code": proc.returncode,
            "output": _cap(proc.stdout + proc.stderr),
            "timed_out": False,
        }
    except subprocess.TimeoutExpired as exc:
        out = exc.stdout or ""
        out = out.decode(errors="replace") if isinstance(out, bytes) else out
        return {"cmd": cmd, "exit_code": -1, "output": _cap(out), "timed_out": True}


def run_agent(task: str) -> dict:
    """Run mini-swe-agent on /work. Returns exit status, steps and token counts."""
    import yaml
    from minisweagent import __version__, package_dir
    from minisweagent.agents.default import DefaultAgent
    from minisweagent.environments.local import LocalEnvironment
    from minisweagent.models.litellm_textbased_model import LitellmTextbasedModel

    api_key = os.environ.pop("LLM_API_KEY", "")
    base_url = os.environ.pop("LLM_BASE_URL", "")
    model = os.environ.pop("LLM_MODEL", "")
    info = {"agent": "mini-swe-agent", "version": __version__, "model": model}
    if not (base_url and model):
        return {**info, "exit_status": "ConfigError", "error": "LLM_BASE_URL and LLM_MODEL"}

    # The stock text-based config: one ```mswea_bash_command block per turn, which works
    # with any chat model behind an OpenAI-compatible proxy (no tool-call support needed).
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
    mode = sys.argv[1] if len(sys.argv) > 1 else "agent"
    if mode not in ("agent", "test"):
        print(f"usage: run_agent.py [agent|test], got {mode!r}", file=sys.stderr)
        return 2
    result: dict = {"mode": mode}
    if mode == "agent":
        result["agent"] = run_agent(sys.stdin.read())
    result["tests"] = run_tests(
        os.environ.get("CODER_TEST_CMD") or "python -m pytest -q",
        int(os.environ.get("CODER_TEST_TIMEOUT_S", "300")),
    )
    sys.stdout.write("\n" + MARKER + json.dumps(result) + "\n")
    sys.stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
