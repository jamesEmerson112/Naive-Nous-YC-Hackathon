"""B1's proof: run a code package's own tests in the `coder` image with no network.

Blocker B1 clears when the device code "is in the repo with a test suite the agent can run
... and they pass in a clean container". This runs exactly what the gate's code replay runs
for the device tests (`Sandbox.run(..., "test", network="none")` on a scratch copy of the
package), so a pass here means the replay's base run passes too.
"""

import tempfile
from collections.abc import Mapping
from pathlib import Path

from romulus_cloud.adapters.coder import DEFAULT_TEST_CMD, Sandbox, SandboxRun, ScratchRepo
from romulus_cloud.services.coder_runs import test_run_of


def run_package_tests(
    files: Mapping[str, bytes],
    *,
    sandbox: Sandbox,
    test_cmd: str = DEFAULT_TEST_CMD,
    timeout_s: int = 300,
    scratch_dir: str | None = None,
) -> SandboxRun:
    """The package's test command in a fresh, offline `coder` container."""
    env = {"CODER_TEST_CMD": test_cmd, "CODER_TEST_TIMEOUT_S": str(timeout_s)}
    with tempfile.TemporaryDirectory(prefix="romulus-devcheck-", dir=scratch_dir) as tmp:
        repo = ScratchRepo.create(Path(tmp), files)
        return sandbox.run(repo.work, "test", env=env, timeout_s=timeout_s + 60, network="none")


def describe(run: SandboxRun) -> tuple[bool, str]:
    """(passed, a report for people): the command, its exit code and its output."""
    tests = run.tests
    if run.result is None:
        why = "timed out" if run.proc.timed_out else f"exited {run.proc.returncode}"
        return False, f"the coder container {why} without a result:\n{run.proc.stderr[-2000:]}"
    passed = test_run_of(run).passed  # the replay's verdict: exit 0 and the sealed results
    state = "timed out" if tests.get("timed_out") else f"exit {tests.get('exit_code')}"
    report = f"$ {tests.get('cmd')}  ({state}, --network none)\n{tests.get('output', '')}"
    return passed, report.rstrip() + "\n"
