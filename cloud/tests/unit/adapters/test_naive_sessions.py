"""Naïve Technician adapters: session normalizers, HttpNaive session/email calls, FixtureNaive
timing, and the shop's product parsing. No network, no DB."""

import importlib.util
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest

from romulus_cloud.adapters import shop
from romulus_cloud.adapters.naive import (
    FixtureNaive,
    HttpNaive,
    NaiveError,
    event_from_raw,
    normalize_state,
    session_from_raw,
)

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "technician"
FAKE = FIXTURES / "joint_stall_fake.json"
SPIKE = Path(__file__).resolve().parents[3] / "scripts" / "technician_spike.py"
C018 = "48037575033049"
WOWROBO = "https://shop.wowrobo.com"
HANDLE = "feetech-sts3215-servo-12v-30kg-high-torque-servo-for-so-arm100"


def _spike():
    spec = importlib.util.spec_from_file_location("technician_spike", SPIKE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# --- normalizers ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("status", "stop", "want"),
    [
        ("queued", None, "running"),
        ("running", None, "running"),
        ("idle", "end_turn", "idle"),
        ("idle", "budget_paused", "idle"),
        ("idle", "awaiting_approval", "awaiting"),
        ("idle", "awaiting_answer", "awaiting"),
        ("idle", "error", "error"),
        ("failed", None, "error"),
        ("cancelled", "interrupted", "cancelled"),
        ("completed", "end_turn", "idle"),
    ],
)
def test_normalize_state(status, stop, want):
    assert normalize_state(status, stop) == want


def test_session_from_raw_reads_report_cost_and_times():
    raw = json.loads(FAKE.read_text())["session"]
    s = session_from_raw(raw)
    assert s.id.startswith("ses_")
    assert (s.state, s.stop_reason) == ("idle", "end_turn")
    assert s.structured_output["part"]["variant_id"] == C018
    assert s.cost_usd == pytest.approx(0.412337)
    assert s.created_at == datetime(2026, 11, 8, 17, 21, 2, tzinfo=UTC)
    assert s.ended_at is None  # idle is not terminal: the API leaves ended_at null
    assert s.studio_url is None  # the API returns none; never hand-built
    assert s.raw is raw


def test_event_from_raw_kinds():
    events = [event_from_raw(e) for e in json.loads(FAKE.read_text())["events"]]
    kinds = [e.kind for e in events]
    assert kinds[0] == "status" and kinds[-1] == "status"
    search = next(e for e in events if e.tool == "web_search" and e.kind == "tool_call")
    assert search.args["query"].startswith("Feetech STS3215")
    fetch = [e for e in events if e.tool == "web_fetch" and e.kind == "tool_call"]
    assert any("shop.wowrobo.com" in e.args["url"] for e in fetch)
    out = next(e for e in events if e.kind == "output")
    assert out.tool == "submit_output" and out.args["verdict"]
    assert all(e.text is None or len(e.text) <= 300 for e in events)
    assert next(e for e in events if e.kind == "message").text
    assert [e.seq for e in events] == sorted(e.seq for e in events)


def test_user_message_is_not_an_agent_message():
    ev = event_from_raw(
        {"seq": 1, "type": "message.completed", "data": {"content": "case file", "role": "user"}}
    )
    assert ev.kind == "other"


# --- HttpNaive -----------------------------------------------------------------------------


def _http(handler):
    naive = HttpNaive(
        "sk_live_test", http_client=httpx.Client(transport=httpx.MockTransport(handler))
    )
    naive._sleep = lambda s: None
    return naive


