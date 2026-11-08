"""domain/technician.py: the report schema and its validation, the gate rules, the cart
permalink and the feed lines (docs/naive-technician/design.md §2.1, §4, §8)."""

import copy
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from romulus_cloud.config import load_parts
from romulus_cloud.domain import technician as t
from romulus_cloud.settings import Settings

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "technician"
ORIGIN = "https://shop.wowrobo.com"
HANDLE = "feetech-sts3215-servo-12v-30kg-high-torque-servo-for-so-arm100"
C018 = "48037575033049"
VENDORS = Settings.model_fields["technician_vendors"].default

REPORT: dict[str, Any] = {
    "verdict": "Elbow blocked or servo failing: needs hands",
    "confidence": "medium",
    "joint": "elbow_flex",
    "hypotheses": [
        {"claim": "Something is jamming the elbow", "check": "Power off; move the elbow by hand"},
        {"claim": "The elbow servo (id 3) has failed", "check": "Swap in the spare servo"},
    ],
    "sources": [{"title": "Feetech STS3215 manual", "url": "https://www.feetechrc.com/sts3215"}],
    "part_needed": "spare",
    "part": {"origin": ORIGIN, "handle": HANDLE, "variant_id": C018, "qty": 1, "why": "spare"},
    "technician_brief": {
        "subject": "arm-1 down: clear elbow jam, spare servo set",
        "body": "The elbow stalled twice. Check for a jam first.",
    },
}
PRODUCT = {
    "title": "Feetech STS3215 Servo 12V",
    "variants": [
        {"id": C018, "title": "C018 12V", "price_cents": 1599, "available": True},
        {"id": "48037674090713", "title": "C001 7.4V", "price_cents": 1599, "available": True},
        {"id": "5", "title": "5-pack", "price_cents": 7495, "available": True},
    ],
}


def _report(**changes: Any) -> dict[str, Any]:
    return copy.deepcopy(REPORT) | changes


# --- the catalogue gap stays open on purpose -----------------------------------------------


def test_joint_stall_and_us_no_echo_stay_uncatalogued():
    """Adding these to parts.yaml would silently turn the Technician off (design §1)."""
    catalog = load_parts()
    assert catalog.for_kind("joint_stall") is None
    assert catalog.for_kind("us_no_echo") is None
    assert catalog.for_kind("servo_absent") is not None  # the catalogued path stays


# --- schema ------------------------------------------------------------------------------


def test_schema_snapshot():
    s = t.report_schema(VENDORS)
    assert s["type"] == "object" and s["additionalProperties"] is False
    assert s["required"] == [
        "verdict",
        "confidence",
        "joint",
        "hypotheses",
        "sources",
        "part_needed",
        "part",
        "technician_brief",
    ]
    part = s["properties"]["part"]
    assert part["type"] == ["object", "null"]
    assert part["properties"]["origin"] == {"type": "string", "enum": [ORIGIN]}
    assert part["properties"]["qty"] == {"type": "integer", "minimum": 1, "maximum": 2}
    assert s["properties"]["verdict"]["maxLength"] == 80
    assert s["properties"]["verdict"]["description"].startswith("Plain words for a video caption")
    assert s["properties"]["technician_brief"]["properties"]["subject"]["maxLength"] == 45
    assert s["properties"]["technician_brief"]["properties"]["body"]["maxLength"] == 1200
    assert s["properties"]["hypotheses"]["maxItems"] == 3
    assert s["properties"]["sources"]["items"]["properties"]["url"]["pattern"] == "^https://"
    # Hand-written: none of the keywords the Naïve docs don't promise.
    text = json.dumps(s)
    for keyword in ('"$ref"', '"format"', '"anyOf"', '"oneOf"', '"allOf"'):
        assert keyword not in text


def test_a_good_report_validates():
    assert t.validate_report(_report(), VENDORS) == []
    assert t.validate_report(_report(part=None, part_needed="no"), VENDORS) == []


