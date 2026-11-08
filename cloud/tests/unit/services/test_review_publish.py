"""services/review_publish.py (fleet learning T1): a gate-promoted review skill is mirrored to
Naïve Skills as a new playbook version, once, with an optional INSIGHTS comment. FakeNaive only;
HttpNaive's skill and comment calls on an `httpx.MockTransport`."""

import hashlib
import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from romulus_cloud import events
from romulus_cloud.adapters.naive import FakeNaive, HttpNaive, NaiveError
from romulus_cloud.ids import new_id
from romulus_cloud.models import Candidate
from romulus_cloud.services import review_publish

T0 = datetime(2026, 11, 8, 18, 0, tzinfo=UTC)  # 10:00 AM in San Francisco
SLUG = "romulus-healer-playbook"
CARD = "crd_insights"
MARKDOWN = (
    "When a joint stalls twice in one pick, back off 5 degrees, re-home that joint alone, "
    "then retry the pick once at half speed."
)


def _candidate(db, status="promoted", version=3, incident_id="inc-1"):
    c = Candidate(
        id=new_id("cand"),
        type="harness",
        version=version,
        device_class="so101",
        from_overlays=[],
        status=status,
        incident_id=incident_id,
    )
    db.add(c)
    db.flush()
    return c


def _accepted(db, c, *, title="Re-home a stalled joint before retrying", session_id="ses_rev1"):
    """A `review.accepted` event in the shape services/review.py records."""
    proposal = {
        "gap": "failed_heal",
        "title": title,
        "pattern": "joint_stall heals fail on the second stall in one pick",
        "evidence": ["run-1", "run-2"],
        "applies_to": ["so101"],
        "proposal": {"kind": "skill", "slug": "rehome-stalled-joint", "markdown": MARKDOWN},
        "confidence": 0.8,
    }
    return events.record(
        db,
        "agent:fleet-doctor",
        "agent",
        "review.accepted",
        {"candidate": c.id, "incident": c.incident_id},
        "Accepted the skill proposal.",
        data={
            "proposal": proposal,
            "result": {"route": "harness"},
            "session_id": session_id,
            "duration_s": 27.0,
            "cost_usd": 0.07,
            "case_id": "review:joint_stall:1",
            "kind": "joint_stall",
            "recorded": False,
        },
        ts=T0,
    )


def _promote(db, c, at=T0 + timedelta(minutes=3)):
    events.record(
        db,
        "gate",
        "automation",
        "candidate.promoted",
        {"candidate": c.id, "harness": c.version, "incident": c.incident_id},
        "Canary held; promoted to so101.",
        data={"type": c.type, "version": c.version},
        ts=at,
    )


def _published(db):
    return events.find(db, {}, kinds=["review.published"])


def test_promoted_review_skill_is_published_once(db_session, make_settings):
    settings = make_settings()
    naive = FakeNaive()
    c = _candidate(db_session)
    _accepted(db_session, c)
    _promote(db_session, c)

    out = review_publish.publish_promoted(db_session, T0 + timedelta(minutes=4), settings, naive)

    assert [o["candidate"] for o in out] == [c.id]
    skill = naive.skills[SLUG]
    # created as the empty playbook (v1), then the lesson pushed as v2: the Studio shows a diff
    assert len(skill["versions"]) == 2 and out[0]["version"] == 2
    v1, v2 = skill["versions"]
    assert "No lessons yet" in v1 and MARKDOWN not in v1
    assert v2.startswith(f"---\nname: {SLUG}\ndescription: ")
    assert (
        "\nversion: 1.1.0\n" in v2
        and "    tags: [robotics, self-healing, fleet-learning, so101]" in v2
    )
    assert "\n---\n\n# Romulus healer playbook\n\n" in v2
    assert (
        "Lessons the Romulus fleet learned; each shipped only after the gate's canary held." in v2
    )
    assert "## Re-home a stalled joint before retrying\n\n" + MARKDOWN in v2
    assert (
        "_proposed by Naïve agent fleet-doctor · session ses_rev1 · gate-promoted harness v3 · "
        "2026-11-08_"
    ) in v2
    assert [name for name, _ in naive.skill_calls] == [
        "get_skill",
        "create_skill",
        "push_skill_version",
    ]

    [ev] = _published(db_session)
    assert (ev.actor, ev.actor_class) == ("agent:fleet-doctor", "agent")
    assert ev.refs == {"candidate": c.id, "harness": 3, "incident": "inc-1"}
    assert ev.data["skill_id"] == skill["id"] and ev.data["version"] == 2
    assert ev.data["slug"] == SLUG and ev.data["comment"] is None
    assert ev.data["human_clicks"] == 0
    assert "romulus-healer-playbook v2" in ev.why

    # idempotent: a second tick publishes nothing and calls Naïve not at all
    calls = len(naive.skill_calls)
    assert (
        review_publish.publish_promoted(db_session, T0 + timedelta(minutes=5), settings, naive)
        == []
    )
    assert len(naive.skill_calls) == calls and len(_published(db_session)) == 1


