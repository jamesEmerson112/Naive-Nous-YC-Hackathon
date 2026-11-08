"""`make worker-host` (M8b): the host worker refuses during the overnight run unless forced."""

import pytest

from romulus_cloud.models import FleetState
from romulus_cloud.worker import host_refusal, main


def test_no_fleet_row_or_no_overnight_run_lets_it_start(db_session, settings):
    assert host_refusal(db_session, settings, force=False) is None
    db_session.add(FleetState(fleet_id="default"))
    db_session.flush()
    assert host_refusal(db_session, settings, force=False) is None


def test_overnight_started_in_the_db_refuses_unless_forced(db_session, settings, now):
    db_session.add(FleetState(fleet_id="default", overnight_started_at=now))
    db_session.flush()
    why = host_refusal(db_session, settings, force=False)
    assert "refused" in why and "Never use worker-host overnight" in why
    assert now.isoformat() in why
    assert host_refusal(db_session, settings, force=True) is None


def test_the_overnight_env_flag_refuses_too(db_session, make_settings):
    flagged = make_settings(overnight_started=True)
    assert "OVERNIGHT_STARTED is set" in host_refusal(db_session, flagged, force=False)


def test_main_parses_the_host_flags(monkeypatch):
    """No DB here: the parser runs before anything connects."""
    with pytest.raises(SystemExit):
        main(["--help"])
