"""domain/story.py: every event kind that can reach an incident chain has a readable label.

The kinds come from two places: the APIs doc §5 table and the kinds the cloud's code
records. The code scan reads every module with `ast` and follows the kind argument of
`events.record(...)` and of any helper that passes its own parameter on as the kind (gate's
`_record`/`_close`, work orders' `_record`, ...). A kind argument may be a string, a
module constant, a local variable, a lookup in a module dict (`TRANSITION_KINDS[...]`), or
an f-string such as `overlay.{status}`, which is expanded over `FSTRING_VALUES`. A kind
the scan can't resolve fails the test, so the scan never silently misses one. Each kind
must be in `LABELS` or in `NOT_IN_CHAINS`.
"""

import ast
import re
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import get_args

from romulus_cloud.domain import story
from romulus_cloud.domain.story import LABELS, NOT_IN_CHAINS, build_story
from romulus_cloud.services.incidents import CLOUD_STATUSES
from romulus_contracts.incident import OverlayState

ROOT = Path(__file__).resolve().parents[4]
SRC = ROOT / "cloud" / "src" / "romulus_cloud"
APIS_DOC = ROOT / "docs" / "cloud-platform-planning" / "cloud-platform-apis.md"
KIND = re.compile(r"^[a-z_]+\.[a-z_]+$")
# f-string kinds: the literal prefix → every value its placeholder can take.
FSTRING_VALUES: dict[str, tuple[str, ...]] = {
    "overlay.": tuple(get_args(OverlayState)) + CLOUD_STATUSES,
}


def _doc_kinds() -> set[str]:
    text = APIS_DOC.read_text()
    section = text.split("## 5. Event kinds", 1)[1].split("\n## ", 1)[0]
    rows = [line.split("|")[2] for line in section.splitlines() if line.count("|") >= 3]
    found = {k for row in rows for k in re.findall(r"`([a-z_]+\.[a-z_]+)`", row)}
    return {k for k in found if not k.startswith("refs.")}  # notes like `refs.incidents`


def _strings(node: ast.expr) -> set[str] | None:
    """The strings a module-level value holds: a constant, a dict's values, or a
    collection's items (also inside `frozenset({...})`)."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return {node.value}
    if isinstance(node, ast.Call) and node.args:
        return _strings(node.args[0])
    items = node.values if isinstance(node, ast.Dict) else getattr(node, "elts", None)
    out = {i.value for i in items or [] if isinstance(i, ast.Constant) and isinstance(i.value, str)}
    return out or None


def _kind_arg(call: ast.Call, index: int, name: str) -> ast.expr | None:
    if len(call.args) > index:
        return call.args[index]
    return next((k.value for k in call.keywords if k.arg == name), None)


def _callee(call: ast.Call) -> str:
    f = call.func
    if isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name) and f.value.id == "events":
        return f"events.{f.attr}"
    return f.id if isinstance(f, ast.Name) else ""


def _assigned(fn: ast.FunctionDef, name: str) -> list[ast.expr]:
    return [
        a.value
        for a in ast.walk(fn)
        if isinstance(a, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == name for t in a.targets)
    ]


class _Module:
    def __init__(self, path: Path) -> None:
        self.path = path
        tree = ast.parse(path.read_text())
        self.values: dict[str, set[str]] = {}
        for n in tree.body:
            if isinstance(n, ast.Assign | ast.AnnAssign) and n.value is not None:
                found = _strings(n.value)
                for t in n.targets if isinstance(n, ast.Assign) else [n.target]:
                    if isinstance(t, ast.Name) and found:
                        self.values[t.id] = found
        self.functions = [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]

    def calls(self) -> Iterator[tuple[ast.FunctionDef, ast.Call]]:
        for fn in self.functions:
            for node in ast.walk(fn):
                if isinstance(node, ast.Call):
                    yield fn, node

    def resolve(self, node: ast.expr | None, fn: ast.FunctionDef, depth: int = 0):
        """Every string `node` can be, or None if the scan can't tell."""
        if node is None or depth > 5:
            return None
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return {node.value}
        if isinstance(node, ast.Subscript) and isinstance(node.value, ast.Name):
            return self.values.get(node.value.id)
        if isinstance(node, ast.JoinedStr):
            head = node.values[0] if len(node.values) == 2 else None
            prefix = str(head.value) if isinstance(head, ast.Constant) else None
            return (
                {prefix + v for v in FSTRING_VALUES[prefix]} if prefix in FSTRING_VALUES else None
            )
        if isinstance(node, ast.Name):
            local = _assigned(fn, node.id)
            if local:
                options = [self.resolve(v, fn, depth + 1) for v in local]
                return None if None in options else set().union(*options)
            got = self.values.get(node.id)
            return got if got and len(got) == 1 else None
        return None

    def sinks(self) -> dict[str, tuple[int, str]]:
        """`events.record` plus every helper that forwards one of its own (never
        reassigned) parameters as the kind, to a fixed point (`_close` → `_record`)."""
        sinks = {"events.record": (3, "kind")}
        changed = True
        while changed:
            changed = False
            for fn, call in self.calls():
                sink = sinks.get(_callee(call))
                arg = _kind_arg(call, *sink) if sink else None
                params = [a.arg for a in fn.args.args]
                kwonly = [a.arg for a in fn.args.kwonlyargs]
                if (
                    isinstance(arg, ast.Name)
                    and arg.id in params + kwonly
                    and fn.name not in sinks
                    and not _assigned(fn, arg.id)
                ):
                    # A keyword-only kind has no position: callers can only name it.
                    index = params.index(arg.id) if arg.id in params else 1 << 30
                    sinks[fn.name] = (index, arg.id)
                    changed = True
        return sinks


