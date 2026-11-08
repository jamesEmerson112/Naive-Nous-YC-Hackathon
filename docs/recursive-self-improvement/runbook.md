# Fleet learning: demo runbook

_Sun Nov 8, after the live run at 10:04 AM PST. Needs #82 (cluster review), #86 (page) and #89 (5 s tick, live publisher) on `main`. The script comes from [`demo.md`](demo.md) §3 and §5, and its honesty rules are §7. Helper: `cloud/scripts/learning_demo.py` (`seed`, `status`, `reset-learn`; run it with `uv run python` from the learn worktree). Every number below was measured in the run named next to it._

## Measured in the live run (10:04 AM PST)
| What | Number | Source |
|---|---|---|
| Naïve sessions (agent `fleet-doctor`, sonnet) | 2 | `review.session_opened` events; Naïve `sessions_get` |
| `servo_read_error` session → skill accepted | 22 s, $0.030 (1,189 output tokens) | `review.accepted` `duration_s`, `cost_usd`; `ses_evmr26xxxxwf6dntt7r1597ehx` |
| `us_frozen` session → rule staged for Olir | 26 s, $0.021 (628 output tokens) | `review.staged`; `ses_h7qnensn63jpx4xtjkwr7377k5` |
| Both sessions | $0.051 | the two above |
| Canary on sim-2 (30 s window) | 32.5 s, 2 ok check-ins | `candidate.canary_started` → `candidate.promoted` |
| Session opened → promoted to every arm | 57 s (page: "lesson → every arm · 57 s · 3¢ · 0 clicks") | `review.session_opened` → `candidate.promoted` |
| → all three sim arms running harness v2 | 61 s; arm-1 (real) **queued**: no Pi on this stack | `install.reported` |
| → mirrored to Naïve Skills | 61 s: `romulus-healer-playbook` v1 (528 B, empty playbook) → v2 (2,070 B) | `review.published`; Naïve `skills_list_versions` |
| Card comment on the CEO board's "Romulus agent specs" card | 1 (`bcm_hcr1nyv1m3vn4j1prsk4q1avh3`), "0 human clicks" | `review.published` `comment`; Naïve `boards_list_comments` |
| Human clicks on the skill's candidate | 0 | `review_publish.human_clicks` (counted, not assumed) |
| The probe (`us-frozen-port`, rev `e58eaee`, 0.88 s) | freezes of 4 s or more: 0 → 120 of 120 caught; false alarms 0 → 0 on 410 healthy windows | `data/learning/probes/probe_us_frozen.json` |
| The probe's limits | a 3.5 s freeze is caught 4 of 30 times with the port (0 of 30 today); a freeze on out-of-range rows 0 of 30 | same file, `limits` |
| The fleet's own data | 9 heal runs → 4 labelled by a verifier (2 pass, 2 fail) → 1 regression test → 4 training rows; 2 unverified, 3 excluded | `/learning`; `export.built` |

**What the agent wrote:**
- **The skill:** "Servo read error: check the retry call site, not the config" (`skills/servo-read-retry-wiring.md`). It cites the failed sim-1 run (`read_retries` 3 → 10, and the app still crashed) and the passing sim-3 run, and says "Not tested on real hardware."
- **The rule:** a missed detection, with the probe `us-frozen-port`, at confidence 0.8. Because it touches `arm/safe_replay.py`, it's staged for Olir (D25).

