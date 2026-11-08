"""Fleet learning T1: mirror the gate-promoted healer playbook to Naïve Skills
(docs/recursive-self-improvement/design.md §8 D31, §9; demo.md §5).

Publishing happens only after a gate promotes: the Skills page is a mirror, not a rollout lane.
Each `review.accepted` skill (written by the Naïve agent fleet-doctor, `services/review.py`)
whose harness candidate the gate has since promoted becomes one section of one playbook
SKILL.md, pushed as a new version of `settings.naive_skill_slug`. On first use the skill is
created with the playbook as it stood before this tick (with no lessons yet, that is v1), so
the Studio's Skills page shows the new lesson as a v1 → v2 diff. One `review.published` event
per candidate makes it idempotent. With `settings.review_insights_card` set, each lesson also
gets one comment on that card of the CEO's board (NAIVE_BOARD_ID).

Naïve errors are logged and nothing is recorded, so the next tick retries; re-pushing the
same bytes mints no version (N:10645) and the same Idempotency-Key replays (N:9440).
Nothing here commits; the worker step does.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any, cast
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from romulus_cloud import events
from romulus_cloud.adapters.naive import NaiveError, SkillPublisher
from romulus_cloud.models import Candidate, Event
from romulus_cloud.services import workorders
from romulus_cloud.settings import Settings

log = logging.getLogger(__name__)

ACTOR = "agent:fleet-doctor"
AGENT = "fleet-doctor"
ACCEPTED = "review.accepted"
PUBLISHED = "review.published"
PROMOTED = "candidate.promoted"  # gate.TRANSITION_KINDS["promoted"]
LEARNING_MODES = ("fixture", "live")
TITLE = "Romulus healer playbook"
INTRO = "Lessons the Romulus fleet learned; each shipped only after the gate's canary held."
DESCRIPTION = (
    "Lessons the Romulus robot fleet learned from failed heals; each shipped to every arm only "
    "after the gate's canary held."
)
EMPTY = "_No lessons yet: each one appears here after the gate promotes it._"


@dataclass(frozen=True)
class Lesson:
    """One gate-promoted review skill, as the playbook shows it."""

    candidate_id: str
    title: str
    slug: str  # the skill's own slug in the harness bundle (`skills/<slug>.md`)
    markdown: str
    session_id: str | None
    harness_version: int | None
    device_class: str
    promoted_at: datetime
    promoted_on: str  # the date, in DISPLAY_TZ
    incident_id: str | None


def enabled(settings: Settings) -> bool:
    """Whether the worker should call `publish_promoted`: REVIEW_PUBLISH on and LEARNING_MODE
    fixture or live (read with getattr until the review lane adds the setting)."""
    return bool(settings.review_publish) and (
        getattr(settings, "learning_mode", "off") in LEARNING_MODES
    )


def publisher(settings: Settings) -> SkillPublisher:
    """With LEARNING_MODE=live, the review lane's live HttpNaive (as `review.naive_for` builds
    it), so a live run needs no NAIVE_MODE=live and turns on no other Naïve writes. Otherwise
    the process's client for NAIVE_MODE (HttpNaive when live, else the in-memory FakeNaive)."""
    if getattr(settings, "learning_mode", "off") == "live":
        from romulus_cloud.services import review  # noqa: PLC0415 - review imports this module's peers

        return cast(SkillPublisher, review.naive_for("live", "publish", settings))
    return cast(SkillPublisher, workorders.naive_client(settings))


# --- reading the event log ---------------------------------------------------------------------


def _skill_proposal(data: dict[str, Any] | None) -> dict[str, Any] | None:
    proposal = (data or {}).get("proposal") or {}
    inner = proposal.get("proposal") or {}
    if inner.get("kind") != "skill" or not inner.get("markdown"):
        return None
    return proposal


def _promoted_at(session: Session, c: Candidate) -> datetime | None:
    found = events.find(session, {"candidate": c.id}, kinds=[PROMOTED])
    return found[-1].ts if found else (c.updated_at or c.created_at)


