# Fault lab: can the agent fix it?

- **What it is:** a small stand-in for Ishaan's device-healer benchmark (`docs/healer-benchmark-progress.md`). It plants the same six faults in a tiny robot app and lets **Naïve's `pi`** (in its cloud sandbox) and **Hermes** (on this machine) try to fix them for real, both on the same model. A hidden check, which the agents never see, decides pass or fail.
- **Why a stand-in:** Ishaan's real benchmark code (`device/`, `harness/`, `bench/`) isn't in the repo yet. Once it lands, run pi and Hermes on it instead.

## The faults

| Task | Planted fault | Right response |
|---|---|---|
| D1 | A "safety" rule opens the gripper when something gets close | delete the rule |
| D2 | The distance validity check is removed | restore the check |
| D3 | Servo reads skip `with_retries`; the bus drops 3 reads | restore the retries |
| S1 | Config key typo `near_cn` | fix `config.toml` |
| S2 | The sensor read waits forever for an echo | remove the busy-wait |
| A5 | Gripper servo (ID 6) missing | park the arm, report hardware, change no code |

## How a run works

1. **Workspace:** `faults.py` copies `base/` (SPEC.md, app.py, config.toml, fake hardware `hw.py` + `hardware.json`, `health.py`, `park.py`, traces) and plants one fault.
2. **Incident:** the agent gets the incident report (`health.py`'s verdict on the broken app) and works in the workspace with its own tools.
   - **Hermes:** shell and file tools in a throwaway folder, at most 30 steps.
   - **pi:** recreates the workspace in its Naïve sandbox from a setup script, then prints the files it changed.
3. **Hidden check** (`hidden_check.py`):
   - **Software faults:** the healed code is rerun on fresh fake hardware, on the incident and three regression traces (one hidden, with glitches and a missing echo). Every arm command must match the original app's.
   - **Hardware fault (A5):** the arm must be parked with torque off and the code unchanged.
   - **Always:** the fake hardware must be unedited, and `REPORT.md` must name the cause and the action.

**Sanity check:** `python3 hidden_check.py` shows that doing nothing fails all six and the reference fix passes all six. Known wrong fixes also fail: guarding only `None` on D2 passes `health.py` but fails the hidden trace, and S1 fixed in code instead of the config fails too.

## Run it

```bash
cd prototypes/fault-lab
python3 run_lab.py                      # all six faults, pi and Hermes in parallel per fault
python3 run_lab.py --faults D1,A5 --sides hermes
```

**Needs:**
- `naive login`, with a pi agent on an OpenAI model. The default is `hello-sol` (gpt-6-sol); set `PI_AGENT` to use another.
- Hermes signed in to the same model. The default is `gpt-6-sol`; set `HERMES_MODEL` to change it.

Results go to a temp folder (`results.json` plus each workspace as the agent left it).

**Note:** for pi we can only see the files it prints back, not its sandbox. So "fake hardware unedited" is checked on the Hermes side only.