## The stack
- **Where:** compose project `learn`, API on **:8001**, worktree `.claude/worktrees/learn-stack`.
  - Never the `tech` stack (:8000, Kent's 1 PM demo), and never Olir's laptop cloud.
  - Never `make reset`; on this stack, only `learning_demo.py reset-learn --yes`.
- **Local override** (untracked, in the worktree): `compose.learn.yaml` reads the main checkout's `.env` (`NAIVE_API_KEY`, `NAIVE_BOARD_ID`, `NAIVE_REVIEW_AGENT`) and takes the learn values from shell variables:

| Variable | Default (= the table state) | Fixture rehearsal | Live run |
|---|---|---|---|
| `LEARNING_MODE` | `data` (standing dataset only, no model) | `fixture` | `live` |
| `REVIEW_PUBLISH` | `false` | `true` (to the in-memory FakeNaive) | `true` |
| `REVIEW_INSIGHTS_CARD` | empty | empty | `crd_px969fbze2p5fhh0ywczxzc5ap` |
| `REVIEW_MAX_SESSIONS_PER_HOUR` | 2 | 4 | 2 |
| `LEARN_NAIVE_MODE` → `NAIVE_MODE` | `fake` | `fake` | **`fake`** (since #89, the live publisher follows `LEARNING_MODE=live`) |

  - Also fixed: `CANARY_WINDOW_S=30`, `TECHNICIAN_MODE=off`, `DOCTOR_MODE=off`, `REVIEW_FIXTURE=` (empty, so each kind replays its own recording), `PROBE_DIR=/data/learning/probes`, `REVIEW_BUDGET_USD=0.50`, chaos off.
  - Since `NAIVE_MODE` stays `fake`, the live run makes no work-order cards and no other Naïve writes. Its only writes are the two sessions, the skill and the one comment.
- **The probe:** `data/learning/probes/probe_us_frozen.json` in the worktree, a copy of `cloud/tests/fixtures/review/probe_us_frozen.json`.
  - It was generated at 9:11 AM PST, and the review rejects it 24 h later.
  - `cli reset` keeps it (it empties only the blob and export dirs).
  - Without it, `us_frozen` doesn't qualify for review: one failing device, and nothing else handles it.
- **Env changes need the containers re-created.** Run this from the worktree (`restart` keeps the old values):
  ```
  cd .claude/worktrees/learn-stack
  export COMPOSE_PROJECT_NAME=learn API_PORT=8001
  docker compose --env-file ../../../.env -f compose.yaml -f compose.dev.yaml -f compose.learn.yaml \
    --profile sim up -d --no-deps api worker          # prefix the variables above, e.g. LEARNING_MODE=live
  ```
- **Watch events:** `docker exec -i learn-db-1 psql -U romulus -d romulus -c "select ts, kind, why from events where kind like 'review.%' or kind like 'candidate.%' or kind like 'install.%' order by ts"`.

## The live run, step by step (10:04 AM PST)
1. Re-create api and worker with `LEARNING_MODE=data` first, so a half-seeded database can't start a review.
2. `uv run python cloud/scripts/learning_demo.py reset-learn --yes`, then `… seed` (7 s; it prints the verdict table). Check that the probe file is still there, sim-1..3 run harness 1, and `/v1/candidates` is `[]`.
3. Re-create api and worker with `LEARNING_MODE=live REVIEW_PUBLISH=true REVIEW_INSIGHTS_CARD=crd_px969fbze2p5fhh0ywczxzc5ap`.
4. The run, from events (the learning tick runs every 5 s, the gate every 2 s, and the fake arms check in every 10 s):

| t (s) | Event |
|---|---|
| 0 | `review.session_opened` servo_read_error (live session, about 8 s after the worker started) |
| 22.1 | `review.accepted` + `candidate.created` harness v2 = v1 + `skills/servo-read-retry-wiring.md` |
| 24.8 | `candidate.replayed` ("replay: stub", since a skill is prose) + `candidate.canary_started` on sim-2 |
| 28.8 | `review.session_opened` us_frozen |
| 31.1 | `install.reported` sim-2 harness 2 |
| 54.7 | `review.staged` us_frozen for Olir (touches `arm/safe_replay.py`), probe `us-frozen-port` |
| 57.3 | `candidate.promoted`: "canary healthy for 30 s: installed 2, 2 ok check-ins"; assigned to arm-1, sim-1, sim-3 |
| 61.2 | `install.reported` sim-1, sim-3 harness 2 (arm-1 queued) |
| 61.3 | `review.published`: `romulus-healer-playbook` v2, and the card comment |

5. **Verify in Naïve, read-only** (MCP `tools_execute`, `writes: false`):
   - `sessions_get` on both session ids: `stop_reason: end_turn`, `structured_output` present;
   - `skills_list_versions` on `skl_17dfd8vymeby9g1xem8k02vfzj`: v1 and v2;
   - `boards_list_comments` on board `agt_j2jy5qp878yhfhqwpxd200ay12`, card `crd_px969fbze2p5fhh0ywczxzc5ap`: one comment.
6. Re-create api and worker with no variables (`LEARNING_MODE=data`). No more sessions open, and the page keeps showing the live run.

**Rehearsal (fixture), for comparison** (9:56 AM, 30 s tick before #89): the same flow took 63 s to promote and 91 s to publish, with no Naïve call. The page shows **● recorded Naïve session** on both cases. Command: step 3 with `LEARNING_MODE=fixture REVIEW_PUBLISH=true REVIEW_MAX_SESSIONS_PER_HOUR=4`.

## At the 11:00 table (about 40 s; the stack is left in `data` mode)
Open `http://localhost:8001/learning`. "Recent reviews" switches cases (`?case=review:us_frozen:47bbaa:1`, `?case=review:servo_read_error:47bbaa:1`).

1. **The problem:** "A frozen distance sensor keeps saying 'all clear'. Our arm's start gate believes any 'ok' reading (`arm/safe_replay.py:66-75`). Our arm simulator's watchdog catches it; our pods don't."
2. **The Naïve agent** (the `us_frozen` case; Naïve Studio → agent `fleet-doctor` → session `ses_h7qnensn63jpx4xtjkwr7377k5`): "The case file went in, and a typed proposal came out: missed detection, port the watchdog's check. One Naïve agent session: 26 seconds and 2 cents."
3. **The probe:** "We measured the proposal: today our pods catch 0 of 120 freezes; with the port, 120 of 120, with no new false alarms on 410 healthy windows. It still misses a 3.5-second freeze most of the time, 4 of 30, and the page says so."
4. **The routing:** "It touches the arm's start gate, so it doesn't ship itself. It's staged for Olir, our firmware engineer, with these numbers. Data and non-safety changes ship on the gate, safety code waits for its engineer, money waits for a person."
5. **The skill that did ship** (the `servo_read_error` case): "Same agent, a failed heal this time. One sim arm's healer raised a config value nothing reads; another restored the retry. The agent wrote the lesson down as a skill. The gate tried it on sim-2 for 30 seconds, then shipped it: 57 seconds from the session to every arm, 3 cents, 0 clicks. It's mirrored to Naïve Skills as `romulus-healer-playbook` v2, and the agent commented on our CEO board."
6. **The data:** "Every run is labelled by what checked it: 9 runs, 4 labelled, 4 training rows. Here's the Hermes trajectories file" (**↓ trajectories.jsonl**, a tar with `DATASET_CARD.md`, `manifest.json` and `trajectories.jsonl`).

**The skill beat for video** (demo.md §5, about 17 s), with the measured overlay:
> Our Naïve agent makes sure a fault only happens once. In sim, one robot's healer couldn't fix a flaky servo read; another could. The agent wrote the lesson down, a sim arm tried it first, and now it's shipping to every arm. Nobody clicked.

Overlay: `lesson → every arm · 57 s · 3¢ · 0 clicks`. Shots: `/learning` head; Naïve Studio Skills `romulus-healer-playbook` v1 → v2 ("mirrored after gate promotion"); the canary chip, then the rollout chips; the overlay.

**The stage sentence** (demo.md §2, beat 5) holds as written: "This morning a Naïve agent found a sensor fault our pods miss, measured the fix, and handed it to its engineer."

## Honesty captions (demo.md §7)
- **Who measured what:** the probe measured the 120 of 120, not the agent ("the probe reports"). The agent proposed the port; we wrote the probe; the watchdog had the check.
- **The fleet:** "1 real arm · 5 simulated" (3 arms, 2 pods), SIM badges on. arm-1 shows **queued**, never installed.
- **The frozen fault is simulated**, and so is the servo fault (seeded runs). Never cut real-arm footage into this.
- **Staged isn't shipped:** the frozen port waits for Olir.
- **"Training data", never "trained".** The healer's model isn't trained on it yet ("NEXT, not built").
- **"replay: stub"** for the skill stays visible below the fold; the canary is the evidence that it does no harm, not that it helps.
- **The rehearsal shows "recorded"**; this morning's live run doesn't.
- **The 57 s includes the 30 s canary**, which runs only 30 s on this stack (120 s by default).

## If things break
| If… | Then… |
|---|---|
| The page shows nothing or wrong numbers | `uv run python cloud/scripts/learning_demo.py status`; check `docker ps` for the four `learn-*` containers. Don't re-run live at the table |
| A live session is rejected or a candidate rolls back | Stop; don't retry blind (each retry spends money and writes to Naïve). A rejected kind cools down for 15 min (or until a newer run); a rolled-back kind pauses until a person clears it (D31) |
| A fresh run is needed | `reset-learn --yes`, `seed`, then the **fixture** rehearsal (about 90 s; the page labels it "recorded"). A second live run makes new sessions, a v3 and a second comment, so ask Kent first |
| Naïve is busy (429, "at capacity") | The review retries on the next tick, and `fleet-doctor` is on sonnet (opus queued indefinitely this morning). At the table, use this morning's run already on the page |
| The probe is older than 24 h | Re-run `python3 cloud/scripts/lesson_probe.py` and copy the new scorecard into `data/learning/probes/` |
| Spend | Capped at $0.50 per session (`REVIEW_BUDGET_USD`) and 2 sessions an hour, within the Doctor's daily $5 cap |

## Known issue (cosmetic)
The card comment runs the lesson title into "shipped": "Fleet learning: Servo read error: check the retry call site, not the config shipped to every so101 arm…" (`services/review_publish.py:197`). Quoting the title would fix it. It's already posted, so leave it for this run.
