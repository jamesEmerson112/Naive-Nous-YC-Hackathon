"""Code lock-in with the coding agent (M8b, D21, flow F11): the batch's draft, the gate's replay.

With `CODER_PROVIDER` other than `fake`, a held overlay with a `code_patch` goes like this:

1. **Draft (batch).** `batch` submits `coder.draft(...)` as a job (`services/jobs.py`) keyed
   by the class's current code version and the patch's hash (identical patches share one
   draft), within CODER_MAX_DRAFTS_PER_HOUR, and leaves the overlay held. On a
   later run the finished draft becomes a new immutable code version (the base package with
   the coder's diff applied, `source = coder`), a firmware manifest naming it, and a code
   candidate. A coder error, a failing draft or a diff touching `verify/` builds nothing:
   the overlay is `lockin_error`, sticky, with the reason. `CodeDraft.passed` only decides
   whether a draft is worth replaying; the gate never trusts it.
2. **Replay (gate).** For a `source = coder` code candidate, `gate_replay` submits
   `coder_runs.replay_code_patch` (fresh containers, no network, held-out verify scripts,
   the device test command) as a job keyed by the candidate, and returns None (still
   replaying) until it finishes. A failing replay rolls the candidate back with the failing
   tests named. With no test command the verify scripts still run. With **no held-out
   verify scripts** the candidate waits (`AwaitingVerify`, not a failure: it replays once
   verify exists), because the device tests run in the same process as the code under test
   and can forge their result seal; `CODER_ALLOW_UNVERIFIED=true` lets the device tests
   alone decide, marked "unverified". With neither, the replay fails (fail closed: only a
   pass-through candidate gets the labeled stub). Then the existing gate rules apply: with
   no canary running real code (B7) it waits at `awaiting_canary`.

Job functions here take plain data (file dicts, text) and never touch the database. The
pass-through path (`CODER_PROVIDER=fake`, the default) doesn't come here at all.
"""

from __future__ import annotations

import hashlib
import io
import json
import re
import shutil
import tarfile
import tempfile
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from functools import partial
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from romulus_cloud.adapters.blobstore import BlobStore, build_blobstore
from romulus_cloud.adapters.coder import (
    DEFAULT_IMAGE,
    HOLDOUT_DIR,
    CodeDraft,
    CoderError,
    Sandbox,
    ScratchRepo,
    redact,
)
from romulus_cloud.domain import gate as g
from romulus_cloud.domain.doctor import patch_changes
from romulus_cloud.models import Candidate, CodeVersion, Overlay
from romulus_cloud.services import registry
from romulus_cloud.services.coder_runs import CodeReplay, replay_code_patch
from romulus_cloud.services.jobs import Done, Failed, Jobs, Missing, Pending, default_jobs
from romulus_cloud.settings import Settings
from romulus_contracts.codepatch import CODE_ROOT, is_under, summarize_code_patch

SOURCE = "coder"  # code_versions.source for a version the coding agent drafted
# Sources the gate replays for real (`replay_code_patch`) and never treats as pass-through:
# the coding agent's, and a verified Fleet Doctor proposal's (`doctor_lockin.SOURCE`, D24).
REPLAYED_SOURCES = frozenset({SOURCE, "doctor"})
OUTPUT_TAIL = 4000  # characters of each test output kept in an event
_FAILED = re.compile(r"^(?:FAILED|ERROR) (\S+)", re.MULTILINE)
UNVERIFIED = "unverified: decided by in-process tests only"
NO_VERIFY = (
    "no held-out verify for this code version: in-process tests alone can't be trusted; "
    "run `make device-verify`"
)
_COUNTS = re.compile(r"\d+ (?:passed|failed|errors?|skipped)\b.*$", re.MULTILINE)


DRAFT_POOL = "coder.draft"  # separate pools: a long draft never queues a replay behind it
REPLAY_POOL = "coder.replay"


