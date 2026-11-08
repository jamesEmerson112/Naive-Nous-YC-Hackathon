"""The coder sandbox's entry point (M8, D21; architecture §2 `coder`).

Reads one JSON request on stdin and writes one JSON result as the last line of stdout;
everything else (git, pytest, the agent's chatter) goes to stderr. Standard library only,
except mini-swe-agent, which is imported only when the agent runs.

- `runner.py draft`: unpack the code version, apply the device's `code_patch`, let the agent
  turn it into a clean, minimal change, run the test command and the verify scripts, and
  return `{diff, test_output, passed, notes, agent, package_b64}`. `agent: ""` means no agent
  drafted it (`CODER_AGENT=none`, or the agent didn't finish), so the change is the device's
  own patch, tested.
- `runner.py test`: unpack and run the test command and the verify scripts:
  `{test_output, passed, notes}`. Code replay (`services/code_replay.py`) uses it.

On an internal failure it returns `{"error": "..."}`.

Request fields: `package_b64` (a `.tar.gz` of `device/`), `patch`, `incident`, `code_rev`,
`agent` (`mini-swe-agent` | `none`), `test_cmd`, `verify_glob`, `max_steps`, `max_tokens`,
`cost_limit`, `timeout_s`. The LLM endpoint comes from the env (`LLM_BASE_URL`, `LLM_MODEL`,
`LLM_API_KEY`); the key is removed from the env first, so the agent's shell commands and
the device tests never see it.

The verify scripts are the fault catalog's hidden checks (`docs/fault-catalog.md`): they are
moved out of the repo while the agent works and put back before the checks run.
"""

from __future__ import annotations

import base64
import contextlib
import io
import json
import os
import shlex
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
from pathlib import Path

