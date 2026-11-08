import pytest
from sqlalchemy import create_engine, inspect

from romulus_cloud import cli


def test_reset_refused_after_overnight_start_without_force(make_settings, monkeypatch, capsys):
    monkeypatch.delenv("FORCE", raising=False)
    # The DB URL points nowhere: a refusal must happen before any connection.
    s = make_settings(overnight_started=True)
    assert cli.main(["reset"], settings=s) == 2
    assert "refused" in capsys.readouterr().err


def test_reset_with_force_after_overnight_start(
    make_settings, test_database_url, tmp_path, monkeypatch
):
    monkeypatch.setenv("FORCE", "1")
    blobs = tmp_path / "blobs" / "sha256" / "ab"
    blobs.mkdir(parents=True)
    (blobs / "abcd").write_bytes(b"x")
    s = make_settings(overnight_started=True, database_url=test_database_url)
    assert cli.main(["reset"], settings=s) == 0
    assert list((tmp_path / "blobs").iterdir()) == []
    eng = create_engine(test_database_url)
    assert "events" in inspect(eng).get_table_names()
    eng.dispose()


@pytest.mark.parametrize("command,milestone", [("seed", "M1"), ("digest", "M7"), ("export", "M6")])
def test_unbuilt_commands_say_so(command, milestone, settings, capsys):
    assert cli.main([command], settings=settings) == 1
    err = capsys.readouterr().err
    assert "not implemented yet" in err and milestone in err
