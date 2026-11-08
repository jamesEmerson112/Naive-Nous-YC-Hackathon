import json

import httpx
import pytest

from romulus_cloud.adapters.naive import (
    FakeNaive,
    HttpNaive,
    NaiveError,
    build_naive,
)

CARD = {"id": "crd_1", "object": "board_card", "status": "todo", "note": None}


def _http(handler, **kw):
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return HttpNaive("sk_live_test", "agt_ceo", http_client=client, **kw)


def test_create_card_sends_headers_and_only_documented_keys():
    seen = {}

    def handler(request):
        seen["method"] = request.method
        seen["url"] = str(request.url)
        seen["headers"] = request.headers
        seen["body"] = json.loads(request.content)
        return httpx.Response(201, json=CARD)

    card = _http(handler).create_card("WO-17 · arm-1", "WORK ORDER wo_17", "fleet-ops", "wo_17")
    assert card == CARD
    assert seen["method"] == "POST"
    assert seen["url"] == "https://api.vetta.sh/v1/boards/agt_ceo/cards"
    assert seen["headers"]["authorization"] == "Bearer sk_live_test"
    assert seen["headers"]["vetta-version"] == "2026-09-30"
    assert seen["headers"]["idempotency-key"] == "wo_17"
    assert seen["body"] == {
        "title": "WO-17 · arm-1",
        "body": "WORK ORDER wo_17",
        "assignee": "fleet-ops",
    }


def test_create_card_without_assignee_omits_key():
    seen = {}

    def handler(request):
        seen["body"] = json.loads(request.content)
        return httpx.Response(201, json=CARD)

    _http(handler).create_card("t", "b", None, "k")
    assert seen["body"] == {"title": "t", "body": "b"}


def test_create_card_validates_lengths_before_sending():
    def handler(request):
        raise AssertionError("should not send")

    naive = _http(handler)
    with pytest.raises(ValueError):
        naive.create_card("", "b", None, "k")
    with pytest.raises(ValueError):
        naive.create_card("x" * 201, "b", None, "k")
    with pytest.raises(ValueError):
        naive.create_card("t", "x" * 20_001, None, "k")


def test_get_card_and_custom_base_url():
    seen = {}

    def handler(request):
        seen["method"] = request.method
        seen["url"] = str(request.url)
        seen["headers"] = request.headers
        return httpx.Response(200, json={**CARD, "status": "doing"})

    naive = _http(handler, base_url="https://naive.test/v1/", version="2027-01-01")
    assert naive.get_card("crd_1")["status"] == "doing"
    assert seen["method"] == "GET"
    assert seen["url"] == "https://naive.test/v1/boards/agt_ceo/cards/crd_1"
    assert seen["headers"]["vetta-version"] == "2027-01-01"
    assert "idempotency-key" not in seen["headers"]


def test_list_comments_follows_cursor():
    pages = {
        None: {"data": [{"id": "c1"}], "has_more": True, "next_cursor": "c1"},
        "c1": {"data": [{"id": "c2"}], "has_more": False, "next_cursor": None},
    }

    def handler(request):
        assert request.url.path == "/v1/boards/agt_ceo/cards/crd_1/comments"
        return httpx.Response(200, json=pages[request.url.params.get("after")])

    assert [c["id"] for c in _http(handler).list_comments("crd_1")] == ["c1", "c2"]


def test_api_error_is_parsed():
    def handler(request):
        return httpx.Response(
            400,
            json={
                "error": {
                    "type": "invalid_request",
                    "code": "validation_failed",
                    "message": "unknown_member: fleet-ops",
                    "request_id": "req_1",
                    "param": "assignee",
                }
            },
        )

    with pytest.raises(NaiveError) as exc:
        _http(handler).create_card("t", "b", "fleet-ops", "k")
    assert exc.value.status == 400
    assert exc.value.code == "validation_failed"
    assert exc.value.request_id == "req_1"
    assert not exc.value.retryable


def test_html_error_and_retryable_statuses():
    naive = _http(lambda r: httpx.Response(403, text="<html>blocked</html>"))
    with pytest.raises(NaiveError) as exc:
        naive.get_card("crd_1")
    assert exc.value.status == 403 and not exc.value.retryable

    for status in (429, 503):
        with pytest.raises(NaiveError) as exc:
            _http(lambda r, s=status: httpx.Response(s, json={})).get_card("crd_1")
        assert exc.value.retryable


