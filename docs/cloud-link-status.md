# Cloud link: what's connected, how far the cloud is, what's left

_Snapshot: Sun Nov 8, 2:38 AM PST. Written from the bench Pi (`full-test-2` at `5b28194`), `main` as last
fetched (`379acbb`, team status of about 2 AM), and the Romulus cloud running on Olir's laptop, read over
the Ethernet cable. Nothing in the cloud was changed to write this._

**In one paragraph:** the Pi now reports the pod healer's incidents to the Romulus cloud on the laptop,
and the first two real incidents arrived. The cloud's own pipeline (timeline, healing pipeline, work
orders, datasets) is built, but it has only been exercised with fake arms and a fake Naïve. **The Naïve
part (required by the hackathon) is written but switched off, and has never posted to the real board.**
Nothing the cloud decides comes back to the Pi.

---

## 1. What's connected now

```
 Pi 5  192.168.50.2                                          laptop  192.168.50.1 (Docker)
 STM32 pod ─► podd ─► firmware/healer/var/ ─► edge-bridge.service ── Ethernet ──► Romulus api :8000 ─► db, worker
                                              (systemd, starts at boot)            dashboard http://localhost:8000/
 firmware/harness/agent.py ───────────────────────────── Ethernet ──► server/serve.py :11434 ─► Ollama qwen3.5:4b
```

| Piece | State (checked 2:38 AM) | Source |
|---|---|---|
| Ethernet link | Pi `192.168.50.2`, laptop `192.168.50.1`, ~0.4 ms ping | `ip -br addr`, `ping` on the Pi |
| Cloud API | `/healthz` answers `{"status":"ok","db":"ok","contract_version":"0.1"}` | `curl http://192.168.50.1:8000/healthz` |
| `pod-1` in the cloud | registered as `sensor_pod`, health `ok`, checks in every 10 s, build `v1.0@BAADB704` | `GET /v1/devices/pod-1` |
| Edge bridge on the Pi | `edge-bridge.service`, active since 2:06 AM, installed by `server/pi/install_bridge.sh` | `systemctl status edge-bridge` |
| Incidents in the cloud | 2, both `us_mismatch`, resolved by `fw_rollback`, cause `software` (the 2:12 and 2:20 heal-test runs) | `GET /v1/incidents?device=pod-1` |
| Work orders | 0 | `GET /v1/work-orders` |
| Model relay | `qwen3.5:4b` served on `192.168.50.1:11434` | `curl …:11434/api/tags` |

How it was set up and how to run the laptop side: [`server/README.md`](../server/README.md), section
"The cloud link". What the bridge sends and when: [`tools/edge_bridge/README.md`](../tools/edge_bridge/README.md).

**When an incident appears.** Only once the fault is over: all podd incidents resolved and 15 s with no
heal-tool call; or the healer asked a person and 60 s passed quietly; or 15 min still open
([`tools/edge_bridge/README.md`](../tools/edge_bridge/README.md), "When a fault is reported"). In the
heal test that's about 15 s after the agent's last tool call.

**Only faults after the bridge's first start (2:06 AM) are reported.** It started without backfill, so
the 16 earlier faults from tonight aren't in the cloud. A one-off dry run showed all 16 parse and pass
the contract models *(dry run on the Pi, outbox in a scratch folder, nothing sent)*.

### Things to remember on the Pi
- After a `git pull`: `sudo systemctl restart edge-bridge` (the service runs from this checkout).
- **podd is not a service.** It was started by hand. After a reboot the bridge comes back on its own
  but podd doesn't; restart it from `firmware/` with `healer/bin/podd &`. Until then `pod-1` checks in
  as `failing`.
- If the laptop reboots with the cable out: rerun `./server/cloud_up.sh` on the laptop
  ([`server/README.md`](../server/README.md), Notes).
- Incidents queue on disk while the cloud is unreachable (`data/edge_bridge/pod-1/outbox`) and go out
  when it answers again; check-ins are not queued ([`server/README.md`](../server/README.md)).

---

## 2. How far the cloud is (Kent's Romulus, `cloud/`)

Status per the build plan ([`docs/cloud-platform-planning/cloud-platform-build-plan.md`](cloud-platform-planning/cloud-platform-build-plan.md) §3)
and Kent's block in `CLAUDE.md` on `main`.

