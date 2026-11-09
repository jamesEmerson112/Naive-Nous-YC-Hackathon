"""The job board, two ways: the real Naïve board through the `naive` CLI, or a local file board
with the same rules for sim runs and tests (no key, no network).

    from armsim.board import open_board
    b = open_board("file")                    # runs/board.json; "naive" for the real board
    card = b.add("ARM JOB ord-1: pick up the block", body="...", key="ord-1")
    b.move(card["id"], status="doing")
    b.comment(card["id"], "RESULT: picked it up")
    b.done(card["id"], note="Picked up the block; healed a stuck sensor")

The rules both keep (docs/raw/naive-llms-full.txt, `naive board`):
- four columns: todo, doing, blocked, done;
- `add --key` is idempotent: the same key on the same board answers the same card;
- `done` needs a note, else 409 `missing_note`; `move --status done` is refused the same way
  unless the card already carries a note;
- there is no delete.

Only `naive board add --json` has been run against the real CLI (story b --hermes, Sat night). The
other verbs' output shapes are parsed leniently: run `python -m armsim.board smoke` on the Mac first.
"""
import argparse
import fcntl
import json
import os
import re
import subprocess
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COLUMNS = ("todo", "doing", "blocked", "done")
DEFAULT_BOARD = os.environ.get("ARMSIM_BOARD", "brd_v2zcnyzc559hv8awzc4hqvgcsm")
FILE_BOARD = Path(os.environ.get("ARMSIM_FILE_BOARD") or ROOT / "runs" / "board.json")


class BoardError(RuntimeError):
    """A refused board call. `code` is the control plane's reason when there is one (missing_note)."""

    def __init__(self, message, code=None):
        super().__init__(message)
        self.code = code


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _unwrap(obj):
    """{'data': x} -> x, repeatedly (any envelope: {data, has_more, next_cursor, ...}); a list stays a list."""
    while isinstance(obj, dict) and not obj.get("id") and isinstance(obj.get("data"), (dict, list)):
        obj = obj["data"]
    return obj


MARKER = re.compile(r"^\W*(RESULT|BLOCKED|CLAIMED|PLAN)\b", re.I)


def marker(text):
    """'RESULT' / 'BLOCKED' / 'CLAIMED' / 'PLAN' when a comment starts with one, even as '**BLOCKED:**'
    (models like bold); else None."""
    m = MARKER.match(text or "")
    return m.group(1).upper() if m else None


def _as_cards(obj):
    obj = _unwrap(obj)
    if isinstance(obj, dict):
        for k in ("cards", "items", "results"):
            if isinstance(obj.get(k), list):
                return obj[k]
        return [obj] if obj.get("id") else []
    return obj if isinstance(obj, list) else []


