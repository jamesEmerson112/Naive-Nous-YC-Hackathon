"""Is a failed coder draft worth retrying? (BUILD §8: retry "when Naïve or the LLM flakes".)

The first real coder run on the device code (B1, PR #38) never reached the model: the Naïve
proxy answered "the service is at capacity" (a 429) and the agent stopped with 0 tokens. Marked
sticky `lockin_error`, one provider blip would lose that fix for good. So:

- **transient**: the provider or the plumbing failed, not the draft. A rate limit, a 429,
  "at capacity", a 5xx, a timeout or connection error, a sandbox that returned no result, or an
  agent that stopped with zero tokens or zero steps. The overlay stays held and is retried
  after a backoff (2 min, doubling, capped at 30 min), at most `MAX_TRANSIENT` times.
- **permanent**: the draft ran and was bad (failing tests, no change, a bad or refused diff),
  or the coder can't be built from the settings. Sticky at once, as before.

Pure: no clock or DB; the caller passes `now` and the attempt count.
"""

import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal

MAX_TRANSIENT = 5
FIRST_DELAY_S = 120
MAX_DELAY_S = 1800

# (label for the why, pattern over the agent's exit status and error text)
_TRANSIENT = [
    ("rate limited", re.compile(r"RateLimit|\b429\b|at capacity|too many requests", re.I)),
    (
        "provider error",
        re.compile(
            r"InternalServerError|ServiceUnavailable|BadGateway|GatewayTimeout|\b50[0234]\b|"
            r"overloaded",
            re.I,
        ),
    ),
    (
        "connection or timeout",
        re.compile(r"Timeout|timed out|APIConnectionError|Connection error|ConnectionError", re.I),
    ),
]


@dataclass(frozen=True)
class CoderOutcome:
    """What a draft attempt reported: the `CodeDraft` fields, or a job's exception text."""

    passed: bool
    exit_status: str = ""
    error: str = ""
    steps: int | None = None
    tokens: int | None = None  # prompt + completion; None when unknown
    no_result: bool = False  # the sandbox returned no result at all


@dataclass(frozen=True)
class Verdict:
    kind: Literal["ok", "transient", "permanent"]
    label: str = ""  # e.g. "rate limited", for "coder unavailable (rate limited)"

    @property
    def transient(self) -> bool:
        return self.kind == "transient"


def classify(o: CoderOutcome) -> Verdict:
    if o.passed:
        return Verdict("ok")
    text = f"{o.exit_status} {o.error}"
    for label, pattern in _TRANSIENT:
        if pattern.search(text):
            return Verdict("transient", label)
    if o.no_result:
        return Verdict("transient", "no result from the sandbox")
    if o.tokens == 0 or o.steps == 0:
        return Verdict("transient", "the agent made no LLM call")
    return Verdict("permanent")


def retry_delay_s(attempt: int) -> int:
    """Wait after transient failure number `attempt` (1-based): 2, 4, 8, 16, then 30 min."""
    return min(FIRST_DELAY_S * 2 ** max(attempt - 1, 0), MAX_DELAY_S)


def retry_at(attempt: int, now: datetime) -> datetime | None:
    """When to retry after transient failure `attempt`, or None: give up (sticky)."""
    if attempt >= MAX_TRANSIENT:
        return None
    return now + timedelta(seconds=retry_delay_s(attempt))
