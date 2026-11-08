"""Test helpers for the coder: the toy device package and a runner that never starts Docker.

`FakeDocker` stands in for `docker run <coder image>`: it finds the /work mount in the argv,
lets a test play the agent by editing those files, then runs the test command on the host
with this interpreter (the toy device is our own fixture code) and prints the same
`ROMULUS_RESULT` line as `cloud/coder/run_agent.py`. It also keeps track of the labelled
containers and networks the egress code creates, so `ps`/`network ls --filter label=...`,
`rm -f` and `network rm` behave like Docker's and a test can see what was left behind.
"""

import importlib.util
import json
import shlex
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
        proxy_logs: str = "llm_proxy ready on :8080\n",
        fail: str | None = None,
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
        self.proxy_logs = proxy_logs  # what `docker logs <egress proxy>` prints
        self.fail = fail  # a docker subcommand (e.g. "network") that fails
        self.calls: list[dict] = []
        self.saw_git_in_work = False
        self.saw_verify_in_work = False
        self.containers: dict[str, dict[str, str]] = {}  # name -> labels (still existing)
        self.networks: dict[str, dict[str, str]] = {}

    def left_behind(self) -> list[str]:
        return sorted(self.containers) + sorted(self.networks)

    def __call__(self, argv, *, stdin=None, env=None, timeout=None) -> ProcResult:
        self.calls.append({"argv": list(argv), "stdin": stdin, "env": dict(env or {})})
        if argv[1] == self.fail:
            return ProcResult(1, "", f"docker {self.fail}: simulated failure")
        if argv[1] == "logs":
            return ProcResult(0, "", self.proxy_logs)
        if argv[1] != "run":
            return self._state(argv[1:])
        work = Path(argv[argv.index("-v") + 1].removesuffix(f":{WORKDIR}"))
        mode = argv[-1]
        self.saw_git_in_work = (work / ".git").exists()
        self.saw_verify_in_work = (work / "verify").exists()
        if mode == "agent" and self.agent:
            self.agent(work)
        if self.timed_out:  # the container outlives the CLI until `docker kill`
            self.containers[argv[argv.index("--name") + 1]] = _labels(argv)
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
        """The image's own `run_tests` (clean env, `python -P -m`, a JUnit report outside the
        work dir), with `python` meaning this interpreter."""
        here = cmd
        if cmd.split()[:1] == ["python"]:
            here = shlex.quote(sys.executable) + cmd[len("python") :]
        result = run_agent.run_tests(here, 120, work=str(work))
        return {**result, "cmd": cmd, "output": result["output"] + self.extra_output}

    def _state(self, args: list[str]) -> ProcResult:
        """Docker's bookkeeping for create/kill/rm/ps and the network commands."""
        if args[:2] == ["network", "create"]:
            self.networks[args[-1]] = _labels(args)
        elif args[0] == "create":
            self.containers[args[args.index("--name") + 1]] = _labels(args)
        elif args[0] == "kill":  # every container here runs with --rm
            self.containers.pop(args[1], None)
        elif args[:2] == ["rm", "-f"]:
            for name in args[2:]:
                self.containers.pop(name, None)
        elif args[:2] == ["network", "rm"]:
            if self.networks.pop(args[2], None) is None:
                return ProcResult(1, "", f"network {args[2]} not found")
        elif args[0] == "ps" or args[:2] == ["network", "ls"]:
            label = args[args.index("--filter") + 1].removeprefix("label=")
            pool = self.containers if args[0] == "ps" else self.networks
            hits = [name for name, labels in pool.items() if _matches(labels, label)]
            if "--format" in args:  # `{{.ID}} {{.Labels}}`, as the egress prune asks for
                rows = (f"{h} {','.join(f'{k}={v}' for k, v in pool[h].items())}" for h in hits)
                return ProcResult(0, "".join(f"{row}\n" for row in rows), "")
            return ProcResult(0, "".join(f"{h}\n" for h in hits), "")
        return ProcResult(0, "", "")

    @property
    def run_call(self) -> dict:
        return next(c for c in self.calls if c["argv"][1] == "run")

    def call(self, *prefix: str) -> dict:
        """The first call whose argv (after `docker`) starts with `prefix`."""
        return next(c for c in self.calls if c["argv"][1 : 1 + len(prefix)] == list(prefix))


def _labels(argv: list[str]) -> dict[str, str]:
    pairs = [argv[i + 1] for i, a in enumerate(argv[:-1]) if a == "--label"]
    return dict(p.split("=", 1) if "=" in p else (p, "") for p in pairs)


def _matches(labels: dict[str, str], label: str) -> bool:
    key, _, value = label.partition("=")
    return key in labels and (not value or labels[key] == value)