class FileBoard:
    """A local board in one JSON file, locked per call, so several processes (the dispatcher, the
    soak, Mission Control) can share it."""

    kind = "file"

    def __init__(self, path=FILE_BOARD, board_id="brd_local"):
        self.path, self.id = Path(path), board_id
        self.path.parent.mkdir(parents=True, exist_ok=True)

    @contextmanager
    def _db(self, write=False):
        lock = self.path.with_suffix(".lock")
        with open(lock, "a+") as lf:
            fcntl.flock(lf, fcntl.LOCK_EX if write else fcntl.LOCK_SH)
            try:
                db = json.loads(self.path.read_text()) if self.path.exists() else {"cards": []}
            except json.JSONDecodeError:
                db = {"cards": []}
            yield db
            if write:
                tmp = self.path.with_suffix(".tmp")
                tmp.write_text(json.dumps(db, indent=1))
                tmp.replace(self.path)

    def _find(self, db, card_id):
        for c in db["cards"]:
            if c["id"] == card_id:
                return c
        raise BoardError(f"no card {card_id!r} on {self.id}", code="not_found")

    def add(self, title, *, body="", key=None, status="todo", assignee=None):
        if status not in COLUMNS:
            raise BoardError(f"unknown status {status!r}; columns are {COLUMNS}", code="invalid")
        with self._db(write=True) as db:
            if key:
                for c in db["cards"]:
                    if c.get("key") == key:
                        return dict(c)
            card = {"id": f"crd_{uuid.uuid4().hex[:12]}", "board_id": self.id, "title": title, "body": body,
                    "status": status, "assignee": assignee, "note": None, "key": key,
                    "created_at": now_iso(), "updated_at": now_iso(), "comments": []}
            db["cards"].append(card)
            return dict(card)

    def show(self, status=None):
        with self._db() as db:
            cards = [dict(c) for c in db["cards"] if status is None or c["status"] == status]
        order = {s: i for i, s in enumerate(COLUMNS)}
        return sorted(cards, key=lambda c: (order[c["status"]], c["created_at"]))

    def card(self, card_id):
        with self._db() as db:
            return dict(self._find(db, card_id))

    def comments(self, card_id):
        with self._db() as db:
            return list(self._find(db, card_id).get("comments", []))

    def comment(self, card_id, body):
        with self._db(write=True) as db:
            c = self._find(db, card_id)
            com = {"id": f"cmt_{uuid.uuid4().hex[:10]}", "body": body, "author": "local", "created_at": now_iso()}
            c.setdefault("comments", []).append(com)
            c["updated_at"] = now_iso()
            return dict(com)

    def move(self, card_id, *, status=None, title=None, body=None, assignee=None, expect=None):
        """expect: the status the card must still have (compare-and-set under the lock), else
        BoardError(code="conflict"). How a claim can't win twice."""
        with self._db(write=True) as db:
            c = self._find(db, card_id)
            if expect is not None and c["status"] != expect:
                raise BoardError(f"card {card_id} is {c['status']}, not {expect}", code="conflict")
            if status is not None:
                if status not in COLUMNS:
                    raise BoardError(f"unknown status {status!r}; columns are {COLUMNS}", code="invalid")
                if status == "done" and not c.get("note"):
                    raise BoardError("409 state_conflict: a done needs a note (missing_note)", code="missing_note")
                c["status"] = status
            for k, v in (("title", title), ("body", body), ("assignee", assignee)):
                if v is not None:
                    c[k] = v
            c["updated_at"] = now_iso()
            return dict(c)

    def done(self, card_id, *, note=None):
        with self._db(write=True) as db:
            c = self._find(db, card_id)
            if not (note or c.get("note")):
                raise BoardError("409 state_conflict: a done needs a note (missing_note)", code="missing_note")
            if note:
                c["note"] = note
            c["status"], c["updated_at"] = "done", now_iso()
            return dict(c)


class NaiveBoard:
    """The real board, through the `naive` CLI (it holds the key from `naive login`)."""

    kind = "naive"

    def __init__(self, board_id=DEFAULT_BOARD, *, cli="naive", timeout=60):
        self.id, self.cli, self.timeout = board_id, cli, timeout

    def _run(self, *args):
        cmd = [self.cli, "board", *args, "--json"]
        try:
            # Its own session: a first, gentle Ctrl-C to the dispatcher must not kill a board call in flight.
            out = subprocess.run(cmd, capture_output=True, text=True, timeout=self.timeout, start_new_session=True)
        except FileNotFoundError as exc:
            raise BoardError(f"`{self.cli}` is not installed here: {exc}", code="no_cli") from exc
        except subprocess.TimeoutExpired as exc:
            raise BoardError(f"`{' '.join(cmd[:4])}` timed out after {self.timeout} s", code="timeout") from exc
        text = (out.stdout or "").strip()
        if out.returncode != 0:
            msg = (out.stderr or text or f"exit {out.returncode}").strip()
            raise BoardError(f"`naive board {args[0]}` failed: {msg[:400]}",
                             code="missing_note" if "missing_note" in msg else None)
        if not text:
            return {}
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            # Some verbs may print a line of text before the JSON: take the last JSON-looking line.
            for line in reversed(text.splitlines()):
                line = line.strip()
                if line.startswith(("{", "[")):
                    try:
                        return json.loads(line)
                    except json.JSONDecodeError:
                        continue
            return {"text": text}

    def add(self, title, *, body="", key=None, status="todo", assignee=None):
        args = ["add", self.id, "--title", title]
        if body:
            args += ["--body", body]
        if key:
            args += ["--key", key[:128]]
        if status and status != "todo":
            args += ["--status", status]
        if assignee:
            args += ["--assignee", assignee]
        return _unwrap(self._run(*args))

    def show(self, status=None):
        args = ["show", self.id] + (["--status", status] if status else [])
        return _as_cards(self._run(*args))

    def card(self, card_id):
        return _unwrap(self._run("card", self.id, card_id))

    def comments(self, card_id):
        return _as_cards(self._run("comments", self.id, card_id)) or []

    def comment(self, card_id, body):
        return _unwrap(self._run("comment", self.id, card_id, "--body", body))

    def move(self, card_id, *, status=None, title=None, body=None, assignee=None, expect=None):
        """expect: re-read the card first and refuse (code "conflict") unless it still has that status.
        The API has no conditional update ("No race guard"), so this narrows the window, nothing more."""
        if expect is not None:
            cur = card_status(self.card(card_id))
            if cur != expect:
                raise BoardError(f"card {card_id} is {cur}, not {expect}", code="conflict")
        args = ["move", self.id, card_id]
        for flag, v in (("--status", status), ("--title", title), ("--body", body), ("--assignee", assignee)):
            if v is not None:
                args += [flag, v]
        return _unwrap(self._run(*args))

    def done(self, card_id, *, note=None):
        args = ["done", self.id, card_id] + (["--note", note] if note else [])
        return _unwrap(self._run(*args))


