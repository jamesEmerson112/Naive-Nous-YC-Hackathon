# Demo guide: every failure, what the logs show, and how the tools fix it

Six bad firmware releases and four physical faults. For each one, this guide gives:
- how to start it
- what the logs and triage show
- the investigation an agent (or you) would run
- the root cause from first principles
- the fix and how it's verified

The outputs quoted here were captured on the bench on Sat Nov 7, with this board and this firmware.

> **Test status (Nov 7, evening):**
> - **Re-verified on the current images:** detection for scenarios 01–05 (`healer/tests/bench.py A1–A5`)
>   and the full 01 fix.
> - **Verified earlier the same day:** the 02, 05 and 06 fixes.
> - **Not yet run:** the durable fixes for 03 and 04 (B1, B2), the physical tests, and any LLM agent run.
> - **Scenario 04 has a known problem** with the probe's serial bridge; see its section. Integration status
>   and open items for the harness team are in [HARNESS.md](HARNESS.md).

## How the pieces fit

```
 operator                          agent (LLM harness, not built yet)
 demo/scenario start/trigger       heal <tool> ...  (tools.json)
        │                                   │
        ▼                                   ▼
 ┌──────────────┐   SWD (OpenOCD)    ┌─────────────────────────────────────────────┐
 │ STM32 pod    │◄──────────────────►│ swd.py: build ID, uptime rate, clock decode,│
 │ firmware     │                    │ halt samples, flash/backup                  │
 │ (pod/)       │   UART 115200      │ podd: parse → rings → logs → triage → socket│
 │              │───────────────────►│ images.py: registry by build-ID CRC         │
 └──────────────┘   @id cmd / R ...  │ workspace.py: firmware source (git), build  │
                                     └─────────────────────────────────────────────┘
```

- **Pod firmware:** streams `D`/`A`/`F`/`H`/`E` lines and answers commands. It also captures its own
  crashes, watchdog resets and boot loops.
- **podd:** owns the serial port, logs everything, and runs the triage rules every 3 s, which open and
  resolve incidents.
- **heal:** 38 tools, each with a risk level: read, runtime, code, flash or notify.
- **The workspace:** a git repo of the firmware source. Tag `v1.0` is known-good. `scenario start` adds one
  "release" commit with the bug, the way a real team's history would look.
- **What the agent can't see:** `demo/` and `pod/`. It finds the bug from the evidence, the git history and
  the source.

## Before the demo
```bash
cd firmware
demo/scenario setup && demo/scenario build-images   # once (again after any change to pod/)
healer/bin/podd &                                    # keep running
demo/scenario reset                                  # known-good v1.0 on the board, workspace at v1.0
healer/bin/heal triage                               # expect: "findings": []
```
Run `demo/scenario reset` between scenarios. It restores v1.0, clears injected faults and sets podd back
to 115200 baud. `demo/scenario clear` clears injected faults only, e.g. to "plug the sensor back in" during
scenario 06.

What healthy looks like (`heal telemetry_stats seconds=5`):
- accelerometer at 49.8 Hz, |a| = 1077 mg ± 6
- distance at 10 Hz
- link at about 2300 bytes/s of 11520
- health `loop_us` about 3300, `stk_free` 4172, `vdda` 3309, `clk` 48000000

## Summary

