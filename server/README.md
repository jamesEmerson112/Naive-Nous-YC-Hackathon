# server: run the healer's model on the laptop, and the harness on the Pi

qwen3.5:4b (the team's pick for the pod healer, `features.txt` line 46) is too slow to run on the Pi 5
*(inferred)*. So Ollama runs it on the laptop, and the pod harness (`firmware/harness/agent.py`, on
`ishaan/pod-agent-loop`) on the Pi calls it over the direct Ethernet cable.

```
 Pi 5  192.168.50.2                                laptop  192.168.50.1 (en7)
 firmware/harness/agent.py ── Ethernet ──► server/serve.py :11434 ──► Ollama 127.0.0.1:11434
   --ollama-url http://192.168.50.1:11434      (relay, Ethernet only)      qwen3.5:4b
```

`serve.py` is a raw TCP relay. Ollama keeps listening on localhost only, so local tools such as
`bench/run_bench.py` work unchanged, and nothing is opened on Wi-Fi. The relay listens only on the
laptop's Ethernet address and lets in only clients on that subnet. The harness's requests (`/api/chat`
with tools, `think: true`, `num_ctx` 32768) pass through untouched, streaming included.

| File | Where it runs | What it does |
|---|---|---|
| `start.sh` | laptop | Runs `serve.py` under `caffeinate -i`, so the laptop doesn't sleep mid-run |
| `serve.py` | laptop | Checks that Ollama has the model, loads it with `num_ctx` 32768, then relays from the Ethernet IP to Ollama. Logs one line per request |
| `pi_check.py` | Pi | Checks the model is listed, answers a plain chat, and returns a tool call with thinking on |

## Run it

On the laptop, with the Ollama app running and the cable plugged in:

```bash
./server/start.sh            # finds the Ethernet IP by routing towards the Pi (192.168.50.2)
```

It's ready when it prints `serving qwen3.5:4b to 192.168.50.0/24 at http://192.168.50.1:11434`.
Leave it running. Ctrl-C stops it.

On the Pi, in a checkout of `ishaan/pod-agent-loop` (or copy the check over with
`scp server/pi_check.py <user>@192.168.50.2:`):

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
- `--num-ctx 16384`: use this if you also pass `--num-ctx 16384` to the harness. Keep the two the
  same, or Ollama reloads the model on the first request.
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
- **The first request is slow after a long idle**: the model unloaded. The harness sends
  `keep_alive: 30m`, and the warm-up keeps the model loaded for 2 h.
