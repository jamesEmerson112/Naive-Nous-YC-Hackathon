"""Naïve Technician: the report, the gate and the feed lines (docs/naive-technician/design.md
§2.1, §4, §8; D30). Pure.

The Naïve agent's single output is a typed report. The model proposes, code disposes:
- `report_schema(vendors)` is the JSON Schema sent with each session, **written by hand**
  (no `$ref`, `format` or `anyOf`: the Naïve docs don't say which keywords are supported),
  built per session so `part.origin` is an enum of the allowlisted vendors.
- `validate_report` checks what comes back against that same schema, so the two can't drift.
- `actions_from_report` works out what Romulus does with it; the gate functions check each
  action in plain code over facts the service fetched (the recipient comes from settings,
  the price from the vendor's store). Nothing here sends anything.
- `feed_line` turns one session event into a short line for the "agent at work" column.
"""

import re
from collections.abc import Mapping, Sequence
from typing import Any, Protocol

JOINTS = (
    "shoulder_pan",
    "shoulder_lift",
    "elbow_flex",
    "wrist_flex",
    "wrist_roll",
    "gripper",
    "sensor_pod",
    "unknown",
)
"""SO-101 joints (Feetech STS3215 bus servo ids 1-6), the STM32 pod, or unknown."""
SERVO_IDS = {j: i for i, j in enumerate(JOINTS[:6], start=1)}

VERDICT_MAX = 80
CLAIM_MAX = 160
SOURCE_TITLE_MAX = 100
PART_WHY_MAX = 200
SUBJECT_MAX = 45
BODY_MAX = 1200
QTY_MAX = 2
HYPOTHESES_MAX = 3
SOURCES_MAX = 4
FEED_MAX = 60
FEED_TEXT_MAX = 90

VERDICT_HINT = (
    "Plain words for a video caption, at most ~8 words, no joint ids, numbers or units. "
    "Example: Elbow blocked or servo failing: needs hands"
)

FAILED_STOP_REASONS = {
    "awaiting_approval": "the session parked on awaiting_approval (a tool asked for approval)",
    "awaiting_answer": "the session parked on awaiting_answer (the agent asked the operator)",
    "error": "the session ended with an error",
    "max_iterations": "the session hit its iteration limit",
    "context_exhausted": "the session ran out of context",
    "interrupted": "the session was interrupted",
    "budget_paused": "the session hit its budget cap and paused",
    "cancelled": "the session was cancelled",
}
"""Naïve `stop_reason` → why the case failed (F:6093; design §2)."""


# --- the report: schema and validation -----------------------------------------------------


def _str(max_len: int, **extra: Any) -> dict[str, Any]:
    return {"type": "string", "maxLength": max_len, **extra}


def _obj(props: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": props,
        "required": list(props),
        "additionalProperties": False,
    }


def vendor_origins(vendors: Sequence[Mapping[str, Any]]) -> list[str]:
    """The allowlisted origins, in order, without repeats."""
    out: list[str] = []
    for v in vendors:
        origin = str(v.get("origin") or "").rstrip("/")
        if origin and origin not in out:
            out.append(origin)
    return out


def vendors_for_arm(
    vendors: Sequence[Mapping[str, Any]], servo_variant: str
) -> list[dict[str, Any]]:
    """The vendor entries for the arm's servo variant (`C018-12V` matches a label naming
    `C018`); all of them when none matches, so a mislabelled list never hides every part."""
    key = servo_variant.split("-")[0].strip().upper()
    found = [dict(v) for v in vendors if key and key in str(v.get("label") or "").upper()]
    return found or [dict(v) for v in vendors]


