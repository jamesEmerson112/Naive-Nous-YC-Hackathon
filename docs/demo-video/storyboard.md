# Hardware Buddy: judges' video, storyboard v2 (Sun Nov 8, ~8:55 AM PST)

Owner: Ishaan. Status: **draft for discussion, not approved.**
- **What it's for:** the video goes to the judges; the top 10 teams pitch on stage at 1 PM (Ishaan, Nov 8).
- **Length:** 2:00 max. Target 1:56.
- **Voices:** a mix of the team's own voices (Ishaan). Proposed split below; swap freely.
- **Built from:** Andre's pitch storyboard (`pitch/storyboard.md` on `claude/hardware-buddy-animation-56b7ff`, scenes 1–9) for the animated part, plus the real demo footage.
- **Format:** 1920×1080, 30 fps, H.264 MP4, AAC. Footage and renders stay out of git, in `~/Movies/naive-demo/{raw,assets,renders}`.
- **Tooling:** Remotion (`video/` on this branch) renders the whole cut from code; ffmpeg probes and converts footage. Re-cut = edit the timings in `video/src/timeline.ts`, run `npm run render`.

## Running order (1:56)
`[ ]` = fill in from the real take. HUD = corner overlay through the demo: an OFFLINE / ONLINE badge and a "Human touches" counter.

| # | Time | Scene | Voice | Voiceover | Visual |
|---|---|---|---|---|---|
| 1 | 0:00–0:07 | Downtime (Andre's scene 1) | Andre | "The world's biggest companies lose 1.4 trillion dollars a year to unplanned downtime." | Arm on a conveyor freezes, pill flips RUNNING → DOWN, counter to **$1.4T**. Footnote: Siemens 2024 |
| 2 | 0:07–0:18 | Out of reach (scene 2) | Andre | Andre's own line: [satellites · Guam · short contact window · no internet · millions lost] | Earth curve, GUAM antenna, contact window, link breaks |
| 3 | 0:18–0:23 | Reveal (scene 3) | Ishaan | "So we built Hardware Buddy: a self-healing OS for robotics and manufacturing." | Buddy chip draws in, blinks, wordmark |
| 4 | 0:23–0:30 | Onboarding (scene 4) | Kent | "Naïve agents find hardware companies and set them up autonomously, with coding agents." | Envelope → factory; terminal ticks `connect machines`, `install local model`, `link to cloud` |
| 5 | 0:30–0:39 | Two parts (scene 5) | Olir | "A local model on the device works offline. The cloud syncs when it's back online." | Local card and Cloud card; link drops, local keeps pulsing, link returns, packets burst up |
| 6 | 0:39–0:42 | Demo bridge (scene 8) | none | | `LIVE DEMO` · `SO-101 arm · ultrasonic · STM32 firmware` |
| 7 | 0:42–0:46 | **The fault** | none (servo whine) | | Real arm on bad firmware v1.7: the reflex fires at nothing, it flails and gives up. Caption `firmware v1.7 · offline`. HUD on |
| 8 | 0:46–1:08 | **It heals itself** | Ishaan | "That's a firmware bug in the distance sensor, and there's no internet. The local model on the device takes over with heal tools we built. It rolls the firmware back, re-runs the job, checks every joint, then finds the root cause: a 16-bit overflow on line 122. Nobody touched it." | Split screen: arm + pod left, live tool-call feed right (`status → fw_images → fw_rollback ✓ → arm_run ✓ 663/663 → src_diff → src_read → note`). Real diff card (below). Side bullets: `qwen3.5:4b · 3.3 GB · on device` / `38 heal tools, each with a risk level` / `every flash: backup → program → verify → auto-rollback`. End card: `arm working in [1:12] · root cause in [2:21] · [N]× speed` |
| 9 | 1:08–1:25 | **When it needs hands** | Kent | "Some faults need hands. The healer can't clear this jam, so a Naïve agent takes the case: diagnoses it, emails a technician what to check, and puts a spare servo in a cart. Our one step: approve." | Kent's shot list in `docs/naive-technician/demo-clip.md`: jammed elbow parks → Naïve session → verdict → phone email → part card → Approve → cart. HUD counter 0 → 1 on Approve. Overlay `[time] · $[cost] · 1 human click` |
| 10 | 1:25–1:33 | **Sync** | Olir | "Back online, every incident and every fix syncs to the cloud." | HUD flips ONLINE; the agent run report lands in Romulus; root cause on the incident timeline |
| 11 | 1:33–1:44 | **The fleet learns** | Ishaan | "Fixes that hold are replayed, tested on a simulated twin, and shipped to every robot. What one robot learns, every robot gets." | Fleet of Buddy chips → cloud → `replay → sim canary → promote` → back out to all; 2 s of the real gate page |
| 12 | 1:44–1:56 | Closing (scene 9) | Andre | "Software is automating everything. Hardware has to get smarter. That's Hardware Buddy." | Thesis lines, then Buddy mark + wordmark, `Built on Naïve`, team: Andre · Ishaan · James · Kent · Olir |

Cut from Andre's storyboard to fit 2:00: scene 6 "Alive" (the demo now shows it for real) and scene 7 "Before / after" (6 s; first back in if there's room).

