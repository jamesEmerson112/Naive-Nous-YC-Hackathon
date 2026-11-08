"""NaiveClient: work orders out as board cards, status back by polling (Naïve integration §3).

Absorbs the Naïve REST API (`https://api.vetta.sh/v1`, `Vetta-Version` header, bearer auth,
`Idempotency-Key` on POST). Outbound only: Naïve never calls us. FakeNaive is the offline
default; it walks each card to `done` after N polls with a `RESULT:` (or `STOPPED:`) note, so
the worker's sync loop can be exercised with no network.

The Naïve Technician (docs/naive-technician/design.md §2, §7) adds sessions and email to the
same client: `create_session`, `get_session`, `session_events`, `cancel_session`, `send_email`
on `HttpNaive`, normalized into `SessionState` / `SessionEvent` by `session_from_raw` /
`event_from_raw`. `FixtureNaive` serves one recorded session file through the same
normalizers (tests and the stage replay).
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Protocol

import httpx

DEFAULT_BASE_URL = "https://api.vetta.sh/v1"
DEFAULT_VERSION = "2026-09-30"
DEFAULT_TIMEOUT_S = 15.0
SESSION_TIMEOUT_S = 5.0  # session and email calls run inside a worker tick (design §6)
EVENT_PAGES_MAX = 10  # at most 10 pages of 100 events per session_events call
TEXT_MAX = 300
RETRY_ATTEMPTS = 3  # Naïve answers 429 "at capacity" under load; session/email calls retry
RETRY_BUDGET_S = 10.0
USER_AGENT = "romulus-cloud/0.1"
TITLE_MAX = 200
BODY_MAX = 20_000

Card = dict[str, Any]
RETRYABLE_409_CODES = frozenset({"idempotency_conflict", "job_not_ready"})


class NaiveError(Exception):
    """A Naïve API call failed.

    `retryable` is true for 429, 5xx, transport errors, and the 409 codes Naïve documents as
    worth retrying with the same Idempotency-Key: `idempotency_conflict` (the first request is
    still in flight, e.g. after a client timeout) and `job_not_ready` (api/errors).
    """

    def __init__(
        self,
        message: str,
        status: int | None = None,
        code: str | None = None,
        request_id: str | None = None,
        retry_after: float | None = None,
        pending: bool = False,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.request_id = request_id
        self.retry_after = retry_after  # seconds, from a Retry-After header
        # create_session only: the POST may have created the session but hasn't answered yet
        # (client timeout, or 409 "still in flight" while the session is queued). Resend the
        # same body and key next tick, or look it up with find_session.
        self.pending = pending

    @property
    def retryable(self) -> bool:
        if self.pending or self.status is None or self.status == 429 or self.status >= 500:
            return True
        return self.status == 409 and self.code in RETRYABLE_409_CODES


class NaiveClient(Protocol):
    def create_card(
        self, title: str, body: str, assignee: str | None, idempotency_key: str
    ) -> Card:
        """Post a card to the board; the same idempotency key returns the same card."""
        ...

    def get_card(self, card_id: str) -> Card:
        """Read one card by id (poll by id, never by paging the list)."""
        ...

    def list_comments(self, card_id: str) -> list[dict[str, Any]]:
        """The card's comments, oldest first."""
        ...


def _check_card(title: str, body: str) -> None:
    if not 1 <= len(title) <= TITLE_MAX:
        raise ValueError(f"card title must be 1-{TITLE_MAX} characters, got {len(title)}")
    if len(body) > BODY_MAX:
        raise ValueError(f"card body must be at most {BODY_MAX} characters, got {len(body)}")


