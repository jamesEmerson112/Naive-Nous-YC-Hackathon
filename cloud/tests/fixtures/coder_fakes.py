"""Test helpers for the coder: the toy device package and a runner that never starts Docker.

`FakeDocker` stands in for `docker run <coder image>`: it finds the /work mount in the argv,
lets a test play the agent by editing those files, then runs the test command on the host
with this interpreter (the toy device is our own fixture code) and prints the same
`ROMULUS_RESULT` line as `cloud/coder/run_agent.py`.
"""

import importlib.util
import json
import os
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

from romulus_cloud.adapters.coder import RESULT_MARKER, WORKDIR, ProcResult

FIXTURES = Path(__file__).resolve().parent
TOY = FIXTURES / "toy_device"
TOY_PATCH = (FIXTURES / "toy_device_d1.patch").read_text()
ENTRYPOINT = FIXTURES.parents[1] / "coder" / "run_agent.py"

_spec = importlib.util.spec_from_file_location("coder_run_agent", ENTRYPOINT)
run_agent = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(run_agent)


def toy_files() -> dict[str, bytes]:
    """The toy device as a code package's files (paths under `device/`)."""
    return {
        f"device/{p.relative_to(TOY).as_posix()}": p.read_bytes()
        for p in sorted(TOY.rglob("*"))
        if p.is_file() and "__pycache__" not in p.parts and ".pytest_cache" not in p.parts
    }


def fix_like_a_careful_engineer(work: Path) -> None:
    """What a good agent does on the toy: remove the rule and add a regression test."""
    gripper = work / "control" / "gripper.py"
    text = gripper.read_text()
    rule = (
        "        if dist_mm is not None and dist_mm < PROXIMITY_MM:\n"
        '            return "RELEASE", "open"\n'
    )
    text = text.replace(rule, "")
    gripper.write_text(text)
    (work / "tests" / "test_regression_d1.py").write_text(
        "from control.gripper import step\n\n\n"
        "def test_close_reading_does_not_release():\n"
        '    assert step("HOLDING", 10.0) == ("HOLDING", "close")\n'
    )
    cache = work / "control" / "__pycache__"
    cache.mkdir(exist_ok=True)
    (cache / "gripper.cpython-312.pyc").write_bytes(b"\x00junk")


class FakeDocker:
    def __init__(
        self,
        agent: Callable[[Path], None] | None = None,
        agent_result: dict | None = None,
        timed_out: bool = False,
        extra_output: str = "",
    ) -> None:
        self.agent = agent
        self.agent_result = agent_result or {
            "agent": "mini-swe-agent",
            "version": "2.4.6",
            "model": "test-model",
            "exit_status": "Submitted",
            "error": "",
            "steps": 7,
            "prompt_tokens": 1200,
            "completion_tokens": 300,
            "seconds": 12.5,
        }
        self.timed_out = timed_out
        self.extra_output = extra_output
        self.calls: list[dict] = []
        self.saw_git_in_work = False
        self.saw_verify_in_work = False

    def __call__(self, argv, *, stdin=None, env=None, timeout=None) -> ProcResult:
        self.calls.append({"argv": list(argv), "stdin": stdin, "env": dict(env or {})})
        if argv[1] != "run":
            return ProcResult(0, "", "")
        work = Path(argv[argv.index("-v") + 1].removesuffix(f":{WORKDIR}"))
        mode = argv[-1]
        self.saw_git_in_work = (work / ".git").exists()
        self.saw_verify_in_work = (work / "verify").exists()
        if mode == "agent" and self.agent:
            self.agent(work)
        if self.timed_out:
            return ProcResult(-1, "partial output", "", timed_out=True)
        result: dict = {"mode": mode}
        if mode == "agent":
            result["agent"] = self.agent_result
        if mode == "verify":  # the image's own fixed verify command, on this interpreter
            result["verify"] = run_agent.run_verify(120, work=str(work), python=sys.executable)
        else:
            result["tests"] = self._run_tests(work, (env or {}).get("CODER_TEST_CMD", ""))
        return ProcResult(0, f"banner\n{RESULT_MARKER}{json.dumps(result)}\n", "")

    def _run_tests(self, work: Path, cmd: str) -> dict:
        argv = cmd.split()
        if argv[:1] == ["python"]:
            argv[0] = sys.executable
        env = {k: v for k, v in os.environ.items() if not k.startswith("PYTEST")}
        proc = subprocess.run(
            [*argv, "-p", "no:cacheprovider"], cwd=work, capture_output=True, text=True, env=env
        )
        return {
            "cmd": cmd,
            "exit_code": proc.returncode,
            "output": proc.stdout + proc.stderr + self.extra_output,
            "timed_out": False,
        }

    @property
    def run_call(self) -> dict:
        return next(c for c in self.calls if c["argv"][1] == "run")
