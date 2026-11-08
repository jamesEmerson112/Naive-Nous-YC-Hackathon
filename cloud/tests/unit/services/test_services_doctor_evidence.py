"""services/doctor_evidence.py (PR 7): the case file reads the agent-trace tail through the
blob store and the code at the base rev from the registry, byte-capped, never `verify/`."""

import hashlib
import io
import json
import uuid
from datetime import timedelta
from types import SimpleNamespace

import pytest

from romulus_cloud.adapters.blobstore import BlobNotFound
from romulus_cloud.models import Blob, Incident, Recording
from romulus_cloud.services import doctor_code, doctor_evidence, registry
from romulus_cloud.services.seed import seed

HIDDEN = b"HIDDEN-VERIFY-BODY: the Doctor must never see this"
PACKAGE = {
    "device/control/hold.py": b"RELEASE_THRESHOLD_MM = 40\n" * 20,
    "device/control/gripper.py": b"def step(): ...\n",
    "device/SPEC.md": b"Keep holding while a hand is near.\n",
    "device/tests/test_hold.py": b"def test_hold(): ...\n",
    "device/verify/test_verify_d1.py": HIDDEN,
}


class MemStore:
    """An in-memory BlobStore (no disk)."""

    def __init__(self, seekable: bool = True) -> None:
        self.blobs: dict[str, bytes] = {}
        self.seekable = seekable

    def put(self, sha256, stream):
        self.blobs[sha256] = stream.read()
        return len(self.blobs[sha256])

    def get(self, sha256):
        if sha256 not in self.blobs:
            raise BlobNotFound(sha256)
        data = self.blobs[sha256]
        return io.BytesIO(data) if self.seekable else _NoSeek(data)

    def exists(self, sha256):
        return sha256 in self.blobs

    def size(self, sha256):
        if sha256 not in self.blobs:
            raise BlobNotFound(sha256)
        return len(self.blobs[sha256])

    def delete(self, sha256):
        return self.blobs.pop(sha256, None) is not None


class _NoSeek(io.BytesIO):
    def seek(self, *args, **kwargs):
        raise io.UnsupportedOperation("no seeking here")


@pytest.fixture
def store():
    return MemStore()


@pytest.fixture
def fleet(db_session, store):
    seed(db_session, store)
    kw = {"actor": "human:test", "actor_class": "human"}
    registry.create_code(db_session, store, registry.build_tarball(PACKAGE), rev="c0de0d1", **kw)
    db_session.flush()
    return db_session


def _incident(session, now, **changes) -> Incident:
    values = {
        "id": str(uuid.uuid4()),
        "fleet_id": "default",
        "device_id": "arm-1",
        "occurred_at": now,
        "received_at": now,
        "kind": "unexpected_release",
        "summary": "Gripper opened while holding",
        "running": {"firmware": 1, "harness": 1, "code_rev": "c0de0d1"},
        "agent_reasoning": "Lowered the threshold twice.",
        "actions": ["park", "patch control/hold.py: RELEASE_THRESHOLD_MM 40 -> 25", "rollback"],
        "raw": {"log_tail": ["S HOLDING->RELEASE reason=dist_lt_threshold dist_mm=18"]},
    }
    incident = Incident(**(values | changes))
    session.add(incident)
    session.flush()
    return incident


def _trace(session, store, incident, data: bytes, *, at, kind="agent_trace", stored=True):
    sha = hashlib.sha256(data).hexdigest()
    store.blobs[sha] = data
    if stored:
        session.merge(Blob(sha256=sha, fleet_id="default", size_bytes=len(data), path=sha))
    rec = Recording(
        id=str(uuid.uuid4()),
        fleet_id="default",
        device_id=incident.device_id,
        kind=kind,
        format="jsonl",
        sha256=sha,
        size_bytes=len(data),
        started_at=at,
        received_at=at,
        incident_id=incident.id,
    )
    session.add(rec)
    session.flush()
    return rec


def _case(session, store, incident, settings):
    return doctor_evidence.case_file(
        session, store, SimpleNamespace(attempt=1), incident, settings=settings
    )


