"""Operator commands: `python -m romulus_cloud.cli {seed,code,reset,digest,overnight,export}`
(architecture §8)."""

import argparse
import json
import os
import shutil
import sys
from pathlib import Path

from pydantic import SecretStr
from sqlalchemy import inspect
from sqlalchemy.exc import ProgrammingError, SQLAlchemyError
from sqlalchemy.orm import Session

from romulus_cloud import db
from romulus_cloud.adapters.blobstore import build_blobstore
from romulus_cloud.adapters.coder import DEFAULT_IMAGE, DEFAULT_TEST_CMD, Sandbox
from romulus_cloud.devicecode import check as device_check
from romulus_cloud.devicecode import package as device_package
from romulus_cloud.devicecode.package import PackageError
from romulus_cloud.errors import ApiError
from romulus_cloud.ids import utcnow
from romulus_cloud.models import Base
from romulus_cloud.services import digest as digest_service
from romulus_cloud.services import overnight as overnight_service
from romulus_cloud.services import registry
from romulus_cloud.services import seed as seed_service
from romulus_cloud.services.seed import seed as seed_fleet
from romulus_cloud.settings import Settings, get_settings
from romulus_contracts.codepatch import CODE_ROOT

UNDEFINED_TABLE, UNDEFINED_COLUMN = "42P01", "42703"  # Postgres SQLSTATEs


def _not_yet(name: str, milestone: str) -> int:
    print(f"{name}: not implemented yet ({milestone}).", file=sys.stderr)
    return 1


def _clear_dir(path: str) -> None:
    root = Path(path)
    if not root.is_dir():
        return
    for child in root.iterdir():
        if child.is_dir() and not child.is_symlink():
            shutil.rmtree(child)
        else:
            child.unlink()


def reset(settings: Settings, force: bool) -> int:
    """Drop and recreate every table and empty the blob and export dirs.

    Refused once the overnight run has started (the `OVERNIGHT_STARTED` env flag, or
    `cli overnight start` in the database), unless forced (build plan K7).
    """
    if settings.overnight_started and not force:
        print(
            "reset: refused. OVERNIGHT_STARTED is set, so this would wipe the overnight data. "
            "Run with FORCE=1 if you really mean it.",
            file=sys.stderr,
        )
        return 2
    engine = db.make_engine(settings.database_url)
    try:
        try:
            started = None if force else _overnight_started_at(engine, settings.fleet_id)
        except SQLAlchemyError as exc:
            print(
                f"reset: refused. Could not read the overnight flag ({type(exc).__name__}), so "
                "this might wipe an overnight run. Fix the database, or FORCE=1 if you mean it.",
                file=sys.stderr,
            )
            return 2
        if started is not None:
            print(
                f"reset: refused. The overnight run started at {started.isoformat()} "
                "(`cli overnight start`), so this would wipe its data. Run "
                "`cli overnight stop` first, or FORCE=1 if you really mean it.",
                file=sys.stderr,
            )
            return 2
        db.drop_all(engine)
        db.create_all(engine)
    finally:
        engine.dispose()
    _clear_dir(settings.blob_dir)
    _clear_dir(settings.export_dir)
    print("reset: database recreated; blob and export dirs emptied.")
    return 0


def _overnight_started_at(engine, fleet_id: str):
    """The DB overnight flag; None when unset or when the table or column doesn't exist yet
    (a database from before M7). Any other database error propagates: fail closed."""
    try:
        with Session(engine) as session:
            return overnight_service.started_at(session, fleet_id)
    except ProgrammingError as exc:
        if getattr(exc.orig, "sqlstate", None) in (UNDEFINED_TABLE, UNDEFINED_COLUMN):
            return None
        raise


def digest(settings: Settings, since: str | None, until: str | None, as_json: bool) -> int:
    """Print the digest since `since` (ISO or 1h / 12h; default: overnight start, else 12 h)."""
    engine = db.make_engine(settings.database_url)
    try:
        with Session(engine) as session:
            report = digest_service.report(
                session,
                utcnow(),
                since=since,
                until=until,
                budget_gb=settings.blob_budget_gb,
                fleet_id=settings.fleet_id,
            )
    except ApiError as exc:
        print(f"digest: {exc.detail}", file=sys.stderr)
        return 2
    finally:
        engine.dispose()
    if as_json:
        print(json.dumps(report.to_dict(), indent=2))
    else:
        print(report.text(settings.display_tz), end="")
    return 0


def overnight(settings: Settings, action: str, by: str) -> int:
    """`start` sets the DB overnight flag (reset then refuses); `stop` clears it."""
    engine = db.make_engine(settings.database_url)
    try:
        with Session(engine) as session:
            if action == "start":
                started = overnight_service.start(session, utcnow(), by, settings.fleet_id)
                session.commit()
                print(f"overnight: started at {started.isoformat()}; reset now refuses.")
            elif action == "stop":
                was = overnight_service.stop(session, utcnow(), by, settings.fleet_id)
                session.commit()
                print("overnight: stopped; reset is allowed again." if was else "overnight: off.")
            else:
                started = overnight_service.started_at(session, settings.fleet_id)
                print(
                    f"overnight: started at {started.isoformat()}" if started else "overnight: off."
                )
    finally:
        engine.dispose()
    return 0