def open_board(kind=None, board_id=None):
    """kind: "file" (default in sim; $ARMSIM_BOARD_KIND) or "naive" (the real board)."""
    kind = kind or os.environ.get("ARMSIM_BOARD_KIND", "file")
    if kind == "naive":
        return NaiveBoard(board_id or DEFAULT_BOARD)
    if kind == "file":
        return FileBoard(board_id=board_id or "brd_local")
    raise SystemExit(f"unknown board kind {kind!r}: file or naive")


def card_status(card):
    """A card's column, or None when the read gave no recognisable card (treat as unknown, not as not-done)."""
    st = (_unwrap(card) or {}).get("status") if isinstance(card, dict) else None
    return st if st in COLUMNS else None


def comment_text(c):
    return (c.get("body") or c.get("text") or c.get("content") or "") if isinstance(c, dict) else str(c)


def smoke(board):
    """Every verb once on a throwaway card, printing each output shape. Run it on the Mac before the
    dispatcher touches the real board."""
    key = f"armsim-smoke-{int(time.time())}"
    steps = []
    card = board.add("ARM SMOKE: board check (safe to ignore)", body="armsim.board smoke test", key=key)
    steps.append(("add", card))
    cid = card.get("id")
    if not cid:
        raise SystemExit(f"add returned no card id: {card!r}")
    steps.append(("add again, same key (same card?)", board.add("ARM SMOKE: dup", key=key).get("id") == cid))
    steps.append(("show todo has it", any(c.get("id") == cid for c in board.show("todo"))))
    steps.append(("move doing", board.move(cid, status="doing").get("status")))
    steps.append(("comment", board.comment(cid, "SMOKE: a comment")))
    steps.append(("comments", [comment_text(c) for c in board.comments(cid)]))
    try:
        board.move(cid, status="done")
        steps.append(("move done without a note", "ACCEPTED (unexpected)"))
    except BoardError as exc:
        steps.append(("move done without a note", f"refused, code={exc.code}"))
    steps.append(("done with a note", board.done(cid, note="smoke test finished").get("status")))
    steps.append(("card", {k: board.card(cid).get(k) for k in ("id", "status", "note")}))
    for name, out in steps:
        print(f"{name:34s} {json.dumps(out, default=str)[:300]}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="The job board: smoke-test it or list it.")
    ap.add_argument("cmd", choices=["smoke", "show"])
    ap.add_argument("--board", choices=["file", "naive"], default=None)
    ap.add_argument("--board-id", default=None)
    a = ap.parse_args()
    b = open_board(a.board, a.board_id)
    if a.cmd == "smoke":
        smoke(b)
    else:
        for c in b.show():
            print(f"{c.get('id')}  {c.get('status'):8s} {c.get('title')}")