def test_the_case_file_carries_the_trace_tail_and_the_code_the_logs_name(
    fleet, store, settings, now
):
    incident = _incident(fleet, now)
    lines = [json.dumps({"role": "assistant", "content": f"step {i}"}) for i in range(60)]
    lines[-1] = json.dumps({"role": "tool", "content": 'File "/work/SPEC.md", line 1'})
    rec = _trace(fleet, store, incident, "\n".join(lines).encode() + b"\n", at=now)
    case = _case(fleet, store, incident, settings)

    assert case["untrusted"] is True and case["notes"] == ["mcap: not wired"]
    assert case["trace_recordings"] == [rec.id]
    assert case["trace_tail"] == lines[-settings.doctor_trace_tail_lines :]
    # Named by the healer's action and by the trace: excerpts in order of first mention.
    code = case["code"]
    assert code["rev"] == "c0de0d1"
    assert [f["path"] for f in code["files"]] == ["SPEC.md", "control/hold.py"]
    spec = code["files"][0]
    assert spec["excerpt"] == PACKAGE["device/SPEC.md"].decode() and spec["truncated"] is False
    assert code["tree"] == [
        "SPEC.md",
        "control/gripper.py",
        "control/hold.py",
        "tests/test_hold.py",
    ]
    assert case["tried"][1] == {"by": "healer", "action": incident.actions[1]}


def test_verify_files_never_reach_the_case_file_even_when_a_log_names_them(
    fleet, store, settings, now
):
    incident = _incident(
        fleet,
        now,
        raw={
            "log_tail": [
                "verify/test_verify_d1.py failed",
                "S HOLDING->RELEASE reason=dist_lt_threshold",
                "device/verify/test_verify_d1.py",
            ]
        },
        actions=["park", "ran /work/verify/test_verify_d1.py: FAIL", "verify d1_hand_trace: FAIL"],
        agent_reasoning="Tier 3.\nverify/test_verify_d1.py still fails.\nParked.",
    )
    trace = b'{"content": "pytest verify/test_verify_d1.py"}\n{"content": "patched hold.py"}\n'
    _trace(fleet, store, incident, trace, at=now)
    case = _case(fleet, store, incident, settings)
    text = json.dumps(case)
    assert HIDDEN.decode() not in text
    # No line naming a verify/ path survives anywhere: log, trace, actions, reasoning.
    hidden = doctor_evidence.rules.VERIFY_WITHHELD
    evidence = json.dumps({k: v for k, v in case.items() if k != "notes"})
    assert "verify/" not in evidence.replace(hidden, "")
    assert case["log_tail"] == [hidden, "S HOLDING->RELEASE reason=dist_lt_threshold", hidden]
    assert case["trace_tail"] == [hidden, '{"content": "patched hold.py"}']
    assert [t["action"] for t in case["tried"]] == ["park", hidden, "verify d1_hand_trace: FAIL"]
    assert case["incident"]["agent_reasoning"] == f"Tier 3.\n{hidden}\nParked."
    assert (
        "verify: 5 log, trace or healer lines withheld (they name held-out verify/ paths)"
        in case["notes"]
    )
    assert all(not p.startswith("verify") for p in case["code"]["tree"])
    assert "verify/test_verify_d1.py" not in [f["path"] for f in case["code"]["files"]]
    # The coder's and the check's files leave it out too.
    files = doctor_code.coder_files(doctor_code.package(fleet, store, "c0de0d1", "default"))
    assert "device/verify/test_verify_d1.py" not in files and "device/SPEC.md" in files


def test_code_excerpts_are_capped_per_file_and_in_total(fleet, store, make_settings, now):
    settings = make_settings(doctor_evidence_file_bytes=100, doctor_evidence_code_bytes=130)
    incident = _incident(fleet, now, actions=["patch control/hold.py", "see SPEC.md"])
    files = _case(fleet, store, incident, settings)["code"]["files"]
    assert [(f["path"], len(f["excerpt"]), f["truncated"]) for f in files] == [
        ("control/hold.py", 100, True),
        ("SPEC.md", 30, True),
    ]
    assert files[0]["size_bytes"] == len(PACKAGE["device/control/hold.py"])