def missing_columns(engine) -> list[str]:
    """`table` or `table.column` for everything the models have and the database lacks.
    `create_all` never adds a column to an existing table (cloud/CLAUDE.md)."""
    have = inspect(engine)
    tables = set(have.get_table_names())
    missing = []
    for table in Base.metadata.sorted_tables:
        if table.name not in tables:
            missing.append(table.name)
            continue
        columns = {c["name"] for c in have.get_columns(table.name)}
        missing += [f"{table.name}.{c.name}" for c in table.columns if c.name not in columns]
    return missing


def schema_check(settings: Settings) -> int:
    """Exit 1 and list what's missing when the database is behind the models."""
    engine = db.make_engine(settings.database_url)
    try:
        missing = missing_columns(engine)
    finally:
        engine.dispose()
    if missing:
        print(f"schema: the database lacks {', '.join(missing)}")
        return 1
    print("schema: the database matches the models.")
    return 0


def setting(settings: Settings, name: str) -> int:
    """Print one setting as the app resolves it (env names match case-insensitively, `.env`,
    defaults), so `make overnight-check` checks what the app will really do (K13)."""
    key = name.strip().lower()
    if key not in Settings.model_fields:
        print(f"setting: unknown setting {name!r}.", file=sys.stderr)
        return 2
    value = getattr(settings, key)
    print("<secret>" if isinstance(value, SecretStr) else value)
    return 0


def seed(settings: Settings) -> int:
    """Create code c0de001, firmware v1, harness v1 and the demo devices; safe to repeat."""
    engine = db.make_engine(settings.database_url)
    try:
        db.create_all(engine)
        with Session(engine) as session:
            result = seed_fleet(session, build_blobstore(settings.blob_dir), settings.fleet_id)
            session.commit()
    finally:
        engine.dispose()
    print(f"seed: created {', '.join(result.created) or 'nothing'}", end="")
    if result.updated:
        print(f"; set kind and role on {', '.join(result.updated)}", end="")
    print(f"; already there: {', '.join(result.existing)}." if result.existing else ".")
    return 0


def seed_bench(settings: Settings, code_rev: str | None) -> int:
    """The real bench: arm-1 (real) and sim-1 (sim canary) on the registered device/ code."""
    engine = db.make_engine(settings.database_url)
    try:
        db.create_all(engine)
        with Session(engine) as session:
            result = seed_service.seed_bench(
                session, build_blobstore(settings.blob_dir), code_rev, settings.fleet_id
            )
            session.commit()
    except ApiError as exc:
        print(f"seed --bench: refused. {exc.detail}", file=sys.stderr)
        return 2
    finally:
        engine.dispose()
    print(f"seed --bench: created {', '.join(result.created) or 'nothing'}", end="")
    if result.updated:
        print(f"; updated {', '.join(result.updated)}", end="")
    print(f"; already there: {', '.join(result.existing)}." if result.existing else ".")
    return 0


def _device_package(
    src: str | None, tar: str | None, rev: str | None
) -> tuple[str, dict[str, bytes]]:
    """(rev, files) from a repo folder (git-tracked files) or a `git archive` tar."""
    if tar is not None:
        if not rev:
            raise PackageError("--tar needs --rev (the git short sha it was archived at)")
        data = sys.stdin.buffer.read() if tar == "-" else Path(tar).read_bytes()
        return rev, device_package.files_from_tar(data)
    folder = Path(src or "device").resolve()
    repo = device_package.repo_root(folder)
    rel = folder.relative_to(repo).as_posix()
    if rel != CODE_ROOT:
        raise PackageError(f"--from must be the repo's {CODE_ROOT}/ folder, got {rel}/")
    dirty = device_package.uncommitted(repo)
    if dirty:
        print(
            f"code: note: {len(dirty)} uncommitted change(s) under {CODE_ROOT}/ are left out "
            f"(packaged from git): {', '.join(dirty[:5])}",
            file=sys.stderr,
        )
    return device_package.from_git(repo, rev)


