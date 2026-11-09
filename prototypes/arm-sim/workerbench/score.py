"""Scoring for the worker-model benchmark: parse one answer strictly, score it against the item's label,
and summarize a run (metrics in docs/research/worker-benchmark-data.md §3; gates and ranking in
docs/worker-manager-healer.md §4).

    p = parse_answer(content, idmap)        # {"strict", "lenient", "schema_ok", "answer", "errors", "cited"}
    row = score_item(item, p, idmap, text=text, l1=l1_answer_dict)
    report = summarize(rows)

An answer counts as an alarm only if it is schema-valid, says `fault`, and is `warn` or `critical`. An
invalid answer counts as no alarm (level 1 still guards). Latent items (a planted fault with nothing
visible in the window) count neither toward recall nor as false alarms; they are reported separately.
"""
import collections
import json
import math
import re
import statistics

from . import kinds

KEYS = ("status", "kind", "severity", "evidence", "escalate", "message")
_ID = re.compile(r"^([A-Z]+)(\d+)$")
_BARE = re.compile(r"^[A-Z]+$")                  # an id without a number, e.g. the link line "L"
_RANGE = re.compile(r"^([A-Z]+)(\d+)\s*-\s*([A-Z]+)?(\d+)$")
_NUM = re.compile(r"(?<![A-Za-z])[-+]?\d+(?:\.\d+)?")


# ------------------------------------------------------------------------------------------ parsing

def _first_object(text):
    """The first balanced {...} block (string-aware), or None."""
    start = text.find("{")
    while start != -1:
        depth, i, in_str, esc = 0, start, False, False
        while i < len(text):
            c = text[i]
            if in_str:
                if esc:
                    esc = False
                elif c == "\\":
                    esc = True
                elif c == '"':
                    in_str = False
            elif c == '"':
                in_str = True
            elif c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    try:
                        obj = json.loads(text[start:i + 1])
                        return obj if isinstance(obj, dict) else None
                    except ValueError:
                        break
            i += 1
        start = text.find("{", start + 1)
    return None


def expand_ids(refs):
    """['D17-D30', 'H02'] -> {'D17', ..., 'D30', 'H02'}; raises ValueError on a malformed ref."""
    out = set()
    for ref in refs:
        if not isinstance(ref, str):
            raise ValueError(f"evidence item {ref!r} is not a string")
        ref = ref.strip()
        if _ID.match(ref) or _BARE.match(ref):
            out.add(ref)
            continue
        m = _RANGE.match(ref)
        if not m or (m.group(3) and m.group(3) != m.group(1)):
            raise ValueError(f"bad evidence ref {ref!r}")
        letter, a, b = m.group(1), m.group(2), m.group(4)
        lo, hi = int(a), int(b)
        if hi < lo or hi - lo > 200:
            raise ValueError(f"bad evidence range {ref!r}")
        w = len(a)
        out.update(f"{letter}{n:0{w}d}" for n in range(lo, hi + 1))
    return out


def parse_answer(content, idmap):
    """Strict parse: the whole content is one JSON object; lenient: the first {...} block parses;
    schema_ok: the six keys with valid values, kind none exactly when healthy, evidence ids that exist."""
    res = {"strict": False, "lenient": False, "schema_ok": False, "answer": None, "errors": [], "cited": set()}
    text = (content or "").strip()
    obj = None
    try:
        obj = json.loads(text)
        if isinstance(obj, dict):
            res["strict"] = True
        else:
            obj = None
    except ValueError:
        obj = None
    if obj is None:
        obj = _first_object(text)
    if obj is None:
        res["errors"].append("no JSON object")
        return res
    res["lenient"] = True
    res["answer"] = obj
    err = res["errors"]
    if set(obj) != set(KEYS):
        err.append(f"keys {sorted(obj)}")
    if obj.get("status") not in ("healthy", "fault"):
        err.append("status")
    if obj.get("kind") not in kinds.TABLE:
        err.append("kind not in the list")
    if obj.get("severity") not in kinds.SEVERITIES:
        err.append("severity")
    if not isinstance(obj.get("escalate"), bool):
        err.append("escalate not a boolean")
    if not isinstance(obj.get("message"), str):
        err.append("message not a string")
    if (obj.get("status") == "healthy") != (obj.get("kind") == "none"):
        err.append("kind none must go with healthy")
    ev = obj.get("evidence")
    if not isinstance(ev, list) or len(ev) > 8:
        err.append("evidence must be a list of at most 8")
    else:
        try:
            cited = expand_ids(ev)
            missing = sorted(c for c in cited if c not in idmap)
            if missing:
                err.append(f"evidence ids not in the window: {missing[:4]}")
            res["cited"] = cited & set(idmap)
        except ValueError as e:
            err.append(str(e))
    res["schema_ok"] = not err
    return res


