"""Every setting the platform reads, from env or `.env` (architecture §6, BUILD §2 principle 4).

Fake modes are the defaults so `make up` works offline with no keys. Going live means
changing one variable per adapter.
"""

import re
from functools import lru_cache
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# `python[3[.x]] [flags] -m pytest` anywhere in a shell command: what the coder image's
# `pin_pytest` (cloud/coder/run_agent.py) rewrites to its sealed launcher. Keep them equal.
SEALABLE_PYTEST = r"(?<![^\s;&|(])((?:\S*/)?python3?(?:\.\d+)?)((?:\s+-[A-Za-z]+)*?)\s+-m\s+pytest"
SEALABLE_PYTEST += r"(?=$|[\s;&|)])"


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
    # Largest recording blob accepted in one PUT (telemetry §3: v0 stays under ~200 MB).
    recording_max_bytes: int = 200 * 1024 * 1024

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
    # The day the per-day caps count in (an IANA zone name). UTC unless set.
    fleet_timezone: str = "UTC"

    # What the work-order card says (M5, naive-integration §3.3). The phone fleet-ops calls
    # comes from here, never from parts.yaml, and must be a teammate's phone, never a
    # business (K13). Live mode refuses to post while it is still the placeholder.
    work_order_phone: str = "TEAMMATE_PHONE"
    work_order_deliver_to: str = "Romulus bench (set WORK_ORDER_DELIVER_TO)"

    @field_validator("fleet_timezone")
    @classmethod
    def _known_timezone(cls, value: str) -> str:
        """A bad zone fails at startup, not silently on every triage tick."""
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError(
                f"FLEET_TIMEZONE {value!r} is not a known IANA zone (e.g. UTC, America/Los_Angeles)"
            ) from exc
        return value

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
    # local_agent: the device test command run after the agent, and the agent's limits
    coder_test_cmd: str = "python -m pytest -q"
    coder_step_limit: int = 30
    coder_time_limit_s: int = 600

    @field_validator("coder_test_cmd")
    @classmethod
    def _sealable_test_cmd(cls, value: str) -> str:
        """Empty, or a command that runs `python [flags] -m pytest ...` somewhere: the one form
        the coder image runs through its result seal (`cloud/coder/run_agent.py`,
        `pin_pytest`; the same pattern). Any other way of starting the tests (bare `pytest`,
        `uv run pytest`, `make test`) would be judged by its exit code alone, so it fails at
        startup instead."""
        if value.strip() and not re.search(SEALABLE_PYTEST, value):
            raise ValueError(
                f"CODER_TEST_CMD {value!r} can't be sealed: the coder image only seals the "
                "results of `python -m pytest ...` (e.g. `python -m pytest -q`); use that form, "
                "or leave it empty to rely on the held-out verify scripts alone"
            )
        return value

    # The gate's code replay (M8b): the device tests (CODER_TEST_CMD, empty = no replay, the
    # stub) and the held-out verify scripts, in fresh containers with no network. The
    # package's own `device/verify/` always runs; CODER_VERIFY_DIR adds a host directory of
    # more verify scripts kept off the device (each file lands at `verify/<its path>`).
    coder_verify_dir: str = ""
    coder_replay_timeout_s: int = 300
    # Where scratch copies of the device code go ("" = the system temp dir). Docker must be
    # able to mount it (Docker Desktop shares /Users, /private, /tmp and /var/folders).
    coder_scratch_dir: str = ""
    # Threads in the worker for slow jobs, per pool (coder drafts and code replays each have
    # their own, so a long draft never queues a replay; services/jobs.py).
    jobs_max_workers: int = 2
    # Coding-agent drafts started per hour, fleet-wide (identical patches share one draft).
    # Over it, a code overlay waits `held` with the why "coder budget".
    coder_max_drafts_per_hour: int = 6

    # Paths under device/ that count as the safety layer or visor (D5a, K17). A code_patch
    # touching any of them is flagged `touches_safety`. Placeholder until Ishaan's list.
    safety_paths: list[str] = ["safety", "visor"]

    # Gate and batch timing (demo values; overnight uses 1200 / 3600)
    canary_window_s: int = 120
    batch_interval_s: int = 120

    # A pass-through code candidate stops at awaiting_canary unless this is true (BUILD §3).
    allow_passthrough_code_promotion: bool = False

    # Time zone the pages show times in (the venue's; D10 "times are local and readable")
    display_tz: str = "America/Los_Angeles"

    # Worker loop period (architecture §5)
    worker_tick_s: float = 2.0

    # Set once the overnight run starts; `reset` then refuses unless FORCE=1 (build plan K7).
    overnight_started: bool = False

    # Fleet Doctor (docs/fleet-doctor/interfaces.md §7; D23-D26). Off by default: the ask
    # route answers 503 and the worker's doctor.tick does nothing.
    # off | fake (FakeLlm) | live (the LLM_* provider above)
    doctor_mode: Literal["off", "fake", "live"] = "off"
    # The cloud opens consults on unresolved non-hardware incidents by itself (D26).
    doctor_auto_consult: bool = False
    doctor_draft: Literal["none", "fake", "coder"] = "fake"
    doctor_check: Literal["stub", "sandbox"] = "stub"
    # Offer proposals whose check is only a stub (demos and F12); the event says so.
    doctor_offer_stub_checked: bool = False
    # False until the K17 list replaces `safety_paths`; while false every proposal is
    # discarded with "safety paths unknown" (D25).
    safety_paths_confirmed: bool = False
    doctor_asks_per_incident: int = 6
    doctor_asks_per_device_day: int = 30  # asks with no incident
    doctor_consults_per_incident: int = 2
    # Daily spend across asks and consults, checked before spending.
    doctor_daily_usd_cap: float = 5.0
    doctor_usd_per_1k_tokens: float = 0.001  # an estimate (inferred); set the real price
    doctor_ask_timeout_s: float = 15
    doctor_ask_max_tokens: int = 400
    doctor_diagnose_timeout_s: float = 120
    doctor_diagnose_max_tokens: int = 4000
    doctor_proposal_ttl_s: int = 3600
    # Consults only open for incidents received this recently: never backfill history.
    doctor_lookback_s: int = 21600
    doctor_tick_s: float = 5
    doctor_max_open: int = 5  # consults advanced per tick
    doctor_job_workers: int = 2  # threads for off-tick LLM and coder jobs
    # DOCTOR_DRAFT=coder: what one coder draft is assumed to cost (checked against the daily
    # cap before each draft, and recorded when the coder doesn't report its tokens). An
    # estimate (inferred); drafts also count against CODER_MAX_DRAFTS_PER_HOUR.
    doctor_draft_estimate_usd: float = 0.10
    # The case file's evidence (PR 7): the agent-trace tail and code excerpts at the base rev.
    doctor_trace_tail_lines: int = 40
    doctor_trace_tail_bytes: int = 16000
    doctor_evidence_file_bytes: int = 4000  # per file named in the log or trace
    doctor_evidence_code_bytes: int = 16000  # all code excerpts together


@lru_cache
def get_settings() -> Settings:
    return Settings()