| Piece | State |
|---|---|
| Device API: check-ins, incidents, recordings, installs | Built. **Working with the real pod** (section 1) |
| Dashboard: timeline, incident pages, fleet, kill switch, data page | Built and working |
| Healing pipeline for arms: merge the overlays devices applied, replay them, canary on a simulated arm, promote or roll back on its own (M3) | Built and tested **with fake arms only** (`sim-1…3`) |
| Coding agent for code patches (M8) | Built; candidates stop at `awaiting_canary` because no canary runs real device code (blocker B7) |
| Work orders → Naïve board cards (M5) | Built; tested **only against the fake Naïve** (section 3) |
| Datasets, labels, exports (M6); overnight chaos run and digest (M7) | Built, fake fleet. The overnight run is Kent's, not on this laptop (`/v1/digest`: `overnight_started_at` null) |
| Fleet Doctor (#52) | Built; every proposal is discarded until Ishaan sends the K17 safety and visor path list (`SAFETY_PATHS_CONFIRMED` false, team status) |

### What the cloud does on its own when an incident arrives
- Records it, builds its story page, labels the evidence.
- **Pod incident still unresolved with `suspected_cause: hardware`** → a work order → a Naïve card (fake
  mode: logged locally) ([`cloud/src/romulus_cloud/services/workorders.py`](../cloud/src/romulus_cloud/services/workorders.py) docstring;
  [`tools/edge_bridge/README.md`](../tools/edge_bridge/README.md), "What reaches the cloud"). All of tonight's
  pod incidents are `software` and resolved, so none triggered.
- Pod firmware fixes are **evidence only** (decision D29): never overlays, so the healing pipeline never
  acts on pod fixes ([`tools/edge_bridge/README.md`](../tools/edge_bridge/README.md)).
- Promote and rollback only apply to arms, and only fake arms report in.

### What comes back to devices
- The only channel is the **check-in response**: `assigned` versions, `drop_overlay`, `frozen` (the kill
  switch) and an optional Fleet Doctor `proposal`. Devices pull; the cloud never pushes
  ([`packages/contracts/src/romulus_contracts/device.py`](../packages/contracts/src/romulus_contracts/device.py), `CheckInResponse`).
- **The bridge ignores the response.** It installs nothing and only warns once if it's assigned arm
  firmware ([`tools/edge_bridge/src/edge_bridge/bridge.py`](../tools/edge_bridge/src/edge_bridge/bridge.py)).
  So the kill switch and proposals never reach the pod healer.
- The real arm doesn't check in: `arm-1` exists (created by `make seed`) but nothing reports for it.
  `sim-1…3` only show activity while the fake fleet runs.

---

## 3. Naïve (required: "All teams must use the Naïve SDK")

The rule and judging line ("how much of the company runs without you") are in [`docs/hackathons.md`](hackathons.md).

- **Naïve's SDK is TypeScript only.** The cloud is Python, so it calls Naïve's REST API with `httpx`
  ([`cloud-platform-naive-integration.md`](cloud-platform-planning/cloud-platform-naive-integration.md) §2).
  Whether REST counts as "using the SDK" is worth asking the organizers *(inferred)*.
- The live org exists: **Romulus**, CEO agent, company board `brd_v2zcnyzc559hv8awzc4hqvgcsm`
  (integration doc §5.1).

| Touchpoint | Code | State |
|---|---|---|
| LLM through Naïve's proxy (`openai/gpt-oss-120b`) | [`cloud/src/romulus_cloud/adapters/llm.py`](../cloud/src/romulus_cloud/adapters/llm.py) | **Proven live** in the M0 spike and the coding agent's dry run (integration doc §5.1, build plan B4). Off by default (`LLM_MODE=fake`) |
| Work-order cards on the board (post, poll, read `RESULT:`/`STOPPED:`) | [`cloud/src/romulus_cloud/adapters/naive.py`](../cloud/src/romulus_cloud/adapters/naive.py), [`services/workorders.py`](../cloud/src/romulus_cloud/services/workorders.py) | Written, with caps, retries, kill switch. **No card ever posted to the real board** ("pending Kent's go-ahead", build plan M0 and M5). Off by default (`NAIVE_MODE=fake`) |
| `fleet-ops` agent that picks the cards up | planned `naive/naive.config.json` + `naive up` (integration doc §3.6) | **Doesn't exist.** The org has only `CEO` and `hello-world` (§5.1); `naive/` isn't in the repo; fleet-ops is listed as unclaimed (team status) |

The only places Naïve runs live today are James's prototypes, e.g. `prototypes/naive-console` (on `main`),
which drives Naïve's `pi` agent through the `naive` CLI to compare it with Hermes. It isn't wired to the cloud.

---

## 4. The UIs

| UI | What it is |
|---|---|
| Romulus cloud: `http://localhost:8000/` on the laptop | The only UI for the cloud. `/` timeline, fleet, kill switch; `/incidents/<id>` one incident's story with evidence downloads; `/data` recordings, labels, datasets, exports |
| Naïve's own web app | Where work-order cards and the `fleet-ops` agent would show up once live *(inferred: not checked)*. This is where judges would see Naïve agents work |
| `prototypes/naive-console` (James) | Local page, pi vs Hermes on one conversation, plus fault-lab results. A tool for choosing the arm's agent loop, not product |
| Heal-test console, `firmware/harness/view.py` | Terminal on the Pi: the agent live, and replays of past runs |
| Hub ("Arm Company Stack Map", `hub/`) | Planning map of the stack and lanes, not a live view |

James's session plans a "product screen" (team status); nothing is built yet.

### What an incident page shows, and what it doesn't
- Shows: the summary, time, suspected cause, how it was resolved; the agent step ("The agent on pod-1
  did: fw_rollback … It resolved it with fw_rollback; suspected cause: software"), with the agent's
  notes as "Why" when it wrote any; the log window and agent trace as JSONL downloads.
- Doesn't show: the model's thinking or conversation (stays in `firmware/harness/runs/<run>/`), read-only
  tool calls (only in the trace download), or anything about the arm.
- The "recording …" links in the bottom Evidence list open JSON metadata; the file itself is at
  `/v1/recordings/<id>/blob`.

---

## 5. To-dos

**Naïve (most important for the hackathon):**

| # | To-do | Who |
|---|---|---|
| 1 | Create the `fleet-ops` agent in the Naïve org (`naive/naive.config.json`, `naive up`; integration doc §3.6) | Unclaimed |
| 2 | Post one live card to the board, to prove the path end to end (integration doc §5 steps 3–5) | Kent's go-ahead |
| 3 | Decide live or fake work orders for the demo; live texts a teammate's phone | Team call, open |
| 4 | Go live: `NAIVE_MODE=live`, `NAIVE_API_KEY` with board scopes (James is owed one), `NAIVE_BOARD_ID`, `WORK_ORDER_PHONE` ([`cloud/README.md`](../cloud/README.md), "Going live") | Whoever runs the demo cloud |
| 5 | Optional, easy: `LLM_MODE=live`, `LLM_PROVIDER=naive_proxy`, `LLM_MODEL=openai/gpt-oss-120b` | Anyone |

**The pod's part of the demo:**

| # | To-do |
|---|---|
| 6 | A pod fault that produces a work order: it has to end unresolved with a hardware cause. The ultrasonic heal test never does, because the rollback resolves it. A wiring fault the healer hands to a person would *(inferred)* |
| 7 | Optional: a reply path to the Pi (nothing reads the check-in response, so the kill switch never reaches the pod) |
| 8 | Optional: the real arm checks in as `arm-1` (needs a small reporter like the bridge) |
| 9 | Optional: send tonight's 16 earlier faults to fill the timeline (rerun the bridge with backfill into the same cloud; ids are deterministic, so resends are harmless) |

**Kent's list** (from his block in `CLAUDE.md`): B5 sandbox egress (#46); the K17 path list from Ishaan
(gates the Fleet Doctor); B7, a canary that runs real device code; Ishaan's contract review (I0); the
overnight go/no-go.

---

## 6. Commands

On the Pi:

```bash
journalctl -u edge-bridge -f | grep --line-buffered -v checkin        # incidents being queued and sent
curl -s http://192.168.50.1:8000/v1/devices/pod-1                     # last_seen_at, health, build
curl -s 'http://192.168.50.1:8000/v1/incidents?device=pod-1' | python3 -m json.tool | head -40
ls data/edge_bridge/pod-1/outbox                                      # anything waiting for the cloud
tail -f firmware/healer/var/logs/telemetry.jsonl | grep --line-buffered -o '"mm":[0-9]*,"us":[0-9]*'   # sensor, before a heal test
python3 arm/heal_test.py                                              # the demo fault (not --sim: no podd incident)
```

On the laptop: `./server/cloud_up.sh`, then open `http://localhost:8000/`.

---

## 7. Fixed Nov 8, ~3:30 AM: the heal agent kept saying "wait, let me just roll back"

Not a cloud issue; parked here so it isn't lost. **Fixed:** the briefing (`firmware/healer/SYSTEM.md` "How to
work") and the arm rules (`ARM_RULES` in `firmware/harness/agent.py`) now say one thing: roll back to
known-good and check the arm first, then fix forward (`src_edit`, `fw_build`, `fw_flash`, now offered with
`--tools arm`) and check the arm again, rolling back if the build fails. Context is 65,536 tokens by default on
both sides, with old thinking dropped at 75%. Flash calls that never reach the chip, or re-flash what's
running, no longer spend the budget (the 2:52 watch run lost its last two flashes that way). What it was:

- The agent's prompt says two different things. The general briefing says find the root cause and
  "prefer a minimal forward fix … `src_commit`". The run rules `arm/heal_test.py` adds say "flash the
  known-good image (fw_rollback). In this run that is the fix … You don't have to explain the exact
  defect before you roll back" (`system` field of any run's `transcript.jsonl`).
- The run offers only read-only source tools (`src_read`, `src_search`, `src_log`, `src_diff`), no
  `fw_build` or source writing, so a forward fix is impossible anyway. The "wait / actually" is the
  4B model's thinking switching from the first instruction to the second.
- **It cost the 2:20 run** (`firmware/harness/runs/20261108-022022-arm08-ollama`): 22 turns reading source,
  rollback at turn 26, then the context hit 32,666 of 32,768 tokens (`stop: max_tokens`) before it ran
  `arm_run`. The 2:12 run rolled back at turn 3 and failed for a different reason: a real object sat
  ~192 mm in front of the sensor from 2:13:22 to 2:17:38 (pod telemetry).
- Options when picked up: make this run's prompt consistent (rollback only, source read-only, drop the
  forward-fix and `src_commit` lines); and/or raise `--num-ctx` on both the harness and `server/start.sh`.