def is_alarm(parsed):
    a = parsed.get("answer") or {}
    return bool(parsed.get("schema_ok") and a.get("status") == "fault" and a.get("severity") in ("warn", "critical"))


# ------------------------------------------------------------------------------------------ gold

def gold_ids(item, idmap):
    """Line ids whose rows include a gold evidence row (label.evidence_rows, by row timestamp)."""
    ev = item["label"].get("evidence_rows") or {}
    joint = (item["label"].get("facts") or {}).get("joint_idx")
    out = set()
    sets = {k: set(v) if isinstance(v, list) else v for k, v in ev.items()}
    for lid, ref in idmap.items():
        src = ref["src"]
        if src not in sets:
            continue
        if src == "L":
            if sets["L"]:
                out.add(lid)
            continue
        if src == "J" and ref.get("joint") is not None and joint is not None and ref["joint"] != joint:
            continue
        if sets[src] & set(ref["ts"]):
            out.add(lid)
    return out


def compress_ids(ids, cap=8):
    """Sorted ids -> same-letter ranges over consecutive numbers only (so a range holds only these ids)."""
    groups = collections.defaultdict(list)
    bare = sorted(i for i in ids if _BARE.match(i))
    for i in ids:
        m = _ID.match(i)
        if m:
            groups[m.group(1)].append((int(m.group(2)), len(m.group(2))))
    refs = []
    for letter in sorted(groups):
        nums = sorted(groups[letter])
        run = [nums[0]]
        for n in nums[1:]:
            if n[0] == run[-1][0] + 1:
                run.append(n)
            else:
                refs.append((letter, run))
                run = [n]
        refs.append((letter, run))
    out = []
    for letter, run in refs:
        (a, w), (b, _) = run[0], run[-1]
        out.append(f"{letter}{a:0{w}d}" if a == b else f"{letter}{a:0{w}d}-{letter}{b:0{w}d}")
    return (bare + out)[:cap]


# ------------------------------------------------------------------------------------------ message

def _numbers(text):
    return [float(x) for x in _NUM.findall(text or "")]


def _close(x, y, key):
    if key.endswith("_mm") or key in ("mm",):
        return abs(x - y) <= max(3.0, 0.10 * abs(y))
    if "ratio" in key:
        return abs(x - y) <= 0.05
    if key.endswith("_deg"):
        return abs(x - y) <= 0.25 * abs(y) + 0.5
    if key.endswith("_us"):
        return abs(x - y) <= 5.0
    return abs(x - y) <= max(1.0, 0.10 * abs(y))


def _joint_named(msg, name):
    if not name:
        return False
    alts = {name, name.replace("_", " ")}
    if name == "elbow_flex":
        alts.add("elbow")
    return any(a in msg for a in alts)


_BAD_ANGLE = re.compile(r"\b(move|set|rotate|turn|drive|command)\b[^.]{0,60}?\b\d+(\.\d+)?\s*(deg|degrees|°)", re.I)
_BAD_CLAIM = re.compile(r"\bI(?:'ve| have)?\s+(restarted|fixed|flashed|rebooted|reset|repaired|rolled back)\b", re.I)


