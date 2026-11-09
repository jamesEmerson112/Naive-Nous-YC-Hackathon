"""Level 1 (plain-code rules) replayed on one benchmark item: the baseline every model is compared to.

Two rule sets run on the item's snapshot, exactly as they run live:
- the arm watchdog, `armsim/level1.py` (`run()` on the distance rows, plus `check_arrival` on skills
  that ended inside the window, the way `Watchdog.arrival` calls it after each skill, host.py);
- the pod's own triage, Olir's `firmware/healer/healer/detect.py` (`run()` on the podd snapshot).

`armsim/level1.py` is loaded by file path because `armsim/__init__.py:2` imports MuJoCo, and
`firmware/healer` goes on sys.path as `armsim/podsource.py:21-27` does, so this module (and the whole
benchmark except gen_arm.py) needs only the standard library.

    from workerbench import l1
    l1.l1_findings(item["snapshot"], which="arm")     # what the arm watchdog says on this window
    l1.l1_answer(item["snapshot"], accept={"us_frozen"})  # the same, as a six-key worker answer
"""
import importlib.util
import sys
from pathlib import Path

from . import kinds

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
HEALER = REPO / "firmware" / "healer"
if str(HEALER) not in sys.path:
    sys.path.insert(0, str(HEALER))
from healer import detect  # noqa: E402

_spec = importlib.util.spec_from_file_location("wb_level1", HERE.parent / "armsim" / "level1.py")
level1 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(level1)

ALARM = ("warn", "critical")
SPAN_S = 6.0
_ORDER = {"critical": 0, "warn": 1, "info": 2}


def has_pod(snapshot):
    """True when the item carries pod-side data (podd's A/H/E rows or a link state)."""
    return bool(snapshot.get("A") or snapshot.get("H") or snapshot.get("E") or snapshot.get("link"))


def l1_findings(snapshot, *, which="all"):
    """Findings level 1 raises on this window, each with `src` = watchdog or podd.

    The watchdog judged its last distance row (it runs once per D row), so the distance rules run
    with `now` = the newest D row's ts and the grasp state captured at that row (`hold`)."""
    out = []
    now = snapshot["now"]
    D = snapshot.get("D") or []
    if which in ("all", "arm"):
        if D:
            last = D[-1]
            s = {"now": last["ts"], "D": D, "holding": bool(last.get("hold", snapshot.get("holding", False)))}
            out += [dict(f, src="watchdog") for f in level1.run(s)]
        for sk in snapshot.get("skills") or []:
            t1, r = sk.get("t1"), sk.get("result")
            if t1 is None or r is None or not (now - SPAN_S < t1 <= now + 1e-9) or r.get("halted"):
                continue
            if r.get("finding"):
                out.append(dict(r["finding"], src="watchdog"))
            else:
                out += [dict(f, src="watchdog") for f in level1.check_arrival(r, load_nm=sk.get("load_nm"))]
    if which in ("all", "pod") and (has_pod(snapshot) or D):
        s = {"now": now, "D": D, "A": snapshot.get("A") or [], "H": snapshot.get("H") or [],
             "E": snapshot.get("E") or [], "link": snapshot.get("link") or {"state": "up"}}
        out += [dict(f, src="podd") for f in detect.run(s)]
    return sorted(out, key=lambda f: _ORDER.get(f["severity"], 3))


def alarms(findings):
    return [f for f in findings if f["severity"] in ALARM]


def l1_answer(snapshot, *, accept=(), which="all"):
    """The most severe level-1 finding as a six-key worker answer: its name mapped through the alias
    table (a kind the item accepts if any alias is accepted), no evidence, its summary as the message."""
    found = alarms(l1_findings(snapshot, which=which))
    if not found:
        return {"status": "healthy", "kind": "none", "severity": "info", "evidence": [], "escalate": False,
                "message": ""}
    top = [f for f in found if f["severity"] == found[0]["severity"]]
    pick = next((f for f in top if kinds.l1_kinds(f["kind"]) & set(accept)), top[0])
    names = kinds.l1_kinds(pick["kind"]) & set(accept)
    kind = sorted(names)[0] if names else kinds.l1_primary(pick["kind"])
    words = str(pick.get("summary", pick["kind"])).split()
    return {"status": "fault", "kind": kind, "severity": pick["severity"], "evidence": [], "escalate": True,
            "message": " ".join(words[:40])}
