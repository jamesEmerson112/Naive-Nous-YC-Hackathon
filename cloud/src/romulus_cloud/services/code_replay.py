"""Replay for code candidates (M8, D21; flows F11 step 4).

`replay(session, candidate, settings)` runs the device's test command and the fault
catalog's verify scripts against the candidate's code version, offline, in the coder sandbox
(`LocalAgent.test`). Without a sandbox (`CODER_PROVIDER` is not `local_agent`) or with
nothing to run (no `CODER_TEST_CMD`, no `CODER_VERIFY_GLOB`) it stays the labeled stub, and
the gate keeps the candidate from going further unless the setting allows (B7).

A sandbox that can't run at all raises `CoderError`: the gate then stops the candidate as
`error` with the reason, which is not the same as failing tests (`rolled_back`).
"""

from sqlalchemy.orm import Session

from romulus_cloud.adapters.blobstore import build_blobstore
from romulus_cloud.adapters.coder import LocalAgent, build_coder
from romulus_cloud.domain import gate as g
from romulus_cloud.models import Candidate, CodeVersion
from romulus_cloud.services import registry
from romulus_cloud.settings import Settings

OUTPUT_TAIL = 800


def replay(session: Session, c: Candidate, settings: Settings) -> g.ReplayResult:
    coder = build_coder(settings)
    if not isinstance(coder, LocalAgent):
        return g.replay_code_stub()
    if not coder.runs_checks:
        return g.replay_stub("no device test command or verify scripts are configured")
    code = session.get(CodeVersion, (c.fleet_id, c.code_rev or ""))
    if code is None:
        return g.ReplayResult(False, f"replay failed: code {c.code_rev} does not exist")
    store = build_blobstore(settings.blob_dir)
    with registry.open_package(store, code.sha256, f"code {code.rev}") as f:
        run = coder.test(f.read())
    out = run.output if len(run.output) <= OUTPUT_TAIL else "…" + run.output[-OUTPUT_TAIL:]
    verdict = "passed" if run.passed else "failed"
    return g.ReplayResult(
        run.passed,
        f"replay: the device tests {verdict} on code {code.rev} in the sandbox ({run.notes}). "
        f"Output:\n{out}",
    )