def draft_key(ov: Overlay, base_rev: str) -> str:
    """One draft per (code version, patch): identical patches from several arms share it."""
    digest = hashlib.sha256((ov.code_patch or "").encode()).hexdigest()[:16]
    return f"coder.draft:{ov.fleet_id}:{base_rev}:{digest}"


def coder_blocker(settings: Settings, docker: str = "docker") -> str | None:
    """Why this process can't run coder jobs at all, or None. Checked once per tick before
    any job is submitted, so a misplaced worker waits with one clear error instead of
    marking overlays `lockin_error`."""
    if settings.coder_provider == "local_agent" and shutil.which(docker) is None:
        return (
            "CODER_PROVIDER=local_agent, but this worker has no `docker` CLI, so it can't "
            "start the coding agent or the code replay; run the worker on the host "
            "(`make worker-host`) or set CODER_PROVIDER=fake"
        )
    return None


def replay_key(c: Candidate) -> str:
    return f"coder.replay:{c.fleet_id}:{c.id}"


def read_package(store: BlobStore, cv: CodeVersion) -> dict[str, bytes]:
    """A code version's package as {path: bytes} (paths under `device/`)."""
    with registry.open_package(store, cv.sha256, f"code {cv.rev}") as stream:
        data = stream.read()
    files: dict[str, bytes] = {}
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
        for member in tar.getmembers():
            if member.isfile():
                files[member.name.removeprefix("./")] = tar.extractfile(member).read()  # type: ignore[union-attr]
    return files


# --- plain-data helpers (no database) -----------------------------------------------------


def apply_diff(files: Mapping[str, bytes], diff: str) -> dict[str, bytes]:
    """The package `files` with `diff` (a code_patch, paths relative to `device/`) applied.
    The changed files come from the parsed diff, not `git status`, so a `.gitignore` can't
    drop one from the new package."""
    with tempfile.TemporaryDirectory(prefix="romulus-lockin-") as tmp:
        repo = ScratchRepo.create(Path(tmp), files)
        error = repo.apply(diff)
        if error:
            raise CoderError(f"the coder's diff does not apply to the base code: {error}")
        out = dict(files)
        for status, rel in patch_changes(diff):
            name = f"{CODE_ROOT}/{rel}"
            if status.startswith("D"):
                out.pop(name, None)
            else:
                out[name] = (repo.work / rel).read_bytes()
    return out


def package_diff(base: Mapping[str, bytes], new: Mapping[str, bytes]) -> str:
    """The code_patch that turns package `base` into package `new` (their `device/` parts).
    Raises CoderError when the diff doesn't name every changed file (git ignored one, e.g.
    through the base's `.gitignore`): the replay must test exactly what would ship."""
    prefix = f"{CODE_ROOT}/"
    changed = {
        name.removeprefix(prefix)
        for name in set(base) | set(new)
        if name.startswith(prefix) and base.get(name) != new.get(name)
    }
    diff = _git_package_diff(base, new)
    missing = sorted(changed - set(summarize_code_patch(diff).paths))
    if missing:
        raise CoderError(
            "the new code's diff leaves out changed files (ignored by git?): "
            + ", ".join(missing[:10])
        )
    return diff


def _git_package_diff(base: Mapping[str, bytes], new: Mapping[str, bytes]) -> str:
    with tempfile.TemporaryDirectory(prefix="romulus-lockin-") as tmp:
        repo = ScratchRepo.create(Path(tmp), base)
        for name in base:
            if name.startswith(f"{CODE_ROOT}/") and name not in new:
                (repo.work / name.removeprefix(f"{CODE_ROOT}/")).unlink(missing_ok=True)
        for name, data in new.items():
            if name.startswith(f"{CODE_ROOT}/") and base.get(name) != data:
                rel = Path(name.removeprefix(f"{CODE_ROOT}/"))
                (repo.work / rel).parent.mkdir(parents=True, exist_ok=True)
                (repo.work / rel).write_bytes(data)
        return repo.diff()


