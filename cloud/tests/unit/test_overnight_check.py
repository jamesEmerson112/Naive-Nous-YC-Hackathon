"""`make overnight-check` asks the app's own settings for the Naïve mode (K13), so a
lowercase `naive_mode=live` can't slip past it. The script runs against a stub `docker`."""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from romulus_cloud import cli
from romulus_cloud.settings import get_settings

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "overnight_check.sh"

STUB = r"""#!/usr/bin/env bash
a="$*"
case "$1" in
  info) exit 0 ;;
  inspect)
    case "$a" in
      *Args*) echo "-m fake_device --chaos=overnight" ;;
      *) echo "running healthy unless-stopped" ;;
    esac ;;
  compose)
    case "$a" in
      *"worker python -m romulus_cloud.cli setting naive_mode"*) echo "$STUB_WORKER_NAIVE" ;;
      *"api python -m romulus_cloud.cli setting naive_mode"*) echo "$STUB_API_NAIVE" ;;
      *"setting canary_window_s"*) echo 1200 ;;
      *"setting batch_interval_s"*) echo 3600 ;;
      *"setting overnight_started"*) echo True ;;
      *"setting llm_mode"*) echo fake ;;
      *"overnight status"*) echo "overnight: started at 2026-11-08T08:30:00+00:00" ;;
      *" ps "*) echo cid ;;
      *logs*) echo '"worker.step"' ;;
    esac ;;
esac
"""


@pytest.fixture
def lowercase_env(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)  # no .env
    get_settings.cache_clear()
    yield monkeypatch
    get_settings.cache_clear()


def test_setting_prints_what_the_app_resolves(lowercase_env, capsys):
    lowercase_env.setenv("naive_mode", "live")  # lowercase: printenv NAIVE_MODE misses it
    assert cli.main(["setting", "NAIVE_MODE"]) == 0
    assert capsys.readouterr().out.strip() == "live"


def test_setting_hides_secrets_and_rejects_unknown_names(lowercase_env, capsys):
    lowercase_env.setenv("NAIVE_API_KEY", "sk-very-secret")
    assert cli.main(["setting", "naive_api_key"]) == 0
    assert capsys.readouterr().out.strip() == "<secret>"
    assert cli.main(["setting", "nope"]) == 2


def run_check(tmp_path, api: str, worker: str) -> str:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    stub = bin_dir / "docker"
    stub.write_text(STUB)
    stub.chmod(0o755)
    env = {
        **os.environ,
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "COMPOSE": "docker compose",
        "API_PORT": "1",
        "STUB_API_NAIVE": api,
        "STUB_WORKER_NAIVE": worker,
    }
    out = subprocess.run(
        ["bash", str(SCRIPT)], env=env, capture_output=True, text=True, timeout=60, cwd=tmp_path
    )
    return out.stdout


@pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")
@pytest.mark.parametrize(
    "api,worker,expected",
    [
        (
            "fake",
            "fake",
            [
                "PASS  NAIVE_MODE resolves to fake in api",
                "PASS  NAIVE_MODE resolves to fake in worker",
            ],
        ),
        ("live", "fake", ["FAIL  NAIVE_MODE resolves to 'live' in api"]),
        ("fake", "live", ["FAIL  NAIVE_MODE resolves to 'live' in worker"]),
        ("", "fake", ["FAIL  can't read the effective NAIVE_MODE in api"]),
    ],
)
def test_check_fails_unless_both_containers_resolve_fake(tmp_path, api, worker, expected):
    out = run_check(tmp_path, api, worker)
    for line in expected:
        assert line in out, out
    assert "PASS  canary_window_s=1200" in out and "PASS  overnight_started=True" in out