**Real diff for the heal (beat 8), from `firmware/demo/bad-images/07-us-dist-16bit/bug.patch`:**
```c
- out->dist_mm = (uint16_t)(us * 343u + 1000u) / 2000u;   // v1.7: truncates to 16 bits
+ out->dist_mm = (us * 343u + 1000u) / 2000u;             // known-good
```

## Sources for numbers on screen
| Claim | Source |
|---|---|
| The world's 500 biggest companies lose almost **$1.4T** a year to unplanned downtime, 11% of revenue | Siemens Senseye, *The True Cost of Downtime 2024*: "We estimate that the world's 500 biggest companies lose almost $1.4 trillion annually through unplanned downtime, equivalent to 11% of their revenues." [PDF](https://assets.new.siemens.com/siemens/assets/api/uuid:1b43afb5-2d07-47f7-9eb7-893fe7d0bc59/TCOD-2024_original.pdf) |
| $2.3M per hour of downtime in a large car plant (spare stat) | Same report |
| 5 million industrial robots at work (spare stat) | IFR, Sep 24 2026: "The global operational stock of industrial robots surged 9% to a record 5 million units in 2025." [IFR](https://ifr.org/ifr-press-releases/five-million-robots-now-operate-in-factories-globally) |
| 38 heal tools, each with a risk level | `firmware/healer/tools.json` |
| The arm07 heal already passed on the real bench: rollback verified at +46 s, job re-run 663 of 663 frames at +72 s, root cause named (the cast on line 122), 141 s, 0 humans | `firmware/harness/RESULTS.md`, run `20261108-000512-arm07-ollama`. **New takes replace these numbers with their own.** |
| Naïve Technician | `docs/naive-technician/demo-clip.md`; numbers from the filmed run |

On-screen rules kept from Kent's clip spec: label speed-ups (`[N]× speed`), say "staged a cart", never "ordered" or "paid".

## Capture checklist, in order
Bench first: the arm and pod are only free until the 11 AM stop.

**Bench (now → 10:15)**
1. [ ] **Olir + Ishaan: the arm07 heal, new take (beats 7, 8).** Terminal font ≥ 20 pt; phone on a tripod, landscape, framing arm + pod; screen-record the terminal. Flash v1.7, run the operator's job (it flails, exit 4), run the agent, let it re-run the job. Note wall-clock start and end. Copy `runs/<run>/transcript.jsonl`, `report.md`, `score.json` to `~/Movies/naive-demo/raw/`.
2. [ ] **Kent: technician jam (beat 9)**, per `demo-clip.md`: at most 2 takes, 2 minutes apart.
3. [ ] **Anyone: B-roll**, 5 s each: STM32 pod, ultrasonic sensor, the device board, hands at the bench.

**Screens (now → 10:15)**
4. [ ] **Kent:** Naïve session, case page, phone email, part card → Approve → cart (beat 9); browser zoom 125–150%, notifications off.
5. [ ] **Kent / Olir:** the run report arriving in Romulus and the incident timeline (beat 10); the gate page for a real fix (beat 11).

**Animation (Andre + Ishaan)**
6. [ ] **Andre:** pick one style of his pitch animation (Buddy, Keynote or Mission Control) and push the HTML; Ishaan records it with `?auto=1` at 1920×1080 into `~/Movies/naive-demo/raw/anim/`.
7. [ ] **Ishaan (Remotion):** HUD, tool-call feed, diff card, bullets, end-card numbers, beat 11's fleet animation, captions; the animatic with placeholders so the voiceover can be recorded against the timing.

**Voice (by 10:15)**
8. [ ] Each voice records their lines from the table, one file per beat, phone voice memo in a quiet corner, named `vo-<beat>-<name>.m4a`, AirDropped to Ishaan → `~/Movies/naive-demo/raw/vo/`.

## Edit recipe
`video/README.md` (to be written once the footage is in).