def promoted_lessons(session: Session, settings: Settings) -> list[Lesson]:
    """Every accepted review skill whose candidate is promoted, oldest promotion first."""
    stmt = (
        select(Event)
        .where(Event.kind == ACCEPTED, Event.fleet_id == settings.fleet_id)
        .order_by(Event.ts, Event.recorded_at, Event.id)
    )
    tz = ZoneInfo(settings.display_tz)
    lessons: dict[str, Lesson] = {}
    for ev in session.scalars(stmt):
        cid = (ev.refs or {}).get("candidate")
        proposal = _skill_proposal(ev.data)
        if not cid or cid in lessons or proposal is None:
            continue
        c = session.get(Candidate, cid)
        if c is None or c.status != "promoted":
            continue
        at = _promoted_at(session, c)
        if at is None:
            continue
        inner = proposal["proposal"]
        lessons[cid] = Lesson(
            candidate_id=cid,
            title=str(proposal.get("title") or inner.get("slug") or "Untitled lesson").strip(),
            slug=str(inner.get("slug") or ""),
            markdown=str(inner["markdown"]).strip(),
            session_id=(ev.data or {}).get("session_id"),
            harness_version=c.version,
            device_class=c.device_class,
            promoted_at=at,
            promoted_on=at.astimezone(tz).strftime("%Y-%m-%d"),
            incident_id=(ev.refs or {}).get("incident") or c.incident_id,
        )
    return sorted(lessons.values(), key=lambda lesson: (lesson.promoted_at, lesson.candidate_id))


def published_candidates(session: Session, fleet_id: str) -> set[str]:
    stmt = select(Event).where(Event.kind == PUBLISHED, Event.fleet_id == fleet_id)
    return {cid for ev in session.scalars(stmt) if (cid := (ev.refs or {}).get("candidate"))}


def human_clicks(session: Session, candidate_id: str) -> int:
    """Events on this candidate a person caused (the "0 clicks" claim is checked, not assumed)."""
    return sum(
        1 for ev in events.find(session, {"candidate": candidate_id}) if ev.actor_class == "human"
    )


# --- rendering (pure) --------------------------------------------------------------------------


def provenance(lesson: Lesson) -> str:
    session_part = f"session {lesson.session_id}" if lesson.session_id else "session unknown"
    harness = f"v{lesson.harness_version}" if lesson.harness_version is not None else "(version?)"
    return (
        f"proposed by Naïve agent {AGENT} · {session_part} · gate-promoted harness {harness} · "
        f"{lesson.promoted_on}"
    )


def render_playbook(lessons: list[Lesson], slug: str) -> str:
    """The playbook SKILL.md: YAML front matter (Naïve: name + description, N:13502-13506;
    Hermes adds version, author and metadata.hermes, H:13486-13494), then one section per
    lesson in promotion order. Deterministic, so a re-push of the same lessons mints nothing."""
    classes = sorted({lesson.device_class for lesson in lessons})
    tags = ", ".join(["robotics", "self-healing", "fleet-learning", *classes])
    lines = [
        "---",
        f"name: {slug}",
        f"description: {DESCRIPTION}",
        f"version: 1.{len(lessons)}.0",
        f"author: Romulus fleet (lessons proposed by Naïve agent {AGENT})",
        "metadata:",
        "  hermes:",
        f"    tags: [{tags}]",
        "    category: devops",
        "---",
        "",
        f"# {TITLE}",
        "",
        INTRO,
        "",
    ]
    if not lessons:
        lines += [EMPTY, ""]
    for lesson in lessons:
        lines += [f"## {lesson.title}", "", lesson.markdown, "", f"_{provenance(lesson)}_", ""]
    return "\n".join(lines)


def comment_text(lesson: Lesson, clicks: int, slug: str, version: Any) -> str:
    who = f"{clicks} human click" + ("" if clicks == 1 else "s")
    return (
        f"Fleet learning: {lesson.title} shipped to every {lesson.device_class} arm after the "
        f"canary held · proposed by Naïve agent {AGENT} · {who} · Naïve skill {slug} v{version}"
    )