def test_create_session_posts_body_with_key_and_user_agent():
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        seen["headers"] = request.headers
        seen["body"] = json.loads(request.content)
        seen["timeout"] = request.extensions["timeout"]
        return httpx.Response(
            201, json={"id": "ses_1", "status": "queued", "consumed_micro_usd": 0}
        )

    s = _http(handler).create_session({"agent_id": "agt_t", "message": "m"}, idempotency_key="tc_1")
    assert (s.id, s.state) == ("ses_1", "running")
    assert seen["url"] == "https://api.vetta.sh/v1/sessions"
    assert seen["headers"]["idempotency-key"] == "tc_1"
    assert seen["headers"]["user-agent"] == "romulus-cloud/0.1"
    assert seen["headers"]["vetta-version"] == "2026-09-30"
    assert seen["body"] == {"agent_id": "agt_t", "message": "m"}
    assert seen["timeout"]["read"] == 5.0


def test_email_retries_429_with_same_key_then_succeeds():
    keys, sleeps = [], []

    def handler(request):
        keys.append(request.headers.get("idempotency-key"))
        if len(keys) < 3:
            return httpx.Response(
                429,
                headers={"Retry-After": "2"},
                json={"error": {"code": "rate_limited", "message": "at capacity"}},
            )
        return httpx.Response(202, json={"object": "email_send", "accepted": True})

    naive = _http(handler)
    naive._sleep = sleeps.append
    naive.send_email(
        identity_id="i",
        inbox_id="e",
        to=["k@y"],
        subject="s",
        text="t",
        idempotency_key="tc_9:email",
    )
    assert keys == ["tc_9:email"] * 3
    assert sleeps == [2.0, 2.0]


def test_create_session_timeout_or_in_flight_is_pending_without_retrying():
    calls = []

    def timeout(request):
        calls.append(1)
        raise httpx.ReadTimeout("slow", request=request)

    with pytest.raises(NaiveError) as exc:
        _http(timeout).create_session({"agent_id": "a"}, idempotency_key="tc_1")
    assert (exc.value.code, exc.value.pending, exc.value.retryable) == ("in_flight", True, True)
    assert len(calls) == 1

    def in_flight(request):
        return httpx.Response(
            409, json={"error": {"code": "idempotency_conflict", "message": "still in flight"}}
        )

    with pytest.raises(NaiveError) as exc:
        _http(in_flight).create_session({"agent_id": "a"}, idempotency_key="tc_1")
    assert exc.value.pending and exc.value.code == "in_flight" and exc.value.status == 409

    def capacity(request):
        return httpx.Response(429, json={"error": {"code": "rate_limited", "message": "busy"}})

    with pytest.raises(NaiveError) as exc:
        _http(capacity).create_session({"agent_id": "a"}, idempotency_key="tc_1")
    assert not exc.value.pending and exc.value.retryable


def test_find_session_matches_case_metadata():
    seen = {}

    def handler(request):
        seen["params"] = dict(request.url.params)
        return httpx.Response(
            200,
            json={
                "data": [
                    {"id": "ses_b", "status": "queued", "metadata": {"romulus_case": "tc_2"}},
                    {"id": "ses_a", "status": "idle", "metadata": {"romulus_case": "tc_1"}},
                ],
                "has_more": False,
            },
        )

    naive = _http(handler)
    found = naive.find_session("agt_t", "tc_1")
    assert found.id == "ses_a" and found.state == "idle"
    assert seen["params"] == {"agent_id": "agt_t", "limit": "20"}
    assert naive.find_session("agt_t", "tc_9") is None


def test_session_call_does_not_retry_400():
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(400, json={"error": {"code": "validation_failed", "message": "x"}})

    with pytest.raises(NaiveError) as exc:
        _http(handler).get_session("ses_1")
    assert exc.value.code == "validation_failed" and len(calls) == 1


def test_session_events_pages_with_cursor():
    pages = {
        None: {
            "data": [{"seq": 2, "type": "session.running", "data": {}}],
            "has_more": True,
            "next_cursor": "c1",
        },
        "c1": {
            "data": [{"seq": 3, "type": "session.idle", "data": {"stop_reason": "end_turn"}}],
            "has_more": False,
            "next_cursor": None,
        },
    }
    seen = []

    def handler(request):
        seen.append(dict(request.url.params))
        return httpx.Response(200, json=pages[request.url.params.get("after")])

    events = _http(handler).session_events("ses_1", after_seq=1)
    assert [e.seq for e in events] == [2, 3]
    assert seen[0] == {"after_seq": "1", "limit": "100"}
    assert seen[1]["after"] == "c1"