CODE_ROOT = "device"
OUTPUT_CAP = 12_000
SKIP = {".git", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache"}
GIT_ENV = {"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}
GIT = ["git", "-c", "user.name=romulus-coder", "-c", "user.email=coder@romulus.invalid"]
DONE = "COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT"

SYSTEM = f"""You are a careful engineer fixing robot-arm device code in a git repository.
Every reply has a short THOUGHT and exactly ONE bash command in this block:

```mswea_bash_command
your_command_here
```

Each command runs in a fresh shell in the repository root. When you are done, run exactly
`printf '{DONE}\\n<one-line summary of the final change>\\n'` on its own."""

INSTANCE = """A robot's on-device agent fixed an incident with the patch below, and it is
already applied to the working tree (`git diff` shows it). {{apply_note}}

Your job: turn it into a clean, minimal change that fixes the incident.
- Keep the behavior fix and any regression test it adds; drop debugging, dead code and
  unrelated edits. If the patch is already clean, leave it as it is.
- Never weaken or delete existing tests. Don't leave scratch files behind.
- Run the tests with `{{test_cmd}}` before you finish; they must pass.
- Read SPEC.md if there is one: it is the intended behavior.

Incident ({{kind}}): {{summary}}
On-device agent's reasoning: {{reasoning}}

Device patch (unified diff against code {{code_rev}}, paths relative to the repo root):
{{patch}}"""

OBSERVATION = (
    "<returncode>{{output.returncode}}</returncode>\n<output>\n"
    "{{ output.output[:6000] }}{% if output.output | length > 6000 %}\n"
    "[... output cut; use head, tail or grep ...]{% endif %}\n</output>"
)


class Fail(Exception):
    """A draft that can't go on, with a plain-English reason (not an internal error)."""


def log(*parts: object) -> None:
    print("[coder]", *parts, file=sys.stderr, flush=True)


def tail(text: str, cap: int = OUTPUT_CAP) -> str:
    return text if len(text) <= cap else f"[... {len(text) - cap} chars cut ...]\n" + text[-cap:]


def git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [*GIT, *args],
        cwd=repo,
        env={**os.environ, **GIT_ENV},
        capture_output=True,
        text=True,
        check=check,
    )


# --- the code tree -----------------------------------------------------------------------


def unpack(package_b64: str, dest: Path) -> tuple[Path, str]:
    """Extract the package; returns the repo root (`device/` inside it, if present) and the
    prefix its files had in the tarball."""
    data = base64.b64decode(package_b64)
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
        tar.extractall(dest, filter="data")
    inner = dest / CODE_ROOT
    return (inner, f"{CODE_ROOT}/") if inner.is_dir() else (dest, "")


def pack(repo: Path, prefix: str) -> str:
    """The repo's files (no git or cache dirs) as a base64 `.tar.gz` under `prefix`."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for path in sorted(repo.rglob("*")):
            rel = path.relative_to(repo)
            if path.is_file() and not SKIP & set(rel.parts) and path.suffix != ".pyc":
                tar.add(path, arcname=prefix + rel.as_posix(), recursive=False)
    return base64.b64encode(buf.getvalue()).decode()


def hide(repo: Path, pattern: str, holding: Path) -> list[str]:
    """Move the verify scripts out of the repo; returns their paths relative to it."""
    found = sorted(p.relative_to(repo).as_posix() for p in repo.glob(pattern) if p.is_file())
    for rel in found:
        (holding / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.move(repo / rel, holding / rel)
    return found


def restore(repo: Path, hidden: list[str], holding: Path) -> None:
    """Put the verify scripts back, ignored by git so they stay out of the diff."""
    for rel in hidden:
        (repo / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(holding / rel, repo / rel)
    exclude = repo / ".git" / "info" / "exclude"
    if exclude.parent.is_dir():
        with exclude.open("a") as f:
            f.write("".join(f"/{rel}\n" for rel in hidden))


# --- checks ------------------------------------------------------------------------------


def run_checks(repo: Path, req: dict, deadline: float) -> tuple[bool, str, str]:
    """Run the test command, then each verify script. Returns (passed, output, note).
    Nothing to run counts as not passed: no tests ran is not a pass."""
    commands: list[tuple[str, list[str]]] = []
    if req.get("test_cmd"):
        commands.append((req["test_cmd"], shlex.split(req["test_cmd"])))
    pattern = req.get("verify_glob") or ""
    verify = sorted(p for p in repo.glob(pattern) if p.is_file()) if pattern else []
    for p in verify:
        rel = p.relative_to(repo).as_posix()
        runner = [sys.executable] if p.suffix == ".py" else ["bash"]
        commands.append((f"verify {rel}", [*runner, rel]))
    if not commands:
        return False, "", "nothing to run: no test command and no verify scripts"
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
    out, ok = [], True
    for label, argv in commands:
        left = max(1.0, deadline - time.monotonic())
        try:
            r = subprocess.run(
                argv, cwd=repo, env=env, capture_output=True, text=True, timeout=left
            )
            code, text = r.returncode, r.stdout + r.stderr
        except subprocess.TimeoutExpired as exc:
            code, text = -1, f"{exc.output or ''}\n[timed out after {left:.0f} s]"
        except OSError as exc:
            code, text = -1, f"[could not run: {exc}]"
        ok = ok and code == 0
        out.append(f"$ {label}\n{text.strip()}\n[exit {code}]")
    what = f"`{req['test_cmd']}`" if req.get("test_cmd") else "no test command"
    note = f"checks {'passed' if ok else 'failed'} ({what} + {len(verify)} verify script(s))"
    return ok, tail("\n\n".join(out)), note


# --- the agent ---------------------------------------------------------------------------


def run_agent(repo: Path, req: dict, key: str, applied: bool, apply_err: str) -> tuple[bool, str]:
    """Let mini-swe-agent clean the change up. Returns (submitted, note)."""
    os.environ.setdefault("MSWEA_SILENT_STARTUP", "1")
    os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")
    os.environ.setdefault("MSWEA_GLOBAL_CONFIG_DIR", tempfile.mkdtemp(prefix="mswea-"))
    from minisweagent import __version__
    from minisweagent.agents.default import DefaultAgent
    from minisweagent.environments.local import LocalEnvironment
    from minisweagent.models.litellm_textbased_model import LitellmTextbasedModel

    model = LitellmTextbasedModel(
        model_name=f"openai/{os.environ.get('LLM_MODEL', '')}",
        model_kwargs={
            "api_base": os.environ.get("LLM_BASE_URL", ""),
            "api_key": key,
            "max_tokens": int(req.get("max_tokens") or 2000),
            "drop_params": True,
        },
        cost_tracking="ignore_errors",
        observation_template=OBSERVATION,
    )
    env = LocalEnvironment(cwd=str(repo), timeout=120, env={"PAGER": "cat", "GIT_PAGER": "cat"})
    agent = DefaultAgent(
        model,
        env,
        system_template=SYSTEM,
        instance_template=INSTANCE,
        step_limit=int(req.get("max_steps") or 15),
        cost_limit=float(req.get("cost_limit") or 0),
        wall_time_limit_seconds=max(30, int(req.get("timeout_s") or 300) - 60),
    )
    incident = req.get("incident") or {}
    apply_note = (
        "It applied cleanly."
        if applied
        else f"It did NOT apply to this code ({apply_err.strip()}); port the fix by hand."
    )
    try:
        info = agent.run(
            "",
            apply_note=apply_note,
            test_cmd=req.get("test_cmd") or "(none)",
            kind=incident.get("kind", ""),
            summary=incident.get("summary", ""),
            reasoning=incident.get("agent_reasoning", ""),
            code_rev=req.get("code_rev", ""),
            patch=req.get("patch", ""),
        )
    except Exception as exc:  # the agent crashed (auth, network); the caller falls back
        return False, f"mini-swe-agent {__version__} crashed: {type(exc).__name__}: {exc}"
    tokens = 0
    for m in agent.messages:
        usage = ((m.get("extra") or {}).get("response") or {}).get("usage") or {}
        tokens += int(usage.get("total_tokens") or 0)
    status = info.get("exit_status", "")
    summary = (info.get("submission") or "").strip().splitlines()
    note = (
        f"mini-swe-agent {__version__} ({os.environ.get('LLM_MODEL', '')}): {status} after "
        f"{agent.n_calls} model call(s), ~{tokens} tokens"
    )
    if summary:
        note += f"; summary: {summary[0][:300]}"
    return status == "Submitted", note


# --- modes -------------------------------------------------------------------------------


def draft(req: dict, key: str, work: Path) -> dict:
    deadline = time.monotonic() + float(req.get("timeout_s") or 300)
    repo, prefix = unpack(req["package_b64"], work / "tree")
    holding = work / "hidden"
    hidden = hide(repo, req.get("verify_glob") or "", holding) if req.get("verify_glob") else []
    git(repo, "init", "-q")
    exclude = repo / ".git" / "info" / "exclude"
    exclude.parent.mkdir(parents=True, exist_ok=True)
    exclude.write_text("__pycache__/\n.pytest_cache/\n*.pyc\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "--allow-empty", "-m", f"code {req.get('code_rev', '')}")
    patch_file = work / "device.patch"
    patch_file.write_text(req.get("patch") or "")
    r = git(repo, "apply", "--whitespace=nowarn", str(patch_file), check=False)
    applied, apply_err = r.returncode == 0, r.stderr
    notes = []

    agent_name = req.get("agent") or "none"
    drafted_by = ""
    if agent_name == "none":
        if not applied:
            raise Fail(
                f"the device's patch does not apply to code {req.get('code_rev')}: "
                f"{apply_err.strip()}"
            )
        notes.append("no coding agent (CODER_AGENT=none): the device's patch, applied as is")
    elif agent_name == "mini-swe-agent":
        submitted, note = run_agent(repo, req, key, applied, apply_err)
        notes.append(note)
        if submitted:
            drafted_by = agent_name
        else:  # an unfinished agent leaves nothing behind
            git(repo, "reset", "-q", "--hard")
            git(repo, "clean", "-qfd")
            if not applied:
                raise Fail(
                    f"the agent did not finish and the device's patch does not apply "
                    f"({apply_err.strip()}); {note}"
                )
            git(repo, "apply", "--whitespace=nowarn", str(patch_file))
            notes.append("the agent did not finish, so the device's patch is used as is")
    else:
        raise Fail(f"unknown coding agent {agent_name!r} (known: mini-swe-agent, none)")

    restore(repo, hidden, holding)
    git(repo, "add", "-A")
    diff = git(repo, "diff", "--cached", "HEAD").stdout
    if not diff.strip():
        raise Fail("the change is empty: nothing differs from code " + str(req.get("code_rev")))
    passed, output, note = run_checks(repo, req, deadline)
    notes.append(note)
    return {
        "diff": diff,
        "test_output": output,
        "passed": passed,
        "notes": "; ".join(notes),
        "agent": drafted_by,
        "package_b64": pack(repo, prefix),
    }


def test(req: dict, work: Path) -> dict:
    deadline = time.monotonic() + float(req.get("timeout_s") or 300)
    repo, _ = unpack(req["package_b64"], work / "tree")
    passed, output, note = run_checks(repo, req, deadline)
    return {"test_output": output, "passed": passed, "notes": note}


def main() -> int:
    mode = sys.argv[1] if len(sys.argv) > 1 else ""
    key = os.environ.pop("LLM_API_KEY", "")
    stdout = sys.stdout
    with contextlib.redirect_stdout(sys.stderr):
        try:
            req = json.loads(sys.stdin.read())
            with tempfile.TemporaryDirectory(prefix="romulus-coder-") as tmp:
                if mode == "draft":
                    result = draft(req, key, Path(tmp))
                elif mode == "test":
                    result = test(req, Path(tmp))
                else:
                    result = {"error": f"unknown mode {mode!r} (draft | test)"}
        except Fail as exc:
            result = {
                "diff": "",
                "test_output": "",
                "passed": False,
                "notes": str(exc),
                "agent": "",
                "package_b64": "",
            }
        except Exception as exc:
            log("internal error:", repr(exc))
            result = {"error": f"{type(exc).__name__}: {exc}"}
    stdout.write(json.dumps(result) + "\n")
    stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