def run_replay(
    sandbox: Sandbox,
    base: Mapping[str, bytes],
    new: Mapping[str, bytes],
    *,
    test_cmd: str,
    verify_files: Mapping[str, bytes],
    timeout_s: int,
    scratch_dir: str | None,
) -> CodeReplay:
    """The replay job: verify + device tests on the base, then on the new code version."""
    return replay_code_patch(
        base,
        package_diff(base, new),
        sandbox=sandbox,
        test_cmd=test_cmd,
        verify_files=verify_files,
        timeout_s=timeout_s,
        scratch_dir=scratch_dir,
    )


def verify_files(directory: str) -> dict[str, bytes]:
    """CODER_VERIFY_DIR's files as `verify/<path>` entries ({} when unset)."""
    if not directory:
        return {}
    root = Path(directory)
    if not root.is_dir():
        raise CoderError(f"CODER_VERIFY_DIR {directory!r} is not a directory")
    return {
        f"{HOLDOUT_DIR}/{p.relative_to(root).as_posix()}": p.read_bytes()
        for p in sorted(root.rglob("*"))
        if p.is_file() and not p.is_symlink() and "__pycache__" not in p.parts
    }


VERIFY_MANIFEST = f"{HOLDOUT_DIR}/MANIFEST.json"  # written by devicecode.verify_from_bench
_LINEAGE_MAX = 100


def stale_verify(session: Session, extra: Mapping[str, bytes], base: CodeVersion) -> str | None:
    """Why CODER_VERIFY_DIR doesn't fit `base`, or None. Verify generated for a code version
    (its MANIFEST.json's `generated_from.code_rev`) fits that version and its descendants
    (`parent_rev` chain); anything else fails closed rather than as misleading test failures.
    A verify dir with no manifest isn't tied to a version and always fits."""
    raw = extra.get(VERIFY_MANIFEST)
    if raw is None:
        return None
    try:
        rev = json.loads(raw)["generated_from"]["code_rev"]
    except (ValueError, KeyError, TypeError):
        rev = None
    if not isinstance(rev, str) or not rev:
        return (
            "stale verify: CODER_VERIFY_DIR's MANIFEST.json names no code_rev; regenerate "
            "with `make device-verify`"
        )
    lineage, cv = [], base
    while cv is not None and len(lineage) < _LINEAGE_MAX:
        lineage.append(cv.rev)
        cv = session.get(CodeVersion, (cv.fleet_id, cv.parent_rev)) if cv.parent_rev else None
    if rev in lineage:
        return None
    return (
        f"stale verify: CODER_VERIFY_DIR was generated from code {rev}, which is not the base "
        f"{base.rev} or one of its ancestors; regenerate with `make device-verify`"
    )


def failing_tests(*outputs: str) -> list[str]:
    """Test ids pytest named as FAILED or ERROR in its short summary, in order, unique."""
    return list(dict.fromkeys(m for out in outputs for m in _FAILED.findall(out or "")))


def test_summary(output: str) -> str:
    """pytest's last counts line ("2 passed, 1 failed in 0.1s"), or a short tail."""
    found = _COUNTS.findall(output or "")
    if found:
        return found[-1].strip("= ").strip()
    tail = (output or "").strip().splitlines()[-1:] or ["no output"]
    return tail[0][:200]


def draft_summary(draft: CodeDraft) -> str:
    return f"{draft.notes} Its own test run (advisory): {test_summary(draft.test_output)}."