@pytest.mark.parametrize(
    "changes, expected",
    [
        ({"confidence": "certain"}, "report.confidence"),
        ({"joint": "knee"}, "report.joint"),
        ({"verdict": "x" * 81}, "report.verdict"),
        ({"hypotheses": []}, "report.hypotheses"),
        ({"sources": [{"title": "a", "url": "http://insecure"}]}, "report.sources[0].url"),
        ({"technician_brief": {"subject": "s" * 46, "body": "b"}}, "subject"),
        ({"extra": 1}, "report.extra: not allowed"),
    ],
)
def test_bad_reports_are_refused(changes, expected):
    errors = t.validate_report(_report(**changes), VENDORS)
    assert any(expected in e for e in errors), errors


def test_too_long_text_is_clipped_not_fatal_when_lenient():
    """The build plan's own test subject is 46 characters: lenient validation clips it."""
    long = _report(
        technician_brief={"subject": "arm-1 down: clear elbow jam, spare servo ready", "body": "b"}
    )
    assert t.validate_report(long, VENDORS)  # strict: too long
    assert t.validate_report(long, VENDORS, strict=False) == []
    clipped = t.clip_report(long, VENDORS)
    assert len(clipped["technician_brief"]["subject"]) == 45
    assert t.validate_report(clipped, VENDORS) == []
    assert t.validate_report(_report(joint="knee"), VENDORS, strict=False)  # enums stay strict


def test_part_origin_must_be_allowlisted_and_ids_exact():
    bad = _report()
    bad["part"] = dict(bad["part"], origin="https://evil.example.com")
    assert any("origin" in e for e in t.validate_report(bad, VENDORS))
    bad["part"] = dict(REPORT["part"], variant_id="4803x")
    assert any("variant_id" in e for e in t.validate_report(bad, VENDORS))
    bad["part"] = dict(REPORT["part"], qty=3)
    assert any("qty" in e for e in t.validate_report(bad, VENDORS))
    bad["part"] = dict(REPORT["part"], qty=True)
    assert any("qty" in e for e in t.validate_report(bad, VENDORS))


def test_a_recorded_fixture_report_validates():
    """The fake (and later live) fixture's structured output passes our own check."""
    for name, strict in (
        ("joint_stall_fake.json", True),
        ("joint_stall_live2.json", True),  # recorded with the 80-character verdict rule
        ("joint_stall_live.json", False),  # recorded before it: its verdict gets clipped
    ):
        path = FIXTURES / name
        if not path.is_file():
            continue
        output = (json.loads(path.read_text()).get("session") or {}).get("structured_output")
        assert t.validate_report(output, VENDORS, strict=strict) == [], name
        assert t.validate_report(t.clip_report(output, VENDORS), VENDORS) == [], name


# --- actions and the gate -------------------------------------------------------------------


def test_actions_come_from_the_report_and_settings_not_the_model():
    actions = t.actions_from_report(_report(), email_to="tech@example.com")
    assert [a["kind"] for a in actions] == ["email_technician", "stage_part"]
    assert actions[0]["to"] == "tech@example.com"
    assert actions[1]["variant_id"] == C018
    assert [a["kind"] for a in t.actions_from_report(_report(part=None), email_to="")] == []


def test_the_brief_loses_links_and_is_clipped():
    subject, body = t.clean_brief(
        "see https://x.io now " + "s" * 60,
        "Buy at shop.wowrobo.com/products/x or https://evil.example.com/a?b=1 today. " + "b" * 2000,
    )
    assert len(subject) <= t.SUBJECT_MAX and "https" not in subject
    assert len(body) <= t.BODY_MAX
    assert "wowrobo" not in body and "evil" not in body and "http" not in body
    assert body.startswith("Buy at or today.")


def test_email_skip_reasons():
    assert t.email_skip_reason(to="", frozen=False)
    assert "frozen" in (t.email_skip_reason(to="a@b.c", frozen=True) or "")
    assert t.email_skip_reason(to="a@b.c", frozen=False) is None


def test_cart_permalink():
    assert t.cart_permalink(ORIGIN + "/", C018, 1) == (
        f"https://shop.wowrobo.com/cart/{C018}:1?storefront=true"
    )


