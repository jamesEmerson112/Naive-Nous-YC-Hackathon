# Hardware Buddy: pitch animation storyboard

The animated visuals for the spoken parts of the pitch: everything before the demo video, plus the closing line after it. The demo video itself is not part of this.

- **Rule for every scene:** one idea, at most three things moving, and on-screen words are keywords, not the voiceover read back.
- **Colour meaning is the same in every scene and every style:** green means healthy or fixed, red means down or broken, and the Buddy accent colour means Hardware Buddy is acting.
- **The Buddy mark** is the brand: a rounded-square chip with three short pins on each side and two oval eyes. It blinks. "Make hardware alive" is the whole idea, so the mark looks alive.

## Running order

| # | Scene | Voiceover (Andre) | Beats | Autoplay |
|---|---|---|---|---|
| 1 | Downtime | "Companies lose billions of dollars in hardware and machine downtime." | 2 | ~7 s |
| 2 | Out of reach | "…satellites… short time window… Guam… no internet… millions of dollars lost." | 3 | ~11.5 s |
| 3 | Reveal | "That's why we came up with Hardware Buddy, a self-healing hardware operating system for manufacturing and robotics companies." | 1 | ~5 s |
| 4 | Onboarding | "We reach out to hardware companies with Naïve agents, and do their setup autonomously with coding agents." | 2 | ~7.5 s |
| 5 | Two parts | "Once set up, our platform has two main parts: the local element and the cloud element." | 3 | ~10 s |
| 6 | Alive | "We make their hardware alive. Need a part? Our agents order it. Firmware issue? The local LLM fixes it." | 3 | ~9.5 s |
| 7 | Before / after | "All problems that traditionally take human hours and shut down a factory or key process." | 2 | ~6 s |
| 8 | Demo bridge | "Here is the flow in a factory that packs chips." → cut to the demo video | 1 (hold) | ~4 s |
| — | *Demo video (not in this file)* | ultrasonic false trigger → local LLM fixes firmware → cloud sync → Naïve orders the part | | |
| 9 | Closing | "In a world with increasing software automation, we need smarter hardware. That's why we made Hardware Buddy." | 2 | ~3.5 s + hold |

About 60–70 seconds of animation in all, which matches the pre-demo voiceover read at a normal pace *(inferred: ~150 words at ~140 words per minute)*.

## Scene by scene

### 1 · Downtime
*Why it's here: the stakes, in one number.*

