"""Every setting the platform reads, from env or `.env` (architecture §6, BUILD §2 principle 4).

Fake modes are the defaults so `make up` works offline with no keys. Going live means
changing one variable per adapter.
"""

from functools import lru_cache
from typing import Literal

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Env names are the upper-case field names (`NAIVE_API_KEY`, `LLM_MODE`, ...).

    Reads the process env first, then `.env` in the working directory (the repo root for
    `uv run`; Compose passes the same file as `env_file`). Secrets are `SecretStr`, so they
    never show in logs or reprs; call `.get_secret_value()` at the one place that sends them.
    """

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Storage
    database_url: str = "postgresql+psycopg://romulus:romulus@db:5432/romulus"
    blob_dir: str = "/data/blobs"
    export_dir: str = "/data/exports"
    blob_budget_gb: float = 20

    # Tenancy (D1): one fleet for v0, but every table carries fleet_id.
    fleet_id: str = "default"

    # Naïve (naive-integration doc)
    naive_mode: Literal["fake", "live"] = "fake"
    naive_api_key: SecretStr = SecretStr("")
    naive_base_url: str = "https://api.vetta.sh/v1"
    naive_version: str = "2026-09-30"
    naive_board_id: str = ""
    naive_ops_agent: str = "fleet-ops"

    # Spend caps, checked in our code before a card is posted (naive-integration §3.4, D19b)
    order_max_usd: float = 50
    orders_per_day_max: int = 3
    orders_per_robot_day_max: int = 1

    # Batch-job LLM (D22): one OpenAI-compatible client for both live providers
    llm_mode: Literal["fake", "live"] = "fake"
    llm_provider: Literal["fake", "naive_proxy", "runpod"] = "fake"
    llm_base_url: str = ""
    llm_model: str = ""
    llm_api_key: SecretStr = SecretStr("")

    # Coding agent for code patches (D21, D22)
    coder_provider: Literal["fake", "local_agent", "naive_session"] = "fake"
    coder_agent: str = ""
    coder_image: str = ""

    # Paths under device/ that count as the safety layer or visor (D5a, K17). A code_patch
    # touching any of them is flagged `touches_safety`. Placeholder until Ishaan's list.
    safety_paths: list[str] = ["safety", "visor"]

    # Gate and batch timing (demo values; overnight uses 1200 / 3600)
    canary_window_s: int = 120
    batch_interval_s: int = 120

    # A pass-through code candidate stops at awaiting_canary unless this is true (BUILD §3).
    allow_passthrough_code_promotion: bool = False

    # Worker loop period (architecture §5)
    worker_tick_s: float = 2.0

    # Set once the overnight run starts; `reset` then refuses unless FORCE=1 (build plan K7).
    overnight_started: bool = False


@lru_cache
def get_settings() -> Settings:
    return Settings()
