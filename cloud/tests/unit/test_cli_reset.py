import pytest
from sqlalchemy import func, inspect, select
from sqlalchemy.orm import Session

from romulus_cloud import cli, events
from romulus_cloud.models import Device, Event


def test_reset_refused_after_overnight_start_without_force(make_settings, monkeypatch, capsys):
    monkeypatch.delenv("FORCE", raising=False)
    # The DB URL points nowhere: a refusal must happen before any connection.
    s = make_settings(overnight_started=True)
    assert cli.main(["reset"], settings=s) == 2
    assert "refused" in capsys.readouterr().err


def test_reset_with_force_after_overnight_start(
    make_settings, test_database_url, engine, tmp_path, monkeypatch, now
):
    """A forced reset really wipes committed rows and blobs, then recreates the tables."""
    monkeypatch.setenv("FORCE", "1")
    with Session(engine) as session:  # committed for real, outside any test transaction
        session.add(Device(id="arm-reset", kind="real"))
        events.record(
            session,
            "human:kent",
            "human",
            "device.first_seen",
            {"device": "arm-reset"},
            "Seeded before reset.",
            ts=now,
        )
        session.commit()
    blobs = tmp_path / "blobs" / "sha256" / "ab"
    blobs.mkdir(parents=True)
    (blobs / "abcd").write_bytes(b"x")
    (tmp_path / "exports").mkdir()
    (tmp_path / "exports" / "corpus.jsonl").write_text("{}")

    s = make_settings(overnight_started=True, database_url=test_database_url)
    assert cli.main(["reset"], settings=s) == 0

    assert list((tmp_path / "blobs").iterdir()) == []
    assert list((tmp_path / "exports").iterdir()) == []
    assert "events" in inspect(engine).get_table_names()
    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(Device)) == 0
        assert session.scalar(select(func.count()).select_from(Event)) == 0


@pytest.mark.parametrize("command,milestone", [("digest", "M7"), ("export", "M6")])
def test_unbuilt_commands_say_so(command, milestone, settings, capsys):
    assert cli.main([command], settings=settings) == 1
    err = capsys.readouterr().err
    assert "not implemented yet" in err and milestone in err
