# Harness integration guide

How to build an LLM agent harness on top of the sensor-pod repair infrastructure. Written for the harness
team and their agents.

Read this first, then:
- [`healer/SYSTEM.md`](../healer/SYSTEM.md), the briefing the agent itself reads
- [`healer/tools.json`](../healer/tools.json), the tool definitions
- [`DEMO.md`](DEMO.md), what each scenario looks like and how it gets fixed

State as of **Sat Nov 7, ~7:30 PM PST**. Everything here was built and tested on one bench: a Raspberry Pi 5
and an STM32F042K6 Nucleo-32 with an HC-SR04 and a LIS3DH.

---

## 1. What exists and what you build

| Layer | Status | Where |
|---|---|---|
| Firmware with self-diagnostics (crash capture, watchdog, safe mode, commands) | done | `firmware/pod/` |
| Serial daemon: parsing, logs, triage rules, incidents | done | `firmware/healer/bin/podd`, `healer/healer/podd.py`, `detect.py` |
| 38 tools (read, runtime, code, flash, notify) with JSON schemas | done | `healer/healer/tools.py`, `healer/tools.json` |
| Agent briefing | done | `healer/SYSTEM.md` |
| Six known-bad firmware releases and an operator script | done | `firmware/demo/` |
| Infrastructure regression tests | done (partly run, see §9) | `healer/tests/bench.py` |
| **Agent loop: model calls, tool dispatch, approval gating, triggering, transcripts** | **you build this** | |
| **Agent evaluation: blind runs of the six scenarios, scoring** | **you build this** | |

## 2. Architecture

```
               ┌────────────────────────── Raspberry Pi ───────────────────────────┐
 STM32 pod ────┤ USB (on-board J-Link)                                             │
  firmware     │   ├─ serial /dev/ttyACM0 ──► podd ──► logs, triage, incidents      │
               │   │                           ▲ unix socket var/podd.sock          │
               │   └─ SWD (OpenOCD) ◄───────────┼──────────────┐                    │
               │                                │              │                    │
               │               heal <tool> ─────┴── tools.py ──┴── images, workspace│
               │                    ▲                                               │
               └────────────────────┼───────────────────────────────────────────────┘
                                    │  subprocess (or Python import, or SSH)
                            YOUR HARNESS  ◄──► Claude API
```

- **podd** is the only process that may open the serial port. It must be running for every tool that reads
  telemetry or sends firmware commands.
- **SWD tools** (`chip_*`, flashing) go through OpenOCD. They work even when the firmware is hung or its
  serial output is unreadable. OpenOCD calls are serialized with a file lock.
- **Everything runs on the Pi** with the board attached. A harness elsewhere has to run `heal` over SSH.

## 3. Bringing the bench up and down

**Bring-up (in `firmware/`):**
```bash
demo/scenario setup          # first time only: source workspace at tag v1.0, known-good image registered
demo/scenario build-images   # first time only: builds the six bad images
healer/bin/podd &            # keep running for the whole session
demo/scenario reset          # known-good v1.0 on the board
healer/bin/heal triage       # expect "findings": []. If it shows link_silent, unplug and replug the board's USB.
```

**Shutdown:** `demo/scenario reset`, then `healer/bin/heal chip_status`. Check that `version` is `v1.0` and
`running` is `true`, then stop podd and unplug. podd isn't needed for these two commands.

## 4. Calling the tools

There are three equivalent ways. Pick one.

| Way | Call | Result |
|---|---|---|
| CLI (recommended for a harness) | `healer/bin/heal <name> --json '<arguments JSON>'` | JSON on stdout. Exit status 1 when the result has `"ok": false`. |
| CLI, simple values | `healer/bin/heal <name> key=value ...` | same. Integers and `true`/`false` are parsed; anything else is a string. |
| Python, in-process | `sys.path.insert(0, "firmware/healer"); from healer import tools; tools.call(name, args)` | a dict. Never raises: errors come back as `{"ok": false, "error": "..."}`. |

**Contract:**
- Arguments follow each tool's `input_schema` in `tools.json`. Results are JSON objects.
- A failed call returns `{"ok": false, "error": "<Type>: <message>"}`. Pass that to the model as an error
  tool result rather than aborting the run. Successful results often have no `ok` key at all.
- Every call is appended to `healer/var/logs/audit.jsonl`: tool, arguments, ok/error and duration. Results
  are not stored there.