def to_replay_result(replay: CodeReplay, rev: str, secrets: Iterable[str]) -> g.ReplayResult:
    """The gate's view of a code replay: pass/fail, a why naming every failing test."""
    summary = redact(replay.summary(), secrets)
    if replay.passed and unverified(replay):
        why = (
            f"{UNVERIFIED} (no held-out verify scripts; CODER_ALLOW_UNVERIFIED=true): code "
            f"replay passed on code {rev} (device tests, fresh containers, no network): "
            f"{summary}"
        )
        return g.ReplayResult(True, why, (g.CaseResult(f"code {rev}", True, summary),))
    if replay.passed:
        why = (
            f"code replay passed on code {rev} (device tests and held-out verify scripts, "
            f"fresh containers, no network): {summary}"
        )
        if not replay.caught_fault:
            why += "; note: the base code passed every check too, so they don't show the fault"
        return g.ReplayResult(True, why, (g.CaseResult(f"code {rev}", True, summary),))
    patched = replay.patched
    outputs = [patched.output if patched else "", (replay.patched_verify or _NO).output]
    # The seal's ids first (the verify process runs outside the work dir, so its printed
    # paths are not the ids); printed FAILED lines only name tests when nothing was sealed.
    failing = list(dict.fromkeys(replay.failing_ids())) or failing_tests(*outputs)
    named = f"; failing: {', '.join(failing)}" if failing else ""
    why = f"code replay failed on code {rev}: {summary}{named}"
    cases = tuple(g.CaseResult(t, False, "failed in the replay") for t in failing)
    return g.ReplayResult(False, why, cases or (g.CaseResult(f"code {rev}", False, summary),))


def unverified(replay: CodeReplay) -> bool:
    """No held-out verify test decided this replay (only the in-process device tests)."""
    return replay.expected_verify == 0


class _No:
    output = ""


_NO = _No()


def replay_output(replay: CodeReplay, secrets: Iterable[str]) -> dict[str, Any]:
    """Redacted output tails for the `candidate.replayed` event."""

    def tail(text: str | None) -> str:
        return redact(text or "", secrets)[-OUTPUT_TAIL:]

    return {
        "replay_summary": redact(replay.summary(), secrets),
        "replay_output": {
            "base_tests": tail(replay.base.output),
            "base_verify": tail(replay.base_verify.output),
            "patched_tests": tail(replay.patched.output if replay.patched else ""),
            "patched_verify": tail(replay.patched_verify.output if replay.patched_verify else ""),
        },
        "replay_flags": list(replay.flags),
        "replay_unverified": unverified(replay),
    }


# --- the gate's side ----------------------------------------------------------------------


@dataclass
class ReplayTools:
    """What the gate's code replay needs: settings, the job runner, the sandbox, the store."""

    settings: Settings
    jobs: Jobs
    sandbox: Sandbox
    store: BlobStore
    blocked: str | None = None  # `coder_blocker`: replays wait instead of failing
    # Base packages by (fleet, rev), so a candidate parked for verify doesn't re-read its
    # base every tick. Code versions are immutable; a few entries at most.
    _bases: dict[tuple[str, str], dict[str, bytes]] = field(default_factory=dict, repr=False)

    def base_package(self, cv: CodeVersion) -> dict[str, bytes]:
        key = (cv.fleet_id, cv.rev)
        if key not in self._bases:
            if len(self._bases) >= 8:
                self._bases.clear()
            self._bases[key] = read_package(self.store, cv)
        return self._bases[key]

    @classmethod
    def from_settings(cls, settings: Settings) -> ReplayTools:
        return cls(
            settings=settings,
            jobs=default_jobs(settings.jobs_max_workers, REPLAY_POOL),
            sandbox=Sandbox(image=settings.coder_image or DEFAULT_IMAGE),
            store=build_blobstore(settings.blob_dir),
            blocked=coder_blocker(settings),
        )

    @property
    def secrets(self) -> list[str]:
        return [self.settings.llm_api_key.get_secret_value()]


def coder_version(session: Session, c: Candidate) -> CodeVersion | None:
    """The candidate's code version if the coding agent (or a verified Fleet Doctor
    proposal) drafted it, else None."""
    if c.type != "code" or not c.code_rev:
        return None
    cv = session.get(CodeVersion, (c.fleet_id, c.code_rev))
    return cv if cv is not None and cv.source in REPLAYED_SOURCES else None


def is_passthrough(session: Session, c: Candidate) -> bool:
    """A code candidate counts as pass-through unless the coding agent or a verified Fleet
    Doctor proposal drafted it (fail closed: a missing code version is pass-through)."""
    return coder_version(session, c) is None


@dataclass(frozen=True)
class AwaitingVerify:
    """The replay can't start: no held-out verify scripts. The gate waits (no failure, no
    retry count) and replays once verify exists."""

    why: str = NO_VERIFY