def _default(fn: ast.FunctionDef, name: str) -> ast.expr | None:
    """The default value of `fn`'s keyword-only parameter `name`, if it has one."""
    for a, d in zip(fn.args.kwonlyargs, fn.args.kw_defaults, strict=True):
        if a.arg == name:
            return d
    return None


def _scan() -> tuple[set[str], list[str]]:
    """(kinds the code records, call sites whose kind the scan couldn't resolve)."""
    found: set[str] = set()
    unresolved: list[str] = []
    for m in (_Module(p) for p in sorted(SRC.rglob("*.py"))):
        sinks = m.sinks()
        for fn, call in m.calls():
            sink = sinks.get(_callee(call))
            if sink is None:
                continue
            arg = _kind_arg(call, *sink)
            mine = sinks.get(fn.name)
            if mine and isinstance(arg, ast.Name) and arg.id == mine[1]:
                # A helper forwarding its own parameter: its callers are scanned, and a
                # keyword-only default (`kind="workorder.status_changed"`) counts too.
                arg = _default(fn, arg.id)
                if arg is None:
                    continue
            elif arg is None and sink[0] == 1 << 30:
                continue  # the caller left a keyword-only kind at its default
            got = m.resolve(arg, fn)
            if got is None:
                unresolved.append(f"{m.path.relative_to(SRC)}:{call.lineno}")
            else:
                found |= {k for k in got if KIND.match(k)}
    return found, unresolved


def test_the_scan_finds_kinds_from_every_source():
    doc, (code, _) = _doc_kinds(), _scan()
    assert {"incident.reported", "candidate.promoted", "workorder.created"} <= doc
    # Direct calls and module constants.
    assert {"regression.case_added", "device.first_seen", "label.changed"} <= code
    # Through helpers: gate `_record` (a dict lookup) and `_close`, work orders' `_record`.
    assert {"candidate.canary_started", "candidate.restarted", "candidate.stale"} <= code
    assert {"workorder.card_updated", "workorder.status_changed"} <= code
    # f-strings, expanded: `overlay.{status}`.
    assert {"overlay.held", "overlay.reverted", "overlay.lockin_rolled_back"} <= code


def test_the_scan_resolves_every_recorded_kind():
    _, unresolved = _scan()
    assert unresolved == [], (
        f"the label test can't tell which kinds these record: {unresolved}; use a constant "
        "or a module dict, or add the f-string prefix to FSTRING_VALUES"
    )


def test_every_kind_that_can_reach_a_chain_has_a_label():
    code, _ = _scan()
    missing = sorted((_doc_kinds() | code) - set(LABELS) - NOT_IN_CHAINS)
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
