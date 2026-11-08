"""`cli code register` (make code-register) and `cli seed --bench` (make seed-bench), B1:
the real device/ registered from git, then arm-1 and one sim canary on it. Commits for real."""

import io
import subprocess
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from romulus_cloud import cli
from romulus_cloud.devicecode import package
from romulus_cloud.models import CodeVersion, Device, Event, FirmwareVersion

REPO = Path(__file__).resolve().parents[3]


def _events(engine):
    with Session(engine) as session:
        return session.scalar(select(func.count()).select_from(Event))


def test_register_device_from_git_then_seed_the_bench(
    make_settings, test_database_url, engine, capsys
):
    s = make_settings(database_url=test_database_url)
    rev = package.last_commit(REPO)
    try:
        assert cli.main(["code", "register", "--from", str(REPO / "device")], settings=s) == 0
        assert f"code: registered {rev} (" in capsys.readouterr().out
        with Session(engine) as session:
            row = session.get(CodeVersion, ("default", rev))
            assert row.source == "repo"
            created = session.scalars(select(Event).where(Event.kind == "code.version_created"))
            assert f"git commit {rev}" in created.one().why

        # The same commit through `--tar` (what `make code-register` pipes in) is a no-op.
        tar = subprocess.run(
            ["git", "-C", str(REPO), "archive", "--format=tar", rev, "--", "device"],
            capture_output=True,
            check=True,
        ).stdout
        before = _events(engine)
        argv = ["code", "register", "--tar", "-", "--rev", rev]
        stdin = type("Stdin", (), {"buffer": io.BytesIO(tar)})()
        real_stdin, cli.sys.stdin = cli.sys.stdin, stdin
        try:
            assert cli.main(argv, settings=s) == 0
        finally:
            cli.sys.stdin = real_stdin
        assert f"code: already registered {rev}" in capsys.readouterr().out
        assert _events(engine) == before

        assert cli.main(["seed", "--bench"], settings=s) == 0
        out = capsys.readouterr().out
        assert f"created harness v1, firmware v1 on code {rev}, arm-1, sim-1." in out
        assert cli.main(["seed", "--bench"], settings=s) == 0
        assert "seed --bench: created nothing; already there:" in capsys.readouterr().out
        with Session(engine) as session:
            kinds = session.execute(select(Device.id, Device.kind, Device.role)).all()
            assert sorted(kinds) == [("arm-1", "real", "normal"), ("sim-1", "sim", "canary")]
            assert session.get(FirmwareVersion, ("default", 1)).manifest["code_rev"] == rev
    finally:
        assert cli.main(["reset"], settings=s) == 0


def test_register_refuses_a_tar_without_a_rev_and_a_folder_that_isnt_device(
    make_settings, tmp_path, capsys
):
    s = make_settings()
    assert cli.main(["code", "register", "--tar", str(tmp_path / "x.tar")], settings=s) == 2
    assert "--tar needs --rev" in capsys.readouterr().err
    assert cli.main(["code", "register", "--from", str(REPO / "bench")], settings=s) == 2
    assert "must be the repo's device/ folder" in capsys.readouterr().err


def test_seed_bench_refuses_with_no_device_code(make_settings, test_database_url, capsys):
    s = make_settings(database_url=test_database_url)
    try:
        assert cli.main(["seed", "--bench"], settings=s) == 2
        assert "make code-register" in capsys.readouterr().err
    finally:
        assert cli.main(["reset"], settings=s) == 0
