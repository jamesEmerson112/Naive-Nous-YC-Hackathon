import os
from pathlib import Path

import pytest

from romulus_cloud.settings import Settings

ARCH_ENV_VARS = [
    "DATABASE_URL",
    "BLOB_DIR",
    "BLOB_BUDGET_GB",
    "NAIVE_MODE",
    "NAIVE_API_KEY",
    "NAIVE_BASE_URL",
    "NAIVE_VERSION",
    "NAIVE_BOARD_ID",
    "NAIVE_OPS_AGENT",
    "ORDER_MAX_USD",
    "ORDERS_PER_DAY_MAX",
    "ORDERS_PER_ROBOT_DAY_MAX",
    "LLM_MODE",
    "LLM_PROVIDER",
    "LLM_BASE_URL",
    "LLM_MODEL",
    "LLM_API_KEY",
    "CODER_PROVIDER",
    "CODER_AGENT",
    "CODER_IMAGE",
    "CANARY_WINDOW_S",
    "BATCH_INTERVAL_S",
]


# conftest's autouse fixture unsets every settings variable before each test.


def test_defaults_are_fake_and_offline():
    s = Settings(_env_file=None)
    assert s.naive_mode == "fake"
    assert s.llm_mode == "fake" and s.llm_provider == "fake"
    assert s.coder_provider == "fake"
    assert s.database_url == "postgresql+psycopg://romulus:romulus@db:5432/romulus"
    assert s.blob_dir == "/data/blobs" and s.blob_budget_gb == 20
    assert s.naive_base_url == "https://api.vetta.sh/v1"
    assert s.naive_version == "2026-09-30"
    assert s.naive_ops_agent == "fleet-ops"
    assert (s.order_max_usd, s.orders_per_day_max, s.orders_per_robot_day_max) == (50, 3, 1)
    assert s.canary_window_s == 120 and s.batch_interval_s == 120
    assert s.allow_passthrough_code_promotion is False
    assert s.overnight_started is False


def test_fleet_doctor_defaults_are_off_and_safe():
    """interfaces.md §7: the Doctor ships off, never offers unchecked proposals, and
    discards every proposal until the safety paths are confirmed."""
    s = Settings(_env_file=None)
    assert s.doctor_mode == "off" and s.doctor_auto_consult is False
    assert (s.doctor_draft, s.doctor_check) == ("fake", "stub")
    assert s.doctor_offer_stub_checked is False and s.safety_paths_confirmed is False
    assert (s.doctor_asks_per_incident, s.doctor_asks_per_device_day) == (6, 30)
    assert s.doctor_consults_per_incident == 2
    assert (s.doctor_daily_usd_cap, s.doctor_usd_per_1k_tokens) == (5.0, 0.001)
    assert (s.doctor_ask_timeout_s, s.doctor_ask_max_tokens) == (15, 400)
    assert (s.doctor_diagnose_timeout_s, s.doctor_diagnose_max_tokens) == (120, 4000)
    assert (s.doctor_proposal_ttl_s, s.doctor_lookback_s) == (3600, 21600)
    assert (s.doctor_tick_s, s.doctor_max_open, s.doctor_job_workers) == (5, 5, 2)


def test_doctor_modes_are_closed_sets(monkeypatch):
    monkeypatch.setenv("DOCTOR_MODE", "turbo")
    with pytest.raises(ValueError, match="doctor_mode"):
        Settings(_env_file=None)


def test_every_architecture_variable_is_a_setting():
    fields = {name.upper() for name in Settings.model_fields}
    assert set(ARCH_ENV_VARS) <= fields


def test_secrets_never_show_in_repr(monkeypatch):
    monkeypatch.setenv("NAIVE_API_KEY", "sk_live_supersecret")
    monkeypatch.setenv("LLM_API_KEY", "llm-supersecret")
    s = Settings(_env_file=None)
    assert "supersecret" not in repr(s) and "supersecret" not in str(s.model_dump())
    assert s.naive_api_key.get_secret_value() == "sk_live_supersecret"
    assert s.llm_api_key.get_secret_value() == "llm-supersecret"


def test_env_overrides_one_variable_per_adapter(monkeypatch):
    monkeypatch.setenv("NAIVE_MODE", "live")
    monkeypatch.setenv("CANARY_WINDOW_S", "1200")
    s = Settings(_env_file=None)
    assert s.naive_mode == "live" and s.canary_window_s == 1200


def test_test_settings_ignore_dotenv_and_stay_fake(make_settings, tmp_path, monkeypatch):
    """A real `.env` with live modes must not leak into tests."""
    (tmp_path / ".env").write_text(
        "NAIVE_MODE=live\nLLM_MODE=live\nLLM_PROVIDER=naive_proxy\n"
        "CODER_PROVIDER=local_agent\nNAIVE_API_KEY=sk_live_from_dotenv\n"
    )
    monkeypatch.chdir(tmp_path)
    assert Settings().naive_mode == "live"  # the plain loader does read it ...
    s = make_settings()  # ... the test loader doesn't
    assert s.naive_mode == "fake" and s.llm_mode == "fake"
    assert s.llm_provider == "fake" and s.coder_provider == "fake"
    assert s.naive_api_key.get_secret_value() == ""


def test_test_settings_ignore_exported_shell_variables(make_settings, tmp_path, monkeypatch):
    """`set -a; . .env` or direnv exports the real key: test settings still ignore it."""
    monkeypatch.setenv("NAIVE_API_KEY", "sk_live_REAL")
    monkeypatch.setenv("NAIVE_MODE", "live")
    monkeypatch.setenv("LLM_MODE", "live")
    monkeypatch.setenv("CANARY_WINDOW_S", "9999")
    monkeypatch.setenv("OVERNIGHT_STARTED", "true")
    (tmp_path / ".env").write_text("NAIVE_MODE=live\nNAIVE_API_KEY=sk_live_from_dotenv\n")
    monkeypatch.chdir(tmp_path)
    assert Settings().naive_api_key.get_secret_value() == "sk_live_REAL"  # plain loader reads it
    s = make_settings(naive_mode="live")
    assert s.naive_mode == "fake" and s.llm_mode == "fake"
    assert s.naive_api_key.get_secret_value() == ""
    assert s.canary_window_s == 120 and s.overnight_started is False


def test_autouse_fixture_unset_every_settings_variable():
    """No settings variable from the developer's shell survives into a test."""
    leaked = [n.upper() for n in Settings.model_fields if n.upper() in os.environ]
    assert leaked == [] and "FORCE" not in os.environ


def test_env_example_lists_every_setting():
    path = Path(__file__).resolve().parents[3] / ".env.example"
    keys = set()
    for line in path.read_text().splitlines():
        line = line.strip().lstrip("# ").strip()
        if "=" in line and line.split("=", 1)[0].isupper():
            keys.add(line.split("=", 1)[0])
    compose_only = {"COMPOSE_PROJECT_NAME", "API_PORT", "FAKE_DEVICES_N"}
    fields = {name.upper() for name in Settings.model_fields}
    assert fields <= keys, f"missing from .env.example: {sorted(fields - keys)}"
    assert keys - compose_only <= fields, f"unknown in .env.example: {sorted(keys - fields)}"
