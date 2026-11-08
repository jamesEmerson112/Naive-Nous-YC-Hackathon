"""`cli overnight start|stop|status`, the DB overnight guard on `reset` (K7), `cli digest`,
and `GET /v1/digest`. The CLI commits for real, so each CLI test ends with a forced reset
that leaves the session database empty again."""

import json
from datetime import timedelta

import pytest
from sqlalchemy.orm import Session

from romulus_cloud import cli, events
from romulus_cloud.ids import utcnow


@pytest.fixture
def real_db(make_settings, test_database_url, engine, monkeypatch):
    s = make_settings(database_url=test_database_url)
    yield s
    monkeypatch.setenv("FORCE", "1")
    assert cli.main(["reset"], settings=s) == 0


def test_overnight_flag_blocks_reset_until_stopped(real_db, capsys, monkeypatch):
    monkeypatch.delenv("FORCE", raising=False)
    assert cli.main(["overnight", "status"], settings=real_db) == 0
    assert "overnight: off." in capsys.readouterr().out

    assert cli.main(["overnight", "start", "--by", "kent"], settings=real_db) == 0
    assert "reset now refuses" in capsys.readouterr().out
    assert cli.main(["overnight", "status"], settings=real_db) == 0
    assert "started at" in capsys.readouterr().out

    assert cli.main(["reset"], settings=real_db) == 2
    err = capsys.readouterr().err
    assert "refused" in err and "overnight stop" in err

    assert cli.main(["overnight", "stop", "--by", "kent"], settings=real_db) == 0
    assert "reset is allowed again" in capsys.readouterr().out
    assert cli.main(["reset"], settings=real_db) == 0


def test_force_resets_even_with_the_db_flag(real_db, monkeypatch, capsys):
    assert cli.main(["overnight", "start"], settings=real_db) == 0
    monkeypatch.setenv("FORCE", "1")
    assert cli.main(["reset"], settings=real_db) == 0
    assert "database recreated" in capsys.readouterr().out


def test_guard_reads_a_missing_table_as_not_started(test_database_url, engine):
    """A database made before M7 (no fleet_state table or column) must not crash reset."""
    from sqlalchemy import text

    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE fleet_state RENAME TO fleet_state_hidden"))
    try:
        assert cli._overnight_started_at(engine, "default") is None
    finally:
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE fleet_state_hidden RENAME TO fleet_state"))


def test_digest_cli_text_and_json(real_db, engine, capsys):
    now = utcnow()
    with Session(engine) as session:
        for kind, cls in [
            ("incident.reported", "automation"),
            ("incident.agent_acted", "agent"),
            ("candidate.promoted", "automation"),
        ]:
            events.record(session, "device:sim-1", cls, kind, {}, "x", ts=now - timedelta(hours=1))
        session.commit()

    assert cli.main(["digest", "--since", "2h"], settings=real_db) == 0
    out = capsys.readouterr().out
    assert out.startswith("Romulus digest:")
    assert "1 incidents" in out and "0 human actions" in out and "Blob store:" in out

    assert cli.main(["digest", "--since", "2h", "--json"], settings=real_db) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["incidents"] == 1 and data["actions_by_class"]["agent"] == 1

    assert cli.main(["digest", "--since", "30m"], settings=real_db) == 0
    assert "0 incidents" in capsys.readouterr().out

    assert cli.main(["digest", "--since", "last tuesday"], settings=real_db) == 2
    assert "neither an ISO time" in capsys.readouterr().err


def test_digest_route(client, db_session):
    now = utcnow()
    events.record(db_session, "device:sim-1", "agent", "incident.agent_acted", {}, "x", ts=now)
    db_session.commit()
    body = client.get("/v1/digest", params={"since": "1h"}).json()
    assert body["actions_by_class"]["agent"] == 1 and body["no_human_share"] == 1.0
    assert body["text"].startswith("Romulus digest:")
    assert "storage" in body and body["human_actions"] == []

    bad = client.get("/v1/digest", params={"since": "soon"})
    assert bad.status_code == 422 and bad.json()["error"] == "bad_since"
