# Self-healing sensor pod: plan

This is the plan from the brainstorming session on Sat Nov 7. It's built from first principles around the
hardware on hand: STM32F042K6 (Nucleo-32), HC-SR04 ultrasonic sensor, LIS3DH accelerometer and a
XIAO ESP32, plus a LeRobot-driven SO-101 arm. An LLM diagnoses problems and applies fixes, including
flashing new firmware.

> **What changed after this plan was written**
> - There is no robot arm on the bench for now, so the demo uses **physical invariants** in place of the
>   arm's forward kinematics (FK):
>   - at rest, an accelerometer reads |a| = 1 g
>   - a real sensor always has a noise floor, so zero variance means frozen data
>   - the MCU's clock must agree with wall-clock time
>   - the UART byte budget must cover the telemetry rate
>
>   The FK residuals below are still the target once the arm is available *(inferred)*.
> - The bad firmware images became the six scenarios in `demo/bad-images/`. They're explained in
>   [DEMO.md](DEMO.md).
> - The I2C bus-lockup scenario (F) became a firmware feature (bus recovery at boot plus an
>   `i2c recover` command) rather than a bad image. The STM32's I2C peripheral can clock a stuck slave
>   free on its own, so the failure isn't deterministic enough to demo *(inferred)*.

## 1. Give the pod a job first

A failure only matters if the arm depends on what failed. Mount the STM32 pod on the wrist bracket:

- **HC-SR04** points along the gripper's approach axis. It measures the height above the table or object
  before the gripper closes.
- **LIS3DH** sits on the same bracket. It measures the gravity vector, which gives wrist orientation, plus
  vibration and impacts.

LeRobot already knows the joint angles. Forward kinematics (FK) predicts both quantities the pod measures:
the wrist's height above the table, and the direction of gravity in the wrist frame. That gives two
independent measurements of each physical quantity. Their difference (the *residual*) is the main
diagnostic signal:

| Residual | Points to |
|---|---|
| Near zero | everything agrees |
| Constant scale factor | a units or clock bug |
| Constant offset | calibration |
| Frozen value | a stuck sensor |
| Dropouts that correlate with motion | a cable |

One limit: rotation about the vertical axis (shoulder_pan) can't be seen by an accelerometer, because it
doesn't change the gravity vector in the wrist frame.

## 2. Failure scenarios

⭐ marks the ones planned for the demo.

### ⭐ A. Bad firmware update causes a crash loop
- **How to create it:** flash an update that faults on the Cortex-M0 (stack overflow, or an unaligned
  access). You get a HardFault, then the watchdog resets the chip, over and over.
- **What the logs show:** the reset cause is IWDG or a software reset, and the boot counter keeps climbing.
  A crash record survives the reset with the stacked PC, LR and SP. The Cortex-M0 has no CFSR or HFSR
  fault-status registers, so the stacked PC is the only evidence you get.
- **How the LLM diagnoses it:** it runs `addr2line` on the PC against the archived ELF for that build ID.
  The build ID changed exactly when the crashes started.
- **Fix:** roll back to the last known-good image.
- **Verify:** the build ID check passes, and the pod stays healthy for 30 s with no new resets.

### ⭐ B. Clock bug: the serial output is garbage but the CPU is alive
- **How to create it:** use the HSI/2 PLL source. This is the CMSIS quirk we hit on this board:
  `SystemCoreClock` reads 96 MHz for a real 48 MHz, so USART2 runs at half its baud rate.
- **What the logs show:** the serial port produces garbage, but SWD reads still work and `g_uptime_ms` keeps
  advancing.
- **How the LLM diagnoses it:** it re-reads the port at other baud rates. At 57600 the text is clean, so the
  clock is off by exactly 2×, which means the clock tree is misconfigured.
- **Fix:** roll back, or fix the line that changed (`git diff` between the two build revisions).

