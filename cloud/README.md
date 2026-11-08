# Romulus cloud platform

The cloud half of the arm company's self-healing loop. Devices report incidents and the overlays their healer applied; the platform locks good overlays into new firmware and harness versions, replays and canaries them on a simulated arm, then promotes them to the fleet or rolls them back on its own. It also turns hardware faults into Naïve work-order cards and keeps every recording as a dataset.

Python 3.12, FastAPI, Postgres, one worker loop, all in Docker Compose. Every outside service (Naïve, the LLM, the coding agent) has a fake mode, and fake is the default.

**The fake fleet below is for local development and testing.** The work now is real integration with the Pi and the arm hardware: the device side checks in with the same contract (`packages/contracts`, APIs doc), and the build plan's I0 hand-off and M8 blockers track what's left. The STM32 sensor pod connects through [`tools/edge_bridge`](../tools/edge_bridge/README.md), which reads its healer's records on the Pi and reports them in the same contract (D29; tested against this app, not yet on the bench).

## Run it locally with fake arms

From the repo root (needs Docker and [uv](https://docs.astral.sh/uv/)):

```bash
uv sync --all-packages
make up && make seed      # db, api, worker on :8000 (API_PORT overrides); firmware v1, harness v1, arm-1, sim-1..3
uv run python -m fake_device --n 3 --url http://localhost:8000 --interval 3 --hold-after 20 --scenario all
```

The last command runs three fake arms (`sim-1`…`sim-3`; `sim-2` is the canary) through every scripted scenario: a rules fix, a code patch, a bad candidate, a late bad candidate and a park. The arms keep checking in until you press Ctrl-C. If you set `API_PORT`, use that port in `--url` and in the links below. Then open:

- <http://localhost:8000/>: the incident timeline, fleet and kill switch;
- an incident from the timeline (`/incidents/{id}`): its story from fault to fleet or rollback, with evidence links;
- <http://localhost:8000/data>: recordings, labels, snapshots and exports.

`make sim` is different: it runs the fake fleet inside Compose with the overnight chaos schedule in real time (a fault about every 10 minutes), not the scripted scenarios. `make down` stops everything.

## Tests

```bash
make test     # every test; each pytest session gets its own throwaway database in romulus-testdb (:55432)
make flows    # only the flow tests, cloud/tests/flows/: F1–F8, F10 and F11 (F9 is covered only inside F10)
make lint
```

## Overnight run

[`OVERNIGHT.md`](OVERNIGHT.md) is the runbook: preflight (`make overnight-check`), launch, the morning digest (`make digest`) and how to stop.

## Going live

Settings come from `.env` (copy [`.env.example`](../.env.example)); overnight values layer on top from `.env.overnight`.

| Variable | Fake (default) | Live |
|---|---|---|
| `NAIVE_MODE` | `fake`: work-order cards are logged locally | `live`: posts cards to the Naïve board. Also needs `NAIVE_API_KEY`, `NAIVE_BOARD_ID`, and `WORK_ORDER_PHONE` set to a teammate's phone; live mode refuses to post while it's the placeholder. Never live overnight (build plan K13) |
| `LLM_MODE`, `LLM_PROVIDER` | `fake`, `fake`: templated explanations | `live`, `naive_proxy` (or `runpod`), with `LLM_BASE_URL`, `LLM_API_KEY` and `LLM_MODEL=openai/gpt-oss-120b` (the model the M0 spike picked) |
| `CODER_PROVIDER` | `fake` | `local_agent`: the real coding agent (below). Never overnight |

## Real bench and coding agent

The real `device/` code (#31) goes through the coding-agent path (M8, F11). In order:

```bash
make code-register         # store device/ at its last commit as a code version (DEVICE_REV overrides) (#38)
make seed-bench            # arm-1 (real) + sim-1, the one canary, on that code; CODE_REV=… to pick one (#38)
make device-check          # device/'s own tests in the coder image, --network none (18 pass) (#38)
make device-verify         # held-out SPEC checks from bench/tasks.py into data/verify/device (#38)
make coder-image           # build the romulus-coder sandbox image (#19, #24)
make coder-firmware-check  # build a copy of firmware/ in the image, offline (B8, #24)
make worker-host           # run the worker on the host so it can `docker run` the coder (#28)
```

`make seed-bench` refuses (409) if another canary exists, e.g. after `make seed`. Rerun `make device-verify` after `device/` changes: a replay on code that isn't the generating commit or a descendant fails as "stale verify" (#38).

To turn the real coder on, set in `.env`: `CODER_PROVIDER=local_agent`; the LLM it uses, `LLM_BASE_URL`, `LLM_MODEL` (`openai/gpt-oss-120b`) and `LLM_API_KEY` (Naïve proxy, or James's RunPod endpoint when the proxy is at capacity); and `CODER_VERIFY_DIR=data/verify/device`. `CODER_TEST_CMD`, `CODER_MAX_DRAFTS_PER_HOUR` and the rest are explained in [`.env.example`](../.env.example). A code candidate stops at `awaiting_canary`: no canary runs real code yet (B7).

**Never use `make worker-host` overnight.** It refuses once the overnight run has started (unless `FORCE=1`), and `make up`/`dev`/`sim` refuse while it runs (#28). The overnight setting is `CODER_PROVIDER=fake`.

## Planning docs

All in [`docs/cloud-platform-planning/`](../docs/cloud-platform-planning/README.md): what's built and what's left in the [build plan](../docs/cloud-platform-planning/cloud-platform-build-plan.md) §3, flow status in the [flows tracker](../docs/cloud-platform-planning/cloud-platform-flows.md), payloads and routes in the [APIs doc](../docs/cloud-platform-planning/cloud-platform-apis.md), code layout and coding rules in the [architecture doc](../docs/cloud-platform-planning/cloud-platform-architecture.md), and coding-session rules in [`CLAUDE.md`](CLAUDE.md).
