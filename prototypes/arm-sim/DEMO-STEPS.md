# Demo steps: Romulus Mission Control on the real arm

_James's runbook for the full-stack demo, Sun Nov 8. The bench details and history are in [`HANDOFF-LOCAL.md`](HANDOFF-LOCAL.md)._

**What the demo shows:** a real robot arm working for an agent-run company.
- An order becomes a **Naïve card**.
- **Hermes** (the LLM commander) picks it up and runs the arm, looking at the job through **the camera**, then closes the card with a `RESULT:`.
- **Romulus** (the cloud) and **Mission Control** (the screen) show the arm, its sensor pod and the fleet live.
- When the hardware breaks in a way software can't fix, the arm **parks itself and hands the job to a person** with a work order.

**Nobody types anything after the order.** No model sits in the real-time loop: plain-code checks park the arm, and the LLM picks skills.

## 0. Where things run
| Machine | Runs |
|---|---|
| **James's Mac** | Romulus (Docker, `:8000`), Hermes, the dispatcher, the camera capture (VS Code terminal), the SSH tunnel |
| **The Pi** (`pi@192.168.50.2`, cable to the Mac's USB Ethernet at `192.168.50.1`) | the arm host (real SO-101), Olir's `podd` (sensor pod), the edge bridge, Mission Control (`:8765`) |

All commands below run on the Mac from `prototypes/arm-sim` unless they say "on the Pi".

## 1. Before you start (and again after moving to the stage)
1. **The cable and the address.** The Mac's adapter must show `192.168.50.1`, and the Pi must answer:
   ```bash
   ping -c1 192.168.50.2
   ```
   If the address is gone, set it again: `sudo networksetup -setmanual "AX88179B" 192.168.50.1 255.255.255.0`.
2. **The tunnel** (carries the arm's socket and Mission Control). If `http://localhost:8765` doesn't load, restart it from Claude Code or the Terminal app. The Claude app's terminal pane can't reach the Pi.
   ```bash
   kill $(cat runs/logs/tunnel.pid) 2>/dev/null; rm -f "$TMPDIR/armsim.sock"; nohup ssh -N -o ExitOnForwardFailure=yes -o StreamLocalBindUnlink=yes -L "$TMPDIR/armsim.sock:/tmp/armsim.sock" -L 8765:127.0.0.1:8765 pi@192.168.50.2 > runs/logs/tunnel.log 2>&1 & echo $! > runs/logs/tunnel.pid
   ```
3. **The arm is up and healthy.** This should print `ok True`, alarm bits all `0` and no incidents:
   ```bash
   PYTHONPATH=. .venv/bin/python -c "from armsim.client import call; s=call('status'); print('ok', s['ok'], s['status'], s['incidents'])"
   ```
4. **Romulus** at `http://localhost:8000` shows `arm-1` and `pod-1` as `ok`, seen a few seconds ago.
5. **The camera (the arm's eye).** In VS Code's terminal (VS Code has the camera permission; Claude and Terminal don't), keep this running:
   ```bash
   mkdir -p /tmp/armcam && /opt/homebrew/bin/ffmpeg -hide_banner -loglevel warning -f avfoundation -pixel_format nv12 -framerate 30 -i "1:none" -vf "fps=1,scale=960:-1" -update 1 -y /tmp/armcam/latest.jpg
   ```
   - `1` is the iPhone (Continuity Camera) and `0` is the laptop.
   - Aim it at the arm, the chips bag and the box.
   - `ls -la /tmp/armcam/latest.jpg` should show it updated this second.
6. **The screens.** In Chrome, open Mission Control `http://localhost:8765` full screen, with Romulus `http://localhost:8000` in a second tab. Reload Mission Control once, so its buttons have the token.

## 2. The demo
### Beat 1: the product at a glance (Mission Control)
- **The digital twin:** the real `arm-1` live in 3D, with its joints in degrees. The twin is posed from the servos.
- **The sensor pod:** Olir's `podd` reads the pod's firmware stream.
- **The fleet:** Romulus's devices and work orders.
- **Say:** "Everything on this screen is live: the real arm, its sensor pod, and our cloud."

### Beat 2: an order runs itself
1. **Place the order.** It becomes a Naïve card:
   ```bash
   PYTHONPATH=. .venv/bin/python -m armsim.orders add wave --board naive
   ```
2. **Start the commander loop** (one job):
   ```bash
   PYTHONPATH=. .venv/bin/python -m armsim.dispatch --board naive --exec hermes --max-jobs 1 --every 10
   ```
3. **What happens:**
   1. The dispatcher claims the card.
   2. Hermes reads it, **looks at the camera**, and runs `go_home`, the chips pick-and-place ("pack the chips": pick up the bag, put it in the box) and `go_home`.
   3. It looks again, then posts `RESULT:` (what it did and what the camera saw) and closes the card.
4. **Show the card:**
   ```bash
   PYTHONPATH=. .venv/bin/python -m armsim.orders list --board naive
   ```
5. **Say:** "Nobody touched a keyboard after the order. The company's agent board handed the job to the arm's commander, and the commander reported back."
6. **If Naïve stalls** (it was at capacity today):
   - The dispatcher retries every 10 s. After about 60 s, press **Ctrl-C once** in its terminal.
   - Use the local fallback (section 3) or the backup clip.

### Beat 3: a fault the arm can't fix itself (the stall): not ready on the real arm
- **Why it doesn't work today:** the stall story needs a `home` pose different from the folded rest, so that the checked retry has to move the elbow. Teaching one by hand didn't work today: the arm stayed locked.
- **Don't run `stories stall --real`.** Its `reach` is refused (no taught `above_block`), and its check at home passes trivially.
- **Instead, show the stall in sim:** `PYTHONPATH=. .venv/bin/python -m armsim.stories stall`, with the sim arm host, not during the real-arm demo. Or use the team video.

### Beat 4: the kill switch
- Press **8 · Kill switch: Freeze fleet** in Mission Control, or click "Freeze fleet". Romulus freezes the whole fleet.
- The header shows **FLEET ● FROZEN**, the button turns into **UNFREEZE**, and the Fleet panel shows the frozen banner. Verified at 11:08 AM.
- **Say:** "One person can stop every arm." Then click **Unfreeze**.

## Known look-and-feel notes
- **Romulus's incident list** starts with four `pod-1 link_silent` incidents marked open (9:36–9:46 AM). The pod's link went quiet during podd restarts. Romulus has no route to close them, and they're a true record. Mission Control's "Pod incidents: none" is live from podd.
- **Mission Control's commander feed** shows jobs run by the Pi's dispatcher (the local fallback). A Naïve card + Hermes job runs from the Mac, so it shows there only as the arm moving, plus the card's `RESULT:` on Naïve.
- **Andre's three recordings** (`wave`, `recording_2`, `recording_3`) are all chips pick-ups with different poses. Only `wave` is a skill. The bag must sit where Andre recorded it, or the gripper closes on air.

## 3. The local fallback (no Naïve)
**Verified 11:09 AM:**
- The button placed the order. The header showed QUEUED, then WORKING ("Order #568d – wave hello – wave"), then IDLE.
- The card went from To Do to Doing to Done ("done in 22 s"). The 3D twin moved with the real arm.
- The Heal timeline counted 1 job done.

**Before the demo, clear the Pi's local board** of leftover test cards, or the dispatcher runs them first:
```bash
ssh pi@192.168.50.2 'cd ~/sage_hardware_healer/Naive-Nous-YC-Hackathon/prototypes/arm-sim && mv runs/board.json runs/board.json.bak-$(date +%H%M)'
```

1. Stop the Mac's dispatcher (Ctrl-C).
2. Start a local one **on the Pi**. **Never run both.** Each machine has its own lock, so two dispatchers would both drive the arm.
   ```bash
   ssh pi@192.168.50.2 'cd ~/sage_hardware_healer/Naive-Nous-YC-Hackathon/prototypes/arm-sim && PYTHONPATH=. .venv/bin/python -m armsim.dispatch --board file --exec scripted --max-jobs 1'
   ```
3. In Mission Control, press **2 · New order: Pack chips**. The card appears in **Jobs**, moves to Doing then Done, and the commander feed shows each step.

## 4. Mission Control's keys on the real arm
| Key | What it does |
|---|---|
| **2** ("Pack chips") | Places a "pack the chips" order (it runs when a dispatcher runs) |
| **8** | Freezes the fleet |
| **7** ("a person fixed it") | Runs the check at home, which **moves the arm** |
| **1** (pick) | Its poses aren't taught, so the job would be refused |
| **3–6** (faults) | Refused on the real arm: faults are planted by hand |

## 5. If something breaks
- **The arm host restart (on the Pi):**
  ```bash
  cd ~/sage_hardware_healer/Naive-Nous-YC-Hackathon/prototypes/arm-sim
  PYTHONPATH=. .venv/bin/python -c "from armsim.client import call; call('shutdown')"
  PYTHONPATH=. setsid nohup .venv/bin/python -u -m armsim.host --arm real --pod real --var ~/sage_hardware_healer/Naive-Nous-YC-Hackathon/firmware/healer/var --romulus http://192.168.50.1:8000 > runs/logs/host.log 2>&1 &
  ```
- **The camera stopped:** press Ctrl-C in VS Code's terminal and run the camera command again. The eye only reports, so a job never stops for it; Hermes notes "no camera".
- **Naïve at capacity:** use the local fallback (section 3), or play the backup clip.
- **The servo alarm bits aren't 0 in `status`:** park the arm (`call('skill', name='park')`), then check the arm by hand.
