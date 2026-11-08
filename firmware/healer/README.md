# healer: logging, triage and repair tools for the sensor pod

Pi-side infrastructure that an LLM harness drives. It doesn't contain a decision loop yet; it provides the
tools and the information. Python 3 standard library only.

```
healer/
├── bin/podd        daemon: owns the serial port, logs, runs triage, serves requests on var/podd.sock
├── bin/heal        CLI over the tools: `heal <tool> key=value ...` prints JSON
├── SYSTEM.md       briefing the agent reads first (tool: system_info)
├── tools.json      tool manifest for a harness (name, description, risk, input_schema); regenerate with
│                   `bin/heal tools --json > tools.json`
├── healer/
│   ├── podd.py       serial reader, rings, JSONL logs, incident tracking, socket server
│   ├── detect.py     deterministic triage rules (physical invariants, no LLM)
│   ├── tools.py      every tool: function + JSON schema + risk level
│   ├── swd.py        OpenOCD wrapper: build ID, uptime rate, clock-tree decode, halt sampling, flash, backup
│   ├── images.py     image registry keyed by build-ID CRC (ELF + bin + source snapshot + status)
│   ├── workspace.py  the editable firmware source (its own git repo), build
│   ├── proto.py      telemetry line parser, radio frame decoder
│   └── client.py / serialport.py / config.py / cli.py
└── var/            (gitignored) logs/, images/, backups/, workspace/pod/, deployments.jsonl, notifications.jsonl
```

## Running it
```bash
cd firmware
demo/scenario setup              # once: workspace at tag v1.0 + known-good image in the registry
demo/scenario build-images       # once: the six known-bad images (see docs/DEMO.md)
healer/bin/podd &                # leave running; it reconnects if the probe re-enumerates
healer/bin/heal triage
healer/bin/heal                  # list all tools
```

`podd` must be the only process using `/dev/ttyACM0`, so don't run `make monitor` alongside it. To watch
the live stream, use `tail -f healer/var/logs/serial.log`.

## Tools by risk level
| risk | tools |
|---|---|
| read | system_info, status, triage, incidents, telemetry_stats, telemetry_recent, events, log_search, link_baud_scan, link_raw_capture, chip_status, chip_clock, chip_halt_sample, chip_read, chip_regs, fw_symbolize, fw_images, src_status, src_list, src_read, src_search, src_log, src_show, src_diff, note |
| runtime | pod_cmd, link_set_baud, chip_reset, fw_mark |
| code | src_edit, src_write, src_revert, src_restore, src_commit, fw_build |
| flash | fw_flash (backup → program → verify → watch → auto-rollback), fw_rollback |
| notify | notify_human |

A harness can expose all of them, or gate by risk (for example, require approval for `code` and `flash`).
`pod_cmd` refuses `inject ...`; fault injection is operator-only (`demo/scenario trigger`).

## Logs (var/logs/)
| file | contents |
|---|---|
| `serial.log` | every raw line with a host timestamp, including garbage |
| `telemetry.jsonl` | A, D, F records (rotated at 20 MB × 5) |
| `health.jsonl` | H records |
| `events.jsonl` | E and R lines, `#` comments, host events (flash, baud, incidents, agent notes) |
| `incidents.jsonl` | incident open/resolve |
| `audit.jsonl` | every tool call: arguments, ok/error, duration |

## Using it from an LLM harness
- Load `tools.json` as the tool definitions.
- Run each call as `heal <name> --json '<arguments>'`, or in Python: `from healer import tools; tools.call(name, args)`.
- Give the agent `system_info` first. Don't give it access to `firmware/demo/` (scenario answers) or to
  `firmware/pod/` (the pristine source). Its source access is the workspace, through the `src_*` tools.
