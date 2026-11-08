"""B3: a code_patch may name files relative to device/ (Ishaan's `--- a/robot/control.py`) or
with a `device/` prefix (`git diff` from the repo root). The replay, the lock-in's apply and
the holdout and safety checks treat both the same; mixed forms and escapes fail closed."""

import sys
from pathlib import Path

import pytest

from romulus_cloud.adapters.coder import CoderError, Sandbox
from romulus_cloud.services import code_lockin
from romulus_cloud.services.coder_runs import patch_refusal, replay_code_patch
from romulus_contracts import touches_safety

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "fixtures"))
import coder_fakes as fakes  # noqa: E402


def prefixed(patch: str) -> str:
    for side in ("a/", "b/"):
        patch = patch.replace(f"{side}control/", f"{side}device/control/")
    return patch


PREFIXED = prefixed(fakes.TOY_PATCH)
TOUCHES_VERIFY = (
    "diff --git a/device/verify/test_verify_d1.py b/device/verify/test_verify_d1.py\n"
    "--- a/device/verify/test_verify_d1.py\n+++ b/device/verify/test_verify_d1.py\n"
    "@@ -1 +1 @@\n"
    '-"""Verify for the planted D1 fault: a hand near the sensor must not make the arm drop."""\n'
    '+"""Nothing to see."""\n'
)


def replay(patch):
    sandbox = Sandbox(image="romulus-coder:test", runner=fakes.FakeDocker())
    return replay_code_patch(fakes.toy_files(), patch, sandbox=sandbox)


def test_the_fixture_patch_really_is_the_relative_form():
    assert "--- a/control/gripper.py" in fakes.TOY_PATCH
    assert "--- a/device/control/gripper.py" in PREFIXED


@pytest.mark.parametrize("patch", [fakes.TOY_PATCH, PREFIXED], ids=["relative", "prefixed"])
def test_replay_passes_the_same_fix_in_either_form(patch):
    result = replay(patch)
    assert result.patch_error is None
    assert result.caught_fault and result.passed, result.summary()


@pytest.mark.parametrize("patch", [fakes.TOY_PATCH, PREFIXED], ids=["relative", "prefixed"])
def test_apply_diff_changes_the_same_file_in_either_form(patch):
    base = fakes.toy_files()
    new = code_lockin.apply_diff(base, patch)
    assert {k for k in new if new[k] != base.get(k)} == {"device/control/gripper.py"}
    assert "device/device/control/gripper.py" not in new


def test_a_prefixed_patch_to_verify_is_refused_like_a_relative_one():
    assert "held-out verify/" in patch_refusal(TOUCHES_VERIFY)
    result = replay(TOUCHES_VERIFY)
    assert result.patched is None and "held-out verify/" in result.patch_error


@pytest.mark.parametrize("patch", [fakes.TOY_PATCH, PREFIXED], ids=["relative", "prefixed"])
def test_touches_safety_sees_the_same_paths(patch):
    assert touches_safety(patch, ["control"])
    assert not touches_safety(patch, ["safety", "visor"])


def test_a_mixed_patch_is_refused_everywhere():
    extra = "--- a/SPEC.md\n+++ b/SPEC.md\n@@ -1 +1 @@\n-x\n+y\n"
    mixed = PREFIXED + extra
    assert "mix the two forms" in patch_refusal(mixed)
    assert replay(mixed).patched is None
    with pytest.raises(CoderError, match="mix the two forms"):
        code_lockin.apply_diff(fakes.toy_files(), mixed)
    assert touches_safety(mixed, [])


def test_a_prefixed_escape_is_refused():
    escape = PREFIXED.replace("a/device/control/", "a/device/../control/").replace(
        "b/device/control/", "b/device/../control/"
    )
    assert "must stay inside device/" in patch_refusal(escape)
    with pytest.raises(CoderError):
        code_lockin.apply_diff(fakes.toy_files(), escape)
