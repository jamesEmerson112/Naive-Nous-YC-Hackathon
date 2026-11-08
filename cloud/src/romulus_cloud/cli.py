"""Operator commands: `python -m romulus_cloud.cli {seed,reset,digest,overnight,export}`
(architecture §8)."""

import argparse
import json
import os
import shutil
import sys
from pathlib import Path

from pydantic import SecretStr
from sqlalchemy.exc import ProgrammingError, SQLAlchemyError
from sqlalchemy.orm import Session

from romulus_cloud import db
from romulus_cloud.adapters.blobstore import build_blobstore
from romulus_cloud.errors import ApiError
from romulus_cloud.ids import utcnow
from romulus_cloud.services import digest as digest_service
from romulus_cloud.services import overnight as overnight_service
from romulus_cloud.services.seed import seed as seed_fleet
from romulus_cloud.settings import Settings, get_settings

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


def main(argv: list[str] | None = None, settings: Settings | None = None) -> int:
    parser = argparse.ArgumentParser(prog="romulus_cloud.cli")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("seed", help="firmware v1, harness v1, arm-1 + sim-1..3 (M1)")
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
    one = sub.add_parser("setting", help="print one setting as the app resolves it")
    one.add_argument("name", help="e.g. naive_mode or NAIVE_MODE")
    sub.add_parser("export", help="build requested dataset exports (M6)")
    args = parser.parse_args(argv)

    if args.command == "reset":
        force = os.environ.get("FORCE", "") == "1"
        return reset(settings or get_settings(), force=force)
    if args.command == "seed":
        return seed(settings or get_settings())
    if args.command == "digest":
        return digest(settings or get_settings(), args.since, args.until, args.json)
    if args.command == "setting":
        return setting(settings or get_settings(), args.name)
    if args.command == "overnight":
        return overnight(settings or get_settings(), args.action, args.by)
    return _not_yet("export", "M6")


if __name__ == "__main__":
    sys.exit(main())