def code_register(
    settings: Settings, src: str | None, tar: str | None, rev: str | None, by: str
) -> int:
    """Package device/ and store it as a code version (B1); safe to repeat."""
    try:
        rev, files = _device_package(src, tar, rev)
    except (PackageError, OSError, ValueError) as exc:
        print(f"code register: {exc}", file=sys.stderr)
        return 2
    data = registry.build_tarball(files)
    engine = db.make_engine(settings.database_url)
    try:
        db.create_all(engine)
        with Session(engine) as session:
            row, created = registry.create_code(
                session,
                build_blobstore(settings.blob_dir),
                data,
                rev=rev,
                actor=f"human:{by}",
                actor_class="human",
                why=f"Registered the repo's {CODE_ROOT}/ at git commit {rev} ({len(files)} "
                "tracked files) as the real device code (B1).",
                source=seed_service.CODE_SOURCE_REPO,
                fleet_id=settings.fleet_id,
            )
            facts = f"{row.rev} ({len(files)} files, {row.size_bytes} bytes, {row.sha256})"
            session.commit()
    except ApiError as exc:
        print(f"code register: refused. {exc.detail}", file=sys.stderr)
        return 2
    finally:
        engine.dispose()
    what = "registered" if created else "already registered"
    print(f"code: {what} {facts}.")
    return 0


def code_check(src: str | None, image: str, test_cmd: str) -> int:
    """B1's proof: the package's own tests in the coder image with --network none."""
    try:
        rev, files = _device_package(src, None, None)
    except (PackageError, OSError, ValueError) as exc:
        print(f"code check: {exc}", file=sys.stderr)
        return 2
    run = device_check.run_package_tests(files, sandbox=Sandbox(image=image), test_cmd=test_cmd)
    passed, report = device_check.describe(run)
    print(f"code check: {CODE_ROOT}/ at {rev}, {len(files)} files, image {image}")
    print(report, end="")
    print("code check: PASS" if passed else "code check: FAIL")
    return 0 if passed else 1


def main(argv: list[str] | None = None, settings: Settings | None = None) -> int:
    parser = argparse.ArgumentParser(prog="romulus_cloud.cli")
    sub = parser.add_subparsers(dest="command", required=True)
    seed_cmd = sub.add_parser("seed", help="firmware v1, harness v1, arm-1 + sim-1..3 (M1)")
    seed_cmd.add_argument(
        "--bench",
        action="store_true",
        help="the real bench instead: arm-1 + one sim canary on the registered device/ code",
    )
    seed_cmd.add_argument("--code-rev", default=None, help="with --bench (default: newest)")
    code = sub.add_parser("code", help="the repo's device/ code (B1)")
    code_sub = code.add_subparsers(dest="code_command", required=True)
    register = code_sub.add_parser("register", help="package device/ and store a code version")
    where = register.add_mutually_exclusive_group()
    where.add_argument("--from", dest="src", default=None, help="the repo's device/ folder")
    where.add_argument("--tar", default=None, help="a `git archive` tar of device/ ('-': stdin)")
    register.add_argument("--rev", default=None, help="default: last commit touching device/")
    register.add_argument("--by", default=os.environ.get("USER") or "operator")
    check = code_sub.add_parser("check", help="run device/'s tests in the coder image, offline")
    check.add_argument("--from", dest="src", default=None, help="the repo's device/ folder")
    check.add_argument("--image", default=DEFAULT_IMAGE)
    check.add_argument("--test-cmd", default=DEFAULT_TEST_CMD)
    sub.add_parser("reset", help="drop and recreate the database; clear data/ (K7 guard)")
    digest_cmd = sub.add_parser("digest", help="counts since a time, from the event log (M7)")
    digest_cmd.add_argument(
        "--since", default=None, help="ISO time or 30m / 12h / 2d ago (default: overnight start)"
    )
    digest_cmd.add_argument("--until", default=None, help="ISO time or duration ago")
    digest_cmd.add_argument("--json", action="store_true", help="JSON instead of text")
    night = sub.add_parser("overnight", help="start / stop / status of the overnight flag (K7)")
    night.add_argument("action", choices=["start", "stop", "status"])
    night.add_argument("--by", default=os.environ.get("USER") or "operator")
    sub.add_parser("schema-check", help="fail if the database lacks a model's column")
    one = sub.add_parser("setting", help="print one setting as the app resolves it")
    one.add_argument("name", help="e.g. naive_mode or NAIVE_MODE")
    sub.add_parser("export", help="build requested dataset exports (M6)")
    args = parser.parse_args(argv)

    if args.command == "reset":
        force = os.environ.get("FORCE", "") == "1"
        return reset(settings or get_settings(), force=force)
    if args.command == "seed" and args.bench:
        return seed_bench(settings or get_settings(), args.code_rev)
    if args.command == "seed":
        return seed(settings or get_settings())
    if args.command == "code" and args.code_command == "register":
        return code_register(settings or get_settings(), args.src, args.tar, args.rev, args.by)
    if args.command == "code":
        return code_check(args.src, args.image, args.test_cmd)
    if args.command == "digest":
        return digest(settings or get_settings(), args.since, args.until, args.json)
    if args.command == "schema-check":
        return schema_check(settings or get_settings())
    if args.command == "setting":
        return setting(settings or get_settings(), args.name)
    if args.command == "overnight":
        return overnight(settings or get_settings(), args.action, args.by)
    return _not_yet("export", "M6")


if __name__ == "__main__":
    sys.exit(main())
