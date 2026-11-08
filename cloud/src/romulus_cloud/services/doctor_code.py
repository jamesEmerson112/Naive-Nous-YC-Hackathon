"""The Fleet Doctor's code side (PR 7): the base package, the coder's draft, the sandbox check
and the coder budget it shares with the batch.

- **Package:** a consult's base is its incident's `running.code_rev`. `package` reads that
  code version's `.tar.gz` (`code_lockin.read_package`) and keys it relative to CODE_ROOT
  with **`verify/` removed** (`domain.doctor.package_rel`), so the held-out verify scripts
  never reach the case file, the coder or the check (architecture §6 item 7). `coder_files`
  puts the `device/` prefix back for the coder and the replay.
- **Draft job** (`draft_job`): `coder.draft(patch="", ..., task=...)`, plain data in, a
  `CodeDraft` out.
- **Check job** (`check_job`): up to four `coder_runs.replay_code_patch` runs in fresh
  containers with no network and `PYTHONSAFEPATH=1`, each stopping the check as soon as it
  fails:
  1. the repro test's part of the patch alone (`domain.doctor.split_patch`) with the fixed
     `REPRO_CMD` on the repro file: its tests must FAIL on behaviour (`repro_failures`);
  2. the full patch with the same `REPRO_CMD`: those same test ids must PASS;
  3. CODER_TEST_CMD, unchanged (never appended to, so a compound command like
     `pytest -q; ruff check .` stays what it says), on the patch's non-test hunks only
     (`domain.doctor.code_only`): the device tests pass without any test file's help;
  4. when the base package has `verify/` scripts: once more on the same code, with them
     (architecture §6 item 7), pass/fail and counts only.
  The verify scripts are in no other run's `files`.
- **Coder budget:** CODER_MAX_DRAFTS_PER_HOUR counts `coder.started` events fleet-wide in
  the last hour, minus job attempts the provider turned away having spent exactly 0 tokens
  (the refund rule of `batch._drafts_this_hour`, #38). `coder_budget` runs the same query
  (copied rather than imported: `batch.py` belongs to another PR) and also refunds the
  Doctor's own zero-token attempts (`doctor.declined`, reason `coder_unavailable`). Every
  Doctor submit records `coder.started` (`data.source = "doctor"`), so one budget covers
  both. The batch's query doesn't see the Doctor's refunds yet (it reads only
  `overlay.deferred` / `batch.error`), so it counts those attempts as spent: stricter, never
  looser.

Job functions never touch the database. Nothing here writes or commits.
"""

import shlex
import tarfile
from collections.abc import Iterable, Mapping
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import distinct, func, select
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
from romulus_cloud.domain import coder_retry
from romulus_cloud.domain import doctor as rules
from romulus_cloud.errors import ApiError
from romulus_cloud.models import CodeVersion, Event
from romulus_cloud.services.code_lockin import failing_tests, read_package, test_summary
from romulus_cloud.services.coder_runs import CodeReplay, replay_code_patch
from romulus_cloud.settings import Settings
from romulus_contracts.codepatch import CODE_ROOT

CODER_STARTED = "coder.started"  # the batch's event; its count is the hourly budget
CODER_UNAVAILABLE = "coder_unavailable"  # the batch's reason for a provider blip (#38)
OUTPUT_TAIL = 2000  # characters of each replay's test output kept in the event
# The repro step's fixed command: isolated Python (a planted `pytest.py` can't shadow
# pytest), `-rA` so passing ids are listed too. The device's conftest still applies.
REPRO_CMD = "python -I -m pytest -q -rA"
# Every check run, CODER_TEST_CMD included (left as configured): Python never puts the work
# dir (or a script's dir) first on sys.path, so a planted `pytest.py` can't shadow pytest.
SAFE_ENV = {"PYTHONSAFEPATH": "1"}


class NoPackage(Exception):
    """The consult's base code can't be read, so there is nothing to draft or check against."""


def _read(session: Session, store: BlobStore, rev: str | None, fleet_id: str) -> dict[str, bytes]:
    if not rev:
        raise NoPackage("the incident has no running.code_rev")
    cv = session.get(CodeVersion, (fleet_id, rev))
    if cv is None:
        raise NoPackage(f"code {rev} is not registered (POST /v1/code)")
    try:
        return read_package(store, cv)
    except (ApiError, tarfile.TarError, OSError, EOFError) as exc:
        detail = exc.detail if isinstance(exc, ApiError) else str(exc)
        raise NoPackage(f"code {rev}'s package can't be read: {detail}") from exc


def package(session: Session, store: BlobStore, rev: str | None, fleet_id: str) -> dict[str, bytes]:
    """Code version `rev`'s files keyed relative to CODE_ROOT, without `verify/`."""
    return package_parts(session, store, rev, fleet_id)[0]


