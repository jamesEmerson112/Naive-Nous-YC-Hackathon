# Firmware APIs and data: how the sensor pod plugs into Romulus, the harness and the arm

_Written Sun Nov 8, about 12:15 AM PST, from the code on `main` at `2f56e2d`._
- Every fact cites `path:line` on that commit.
- *(inferred)* means it was read from the code but not seen on the bench.
- Olir owns the firmware. Olir, please review this: where it disagrees with your code, the code wins and this doc gets fixed.
- Section 15 lists what is still unwired. Section 16 covers the data we have and the data on its way.

## 0. The map

```
 STM32 pod (firmware/pod)
   │  USART2 115200 8N1, one "TYPE key=value" line per record      ▲ "@<id> <command>" lines
   ▼                                                               │
 podd (firmware/healer/bin/podd): the ONLY process on the pod's serial port
   ├── var/podd.sock: 11 JSON ops ─► heal tools (firmware/healer/bin/heal <tool> --json …)
   │                                   └─► pod healer harness (firmware/harness/agent.py)
   │                                         └─► model: Ollama on the laptop, relayed by server/serve.py
   ├── var/logs/*.jsonl + var/*.jsonl ─► edge bridge (tools/edge_bridge) ─HTTP, contract v0.1─► Romulus (cloud/)
   └── recent D rows ─► arm/safe_replay.py today; our arm host next (section 15)

 SO-101 servo bus (LeRobot): its own USB serial port, which must never be the pod's port (section 13)
```

## 1. Where to look

| I want to… | Section |
|---|---|
| read distance or acceleration in my own code | 4 (`recent`, `stats`) and 2 (fields and units) |
| send the pod a command | 3, and 4 (`cmd`) |
| know when something broke | 6 (findings and incidents) |
| repair the pod from an agent | 7 (heal tools) and 9 (harness) |
| get pod faults into Romulus | 11 (edge bridge and contract) |
| work with the logs offline | 5, and 16 (data) |

## 2. Pod serial wire format
- **Link:** USART2, 115200 baud, 8N1 (`firmware/pod/src/main.c:30,308`; default `POD_BAUD` in `firmware/healer/healer/config.py:20`). That gives a byte budget of 11,520 B/s (`config.py:28`).
- **Lines:** `<TYPE> key=value …`. A value is a bare word or `"quoted"` (`firmware/pod/src/tl.h:3-4`, `tl.c:62-69`). The pod ends each line with `\r\n` (`tl.c:91`, `uart.c:80`). podd splits on `\n` and strips the `\r` (`firmware/healer/healer/podd.py:160,167`).
- **Value types on the host:**
  - All digits become an int.
  - `d.d` becomes a float.
  - Anything else stays a string, so `0x08002B9A` is a string (`proto.py:15-22`).
  - podd stamps every record with `ts`, the host's epoch seconds (`podd.py:181`).

| Type | Fields (units and values) | Rate | Source |
|---|---|---|---|
| **D** distance | `t` MCU ms · `mm` = (us·343+1000)/2000 · `us` echo µs (both 0 without an echo) · `st` = `ok` / `out_of_range` (echo <116 or >23300 µs) / `no_echo` / `busy` | 10 Hz | `main.c:342-347`, `hcsr04.c:122-140`, `hcsr04.h:18-26` |
| **A** accel | `t` · `x` `y` `z` mg · `st` = `ok` / `stale` | 50 Hz | `main.c:137-143`, `lis3dh.c:32-36` |
| **F** radio frame | `t` · `hex` (32 hex chars) | 1 Hz, while sensors are on | `main.c:170-181,359-362` |
| **H** health | `t` `mode` (normal/safe) `boots` `streak` `crashes` `wdg` `rst` `loop_us` `stk_free` `vdda` (mV) `temp` (°C) `acc` `acc_errs` `acc_cfg_lost` `i2c_err` `us` (last D `st`) `tx_drop` `rx_ovr` `clk` (Hz) `acc_hz` | 1 Hz, and on `health` | `main.c:202-232` |
| **E** event | `t` `ev` + per-event fields | on event | below |
| **R** reply | `id` + reply fields; last line is `end=1 ok=1` or `end=1 ok=0 err="…"` | per command | `tl.c:95-99`, `cmd.c:42-57` |
| **#** text | boot banner, `# build <id>`, legend | at boot | `main.c:241-243,296` |