class HttpNaive:
    """The live Naïve REST API over httpx."""

    def __init__(
        self,
        api_key: str,
        board_id: str = "",
        base_url: str = DEFAULT_BASE_URL,
        version: str = DEFAULT_VERSION,
        timeout_s: float = DEFAULT_TIMEOUT_S,
        http_client: httpx.Client | None = None,
    ) -> None:
        self.board_id = board_id
        self._base = base_url.rstrip("/")
        self._headers = {
            "Authorization": f"Bearer {api_key}",
            "Vetta-Version": version,
            "Accept": "application/json",
            # Cloudflare in front of api.vetta.sh answers 403 (error 1010) to some default
            # client User-Agents (urllib's), so name ourselves.
            "User-Agent": USER_AGENT,
        }
        self._http = http_client or httpx.Client(timeout=timeout_s)
        self._timeout = timeout_s
        self._sleep: Callable[[float], None] = time.sleep  # tests replace it

    def _request_retrying(self, method: str, path: str, **kw: Any) -> Any:
        """`_request` with a 5 s timeout, retrying retryable failures (429, 5xx, transport,
        409 idempotency_conflict/job_not_ready) up to RETRY_ATTEMPTS times with backoff that
        honours Retry-After, sleeping at most RETRY_BUDGET_S in total. Same headers (so the
        same Idempotency-Key) on every attempt."""
        waited = 0.0
        for attempt in range(RETRY_ATTEMPTS + 1):
            try:
                return self._request(method, path, timeout=SESSION_TIMEOUT_S, **kw)
            except NaiveError as exc:
                left = RETRY_BUDGET_S - waited
                if not exc.retryable or attempt == RETRY_ATTEMPTS or left <= 0:
                    raise
                backoff = 0.5 * 2**attempt
                delay = min(exc.retry_after if exc.retry_after is not None else backoff, left)
                self._sleep(delay)
                waited += delay
        raise AssertionError("unreachable")

    def _request(
        self,
        method: str,
        path: str,
        json: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        params: dict[str, Any] | None = None,
        timeout: float | None = None,
    ) -> Any:
        url = f"{self._base}{path}"
        try:
            resp = self._http.request(
                method,
                url,
                json=json,
                params=params,
                headers={**self._headers, **(headers or {})},
                timeout=self._timeout if timeout is None else timeout,
            )
        except httpx.HTTPError as exc:
            raise NaiveError(f"Naïve {method} {path} failed: {exc}") from exc
        if resp.status_code >= 400:
            raise _error_from(resp, method, path)
        try:
            data = resp.json()
        except ValueError:
            raise NaiveError(
                f"Naïve {method} {path} answered {resp.status_code} with non-JSON: "
                f"{resp.text[:200]!r}",
                status=resp.status_code,
                code="invalid_response",
            ) from None
        if not isinstance(data, dict):
            raise NaiveError(
                f"Naïve {method} {path} answered {resp.status_code} with a non-object body",
                status=resp.status_code,
                code="invalid_response",
            )
        return data

    def create_card(
        self, title: str, body: str, assignee: str | None, idempotency_key: str
    ) -> Card:
        _check_card(title, body)
        # Only documented keys: Naïve refuses unknown body keys with 400 (api/overview).
        payload: dict[str, Any] = {"title": title, "body": body}
        if assignee is not None:
            payload["assignee"] = assignee
        return self._request(
            "POST",
            f"/boards/{self.board_id}/cards",
            json=payload,
            headers={"Idempotency-Key": idempotency_key},
        )

    def get_card(self, card_id: str) -> Card:
        return self._request("GET", f"/boards/{self.board_id}/cards/{card_id}")

    def list_comments(self, card_id: str) -> list[dict[str, Any]]:
        comments: list[dict[str, Any]] = []
        path = f"/boards/{self.board_id}/cards/{card_id}/comments"
        cursor: str | None = None
        while True:
            page = self._request("GET", path, params={"after": cursor} if cursor else None)
            comments.extend(page.get("data", []))
            cursor = page.get("next_cursor")
            if not page.get("has_more") or not cursor:
                return comments

    # --- Naïve Technician: sessions and email (design §2, §4, §7) ---

    def create_session(self, body: dict[str, Any], *, idempotency_key: str) -> SessionState:
        """`POST /v1/sessions` creates and starts the run (F:28973-29030). The key is bound to
        the exact body (F:9432-9441): resend the frozen body on retry.

        No retries inside the call. Under load Naïve creates the session and holds the POST open
        while it is `queued`, so a client timeout or a `409 idempotency_conflict` ("still in
        flight") raises `NaiveError(code="in_flight", pending=True)`: the caller resends the same
        body and key on its next tick, or finds the session with `find_session`."""
        try:
            raw = self._request(
                "POST",
                "/sessions",
                json=body,
                headers={"Idempotency-Key": idempotency_key},
                timeout=SESSION_TIMEOUT_S,
            )
        except NaiveError as exc:
            if exc.status is None or (exc.status == 409 and exc.code == "idempotency_conflict"):
                raise NaiveError(
                    f"Naïve session create still in flight for key {idempotency_key}: {exc}",
                    status=exc.status,
                    code="in_flight",
                    request_id=exc.request_id,
                    retry_after=exc.retry_after,
                    pending=True,
                ) from exc
            raise
        return session_from_raw(raw)

    def find_session(self, agent_id: str, case_id: str) -> SessionState | None:
        """The agent's recent session whose `metadata.romulus_case == case_id`, or None
        (`GET /v1/sessions?agent_id=…`, newest first, one page of 20)."""
        page = self._request_retrying(
            "GET", "/sessions", params={"agent_id": agent_id, "limit": 20}
        )
        for raw in page.get("data") or []:
            if isinstance(raw, dict) and (raw.get("metadata") or {}).get("romulus_case") == case_id:
                return session_from_raw(raw)
        return None

    def get_session(self, session_id: str) -> SessionState:
        return session_from_raw(self._request_retrying("GET", f"/sessions/{session_id}"))

    def session_events(self, session_id: str, after_seq: int = 0) -> list[SessionEvent]:
        """`GET /v1/sessions/{id}/events?after_seq=N` (F:6099), seq order, gap-free. Pages with
        `after=next_cursor` while `has_more`; a page can be empty with `has_more: true`."""
        events: list[SessionEvent] = []
        params: dict[str, Any] = {"after_seq": after_seq, "limit": 100}
        for _ in range(EVENT_PAGES_MAX):
            page = self._request_retrying("GET", f"/sessions/{session_id}/events", params=params)
            events.extend(event_from_raw(e) for e in page.get("data") or [])
            cursor = page.get("next_cursor")
            if not page.get("has_more") or not cursor:
                break
            params = {"after_seq": after_seq, "limit": 100, "after": cursor}
        return events

    def cancel_session(self, session_id: str) -> None:
        """`POST /v1/sessions/{id}/cancel`: terminal and idempotent (F:29205)."""
        self._request_retrying("POST", f"/sessions/{session_id}/cancel")

    def send_email(
        self,
        *,
        identity_id: str,
        inbox_id: str,
        to: list[str],
        subject: str,
        text: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        """`POST /v1/identities/{id}/emails/{eid}/send` (F:8172-8195). `to` is an array. Answers
        202 with a receipt `{object, accepted, from, to, sent_at}`: no message id (F:27198)."""
        return self._request_retrying(
            "POST",
            f"/identities/{identity_id}/emails/{inbox_id}/send",
            json={"to": list(to), "subject": subject, "text": text},
            headers={"Idempotency-Key": idempotency_key},
        )


def _error_from(resp: httpx.Response, method: str, path: str) -> NaiveError:
    # Naïve errors are {error: {type, code, message, request_id, param}} (api/errors);
    # the edge can also answer HTML (e.g. a 403 for SQL-looking bodies).
    code = message = request_id = None
    try:
        body = resp.json()
    except ValueError:
        body = None
    err = body.get("error") if isinstance(body, dict) else None
    if isinstance(err, dict):
        code, message, request_id = err.get("code"), err.get("message"), err.get("request_id")
    else:
        message = resp.text[:200]
    try:
        retry_after = float(resp.headers.get("retry-after", ""))
    except ValueError:
        retry_after = None
    return NaiveError(
        f"Naïve {method} {path} answered {resp.status_code}: {code or ''} {message or ''}".strip(),
        status=resp.status_code,
        code=code,
        request_id=request_id,
        retry_after=retry_after,
    )


class FakeNaive:
    """In-memory board. Each card goes todo → doing → done after `polls_to_done` reads.

    `outcome` ("result" or "stopped") is fixed per card when it is created, so a test can
    change `fake.outcome` between creates to script a mix.
    """

    BASE_TIME = datetime(2026, 1, 1, tzinfo=UTC)

    def __init__(
        self,
        board_id: str = "agt_fake",
        polls_to_done: int = 3,
        outcome: str = "result",
        cost_usd: str = "12.00",
        stop_reason: str = "part out of stock",
    ) -> None:
        if outcome not in ("result", "stopped"):
            raise ValueError(f"outcome must be 'result' or 'stopped', got {outcome!r}")
        self.board_id = board_id
        self.polls_to_done = polls_to_done
        self.outcome = outcome
        self.cost_usd = cost_usd
        self.stop_reason = stop_reason
        self.cards: dict[str, Card] = {}
        self.comments: dict[str, list[dict[str, Any]]] = {}
        self._by_key: dict[str, str] = {}
        self._requests: dict[str, tuple[str, str, str | None]] = {}
        self._polls: dict[str, int] = {}
        self._outcomes: dict[str, str] = {}
        self._tick = 0

    def _now(self) -> str:
        self._tick += 1
        return (self.BASE_TIME + timedelta(seconds=self._tick)).isoformat().replace("+00:00", "Z")

    def create_card(
        self, title: str, body: str, assignee: str | None, idempotency_key: str
    ) -> Card:
        _check_card(title, body)
        request = (title, body, assignee)
        if idempotency_key in self._by_key:
            if self._requests[idempotency_key] != request:
                # Same as the live API: a key reused with a different body is a 409.
                raise NaiveError(
                    f"fake Naïve: Idempotency-Key {idempotency_key} reused with a different body",
                    status=409,
                    code="idempotency_conflict",
                )
            return dict(self.cards[self._by_key[idempotency_key]])
        n = len(self.cards) + 1
        card_id = f"crd_fake{n:04d}"
        now = self._now()
        self.cards[card_id] = {
            "id": card_id,
            "object": "board_card",
            "board_id": self.board_id,
            "title": title,
            "body": body,
            "note": None,
            "status": "todo",
            "assignee": assignee,
            "blocked_by": [],
            "woken_session_id": None,
            "reported_status": None,
            "created_at": now,
            "updated_at": now,
        }
        self.comments[card_id] = []
        self._by_key[idempotency_key] = card_id
        self._requests[idempotency_key] = request
        self._polls[card_id] = 0
        self._outcomes[card_id] = self.outcome
        return dict(self.cards[card_id])

    def get_card(self, card_id: str) -> Card:
        if card_id not in self.cards:
            raise NaiveError(f"fake Naïve: no card {card_id}", status=404, code="not_found")
        card = self.cards[card_id]
        if card["status"] != "done":
            self._polls[card_id] += 1
            if self._polls[card_id] >= self.polls_to_done:
                self._finish(card)
            elif card["status"] == "todo":
                card["status"] = "doing"
                card["woken_session_id"] = f"ses_fake{card_id[-4:]}"
                card["updated_at"] = self._now()
        return dict(card)

    def _finish(self, card: Card) -> None:
        n = card["id"][-4:]
        if self._outcomes[card["id"]] == "result":
            first = f"RESULT: ordered ORDER_REF=FAKE-{n} COST_USD={self.cost_usd}"
        else:
            first = f"STOPPED: {self.stop_reason}"
        card["note"] = f"{first}\n(fake Naïve: no agent ran and no order was placed)"
        card["status"] = "done"
        card["woken_session_id"] = card["woken_session_id"] or f"ses_fake{n}"
        card["updated_at"] = self._now()
        self.comments[card["id"]].append(
            {
                "id": f"cmt_fake{n}",
                "author": card["woken_session_id"],
                "body": f"fake Naïve: {first}",
                "created_at": card["updated_at"],
            }
        )

    def list_comments(self, card_id: str) -> list[dict[str, Any]]:
        if card_id not in self.cards:
            raise NaiveError(f"fake Naïve: no card {card_id}", status=404, code="not_found")
        return [dict(c) for c in self.comments[card_id]]


def build_naive(
    mode: str,
    api_key: str | None = None,
    board_id: str | None = None,
    base_url: str = DEFAULT_BASE_URL,
    version: str = DEFAULT_VERSION,
) -> NaiveClient:
    """Factory called with NAIVE_MODE, NAIVE_API_KEY, NAIVE_BOARD_ID, NAIVE_BASE_URL, NAIVE_VERSION
    (architecture §6)."""
    if mode == "fake":
        return FakeNaive(board_id=board_id or "agt_fake")
    if mode != "live":
        raise ValueError(f"unknown NAIVE_MODE {mode!r} (expected fake or live)")
    if not api_key or not board_id:
        raise ValueError("NAIVE_MODE=live needs NAIVE_API_KEY and NAIVE_BOARD_ID")
    return HttpNaive(api_key, board_id, base_url=base_url, version=version)


# --- Naïve Technician: sessions (design §2, §7; build contract Interface 1) -------------------
#
# Where things live in the raw API JSON (docs/raw/naive-llms-full.txt):
# - session object (F:9959-10101): `status` (queued|running|idle|completed|failed|cancelled),
#   `stop_reason` (F:6093 enum), `structured_output` (the typed report, set when the session goes
#   idle satisfying `output_schema`), `consumed_micro_usd`, `created_at`, `started_at`, `ended_at`
#   (terminal states only: an idle session has `ended_at: null`). No Studio URL is returned.
# - events (F:6040-6091): `{id, seq, type, session_id, data, created_at}`. The report also shows
#   up as `tool.started` for `submit_output` (args = the report) and on `session.idle`'s
#   `data.structured_output` (F:13770). `session.usage` carries the running `consumed_micro_usd`.

SESSION_STATES = ("running", "idle", "awaiting", "error", "cancelled")
_AWAITING_STOPS = frozenset(
    {"awaiting_input", "awaiting_approval", "awaiting_answer", "awaiting_delegation"}
)


@dataclass(frozen=True)
class SessionState:
    id: str
    state: str  # one of SESSION_STATES (normalized; raw status/stop_reason stay in `raw`)
    stop_reason: str | None
    structured_output: dict[str, Any] | None
    cost_usd: float
    created_at: datetime | None
    ended_at: datetime | None
    studio_url: str | None
    raw: dict[str, Any] = field(repr=False)


@dataclass(frozen=True)
class SessionEvent:
    seq: int
    at: datetime | None
    kind: str  # tool_call | tool_result | message | output | status | other
    tool: str | None
    args: dict[str, Any]
    text: str | None
    raw: dict[str, Any] = field(repr=False)


class TechnicianNaive(Protocol):
    def create_session(self, body: dict[str, Any], *, idempotency_key: str) -> SessionState: ...

    def find_session(self, agent_id: str, case_id: str) -> SessionState | None: ...

    def get_session(self, session_id: str) -> SessionState: ...

    def session_events(self, session_id: str, after_seq: int = 0) -> list[SessionEvent]: ...

    def cancel_session(self, session_id: str) -> None: ...

    def send_email(
        self,
        *,
        identity_id: str,
        inbox_id: str,
        to: list[str],
        subject: str,
        text: str,
        idempotency_key: str,
    ) -> dict[str, Any]: ...


def parse_ts(value: Any) -> datetime | None:
    """An API timestamp (`2026-08-20T17:05:00Z`) as an aware datetime, or None."""
    if not isinstance(value, str) or not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def _clip(text: Any, limit: int = TEXT_MAX) -> str | None:
    if text is None:
        return None
    if not isinstance(text, str):
        text = json.dumps(text, ensure_ascii=False)
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def normalize_state(status: str | None, stop_reason: str | None) -> str:
    """Raw `status` + `stop_reason` → running | idle | awaiting | error | cancelled (inferred):
    queued/running → running; cancelled → cancelled; failed → error; idle with an `awaiting_*`
    stop → awaiting; idle with `error` → error; any other idle (end_turn, budget_paused,
    interrupted, max_iterations, context_exhausted) or completed → idle. Callers branch on
    `stop_reason` for those, as the docs say (F:10125)."""
    if status in ("queued", "running", None):
        return "running"
    if status == "cancelled":
        return "cancelled"
    if status == "failed":
        return "error"
    if stop_reason in _AWAITING_STOPS:
        return "awaiting"
    if stop_reason == "error":
        return "error"
    return "idle"


def session_from_raw(raw: dict[str, Any]) -> SessionState:
    out = raw.get("structured_output")
    micro = raw.get("consumed_micro_usd") or 0
    return SessionState(
        id=str(raw.get("id") or ""),
        state=normalize_state(raw.get("status"), raw.get("stop_reason")),
        stop_reason=raw.get("stop_reason"),
        structured_output=out if isinstance(out, dict) else None,
        cost_usd=round(float(micro) / 1e6, 6),
        created_at=parse_ts(raw.get("created_at")),
        ended_at=parse_ts(raw.get("ended_at")),
        studio_url=raw.get("studio_url") or None,
        raw=raw,
    )


_STATUS_TYPES = frozenset(
    {
        "session.running",
        "session.idle",
        "session.usage",
        "session.retrying",
        "session.degraded",
        "budget.exceeded",
        "tool.confirm",
    }
)


def _status_text(etype: str, data: dict[str, Any]) -> str | None:
    if etype == "session.running":
        return "turn started"
    if etype == "session.idle":
        text = f"idle: {data.get('stop_reason') or '?'}"
        return _clip(f"{text} ({data['error']})") if data.get("error") else text
    if etype == "session.usage":
        micro = data.get("consumed_micro_usd") or 0
        return f"spent ${float(micro) / 1e6:.2f} so far"
    if etype == "session.retrying":
        return f"retrying ({data.get('code') or 'error'})"
    if etype == "tool.confirm":
        return f"waiting for approval: {data.get('name')}"
    return _clip(data.get("message") or data.get("reason") or etype)


def event_from_raw(raw: dict[str, Any]) -> SessionEvent:
    etype = str(raw.get("type") or "")
    data = raw.get("data") if isinstance(raw.get("data"), dict) else {}
    tool: str | None = None
    args: dict[str, Any] = {}
    text: str | None = None
    if etype == "tool.started":
        tool = data.get("name")
        a = data.get("args")
        args = a if isinstance(a, dict) else ({} if a is None else {"value": a})
        kind = "output" if tool == "submit_output" else "tool_call"
    elif etype == "tool.completed":
        tool = data.get("name")
        kind = "tool_result"
        text = _clip(data.get("output"))
        if data.get("is_error"):
            text = _clip(f"error: {text or ''}".strip())
    elif etype == "message.completed":
        mine = data.get("role") == "user" or data.get("from") == "platform"
        kind = "other" if mine else "message"
        text = _clip(data.get("content"))
    elif etype in _STATUS_TYPES:
        kind = "status"
        tool = data.get("name") if etype == "tool.confirm" else None
        text = _status_text(etype, data)
    else:
        kind = "other"
        text = _clip(data.get("content")) if "content" in data else None
    return SessionEvent(
        seq=int(raw.get("seq") or 0),
        at=parse_ts(raw.get("created_at")),
        kind=kind,
        tool=tool,
        args=args,
        text=text,
        raw=raw,
    )


class FixtureNaive:
    """Serves one recorded session file (tests, and the stage replay; design §7).

    Fixture format (raw API JSON, so a replay is honest):
    `{"captured_at", "request": <POST body>, "session": <raw final session JSON, with
    structured_output inside it>, "events": [<raw events>], "duration_s", "cost_usd"}`.

    `create_session` records `now()`; events come out in step with the time since then, scaled
    so the recorded run plays in `play_seconds` (0 = all at once). `get_session` says running
    until every event is out, then the recorded final session. Timestamps stay as recorded.
    The first case gets the recorded session id; another idempotency key gets `<id>_r2`, ….
    """

    def __init__(
        self,
        fixture: dict[str, Any] | Path | str,
        *,
        now: Callable[[], datetime],
        play_seconds: float = 0,
    ) -> None:
        if not isinstance(fixture, dict):
            fixture = json.loads(Path(fixture).read_text())
        self.fixture = fixture
        self.now = now
        self.play_seconds = float(play_seconds)
        self.final: dict[str, Any] = dict(fixture["session"])
        self.events_raw: list[dict[str, Any]] = sorted(
            fixture.get("events") or [], key=lambda e: int(e.get("seq") or 0)
        )
        self._release_at = self._release_fractions()
        self.sent: list[dict[str, Any]] = []
        self.cancelled: list[str] = []
        self.requests: dict[str, dict[str, Any]] = {}
        self._by_key: dict[str, str] = {}
        self._started: dict[str, datetime] = {}
        self._receipts: dict[str, tuple[dict[str, Any], dict[str, Any]]] = {}

    def _release_fractions(self) -> list[float]:
        """Each event's release point in [0, 1]: recorded time offsets if every event has one,
        else evenly by count."""
        times = [parse_ts(e.get("created_at")) for e in self.events_raw]
        n = len(times)
        if n and all(t is not None for t in times):
            stamps = [t for t in times if t is not None]
            span = (stamps[-1] - stamps[0]).total_seconds()
            if span > 0:
                return [(t - stamps[0]).total_seconds() / span for t in stamps]
        return [(i + 1) / n for i in range(n)] if n else []

    def _released(self, session_id: str) -> int:
        start = self._started.get(session_id)
        if start is None:
            raise NaiveError(
                f"fixture Naïve: no session {session_id}", status=404, code="not_found"
            )
        if self.play_seconds <= 0:
            return len(self.events_raw)
        frac = (self.now() - start).total_seconds() / self.play_seconds
        return sum(1 for f in self._release_at if f <= frac)

    def create_session(self, body: dict[str, Any], *, idempotency_key: str) -> SessionState:
        if idempotency_key in self._by_key:
            if self.requests[idempotency_key] != body:
                raise NaiveError(
                    f"fixture Naïve: Idempotency-Key {idempotency_key} reused with another body",
                    status=409,
                    code="idempotency_conflict",
                )
            return self.get_session(self._by_key[idempotency_key])
        n = len(self._by_key) + 1
        base_id = str(self.final.get("id") or "ses_fixture")
        session_id = base_id if n == 1 else f"{base_id}_r{n}"
        self._by_key[idempotency_key] = session_id
        self.requests[idempotency_key] = body
        self._started[session_id] = self.now()
        return self.get_session(session_id)

    def find_session(self, agent_id: str, case_id: str) -> SessionState | None:
        """The session created for this case (idempotency key or `metadata.romulus_case`)."""
        for key, session_id in self._by_key.items():
            meta = self.requests[key].get("metadata") or {}
            if case_id in (key, meta.get("romulus_case")):
                return self.get_session(session_id)
        return None

    def get_session(self, session_id: str) -> SessionState:
        released = self._released(session_id)
        total = len(self.events_raw)
        raw = {**self.final, "id": session_id}
        if session_id in self.cancelled and raw.get("status") not in ("completed", "failed"):
            raw.update(status="cancelled", stop_reason="interrupted")
            if released < total:
                raw["structured_output"] = None
        elif released < total:
            frac = released / total if total else 0.0
            raw.update(
                status="running",
                stop_reason=None,
                structured_output=None,
                ended_at=None,
                consumed_micro_usd=int((self.final.get("consumed_micro_usd") or 0) * frac),
                last_seq=int(self.events_raw[released - 1].get("seq") or 0) if released else 0,
            )
        return session_from_raw(raw)

    def session_events(self, session_id: str, after_seq: int = 0) -> list[SessionEvent]:
        released = self.events_raw[: self._released(session_id)]
        return [
            event_from_raw({**e, "session_id": session_id})
            for e in released
            if int(e.get("seq") or 0) > after_seq
        ]

    def cancel_session(self, session_id: str) -> None:
        self._released(session_id)  # 404 for an unknown id, like the live API
        if session_id not in self.cancelled:
            self.cancelled.append(session_id)

    def send_email(
        self,
        *,
        identity_id: str,
        inbox_id: str,
        to: list[str],
        subject: str,
        text: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        call = {
            "identity_id": identity_id,
            "inbox_id": inbox_id,
            "to": list(to),
            "subject": subject,
            "text": text,
            "idempotency_key": idempotency_key,
        }
        if idempotency_key in self._receipts:
            first, receipt = self._receipts[idempotency_key]
            if first != call:
                raise NaiveError(
                    f"fixture Naïve: Idempotency-Key {idempotency_key} reused with another body",
                    status=409,
                    code="idempotency_conflict",
                )
            return dict(receipt)
        receipt = {
            "object": "email_send",
            "accepted": True,
            "from": "technician@fixture.invalid",
            "to": list(to),
            "sent_at": self.now().astimezone(UTC).isoformat().replace("+00:00", "Z"),
        }
        self.sent.append(call)
        self._receipts[idempotency_key] = (call, receipt)
        return dict(receipt)