### ⭐ C. The LIS3DH silently resets while the MCU keeps running
- **Why it happens:** the sensor and the MCU have different brownout thresholds. After a reset, the LIS3DH
  comes back with `CTRL_REG1` (0x20) at its default of 0x07, which is power-down mode.
- **What the logs show:** the acceleration values are frozen with exactly zero variance. A real MEMS sensor
  always has a few mg of noise, so zero variance means the data is stale.
- **How the LLM diagnoses it:** `WHO_AM_I` (0x0F) still reads 0x33 but `CTRL_REG1` reads 0x07, so the sensor
  reset and wasn't reconfigured.
- **Fix:** run `acc init` right away. Then flash the permanent fix: firmware that reads the config registers
  back every second and rewrites them if they've changed.

### ⭐ D. Servo gain causes oscillation (needs the arm)
- **What the logs show:** a narrow-band peak in the accelerometer spectrum at roughly 5–20 Hz while a joint
  is holding position.
- **Fix:** write `P_Coefficient` back to its stored baseline. The action uses a whitelist of registers and
  allowed bounds.

### ⭐ E. Physical faults the system can't fix itself

| What's happening | How it shows up |
|---|---|
| Sensor covered | Reads a constant ~20 mm with status `ok`. The sensor is blocked, not dead. |
| Intermittent cable | `no_echo` bursts that line up with motion the LIS3DH sees |
| Sensor dead | `no_echo` all the time, and the wiring self-test fails |

The system makes things safe, emails a specific instruction ("reseat the ECHO wire at D3"), and resumes
when the readings recover.

### F. I2C bus lockup (backup)
If the MCU resets during a read while the LIS3DH is driving a 0 bit, the sensor holds SDA low. The fix
ladder is: 9-clock bus recovery → power-cycle the sensor through the XIAO → ask a human.

## 3. STM32 firmware

1. Tagged telemetry lines (`D`, `A`, `H`, `E`, `R`, `F`), with `#` lines as comments.
2. LIS3DH driver on I2C1, PB6 (SCL) and PB7 (SDA), address 0x18, with periodic config readback.
   A4/A5 on the Nucleo-32 reach PB7/PB6 only through solder bridges SB16/SB18, so PA5/PA6 must stay inputs.
3. A health record once a second: uptime, reset cause, boot count, build ID, VDDA, die temperature, the
   longest main-loop iteration, stack high-water mark, I2C errors and sensor status.
4. Crash capture: the HardFault handler saves the stacked registers to `.noinit` RAM.
5. Independent watchdog (IWDG) with a "last checkpoint" kept in `.noinit`.
6. Safe mode on boot loops.
7. Line-based commands, plus `inject <fault>` behind a build flag.
8. Persistent config in flash *(not built yet; runtime config only)*.
9. Known-bad images for the demo.

**XIAO ESP32 (optional, add last):** use it as a supervisor that drives NRST and power-cycles the sensors.
Telemetry relay and reflashing over the STM32's ROM UART bootloader (AN3155) are stretch goals.

## 4. Pi-side harness

- **`podd` daemon:** the only process that opens the serial port. It parses lines, keeps a ring buffer and
  writes JSONL logs.
- **Fixed-rule detectors (no LLM):** they raise incidents with evidence attached.
- **Firmware image registry:** every build is stored by build ID: ELF (kept for symbols), bin, git revision
  and status.
- **Read-only tools, action tools and code tools:** each action has a verify step and an undo.
- **Flashing sequence:** back up the chip → flash → check the build ID → watch health. If health doesn't
  recover, roll back automatically.
- **Audit log** of each incident → hypothesis → action → verification.

## 5. Build order
1. Telemetry, health, reset cause, crash capture, IWDG
2. LIS3DH driver, `inject` commands, bad images
3. `podd`, image registry, flash and rollback with verification
4. Detectors
5. LLM tool schemas (a decision harness comes later)
6. FK residuals once the arm is available
7. XIAO supervisor