**E events:**
- `boot`: `build`, `crc=0x…`, `rst`, `boots`, `streak`, `crashes`, `wdg`, `last_cp`, `mode`, `new_build`, `clk` (`main.c:245-260`).
- `crash`: `build_crc`, `pc`, `lr`, `sp`, `xpsr`, `r0`–`r3`, `r12`, `up`, `cp` (`main.c:264-280`).
- `watchdog_reset` (`main.c:283-287`).
- `safe_mode`: `reason` = boot_loop or forced (`main.c:290-295`).
- `acc_err` (`main.c:86-96`), `acc_cfg_lost` (`main.c:158-165`), `acc_init` (`main.c:107-112`).

**Parsing:**
- Each type has required fields: D `t,mm,us,st` · A `t,x,y,z,st` · F `t,hex` · H `t,mode,acc_hz` · E `t,ev` · R `id` (`proto.py:7-8`).
- `parse_line` returns `None` for empty lines, lines over 400 characters, an unknown type, stray text, or a missing required field (`proto.py:25-47`).
- podd also rejects non-printable bytes (`podd.py:172`). A bad line still lands in `serial.log` and counts against the link's valid ratio (`podd.py:171-178`).

**Dropped lines:**
- When the pod's TX ring has less than 112 bytes free, it drops whole D, A and F lines and counts them in `H.tx_drop`.
- H, E and R lines wait for space instead (`tl.c:4,15-24`).

**F frame decode** (`proto.py:57-71`, layout in `frame.h:4-13`): `{type:"0xA1", t_ms, mean_x, mean_y, mean_z (mg), rms_mg, n, crc_ok}`. The checksum is CRC-8, poly 0x07.

## 3. Pod commands
- podd sends `@<id> <line>`, with ids counting up from 1001 (`podd.py:82,215,220`).
- The pod takes at most 6 arguments and 79 characters, `@id ` included (`cmd.c:12,368-386,422-426`).
- Runtime settings are lost on reset (`firmware/healer/SYSTEM.md:73`).

| Command | What it does | Reply | `cmd.c` |
|---|---|---|---|
| `help` | lists commands | cmds, sensors, i2c | 61-78 |
| `info` | build, crc, clocks, mode, boots | many fields | 80-113 |
| `health` | sends an **H line, not an R** | none | 392 |
| `stats` | I2C, UART and sensor counters | many fields | 115-144 |
| `crash [clear]` | the last crash record | has, pc, lr, … | 146-180 |
| `i2c scan` / `r` / `w` / `lines` / `recover` | bus tools | `data` as hex with no 0x | 186-220 |
| `acc regs` / `init` / `on` / `off` | accelerometer | registers / end | 228-260 |
| `us check` | ECHO-voltage self-test (about 150 ms) | `result` = ok, warn_low_level, danger_divider_missing, no_echo_pulse or echo_stuck_high | 268-279 |
| `us on` / `off` | D stream on or off | end | 280-282 |
| `stream a\|d\|f on\|off` | one stream on or off | end | 288-297 |
| `rate acc 1–500` / `rate us 1–16` | sample rate in Hz | end | 299-307 |
| `safe on` / `off` | `on` resets into safe mode; `off` starts the sensors | end | 311-322 |
| `reset` | replies, then resets | end | 404 |
| `inject hardfault\|hang\|acc_glitch\|acc_gone\|clear` | fault injection (`FAULT_INJECT ?= 1`, `firmware/pod/Makefile:20`) | end | 328-363 |

## 4. podd: the socket API
- **Start:** `firmware/healer/bin/podd [--port] [--baud]`.
  - It exits if another podd already answers `ping` (`podd.py:478-483`).
  - It runs a reader thread and a triage thread (`podd.py:486-487`).
- **Environment** (`config.py:11-22`): `HEALER_VAR` (default `firmware/healer/var`), `POD_PORT` (default `auto`), `POD_BAUD` (115200), `POD_PROBE` (jlink), `SWD_KHZ` (1000).
- **Port resolution with `POD_PORT=auto`**, first hit wins (`serialport.py:8-28`):
  1. `/dev/serial/by-id/*` links whose name matches a debug probe (SEGGER, J-Link, STMicro, ST-LINK, DAPLink, CMSIS-DAP, Debug_Probe);
  2. any `/dev/ttyACM*`;
  3. any `/dev/cu.usbmodem*`.

  Step 2 can grab the arm's servo adapter (section 13).