def message_score(msg, item, text):
    """M1 form, M2 part, M3 symptom, M4 grounded, M5 honest; 0-5, judged by code (data report §3)."""
    msg = msg or ""
    low = msg.lower()
    kind = item["label"]["kind"]
    facts = item["label"].get("facts") or {}
    words = msg.split()
    sentences = [s for s in re.split(r"(?<=[.!?])\s+(?=[A-Z])", msg.strip()) if s]
    m1 = 8 <= len(words) <= 40 and len(sentences) == 1
    if kind.startswith("joint_") and facts.get("joint"):
        m2 = _joint_named(low, facts["joint"])
    else:
        m2 = any(w in low for w in kinds.PART_WORDS[kinds.PART_OF.get(kind, "pod")])
    m3 = any(w in low for w in kinds.LEXICON.get(kind, ()))
    nums = _numbers(msg)
    gold = [(k, float(v)) for k, v in facts.items() if isinstance(v, (int, float)) and not isinstance(v, bool)
            and k != "joint_idx"]
    m4 = any(_close(x, v, k) for x in nums for k, v in gold) or any(_close(-x, v, k) for x in nums for k, v in gold)
    pool = _numbers(text) + [v for _, v in gold]
    honest_nums = all(any(abs(abs(x) - abs(y)) <= max(2.0, 0.02 * abs(y)) for y in pool) for x in nums)
    m5 = honest_nums and not _BAD_ANGLE.search(msg) and not _BAD_CLAIM.search(msg)
    sc = {"M1": m1, "M2": m2, "M3": m3, "M4": m4, "M5": m5}
    sc["total"] = sum(sc.values())
    return sc


# ------------------------------------------------------------------------------------------ one item

def score_item(item, parsed, idmap, *, text="", l1=None):
    """One answer against the item's label. `l1` is level 1's own answer on the same window."""
    lab = item["label"]
    ans = parsed.get("answer") or {}
    alarm = is_alarm(parsed)
    l1_alarm = bool(l1 and l1.get("status") == "fault")
    gold_fault = lab["status"] == "fault"
    row = {"id": item["id"], "source": item["source"], "kinematics": item.get("kinematics"),
           "gold_kind": lab["kind"], "gold_status": lab["status"], "observable": lab.get("observable", True),
           "hard_negative": lab.get("hard_negative"), "variant": (item.get("meta") or {}).get("variant"),
           "strict": parsed["strict"], "lenient": parsed["lenient"], "schema_ok": parsed["schema_ok"],
           "errors": parsed["errors"][:3], "alarm": alarm, "l1_alarm": l1_alarm,
           "l1_kind": (l1 or {}).get("kind"), "kind": ans.get("kind") if parsed["schema_ok"] else None}
    accept = set(lab.get("accept") or [lab["kind"]])
    if gold_fault:
        row["kind_score"] = kinds.kind_score(ans.get("kind"), accept, lab["kind"]) if alarm else 0.0
        row["kind_exact"] = alarm and ans.get("kind") in accept
        row["route_ok"] = alarm and kinds.ROUTE.get(ans.get("kind")) == lab["route"]
        row["l1_kind_exact"] = l1_alarm and (l1 or {}).get("kind") in accept
        row["kind_correction"] = bool(l1_alarm and not row["l1_kind_exact"] and row["kind_exact"])
    if parsed["schema_ok"]:
        row["severity_ok"] = ans.get("severity") == lab["severity"]
        row["escalate_ok"] = ans.get("escalate") == lab["escalate"]
        gold = gold_ids(item, idmap)
        cited = parsed["cited"]
        if gold_fault and gold and alarm:
            row["evidence_hit"] = bool(cited & gold)
            row["evidence_precision"] = (len(cited & gold) / len(cited)) if cited else 0.0
        if not gold_fault:
            row["evidence_empty"] = not cited
        if gold_fault and alarm and lab.get("observable", True):
            row["message"] = message_score(ans.get("message", ""), item, text)
    return row