def no_verify(directory: str, base_rev: str) -> AwaitingVerify:
    """Wait for verify; the why names CODER_VERIFY_DIR when it is set (missing or empty)."""
    if not directory:
        return AwaitingVerify()
    return AwaitingVerify(
        f"no held-out verify at `{directory}` (nor in code {base_rev}'s verify/): in-process "
        "tests alone can't be trusted; run `make device-verify`"
    )


def gate_replay(
    session: Session, c: Candidate, tools: ReplayTools
) -> g.ReplayResult | AwaitingVerify | None:
    """The replay for a code candidate; None while its replay job runs (the gate waits),
    `AwaitingVerify` while there are no held-out verify scripts."""
    cv = coder_version(session, c)
    if cv is None:
        return g.replay_code_stub()
    settings = tools.settings
    key = replay_key(c)
    status = tools.jobs.poll(key)
    if isinstance(status, Missing):
        if tools.blocked:
            return None  # the worker logged why at startup; wait for a worker that can run it
        parent = session.get(CodeVersion, (c.fleet_id, cv.parent_rev)) if cv.parent_rev else None
        if parent is None:
            return g.ReplayResult(
                False, f"code replay failed: the base code {cv.parent_rev} is not registered"
            )
        base = tools.base_package(parent)
        base_verify = any(is_under(n, f"{CODE_ROOT}/{HOLDOUT_DIR}") for n in base)
        try:
            extra = verify_files(settings.coder_verify_dir)
        except CoderError as exc:
            if not base_verify and not settings.coder_allow_unverified:
                # e.g. the default data/verify/device before `make device-verify`: wait for
                # it, never roll back (a single-overlay fix would go sticky)
                return no_verify(settings.coder_verify_dir, parent.rev)
            return g.ReplayResult(False, f"code replay could not start: {exc}")
        stale = stale_verify(session, extra, parent)
        if stale:
            return g.ReplayResult(False, f"code replay failed: {stale}")
        has_verify = bool(extra) or base_verify
        if not has_verify and not settings.coder_allow_unverified:
            return no_verify(settings.coder_verify_dir, parent.rev)
        if not settings.coder_test_cmd.strip() and not has_verify:
            return g.ReplayResult(
                False,
                "code replay failed: nothing can check the coder's change (CODER_TEST_CMD is "
                f"empty and code {cv.parent_rev} has no verify scripts, nor CODER_VERIFY_DIR)",
            )
        job = partial(
            run_replay,
            tools.sandbox,
            base,
            read_package(tools.store, cv),
            test_cmd=settings.coder_test_cmd,
            verify_files=extra,
            timeout_s=settings.coder_replay_timeout_s,
            scratch_dir=settings.coder_scratch_dir or None,
        )
        tools.jobs.submit(key, job)
        status = tools.jobs.poll(key)
    if isinstance(status, Pending | Missing):
        return None
    if isinstance(status, Failed):
        return g.ReplayResult(
            False, f"code replay could not run: {redact(status.error, tools.secrets)}"
        )
    assert isinstance(status, Done)
    replay = status.result
    if replay.passed and unverified(replay) and not settings.coder_allow_unverified:
        # verify files were there but no verify test was collected: nothing held-out decided
        return g.ReplayResult(
            False,
            f"code replay failed on code {cv.rev}: the held-out verify scripts collected no "
            "tests, so only the in-process device tests checked it; fix the verify scripts "
            "(`make device-verify`)",
        )
    return to_replay_result(replay, cv.rev, tools.secrets)


def gate_replay_applied(c: Candidate, tools: ReplayTools) -> dict[str, Any]:
    """After the gate applied a replay result: its redacted outputs for the event (or {}),
    and the job is forgotten."""
    key = replay_key(c)
    status = tools.jobs.poll(key)
    tools.jobs.forget(key)
    if isinstance(status, Done) and isinstance(status.result, CodeReplay):
        return replay_output(status.result, tools.secrets)
    return {}
