"""domain/coder_retry: a provider blip is retried with a bounded backoff; a bad draft is not."""

from datetime import UTC, datetime, timedelta

import pytest

from romulus_cloud.domain.coder_retry import (
    MAX_TRANSIENT,
    CoderOutcome,
    classify,
    retry_at,
    retry_delay_s,
)

CONN, NO_CALL = "connection or timeout", "the agent made no LLM call"
NOW = datetime(2026, 11, 7, 22, 0, tzinfo=UTC)
# Run #2 of PR #38, verbatim: the Naïve proxy at capacity, 1 step, 0 tokens.
RUN_2 = CoderOutcome(
    passed=False,
    exit_status="RateLimitError",
    error="litellm.RateLimitError: RateLimitError: OpenAIException - the service is at capacity",
    steps=1,
    tokens=0,
)


def test_real_run_2_is_a_transient_rate_limit():
    v = classify(RUN_2)
    assert v.transient and v.label == "rate limited"


@pytest.mark.parametrize(
    "outcome,label",
    [
        (
            CoderOutcome(False, error="Error code: 429 - Too Many Requests", tokens=900),
            "rate limited",
        ),
        (CoderOutcome(False, error="the service is at capacity"), "rate limited"),
        (CoderOutcome(False, exit_status="InternalServerError", tokens=10), "provider error"),
        (CoderOutcome(False, error="Error code: 503 Service Unavailable"), "provider error"),
        (CoderOutcome(False, error="502 Bad Gateway"), "provider error"),
        (CoderOutcome(False, exit_status="Timeout", error="litellm.Timeout"), CONN),
        (CoderOutcome(False, error="OpenAIException - Connection error."), CONN),
        (CoderOutcome(False, no_result=True), "no result from the sandbox"),
        (CoderOutcome(False, exit_status="Submitted", steps=3, tokens=0), NO_CALL),
        (CoderOutcome(False, exit_status="FormatError", steps=0, tokens=None), NO_CALL),
    ],
)
def test_transient_failures(outcome, label):
    v = classify(outcome)
    assert v.kind == "transient" and v.label == label


@pytest.mark.parametrize(
    "outcome",
    [
        # the draft ran and was bad: failing tests, no change, a refused diff
        CoderOutcome(False, exit_status="Submitted", steps=7, tokens=1500),
        CoderOutcome(False, exit_status="LimitsExceeded", steps=15, tokens=30000),
        # the coder can't be built from the settings (a job exception, no tokens known)
        CoderOutcome(False, error="CODER_PROVIDER=local_agent needs LLM_BASE_URL and LLM_MODEL"),
        CoderOutcome(False, error="the coder's diff does not apply to the base code"),
    ],
)
def test_permanent_failures(outcome):
    assert classify(outcome).kind == "permanent"


def test_a_passing_draft_is_ok_even_with_noise_in_its_error():
    assert classify(CoderOutcome(True, error="429 once, then fine", tokens=0)).kind == "ok"


def test_backoff_doubles_from_two_minutes_and_caps_at_thirty():
    assert [retry_delay_s(n) for n in range(1, 7)] == [120, 240, 480, 960, 1800, 1800]


def test_retry_at_gives_up_at_the_fifth_transient_failure():
    assert MAX_TRANSIENT == 5
    assert retry_at(1, NOW) == NOW + timedelta(minutes=2)
    assert retry_at(4, NOW) == NOW + timedelta(minutes=16)
    assert retry_at(5, NOW) is None