def test_part_checks_pass_on_the_exact_variant():
    got = t.part_checks(REPORT["part"], PRODUCT, vendors=VENDORS, order_max_usd=50)
    assert got["staged"] is True and got["blocked_reason"] is None
    assert (got["unit_usd"], got["total_usd"]) == ("15.99", "15.99")
    assert got["cart_url"] == t.cart_permalink(ORIGIN, C018, 1)
    assert got["vendor_label"] == "WowRobo" and got["origin_host"] == "shop.wowrobo.com"
    assert [c["ok"] for c in got["checks"]] == [True, True, True, True]
    assert got["checks"][2]["detail"] == "$15.99 live"


@pytest.mark.parametrize(
    "part, product, cap, reason",
    [
        (dict(REPORT["part"], qty=2), PRODUCT, 20, "over ORDER_MAX_USD"),
        (
            REPORT["part"],
            {"title": "x", "variants": [dict(PRODUCT["variants"][0], available=False)]},
            50,
            "out of stock",
        ),
        (dict(REPORT["part"], variant_id="999"), PRODUCT, 50, "variant 999 is not on"),
        (dict(REPORT["part"], origin="https://other.example"), PRODUCT, 50, "not an allowlisted"),
        (REPORT["part"], None, 50, "couldn't check price: timeout"),
    ],
)
def test_part_checks_block_with_a_reason(part, product, cap, reason):
    got = t.part_checks(part, product, vendors=VENDORS, order_max_usd=cap, shop_error="timeout")
    assert got["staged"] is False and got["cart_url"] is None
    assert reason in got["blocked_reason"]


def test_session_outcomes():
    kw = {"elapsed_s": 10, "timeout_s": 600}
    assert t.session_outcome(state="idle", stop_reason="end_turn", has_output=True, **kw) == (
        "report",
        None,
    )
    assert t.session_outcome(state="running", stop_reason=None, has_output=False, **kw)[0] == "wait"
    late = t.session_outcome(
        state="running", stop_reason=None, has_output=False, elapsed_s=601, timeout_s=600
    )
    assert late[0] == "fail" and "600 s" in late[1]
    parked = t.session_outcome(
        state="awaiting", stop_reason="awaiting_answer", has_output=False, **kw
    )
    assert parked[0] == "fail" and "awaiting_answer" in parked[1]
    empty = t.session_outcome(state="idle", stop_reason="end_turn", has_output=False, **kw)
    assert empty[0] == "fail" and "without a typed report" in empty[1]


# --- feed lines ------------------------------------------------------------------------------


@dataclass
class Ev:
    kind: str
    tool: str | None = None
    args: dict[str, Any] = field(default_factory=dict)
    text: str | None = None


@pytest.mark.parametrize(
    "event, line",
    [
        (
            Ev("tool_call", "web_search", {"query": "STS3215 stall elbow"}),
            "Searched “STS3215 stall elbow”",
        ),
        (
            Ev("tool_call", "web_fetch", {"url": f"{ORIGIN}/products/{HANDLE}.js"}),
            "Read shop.wowrobo.com/products/feetech-sts3215-servo-12v-30kg-hi…",
        ),
        (Ev("tool_call", "web_fetch", {"url": "https://www.feetechrc.com/"}), "Read feetechrc.com"),
        (Ev("tool_call", "data_search", {"query": "elbow"}), "Searched its data for “elbow”"),
        (Ev("tool_call", "data_run"), "Ran a data query"),
        (Ev("tool_call", "submit_output"), "Submitted the typed report"),
        (Ev("message", text="Checking  the\nservo"), "Checking the servo"),
        (Ev("tool_result", "web_search"), ""),
        (
            Ev(
                "tool_result",
                "submit_output",
                text='error: Validation failed for tool "submit_output": - '
                "technician_brief.subject: must not have more than 45 characters",
            ),
            "Report rejected by schema (subject too long)",
        ),
        (Ev("tool_result", "submit_output", text="Final result recorded."), ""),
        (Ev("other", text="span.started"), ""),
        (Ev("status"), ""),
    ],
)
def test_feed_lines_name_real_places(event, line):
    assert t.feed_line(event) == line


def test_feed_lines_are_short():
    long = Ev("tool_call", "web_search", {"query": "q" * 500})
    assert len(t.feed_line(long)) < 80
