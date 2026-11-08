"""domain/story.py, M4 additions: recording events become evidence steps."""

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from romulus_cloud.domain.story import build_story

T0 = datetime(2026, 11, 7, 22, 2, 11, tzinfo=UTC)
INC = "0b6c6f3e-8d2a-4c1b-b5e3-6f7a8b9c0d1e"
REC = "7a1c9e2f-4b3d-4f8a-8e6b-1c2d3e4f5a6b"


@dataclass
class Ev:
    kind: str
    ts: datetime = T0
    why: str = "because"
    actor: str = "device:arm-1"
    actor_class: str = "automation"
    refs: dict[str, Any] = field(default_factory=lambda: {"incident": INC, "device": "arm-1"})
    data: dict[str, Any] | None = None


def _ev(stored: bool, **over) -> dict[str, Any]:
    return {
        "recording": REC,
        "kind": "log_window",
        "format": "mcap",
        "size_bytes": 2048,
        "stored": stored,
        "download": f"/v1/recordings/{REC}/blob",
        **over,
    }


def _rec_refs():
    return {"incident": INC, "device": "arm-1", "recording": REC}


def _incident():
    return Ev("incident.reported", data={"kind": "camera_occluded", "summary": "dark"})


def test_received_recording_is_an_evidence_step():
    received = Ev(
        "recording.received",
        ts=T0 + timedelta(seconds=30),
        refs=_rec_refs(),
        data={"evidence": _ev(False)},
    )
    s = build_story([received, _incident()], INC)
    assert s.chain == "camera_occluded → evidence log_window"
    step = s.steps[1]
    assert step.evidence == _ev(False)
    assert step.line == "Evidence from arm-1: log_window (mcap, 2.0 KB)."
    assert s.steps[0].evidence is None


def test_stored_step_has_the_link_and_stays_out_of_the_chain():
    evs = [
        _incident(),
        Ev("recording.received", refs=_rec_refs(), data={"evidence": _ev(False)}),
        Ev(
            "recording.blob_stored",
            ts=T0 + timedelta(minutes=1),
            refs=_rec_refs(),
            data={"evidence": _ev(True)},
        ),
    ]
    s = build_story(evs, INC)
    assert s.chain == "camera_occluded → evidence log_window"
    assert s.steps[-1].label == ""
    assert s.steps[-1].line.endswith(f"Download: /v1/recordings/{REC}/blob")


def test_story_evidence_lists_each_recording_once_with_latest_state():
    other = "9b2d0f3a-5c4e-4a9b-9f7c-2d3e4f5a6b7c"
    evs = [
        Ev("recording.received", refs=_rec_refs(), data={"evidence": _ev(False)}),
        Ev(
            "recording.received",
            refs={**_rec_refs(), "recording": other},
            data={"evidence": _ev(True, recording=other, kind="audit", format="json")},
        ),
        Ev(
            "recording.blob_stored",
            ts=T0 + timedelta(minutes=1),
            refs=_rec_refs(),
            data={"evidence": _ev(True)},
        ),
    ]
    s = build_story(evs, INC)
    assert [(e["recording"], e["stored"]) for e in s.evidence] == [(REC, True), (other, True)]
    d = s.to_dict()
    assert d["evidence"] == list(s.evidence)
    assert "evidence" in d["steps"][0]


def test_linked_line_names_added_labels():
    linked = Ev(
        "recording.linked",
        refs=_rec_refs(),
        data={"evidence": _ev(True), "labels_added": ["kind:camera_occluded"]},
    )
    s = build_story([linked], INC)
    assert s.steps[0].line == (
        "The log_window recording was linked to this incident; labels added: kind:camera_occluded."
    )
    assert s.chain == ""


def test_steps_without_evidence_keep_their_dict_shape():
    d = build_story([_incident()], INC).to_dict()
    keys = {"at", "actor", "actor_class", "kind", "line", "why", "label", "refs"}
    assert set(d["steps"][0]) == keys
    assert d["evidence"] == []


def test_evidence_without_size_or_data_still_reads():
    bare = Ev("recording.received", refs=_rec_refs(), data=None)
    s = build_story([bare], INC)
    assert s.steps[0].line == "Evidence from arm-1: recording (?, size unknown)."
    assert s.steps[0].evidence is None and s.evidence == ()


def test_a_new_link_shows_in_the_chain_as_evidence():
    """A recording that didn't name its incident has no `received` step in the story."""
    linked = Ev(
        "recording.linked",
        refs=_rec_refs(),
        data={"evidence": _ev(True, kind="audit"), "labels_added": [], "linked": True},
    )
    s = build_story([_incident(), linked], INC)
    assert s.chain == "camera_occluded → evidence audit"


def test_evidence_steps_never_change_the_status():
    later = T0 + timedelta(hours=1)
    evs = [
        _incident(),
        Ev("overlay.held", refs={**_rec_refs(), "overlay": "ov-12"}),
        Ev("recording.received", ts=later, refs=_rec_refs(), data={}),
        Ev("recording.blob_stored", ts=later, refs=_rec_refs(), data={}),
        Ev("recording.linked", ts=later, refs=_rec_refs(), data={"linked": True}),
    ]
    assert build_story(evs, INC).status == ("overlay held", "good")
