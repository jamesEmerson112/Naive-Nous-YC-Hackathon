"""NaiveClient: work orders out as board cards, status back by polling (Naïve integration §3).

Absorbs the Naïve REST API (`https://api.vetta.sh/v1`, `Vetta-Version` header, bearer auth,
`Idempotency-Key` on POST). Outbound only: Naïve never calls us. FakeNaive is the offline
default; it walks each card to `done` after N polls with a `RESULT:` (or `STOPPED:`) note, so
the worker's sync loop can be exercised with no network.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

import httpx

DEFAULT_BASE_URL = "https://api.vetta.sh/v1"
DEFAULT_VERSION = "2026-09-30"
DEFAULT_TIMEOUT_S = 15.0
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
    ) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.request_id = request_id

    @property
    def retryable(self) -> bool:
        if self.status is None or self.status == 429 or self.status >= 500:
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
        board_id: str,
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
        }
        self._http = http_client or httpx.Client(timeout=timeout_s)
        self._timeout = timeout_s

    def _request(
        self,
        method: str,
        path: str,
        json: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        params: dict[str, str] | None = None,
    ) -> Any:
        url = f"{self._base}{path}"
        try:
            resp = self._http.request(
                method,
                url,
                json=json,
                params=params,
                headers={**self._headers, **(headers or {})},
                timeout=self._timeout,
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
    return NaiveError(
        f"Naïve {method} {path} answered {resp.status_code}: {code or ''} {message or ''}".strip(),
        status=resp.status_code,
        code=code,
        request_id=request_id,
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
