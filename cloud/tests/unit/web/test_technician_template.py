"""The Naïve Technician case page (design §8): both templates render every sample view
(`tests/fixtures/technician/view_sample.json`, the Interface 3 shape) through the app's
own Jinja environment, with no database. The checks are the clip's acceptance criteria:
header, verdict, part card ticks, the one approval, the email card and the replay badge."""

import copy
import json
from pathlib import Path

import pytest

from romulus_cloud.api.web import templates

SAMPLES = json.loads(
    (Path(__file__).parents[2] / "fixtures" / "technician" / "view_sample.json").read_text()
)
STATES = ["diagnosing", "waiting", "done", "replay"]


def page(v: dict) -> str:
    """The full page, with the context the route passes besides `request`."""
    return templates.env.get_template("technician.html").render(
        v=v, fleet={"frozen": False}, tz="America/Los_Angeles"
    )


def partial(v: dict) -> str:
    return templates.env.get_template("partials/technician_case.html").render(v=v)


def sample(state: str) -> dict:
    return copy.deepcopy(SAMPLES[state])


@pytest.mark.parametrize("state", STATES)
def test_every_state_renders_the_header_stages_and_numbers(state):
    v = sample(state)
    for html in (page(v), partial(v)):
        assert 'id="tech-case"' in html
        assert "Naïve agent" in html and "technician" in html
        assert v["badge"] in html
        assert v["duration_label"] in html and v["cost_label"] in html
        assert "human click" in html
        for stage in v["stages"]:
            if stage["state"] not in ("skipped", "failed"):  # those read "email not sent" etc.
                assert stage["label"] in html
        assert v["feed"][-1]["text"] in html
        assert v["studio_url"] in html


def test_page_has_the_nav_line_and_the_approve_handler():
    html = page(sample("waiting"))
    assert '<a class="nav" href="/technician">Technician</a>' in html
    # The cart opens inside the click, before the first await, then the resolve POST.
    script = html[html.index('data-tech-action]")') :]
    assert script.index("window.open(") < script.index("await fetch(")
    assert "JSON.stringify({action: action, by: by, why: why})" in html


def test_diagnosing_polls_and_shows_a_working_state():
    html = partial(sample("diagnosing"))
    assert 'hx-get="/technician/tc_01jcx9e2m4/partial"' in html
    assert "every 2s" in html
    assert "Diagnosing" in html and "Agent at work" in html
    assert "Waiting for the agent's report" in html
    assert "data-tech-action" not in html


def test_waiting_shows_verdict_part_ticks_email_and_both_buttons():
    v = sample("waiting")
    html = partial(v)
    assert "hx-get" not in html  # settled: no polling
    assert "“Elbow blocked or servo failing: needs hands”" in html
    assert 'class="tc-verdict-text"' in html
    assert "Feetech STS3215 C018 12V" in html and "WowRobo" in html and "$15.99" in html
    for label in ("allowlisted vendor", "in stock", "price checked", "≤ $50"):
        assert label in html
    assert html.count('<li class="ok"') == 4
    assert 'data-tech-action="approve"' in html and 'data-tech-action="deny"' in html
    assert 'data-cart="https://shop.wowrobo.com/cart/48037575033049:1?storefront=true"' in html
    assert 'data-url="/v1/work-orders/wo_01jcx9e1ka/resolve"' in html
    assert "Approve &amp; open cart" in html
    assert "Technician emailed" in html and "tech@naivenous.dev" in html and "sent 07:44" in html
    assert "arm-1 down: clear elbow jam, spare servo ready" in html
    assert "Romulus Technician (Naïve agent)" in html
    assert v["email"]["via"].replace("'", "&#39;") in html
    for h in v["hypotheses"]:
        assert h["claim"].replace("'", "&#39;") in html
    for s in v["sources"]:
        assert s["url"] in html


def test_done_shows_who_approved_and_no_buttons():
    html = partial(sample("done"))
    assert "Approved by kent" in html
    assert "open the cart again" in html
    assert "data-tech-action" not in html
    assert "1</span> human click<" in html


def test_buttons_follow_can_approve_and_can_deny():
    v = sample("waiting")
    v["can_approve"] = False
    html = partial(v)
    assert 'data-tech-action="approve"' not in html and 'data-tech-action="deny"' in html