def test_the_trace_tail_is_byte_capped_and_reads_from_the_end(fleet, make_settings, now):
    store = MemStore()
    seed(fleet, store)
    kw = {"actor": "human:test", "actor_class": "human"}
    registry.create_code(fleet, store, registry.build_tarball(PACKAGE), rev="c0de0d1", **kw)
    settings = make_settings(doctor_trace_tail_lines=100, doctor_trace_tail_bytes=40)
    incident = _incident(fleet, now)
    big = b"".join(f"line {i:05d} {'x' * 20}\n".encode() for i in range(10_000))
    _trace(fleet, store, incident, big, at=now)
    tail = _case(fleet, store, incident, settings)["trace_tail"]
    assert tail == [f"line 09999 {'x' * 20}"]  # 32 bytes + newline fit; two don't
    assert sum(len(x) + 1 for x in tail) <= 40


def test_a_store_that_cant_seek_is_read_whole(fleet, make_settings, now):
    store = MemStore(seekable=False)
    seed(fleet, store)
    incident = _incident(fleet, now, running={"code_rev": "c0de001"})
    _trace(fleet, store, incident, b"one\ntwo\nthree\n", at=now)
    settings = make_settings(doctor_trace_tail_lines=2)
    assert _case(fleet, store, incident, settings)["trace_tail"] == ["two", "three"]


def test_only_this_incidents_stored_agent_traces_count_newest_last(fleet, store, settings, now):
    incident = _incident(fleet, now)
    other = _incident(fleet, now)
    old = _trace(fleet, store, incident, b"older trace\n", at=now - timedelta(minutes=5))
    new = _trace(fleet, store, incident, b"newer trace\n", at=now)
    _trace(fleet, store, incident, b"not uploaded\n", at=now, stored=False)
    _trace(fleet, store, incident, b"a video\n", at=now, kind="incident_window")
    _trace(fleet, store, other, b"another incident\n", at=now)
    case = _case(fleet, store, incident, settings)
    assert case["trace_recordings"] == [old.id, new.id]
    assert case["trace_tail"] == ["older trace", "newer trace"]


def test_a_trace_whose_bytes_vanished_is_noted_not_fatal(fleet, store, settings, now):
    incident = _incident(fleet, now)
    rec = _trace(fleet, store, incident, b"gone\n", at=now)
    store.delete(rec.sha256)
    case = _case(fleet, store, incident, settings)
    assert case["trace_tail"] == [] and case["trace_recordings"] == []
    assert f"trace {rec.id}: can't read its bytes (BlobNotFound)" in case["notes"]


@pytest.mark.parametrize(
    ("running", "note"),
    [
        ({"code_rev": "nope"}, "code: code nope is not registered (POST /v1/code)"),
        ({}, "code: the incident has no running.code_rev"),
    ],
)
def test_missing_code_is_a_note_with_no_files(fleet, store, settings, now, running, note):
    incident = _incident(fleet, now, running=running)
    case = _case(fleet, store, incident, settings)
    assert case["code"]["files"] == [] and case["code"]["tree"] == []
    assert note in case["notes"]
    assert "trace: no agent_trace recording stored for this incident" in case["notes"]


def test_a_package_without_the_device_prefix_still_reads(fleet, store, settings, now):
    flat = {"control/hold.py": b"X = 1\n", "verify/test_v.py": HIDDEN}
    kw = {"actor": "human:test", "actor_class": "human"}
    registry.create_code(fleet, store, registry.build_tarball(flat), rev="flat01", **kw)
    incident = _incident(fleet, now, running={"code_rev": "flat01"})
    code = _case(fleet, store, incident, settings)["code"]
    assert code["tree"] == ["control/hold.py"]
    assert [f["path"] for f in code["files"]] == ["control/hold.py"]


def test_no_blob_store_means_no_trace_and_no_code(fleet, settings, now):
    incident = _incident(fleet, now)
    case = _case(fleet, None, incident, settings)
    assert case["trace_tail"] == [] and case["code"]["files"] == []
    assert case["notes"] == ["trace: no blob store", "code: no blob store", "mcap: not wired"]
