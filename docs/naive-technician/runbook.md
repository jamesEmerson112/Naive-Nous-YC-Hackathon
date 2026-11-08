# Naïve Technician: demo runbook

_Sun Nov 8. Needs the PR #68 backend (`tech/backend`). Shot list and stage plan: [`demo-clip.md`](demo-clip.md); routes: [`design.md`](design.md) §9. Helper: `cloud/scripts/technician_demo.py` (stdlib; `--help`)._

## Before filming
- **Stack**, from the repo root: `export COMPOSE_PROJECT_NAME=tech`, then `make dev && make seed`. Export it: `COMPOSE_PROJECT_NAME=tech make dev && make seed` seeds the default project, not `tech`.
- **`.env`:** `TECHNICIAN_MODE=live`, plus the ids already there (`NAIVE_API_KEY`, `NAIVE_TECHNICIAN_AGENT`, `NAIVE_TECHNICIAN_IDENTITY`, `NAIVE_TECHNICIAN_INBOX`, `TECHNICIAN_EMAIL`; an empty `TECHNICIAN_EMAIL` sends no email).
- **After changing `.env`**, run `make dev` again. Compose reads `.env` as `env_file` when it creates a container, so `docker compose -p tech restart api worker` keeps the old values.
- **Browser:** zoom 125–150%, cursor visible. On the case page, click **change name** and enter `kent`, so Approve records `human:kent`. Notifications off everywhere except the teammate's phone.

## 9:00 rehearsal, 9:15 guaranteed clip
- `python3 cloud/scripts/technician_demo.py live` posts a fresh `joint_stall` for `sim-1`, waits for its work order, opens a live case and opens the page. It takes about 30 s, starts one Naïve session (≤ `TECHNICIAN_SESSION_USD`, $3) and **sends one real email**.
- `python3 cloud/scripts/technician_demo.py --status <case_id>` prints the status, verdict, email, staged part, cost, duration and Naïve session id.
- **Shots** (`demo-clip.md`):
  - 1, arm: `sim-1` is a sim incident, so caption the arm "re-enacted" or leave it out (honesty rules).
  - 2: Naïve Studio → agent `technician` → the session (its id is under `--status`).
  - 3: the case page's verdict.
  - 4: the phone with the email.
  - 5: **Approve & open cart** → the WowRobo cart.

## 9:30 arm slot
- On the Pi: `ARMSIM_ROMULUS=http://<kent-ip>:8000 prototypes/arm-sim/demo/up.sh --real` (`ipconfig getifaddr en0` gives Kent's IP).
- `make seed` registers `arm-1` as `real`, so in live mode its unresolved `joint_stall` opens a live case on its own: the worker's `technician.open` step runs every 5 s. Lock the elbow **both ways**; with foam alone, the move home succeeds, the stall reports resolved and no case opens.
- `open http://localhost:8000/technician` redirects to the newest case. At most 2 takes, 2 min apart, because the servo heats at stall.

## On stage
- **Before going on:** `python3 cloud/scripts/technician_demo.py replay --no-open` and keep its page URL in a tab. It plays the bundled recording over `TECHNICIAN_REPLAY_SECONDS` (20 s) and shows the "Recorded Naïve session" badge.
- **During the pitch:** a live run takes about 30 s, so run `technician_demo.py live` while you talk. The replay tab is the fallback ("this is the run from this morning"). Bring a hotspot.

## If things break
- **The script's own errors:** stack not up; a 409 with the server's reason (mode off, `live needs NAIVE_API_KEY` or `NAIVE_TECHNICIAN_AGENT`, order not `needs_human`); no work order within 15 s (check `docker compose -p tech logs worker`).
- **Shop 429:** WowRobo rate-limits `/products/<handle>.js` from inside Docker. `shop.product` then falls back to `/products.json?limit=250` and caches a good read for 10 min. If the store still fails, the gate re-checks every ~20 s for up to 10 min before it shows "not staged", and the email carries a Romulus note saying the part isn't staged yet (#68).
- **Naïve "at capacity":**
  - This morning, sonnet sessions started in about 2 s while opus queued indefinitely, so keep the `technician` agent on sonnet.
  - A case that hangs in diagnosing: `curl -XPOST localhost:8000/v1/technician/cases/<id>/stop`, then switch to the replay tab.
  - A `failed` case can be replaced on the same order with `curl -XPOST 'localhost:8000/v1/work-orders/<wo>/technician?mode=replay'`.
- **Never `make reset` Olir's laptop cloud:** it holds the pod's real agent runs.
