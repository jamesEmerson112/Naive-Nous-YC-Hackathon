"""`services.datasets.replay_corpus`: the gate-safe regression-corpus replay (pure, no DB)."""

import pytest

from romulus_cloud.domain import gate
from romulus_cloud.services.datasets import replay_corpus

# --- corpus replay for the gate ----------------------------------------------------------------

RULES = {
    "rules": {"camera_occluded": {"signal": "brightness", "below": 0.2, "action": "safe_pose"}}
}
CASE = gate.ReplayCase("i1", {"brightness": [0.4, 0.1]}, "safe_pose", "camera_occluded")
ODD = gate.ReplayCase("i2", {"bus_read_errors": [3, 3]}, "park", "servo_absent")


def test_replay_corpus_empty_corpus_passes():
    result = replay_corpus(RULES, [])
    assert result.passed and "no cases yet" in result.why and not result.stub


def test_replay_corpus_passes_and_fails_like_replay():
    assert replay_corpus(RULES, [CASE]).passed
    failed = replay_corpus({"rules": {}}, [CASE])
    assert not failed.passed and "1 regress" in failed.why and "i1" in failed.why


@pytest.mark.parametrize(
    "manifest",
    [
        {"rules": {"camera_occluded": {"signal": "brightness", "action": "safe_pose"}}},
        {"rules": {"camera_occluded": "nonsense"}},
        {"rules": ["not", "a", "map"]},
        "not a manifest",
    ],
)
def test_replay_corpus_malformed_rules_fail_with_a_sentence(manifest):
    result = replay_corpus(manifest, [CASE])
    assert not result.passed and "regression corpus" in result.why
    assert "malformed" in result.why or "fail" in result.why


def test_replay_corpus_regression_only_with_baseline():
    # The current release already fails ODD, so ODD can't block the candidate...
    result = replay_corpus(RULES, [CASE, ODD], baseline=RULES)
    assert result.passed and "1 already fail" in result.why
    # ...but a case the release passes and the candidate fails still does.
    assert not replay_corpus({"rules": {}}, [CASE, ODD], baseline=RULES).passed
    # Without a baseline every case counts.
    assert not replay_corpus(RULES, [CASE, ODD]).passed
