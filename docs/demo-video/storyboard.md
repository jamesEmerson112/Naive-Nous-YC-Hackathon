# Demo video: storyboard v1 (draft, Sun Nov 8 ~8:50 AM PST)

Owner: Ishaan. Status: **draft for discussion, not approved.** Footage and renders never go in git; they live in `~/Movies/naive-demo/{raw,assets,renders}` on Ishaan's Mac.

## What this video is
- **Length:** 2:00 max (Ishaan's call). Target 1:56, leaving 4 s of slack.
- **Format:** 1920×1080, 30 fps, H.264 MP4, AAC stereo, loudness about −14 LUFS *(inferred: a normal web and projector target)*.
- **Naïve rules:** the hackathon asks for a live demo and says nothing about a video (`docs/hackathons.md` §1; YC event page). Hacking stops **11:00 AM**, presentations at **1:00 PM** (same sources). The team plan already wants a backup video at the 10 AM freeze (`features.txt`: "Never cut: … the backup video").
- **Hermes, later:** that entry needs a video **under 90 s**, posted on X tagging NousResearch, by Wed Nov 11 5 PM EST (`docs/hackathons.md` §2). Its star is the Hermes plugin, so it's a separate cut, not this one.
- **Open:** is this played on stage at 1 PM, a backup, or both? That sets the deadline (see the schedule at the end).

## Facts the script uses (checked today)
| Claim on screen | Source |
|---|---|
| The world's 500 biggest companies lose almost **$1.4 trillion** a year to unplanned downtime, 11% of revenue | Siemens Senseye, *The True Cost of Downtime 2024*: "We estimate that the world's 500 biggest companies lose almost $1.4 trillion annually through unplanned downtime, equivalent to 11% of their revenues." [PDF](https://assets.new.siemens.com/siemens/assets/api/uuid:1b43afb5-2d07-47f7-9eb7-893fe7d0bc59/TCOD-2024_original.pdf) |
| An hour of downtime in a large car plant costs **$2.3M** | Same report: "An hour's downtime in a large Automotive plant now costs $2.3 million an hour – or more than $600 a second." |
| **5 million** industrial robots in operation | IFR press release, Sep 24 2026: "The global operational stock of industrial robots surged 9% to a record 5 million units in 2025." [IFR](https://ifr.org/ifr-press-releases/five-million-robots-now-operate-in-factories-globally) |
| The healer model is qwen3.5:4b, 3.3 GB | `edge-final-report` (bench, Nov 7); `server/README.md` |
| The model runs **on the laptop, wired to the Pi by Ethernet**, not on the Pi | `server/README.md` ("too slow to run on the Pi 5") |
| 38 heal tools, each tagged read / runtime / code / flash / notify | `firmware/healer/tools.json` (38 entries) |
| `fw_flash` = backup → program → verify → watch → auto-rollback | `docs/project-overview.md` |
| Flashes capped per run (default 5, `--flash-cap`); tools outside the task's set are refused | `firmware/harness/README.md` |
| **The demo-1 fault already healed on the real bench:** bad firmware v1.7 (a `uint16_t` cast in the HC-SR04 distance math) makes the arm flail for 8 s and give up; the agent rolled back (verified, +46 s), re-ran the job (663 of 663 frames, +72 s), then named the cast on line 122 of `hcsr04.c`. 12 turns, 10 tool calls, 0 humans, 141 s | `firmware/harness/RESULTS.md`, run `20261108-000512-arm07-ollama` (Sun 12:05 AM). Its transcript is on Olir's laptop; `python3 view.py runs/<run>` replays it |
| Naïve Technician live spike: valid report in 29.6 s for $0.072 | Kent's block in `CLAUDE.md` (#67). **Use the numbers from the filmed run, not these.** |

No market-size (TAM) number: none we found is sourced well enough to put on screen *(inferred: most robot-ops TAM figures come from paid market-report sites)*.

## Storyboard (1:56)
VO is about 250 words, roughly 2.2 words a second. `[ ]` = fill in from the real take. **HUD** = the corner overlay that runs through the demo beats: an **OFFLINE / ONLINE** badge and a **"Human touches: 0"** counter.

| # | Time | Shot | Voiceover | On screen | Footage needed, from whom |
|---|---|---|---|---|---|
| 1 | 0:00–0:04 | **Cold open.** The arm on bad firmware v1.7: the obstacle reflex fires at nothing and it flails, then gives up. Glitch cut to black. | none (servo whine, glitch sound) | `arm-1 · firmware v1.7 · offline` | The arm07 fault on the real arm, 10 s, tripod. **Olir** (bench), same session as beat 7 |
| 2 | 0:04–0:12 | **Olir's story.** Animated orbit line with a satellite going dark, or a photo of Olir. | "Olir used to build satellites. When one [broke in orbit], nobody could log in to fix it, and [the data was lost]." | small caption: `Olir, [role]` | **Olir to confirm the facts.** Animation: Ishaan |
| 3 | 0:12–0:21 | **Stakes.** Three counters tick up, one at a time. | "Robots have the same problem on Earth. Unplanned downtime costs the world's 500 biggest companies 1.4 trillion dollars a year." | `$1.4T / year` · `$2.3M / hour, one car plant` · `5M robots at work` + source line | Animation: Ishaan |
| 4 | 0:21–0:27 | **Name reveal.** Logo, then the tagline. | "Meet HardwareBuddy. We make hardware companies autonomous." | logo + tagline | Animation: Ishaan |
| 5 | 0:27–0:40 | **How it works.** Three nodes light up in turn: Robot → Technician → Fleet cloud, joined in a loop. | "Three agents, one loop: a healer that fixes each robot's code offline, a Naïve technician for faults that need hands, and a fleet cloud that ships every good fix everywhere." | node labels: `Healer · on the robot`, `Technician · Naïve agent`, `Fleet cloud` | Animation: Ishaan |
| 6 | 0:40–0:48 | **Naïve.** Fast pass over Naïve Studio: the agents, a session's tool calls. | "The company side runs on the Naïve SDK: agents with their own tools and budget take cases, email technicians and source parts." | `Built on Naïve` | Screen recording of Naïve Studio. **Kent** |
| 7 | 0:48–1:10 | **Demo 1: it heals itself.** Split screen: left, the arm and pod on the bench; right, the agent's tool calls as a live feed (`view.py` replay or our animated feed from the transcript). Bullets slide in on the side. Sped up, with the speed shown. | "That flailing was a bug in the distance sensor's firmware, and there's no internet. A four-billion-parameter model on a laptop wired to the robot takes over with heal tools we built. It rolls the firmware back, re-runs the job, checks every joint, then finds the root cause: a 16-bit overflow on line 122. Nobody touched it." | HUD `OFFLINE` · `Human touches: 0`. Bullets: `qwen3.5:4b · 3.3 GB · no cloud` / `38 heal tools, each with a risk level` / `Every flash: backup → program → verify → auto-rollback` / `A gate caps flashes and refuses tools outside the job`. Tool feed: `status → fw_images → fw_rollback ✓ → arm_run ✓ 663/663 → src_diff → src_read → note`. End card: `Arm working in [1:12] · root cause in [2:21] · [N]× speed` (from the filmed run) | arm07 heal run on the bench, filmed (arm + pod wide, terminal screen-recorded) + its `transcript.jsonl`, `report.md`, `score.json`. **Olir** (with Ishaan). Close-up of the laptop's Wi-Fi turned off. Plan B: the 12:05 AM run's transcript replayed with `view.py`, labelled `Recorded run, Sun 12:05 AM` |
| 8 | 1:10–1:27 | **Demo 2: when it can't.** Kent's technician beat, as specified in `docs/naive-technician/demo-clip.md`: jammed elbow parks → Naïve session → verdict → email on a phone → part card → Approve → cart. | "And when it can't? Some faults need hands. The healer can't clear this jam, so a Naïve agent takes the case: diagnoses it, emails a technician what to check, and puts a spare servo in a cart. Our one step: approve." | HUD counter flips to `Human touches: 1` on the Approve click. Overlay `[time] · $[cost] · 1 human click` (measured) | Arm jam, Studio, case page, phone, cart. **Kent** (arm slot ~9:30) |
| 9 | 1:27–1:37 | **Sync.** The badge flips to `ONLINE`; incidents and the agent run report land in Romulus Mission Control; the heal timeline fills in. | "When the robot reconnects, every incident and every fix syncs to the cloud, root cause on the timeline. Nothing is lost." | HUD `ONLINE` | Screen recording of the sync (edge bridge → `agent_runs` → dashboard). **Kent / Olir** |
| 10 | 1:37–1:48 | **The fleet learns.** Animation: fixes from many robots flow into the cloud → replay → simulated canary → promote → out to every robot. Cut to the real gate page for 2 s. | "Fixes that hold are replayed, tested on a simulated twin, and rolled out to the whole fleet. What one robot learns, every robot gets." | `replay → sim canary → promote or roll back`; small tag `next: train the on-robot model on fleet traces` | Animation: Ishaan. Gate page recording: **Kent** |
| 11 | 1:48–1:56 | **Close.** The arm resumes its task (callback to the cold open), then the end card. | "HardwareBuddy. Robots that fix themselves, so hardware companies can run themselves." | logo, tagline, `Built on Naïve`, team: Andre · Ishaan · James · Kent · Olir | The arm finishing its job after the heal, 5 s (same take as beat 7). **Olir** |

## Honesty rules (from `docs/naive-technician/demo-clip.md`, plus two for this video)
- Label every speed-up on screen (`[N]× speed`).
- Label sim footage `SIM` and replays `Recorded run` in every frame. Never splice a sim incident onto real-arm footage.
- Say "staged a cart", never "ordered" or "paid".
- **Model location:** say "on a laptop wired to the robot" or "no cloud". Say "no internet" only if the laptop's Wi-Fi is off during the take (film the menu bar).
- **Match the VO to the take:** if the agent rolled back instead of writing a fix, the VO says so. Numbers on screen come from the filmed run's own logs.
- "Every robot gets the fix" is true of the gate's promote step; training the model on traces is roadmap, so it's tagged `next:`.

## Capture checklist, in order
Bench shots first: the arm and pod are only free until the 10 AM code freeze and the 11 AM stop.

**Bench (9:00–10:30, before the freeze where possible)**
1. [ ] **Olir + Ishaan: the arm07 heal, filmed (beats 1, 7, 11).** One take covers the cold open, the heal and the ending. Before starting: laptop Wi-Fi off, terminal font large (≥ 20 pt), phone on a tripod framing the arm and pod. Then: flash v1.7, start the screen recording and the phone, run the operator's job (the arm flails, exit 4), run the agent, let it re-run the job. Note the wall-clock start and end. Afterwards copy `runs/<run>/transcript.jsonl`, `report.md` and `score.json` to `~/Movies/naive-demo/raw/`. Plan B if the take fails before 10:30: the 12:05 AM run, replayed with `view.py` and labelled.
2. [ ] **Andre / James: arm B-roll.** A clean 10 s of the arm doing its normal job, from a second angle. Landscape, tripod, 1080p or 4K at 30 fps.
3. [ ] **Kent: technician jam (beat 8),** per `demo-clip.md`: at most 2 takes, 2 minutes apart (the servo heats at stall).
4. [ ] **Anyone: B-roll,** 5–10 s each: the STM32 pod, the ultrasonic sensor, the Pi with the Ethernet cable, hands at the bench, the team working.

**Screens (any time before 12:00)**
5. [ ] **Kent:** Naïve Studio agents and a session's tool calls (beat 6); the case page, phone email, part card, Approve, cart (beat 8). Browser zoom 125–150%, notifications off.
6. [ ] **Kent / Olir:** the sync moment: run report arriving in Romulus, heal timeline (beat 9).
7. [ ] **Kent:** the gate page for a real fix: replay → sim canary → promote (beat 10).

**Studio (Ishaan's side, in parallel)**
8. [ ] Animations for beats 2–5, 10 and 11, plus the HUD and bullets, built as HTML in `docs/demo-video/animations/` and screen-recorded.
9. [ ] Voiceover, once the cut is timed: one take per beat, quiet room.
10. [ ] Music: one royalty-free track, ducked under the VO.

## Schedule (if it plays on stage at 1 PM)
| Time | What |
|---|---|
| 8:50–9:00 | Plan approved; shot list sent to the team |
| 9:00–10:30 | Bench capture; animations built in parallel |
| 10:30–11:30 | Screen captures; rough cut |
| 11:30–12:15 | Voiceover, music, captions; final render |
| 12:15–12:30 | Copy to the presenting laptop and a USB stick (venue Wi-Fi) |

## Edit recipe
To be written once the footage is in: ffmpeg commands (or CapCut steps) that rebuild the cut from `~/Movies/naive-demo/raw/`.
