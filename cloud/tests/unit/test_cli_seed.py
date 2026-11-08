"""`python -m romulus_cloud.cli seed` (make seed): commits the demo fleet, safe to repeat."""

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from romulus_cloud import cli
from romulus_cloud.models import Device, Event, FirmwareVersion


def test_cli_seed_commits_and_is_idempotent(make_settings, test_database_url, engine, capsys):
    s = make_settings(database_url=test_database_url)
    try:
        assert cli.main(["seed"], settings=s) == 0
        assert "created code c0de001, harness v1, firmware v1, arm-1" in capsys.readouterr().out
        with Session(engine) as session:
            events_after_first = session.scalar(select(func.count()).select_from(Event))
        assert cli.main(["seed"], settings=s) == 0
        assert "seed: created nothing; already there:" in capsys.readouterr().out
        with Session(engine) as session:
            assert session.scalar(select(func.count()).select_from(Device)) == 4
            assert session.get(FirmwareVersion, ("default", 1)).manifest["code_rev"] == "c0de001"
            assert session.scalar(select(func.count()).select_from(Event)) == events_after_first
    finally:
        # Committed for real (outside any test transaction), so wipe it for later tests.
        assert cli.main(["reset"], settings=s) == 0