def test_send_email_posts_array_and_returns_receipt():
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        seen["body"] = json.loads(request.content)
        seen["key"] = request.headers["idempotency-key"]
        return httpx.Response(
            202,
            json={
                "object": "email_send",
                "accepted": True,
                "from": "t@x",
                "to": ["k@y"],
                "sent_at": "z",
            },
        )

    r = _http(handler).send_email(
        identity_id="idn_1",
        inbox_id="eml_1",
        to=["k@y"],
        subject="s",
        text="t",
        idempotency_key="tc_1:email",
    )
    assert r["accepted"] is True
    assert seen["url"] == "https://api.vetta.sh/v1/identities/idn_1/emails/eml_1/send"
    assert seen["body"] == {"to": ["k@y"], "subject": "s", "text": "t"}
    assert seen["key"] == "tc_1:email"


def test_cancel_session_posts():
    seen = []

    def handler(request):
        seen.append((request.method, request.url.path))
        return httpx.Response(200, json={"id": "ses_1", "status": "cancelled"})

    _http(handler).cancel_session("ses_1")
    assert seen == [("POST", "/v1/sessions/ses_1/cancel")]


# --- FixtureNaive --------------------------------------------------------------------------


class Clock:
    def __init__(self):
        self.t = datetime(2026, 11, 8, 18, 0, tzinfo=UTC)

    def __call__(self):
        return self.t


def test_fixture_naive_play_zero_releases_everything():
    fx = FixtureNaive(FAKE, now=Clock())
    s = fx.create_session({"agent_id": "a"}, idempotency_key="tc_1")
    assert s.state == "idle" and s.structured_output["verdict"]
    assert len(fx.session_events(s.id)) == 14
    assert fx.session_events(s.id, after_seq=14) == [fx.session_events(s.id)[-1]]


def test_fixture_naive_releases_events_over_play_seconds():
    clock = Clock()
    fx = FixtureNaive(FAKE, now=clock, play_seconds=20)
    s = fx.create_session({"agent_id": "a"}, idempotency_key="tc_1")
    assert s.state == "running" and s.structured_output is None
    first = fx.session_events(s.id)
    assert 0 < len(first) < 14  # session.running is out at t=0
    clock.t += timedelta(seconds=10)
    mid = fx.get_session(s.id)
    assert mid.state == "running" and 0 < mid.cost_usd < 0.412337
    clock.t += timedelta(seconds=10)
    done = fx.get_session(s.id)
    assert done.state == "idle" and done.cost_usd == pytest.approx(0.412337)
    assert done.structured_output["part"]["variant_id"] == C018
    assert len(fx.session_events(s.id)) == 14


def test_fixture_naive_idempotency_and_new_ids():
    fx = FixtureNaive(FAKE, now=Clock())
    a = fx.create_session({"x": 1}, idempotency_key="tc_1")
    assert fx.create_session({"x": 1}, idempotency_key="tc_1").id == a.id
    with pytest.raises(NaiveError) as exc:
        fx.create_session({"x": 2}, idempotency_key="tc_1")
    assert exc.value.code == "idempotency_conflict"
    b = fx.create_session({"x": 1}, idempotency_key="tc_2")
    assert b.id == f"{a.id}_r2"
    assert fx.find_session("agt", "tc_2").id == b.id
    assert fx.find_session("agt", "tc_3") is None
    fx.create_session({"metadata": {"romulus_case": "tc_meta"}}, idempotency_key="other-key")
    assert fx.find_session("agt", "tc_meta").id == f"{a.id}_r3"


