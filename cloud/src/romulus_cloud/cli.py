"""Operator commands: `python -m romulus_cloud.cli {seed,reset,digest,export}` (architecture §8)."""

import argparse
import os
import shutil
import sys
from pathlib import Path

from romulus_cloud import db
from romulus_cloud.settings import Settings, get_settings


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

    Refused once the overnight run has started, unless forced (build plan K7).
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
        db.drop_all(engine)
        db.create_all(engine)
    finally:
        engine.dispose()
    _clear_dir(settings.blob_dir)
    _clear_dir(settings.export_dir)
    print("reset: database recreated; blob and export dirs emptied.")
    return 0


def main(argv: list[str] | None = None, settings: Settings | None = None) -> int:
    parser = argparse.ArgumentParser(prog="romulus_cloud.cli")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("seed", help="firmware v1, harness v1, arm-1 + sim-1..3 (M1)")
    sub.add_parser("reset", help="drop and recreate the database; clear data/ (K7 guard)")
    digest = sub.add_parser("digest", help="counts since a time, from the event log (M7)")
    digest.add_argument("--since", default=None)
    sub.add_parser("export", help="build requested dataset exports (M6)")
    args = parser.parse_args(argv)

    if args.command == "reset":
        force = os.environ.get("FORCE", "") == "1"
        return reset(settings or get_settings(), force=force)
    if args.command == "seed":
        return _not_yet("seed", "M1")
    if args.command == "digest":
        return _not_yet("digest", "M7")
    return _not_yet("export", "M6")


if __name__ == "__main__":
    sys.exit(main())