| # | Release | What breaks | First signal | Key evidence tool | Durable fix |
|---|---|---|---|---|---|
| 1 | v1.1 | HardFault crash loop → safe mode | `crash`, `reboot_loop`, `safe_mode` | `fw_symbolize` on crash pc | code: byte-wise stores (memcpy) |
| 2 | v1.2 | UART garbled, time and distances at 0.5× | `link_garbled` | `link_baud_scan`, `chip_clock` | code: restore HSI/PREDIV clock setup |
| 3 | v1.3 | accelerometer frozen after a sensor glitch | `acc_frozen` | `pod_cmd "acc regs"` | runtime `acc init`, then code: restore config readback |
| 4 ⚠ | v1.4 | telemetry overruns the UART; distance starves | `tx_overrun` | `telemetry_stats` (byte budget) | runtime `rate acc 50`, then code: fit the budget. **Keep runs short: the probe's serial bridge can wedge after ~20 s** |
| 5 | v1.5 | accelerometer reads 0.5 g at rest | `acc_gravity` | `pod_cmd "acc regs"` (FS bits) | code: mg/digit = 2 for ±4 g |
| 6 | v1.6 | missing sensor → hang → watchdog loop → safe mode | `watchdog`, `reboot_loop` | boot `last_cp`, `chip_halt_sample` | code: restore I2C timeouts, then a human replugs |
| 7 | v1.7 | ultrasonic reads 0–32 mm whatever is in front; the arm never starts | `us_mismatch` (critical) | `telemetry_recent kind=D` (us vs mm), `src_diff rev_a=v1.0` | arm task: `fw_rollback` to known-good, confirmed by `arm_test`; later code: drop the cast |
| P1 | v1.0 | ultrasonic covered | `us_blocked` | `telemetry_stats` | none needed: wait / human |
| P2 | v1.0 | loose ECHO wire | `us_intermittent` (+ motion) | `pod_cmd "us check"` | human reseats the wire |
| P3 | v1.0 | LIS3DH unplugged | `acc_error` (nack) | `pod_cmd "i2c scan"` | human reconnects; firmware keeps running |
| P4 | v1.0 | LIS3DH glitch (same as #3) | none: firmware heals itself | `events kinds=acc_cfg_lost,acc_init` | already in v1.0 |

Each fix follows the escalation ladder in `healer/SYSTEM.md`:
1. Observe.
2. Find the root cause, physical vs firmware.
3. Mitigate at runtime.
4. Fix durably with the smallest change.
5. Verify.
6. Record.

---

## 1. v1.1: "frame: pack fields with direct stores" (HardFault crash loop)

**Start:** `demo/scenario start 01`. No trigger is needed: it crashes about 1 s after boot, at the first
radio frame.

**The bug:** the radio frame puts a `uint32_t` at byte offset 1 and `int16_t`s at odd offsets. The release
replaced byte-wise packing with pointer casts:
```c
*(uint32_t *)(buf + 1) = s->t_ms;      // compiles to: str r1, [r3, #0] with r3 odd
```
The Cortex-M0 (ARMv6-M) doesn't support unaligned word or halfword access, so this raises a HardFault.
The same code runs fine on an M3/M4, which is how this kind of bug gets shipped.

**What the logs show:** within about 4 s, three crash-and-reboot cycles and then safe mode.
```
critical reboot_loop - 3 abnormal boots in the last 60 s    (streak 1, 2, 3; last_cp=frame)
critical crash       - HardFault at pc=0x0800201E (lr=0x0800200B) in build 0xE4E0E4BB, during 'frame'
                       r3=0x2000002D  r1=0x000003EB  sp=0x20001750  up=1005
critical safe_mode   - Firmware is in safe mode (sensors off) after a boot loop   {streak: 3, crashes: 3}
```

**Investigation:**
1. `triage` reports crash, reboot_loop and safe_mode. The firmware is still up and answering, because safe
   mode stopped the crashing code.
2. `fw_symbolize address=0x0800201E image=E4E0E4BB` gives `frame_pack` at `src/frame.c:63`, with source
   context and disassembly:
   ```
   ->   63      *(uint32_t *)(buf + 1) = s->t_ms;
    800201c:  adds  r3, r5, #1
    800201e:  str   r1, [r3, #0]
   ```
   The crash record's `r3 = 0x2000002D` is the store address, and it's odd, so the fault is an unaligned
   word store. That's proof, not a guess.
3. `src_log` shows that v1.1 is a single commit after v1.0. `src_diff rev_a=v1.0` confirms only `frame.c`
   changed.

**Fix:**
- **Rollback** (`fw_rollback`) works, but it drops the release.
- **Forward fix** (tested): keep the faster single-pass packing, but store through `memcpy`, which is
  alignment-safe and stays small with `-Os`:
  ```c
  uint32_t t = s->t_ms; ...
  memcpy(buf + 1, &t, 4);  memcpy(buf + 5, mean, 6);  memcpy(buf + 11, &rms, 2);  memcpy(buf + 13, &cnt, 2);
  ```
  Then run `src_edit` (plus `#include <string.h>`), `fw_build` and `fw_flash`.

**Verify:** `fw_flash` reported `build_id_in_flash ✓`, `boot_event ✓`,
`no_reboots ✓ {boots: 1, crashes: 0}`, `health_flowing ✓ 15 in 15 s` and `normal_mode ✓`. Then run
`src_commit`.

**Talking point:** the Cortex-M0 has no fault-status registers. The stacked PC plus the archived ELF for
that exact build ID is the whole diagnosis, and the system keeps both.

---

## 2. v1.2: "clock: use the fixed HSI/2 PLL input" (garbled UART, slow time)

**Start:** `demo/scenario start 02`. It's broken from boot.

**The bug:** the release switched the PLL input from HSI/PREDIV (÷2) to the fixed HSI/2 input. The hardware
still runs at 48 MHz (4 MHz × 12). But on the STM32F042, CMSIS's `SystemCoreClockUpdate()` treats both
HSI inputs as HSI/PREDIV. With PREDIV left at its reset value of ÷1, it computes 8 × 12 = **96 MHz**. We hit
this exact quirk earlier on this board; see the hello project.

Everything programmed from `SystemCoreClock` is therefore off by the same 2×:
- UART BRR = 96 MHz / 115200 = 833, so the real baud is 48 MHz / 833 = 57623
- SysTick ticks every 2 ms instead of 1 ms
- TIM3 counts at 0.5 MHz, so echo times and distances come out halved

**What the logs show:**
```
critical link_garbled - Bytes arrive but don't parse as telemetry (0% of lines valid)
         sample: "\x80\x98\x80`\x06\x18~\xe6\x98x..."      (serial.log keeps every garbled line)
```

**Investigation:**
1. `link_baud_scan`:
   ```
   115200 valid 0.00 | 57600 valid 0.97 | 230400 0.00 | 38400 0.00 | ...
   "telemetry parses cleanly at 57600 baud: the MCU's UART runs at 0.500x the intended rate"
   ```
2. `chip_status` over SWD: `uptime_ratio_to_real_time: 0.501`. The CPU is alive, and its millisecond
   counter runs at half speed.
3. `chip_clock` decodes the RCC registers:
   ```
   MISMATCH: firmware believes 96 MHz but the RCC is configured for 48 MHz (HSI/2 x12).
   uart_actual_baud 57623 (intended 115200)   systick_period_ms_actual 2.0   tim3_tick_us_actual 2.0
   ```
4. Optional: `link_set_baud baud=57600` makes the telemetry readable. The `clock_skew` rule then fires
   ("counter runs at 0.50x real time"), and the health record shows `clk=96000000`.
5. `src_diff rev_a=v1.0` shows the PLL source change, and that the comment explaining the CMSIS quirk was
   deleted.

**Fix:**
- **Forward fix:** restore `RCC->CFGR2 = RCC_CFGR2_PREDIV_DIV2` and `RCC_CFGR_PLLSRC_HSI_PREDIV` (the same
  48 MHz, but CMSIS computes it correctly).
- **Alternative:** keep HSI/2 and set `SystemCoreClock = 48000000` explicitly. That works, but it's
  fragile.
- `fw_flash` resets podd to 115200 before flashing.

**Verify:** a clean link at 115200, `chip_clock` ratio 1.0, `chip_status` uptime ratio 1.00.

**Guardrail (tested):** if a broken image like this is flashed through `fw_flash`, no boot line appears, so
verification fails, the image is marked `bad`, and the previous image is re-flashed automatically:
```
boot_event False  "no boot line from this build within 8 s (UART garbled or firmware not starting?)"
rollback -> ok   pod v1.6-1-gc9bd3d9
```

**Talking point:** three independent views agree: serial, SWD timing and register decode. When one clock
assumption is wrong, every derived rate is wrong by the same factor.

---

## 3. v1.3: "acc: drop the once-a-second config readback" (frozen accelerometer)

**Start:** `demo/scenario start 03`, then about 5 s later `demo/scenario trigger 03` (`inject acc_glitch`).

**The bug:** a brief supply dip or ESD event can reset the LIS3DH without resetting the MCU, because their
brownout thresholds differ. The sensor comes back in its power-on state, which is power-down mode
(`CTRL_REG1 = 0x07`), so it stops producing samples. v1.0 caught this with a once-a-second register
readback, and v1.3 removed it "to save I2C traffic". The injection writes the power-on register values,
which is what such a glitch does.

A longer unplug is different: it causes I2C errors, and the error path re-initializes the sensor. Only a
silent reset gets through, and that's why the injection is used here.

**What the logs show:**
```
critical acc_frozen - Accelerometer output is frozen: 150 samples, every axis identical (zero noise)
         std_mg [0.0, 0.0, 0.0], mean_mg [24, -20, 1068], stale_fraction 1.0
```
The values still look plausible (|a| = 1068 mg), so a range check alone would miss this. Zero variance is
the giveaway: a real MEMS sensor always shows a few mg of noise.

**Investigation:** `pod_cmd "acc regs"` returns:
```
who_am_i=0x33 ctrl1=0x07 ctrl4=0x00 status=0x00   expect_ctrl1=0x57 expect_ctrl4=0x88
```
The sensor answers, so the wiring and the device are fine. Its configuration is back at power-on defaults.
That means it reset, and nothing re-applied the configuration.

**Fix:**
1. **Mitigation:** `pod_cmd "acc init"`. The `acc_frozen` finding clears within a few seconds (tested).
   For about 3 s afterwards, triage may show `acc_stale`, because its 3 s window still holds the frozen
   samples. It clears by itself. The next glitch freezes the sensor again, though.
2. **Durable:** `src_diff rev_a=v1.0` shows that `acc_check_task()` and its call were removed. Restore them,
   either by editing or with `src_restore rev=v1.0` on `main.c`. Then `fw_build`, `fw_flash` and `src_commit`.

**Verify:** run `demo/scenario trigger 03` again. The fixed firmware heals itself, as in P4:
`E ev=acc_cfg_lost ctrl1=0x07`, then `E ev=acc_init why=config_lost result=ok`, and no finding.

**Talking point:** this shows a runtime fix followed by a durable fix, and the durable fix proves itself on
the next fault.

*Durable fix not yet run end to end (bench test B1). The same code healing itself on v1.0 is tested (P4).*

---

## 4. v1.4: "acc: stream at 400 Hz for vibration analysis" (UART overrun)

**Start:** `demo/scenario start 04`. It's broken from boot.

> ⚠ **Known problem (found Nov 7, evening):** after about 20 s of full-rate traffic, the on-board J-Link's
> USB serial bridge stopped delivering bytes to the Pi. The MCU kept transmitting at about 12 kB/s, as SWD
> showed. Triage then reports `link_silent`, and neither reopening the port nor a baud change brought it
> back. A USB replug is expected to clear it (not yet verified).
> - **For the demo:** capture the `tx_overrun` evidence within the first ~15 s and apply `rate acc 50`
>   quickly, or skip 04.
> - **Likely fix:** a milder overrun in the 04 image (about 250–300 Hz).

**The bug:** at 115200 baud 8N1 the link carries at most 11520 bytes/s. An `A` line is about 35 bytes. The
stream period is `1000/400 = 2 ms` after integer division, so the firmware actually tries 500 Hz:
500 × 35 ≈ 17.5 kB/s for accelerometer lines alone. The TX ring fills, data lines get dropped whole, and the
10 Hz distance stream loses out to the accelerometer for the free slots. Each 400 Hz read also blocks the
loop for about 1.2 ms of I2C.

**What the logs show:**
```
warn tx_overrun - Telemetry exceeds the UART: 1702 lines dropped in 9 s
     observed_bytes_per_s 11502 (capacity 11520)   acc_hz_requested 500   avg_A_line_bytes 35.1
     A_bytes_per_s_requested 17562   A_lines_per_s_received 320.8   D_lines_per_s_received 0.4 (expected 10)
```
`telemetry_stats` agrees: distance at 0.4 Hz, `loop_us` 12237, and accelerometer MCU time gaps of 2–25 ms.

**Investigation:** it's arithmetic. The requested rate times the line length exceeds the link capacity, and
the link sits at 99.8% of capacity. `src_diff rev_a=v1.0` shows `ACC_STREAM_HZ 50 → 400` and ODR
100 → 400 Hz.

**Fix:**
1. **Mitigation:** `pod_cmd "rate acc 50"` clears the finding (tested). It's lost on reset.
2. **Durable:** the release wanted vibration data, so keep that intent within the budget. Options:
   - stream 100 Hz (≈3.5 kB/s)
   - keep sampling at 400 Hz but send the 1 Hz summary (the `F` frame already carries RMS vibration)
   - raise the baud rate (needs host and probe support)

   The minimal change is `ACC_STREAM_HZ` 100 with ODR kept at 400.

**Verify:** `tx_drop` stops increasing, distance is back at 10 Hz, and `loop_us` returns to about 3 ms.

**Talking point:** the agent solves this from first principles, with a byte budget instead of pattern
matching.

---

## 5. v1.5: "acc: use ±4 g full scale so impacts don't clip" (0.5 g at rest)

**Start:** `demo/scenario start 05`, and keep the board still.

**The bug:** the release changed `CTRL_REG4` to FS = ±4 g but kept `ACC_MG_PER_DIGIT = 1`. In
high-resolution mode the LIS3DH gives 1 mg/digit at ±2 g and 2 mg/digit at ±4 g (datasheet Table 4), so
every reading is half of what it should be.

**What the logs show:**
```
warn acc_gravity - Board is at rest but measures 529 mg, not 1000 mg (ratio 0.53)
     note: "ratio is ~0.5, a power of two: typical of a full-scale range that doesn't match the sensitivity"
```
Against this board's own baseline of 1077 mg the ratio is 0.49, so exactly 2×.

**Investigation:** `pod_cmd "acc regs"` shows `ctrl4=0x98`. Bits 5:4 = 01 means ±4 g, but
`mg_per_digit=1`. `src_search pattern="MG_PER_DIGIT|CTRL4"` and `src_diff rev_a=v1.0` show that only the
FS bits changed.

**Fix:** the forward fix keeps ±4 g, as the release intended, and sets `ACC_MG_PER_DIGIT 2`. Ideally the
constant is derived from the FS setting so the two can't drift apart again. Rollback would lose the
anti-clipping change.

**Verify:** |a| is about 1000–1080 mg at rest, and the `acc_gravity` finding is gone.

**Talking point:** this is a quiet data-integrity bug. Nothing crashes. Only a physical invariant (gravity
is 1 g) catches it. Once the arm is available, the same check becomes FK-predicted gravity against measured.

---

## 6. v1.6: "i2c: simplify flag waits" (missing sensor, watchdog loop)

**Start:** `demo/scenario start 06`, then:
- **Physically:** pull the LIS3DH SDA wire (A4/D4) or its VIN. This is the best version for judges.
- **Or:** `demo/scenario trigger 06` (`inject acc_gone`). The firmware then addresses 0x1A, as if the
  sensor were absent. The flag survives resets and reflashes until `scenario reset` or a power cycle.

**The bug:** v1.6 replaced the I2C wait (flag, or NACK / bus error / arbitration lost / 5 ms timeout) with
`while (!(I2C1->ISR & flag)) {}`. When nothing answers, the address is NACKed, TXIS never sets, and the
loop spins forever. The watchdog resets the chip after about 2 s. The next boot hangs in `lis3dh_init`
again, and after three such boots the firmware enters safe mode.

**What the logs show:**
```
critical reboot_loop - 3 abnormal boots in the last 60 s
         rst=pin,iwdg last_cp=acc_read, then acc_init, acc_init (safe)
critical watchdog    - Watchdog reset; the main loop was last at checkpoint 'acc_init'
critical safe_mode   - Firmware is in safe mode (sensors off) after a boot loop   {crashes: 0, wdg: 3}
```
There are no crash records, because nothing faulted. The CPU was stuck, not crashed.

**Investigation:**
1. The checkpoint names the stage (`acc_init`). If you catch it before safe mode, `chip_halt_sample` shows
   the CPU sitting in `wait_isr` (`src/i2c.c`).
2. `src_diff rev_a=v1.0` shows the error checks removed from `wait_isr`.
3. In safe mode, `pod_cmd "i2c scan"` finds nothing at 0x18, so the sensor really is gone.

**There are two root causes.** A physical one (the sensor is disconnected) and a firmware one (a
disconnected sensor shouldn't take down the whole pod).

**Fix (tested):**
1. Restore the timeouts with `src_restore rev=v1.0` (or edit), then `src_commit`, `fw_build` and `fw_flash`.
2. Verification passes with the sensor still missing: no reboots, normal mode, ultrasonic working.
3. Triage then narrows the problem to `acc_error - Accelerometer not responding on I2C (last error: nack)`.
4. `notify_human`: "Reconnect the LIS3DH: SDA → A4/D4, SCL → A5/D5, VIN → 3V3."
5. When it's plugged back in (or after `demo/scenario clear`), the firmware's 2 s retry re-initializes it on
   its own. *Not yet verified on the bench (test C2).*

**Talking point:** the agent fixes what software can fix and hands the rest to a human with precise
instructions. That's the trust story: it knows what it can't fix.

---

## 7. v1.7: "us: tidy the distance conversion" (the arm never starts)

**Start:** `demo/scenario start 07`, with the HC-SR04 pointed at open space more than 200 mm away. The whole
loop, arm included, is `python3 arm/heal_test.py` (see `arm/README.md`, "Self-healing test").

**The bug:** one misplaced cast in `hcsr04.c`:
```c
out->dist_mm = (uint16_t)(us * 343u + 1000u) / 2000u;   /* v1.7 */
out->dist_mm = (us * 343u + 1000u) / 2000u;             /* v1.0 */
```
The cast applies to the numerator, not to the result, so `us × 343 + 1000` is cut to 16 bits before
the divide. Every real echo (116–23300 µs) comes out as 0–32 mm (65535 / 2000 = 32). The echo time
itself is untouched, so each `D` line contradicts itself: `us=5240` means 899 mm, but `mm=15`.
The release has no comment explaining the cast and a neutral commit message, so nothing but the code
change itself points at the bug.
`arm/tests/test_bad_image.py` compiles both lines with gcc and checks every echo value.

**What the logs show:**
```
critical us_mismatch - Ultrasonic distance disagrees with its own echo time: echo 5240 us means ~899 mm,
                       but the firmware reports 15 mm (20 of 20 samples wrong)
```
The arm's start gate (`arm/safe_replay.py`) refuses: `object at 13 mm (limit 200 mm)`, `NOT MOVING.`

**Investigation:** physical or firmware? An object at 15 mm would return an echo under ~90 µs, and the
echo says ~900 mm, so it isn't an obstruction (the rule also keeps `us_blocked` quiet when the echo is
long). `src_log` shows v1.7, and `src_diff rev_a=v1.0` shows only the cast changed.

**Fix (arm task):** `fw_rollback` to known-good, then `arm_test` to prove the gate opens. The harness's arm
rules make restoring service the fix, so the agent gets no edit or build tools in this task. The forward
fix (drop the cast, or cast the result) is a person's follow-up.

**Verify:** `chip_status` shows the known-good build, `us_mismatch` is gone, and `arm_test` passes.

**Talking point:** the sensor is fine and the arm is fine; a one-line integer truncation in C blocks the robot.
The deterministic rule tells "something is close" apart from "the firmware can't do arithmetic" by checking
the pod's two numbers against each other, with no model. The model then only has to read one diff and decide
to roll back.

---

## Physical faults on the known-good firmware (v1.0)

Start each with `demo/scenario reset`.

**P1. Cover the ultrasonic sensor** with a hand or card, flat against it.
- **Expected:** `us_blocked (info) - reads a constant ~20–30 mm`.
- **Action:** none. Treat distance as untrusted until it clears. The incident resolves by itself 10 s after
  the cover is removed.
- *Not yet exercised on the bench:* the rule's threshold (< 30 mm, std < 3 mm) is set from the sensor's
  minimum range *(inferred)*. Something on the bench currently reads 39 mm, which correctly doesn't trigger
  it.

**P2. Loosen the ECHO jumper and wiggle or shake the board.**
- **Expected:** `us_intermittent - drops out intermittently (N% no_echo), while the board is moving`. The
  rule correlates dropouts with accelerometer motion (|a| std > 40 mg).
- **Next:** `pod_cmd "us check"` measures the ECHO voltage at D3 (healthy: `result=ok echo_mv≈3108`).
- **Action:** `notify_human`: "Reseat the HC-SR04 ECHO wire (D3, through the 10k/18k divider)."
- *Not yet exercised on the bench.*

**P3. Unplug the LIS3DH** (VIN or SDA).
- **Expected:** `acc_error (nack or timeout)`. Ultrasonic and health keep running. That's the contrast with
  v1.6.
- **Next:** `pod_cmd "i2c lines"` / `"i2c scan"`.
- **Action:** `notify_human`.
- **On replug:** the 2 s retry re-initializes the sensor and the incident resolves.

**P4. Sensor glitch on v1.0:** `demo/scenario trigger 03` while v1.0 runs.
- **Expected:** no incident at all. The events show the firmware healing itself:
  `acc_cfg_lost ctrl1=0x07` → `acc_init why=config_lost result=ok` (tested).

---

## A 3-minute version for the judges

1. **Healthy baseline (15 s):** `heal telemetry_stats`. 1 g at rest, 10 Hz distance, link at 20%.
2. **Crash loop (60 s):** `scenario start 01`. Show the crash record, `fw_symbolize` pointing at the line,
   the odd store address in r3, the memcpy fix, and `fw_flash` verification.
3. **Silent data bug (45 s):** `scenario start 05`. Nothing crashes, but gravity reads 0.5 g. Show
   `acc regs` FS bits, the one-line fix and |a| back at 1 g.
4. **Knows what it can't fix (45 s):** `scenario start 06` and pull the SDA wire live. Watchdog loop, safe
   mode, timeouts restored so the pod survives, then `notify_human` and replug. *Rehearse this once: the
   physical wire pull and the replug recovery haven't been run on the bench yet (E1, C2). The simulated
   version (`demo/scenario trigger 06`) is tested.*
5. **Backup:** scenario 02, for the "three views agree" clock story.

## Notes and limits
- **Who runs what:** the operator uses `demo/scenario`; the agent uses `heal`. Fault injection is refused
  through `pod_cmd`.
- **What survives what:** the `.noinit` record (crash record, counters, inject flags) lives in the top 128 B
  of RAM, so it survives resets and reflashing. A power cycle (USB unplug) clears it.
- **Build IDs:** they come from `git describe` in the workspace. An agent fix shows up as
  `pod v1.6-1-g<hash>`, or `-dirty` if it's built before committing.
- **One serial user:** `podd` must be the only program on `/dev/ttyACM0`, so don't run `make monitor`
  alongside it. Use `tail -f healer/var/logs/serial.log` to watch.
- **Triage counts only abnormal boots** as a reboot loop (after a crash or watchdog reset, or power loss).
  Deliberate reflashes don't count.
- **The probe's serial bridge can go out of step after baud changes.** podd always switches to another
  rate and back when it opens the port or changes baud, so this is handled. If triage ever shows a link
  that drops characters mid-line, `healer/bin/heal link_set_baud baud=115200` forces a resync.
- **Before unplugging:** `demo/scenario reset`, then `healer/bin/heal chip_status`, and check that
  `version` is `v1.0`. podd isn't needed for this.
- **No decision loop yet:** see [HARNESS.md](HARNESS.md) for how to build one on these tools.