def test_replay_badge_is_in_the_header_and_on_every_card_never_floating():
    html = partial(sample("replay"))
    assert "live session" not in html
    assert 'class="tc-badge-rec">● Recorded Naïve session ses_01jcx9e3qv7k · 09:31<' in html
    assert "tc-rec-fixed" not in html  # nothing pinned over the content
    assert html.count('class="tc-rec-tag"') == 4  # verdict, part, email, feed


def test_a_skipped_email_never_reads_as_sent():
    v = sample("replay")
    html = partial(v)
    assert "Email not sent" in html and "Technician emailed" not in html
    assert "✓ sent" not in html and "sent 07:44" not in html and " · not sent" in html
    assert "replay: not sent (the recorded run&#39;s email went to the technician)" in html
    # The banner's email stage shows as not sent, not done.
    assert '<li class="st-skipped">' in html and "email not sent" in html
    assert "technician emailed" not in html


def test_failed_email_and_failed_stage():
    v = sample("waiting")
    v["stages"][2]["state"] = "failed"
    v["email"].update(status="failed", error="Naïve outbound mail answered 501")
    html = partial(v)
    assert '<li class="st-failed">' in html and "email failed" in html
    assert "✕ Email failed" in html and "✓ sent" not in html


def test_staged_chip_only_when_the_gate_staged_the_part():
    v = sample("waiting")
    assert "✓ spare part staged" in partial(v)
    v["part"]["staged"] = False
    html = partial(v)
    assert "spare part staged" not in html and "spare suggested" in html
    v["part"] = None
    html = partial(v)
    assert "spare part staged" not in html and "spare suggested" in html
    v["part_needed"] = "no"
    assert "spare suggested" not in partial(v)


def test_an_unpriced_part_never_shows_none():
    v = sample("waiting")
    v["part"].update(
        staged=False, unit_usd=None, total_usd=None, cart_url=None, blocked_reason="no price"
    )
    v["can_approve"] = False
    html = partial(v)
    assert "None" not in html
    assert "not checked yet" in html


def test_blocked_part_and_failed_email():
    v = sample("waiting")
    v["part"].update(staged=False, cart_url=None, blocked_reason="Out of stock at WowRobo")
    v["part"]["checks"][1] = {"label": "in stock", "ok": False}
    v["can_approve"] = False
    v["email"].update(status="failed", error="Naïve outbound mail answered 501")
    html = partial(v)
    assert "Not staged: Out of stock at WowRobo" in html
    assert '<li class="no"' in html
    assert 'data-tech-action="approve"' not in html
    assert "Email failed" in html and "answered 501" in html


@pytest.mark.parametrize("status", ["failed", "stopped"])
def test_failed_or_stopped_without_a_report_says_why(status):
    v = sample("diagnosing")
    v.update(status=status, poll=False, why="The session hit the $3.00 budget")
    html = partial(v)
    assert "no report" in html and "The session hit the $3.00 budget" in html
    assert "hx-get" not in html


def test_no_part_needed_and_no_email():
    v = sample("waiting")
    v.update(part=None, email=None, part_needed="no", can_approve=False, can_deny=False)
    html = partial(v)
    assert "No part needed" in html and "No email sent" in html
    assert "data-tech-action" not in html


def test_device_text_renders_inert():
    v = sample("waiting")
    v["verdict"] = "<script>alert(1)</script>"
    v["feed"].append({"t": "07:45:00", "text": "<img src=x onerror=alert(1)>"})
    html = partial(v)
    assert "<script>alert(1)" not in html and "<img src=x" not in html


def test_none_lists_render_and_only_https_sources_are_links():
    v = sample("waiting")
    v.update(feed=None, stages=None)
    v["part"]["checks"] = None
    v["sources"] = [
        {"title": "Datasheet", "url": "https://www.feetechrc.com/x"},
        {"title": "Sketchy", "url": "javascript:alert(1)"},
    ]
    html = partial(v)
    assert '<a href="https://www.feetechrc.com/x"' in html
    assert 'href="javascript:' not in html and "Sketchy" in html


def test_poll_is_paused_while_a_resolve_is_in_flight():
    html = page(sample("waiting"))
    assert "htmx:beforeSwap" in html and "ev.detail.shouldSwap = false" in html
    assert "if (!btn || btn.disabled || busy) return;" in html