def test_fixture_naive_cancel_and_email():
    clock = Clock()
    fx = FixtureNaive(FAKE, now=clock, play_seconds=60)
    s = fx.create_session({}, idempotency_key="tc_1")
    fx.cancel_session(s.id)
    assert fx.cancelled == [s.id]
    c = fx.get_session(s.id)
    assert c.state == "cancelled" and c.structured_output is None
    r = fx.send_email(
        identity_id="i", inbox_id="e", to=["k@y"], subject="s", text="t", idempotency_key="k1"
    )
    again = fx.send_email(
        identity_id="i", inbox_id="e", to=["k@y"], subject="s", text="t", idempotency_key="k1"
    )
    assert r == again and r["accepted"] is True and r["to"] == ["k@y"]
    assert len(fx.sent) == 1 and fx.sent[0]["subject"] == "s"
    with pytest.raises(NaiveError):
        fx.send_email(
            identity_id="i",
            inbox_id="e",
            to=["k@y"],
            subject="other",
            text="t",
            idempotency_key="k1",
        )


def test_fake_fixture_report_satisfies_the_schema():
    spike = _spike()
    data = json.loads(FAKE.read_text())
    report = data["session"]["structured_output"]
    assert spike.validate(report, data["request"]["output_schema"]) == []
    assert report["part_needed"] == "spare" and report["part"]["qty"] == 1
    assert report["verdict"] == "Elbow blocked or servo failing: needs hands"
    assert len(report["technician_brief"]["subject"]) <= 45
    bad = {**report, "confidence": "certain", "extra": 1}
    assert len(spike.validate(bad, data["request"]["output_schema"])) == 2


# --- shop ----------------------------------------------------------------------------------

PRODUCT_JS = {
    "title": "Feetech STS3215 C018 Servo – 12V 30KG",
    "handle": HANDLE,
    "variants": [
        {
            "id": 48037575033049,
            "title": "Single Piece(15.99/pcs)",
            "price": 1599,
            "available": True,
        },
        {"id": 48037575065817, "title": "5 Pieces(14.99/pcs)", "price": 7495, "available": False},
    ],
}


def test_shop_product_parses_cents_and_string_ids():
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        seen["ua"] = request.headers["user-agent"]
        return httpx.Response(200, json=PRODUCT_JS)

    p = shop.product(
        WOWROBO, HANDLE, http_client=httpx.Client(transport=httpx.MockTransport(handler))
    )
    assert seen["url"] == f"{WOWROBO}/products/{HANDLE}.js"
    assert seen["ua"] == "romulus-cloud/0.1"
    assert p["title"].startswith("Feetech")
    assert p["variants"][0] == {
        "id": C018,
        "title": "Single Piece(15.99/pcs)",
        "price_cents": 1599,
        "available": True,
    }
    assert p["variants"][1]["available"] is False


def test_shop_product_errors():
    client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(404, text="nope")))
    with pytest.raises(shop.ShopError):
        shop.product(WOWROBO, HANDLE, http_client=client)
    with pytest.raises(shop.ShopError):
        shop.product("http://shop.wowrobo.com", HANDLE)
    with pytest.raises(shop.ShopError):
        shop.product(WOWROBO, "../admin")
    assert (
        shop.normalize_product({"variants": [{"id": 1, "price": "15.99"}]})["variants"][0][
            "price_cents"
        ]
        == 1599
    )


def test_fake_shop_serves_raw_or_normalized():
    fake = shop.FakeShop({(WOWROBO, HANDLE): PRODUCT_JS})
    assert fake.product(WOWROBO + "/", HANDLE)["variants"][0]["id"] == C018
    assert fake.calls == [(WOWROBO, HANDLE)]
    with pytest.raises(shop.ShopError):
        fake.product(WOWROBO, "missing")
    norm = shop.FakeShop(
        {
            (WOWROBO, HANDLE): {
                "title": "t",
                "variants": [{"id": C018, "title": "s", "price_cents": 1599, "available": True}],
            }
        }
    )
    assert norm.product(WOWROBO, HANDLE)["variants"][0]["price_cents"] == 1599