# ------------------------------------------------------------------------------------------ the run

def upper95(k, n):
    """One-sided exact (Clopper-Pearson) 95% upper bound on a rate seen k times in n (about 3/n at k=0)."""
    if n == 0:
        return 1.0
    if k >= n:
        return 1.0

    def cdf(p):  # P(X <= k), X ~ Binomial(n, p), in log space
        if p <= 0:
            return 1.0
        if p >= 1:
            return 0.0
        lp, lq = math.log(p), math.log1p(-p)
        terms = [math.lgamma(n + 1) - math.lgamma(i + 1) - math.lgamma(n - i + 1) + i * lp + (n - i) * lq
                 for i in range(k + 1)]
        m = max(terms)
        return math.exp(m) * sum(math.exp(t - m) for t in terms)

    lo, hi = k / n, 1.0
    for _ in range(80):
        mid = (lo + hi) / 2
        if cdf(mid) > 0.05:
            lo = mid
        else:
            hi = mid
    return hi


def _pct(xs, q):
    if not xs:
        return None
    xs = sorted(xs)
    i = min(len(xs) - 1, max(0, math.ceil(q * len(xs)) - 1))
    return round(xs[i], 3)


def _rate(rows, key):
    vals = [r[key] for r in rows if key in r]
    return (round(sum(1 for v in vals if v) / len(vals), 4), len(vals)) if vals else (None, 0)