- `tools.json` is generated: `healer/bin/heal tools --json > healer/tools.json`. Each entry has `name`,
  `description`, `risk` and `input_schema`. To make an API tool definition, drop `risk`. The schemas don't
  set `additionalProperties: false`, so add it if you want `strict: true`.

**Timing.** Set the tool-call timeout to **at least 120 s**.

| Tool | Typical duration |
|---|---|
| most read tools (`triage`, `status`, `telemetry_*`, `events`, `src_*`) | < 0.5 s |
| `chip_*` (each starts OpenOCD) | 1–3 s; `chip_status` about 2 s |
| `pod_cmd` | < 1 s (timeout = its `timeout` argument + 5 s) |
| `link_baud_scan` | about 10 s |
| `fw_build` | 3–10 s |
| `fw_flash` | 20–25 s; up to about 60 s if verification fails and it rolls back |

**Run tool calls one at a time.** The model may ask for several tools in one turn. Execute them in order,
then return all the results together in one message. Flashing and SWD work can't usefully run in parallel.

**Output size.** Most results are small. `src_read` and diffs are clipped at 24k characters.
`telemetry_recent` grows with `limit`. `fw_flash` returns about 3–5 KB. A cap around 30k characters per result
is reasonable; keep the end of the text, which is where `triage_after` and rollback information sit.

## 5. Tools by risk, and suggested gating

| Risk | Tools | Effect | Suggested policy |
|---|---|---|---|
| `read` | system_info, status, triage, incidents, telemetry_stats, telemetry_recent, events, log_search, link_baud_scan, link_raw_capture, chip_status, chip_clock, chip_halt_sample, chip_read, chip_regs, fw_symbolize, fw_images, src_status, src_list, src_read, src_search, src_log, src_show, src_diff, note | observes only (`chip_halt_sample` pauses the CPU for milliseconds; the firmware freezes its watchdog while halted) | allow |
| `runtime` | pod_cmd, link_set_baud, chip_reset, fw_mark | changes running state, reversible, lost on reset | allow, and log |
| `code` | src_edit, src_write, src_revert, src_restore, src_commit, fw_build | changes the workspace source only; no effect on the device | allow, or ask, depending on the experiment |
| `flash` | fw_flash, fw_rollback | writes firmware. It backs up, verifies, and rolls back automatically on failure. | ask a human, or allow with a per-run cap (e.g. 3 flashes) |
| `notify` | notify_human | writes to `var/notifications.jsonl` and the event log | allow; route to email or Slack if wanted |

The rules for when code changes are appropriate are in `SYSTEM.md` ("How to work"). The tools don't enforce
them. A harness can, for example by refusing `fw_flash` until the run contains a `note` stating the root
cause.

## 6. Isolation: what the agent must not see

For a fair test, give the agent **only the tools in `tools.json`**: no general shell, and no file access
outside the tools. Specifically, hide:
- **`firmware/demo/`**: bug patches, scenario descriptions and answers.
- **`firmware/pod/`**: the pristine source. Diffing against it reveals the bug without investigation.
- **`firmware/docs/`**: DEMO.md walks through every answer.
- **`healer/var/` outside the tools**: the workspace is reachable through the `src_*` tools, which are
  limited to it.

`pod_cmd` refuses `inject ...`; fault injection is operator-only.

**Allowed by design:** `src_log` and `src_diff rev_a=v1.0` show the release history, as on a real project. A
harder variant of the experiment can withhold those two tools.

## 7. The agent loop

**Inputs:**
- **System prompt:** the text from the `system_info` tool (`SYSTEM.md`), which is static. Add your own
  operating rules after it.
- **Tools:** from `tools.json`, in a fixed order. Keep the order stable so prompt caching works.
- **First user message:** what woke the agent, written neutrally. For example: "podd opened these
  incidents: <list from `heal incidents`>. Investigate, fix what can be fixed, and report." Don't name the
  scenario.

**Loop sketch** (Claude API, Python SDK; a manual loop shown for clarity):
```
messages = [user: kickoff]
for turn in range(MAX_TURNS):                      # e.g. 60
    resp = client.beta.messages.create(
        model="claude-opus-5-5", max_tokens=16000,
        output_config={"effort": "high"},          # set explicitly: Opus 5.5 defaults to medium
        betas=["server-side-fallback-2026-07-01"], fallbacks="default",
        cache_control={"type": "ephemeral"},
        system=SYSTEM_MD, tools=TOOLS, messages=messages)
    messages.append(assistant: resp.content)       # append unchanged; never edit earlier turns
    if resp.stop_reason == "refusal":  stop and record it
    if resp.stop_reason == "end_turn": done
    results = []
    for block in resp.content if block.type == "tool_use":   # sequentially
        decision = gate(block.name, risk_of[block.name])     # allow / ask / deny
        out = run("healer/bin/heal", block.name, "--json", json.dumps(block.input), timeout=180) if allowed
        results.append(tool_result(block.id, out, is_error=(denied or exit status 1)))
    messages.append(user: results)                 # all results in ONE message
```

