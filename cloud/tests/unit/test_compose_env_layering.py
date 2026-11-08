"""compose.yaml layers `.env.overnight` over `.env` (M7): the overnight values win in api,
worker and fake-devices, and `.env` itself is never edited. Checked with `docker compose
config` on a copy of compose.yaml in a temp dir (no containers start)."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]

pytestmark = pytest.mark.skipif(shutil.which("docker") is None, reason="needs docker compose")


def config(tmp_path: Path) -> dict:
    out = subprocess.run(
        ["docker", "compose", "-p", "m7-env-layering-test", "--profile", "sim"]
        + ["config", "--format", "json"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=60,
    )
    if out.returncode != 0 and "docker" in out.stderr.lower() and "compose" in out.stderr.lower():
        pytest.skip(f"docker compose unavailable: {out.stderr[:200]}")
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


@pytest.fixture
def project(tmp_path: Path) -> Path:
    shutil.copy(ROOT / "compose.yaml", tmp_path / "compose.yaml")
    (tmp_path / ".env").write_text(
        "NAIVE_MODE=live\nNAIVE_API_KEY=real-key\nBATCH_INTERVAL_S=120\nLLM_MODE=live\n"
    )
    return tmp_path


def test_overnight_values_win_over_env(project):
    shutil.copy(ROOT / ".env.overnight.example", project / ".env.overnight")
    services = config(project)["services"]
    for name in ("api", "worker", "fake-devices"):
        env = services[name]["environment"]
        assert env["NAIVE_MODE"] == "fake", name
        assert env["BATCH_INTERVAL_S"] == "3600" and env["LLM_MODE"] == "fake", name
        assert env["NAIVE_API_KEY"] == "real-key", name  # the key still comes from .env
        assert env["OVERNIGHT_STARTED"] == "true", name
        assert env["DOCTOR_MODE"] == "off", name  # the Fleet Doctor stays off overnight
    assert (project / ".env").read_text().startswith("NAIVE_MODE=live")  # untouched


def test_without_the_overnight_file_env_applies(project):
    env = config(project)["services"]["api"]["environment"]
    assert env["NAIVE_MODE"] == "live" and env["BATCH_INTERVAL_S"] == "120"


def test_overnight_file_is_git_ignored_but_the_example_is_not():
    def ignored(path: str) -> bool:
        cmd = ["git", "-C", str(ROOT), "check-ignore", "-q", path]
        return subprocess.run(cmd).returncode == 0

    assert ignored(".env.overnight") and ignored(".env")
    assert not ignored(".env.overnight.example")