- **Beat 1 (holding frame, 2.5 s).** One robot arm on a short conveyor, picking boxes in a calm loop. A status pill reads `● RUNNING` in green. This can sit on screen while Andre is introduced.
- **Beat 2 (4.5 s).** The arm stutters and freezes mid-reach, the conveyor stops, and the pill flips to `● DOWN` in red. The arm dims and slides aside. A big number counts up from $0 to **$1.4 trillion**, with the caption **"lost every year to unplanned downtime"**.
  - Footnote, small: *World's 500 largest companies · Siemens, The True Cost of Downtime 2024.*
  - Source: [Siemens TCOD 2024](https://assets.new.siemens.com/siemens/assets/api/uuid:1b43afb5-2d07-47f7-9eb7-893fe7d0bc59/TCOD-2024_original.pdf): almost $1.4 trillion a year, 11% of revenue.
  - Suggested voiceover tweak, so the words match the number: "The world's biggest companies lose $1.4 trillion a year to machine downtime."

### 2 · Out of reach
*Why it's here: Andre's own story makes the problem real and explains "offline-first".*

- **Beat 1 (4 s).** The curve of the Earth along the bottom. A small ground antenna with a pin labelled **GUAM**. A satellite rises from the left along an arc, and the part of the arc overhead lights up: **contact window**.
- **Beat 2 (4 s).** The antenna sends signal pulses up toward the satellite. They break up halfway, and a red label appears: **NO LINK · no internet**. The satellite keeps going and leaves the window; the lit arc drains away.
- **Beat 3 (3.5 s).** One line of text fades in: **"One failure out of reach can cost millions."**

### 3 · Reveal
*Why it's here: the name and the one-line definition.*

- **Beat 1 (5 s).** The Buddy mark draws itself in, its eyes open and blink once, then the wordmark **Hardware Buddy** appears beside or under it.
  - Tagline: **"The self-healing OS for hardware"**
  - Sub-line, smaller: **"for manufacturing & robotics"**

### 4 · Onboarding
*Why it's here: how a customer gets on the platform with no humans involved (the Naïve hackathon brief).*

Three nodes: **Naïve agents** on the left, a **Hardware company** (factory icon) in the middle, **Coding agents** on the right.

- **Beat 1 (3.5 s).** An envelope travels from Naïve agents to the hardware company. Step label: **1 · Reach out**.
- **Beat 2 (4 s).** Coding agents connect to the company. A small terminal card ticks off three lines, each with a green check: `connect machines`, `install local model`, `link to cloud`. Step label: **2 · Set up, autonomously**. The company node gets a small Buddy badge: it's live.

### 5 · Two parts
*Why it's here: the architecture, in two boxes and one line.*

- **Beat 1 (3 s).** Left card, **Local**: a machine with the Buddy chip on it. Two short lines: "on-device LLM", "works offline".
- **Beat 2 (3 s).** Right card, **Cloud**: a cloud icon. Two short lines: "fleet memory", "Naïve agents for parts & people".
- **Beat 3 (4 s).** A link joins the two cards, with small packets flowing along it. The link drops (grey, dashed, label **offline**) while the Local card keeps pulsing green: it still works. Then the link comes back (label **syncs when online**) and a short burst of queued packets flows up to the cloud.

### 6 · Alive
*Why it's here: the two things Buddy does that nobody else does: it acts on hardware, it doesn't just report.*

- **Beat 1 (2.5 s).** Headline **"Make your hardware alive."** A heartbeat line runs across the screen and through a small Buddy mark.
- **Beat 2 (3.5 s).** Card **"Need a part?"**: a wear meter for a part (e.g. "gripper servo") fills to amber, then the card resolves to **"Ordered by agents ✓"**.
- **Beat 3 (3.5 s).** Card **"Firmware issue?"**: a two-line code diff. The red line goes out and the green line comes in, then the card resolves to **"Fixed by the local LLM ✓"**.

  ```c
  - if (dist_cm < STOP_CM) halt();
  + if (median3(dist_cm) < STOP_CM) halt();
  ```
  This is illustrative. It hints at the demo (a noisy ultrasonic reading stops the arm) without spoiling it.

### 7 · Before / after
*Why it's here: the contrast that makes the value obvious, right before the demo.*

- **Beat 1 (3 s).** Row **Today**: a short timeline: fault ⚠ → ticket → technician → fixed. A long red band under it reads **line stopped · hours of human work**.
- **Beat 2 (3 s).** Row **With Hardware Buddy**: fault ⚠ → fixed on the device ✓. A short green band reads **line keeps running**.

### 8 · Demo bridge
*Why it's here: a clean handoff to the video.*

- **Beat 1 (hold).** Kicker **LIVE DEMO**. Title **"A chip-packing line"**. A small line under it: **SO-101 arm · ultrasonic sensor · STM32 firmware**. Then cut to the demo video.

### 9 · Closing (after the demo)
*Why it's here: the thesis, then the name, last thing on screen.*

- **Beat 1 (3.5 s).** A quiet grey line, **"Software is automating everything."**, then under it in bold: **"Hardware has to get smarter."**
- **Beat 2 (hold).** The Buddy mark and **Hardware Buddy** wordmark return, with **"Self-healing hardware for robotics & manufacturing"** under it. The mark blinks every few seconds while it sits on screen for questions.

## How the files work

All three styles follow the same contract, so any of them can be swapped in on the day.

- **One self-contained HTML file per style**, with no network access at all: no CDNs, no web fonts, no images fetched from anywhere. Venue Wi-Fi is unreliable. It uses the Mac's system fonts (SF Pro, SF Mono, SF Pro Rounded).
- **A 1920 × 1080 stage**, scaled to fit any window, letterboxed.
- **Controls:**
  - Next beat: `→`, `Space`, `PageDown`, `Enter`, or a click. Presentation clickers send `PageDown` and `PageUp`.
  - Previous beat: `←` or `PageUp`.
  - `1`–`9` jumps to a scene; `Home` goes back to the start.
  - `A` turns autoplay on or off; autoplay uses the beat timings above. `?auto=1` in the URL starts in autoplay, for screen recording.
  - `F` toggles fullscreen; `H` hides the small progress dots.
  - The URL hash keeps the current scene (`#5`), so reloading stays on that scene.
- **Live pitch:** open the file full screen and click through the beats while talking. After scene 8, switch to the demo video; come back and press `→` for scene 9.
- **Recorded pitch:** open it with `?auto=1`, screen-record, and lay the voiceover on top. Scene 8 is the cut point for the demo video.
