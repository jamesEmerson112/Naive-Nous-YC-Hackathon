"""domain/story.py: every event kind that can reach an incident chain has a readable label.

The kinds come from two places: the APIs doc §5 table and the kinds the cloud's code
actually records (`events.record(...)` calls, read with `ast`, resolving module-level
string constants). Each must be in `LABELS` or in `NOT_IN_CHAINS`.
"""

import ast
import re
from datetime import UTC, datetime
from pathlib import Path

from romulus_cloud.domain import story
from romulus_cloud.domain.story import LABELS, NOT_IN_CHAINS, build_story

ROOT = Path(__file__).resolve().parents[4]
SRC = ROOT / "cloud" / "src" / "romulus_cloud"
APIS_DOC = ROOT / "docs" / "cloud-platform-planning" / "cloud-platform-apis.md"
KIND = re.compile(r"^[a-z_]+\.[a-z_]+$")


def _doc_kinds() -> set[str]:
    text = APIS_DOC.read_text()
    section = text.split("## 5. Event kinds", 1)[1].split("\n## ", 1)[0]
    rows = [line.split("|")[2] for line in section.splitlines() if line.count("|") >= 3]
    found = {k for row in rows for k in re.findall(r"`([a-z_]+\.[a-z_]+)`", row)}
    return {k for k in found if not k.startswith("refs.")}  # notes like `refs.incidents`


def _recorded_kinds() -> set[str]:
    found: set[str] = set()
    for path in SRC.rglob("*.py"):
        tree = ast.parse(path.read_text())
        consts = {
            t.id: n.value.value
            for n in tree.body
            if isinstance(n, ast.Assign) and isinstance(n.value, ast.Constant)
            for t in n.targets
            if isinstance(t, ast.Name) and isinstance(n.value.value, str)
        }
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
                continue
            if node.func.attr != "record" or not isinstance(node.func.value, ast.Name):
                continue
            if node.func.value.id != "events":
                continue
            arg = node.args[3] if len(node.args) > 3 else None
            arg = arg or next((k.value for k in node.keywords if k.arg == "kind"), None)
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                found.add(arg.value)
            elif isinstance(arg, ast.Name) and arg.id in consts:
                found.add(consts[arg.id])
    return {k for k in found if KIND.match(k)}


def test_the_scan_finds_kinds_from_both_sources():
    doc, code = _doc_kinds(), _recorded_kinds()
    assert {"incident.reported", "candidate.promoted", "workorder.created"} <= doc
    assert {"regression.case_added", "device.first_seen", "label.changed"} <= code


def test_every_kind_that_can_reach_a_chain_has_a_label():
    missing = sorted((_doc_kinds() | _recorded_kinds()) - set(LABELS) - NOT_IN_CHAINS)
    assert missing == [], f"add a short label to story.LABELS (or NOT_IN_CHAINS) for {missing}"


def test_no_kind_is_both_labelled_and_out_of_chains():
    assert not (set(LABELS) & NOT_IN_CHAINS)


class Ev:
    def __init__(self, kind, refs=None, data=None):
        self.ts = datetime(2026, 11, 7, 23, 0, tzinfo=UTC)
        self.actor, self.actor_class, self.kind = "automation:worker", "automation", kind
        self.refs, self.why, self.data = refs or {"incident": "i1"}, "because", data


def test_regression_case_reads_as_words_and_keeps_the_raw_kind_for_the_tooltip():
    ev = Ev("regression.case_added", data={"expected_action": "safe_pose"})
    s = build_story([Ev("incident.reported", data={"kind": "camera_occluded"}), ev], "i1")
    assert s.chain == "camera_occluded → added to regression corpus"
    assert s.links[-1] == ("added to regression corpus", "regression.case_added")
    assert s.to_dict()["links"][-1] == {
        "label": "added to regression corpus",
        "kind": "regression.case_added",
    }
    assert "safe_pose" in s.steps[-1].line


def test_no_labelled_kind_shows_a_dotted_raw_kind():
    for kind, label in LABELS.items():
        text = label(Ev(kind, refs={"incident": "i1", "overlay": "ov-1", "device": "sim-1"}))
        assert not KIND.match(text), f"{kind} labels itself {text!r}"


def test_an_unlabelled_kind_falls_back_to_words():
    (step,) = build_story([Ev("brand.new_thing")], "i1").steps
    assert step.label == "brand new thing"
    assert story._default_label(Ev("x.y_z")) == "x y z"
