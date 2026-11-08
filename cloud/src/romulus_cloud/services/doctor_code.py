"""The Fleet Doctor's code side (PR 7): the base package, the coder's draft, the sandbox check
and the coder budget it shares with the batch.

- **Package:** a consult's base is its incident's `running.code_rev`. `package` reads that
  code version's `.tar.gz` (`code_lockin.read_package`) and keys it relative to CODE_ROOT
  with **`verify/` removed** (`domain.doctor.package_rel`), so the held-out verify scripts
  never reach the case file, the coder or the check (architecture §6 item 7). `coder_files`
  puts the `device/` prefix back for the coder and the replay.
- **Draft job** (`draft_job`): `coder.draft(patch="", ..., task=...)`, plain data in, a
  `CodeDraft` out.
- **Check job** (`check_job`): two `coder_runs.replay_code_patch` runs in fresh containers
  with no network: the repro test's part of the patch alone (`domain.doctor.split_patch`),
  with the test command narrowed to that file, must FAIL; then the full patch with
  CODER_TEST_CMD must PASS (skipped when the first already failed the check). The package's
  verify scripts are not in `files`, so neither run sees them; the one pass/fail verify run
  at the end that architecture §6 item 7 allows is skipped in v0.
- **Coder budget:** CODER_MAX_DRAFTS_PER_HOUR counts `coder.started` events fleet-wide in
  the last hour. The batch counts them in `batch._drafts_this_hour`; `coder_budget` runs the
  same query (copied rather than imported: `batch.py` is private to another PR right now),
  and the Doctor records its own `coder.started` (`data.source = "doctor"`) for every draft
  it submits, so one budget covers both.

Job functions never touch the database. Nothing here writes or commits.
"""

import shlex
import tarfile
from collections.abc import Iterable, Mapping
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from romulus_cloud.adapters.blobstore import BlobStore
from romulus_cloud.adapters.coder import (
    CodeDraft,
    Coder,
    CoderError,
    Sandbox,
    build_coder,
    redact,
)
from romulus_cloud.domain import doctor as rules
from romulus_cloud.errors import ApiError
from romulus_cloud.models import CodeVersion, Event
from romulus_cloud.services.code_lockin import failing_tests, read_package, test_summary
from romulus_cloud.services.coder_runs import CodeReplay, replay_code_patch
from romulus_cloud.settings import Settings
from romulus_contracts.codepatch import CODE_ROOT

CODER_STARTED = "coder.started"  # the batch's event; its count is the hourly budget
OUTPUT_TAIL = 2000  # characters of each replay's test output kept in the event


class NoPackage(Exception):
    """The consult's base code can't be read, so there is nothing to draft or check against."""


def package(session: Session, store: BlobStore, rev: str | None, fleet_id: str) -> dict[str, bytes]:
    """Code version `rev`'s files keyed relative to CODE_ROOT, without `verify/`."""
    if not rev:
        raise NoPackage("the incident has no running.code_rev")
    cv = session.get(CodeVersion, (fleet_id, rev))
    if cv is None:
        raise NoPackage(f"code {rev} is not registered (POST /v1/code)")
    try:
        files = read_package(store, cv)
    except (ApiError, tarfile.TarError, OSError, EOFError) as exc:
        detail = exc.detail if isinstance(exc, ApiError) else str(exc)
        raise NoPackage(f"code {rev}'s package can't be read: {detail}") from exc
    rel = rules.package_rel(files)
    if not rel:
        raise NoPackage(f"code {rev}'s package has no files outside {rules.HOLDOUT_DIR}/")
    return rel


def coder_files(rel: Mapping[str, bytes]) -> dict[str, bytes]:
    """`package` files as the coder and the replay take them (paths under `device/`)."""
    return {f"{CODE_ROOT}/{path}": data for path, data in rel.items()}


class Unavailable:
    """The coder settings can't build a coder (e.g. no LLM_BASE_URL): every draft fails with
    that reason, so the consult's draft job fails (and gives up after its tries) instead of
    the tick crashing."""

    def __init__(self, why: str) -> None:
        self.why = why

    def draft(self, patch, incident, code_rev, files=None, task=None) -> CodeDraft:
        raise CoderError(self.why)


