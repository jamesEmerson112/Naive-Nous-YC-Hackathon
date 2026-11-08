# Fleet learning: build plan (v2, Sun Nov 8, 9:00 → 10:00 AM freeze, then after 1 PM)

_**Superseded by what was built** (see the README's *What's built* and [`runbook.md`](runbook.md)): everything through T0 plus the T1 Skills mirror landed before 10:20 AM. Fact 2 below was wrong: Kent's `tech` stack mounts from `.claude/worktrees/tech-demo`, not the main checkout, so `cloud/src` merges to main were safe._

_Times are PST. Hacking stops at 11:00 (`docs/hackathons.md:39`); the team freezes code at 10:00 to rehearse. v2 follows the execution review ([`review.md`](review.md)). The Naïve Technician was rehearsed at 8:44 and is merged (#65–#72), so Kent is free from about 9:00._

## 1. Facts that shape this plan
1. **The full T0 takes 70–90 minutes, not 40** (execution review). The time goes to F15's canary under the fake clock, copying `_write_skills`, the run loader, and the worker step lists asserted in `tests/unit/test_worker_loop.py:72,83`. So before 10:00, build only what doesn't need the gate. The hero is **staged** (safety, D25), so it never needs a candidate.
2. **No merge into main before 1 PM for anything under `cloud/src`.** Kent's `tech` API runs `--reload` from the main checkout, and it's the 1 PM Technician demo. Scripts, fixtures and docs don't hot-reload, so they can go to main.
3. **The worktree needs what's local:**
   - branch from `origin/main`, since PRs #68–#73 are merged there;
   - pass `--env-file /Users/kentgang/git2/Naive-Nous-YC-Hackathon/.env`, because `.env` is gitignored;
   - the probe prototype is at `/private/tmp/claude-501/-Users-kentgang-git2-Naive-Nous-YC-Hackathon/dcade778-e9a6-4843-8118-a052a5b21c6d/scratchpad/frozen_check2.py`.
4. **Its own stack:** `COMPOSE_PROJECT_NAME=learn API_PORT=8001`. Nothing collides with `tech` (no fixed container names, an unpublished db port, `pgdata` per project). Never point anything at :8000 or at Olir's laptop.
5. **Two owners to tell now:**
   - **Olir** has a P0 line to write the frozen-ultrasonic rule. Offer it to him as the staged proposal with the probe's numbers, or ask him to hold it until after 1 PM.
   - **James** owns Level 3. Fleet learning makes his training input and trains nothing.

## 2. Before 10:00: three lanes in parallel
| Lane | Who | What | Min | Done means |
|---|---|---|---|---|
| **0 · decisions** | Kent, 9:00–9:05 | README decisions 1–5 (default: all as proposed); message Olir and James; create the Naïve agent `fleet-doctor` through the MCP (`pi`, `handoffs:false`, all tools denied, Sonnet 5.5, $0.50 per task), then read it back with `agents_get` | 5 | agent id in `.env` as `NAIVE_REVIEW_AGENT` |
| **A · evidence** (scripts only, safe to push to main) | session A | **A1** `cloud/scripts/lesson_probe.py` (from the prototype; [`design.md`](design.md) §6), committing the seeded 120-window set. **A2** `cloud/scripts/review_spike.py`: a cut-down copy of `technician_spike.py`, which is 656 lines, mostly shop and email code; keep only `create_session`/`get_session`/`session_events`. It sends a case file built from the probe JSON, a `gen_pod` frozen window, the `level1.py:30-44` docstring, the `detect.py` rule names and the `safe_replay.py:66-75` excerpt. Run it live once; save the fixture to `cloud/tests/fixtures/review/us_frozen_live.json` | 35 | **G1, by 9:45:** the proposal validates; `gap = missed_detection`; it cites the probe; ≤ 60 s and ≤ $0.50; the session is visible in the Studio |
| **B · data** (worktree `learn/fleet-learning`, `learn` stack; not merged before 1 PM) | session B | **B1** `domain/runs.py` (the verdict table) + unit tests. **B2** `services/runs.py` + `actor_class` on the three dataset writers + the standing `fleet-runs` dataset in a `learning.tick` step (with `REVIEW_MODE` off, the step does only the dataset) + the `trajectories` format (`trajectories.jsonl`, `DATASET_CARD.md`). **B3** the start of F15, modelled on `test_f3_lock_in.py`: export rows match the verdicts | 45 | F15's data assertions pass; a real export file on `learn` from `agent_runs.build()` runs |
| **C · page** (same worktree, after B2) | session B, or a third session | `services/learning.py` `view()`, `templates/learning.html` above the fold plus the data strip ([`demo.md`](demo.md) §4), one route, a nav link. The proposal card reads the A2 fixture through `domain/review.py`'s checks: **staged** path only, no candidate | 20 | `/learning` on :8001 shows the hero as "staged for Olir" and a real export link |

### Timeline
| Time | What | Gate |
|---|---|---|
| 9:00–9:05 | Lane 0 | agent created |
| 9:05–9:20 | A1 probe ‖ B1 verdict table | **the probe prints its numbers by 9:20** |
| 9:20–9:45 | A2 spike ‖ B2 dataset + export | **G1 by 9:45** |
| 9:45–10:00 | C page ‖ B3 test. Commit A to main (scripts + fixture + docs) | **freeze** |
| 10:00–10:15 | Kent rehearses the table walk-through and the beat-5 sentence ([`demo.md`](demo.md) §2–3) | |

- **After the freeze:** the `learn` stack is separate from the demo stacks, so B and C may keep going until 10:45 at Kent's call. Nothing after 11:00.
- **Cut order** (first to go): C's below-the-fold → B3 → C entirely (the table uses the Studio tab, the probe output and the loop diagram) → B entirely.
- **Never cut:** the probe's real numbers; the honesty badges (SIM, recorded, staged).

## 3. After 1 PM: the full T0, then T1 and T2
| Tier | What | Est. |
|---|---|---|
| **T0** | Merge B and C. `services/review.py` in fixture mode: the trigger (transfer and generalize), case file, checks, routing; staged, accepted and rejected outcomes; the **skill lane** through the gate (harness candidate, waiting while any harness candidate is live, built on the current default with `parent_version`); F15 complete. Pick a **non-safety failed-heal hero** for the full beat ([`demo.md`](demo.md) §5) | 2–3 h |
| **T1** | `REVIEW_MODE=live` in the worker; `sft.jsonl`/`kto.jsonl`; mirror the playbook to Naïve Skills after promotion (`review.published`); the repeat rate in the digest and on the page | 1–2 h |
| **T2** | **The rule lane** with Olir: an optional `distance_spread_mm` signal in the contract, so a frozen check fits the rules format and gets the gate's real replay. The coder drafts the staged `detect.py`/`safe_replay.py` port and stops at B7. A comment on the CEO board's INSIGHTS card on promotion. Naïve Files for the export (`files:write` unverified). The Technician and the Hermes `manager` profile read the playbook only once their reading is gated. James's L3b LoRA trains on `sft`/`kto`, and the model goes through the same gate as a new artifact type | — |

**Hermes entry (due Wed, 2 PM PST):** a plugin on `on_session_end` that sends each Hermes trajectory to Romulus, where it becomes a labelled run. Hermes runs then feed the same dataset, in Hermes's own format, with `completed` set by our verifier rather than by the agent *(inferred: fits "more than a prompt or a skill", `docs/hackathons.md:96`)*.

## 4. Briefing the build sessions (gotchas from the execution review)
1. **Read [`design.md`](design.md) §0 first:** only a verifier labels; route by gap type; this is a Fleet Doctor mode under D25.
2. **Real-shape agent runs only:** `edge_bridge.agent_runs.build()` or a `fake_healer` run folder, never hand-written report dicts (`cloud/CLAUDE.md`, "Real device data").
3. **Don't seed `--scenario all`.** Its batch candidates take `sim-2` and make a review candidate go `stale` (`services/gate.py:190-201, 686-700`).
4. **Register sim pods as `sensor_pod` before their first check-in.** Otherwise they auto-register as `so101` (`services/devices.py:213`).
5. **`Candidate` has no `data` column.** Payloads go in the `candidate.created` event. `find_session` matches `metadata.romulus_case`.
6. **Reuse the schema checker** in `domain/technician.py:153-210`.
7. **The canary for rehearsal:** `CANARY_WINDOW_S=30` on `learn`, fake arms with `CHAOS=off`, and `compose.yaml` + `compose.dev.yaml` with `--profile sim` (`make sim` alone drops the dev mounts, `Makefile:32-34`).
8. **Never `make reset`** any stack except `learn`.
9. **Commit small; no AI co-author lines;** `git pull --rebase origin main` before pushing (CLAUDE.md working rules).