def test_not_promoted_publishes_nothing(db_session, make_settings):
    naive = FakeNaive()
    for status in ("replaying", "canary", "rolled_back"):
        _accepted(db_session, _candidate(db_session, status=status))
    out = review_publish.publish_promoted(db_session, T0, make_settings(), naive)
    assert out == [] and naive.skill_calls == [] and _published(db_session) == []


def test_rule_proposals_are_not_mirrored(db_session, make_settings):
    naive = FakeNaive()
    c = _candidate(db_session)
    ev = _accepted(db_session, c)
    ev.data = {
        **ev.data,
        "proposal": {**ev.data["proposal"], "proposal": {"kind": "rule", "probe": "p1"}},
    }
    db_session.flush()
    _promote(db_session, c)
    assert review_publish.publish_promoted(db_session, T0, make_settings(), naive) == []


def test_comment_only_when_the_card_is_set(db_session, make_settings):
    naive = FakeNaive()
    c = _candidate(db_session)
    _accepted(db_session, c)
    _promote(db_session, c)
    review_publish.publish_promoted(db_session, T0, make_settings(), naive)
    assert "comment_on_card" not in [name for name, _ in naive.skill_calls]

    c2 = _candidate(db_session, version=4, incident_id="inc-2")
    _accepted(db_session, c2, title="Slow the gripper on glossy blocks", session_id="ses_rev2")
    _promote(db_session, c2, at=T0 + timedelta(minutes=10))
    out = review_publish.publish_promoted(
        db_session, T0, make_settings(review_insights_card=CARD), naive
    )
    [comment] = naive.comments[CARD]
    assert comment["body"] == (
        "Fleet learning: “Slow the gripper on glossy blocks” shipped to every so101 arm after the "
        "canary held · proposed by Naïve agent fleet-doctor · 0 human clicks · "
        "Naïve skill romulus-healer-playbook v3"
    )
    assert [o["candidate"] for o in out] == [c2.id]
    assert out[0]["comment"]["id"] == comment["id"]
    # the playbook is cumulative: v3 carries both lessons, in promotion order
    v3 = naive.skills[SLUG]["versions"][-1]
    assert v3.index("Re-home a stalled joint") < v3.index("Slow the gripper on glossy blocks")
    assert "gate-promoted harness v4" in v3 and "\nversion: 1.2.0\n" in v3


def test_human_clicks_are_counted_not_assumed(db_session, make_settings):
    naive = FakeNaive()
    c = _candidate(db_session)
    _accepted(db_session, c)
    events.record(
        db_session, "human:kent", "human", "candidate.restarted", {"candidate": c.id}, "Unfroze."
    )
    _promote(db_session, c)
    review_publish.publish_promoted(db_session, T0, make_settings(review_insights_card=CARD), naive)
    assert "· 1 human click ·" in naive.comments[CARD][0]["body"]