def report_schema(vendors: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """The report's JSON Schema for one session. `part.origin` is an enum of the vendors'
    origins, so the model can only name an allowlisted store (design §2.1)."""
    part = _obj(
        {
            "origin": {"type": "string", "enum": vendor_origins(vendors)},
            "handle": {"type": "string", "pattern": "^[a-z0-9-]+$", "maxLength": 200},
            "variant_id": {"type": "string", "pattern": "^[0-9]+$", "maxLength": 30},
            "qty": {"type": "integer", "minimum": 1, "maximum": QTY_MAX},
            "why": _str(PART_WHY_MAX),
        }
    )
    part["type"] = ["object", "null"]
    return _obj(
        {
            "verdict": _str(VERDICT_MAX, minLength=1, description=VERDICT_HINT),
            "confidence": {"type": "string", "enum": ["low", "medium", "high"]},
            "joint": {"type": "string", "enum": list(JOINTS)},
            "hypotheses": {
                "type": "array",
                "minItems": 1,
                "maxItems": HYPOTHESES_MAX,
                "items": _obj({"claim": _str(CLAIM_MAX), "check": _str(CLAIM_MAX)}),
            },
            "sources": {
                "type": "array",
                "minItems": 0,
                "maxItems": SOURCES_MAX,
                "items": _obj(
                    {
                        "title": _str(SOURCE_TITLE_MAX),
                        "url": {"type": "string", "pattern": "^https://", "maxLength": 500},
                    }
                ),
            },
            "part_needed": {"type": "string", "enum": ["yes", "no", "spare"]},
            "part": part,
            "technician_brief": _obj(
                {"subject": _str(SUBJECT_MAX, minLength=1), "body": _str(BODY_MAX, minLength=1)}
            ),
        }
    )


_TYPES: dict[str, type | tuple[type, ...]] = {
    "string": str,
    "integer": int,
    "number": (int, float),
    "boolean": bool,
    "object": dict,
    "array": list,
    "null": type(None),
}


def _type_ok(value: Any, name: str) -> bool:
    if name in ("integer", "number") and isinstance(value, bool):
        return False
    return isinstance(value, _TYPES[name])


def _check(value: Any, schema: Mapping[str, Any], path: str, errors: list[str]) -> None:
    """The subset of JSON Schema that `report_schema` uses."""
    types = schema.get("type")
    names = [types] if isinstance(types, str) else list(types or [])
    if names and not any(_type_ok(value, n) for n in names):
        errors.append(f"{path}: expected {' or '.join(names)}, got {type(value).__name__}")
        return
    if value is None:
        return
    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"{path}: {value!r} is not one of {schema['enum']}")
    if isinstance(value, str):
        if len(value) > schema.get("maxLength", len(value)):
            errors.append(f"{path}: longer than {schema['maxLength']} characters")
        if len(value) < schema.get("minLength", 0):
            errors.append(f"{path}: shorter than {schema['minLength']} characters")
        if "pattern" in schema and not re.search(schema["pattern"], value):
            errors.append(f"{path}: does not match {schema['pattern']}")
    if isinstance(value, int) and not isinstance(value, bool):
        if value < schema.get("minimum", value) or value > schema.get("maximum", value):
            errors.append(f"{path}: {value} is out of range")
    if isinstance(value, list):
        if not schema.get("minItems", 0) <= len(value) <= schema.get("maxItems", len(value)):
            errors.append(f"{path}: has {len(value)} items")
        for i, item in enumerate(value):
            _check(item, schema.get("items", {}), f"{path}[{i}]", errors)
    if isinstance(value, dict):
        props = schema.get("properties", {})
        for key in schema.get("required", []):
            if key not in value:
                errors.append(f"{path}.{key}: missing")
        if schema.get("additionalProperties") is False:
            for key in value:
                if key not in props:
                    errors.append(f"{path}.{key}: not allowed")
        for key, sub in props.items():
            if key in value:
                _check(value[key], sub, f"{path}.{key}", errors)


def validate_report(
    data: Any, vendors: Sequence[Mapping[str, Any]], *, strict: bool = True
) -> list[str]:
    """Why `data` is not a valid report for these vendors ([] = valid). With `strict=False`,
    text that is only too long is not an error: `clip_report` shortens it (a 46-character
    subject shouldn't sink a good diagnosis)."""
    errors: list[str] = []
    _check(data, report_schema(vendors), "report", errors)
    if not strict:
        errors = [e for e in errors if "longer than" not in e]
    return errors


def _clip_to(value: Any, schema: Mapping[str, Any]) -> Any:
    if isinstance(value, str) and "maxLength" in schema and len(value) > schema["maxLength"]:
        return _clip(value, schema["maxLength"])
    if isinstance(value, list):
        return [_clip_to(v, schema.get("items", {})) for v in value]
    if isinstance(value, dict):
        props = schema.get("properties", {})
        return {k: _clip_to(v, props.get(k, {})) for k, v in value.items()}
    return value


