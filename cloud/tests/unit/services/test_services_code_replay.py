"""Code replay (M8): the device tests and verify scripts on a code candidate's code version."""

import pytest

from fake_device.scenarios import D1_CODE_PATCH
from romulus_cloud.adapters.blobstore import build_blobstore
from romulus_cloud.adapters.coder import CoderError, build_coder
from romulus_cloud.models import Candidate
from romulus_cloud.services import code_replay, registry

KW = {"actor": "human:test", "actor_class": "human"}


def _candidate(rev: str) -> Candidate:
    return Candidate(id="cand-1", fleet_id="default", type="code", version=3, code_rev=rev)


@pytest.fixture
def sandbox(make_settings, host_coder):
    """Settings with the host sandbox switched on (make_settings forces fake adapters)."""
    return make_settings(**host_coder).model_copy(update={"coder_provider": "local_agent"})


def test_without_a_sandbox_it_is_the_labeled_stub(db_session, settings):
    r = code_replay.replay(db_session, _candidate("c0de001"), settings)
    assert r.passed and r.stub
    assert r.why == "replay: stub, passed by default; device tests not wired (M8)"


def test_with_nothing_to_run_it_is_a_stub_that_says_so(db_session, sandbox):
    nothing = sandbox.model_copy(update={"coder_test_cmd": "", "coder_verify_glob": ""})
    r = code_replay.replay(db_session, _candidate("c0de001"), nothing)
    assert r.passed and r.stub and "no device test command or verify scripts" in r.why


def test_unknown_code_fails(db_session, sandbox):
    r = code_replay.replay(db_session, _candidate("nope123"), sandbox)
    assert not r.passed and not r.stub and "code nope123 does not exist" in r.why


def test_the_buggy_code_fails_its_verify_script(db_session, sandbox, toy_package):
    store = build_blobstore(sandbox.blob_dir)
    registry.create_code(db_session, store, toy_package, rev="toy0001", **KW)
    r = code_replay.replay(db_session, _candidate("toy0001"), sandbox)
    assert not r.passed and not r.stub
    assert "the device tests failed on code toy0001 in the sandbox" in r.why
    assert "FAIL: step 0 with a hand at 30 mm" in r.why


def test_the_fixed_code_passes(db_session, sandbox, toy_package):
    fixed = build_coder(sandbox).draft(D1_CODE_PATCH, {}, "toy0001", toy_package)
    store = build_blobstore(sandbox.blob_dir)
    code, _ = registry.create_code(
        db_session, store, registry.build_tarball(fixed.files or {}), rev=None, **KW
    )
    r = code_replay.replay(db_session, _candidate(code.rev), sandbox)
    assert r.passed and not r.stub, r.why
    assert f"the device tests passed on code {code.rev}" in r.why and "5 passed" in r.why


def test_a_broken_sandbox_raises_so_the_gate_marks_an_error(db_session, sandbox, toy_package):
    store = build_blobstore(sandbox.blob_dir)
    registry.create_code(db_session, store, toy_package, rev="toy0001", **KW)
    broken = sandbox.model_copy(update={"coder_command": "/nonexistent/romulus-coder"})
    with pytest.raises(CoderError, match="could not start the coder sandbox"):
        code_replay.replay(db_session, _candidate("toy0001"), broken)
