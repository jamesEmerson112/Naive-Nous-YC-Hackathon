# server: run the healer's model on the laptop, and the harness on the Pi

qwen3.5:4b (the team's pick for the pod healer, `features.txt` line 46) is too slow to run on the Pi 5
*(inferred)*. So Ollama runs it on the laptop, and the pod harness (`firmware/harness/agent.py`, merged as #41)
on the Pi calls it over the direct Ethernet cable.

```
 Pi 5  192.168.50.2                                laptop  192.168.50.1 (en7)
 firmware/harness/agent.py ── Ethernet ──► server/serve.py :11434 ──► Ollama 127.0.0.1:11434
   --ollama-url http://192.168.50.1:11434      (relay, Ethernet only)      qwen3.5:4b
```

`serve.py` is a raw TCP relay. Ollama keeps listening on localhost only, so local tools such as
`bench/run_bench.py` work unchanged, and nothing is opened on Wi-Fi. The relay listens only on the
laptop's Ethernet address and lets in only clients on that subnet. The harness's requests (`/api/chat`
with tools, `think: true`, `num_ctx` 65536) pass through untouched, streaming included.

| File | Where it runs | What it does |
|---|---|---|
| `start.sh` | laptop | Runs `serve.py` under `caffeinate -i`, so the laptop doesn't sleep mid-run |
| `serve.py` | laptop | Checks that Ollama has the model, loads it with `num_ctx` 65536, then relays from the Ethernet IP to Ollama. Logs one line per request |
| `pi_check.py` | Pi | Checks the model is listed, answers a plain chat, and returns a tool call with thinking on |

## Run it

On the laptop, with the Ollama app running and the cable plugged in:

```bash
./server/start.sh            # finds the Ethernet IP by routing towards the Pi (192.168.50.2)
```

It's ready when it prints `serving qwen3.5:4b to 192.168.50.0/24 at http://192.168.50.1:11434`.
Leave it running. Ctrl-C stops it.

On the Pi, in an up-to-date checkout of `main`:

```bash
python3 server/pi_check.py                                        # all three lines should say ok
cd firmware/harness
python3 agent.py run --tools core --ollama-url http://192.168.50.1:11434 --max-turns 6
python3 evaluate.py --tools core --ollama-url http://192.168.50.1:11434 --scenarios 05
python3 agent.py watch --tools core --ollama-url http://192.168.50.1:11434   # live healer
```

The steps and the report format are in `firmware/harness/HANDOFF.md` on that branch.

## Options

- `--pi 192.168.50.x`: the Pi got a different address. `--bind` names the laptop's address directly.
- `--num-ctx 131072`: a bigger context window, if the laptop has the memory (qwen3.5:4b takes up to 262144;
  65536 is the default since Nov 8, after the arm runs filled 32768). Pass the same `--num-ctx` to the harness
  and keep the two equal, or Ollama reloads the model on the first request.
- `--model qwen2.5:7b`: serve another installed model. Pass `--model` to the harness too.
- `--no-warm`: skip loading the model at startup.

## Checked (Sat Nov 7, 10:07 PM PST, from the laptop over 192.168.50.1)

- `pi_check.py`: all three checks pass. The thinking-and-tools turn took 2.4 s at 78 tokens/s and
  called `chip_status`. The model loaded in 3.8 s.
- Port 11434 doesn't answer on the laptop's Wi-Fi address.
- Not run from the Pi itself yet: the laptop has no SSH key for it. Run `pi_check.py` there first.

## Troubleshooting

- **`no route to the Pi`**: the cable is out, or the laptop's Ethernet has no 192.168.50.x address
  (System Settings → Network → the USB Ethernet adapter).
- **`Ollama isn't answering`**: open the Ollama app, or run `ollama serve`.
- **Connection refused on the Pi**: check that `start.sh` is still running and `ping 192.168.50.1`
  works. The macOS firewall was off when this was checked. If you turn it on, allow incoming
  connections for `python3`.
- **The first request is slow after a long idle**: the model unloaded. Each request keeps it loaded
  for its own `keep_alive` (the harness sends 30 min), which replaces the warm-up's 2 h. A reload
  takes about 4 s.

## The cloud link: the Pi reports to Romulus on the laptop

The pod healer records everything locally. The edge bridge (`tools/edge_bridge`, Kent's, decision D29)
runs on the Pi, reads those records and reports them to the Romulus cloud: a check-in every 10 s, and
one incident per fault with its log window and agent trace ([its README](../tools/edge_bridge/README.md)).
These scripts run the cloud on the laptop and keep the bridge running on the Pi.

```
 Pi 5                                                         laptop (Docker)
 podd ─► firmware/healer/var/ ─► edge-bridge.service ─► outbox/ ── Ethernet ──► api :8000 ─► db, worker
                                 (systemd, at boot)     on disk                 192.168.50.1 + localhost
```

**When the link is bad.** The bridge needs no link to keep working:
- **Incidents and recordings** go into an on-disk outbox (`data/edge_bridge/pod-1/outbox` on the Pi)
  before anything is sent. It survives restarts and power cuts and sends strictly in order. When the
  cloud doesn't answer, it retries with backoff (2, 4, 8 … up to 60 s), so everything queued arrives
  within about a minute of the link coming back (`tools/fake_device/src/fake_device/outbox.py`).
  Resends are harmless, because ids are made on the Pi.
- **Check-ins** aren't queued, since an old health report is worthless. While the link is down, the
  dashboard shows the pod as not checking in.
- **Systemd** starts the bridge at boot without waiting for a network and restarts it if it dies.

| File | Where it runs | What it does |
|---|---|---|
| `cloud_up.sh` | laptop | Starts db, api and worker with Compose, publishes the API on `127.0.0.1:8000` and the Ethernet IP (not Wi-Fi), seeds, and registers `pod-1` as a sensor pod. Safe to rerun |
| `compose.ethernet.yaml` | laptop | The Compose override behind those port bindings. The device API has no auth yet (architecture §10) |
| `pi/install_bridge.sh` | Pi | Installs Python 3.12 and only the bridge's packages into `.venv`, then installs and starts `edge-bridge.service` |

On the laptop:

```bash
./server/cloud_up.sh        # ends with: cloud reachable at http://192.168.50.1:8000
open http://localhost:8000/ # incident timeline, fleet (pod-1 is listed), kill switch
```

On the Pi, from the repo checkout, with internet for the first install:

```bash
git pull
bash server/pi/install_bridge.sh        # CLOUD_URL defaults to http://192.168.50.1:8000
journalctl -u edge-bridge -f            # check-ins, queued incidents, retries
firmware/healer/bin/podd                # the bridge reads podd; until it runs, pod-1 checks in as failing
```

After a `git pull` on the Pi, run `sudo systemctl restart edge-bridge`.

Notes:
- `make up` and `make down` still work, but `make up` publishes the API on every interface, Wi-Fi
  included. Rerun `./server/cloud_up.sh` to go back to Ethernet only. The containers restart with
  Docker (`restart: unless-stopped`). If the laptop boots with the cable out, the Ethernet binding
  fails, so rerun `cloud_up.sh` once the cable is back.
- Port 8000 was held by an unrelated container, `codex-dev-1` (`~/Projects/teamgpt/codex`). It was
  stopped, not removed, on Sun Nov 8. To give the port back: `docker compose down` here, then
  `docker start codex-dev-1`.
- **Checked from the laptop (Sun Nov 8):** the API answers on `192.168.50.1:8000` and not on the
  Wi-Fi address. `pod-1` shows `device_class: sensor_pod`. `uv run pytest tools/edge_bridge` passes
  60 tests. The bridge-only install imports cleanly with no cloud packages. **Not yet run on the Pi:**
  the laptop has no SSH key for it.
