# Fleet learning: demo runbook

_Sun Nov 8, draft. Needs #82 (cluster review) on `main`. The table script is [`demo.md`](demo.md) §3 and §5, and its honesty rules are §7. Helper: `cloud/scripts/learning_demo.py` (`seed`, `status`, `reset-learn`). Numbers marked **(fixture)** come from the recorded rehearsal; the live numbers replace them after the live run._

## The stack
- **Where:** compose project `learn`, API on **:8001**, worktree `.claude/worktrees/learn-stack`. Never the `tech` stack (:8000, Kent's 1 PM demo), never Olir's laptop cloud, never `make reset` (only `learning_demo.py reset-learn`).
- **Local override** (untracked, in the worktree): `compose.learn.yaml` reads the main checkout's `.env` (`NAIVE_API_KEY`, `NAIVE_BOARD_ID`, `NAIVE_REVIEW_AGENT`) and sets the learn values from shell variables:

| Variable | Default | Fixture run | Live run | Table (after) |
|---|---|---|---|---|
| `LEARNING_MODE` | `data` | `fixture` | `live` | `data` |
| `LEARN_NAIVE_MODE` → `NAIVE_MODE` | `fake` | `fake` | `live` (the Skills publisher follows it) | `fake` |
| `REVIEW_PUBLISH` | `false` | `true` (FakeNaive, in memory) | `true` | `false` |
| `REVIEW_INSIGHTS_CARD` | empty | empty | `crd_px969fbze2p5fhh0ywczxzc5ap` | empty |
| `REVIEW_MAX_SESSIONS_PER_HOUR` | 2 | 4 | 2 | 2 |
| fixed | | `CANARY_WINDOW_S=30`, `TECHNICIAN_MODE=off`, `DOCTOR_MODE=off`, `REVIEW_FIXTURE=` (empty: per-kind recordings), `PROBE_DIR=/data/learning/probes`, chaos off | | |

- **The probe:** `data/learning/probes/probe_us_frozen.json` in the worktree, a copy of `cloud/tests/fixtures/review/probe_us_frozen.json` (generated 17:11 UTC, valid 24 h). `cli reset` keeps it. Without it, `us_frozen` doesn't qualify for review.
- **Bring it up or change env** from the worktree. Env changes need the containers re-created, not `restart`:
  ```
  cd .claude/worktrees/learn-stack
  export COMPOSE_PROJECT_NAME=learn API_PORT=8001
  LEARNING_MODE=fixture REVIEW_PUBLISH=true REVIEW_MAX_SESSIONS_PER_HOUR=4 \
    docker compose --env-file ../../../.env -f compose.yaml -f compose.dev.yaml -f compose.learn.yaml \
    --profile sim up -d --no-deps api worker
  ```

## What NAIVE_MODE=live turns on here
- **The Skills publisher** (`review_publish.publisher` → `workorders.naive_client`): that's why it's needed.
- **Work-order cards** (`workorders._post`): live cards go only to devices registered `kind: real` (K13, `workorders.py:519`). On `learn` that's `arm-1`, which gets no incidents (no Pi points at :8001). Orders made in fake mode turn `needs_human` and aren't posted.
- **Not affected:** cluster review builds its own `HttpNaive` whenever `LEARNING_MODE=live` (`services/review.py`, `naive_for`); the Technician is off; the Doctor is off; the LLM and coder providers are `fake`.

## Timing (from events)
- **The learning tick runs every 30 s** (`worker.py:136`), so a review's outcome lands on the tick after the session ends: "staged · 30 s" is tick-bound, and the Naïve session itself is shorter (the spike's took 11.9 s for 2.6¢, `us_frozen_live.json`).
- **The gate runs every 2 s.** A fake arm checks in every 10 s, so an install shows up within about 6–10 s of its assignment.

| Step (fixture rehearsal, 9:56 AM) | t (s) | from the previous step |
|---|---|---|
| `review.session_opened` servo_read_error (+ dataset snapshot, export requested) | 0 | |
| `export.built`: 4 trajectories (2 pass, 2 fail; 2 unverified and 1 excluded not exported) | 6.1 | 6.1 |
| `review.accepted` + `candidate.created` harness v2 (`skills/servo-read-retry.md`) | 30.2 | 30.2 (one tick) |
| `candidate.replayed` (stub) + `candidate.canary_started` on sim-2 | 32.3 | 2.0 |
| `install.reported` sim-2 harness 2 | 38.6 | 6.3 |
| `review.session_opened` us_frozen | 60.5 | |
| `candidate.promoted`: canary healthy for 30 s, 2 ok check-ins; assigned to arm-1, sim-1, sim-3 | 62.6 | 30.3 after canary start |
| `install.reported` sim-1, sim-3 harness 2 (arm-1 stays queued: no Pi) | 68.6 | 6.1 |
| `review.staged` us_frozen, for Olir (touches `arm/safe_replay.py`), probe `us-frozen-port` | 90.7 | 30.2 (one tick) |
| `review.published` `romulus-healer-playbook` v2 (FakeNaive), 0 human clicks | 90.7 | 28.1 after promote (one tick) |

The page shows the skill as "lesson → every arm · 1 min 3 s · 0 clicks". Pods aren't touched (they keep their harness 1 assignment).

## Rehearsal (fixture)
1. The stack is seeded (`learning_demo.py status` lists the runs). Re-create api and worker with `LEARNING_MODE=fixture REVIEW_PUBLISH=true REVIEW_MAX_SESSIONS_PER_HOUR=4` (command above; `LEARN_NAIVE_MODE` stays `fake`, so the publish goes to the in-memory FakeNaive).
2. Watch events: `docker compose -p learn exec db psql -U romulus -c "select ts, kind, why from events where kind like 'review.%' or kind like 'candidate.%' order by ts"`.
3. Expect the table above in about 90 s. Then both kinds wait for a newer run (staged and accepted kinds cool down), so no more sessions open.
4. `/learning` on :8001: "Recent reviews" lists both, each with the **● recorded Naïve session** badge. `?case=review:<kind>:<salt>:1` switches between them, and the skill case shows the rollout chips (`arm-1 queued REAL`) and "mirrored to Naïve Skills · v2".
5. **↓ trajectories.jsonl** downloads a tar: `DATASET_CARD.md`, `manifest.json`, `trajectories.jsonl`.

## Live run
_Filled in._

## At the table
_Filled in._

## If things break
_Filled in._
