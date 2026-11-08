# Fleet learning: build plan (draft v1, Sun Nov 8, 8:45 AM → 10:00 AM freeze)

_Times are PST. Hacking stops at 11:00 (`docs/hackathons.md:39`); the team freezes code at 10:00 to rehearse. Kent is on the Naïve Technician until 10:00 (G1 8:45, go or break-glass 9:00, clip 9:15, arm slot 9:30, `docs/naive-technician/build-plan.md`), so this is **one Claude session** plus **two OKs from Kent, about 5 minutes in total**._

## 1. Facts that shape this plan
1. **The full T0 doesn't fit.**
   - [`design.md`](design.md) T0 is about 75 minutes of backend plus about 20 for the page. From 8:55, that ends after 10:30.
   - So the plan has a **guaranteed minimum beat** (§2), and a T0 cut into two halves with a go or no-go call at 9:35.
2. **PR #68 (`tech/backend`, open)** touches `settings.py`, `worker.py`, `adapters/naive.py` and the nav. Branch from main, keep the new code in new files where possible, and expect one small rebase.
3. **The stack is separate:** `COMPOSE_PROJECT_NAME=learn API_PORT=8001 make dev && make seed`. It never touches `tech` on :8000 or Olir's laptop. The full test suite takes about 6.5 minutes, so the inner loop runs only the new unit files and F15.
4. **Two people own pieces of this story.**
   - **Olir** has a P0 line to write the frozen-ultrasonic rule by hand (`features.txt`). Ask him to hold it until after 1 PM, or to land it as the curator's staged patch.
   - **James** has the Level 3 line (fine-tune a small model, `docs/healer-data-levels.md`). Fleet learning makes his training input; it doesn't train anything.