def package_parts(
    session: Session, store: BlobStore, rev: str | None, fleet_id: str
) -> tuple[dict[str, bytes], dict[str, bytes]]:
    """(the package without `verify/`, keyed relative to CODE_ROOT; its held-out `verify/`
    scripts as `verify/<path>`). Only the check's final pass/fail run gets the second."""
    files = _read(session, store, rev, fleet_id)
    rel = rules.package_rel(files)
    if not rel:
        raise NoPackage(f"code {rev}'s package has no files outside {rules.HOLDOUT_DIR}/")
    return rel, rules.held_out_files(files)


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
    """(coder drafts counted against the last hour, fleet-wide, batch and Doctor alike; when
    the budget next has room, or None if it has room now).

    The batch's rule (`batch._drafts_this_hour`): `coder.started` events with
    `ts > now - 1h`, minus job attempts known to have spent exactly 0 tokens, counted once
    per job attempt (`reason: coder_unavailable, no_tokens: true` on the batch's
    `overlay.deferred` / `batch.error` and on the Doctor's `doctor.declined`). Over budget,
    room comes back when enough of the oldest starts leave the window (checked again then);
    with a budget of 0 it never does, so the answer is an hour from now.
    """
    recent = (Event.fleet_id == fleet_id, Event.ts > now - timedelta(hours=1))
    stamps = list(
        session.scalars(
            select(Event.ts).where(*recent, Event.kind == CODER_STARTED).order_by(Event.ts)
        )
    )
    job_attempt = Event.data["job"].astext.concat(":").concat(Event.data["attempt"].astext)
    free = session.scalar(
        select(func.count(distinct(job_attempt))).where(
            *recent,
            Event.kind.in_(["overlay.deferred", "batch.error", "doctor.declined"]),
            Event.data.contains({"reason": CODER_UNAVAILABLE, "no_tokens": True}),
        )
    )
    counted = max(len(stamps) - (free or 0), 0)
    if counted < budget:
        return counted, None
    if budget <= 0 or not stamps:
        return counted, now + timedelta(hours=1)
    return counted, stamps[min(counted - budget, len(stamps) - 1)] + timedelta(hours=1)


# --- jobs (plain data in and out; never the database) -------------------------------------


def outcome(draft: CodeDraft, secrets: Iterable[str] = ()) -> coder_retry.CoderOutcome:
    """A draft as `domain.coder_retry` reads it (the batch's `_outcome`, error redacted)."""
    return coder_retry.CoderOutcome(
        passed=draft.passed,
        exit_status=draft.exit_status,
        error=redact(draft.error, list(secrets)),
        steps=draft.steps,
        tokens=draft.tokens,
        no_result=draft.no_result,
    )


def draft_job(
    coder: Coder,
    files: Mapping[str, bytes],
    incident: dict[str, Any],
    base_rev: str,
    task: str,
) -> CodeDraft:
    """Off the tick: the coder writes a repro test and a fix from scratch (`task`)."""
    return coder.draft("", incident, base_rev, files, task=task)


def repro_command(repro_test: str) -> str:
    """`REPRO_CMD` on the repro file, shell-quoted (the image runs commands through a shell
    in CODE_ROOT). Never derived from CODER_TEST_CMD. Raises ValueError for a path that isn't
    a test file under `tests/` (the contract's reading)."""
    rel = rules.patch_path(repro_test)
    if rel is None or not rules.is_test_path(rel):
        raise ValueError(f"{repro_test!r} is not a test file under {rules.REPRO_DIR}/")
    return f"{REPRO_CMD} {shlex.quote(rel)}"


def _run(replay: CodeReplay, secrets: Iterable[str]) -> dict[str, Any]:
    """The plain-data view of one replay that `domain.doctor.check_verdict` reads."""
    patched = replay.patched
    output = redact(patched.output if patched else "", secrets)
    results = rules.pytest_results(output)
    return {
        "patch_error": redact(replay.patch_error, secrets) if replay.patch_error else None,
        "configured": patched.configured if patched else True,
        "exit_code": patched.exit_code if patched else None,
        "timed_out": patched.timed_out if patched else False,
        "tests_passed": patched.passed if patched else False,
        "passed": replay.passed,
        "tests_summary": test_summary(output) if patched else "",
        "failing": failing_tests(output),
        "failed": results["failed"],
        "passed_ids": results["passed"],
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
    verify_files: Mapping[str, bytes] | None = None,
) -> dict[str, Any]:
    """Off the tick, every run with `SAFE_ENV`; each stops the check when it fails:

    1. the repro tests alone on the base (must fail on behaviour);
    2. the same tests with the full patch (must pass);
    3. the device tests (`test_cmd`) on the base plus the patch's non-test hunks only
       (`domain.doctor.code_only`), so no test file can mask a regression;
    4. when `verify_files` (the base's held-out `verify/` scripts) exist: once, on the same
       code, pass/fail only. Only counts come back; their output is dropped here.

    → `{"before", "after_repro", "after", "verify"}`; a later step is None when an earlier
    one already failed the check, and `verify` is `{"available": False}` with no scripts."""
    secrets = list(secrets)
    kw = {
        "sandbox": sandbox,
        "timeout_s": timeout_s,
        "scratch_dir": scratch_dir,
        "extra_env": SAFE_ENV,
        "split_tests": False,  # this check splits the patch itself (repro, code_only)
    }
    repro_cmd = repro_command(repro_test)
    repro_only = rules.split_patch(patch, repro_test)
    before = _run(replay_code_patch(files, repro_only, test_cmd=repro_cmd, **kw), secrets)
    result: dict[str, Any] = {"before": before, "after_repro": None, "after": None, "verify": None}
    ids, _ = rules.repro_failures(before, repro_test)
    if not ids:
        return result
    result["after_repro"] = _run(replay_code_patch(files, patch, test_cmd=repro_cmd, **kw), secrets)
    if rules.repro_passes(result["after_repro"], ids):
        return result
    code = rules.code_only(patch)
    result["after"] = _run(replay_code_patch(files, code, test_cmd=test_cmd, **kw), secrets)
    if not result["after"]["passed"]:
        return result
    if not verify_files:
        result["verify"] = {"available": False}
        return result
    held = replay_code_patch(files, code, test_cmd="", verify_files=verify_files, **kw)
    run = held.patched_verify
    result["verify"] = {  # pass/fail and counts only: the scripts' output stays hidden
        "available": True,
        "passed": held.verify_passed and held.patch_error is None,
        "tests": held.expected_verify,
        "passed_count": run.passed if run else 0,
        "skipped": run.skipped if run else 0,
        "timed_out": bool(run and run.timed_out),
    }
    return result