def test_transport_error_is_retryable():
    def handler(request):
        raise httpx.ConnectError("no wifi", request=request)

    with pytest.raises(NaiveError) as exc:
        _http(handler).get_card("crd_1")
    assert exc.value.status is None and exc.value.retryable


def test_fake_progresses_to_result_after_n_polls():
    fake = FakeNaive(polls_to_done=3)
    card = fake.create_card("WO-1", "body", "fleet-ops", "wo_1")
    assert card["status"] == "todo" and card["note"] is None
    assert fake.get_card(card["id"])["status"] == "doing"
    assert fake.get_card(card["id"])["status"] == "doing"
    done = fake.get_card(card["id"])
    assert done["status"] == "done"
    first, second = done["note"].split("\n")
    assert first == "RESULT: ordered ORDER_REF=FAKE-0001 COST_USD=12.00"
    assert "fake" in second
    assert done["updated_at"] > card["updated_at"]
    # Further polls are stable.
    assert fake.get_card(card["id"]) == done
    comments = fake.list_comments(card["id"])
    assert len(comments) == 1 and comments[0]["author"].startswith("ses_")


def test_fake_can_stop_and_is_idempotent():
    fake = FakeNaive(polls_to_done=1, outcome="stopped", stop_reason="over MAX_USD")
    a = fake.create_card("WO-2", "body", "fleet-ops", "wo_2")
    assert fake.create_card("WO-2", "body", "fleet-ops", "wo_2")["id"] == a["id"]
    assert len(fake.cards) == 1
    done = fake.get_card(a["id"])
    assert done["note"].startswith("STOPPED: over MAX_USD")

    fake.outcome = "result"
    b = fake.create_card("WO-3", "body", "fleet-ops", "wo_3")
    assert fake.get_card(b["id"])["note"].startswith("RESULT:")


def test_fake_is_deterministic():
    def run():
        fake = FakeNaive(polls_to_done=2)
        card = fake.create_card("WO", "b", "fleet-ops", "wo_9")
        return [fake.get_card(card["id"]) for _ in range(3)]

    assert run() == run()


def test_fake_unknown_card():
    with pytest.raises(NaiveError) as exc:
        FakeNaive().get_card("crd_nope")
    assert exc.value.status == 404


def test_factory():
    assert isinstance(build_naive("fake"), FakeNaive)
    assert isinstance(build_naive("live", "sk", "agt_1"), HttpNaive)
    with pytest.raises(ValueError):
        build_naive("live", None, "agt_1")
    with pytest.raises(ValueError):
        build_naive("bogus")
    with pytest.raises(ValueError):
        FakeNaive(outcome="maybe")


@pytest.mark.parametrize("code", ["idempotency_conflict", "job_not_ready"])
def test_retryable_409_codes(code):
    naive = _http(lambda r: httpx.Response(409, json={"error": {"code": code, "message": "retry"}}))
    with pytest.raises(NaiveError) as exc:
        naive.create_card("t", "b", "fleet-ops", "wo_1")
    assert exc.value.status == 409 and exc.value.code == code
    assert exc.value.retryable


def test_other_409_is_not_retryable():
    naive = _http(lambda r: httpx.Response(409, json={"error": {"code": "state_conflict"}}))
    with pytest.raises(NaiveError) as exc:
        naive.get_card("crd_1")
    assert not exc.value.retryable


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(200, text="<html>ok?</html>"),
        httpx.Response(200, json=["not", "an", "object"]),
        httpx.Response(400, json=["not", "an", "object"]),
        httpx.Response(400, json={"error": "a string, not an object"}),
    ],
)
def test_malformed_bodies_raise_naive_error(response):
    with pytest.raises(NaiveError):
        _http(lambda r: response).get_card("crd_1")


def test_fake_key_reused_with_different_body_is_409():
    fake = FakeNaive()
    fake.create_card("WO-2", "body", "fleet-ops", "wo_2")
    with pytest.raises(NaiveError) as exc:
        fake.create_card("WO-2 again", "other", None, "wo_2")
    assert exc.value.status == 409 and exc.value.code == "idempotency_conflict"
    assert len(fake.cards) == 1