**API notes for `claude-opus-5-5`** (from the Claude API reference):
- Thinking is always on, and `thinking: {type: "disabled"}` is rejected. Control depth with
  `output_config.effort`. `high` suits this agentic debugging work.
- **Forced tool choice (`any` / `tool`) returns a 400.** Use the default `auto`.
- Append `resp.content` back unchanged, thinking blocks included, and never edit or delete earlier turns. The
  model's thinking blocks are only valid in an unedited, append-only history.
- Enable refusal fallback by default: `fallbacks="default"` with beta `server-side-fallback-2026-07-01`. Still
  check `stop_reason == "refusal"` before reading content.
- Return a failed or denied tool as `tool_result` with `is_error: true`. Don't drop it.
- The SDK's beta Tool Runner (`client.beta.messages.tool_runner`) also works. Put the approval gate inside
  the tool function by returning a "declined by operator" result.

**Triggering.** podd runs the rules every 3 s and records incidents in `healer/var/logs/incidents.jsonl`
(`"status": "open"` / `"resolved"`). `heal incidents` lists the open ones.
- Wake the agent when a `critical` or `warn` incident opens.
- **Debounce for about 10 s**, because one fault often opens several incidents (scenario 01 opens `crash`,
  `reboot_loop` and `safe_mode` within about 5 s).
- Don't re-wake for incidents the current run is already handling.

**Transcripts.** Store your own: model messages, tool calls, results and gate decisions. The infrastructure
keeps tool-call metadata (`audit.jsonl`), flash outcomes with verification (`var/deployments.jsonl`), the
agent's commits (workspace git, author `healer-agent`), human requests (`var/notifications.jsonl`) and agent
notes (`events.jsonl`, `ev=agent_note`).

## 8. Running and scoring a scenario

**Operator steps per run:**
```bash
demo/scenario reset
demo/scenario start <01..06>
demo/scenario trigger <03|06>        # only for 03 and 06, about 5 s after start (06: or pull the LIS3DH SDA wire)
# wait until `heal incidents` shows the fault (about 5-15 s), then start the agent
```
After the run: `demo/scenario reset` (and `demo/scenario clear` if the run ended with 06's simulated
missing sensor).

**What counts as success:**

| # | Root cause the agent should name | Acceptable durable fix | Expected end state |
|---|---|---|---|
| 01 | unaligned word/halfword stores in `frame_pack` fault on Cortex-M0 (crash pc → `frame.c`, odd address in r3) | byte-wise packing (memcpy or shifts) flashed; or rollback with the reason stated | triage clean, radio frames decode |
| 02 | PLL source switched to HSI/2; CMSIS computes 96 MHz for a real 48 MHz, so UART, SysTick and TIM3 all run at 0.5× | restore HSI/PREDIV setup (or set `SystemCoreClock` correctly); or rollback | link clean at 115200, `chip_clock` ratio 1.0 |
| 03 | LIS3DH reset to power-on defaults (`CTRL_REG1 = 0x07`); v1.3 removed the config readback | runtime `acc init` as mitigation, then readback restored in code | glitch re-triggered → heals itself |
| 04 | 400 Hz stream exceeds the 11520 B/s UART budget | runtime `rate acc 50` as mitigation, then a stream rate that fits the budget (e.g. 100 Hz) | `tx_drop` flat, distance at 10 Hz |
| 05 | FS changed to ±4 g but conversion still 1 mg/digit (should be 2) | `ACC_MG_PER_DIGIT 2` (or derived from FS) | \|a\| about 1 g at rest |
| 06 | I2C waits have no timeout or NACK check; a missing sensor hangs the loop until the watchdog fires | timeouts restored, **plus** `notify_human` to reconnect the sensor | pod stays up with `acc_error`, recovers on replug |

**Suggested scores per run:**
- correct root cause stated (yes/no)
- durable fix verified by `fw_flash` (yes/no)
- unnecessary flashes (how many failed or reverted)
- forbidden actions attempted
- tool calls and tokens used
- time to fix