def test_naive_errors_record_nothing_and_retry_next_tick(db_session, make_settings, monkeypatch):
    settings = make_settings(review_insights_card=CARD)
    naive = FakeNaive()
    c = _candidate(db_session)
    _accepted(db_session, c)
    _promote(db_session, c)

    def boom(*a, **kw):
        raise NaiveError("at capacity", status=429)

    monkeypatch.setattr(naive, "push_skill_version", boom)
    assert review_publish.publish_promoted(db_session, T0, settings, naive) == []
    assert _published(db_session) == []
    monkeypatch.undo()

    monkeypatch.setattr(naive, "comment_on_card", boom)  # a retryable comment failure waits too
    assert review_publish.publish_promoted(db_session, T0, settings, naive) == []
    assert _published(db_session) == []
    monkeypatch.undo()

    out = review_publish.publish_promoted(db_session, T0, settings, naive)
    assert len(out) == 1 and len(naive.comments[CARD]) == 1
    assert len(naive.skills[SLUG]["versions"]) == 2  # the retries minted no extra version


def test_a_refused_comment_still_publishes(db_session, make_settings, monkeypatch):
    naive = FakeNaive()
    c = _candidate(db_session)
    _accepted(db_session, c)
    _promote(db_session, c)

    def refused(*a, **kw):
        raise NaiveError("no such card", status=404, code="not_found")

    monkeypatch.setattr(naive, "comment_on_card", refused)
    [out] = review_publish.publish_promoted(
        db_session, T0, make_settings(review_insights_card=CARD), naive
    )
    assert out["comment"] is None and "no such card" in out["comment_error"]


def test_enabled_needs_the_flag_and_a_learning_mode(make_settings):
    assert review_publish.enabled(make_settings()) is False
    s = make_settings(review_publish=True)
    assert review_publish.enabled(s) is False  # no LEARNING_MODE yet: treated as off
    for mode, want in (("off", False), ("fixture", True), ("live", True)):
        fake = type("S", (), {"review_publish": True, "learning_mode": mode})()
        assert review_publish.enabled(fake) is want


def test_defaults(make_settings):
    s = make_settings()
    assert (s.review_publish, s.naive_skill_slug, s.review_insights_card) == (
        False,
        SLUG,
        None,
    )


# --- HttpNaive: the real endpoints and payloads --------------------------------------------


def _http(handler):
    naive = HttpNaive(
        "sk_live_test",
        "agt_ceo",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    naive._sleep = lambda s: None
    return naive


def test_http_skill_and_comment_calls():
    seen = []

    def handler(request):
        body = json.loads(request.content) if request.content else None
        seen.append(
            (request.method, request.url.path, body, request.headers.get("idempotency-key"))
        )
        if request.method == "GET":
            return httpx.Response(
                404, json={"error": {"code": "not_found", "message": "no such skill"}}
            )
        if request.url.path.endswith("/comments"):
            return httpx.Response(201, json={"id": "bcm_1", "object": "board_comment"})
        return httpx.Response(201, json={"id": "skl_1", "slug": SLUG, "version": 1})

    naive = _http(handler)
    assert naive.get_skill(SLUG) is None
    naive.create_skill(slug=SLUG, content="# A", description="d", idempotency_key="k1")
    naive.push_skill_version("skl_1", content="# B", idempotency_key="k2")
    naive.comment_on_card("crd_9", "hello", idempotency_key="k3")

    sha = lambda t: hashlib.sha256(t.encode()).hexdigest()  # noqa: E731
    assert seen == [
        ("GET", f"/v1/skills/{SLUG}", None, None),
        (
            "POST",
            "/v1/skills",
            {"slug": SLUG, "description": "d", "content": "# A", "sha256": sha("# A")},
            "k1",
        ),
        ("POST", "/v1/skills/skl_1/versions", {"content": "# B", "sha256": sha("# B")}, "k2"),
        ("POST", "/v1/boards/agt_ceo/cards/crd_9/comments", {"body": "hello"}, "k3"),
    ]


def test_http_get_skill_raises_other_errors():
    naive = _http(lambda r: httpx.Response(403, json={"error": {"code": "forbidden"}}))
    with pytest.raises(NaiveError):
        naive.get_skill(SLUG)