def build_doctor_coder(settings: Settings) -> Coder:
    """The coder CODER_PROVIDER names, built the way the batch builds its own."""
    try:
        return build_coder(
            settings.coder_provider,
            settings.coder_agent,
            settings.coder_image,
            base_url=settings.llm_base_url,
            model=settings.llm_model,
            api_key=settings.llm_api_key.get_secret_value(),
            test_cmd=settings.coder_test_cmd,
            step_limit=settings.coder_step_limit,
            time_limit_s=settings.coder_time_limit_s,
            scratch_dir=settings.coder_scratch_dir or None,
        )
    except (CoderError, NotImplementedError, ValueError) as exc:
        return Unavailable(f"CODER_PROVIDER={settings.coder_provider}: {exc}")


def coder_budget(
    session: Session, fleet_id: str, now: datetime, budget: int
) -> tuple[int, datetime | None]:
    """(coder drafts started in the last hour, fleet-wide, batch and Doctor alike; when the
    budget next has room, or None if it has room now).

    Same window as the batch: `coder.started` events with `ts > now - 1h`. Over budget, room
    comes back when enough of the oldest starts leave the window; with a budget of 0 it never
    does, so the answer is an hour from now (check again then).
    """
    stamps = list(
        session.scalars(
            select(Event.ts)
            .where(
                Event.fleet_id == fleet_id,
                Event.kind == CODER_STARTED,
                Event.ts > now - timedelta(hours=1),
            )
            .order_by(Event.ts)
        )
    )
    started = len(stamps)
    if started < budget:
        return started, None
    if budget <= 0:
        return started, now + timedelta(hours=1)
    return started, stamps[started - budget] + timedelta(hours=1)


# --- jobs (plain data in and out; never the database) -------------------------------------


def draft_job(
    coder: Coder,
    files: Mapping[str, bytes],
    incident: dict[str, Any],
    base_rev: str,
    task: str,
) -> CodeDraft:
    """Off the tick: the coder writes a repro test and a fix from scratch (`task`)."""
    return coder.draft("", incident, base_rev, files, task=task)


def repro_command(test_cmd: str, repro_test: str) -> str:
    """The test command narrowed to the repro test, when it is a pytest command (the file is
    shell-quoted; the image runs the command through a shell in CODE_ROOT). Otherwise the
    whole command."""
    if "pytest" not in test_cmd:
        return test_cmd
    return f"{test_cmd} {shlex.quote(rules.code_rel(repro_test))}"


def _run(replay: CodeReplay, secrets: Iterable[str]) -> dict[str, Any]:
    """The plain-data view of one replay that `domain.doctor.check_verdict` reads."""
    patched = replay.patched
    output = redact(patched.output if patched else "", secrets)
    return {
        "patch_error": redact(replay.patch_error, secrets) if replay.patch_error else None,
        "configured": patched.configured if patched else True,
        "exit_code": patched.exit_code if patched else None,
        "timed_out": patched.timed_out if patched else False,
        "tests_passed": patched.passed if patched else False,
        "passed": replay.passed,
        "tests_summary": test_summary(output) if patched else "",
        "failing": failing_tests(output),
        "flags": [redact(f, secrets) for f in replay.flags],
        "summary": redact(replay.summary(), secrets),
        "output_tail": output[-OUTPUT_TAIL:],
    }


def check_job(
    sandbox: Sandbox,
    files: Mapping[str, bytes],
    patch: str,
    repro_test: str,
    *,
    test_cmd: str,
    timeout_s: int,
    scratch_dir: str | None,
    secrets: Iterable[str] = (),
) -> dict[str, Any]:
    """Off the tick: the repro test alone on the base (must fail), then the full patch (must
    pass). → `{"before": run, "after": run | None}`; `after` is None when `before` already
    failed the check (the repro test passed on the base, or didn't apply)."""
    secrets = list(secrets)
    kw = {"sandbox": sandbox, "timeout_s": timeout_s, "scratch_dir": scratch_dir}
    repro_only = rules.split_patch(patch, repro_test)
    before = replay_code_patch(
        files, repro_only, test_cmd=repro_command(test_cmd, repro_test), **kw
    )
    result: dict[str, Any] = {"before": _run(before, secrets), "after": None}
    if rules.repro_failed(result["before"]):
        result["after"] = _run(replay_code_patch(files, patch, test_cmd=test_cmd, **kw), secrets)
    return result