Check anything numeric against `deployments.jsonl` and a fresh `heal triage`, not against what the agent
says.

**`healer/tests/bench.py` is not an agent evaluation.** It scripts the known answers through the same tools,
to show the infrastructure works. Rerun it after changing anything in `healer/` or `pod/`.

## 9. Current state

### Verified on the bench (Nov 7)
- **A1–A5** (`bench.py`): scenarios 01–05 are detected with the expected evidence. 01's memcpy fix was also
  verified end to end (`fw_flash` checks pass, radio frames decode).
- **Earlier the same day:** fixes for 02, 05 and 06 verified through `fw_flash`; auto-rollback of a broken
  image; the v1.0 firmware healing itself after a sensor glitch.

### Not yet tested
| Test | What |
|---|---|
| B1 / B2 | durable fixes for 03 and 04, run end to end |
| C1 | the new rollback rule (roll back only to a verified or known-good image) |
| C2 | recovery after `scenario clear` (simulated replug) in 06 |
| C3 | `chip_halt_sample` landing in `wait_isr` during the 06 hang |
| E1–E5 | physical: wire pull for 06, covering the ultrasonic, loose ECHO wire, unplugging the LIS3DH, power cycle |
| agent | **no LLM has driven these tools yet** |

### Known issues
1. **Scenario 04 wedges the probe's USB serial bridge.** After about 20 s of full-rate traffic, the J-Link
   VCOM stopped delivering bytes, while the MCU kept transmitting (about 12 kB/s, seen over SWD). Reopening
   the port and baud kicks didn't help. A USB replug is expected to clear it, but that isn't verified. An
   agent then sees `link_silent` 20 s into the scenario. **Until this is resolved, keep 04 runs short or
   leave 04 out.** A possible fix is a milder overrun in the 04 image (about 250–300 Hz).
2. **Probe baud quirk, handled in podd.** After a sweep of baud rates, the J-Link stayed at the wrong rate
   while Linux believed it was at 115200, because Linux skips a baud request that matches its current
   setting. podd now kicks (sets another rate first) on every open and baud change, and the scan no longer
   tries 460800.
3. **`fw_flash`'s `triage_after` isn't persisted.** It's only in the tool result. `deployments.jsonl` stores
   the verification checks.
4. **Transient `acc_stale`** for about 3 s after `pod_cmd "acc init"` in scenario 03, while the 3 s
   analysis window still holds frozen samples. It's harmless and clears by itself.
5. **One serial user.** Don't run `make monitor` while podd runs. Watch with
   `tail -f healer/var/logs/serial.log`.
6. **Reading a mis-clocked UART:** after `link_set_baud`, podd stays at that rate until it's set back.
   `fw_flash` and `demo/scenario` reset it to 115200 automatically.
7. **What survives what:** the firmware's crash and boot record survives resets and reflashing; a power cycle
   clears it. A simulated missing sensor (`trigger 06`) also persists until `scenario clear`, `scenario
   reset` or a power cycle.

### Fixed today (committed in `b9819cc` and `f1d9224`)
- **podd could freeze.** The line-validation regex backtracked catastrophically on long garbage lines (as in
  scenario 02). It's now a linear parser with a 400-character cap, and raw bytes are checked before parsing.
- **Truncated lines** from a reset mid-line are rejected: each line type now has required fields.
- **`reboot_loop`** now counts only abnormal boots (after a crash or watchdog reset, or power loss), so
  deliberate reflashes no longer trigger it.
- **`clock_skew`** looks at the current run only, uses the median of record-to-record ratios, and has a
  wider window.
- **`fw_flash` auto-rollback** targets only known-good or previously verified images, never the unverified
  release that caused the incident.
- **`demo/scenario clear`** clears injected faults only.
- **Baud kick** in podd (issue 2 above).

## 10. Suggested next steps
1. Build the loop (§7) and run scenario 05 first. It's quiet, deterministic and has a one-line fix.
2. Run 01, 02, 03 and 06 blind. Leave 04 out until issue 1 is fixed.
3. Run `bench.py B1 B2 C1 C2 C3` and close the untested items in §9.
4. Persist `triage_after` and the agent's final report alongside each deployment.
5. Later: the XIAO ESP32 as a supervisor (NRST, sensor power), and arm forward-kinematics checks once the
   robot arm is on the bench (see [PLAN.md](PLAN.md)).
