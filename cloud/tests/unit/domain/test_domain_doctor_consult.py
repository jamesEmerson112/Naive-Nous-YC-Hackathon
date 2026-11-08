"""domain/doctor.py, consult rules (PR 4): how a waiting consult ends, and what follows."""

import math

import pytest

from romulus_cloud.domain.doctor import (
    GAVE_UP,
    HELD_OVERLAY,
    RETRY_OUTCOMES,
    after_close,
    applied_outcome,
)


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