- **Reconnects:** it retries every 1 s and reopens the port on errors (`podd.py:129-147`).
- **`var/`** (gitignored, `firmware/healer/.gitignore:1`): `logs/`, `images/`, `backups/`, `workspace/pod/` (a git repo), `podd.sock`, `swd.lock`, `deployments.jsonl`, `notifications.jsonl` (`config.py:11-17`, `images.py:132`, `tools.py:658`).
- **Socket framing:** an AF_UNIX stream at `var/podd.sock`. You send one JSON object and a newline, get one JSON line back, and the socket closes (`podd.py:453-462`).
  - The Python client is `healer.client.call(op, **fields)` (`firmware/healer/healer/client.py:12-30`). It raises `PoddUnavailable` when podd is down (`client.py:19`).
  - Errors come back as `{"ok":false,"error":"…"}` (`podd.py:450,460-461`).

| op | Request (defaults) | Response | Side effects | `podd.py` |
|---|---|---|---|---|
| `ping` | – | `{ok:true}` | – | 428 |
| `status` | – | `link`, `last_boot`, `last_health`, `health_age_s`, `open_incidents[]`, `uptime_s` | – | 430, 417-424 |
| `cmd` | `line` (required, 1–78 chars), `timeout` 2.0 | `command`, `responses[]` (R fields + `ts`), `ok`, `error` | writes to the pod, one command at a time | 432, 210-232 |
| `recent` | `kind` "E" (A D F H E R # raw), `seconds` 10, `limit` 50 | `rows[]`, the newest `limit` | – | 434, 358-365 |
| `stats` | `seconds` 10 | `accel{n, rate_hz, x/y/z{mean,std,min,max}, magnitude_mg, …}`, `distance{n, rate_hz, mm_ok, status}`, `health_latest`, `radio_frame_latest`, `events` (last 20) | – | 436, 316-356 |
| `triage` | – | `findings[]`, `link` | – | 438, 274-277 |
| `incidents` | – | `open[]` | – | 440-441 |
| `raw` | `seconds` 2 (max 10), `baud` | bytes, printable and valid ratios, `text_head` | **pauses parsing and logging while it runs** | 442, 367-391 |
| `baud_scan` | – | per-baud results, `best_baud` | same pause for about 7 s; may open `link_silent` *(inferred)* | 444, 393-406 |
| `set_baud` | `baud` (required) | `baud`, `previous` | sticks until set back | 446, 408-415 |
| `note` | `ev` "note", `fields` {} | `event` | writes a host event to `events.jsonl` | 448, 96-101 |

- **`link` object** (`podd.py:234-261`): `state` is `down` (port not open), `silent` (no bytes for over 3 s), `garbled` (valid ratio under 0.5), or `up`. It also carries `bytes_per_s`, `valid_ratio` and `silent_s`.
- **In-memory rings** (`podd.py:30,76`): A 3000, D 600 (60 s at 10 Hz), F 300, H 900, E 2000, R 500, # 400, raw 300.

## 5. podd logs and other on-disk records

| File | Records | Rotation |
|---|---|---|
| `var/logs/telemetry.jsonl` | A, D, F | 20 MB × 5 (`podd.py:31,46,87`) |
| `var/logs/health.jsonl` | H | 20 MB × 5 |
| `var/logs/events.jsonl` | E, R, #, host events | 20 MB × 5 |
| `var/logs/incidents.jsonl` | incident open and resolved | 20 MB × 5 (`podd.py:87-88`) |
| `var/logs/serial.log` | every raw line, `<local ISO time> <line>` | 10 MB × 3 (`podd.py:89,171`) |
| `var/logs/audit.jsonl` | one row per heal-tool call | never rotates (`tools.py:683-695`) |
| `var/deployments.jsonl` | `{ts, crc, result: ok\|failed\|unverified, build_id, reason, backup, checks[]}` | never (`images.py:129-134`) |
| `var/notifications.jsonl` | `{ts, severity, summary, actions}` | never (`tools.py:657-659`) |
| `var/images/<CRC>/` | `pod.elf`, `pod.bin`, `meta.json` (crc, build_id, version, status, origin, …), maybe `source.patch` | – (`images.py:46-77`) |

Records are compact JSON, one per line (`podd.py:94`). Examples, built from the code:
- **Telemetry:** `{"type":"D","t":66804,"mm":49,"us":286,"st":"ok","ts":1794105848.03}` (the shape of `firmware/ultrasonic-sample.json`).
- **Host event:** `{"ts":…,"type":"host","ev":"port_open","port":"/dev/ttyACM0","baud":115200}` (`podd.py:97,113`).
- **Incident open:** `{"ts":…,"status":"open","id":"acc_frozen-1762574400","kind":"acc_frozen","opened":…,"last_seen":…,"severity":"critical","summary":"…","first_evidence":{…}}` (`podd.py:296-300`).
- **Incident resolved:** `{"ts":…,"status":"resolved","id":…,"kind":…,"duration_s":42.0}` (`podd.py:308-309`).
- **Host event names:** `port_open`, `port_closed`, `link_state`, `incident_open`, `incident_resolved`, `triage_error`, `baud_changed`, `podd_start`, `podd_stop`. Heal tools add `flash_start`, `flash_done`, `chip_reset`, `notify_human` and `agent_note` (`tools.py:352,608,614,660,667`).

## 6. Triage findings and incidents
**Finding shape**, the same everywhere: `{kind, severity: critical|warn|info, summary, evidence{}, hypotheses[], next_steps[]}`, sorted with critical first (`firmware/healer/healer/detect.py:3-9,23-25,279-280`).

| kind | Severity | Fires when (`detect.py`) |
|---|---|---|
| `link_down` / `link_silent` / `link_garbled` | critical | link state (39-60) |
| `reboot_loop` | critical | 2 or more abnormal boots in 60 s on the current build (67-85) |
| `crash` | critical | a crash event in 120 s on the current build (76, 86) |
| `watchdog` | critical | a watchdog reset in 120 s (78, 97) |
| `safe_mode` | critical | the latest H has `mode=safe` (105) |
| `acc_error` | warn | the accelerometer reports errors and no A samples arrive (121-122) |
| `acc_frozen` | critical | 20 or more A samples in 3 s, and x, y and z all have **exactly zero** variance (133-142) |
| `acc_stale` | warn | over 50% stale A samples (150) |
| `acc_gravity` | warn | \|a\| is steady but more than 150 mg away from 1 g (154-158) |
| `tx_overrun` | warn | `tx_drop` rising (171-174) |
| `clock_skew` | critical | MCU time runs more than 5% off host time (191-204) |
| `us_no_echo` | warn | over 80% `no_echo` in 4 s (216-222) |
| `us_intermittent` | warn | 5–80% `no_echo` (227-230) |
| `us_blocked` | info | ok readings under 30 mm with std under 3 mm (239-241) |
| `loop_slow` / `stack_low` / `supply` | warn | `loop_us` >20 ms / `stk_free` <256 / `vdda` outside 3.0–3.6 V (255-263) |

**No ultrasonic "frozen" rule exists.** A distance stuck at one value of 30 mm or more fires nothing, and `out_of_range` has no rule (`detect.py:214-246`). Our sim's watchdog has one, mirroring `acc_frozen`: zero `us` variance over 30 or more rows in 4 s (`prototypes/arm-sim/armsim/level1.py`, branch `james/arm-sim`). It is a few lines to port into `check_ultrasonic`.

**Incidents** (`podd.py:279-314`):
- **Triage loop:** every 3 s (`TRIAGE_PERIOD_S`), skipped for the first 5 s after start.
- **One open incident per `kind`.** Its id is `<kind>-<epoch>`. Repeats update `last_seen` and `summary`.
- **Resolve:** once the kind has been unseen for over 10 s (`RESOLVE_AFTER_S`).
- **No debounce in podd.** The harness debounces 10 s (`firmware/docs/HARNESS.md:177-180`).
- **Restarts:** a podd restart forgets open incidents and writes no resolved record *(inferred, `podd.py:84`)*.

## 7. Heal tools (the repair surface)
- **CLI** (`firmware/healer/healer/cli.py:1-56`):
  - Forms: `healer/bin/heal <tool> --json '<object>'` or `heal <tool> k=v …`; `heal tools --json` lists them.
  - Output is JSON on stdout. The exit code is 1 only when the result says `ok:false`.
  - Errors look like `{"ok":false,"error":"…"}`.
- **38 tools in 5 risk tiers** (`firmware/healer/README.md:42-46`, `tools.py:3-8`; schemas in `firmware/healer/tools.json`):
  - **read (25):** `system_info`, `status`, `triage`, `incidents`, `telemetry_stats`, `telemetry_recent`, `events`, `log_search`, `link_baud_scan`, `link_raw_capture`, `chip_status`, `chip_clock`, `chip_halt_sample`, `chip_read`, `chip_regs`, `fw_symbolize`, `fw_images`, `src_status`, `src_list`, `src_read`, `src_search`, `src_log`, `src_show`, `src_diff`, `note`.
  - **runtime (4):** `pod_cmd` (one command; refuses `inject`), `link_set_baud`, `chip_reset`, `fw_mark`.
  - **code (6):** `src_edit`, `src_write`, `src_revert`, `src_restore`, `src_commit`, `fw_build`.
  - **flash (2):** `fw_flash`, `fw_rollback`.
  - **notify (1):** `notify_human`.
- **Audit:** every call appends to `var/logs/audit.jsonl` as `{ts, tool, args, ok, duration_s}`. There, `ok` means "didn't raise", not the result's `ok` (`tools.py:683-695`).
- **The `inject` block is tool-level only.** podd's own `cmd` op passes `inject` through (`podd.py:210-232`).

## 8. Images, flashing and rollback
- **Image id:**
  - The build ID is `"pod <git describe> <UTC time>"`, stored in flash in `.fw_info` (`firmware/pod/src/fw_info.h:12-19`).
  - The CRC is the CRC-32 of that string, written `%08X`. The pod prints the same value as `crc=0x…` at boot (`images.py:22-23`, `diag.c:47,83`).
  - An image is referred to by a CRC prefix, `known-good`, `latest`, `deployed`, or an `.elf` path (`images.py:92-116`).
- **`fw_flash`** (`tools.py:582-639`):
  1. Refuses an image marked bad.
  2. Backs up the 32 KB of flash.
  3. Programs it over SWD (OpenOCD, "Verified OK").
  4. Checks the build ID in flash, a boot line with that CRC within 8 s, then 15 s with no reboots, health flowing and `mode=normal`.
  5. Writes `deployments.jsonl`.

  On failure it marks the image bad and can roll back automatically. With podd down, the result is `unverified`.
- **`fw_rollback`** = `fw_flash(to="known-good")`, with no automatic rollback of its own (`tools.py:642-645`).
- **The bad images** (`firmware/demo/bad-images/*/scenario.json`). Each has a `scenario.json`, but none of the telemetry it produces is committed:

  | # | Title | Trigger |
  |---|---|---|
  | 01 | HardFault crash loop from unaligned stores | none (crashes about 1 s after boot) |
  | 02 | Wrong clock: UART at half baud, time and distances halved | none |
  | 03 | Accelerometer silently resets, never recovers | `inject acc_glitch` |
  | 04 | Telemetry over the UART budget: lines dropped | none (the harness refuses it) |
  | 05 | Accelerometer reads 0.5 g at rest | none |
  | 06 | Sensor unplugged → I2C hang → watchdog loop → safe mode | pull SDA/VIN, or `inject acc_gone` |

  **There is no image 07 (an ultrasonic fault) yet.**

## 9. The pod healer harness (`firmware/harness`)
**CLI.** `agent.py run|watch` (`agent.py:525-543`):

| Flag | Default |
|---|---|
| `--driver ollama\|claude` | `ollama` |
| `--model` | `qwen3.5:4b` / `claude-opus-5-5` |
| `--tools all\|core\|<list>` | `all` |
| `--ollama-url` | `http://localhost:11434` |
| `--num-ctx` | 32768 |
| `--target user@host:/path/to/firmware` | this checkout |
| `--max-turns` | 60 |
| `--time-limit` | 1500 s |
| `--flash-cap` | 3 |

**Tools.**
- Every tool call is `healer/bin/heal <name> --json <json>`, run inside `firmware/`. With `--target` it runs over SSH (`agent.py:80-93`).
- The harness never opens the serial port (`firmware/harness/README.md:29-32`).
- `--tools core` = 23 tools (`agent.py:40-43`): `status`, `triage`, `incidents`, `telemetry_stats`, `telemetry_recent`, `events`, `pod_cmd`, `link_baud_scan`, `link_set_baud`, `chip_status`, `chip_clock`, `fw_symbolize`, `fw_images`, `src_read`, `src_search`, `src_diff`, `src_edit`, `src_commit`, `fw_build`, `fw_flash`, `fw_rollback`, `notify_human`, `note`.

**Gate** (`agent.py:175-217`).
- It allows the read, runtime, code and notify tiers, and at most 3 flashes per run.
- It denies `inject`, paths outside the workspace, and image paths.

**Watching and outputs.**
- **`watch`** polls `heal incidents` every 3 s. On a new warn or critical incident it waits 10 s, then runs once (`agent.py:501-522`).
- **Outputs** go to `firmware/harness/runs/<time>-<label>/`, as `transcript.jsonl` (`run_start`, `model_turn`, `tool_call`, `run_end`) and `report.md` (`agent.py:422-498`).

**Scoring.** `evaluate.py` resets the bench, starts a scenario, waits for incidents, runs the agent blind, and scores from the bench's own records. The result is `score.json` plus a row in `runs/results.jsonl` (`evaluate.py:156-202`; scoring at 112-153).

## 10. Model relay (`server/`)
- **Path:** the Pi at `192.168.50.2` calls the laptop at `192.168.50.1:11434` over a direct Ethernet cable (`server/README.md:8-10`).
- **Relay:** `serve.py` is a raw TCP relay to the laptop's Ollama on `127.0.0.1`. It binds only to the Ethernet address and refuses peers outside that /24. Requests pass through unchanged, streaming included (`serve.py:51-125`).
- **Defaults:** model `qwen3.5:4b`, `num_ctx` 32768, keep-alive 2 h (`serve.py:112-120`).
- **Checks:** `pi_check.py` checks the model list, a plain chat, and one tool call with thinking on (`pi_check.py:35-61`).

## 11. Into Romulus: the edge bridge and contract v0.1
The bridge adapts on the cloud side, so the firmware needs no changes (decision D29, `tools/edge_bridge/README.md:5-20`).

**What it reads.**
- **podd ops:** only `status`, and `stats` with `seconds=10` (`bridge.py:142-143`).
- **Logs, followed with cursors:** `incidents.jsonl`, `audit.jsonl`, `notifications.jsonl`, and the `boot`, `flash_start` and `flash_done` events (`bridge.py:43,183-188`).
- **Per fault, it also reads:** the windowed logs, `deployments.jsonl`, the image's `meta.json` and `source.patch`, and the workspace's git log (`bridge.py:257-276`).
- It never runs a heal tool and never opens the serial port (`README.md:67`).

**Run.**
- Every 10 s it checks in live and drains an ordered outbox (`fake_device`'s `Outbox`; retries on 5xx, 408 and 429; other 4xx go to `dead/`) (`bridge.py:140-159`, `outbox.py:8-17`).
- Flags (`__main__.py:27-71`): `--url` (`$CLOUD_URL`, default `http://localhost:8000`), `--device-id` (`$POD_DEVICE_ID`, default `pod-1`), `--healer`, `--var`, `--state-dir data/edge_bridge`, `--interval 10`, `--quiet 60`, `--max-open 900`.

**Mapping** (`tools/edge_bridge/src/edge_bridge/translate.py`):

| Contract field | From |
|---|---|
| `CheckIn.health` | `failing` if podd is down, the link isn't `up`, or a critical incident is open; `degraded` on a warning; else `ok` (124-142) |
| `CheckIn` metrics | `pod_link_bytes_per_s`, `pod_link_valid_ratio`, `pod_open_incidents`, `pod_acc_hz`, `pod_acc_mag_mg`, `pod_distance_hz`, `pod_distance_mm` (= `stats.distance.mm_ok.mean`) (145-162) |
| `running.mcu_build` | `<version>@<CRC>` from the boot record (97-111) |
| `IncidentReport.incident_id` | uuid5 of `<device_id>/<first podd incident id>` (55, 75-76) |
| `kind`, `summary` | the most severe podd incident (`faults.py:104-107`) |
| `outcome` | a verified flash gives `fw_flash`/fix-code or `fw_rollback`/ops; a runtime command that took effect gives `pod_cmd: …`/ops; `notify_human` with no fix gives `suspected_cause: hardware`, and Romulus then opens a Naïve work order (297-358) |
| `overlay` | always `null`, so a pod patch is never rolled out to the arms (250) |
| `signals` | `pod_distance_mm` (ok D rows), `pod_acc_mag_mg`, health, at most 120 samples (394-413) |
| recordings | `log_window` (podd records, 30 s before the fault to 10 s after) and `agent_trace` (audit rows and notes), both JSONL (419-460) |

**When a fault is sent** (`faults.py:43-48,109-122,183-186`):
- `resolved`: all its incidents resolved and quiet for 15 s;
- `handed_off`: a person was asked to act, then 60 s quiet;
- `timed_out`: still open after 15 min.

A flash in progress holds the report.

**Contract v0.1** (`packages/contracts/src/romulus_contracts/`):
- **Version:** `CONTRACT_VERSION = "0.1"`, matched exactly (`version.py:14,24-29`). Extra fields are kept (`common.py:26-29`).
- **`CheckIn`** (`device.py:19-39`): `running`, `overlay_id`, `health` (ok/degraded/failing), `metrics`, `health_detail`, `contract_version`.
- **`IncidentReport`** (`incident.py:42-125`): `incident_id` (UUID), `occurred_at`, `kind`, `summary`, `running`, `agent{reasoning,actions}`, `overlay`, `outcome{resolved,resolving_action,suspected_cause,category}`, `signals`, `recordings[]`, `tier`, `log_tail`.
- **`RecordingMeta`** (`telemetry.py:52-74`): kinds `incident_window`, `log_window`, `agent_trace`, `episode`.

**Device routes** (`docs/cloud-platform-planning/cloud-platform-apis.md` §3, lines 241-254). There is no auth in v0, and the device id goes in the path.

| Method and path | Purpose |
|---|---|
| `POST /v1/devices/{id}/checkin` | check-in, about every 10 s; the first one registers the device |
| `POST /v1/devices/{id}/incidents` | incident (201 new, 200 duplicate) |
| `POST /v1/devices/{id}/recordings` then `PUT /v1/recordings/{rid}/blob` (`X-Sha256`) | recording metadata, then its bytes if `upload: needed` |
| `GET /v1/firmware/{v}`, `GET /v1/harness/{v}/package`, `GET /v1/code/{rev}/package`, `POST …/installs`, `POST …/overlays/{ov}` | updates and overlays (arms) |

**Register the pod before its first check-in:**
```
POST /v1/devices {"id":"pod-1","kind":"real","device_class":"sensor_pod","by":"…"}
```
(`tools/edge_bridge/README.md:46-48`). Otherwise it auto-registers as an `so101` arm (`cloud/src/romulus_cloud/services/devices.py:213`).

## 12. Who uses pod data today

| Consumer | How it gets distance | Units | Notes |
|---|---|---|---|
| `arm/safe_replay.py`, `arm/live_replay.py` | podd `ping`, then `recent kind=D seconds=2 limit=50` every 50 ms. **If podd doesn't answer, it opens the pod's port itself** (`safe_replay.py:66-113`) | mm | Starts a motion only if 6 or more readings over 1.5 s are all clear (`--min-mm` 200). With `--live-check` it holds the arm when anything comes close (`safe_replay.py:189-267`). Saves no data. |
| `device/robot/` (Pi app) | **Pi GPIO, not podd**: gpiozero `DistanceSensor`, trigger BCM 23, echo BCM 24 (`hal.py:66-79`, `config.toml:23-25`) | **cm** (2–400) | Arm on `/dev/ttyACM0` (`config.toml:9`). Logs `dist_cm` with `t` (`app.py:58,69-79`). |
| `tools/edge_bridge` | `stats` op plus the logs | mm | section 11 |
| `firmware/harness` | heal tools | mm | section 9 |
| our sim arm host (`prototypes/arm-sim/`, branch `james/arm-sim`, not on main) | its own simulated D rows in the pod's exact shape, plus `pred_mm` from forward kinematics | mm | written so the same code reads podd tomorrow *(inferred)* |

## 13. Units, clocks and ports
- **Clocks:**
  - Device `t` is MCU uptime in ms and restarts at every reset (`main.c:43-46`).
  - Host `ts` is epoch seconds (float).
  - `serial.log` uses local time with no timezone.
- **Units:** distance in mm, echo in µs, acceleration in mg, `vdda` in mV, `temp` in °C. The pod reports distance in **mm**; `device/robot` uses **cm**.
- **Ports.**
  - podd's `auto` port falls back to the first `/dev/ttyACM*`, which can be the arm's servo adapter (`serialport.py:13-28`).
  - `device/robot/config.toml:9` and `device/sim/fake_arm.py:16` default the arm to `/dev/ttyACM0`.
  - Several docs also name `/dev/ttyACM0` for the pod (`firmware/healer/README.md:36`, `firmware/docs/HARNESS.md:34`, `firmware/README.md:43`).
  - Which device gets which number depends on plug order. The fix is to pin both by `/dev/serial/by-id/` (section 15, item 1).

## 14. Known mismatches between docs and code (for the review)
1. **`pod_cmd`'s `timeout` doesn't reach podd.** `client.call` uses it as the socket timeout, so podd waits its default 2 s (`tools.py:231`, `client.py:12`) *(inferred)*.
2. **Command length.** podd allows 78 characters, but the pod's 79 include `@1001 `. A command over about 73 characters gets `line too long`, and podd then times out *(inferred)*.
3. **`raw` and `baud_scan` stop logging while they run** and can open a short `link_silent` *(inferred, `podd.py:158,246,370-384`)*.
4. **An unknown heal-tool name prints a traceback, not JSON** (`tools.py:679-680`), although `HARNESS.md:73` says it never raises.
5. **Rotation.** All four `.jsonl` logs rotate (`podd.py:45-59`), while the docs say only telemetry and health do. `audit`, `deployments` and `notifications` never rotate.
6. **Undocumented commands.** `us on|off` isn't in `SYSTEM.md:67-71`, and `health` replies with an H line rather than an R.
7. **`reboot_loop` matches `wwdg`,** but this firmware's watchdog shows as `iwdg` (`detect.py:75`).
8. **Stale "not done" items.** The edge bridge README's "Not done yet" (`README.md:86-92`) predates `device/` (#31) and `firmware/harness`'s `transcript.jsonl`.

## 15. Integration work list (what isn't wired yet)
1. **One owner per serial port.**
   - Pin the pod with `POD_PORT=/dev/serial/by-id/<probe>` and the arm with its own by-id path.
   - Replace the `/dev/ttyACM0` defaults in `device/robot/config.toml` and the docs.
2. **One ultrasonic source.** Read it from the pod through podd, in mm. The healer, the edge bridge and `safe_replay.py` already do; `device/robot` reads cm from GPIO *(inferred recommendation)*.
3. **The arm host reads distance from podd only:** `recent kind=D`, trusting `st=="ok"`.
   - It never falls back to opening the pod's port while podd may be running. `safe_replay.py`'s fallback can create a second reader.
   - The sim host already uses the same D-row shape.
4. **A frozen-ultrasonic rule** in `detect.check_ultrasonic`: port `acc_frozen`'s zero-variance test to `us` (section 6). Without it, the healer misses the demo's stuck sensor.
5. **Arm-side incidents to Romulus.** There is no path today:
   - `device/supervisor/monitor.classify` is only called from tests;
   - `harness/h3/tools/report.py` writes a local file;
   - the bridge sends only podd incidents.

   The smallest step: the arm host posts a contract `IncidentReport` (plus a `log_window` recording) with `fake_device`'s `DeviceClient` and `Outbox`, as device `arm-1`.
6. **Register `pod-1` as `sensor_pod`** before its first check-in (section 11).
7. **Run the edge bridge once against the live podd.** It has only been tested against recorded shapes (`tools/edge_bridge/README.md:86-88`).
8. **The bad-image-07 demo path, end to end:**
   1. Flash 07 (it doesn't exist yet).
   2. podd opens an incident; this needs item 4 if the fault is a frozen reading.
   3. The arm watchdog parks the arm.
   4. The pod healer (`agent.py watch`) rolls back with `fw_rollback`.
   5. The arm resumes.
   6. The edge bridge sends the incident with `outcome fw_rollback/ops`.
   7. It shows on Romulus's timeline.
9. **`agent_trace` carries no model messages.** Since Nov 8 (`5ac13d7`) the bridge sends each run's `transcript.jsonl` as an agent run report instead (APIs §3.11), with the model's thinking per step; `agent_trace` still holds only tool calls and notes.

## 16. Data: what exists, what's coming, where it goes

| What | Where | What's in it |
|---|---|---|
| **Real pod data committed** | `firmware/ultrasonic-sample.json` (commit `b9819cc`) | 300 D rows, 30 s at 10 Hz, one still target: `mm` 49 throughout, `us` 285–286 (std 0.4 µs). A clean noise baseline, with no motion and no faults. |
| Fault ground truth | `firmware/demo/bad-images/0*/scenario.json` | what each bad image breaks and how to trigger it. No telemetry is committed. |
| Arm motion | `arm/recording_2.json`, `arm/recording_3.json`, `arm/wave.json` (also under `arm/prims/library/` on `arm/teleop-recording`) | joint angles at 50 fps, recorded by hand; no sensor data |
| Synthetic | `device/sim/traces/*.csv`, `packages/contracts/examples/*.json` | tiny hand-made traces (cm) and contract examples |
| **Not committed** | the Pi's `firmware/healer/var/` (gitignored); `data/` at the repo root (gitignored), including our 1,000 sim fault runs | – |

**Coming:**
- **Overnight:** Olir's pod under podd. These are the first real long logs. Romulus gets none of it tonight, because the overnight run uses the fake fleet (`cloud/OVERNIGHT.md:3`).
- **Bench runs:** each one from `firmware/harness/HANDOFF.md` steps 4–5 gives `runs/*/transcript.jsonl` plus `score.json`. That's real fault data with known answers.
- **Sunday:** the first real arm-and-pod runs, in the sim's `arm-run/v1` format.

**Where data goes:**
- **Small slices go in git** under a new top-level `samples/`, because `data/` is ignored:
  - `samples/pod/<date>-<what>/` and `samples/arm/<date>-<what>/`;
  - under 5 MB per drop;
  - each with a README: source machine, time span, firmware CRC, anything changed by hand.
- **Keep podd's own JSONL records unchanged**, so `detect.py` and the edge bridge read the slices as they are.
- **A pod slice holds** 10 quiet minutes, every incident window (30 s before to 10 s after, the same as the bridge's `log_window`), and one `stats` snapshot.
- **Bigger data** goes to Romulus as recordings through the bridge, or stays off git.