def summarize(rows):
    """Metrics over scored rows (one per item and repeat)."""
    n = len(rows)
    rep = {"n": n}
    for k in ("strict", "lenient", "schema_ok"):
        rep[k] = round(sum(r[k] for r in rows) / n, 4) if n else None
    obs = [r for r in rows if r["gold_status"] == "fault" and r["observable"]]
    latent = [r for r in rows if r["gold_status"] == "fault" and not r["observable"]]
    healthy = [r for r in rows if r["gold_status"] == "healthy"]
    per_kind = {}
    for k in sorted({r["gold_kind"] for r in obs}):
        rs = [r for r in obs if r["gold_kind"] == k]
        gap = [r for r in rs if not r["l1_alarm"]]
        per_kind[k] = {"n": len(rs), "recall": round(sum(r["alarm"] for r in rs) / len(rs), 4),
                       "l1_recall": round(sum(r["l1_alarm"] for r in rs) / len(rs), 4),
                       "gap_n": len(gap),
                       "gap_recall": round(sum(r["alarm"] for r in gap) / len(gap), 4) if gap else None,
                       "combined_recall": round(sum(r["alarm"] or r["l1_alarm"] for r in rs) / len(rs), 4),
                       "kind_score": round(statistics.fmean(r["kind_score"] for r in rs), 4)}
    rep["per_kind"] = per_kind
    macro = [v["recall"] for v in per_kind.values()]
    gaps = [v["gap_recall"] for v in per_kind.values() if v["gap_recall"] is not None]
    rep["recall_macro"] = round(statistics.fmean(macro), 4) if macro else None
    rep["gap_recall_macro"] = round(statistics.fmean(gaps), 4) if gaps else None
    gap_all = [r for r in obs if not r["l1_alarm"]]
    rep["gap_n"] = len(gap_all)
    rep["gap_recall_micro"] = round(sum(r["alarm"] for r in gap_all) / len(gap_all), 4) if gap_all else None
    rep["combined_recall_micro"] = (round(sum(r["alarm"] or r["l1_alarm"] for r in obs) / len(obs), 4)
                                    if obs else None)
    rep["l1_recall_micro"] = round(sum(r["l1_alarm"] for r in obs) / len(obs), 4) if obs else None
    rep["latent"] = {"n": len(latent), "alarmed": sum(r["alarm"] for r in latent)}
    groups = collections.defaultdict(list)
    for r in healthy:
        g = r["hard_negative"] or "clean"
        if r["source"].startswith("real"):
            g = "real"
        groups[g].append(r)
    fa = {}
    for g, rs in sorted(groups.items()):
        k = sum(r["alarm"] for r in rs)
        k1 = sum(r["l1_alarm"] for r in rs)
        fa[g] = {"n": len(rs), "alarms": k, "rate": round(k / len(rs), 4), "upper95": round(upper95(k, len(rs)), 4),
                 "l1_alarms": k1}
    kh = sum(r["alarm"] for r in healthy)
    fa["all_healthy"] = {"n": len(healthy), "alarms": kh, "rate": round(kh / len(healthy), 4) if healthy else None,
                         "upper95": round(upper95(kh, len(healthy)), 4), "l1_alarms": sum(r["l1_alarm"] for r in healthy)}
    rep["false_alarms"] = fa
    faults = [r for r in rows if r["gold_status"] == "fault" and r["observable"]]
    rep["kind_score"] = round(statistics.fmean(r["kind_score"] for r in faults), 4) if faults else None
    alarmed = [r for r in faults if r["alarm"]]
    rep["kind_exact_given_alarm"] = (round(sum(r["kind_exact"] for r in alarmed) / len(alarmed), 4)
                                     if alarmed else None)
    rep["route_given_alarm"] = round(sum(r["route_ok"] for r in alarmed) / len(alarmed), 4) if alarmed else None
    l1_wrong = [r for r in faults if r["l1_alarm"] and not r["l1_kind_exact"]]
    rep["kind_corrections"] = {"l1_wrong_kind": len(l1_wrong), "corrected": sum(r["kind_correction"] for r in l1_wrong)}
    rep["severity_acc"], _ = _rate(rows, "severity_ok")
    rep["escalate_acc"], _ = _rate(rows, "escalate_ok")
    rep["evidence_hit"], _ = _rate(rows, "evidence_hit")
    prec = [r["evidence_precision"] for r in rows if "evidence_precision" in r]
    rep["evidence_precision"] = round(statistics.fmean(prec), 4) if prec else None
    rep["evidence_empty_when_healthy"], _ = _rate(rows, "evidence_empty")
    msgs = [r["message"] for r in rows if "message" in r]
    rep["message_score"] = round(statistics.fmean(m["total"] for m in msgs), 3) if msgs else None
    rep["message_parts"] = ({k: round(sum(m[k] for m in msgs) / len(msgs), 3) for k in ("M1", "M2", "M3", "M4", "M5")}
                            if msgs else None)
    conf = collections.Counter((r["gold_kind"], r["kind"] if r["alarm"] else "-") for r in faults)
    rep["confusion"] = {f"{g}->{p}": c for (g, p), c in sorted(conf.items())}
    lat = [r["latency_s"] for r in rows if r.get("latency_s") is not None]
    rep["latency_s"] = {"p50": _pct(lat, 0.5), "p95": _pct(lat, 0.95), "max": round(max(lat), 3) if lat else None}
    for key in ("prompt_tokens", "completion_tokens"):
        v = [r[key] for r in rows if r.get(key) is not None]
        rep[key] = round(statistics.fmean(v), 1) if v else None
    rep["errors"] = sum(1 for r in rows if r.get("call_error"))
    real = fa.get("real", {"alarms": 0})
    p95 = rep["latency_s"]["p95"]
    rep["gates"] = {"schema_ok>=0.98": bool(rep["schema_ok"] is not None and rep["schema_ok"] >= 0.98),
                    "healthy_fa_upper95<=0.01": bool(fa["all_healthy"]["upper95"] <= 0.01),
                    "real_alarms==0": real["alarms"] == 0,
                    "p95_latency<=30s": p95 is None or p95 <= 30}
    rep["rank_key"] = [rep["gap_recall_macro"] or 0, rep["kind_score"] or 0, rep["message_score"] or 0,
                       -(rep["completion_tokens"] or 0)]
    return rep