def _key(prefix: str, *parts: str) -> str:
    """An Idempotency-Key bound to the exact request content (N:9440-9441)."""
    return f"romulus-{prefix}-" + hashlib.sha256("\n".join(parts).encode()).hexdigest()[:32]


# --- the worker step's body --------------------------------------------------------------------


def publish_promoted(
    session: Session, now: datetime, settings: Settings, naive: SkillPublisher
) -> list[dict[str, Any]]:
    """Push the playbook once for every promoted, not yet published review skill; record one
    `review.published` per candidate. Returns each event's data (with `candidate`)."""
    lessons = promoted_lessons(session, settings)
    done = published_candidates(session, settings.fleet_id)
    new = [lesson for lesson in lessons if lesson.candidate_id not in done]
    if not new:
        return []
    slug = settings.naive_skill_slug
    content = render_playbook(lessons, slug)
    try:
        skill = naive.get_skill(slug)
        if skill is None:
            # Create it as it stood before this tick, so the new lessons show as a diff.
            # (POST on an existing slug is a new version, N:10593; re-pushing bytes that match
            # the current version mints nothing, N:10645: safe if the lookup missed it.)
            before = render_playbook([x for x in lessons if x.candidate_id in done], slug)
            skill = naive.create_skill(
                slug=slug,
                content=before,
                description=DESCRIPTION,
                idempotency_key=_key("skill-create", slug, before),
            )
        skill_id = str(skill["id"])
        pushed = naive.push_skill_version(
            skill_id, content=content, idempotency_key=_key("skill-push", skill_id, content)
        )
    except NaiveError as exc:
        log.warning("review publish: Naïve skill push failed, retrying next tick: %s", exc)
        return []

    version = pushed.get("version")
    card = settings.review_insights_card
    out: list[dict[str, Any]] = []
    for lesson in new:
        comment: dict[str, Any] | None = None
        comment_error: str | None = None
        clicks = human_clicks(session, lesson.candidate_id)
        if card:
            body = comment_text(lesson, clicks, slug, version)
            try:
                raw = naive.comment_on_card(
                    card, body, idempotency_key=_key("comment", lesson.candidate_id, body)
                )
                comment = {"card_id": card, "id": raw.get("id"), "body": body}
            except NaiveError as exc:
                if exc.retryable:
                    log.warning("review publish: comment on %s failed, retrying: %s", card, exc)
                    break  # this lesson and the rest wait for the next tick
                comment_error = str(exc)  # e.g. a wrong card id: publish, say so, don't loop
                log.warning("review publish: comment on %s refused: %s", card, exc)
        data = {
            "skill_id": skill_id,
            "slug": slug,
            "version": version,
            "sha256": pushed.get("sha256"),
            "url": pushed.get("url"),  # the API returns none today; never hand-built
            "title": lesson.title,
            "lesson_slug": lesson.slug,
            "session_id": lesson.session_id,
            "harness": lesson.harness_version,
            "device_class": lesson.device_class,
            "lessons": len(lessons),
            "human_clicks": clicks,
            "comment": comment,
            "comment_error": comment_error,
        }
        refs: dict[str, Any] = {"candidate": lesson.candidate_id}
        if lesson.harness_version is not None:
            refs["harness"] = lesson.harness_version
        if lesson.incident_id:
            refs["incident"] = lesson.incident_id
        events.record(
            session,
            ACTOR,
            "agent",
            PUBLISHED,
            refs,
            f"Mirrored the healer playbook to Naïve Skills as {slug} v{version} after the gate "
            f'promoted harness v{lesson.harness_version}: "{lesson.title}"'
            + (f" and commented on card {card}." if comment else "."),
            data=data,
            ts=now,
            fleet_id=settings.fleet_id,
        )
        out.append({"candidate": lesson.candidate_id, **data})
    return out
