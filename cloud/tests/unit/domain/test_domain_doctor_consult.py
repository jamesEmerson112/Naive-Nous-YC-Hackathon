"""domain/doctor.py, consult rules (PR 4): how a waiting consult ends, what follows, and
what a diagnose call may cost."""

import math

import pytest

from romulus_cloud.domain.doctor import (
    BLOCKED_OVER_CAP,
    DIAGNOSE_SYSTEM,
    GAVE_UP,
    HELD_OVERLAY,
    MAX_EVIDENCE_CHARS,
    RETRY_OUTCOMES,
    Spend,
    admit,
    after_close,
    applied_outcome,
    cost_usd,
    diagnose_estimate_usd,
    estimate_tokens,
    failed_run_cost_usd,
)


def test_estimate_tokens_is_len_over_4_and_never_zero():
    assert estimate_tokens(400) == 100
    assert estimate_tokens(0) == 1 and estimate_tokens(3) == 1


def test_the_diagnose_estimate_covers_the_largest_prompt_and_the_completion_budget():
    est = diagnose_estimate_usd(4000, 0.001)
    biggest_prompt = estimate_tokens(len(DIAGNOSE_SYSTEM) + MAX_EVIDENCE_CHARS)
    assert est > cost_usd(biggest_prompt, 4000, 0.001)
    assert diagnose_estimate_usd(0, 0.001) > 0  # the prompt alone costs something
    assert diagnose_estimate_usd(4000, 0.0) == 0.0


@pytest.mark.parametrize("price", [-0.001, math.nan, math.inf])
def test_a_bad_price_never_estimates_as_free(price):
    with pytest.raises(ValueError):
        diagnose_estimate_usd(4000, price)
    with pytest.raises(ValueError):
        failed_run_cost_usd(1000, price)


def test_a_failed_run_costs_its_prompt():
    tokens, cost = failed_run_cost_usd(4000, 0.001)
    assert tokens == 1000 and cost == pytest.approx(0.001)
    assert failed_run_cost_usd(0, 0.001)[0] == 1


def _admit(spent: float, cap: float, estimate: float):
    return admit(
        frozen=False,
        code_rev="c0de001",
        consults_for_incident=0,
        consult_cap=2,
        spend=Spend(spent, cap),
        mode="fake",
        estimate_usd=estimate,
    )


def test_admit_counts_the_estimate_against_the_cap():
    assert _admit(0.0, 1.0, 1.0).ok
    blocked = _admit(0.0, 1.0, 1.01)
    assert not blocked.ok and blocked.outcome == BLOCKED_OVER_CAP and blocked.which == "spend"
    assert not _admit(0.5, 1.0, 0.6).ok
    assert not _admit(0.0, 1.0, math.nan).ok  # fails closed


@pytest.mark.parametrize(
    ("proposal", "overlay", "outcome"),
    [
        ("offered", None, None),  # the device hasn't answered
        ("applied", None, None),  # no overlay row yet
        ("applied", "active", None),  # applied, not held yet
        ("applied", "held", "held"),
        ("applied", "locked_in", "held"),  # locked in since: it was held
        ("applied", "lockin_rolled_back", "held"),
        ("applied", "dropped", "held"),
        ("applied", "reverted", "reverted"),
        ("applied", "superseded", "stale"),  # a new version replaced it before it held
        ("held", "held", "held"),
        ("reverted", "reverted", "reverted"),
        ("failed_verify", None, "failed_verify"),
        ("stale", None, "stale"),
        ("rejected", None, "rejected"),
        ("expired", None, "expired"),
        ("something_new", "held", None),  # never invents an outcome
    ],
)
def test_applied_outcome(proposal, overlay, outcome):
    assert applied_outcome(proposal, overlay) == outcome


def test_every_cloud_status_after_held_counts_as_held():
    assert {"held", "locked_in", "dropped", "lockin_rolled_back", "lockin_error"} == HELD_OVERLAY


@pytest.mark.parametrize("outcome", sorted(RETRY_OUTCOMES))
def test_retryable_outcomes_retry_under_the_cap_then_give_up(outcome):
    assert after_close(outcome, 1, 2) == "retry"
    assert after_close(outcome, 2, 2) == GAVE_UP
    assert after_close(outcome, 3, 2) == GAVE_UP
    assert after_close(outcome, 1, 0) == GAVE_UP  # a cap of 0: no retries at all


@pytest.mark.parametrize(
    "outcome",
    [
        "held",
        "advised",
        "discarded",
        "declined",
        "blocked_over_cap",
        "gave_up",
        "stale",
        "rejected",
        "expired",
        None,
    ],
)
def test_other_outcomes_are_not_retried(outcome):
    assert after_close(outcome, 1, 5) is None


@pytest.mark.parametrize(
    ("used", "cap"), [(-1, 2), (1, -1), (True, 2), (1, math.nan), (1.0, 2), (None, 2)]
)
def test_bad_counts_give_up_rather_than_retry(used, cap):
    assert after_close("reverted", used, cap) == GAVE_UP
