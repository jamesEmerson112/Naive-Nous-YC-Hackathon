"""Order intake: an order becomes an ARM JOB card on the job board.

    python -m armsim.orders add pick                    # file board (sim)
    python -m armsim.orders add pick --board naive      # the real Naïve board
    python -m armsim.orders list

The ARM JOB card format (the dispatcher and Hermes both read it):
  title  "ARM JOB ord-20261108-031500-a1b2: pick up the block"
  body   the brief a person or Hermes reads, then machine lines, one per line:
           job: pick
           order: ord-20261108-031500-a1b2
           source: soak | cli | mission | naive
           steps: go_home, reach, approach, grip, lift, release, go_home
  key    the order id, so placing the same order twice gives the same card.
A planted fault is never written on the card: the commander must find it the way it would on the bench.
"""
import argparse
import re
import time
import uuid
from datetime import datetime

from . import events
from .board import open_board

PREFIX = "ARM JOB"
JOBS = {
    "pick": {"title": "pick up the block",
             "steps": ["go_home", "reach", "approach", "grip", "lift", "release", "go_home"],
             "brief": "Pick up the 3 cm block: reach, approach, grip, lift 30 mm, then release it and go home."},
    "wave": {"title": "wave hello",
             "steps": ["go_home", "wave", "go_home"],
             "brief": "Wave hello to the visitor, then go home."},
}
RULES = ("Heal any fault and retry that step once. When it's done, comment 'RESULT:' with what you did and any fault "
         "you healed, and move this card to done. If you can't finish, comment 'BLOCKED:' with the reason.")
ORDER_RE = re.compile(r"\bord-[0-9]{8}-[0-9]{6}-[0-9a-f]{4}\b")


def new_order_id(now=None):
    t = time.localtime(now or time.time())
    return f"ord-{time.strftime('%Y%m%d-%H%M%S', t)}-{uuid.uuid4().hex[:4]}"


def card_text(job, order_id, source):
    spec = JOBS[job]
    title = f"{PREFIX} {order_id}: {spec['title']}"
    body = (f"{spec['brief']} {RULES}\n\n"
            f"job: {job}\norder: {order_id}\nsource: {source}\nsteps: {', '.join(spec['steps'])}")
    return title, body


def place(board, job="pick", *, source="cli", order_id=None):
    """Put one order on the board; returns (order, card)."""
    if job not in JOBS:
        raise SystemExit(f"unknown job {job!r}; jobs: {sorted(JOBS)}")
    order_id = order_id or new_order_id()
    title, body = card_text(job, order_id, source)
    card = board.add(title, body=body, key=order_id)
    order = {"id": order_id, "job": job, "source": source, "card": card.get("id"),
             "placed_at": datetime.now().isoformat(timespec="seconds")}
    events.emit("order", "order_placed", f"order {order_id} ({JOBS[job]['title']}) is card {card.get('id')}",
                order=order_id, job=job, card=card.get("id"), source=source, board=getattr(board, "kind", "?"))
    return order, card


def is_arm_job(card):
    return (card.get("title") or "").startswith(PREFIX)


def parse_card(card):
    """{job, order, steps} from an ARM JOB card; tolerant of hand-written cards (job defaults to pick)."""
    body, title = card.get("body") or "", card.get("title") or ""
    fields = dict(re.findall(r"^(job|order|source|steps):\s*(.+)$", body, flags=re.M))
    job = fields.get("job", "").strip()
    if job not in JOBS:
        job = "wave" if "wave" in title.lower() else "pick"
    m = ORDER_RE.search(fields.get("order", "")) or ORDER_RE.search(title)
    steps = [s.strip() for s in fields["steps"].split(",")] if fields.get("steps") else JOBS[job]["steps"]
    return {"job": job, "order": m.group(0) if m else None, "source": fields.get("source", "").strip() or None,
            "steps": steps}


def main():
    ap = argparse.ArgumentParser(description="Place an arm order: it becomes an ARM JOB card.")
    ap.add_argument("cmd", choices=["add", "list"])
    ap.add_argument("job", nargs="?", default="pick", choices=sorted(JOBS))
    ap.add_argument("--board", choices=["file", "naive"], default=None)
    ap.add_argument("--board-id", default=None)
    ap.add_argument("--source", default="cli")
    a = ap.parse_args()
    board = open_board(a.board, a.board_id)
    if a.cmd == "add":
        order, card = place(board, a.job, source=a.source)
        print(f"{order['id']} -> {card.get('id')} '{card.get('title')}'")
    else:
        for c in board.show():
            if is_arm_job(c):
                print(f"{c.get('id')}  {c.get('status', '?'):8s} {c.get('title')}")


if __name__ == "__main__":
    main()
