import pytest
from romulus_cloud.adapters.coder import CodeDraft, FakeCoder, build_coder

PATCH = (
    "--- a/control/gripper.py\n+++ b/control/gripper.py\n@@ -1 +1 @@\n-LIMIT = 900\n+LIMIT = 800\n"
)


def test_fake_coder_passes_patch_through_and_says_so():
    draft = FakeCoder().draft(PATCH, {"id": "inc-1"}, "rev-3")
    assert isinstance(draft, CodeDraft)
    assert draft.diff == PATCH
    assert draft.passed is True
    assert "pass-through: coding agent not wired" in draft.notes
    assert "rev-3" in draft.notes and "inc-1" in draft.notes


def test_factory():
    assert isinstance(build_coder("fake"), FakeCoder)
    for stub in ("local_agent", "naive_session"):
        with pytest.raises(NotImplementedError, match="M8 stub"):
            build_coder(stub)
    with pytest.raises(ValueError):
        build_coder("claude")