## 2. The guaranteed minimum beat (M), done by 9:30 whatever else happens
| Step | Who | What | Minutes |
|---|---|---|---|
| M1 | session | `cloud/scripts/lesson_probe.py` (standard library, [`design.md`](design.md) §6) writes `data/learning/probes/us_frozen_port.json`. Commit the script and the seeded 120-window set; the JSON itself is gitignored | 15 |
| M2 | Kent, OK 1 | Through the Naïve MCP: create the agent `curator` (`pi`, `handoffs:false`, deny-all tools, Sonnet 5.5, $0.50 per task), then read it back with `agents_get` | 3 |
| M3 | session | `cloud/scripts/curator_spike.py`, a copy of `technician_spike.py`, with the `FleetLesson` schema and a case file built from the hero cluster. Run it live once; save the fixture to `cloud/tests/fixtures/curator/us_frozen_live.json` | 15 |
| M4 | Kent, OK 2 | Publish `romulus-healer-playbook` v1 (today's harness skills) to Naïve Skills. **v2 only after a gate promotes it** (T0a); without T0a, show the proposed skill text, not a published version | 2 |

**What M alone shows:** real probe numbers, a real Naïve curator session in the Studio, and its typed lesson. That's the beat-4 fallback in [`demo.md`](demo.md) §7: one slide plus the Studio tab, and never a mock page.

## 3. T0, split so that half of it still lands
| Half | Files | Minutes | Done means |
|---|---|---|---|
| **T0a · the lesson reaches the fleet** (visible) | `domain/episodes.py` (verdict table only) + unit · `domain/lessons.py` (checks) + unit · `services/curator.py` (fixture mode: cluster → case file → checks → harness candidate) + one `worker.py` Step + `settings.py` · `tests/flows/test_f15_learning.py` (accept + reject) | 40 | F15 green: a fixture lesson becomes a harness candidate that the gate canaries and promotes to every `so101` device |
| **T0b · the fleet keeps its own data** | `services/datasets.py`: `actor_class` + the standing `fleet-episodes` dataset + the `trajectories` format (`trajectories.jsonl` + `DATASET_CARD.md`; **`sft`/`kto` are cut to T1**) · F15 gains the export assertions | 20 | the export file has `completed` true and false rows matching the verdicts |
| **Page** | `templates/learning.html`, `services/learning.py` (`view()`), one route in `api/web.py`, a nav link | 20 | [`demo.md`](demo.md) §3 above the fold, fed by the `learn` stack |

## 4. Timeline
| Time | Who | What | Gate |
|---|---|---|---|
| **8:45–8:55** | Kent | Read [`README.md`](README.md); answer its five decisions, or let the defaults stand. Message Olir and James (§1.4). Start a Claude session in a worktree `learn/fleet-learning` off main, briefed with `design.md` and this file | go or no-go |
| 8:55–9:10 | session | M1, the probe. Then start the stack (`learn`, :8001) and the seed script ([`demo.md`](demo.md) §5) | **the probe prints the numbers by 9:10** |
| 9:10–9:15 | Kent | M2: create the agent (OK 1). That's the slot between the Technician's go call and its clip | |
| 9:10–9:30 | session | M3: the spike, live, then the fixture. In parallel, start T0a with the domain files | **M done by 9:30** |
| 9:30–9:35 | session | **Go or no-go on T0a:** go if the domain units are green and the curator service compiles; otherwise stop at M and write the slide | |
| 9:35–9:50 | session | Finish T0a (the curator service and F15), then run it on the `learn` stack: a lesson goes through canary (120 s) to promote | F15 green |
| 9:50–10:00 | session | The page first; T0b only if the page is done. Kent's OK 2 publishes skill v2 once the `learn` gate has promoted it | **freeze** |
| 10:00–10:15 | Kent | Rehearse beat 4 on the finished page (or M's slide). Record the backup clip | |

**After the freeze (Kent's call):** the `learn` stack is separate from the demo stacks, so this session can keep going on T0b until 10:45 without touching anything being rehearsed. Nothing goes in after 11:00.

## 5. Cut order (first to go)
1. `sft`/`kto` files and the cron deployment (already T1/T2)
2. T0b (the data strip shows only the regression-case count the cloud already keeps)
3. The page's lower half (episode list, feed, staged patch)
4. T0a's live mode (fixture only)
5. The page itself (fall back to M's slide plus the Studio tab)

**Never cut:** the probe's real numbers; the honesty badges (SIM, recorded, "replay: stub"); the rule that a skill version is published only after a gate promotes it.

## 6. Briefing the build session (gotchas)
1. **Read [`design.md`](design.md) §0 first.** The curator doesn't repeat the batch, and only a verifier labels.
2. **Real-shape agent runs only:** use `edge_bridge.agent_runs.build()` or a `fake_healer` run folder, never hand-written report dicts (`cloud/CLAUDE.md`, "Real device data").
3. **`from_overlays=[]` on a harness candidate:** F15 must assert that the gate accepts it end to end, since that's *(inferred)* from `services/gate.py:132, 455, 480, 513, 788`.
4. **The probe runs on the host,** not in the container (`compose.dev.yaml:7-15`). The worker only reads its JSON.
5. **Never `make reset`** any stack but `learn`, and never point anything at :8000.
6. **Commit small, no AI co-author lines, `git pull --rebase origin main` before pushing** (CLAUDE.md working rules).

## 7. After 1 PM (not today)
- **T1:** `sft`/`kto`; live curator mode in the worker; `lesson.published` automatic on promotion.
- **T2:**
  - the Technician pins the playbook, and the Hermes `manager` profile pulls it (Hermes uses the same SKILL.md standard, H:12494), so the skill gets real readers;
  - the coder drafts the `detect.py`/`safe_replay.py` port and stops at B7;
  - the gate reads the scorecard instead of the stub;
  - a board card on the CEO's INSIGHTS spec;
  - Naïve Files for the export (`files:write` unverified).
- **Hermes entry (due Wed):** a plugin on `on_session_end` that ships a Hermes trajectory to Romulus, where the gate labels it. That turns Hermes runs into rows of the same dataset *(inferred: a fit for "more than a prompt or a skill", `docs/hackathons.md:96`)*.
- **Weights:** James's L3b LoRA trains on `sft.jsonl`/`kto.jsonl`, and the model goes through the same gate as a new artifact type.