def clip_report(data: Mapping[str, Any], vendors: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """`data` with every string cut to its schema's maxLength."""
    return _clip_to(dict(data), report_schema(vendors))


# --- from report to actions, and the gate ---------------------------------------------------


def actions_from_report(report: Mapping[str, Any], *, email_to: str) -> list[dict[str, Any]]:
    """What Romulus does with a valid report (design §4): email the technician whenever a
    recipient is set (from settings, never the model), and stage the part the report names.
    The agent lists no actions itself."""
    actions: list[dict[str, Any]] = []
    if email_to.strip():
        brief = report.get("technician_brief") or {}
        subject, body = clean_brief(str(brief.get("subject") or ""), str(brief.get("body") or ""))
        actions.append(
            {"kind": "email_technician", "to": email_to.strip(), "subject": subject, "text": body}
        )
    part = report.get("part")
    if isinstance(part, Mapping):
        actions.append({"kind": "stage_part", **dict(part)})
    return actions


_URL = re.compile(r"\b(?:https?|ftp)://\S+|\bwww\.\S+", re.IGNORECASE)
_DOMAIN = re.compile(
    r"\b(?:[A-Za-z0-9-]+\.)+(?:com|net|org|io|ai|co|sh|shop|store|dev|app|me|us|uk|cn|de|jp"
    r"|xyz|info|biz|tech|link|ly|gl|to|so|tv)\b(?:/\S*)?",
    re.IGNORECASE,
)


def strip_links(text: str) -> str:
    """`text` with URLs and bare domains removed: a page the agent read can't put a link
    in front of the technician (design §4)."""
    text = _URL.sub("", text)
    text = _DOMAIN.sub("", text)
    return re.sub(r"[ \t]{2,}", " ", text).strip()


def _clip(text: str, limit: int) -> str:
    text = text.strip()
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def clean_brief(subject: str, body: str) -> tuple[str, str]:
    """The email's subject (≤ 45, one line) and body (≤ 1,200), links stripped."""
    subject = _clip(" ".join(strip_links(subject).split()), SUBJECT_MAX)
    return subject, _clip(strip_links(body), BODY_MAX)


def email_skip_reason(*, to: str, frozen: bool) -> str | None:
    """Why the email must not go out now, or None."""
    if not to.strip():
        return "TECHNICIAN_EMAIL is empty, so no email is sent"
    if frozen:
        return "the fleet is frozen (kill switch, D11), so no email is sent"
    return None


def cart_permalink(origin: str, variant_id: str, qty: int) -> str:
    """Shopify's cart permalink: `{origin}/cart/{variant_id}:{qty}?storefront=true`."""
    return f"{origin.rstrip('/')}/cart/{variant_id}:{int(qty)}?storefront=true"


def host(url: str) -> str:
    """`https://shop.wowrobo.com/x` → `shop.wowrobo.com`."""
    m = re.match(r"^[a-z][a-z0-9+.-]*://([^/?#]+)", url.strip(), re.IGNORECASE)
    return (m.group(1) if m else url).lower().removeprefix("www.")


def vendor_label(origin: str, vendors: Sequence[Mapping[str, Any]]) -> str:
    """`WowRobo` from a vendor label like `WowRobo · …`, else the store's host."""
    for v in vendors:
        if str(v.get("origin") or "").rstrip("/") == origin.rstrip("/"):
            label = str(v.get("label") or "")
            if label:
                return label.split("·")[0].strip()
    return host(origin)


def part_checks(
    part: Mapping[str, Any],
    product: Mapping[str, Any] | None,
    *,
    vendors: Sequence[Mapping[str, Any]],
    order_max_usd: float,
    shop_error: str | None = None,
) -> dict[str, Any]:
    """Check the part the report named against the vendor's live product JSON (`product`, as
    `adapters/shop.product` returns it; None with `shop_error` when it couldn't be read).
    Returns the gate result: `staged` is true only when every check passed."""
    origin = str(part.get("origin") or "").rstrip("/")
    variant_id = str(part.get("variant_id") or "")
    qty = int(part.get("qty") or 1)
    allowed = origin in vendor_origins(vendors)
    variant = None
    if product:
        variant = next(
            (v for v in product.get("variants") or [] if str(v.get("id")) == variant_id), None
        )
    title = str((product or {}).get("title") or part.get("handle") or "part")
    if variant and variant.get("title") and variant["title"] != "Default Title":
        title = f"{title} ({variant['title']})"
    price_cents = variant.get("price_cents") if variant else None
    unit = price_cents / 100 if isinstance(price_cents, int | float) else None
    total = unit * qty if unit is not None else None
    in_stock = bool(variant and variant.get("available"))
    under_cap = total is not None and total <= order_max_usd
    checks = [
        {"label": "allowlisted vendor", "ok": allowed},
        {"label": "in stock", "ok": in_stock},
        {
            "label": "price checked",
            "ok": unit is not None,
            "detail": f"${unit:.2f} live" if unit is not None else None,
        },
        {"label": f"≤ ${order_max_usd:.0f}", "ok": under_cap},
    ]
    blocked = None
    if not allowed:
        blocked = f"{origin or 'the store'} is not an allowlisted vendor"
    elif product is None:
        blocked = f"couldn't check price: {shop_error or 'no answer from the store'}"
    elif variant is None:
        blocked = f"variant {variant_id} is not on {origin}/products/{part.get('handle')}"
    elif not in_stock:
        blocked = f"{title} is out of stock at {host(origin)}"
    elif unit is None:
        blocked = "the store gave no price"
    elif not under_cap:
        blocked = f"{qty} × ${unit:.2f} = ${total:.2f} is over ORDER_MAX_USD ${order_max_usd:.2f}"
    staged = blocked is None
    return {
        "origin": origin,
        "handle": part.get("handle"),
        "variant_id": variant_id,
        "qty": qty,
        "title": title,
        "vendor_label": vendor_label(origin, vendors),
        "origin_host": host(origin),
        "unit_usd": f"{unit:.2f}" if unit is not None else None,
        "total_usd": f"{total:.2f}" if total is not None else None,
        "cart_url": cart_permalink(origin, variant_id, qty) if staged else None,
        "checks": checks,
        "staged": staged,
        "why": str(part.get("why") or ""),
        "blocked_reason": blocked,
        "shop_error": shop_error if product is None else None,  # worth another try
    }


def session_outcome(
    *,
    state: str,
    stop_reason: str | None,
    has_output: bool,
    elapsed_s: float,
    timeout_s: float,
) -> tuple[str, str | None]:
    """What a sync tick does with the session: ("report", None) to read the report,
    ("wait", None) to look again next tick, or ("fail", why) to cancel and fail the case."""
    if state == "idle" and has_output:
        return "report", None
    if state == "running":
        if elapsed_s > timeout_s:
            return "fail", f"no report within {timeout_s:.0f} s (TECHNICIAN_TIMEOUT_S)"
        return "wait", None
    why = FAILED_STOP_REASONS.get(stop_reason or "") or FAILED_STOP_REASONS.get(state)
    if state == "idle" and not has_output and not why:
        why = f"the session ended ({stop_reason or 'idle'}) without a typed report"
    return "fail", why or f"the session is {state} ({stop_reason or 'no stop reason'})"


# --- the "agent at work" feed ----------------------------------------------------------------


class EventLike(Protocol):
    """What `feed_line` reads from an `adapters.naive.SessionEvent`."""

    kind: str
    tool: str | None
    args: Mapping[str, Any]
    text: str | None


def _short_url(url: str, limit: int = 60) -> str:
    m = re.match(r"^[a-z][a-z0-9+.-]*://([^?#]*)", url.strip(), re.IGNORECASE)
    rest = (m.group(1) if m else url).removeprefix("www.").rstrip("/")
    return _clip(rest, limit)


def _quoted(text: Any, limit: int = 60) -> str:
    return f"“{_clip(' '.join(str(text).split()), limit)}”"


def _rejection(text: str) -> str:
    """`subject too long` from Naïve's "- technician_brief.subject: must not have more than
    45 characters"; else the first field named, else `invalid`."""
    m = re.search(r"-\s*([\w.\[\]]+):\s*([^\n]*)", text)
    if not m:
        return "invalid"
    field = m.group(1).split(".")[-1]
    return f"{field} too long" if "more than" in m.group(2) else f"{field} invalid"


def feed_line(e: EventLike) -> str:
    """One short line for an event ("Searched “STS3215 stall”", "Read shop.wowrobo.com/…"),
    or "" for events the feed leaves out (tool results, bookkeeping)."""
    args = e.args if isinstance(e.args, Mapping) else {}
    tool = (e.tool or "").lower()
    if e.kind == "tool_call":
        if tool == "web_search":
            return f"Searched {_quoted(args.get('query') or args.get('q') or '…')}"
        if tool == "web_fetch":
            url = str(args.get("url") or "")
            return f"Read {_short_url(url)}" if url else "Read a web page"
        if tool == "data_search":
            return f"Searched its data for {_quoted(args.get('query') or '…')}"
        if tool == "data_run":
            return "Ran a data query"
        if tool == "submit_output":
            return "Submitted the typed report"
        if tool in ("ask_operator", "ask"):
            return "Asked the operator a question"
        return _clip(f"Used {tool or 'a tool'}", FEED_TEXT_MAX)
    if e.kind == "tool_result" and tool == "submit_output" and (e.text or "").startswith("error"):
        return f"Report rejected by schema ({_rejection(e.text or '')})"
    if e.kind == "message" and e.text:
        return _clip(" ".join(e.text.replace("**", "").split()), FEED_TEXT_MAX)
    if e.kind == "output":
        return "Submitted the typed report"
    return ""
