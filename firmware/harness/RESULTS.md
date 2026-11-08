# Pod healer results

Report format from [`HANDOFF.md`](HANDOFF.md). One row per run. The raw runs stay local in `runs/`
(`score.json`, `transcript.jsonl`); replay one with `python3 view.py runs/<run>`.

- **Model:** qwen3.5:4b, thinking on, `num_ctx` 32768, through Ollama on Olir's laptop. The Pi reaches it
  through the `server/serve.py` relay at `192.168.50.1:11434` over the direct Ethernet cable.
- **Tool set:** `--tools arm`: 17 `heal` tools (`agent.ARM_TOOLS`) plus `arm_test` (gate only; no arm on
  USB).
- **Harness:** branch `olir/arm-ultrasonic-heal`. The runs below came before the commit, in this order:
  - **v0:** the arm rules as first written.
  - **v1:** adds the rule "You don't have to explain the exact defect before you roll back...".
  - **v2:** v1 plus the turn-budget notice.
- **Scenario:** arm07 (bad release v1.7, `uint16_t` truncation in the HC-SR04 distance math), scored by
  `arm/heal_test.py`.
- **Bench vs sim:** "sim" means the real model against `arm/tests/simpod.py`. That's the real distance
  math, triage rules, gate code and v1.7 source, but flashing is simulated. It shows what the model does; it
  doesn't show the bench works. The one bench run stopped at stage 1 because the HC-SR04 gets no echo
  (wiring, see HANDOFF).

## Bench, hint-free: passed (Sat Nov 7, 11:22 PM PST)
`20261107-232214-arm07-ollama`: all 9 checks, after three hints were removed. `arm/hint_audit.py` found 0
leaks on this setup. The hints that were there before:
- podd's `us_mismatch` hypotheses named "integer overflow/truncation" and suggested `src_search 'dist_mm'` and
  `fw_rollback`.
- The v1.7 commit message said "keep the distance in 16 bits".
- The patch carried a "16-bit math" comment.

| | |
|---|---|
| Timeline | triage on turn 1; `src_diff` on turn 2; **`fw_rollback`, verified (turn 5, +48 s)**; **`arm_test` passed (turn 6, +51 s)**; triage clean (7); report (13) |
| Totals | 13 turns, 11 tool calls (9 read, 1 flash, 1 arm), 1 refused, 0 errors, 86.1 s |
| Refused | turn 12: `src_commit`, which isn't in the arm tool set; refused as invalid |
| Root cause | named the `(uint16_t)` cast on line 122 of `hcsr04.c`. Its arithmetic slipped: it put the truncated value at ~53445 where it is 30189 (882157 mod 65536), though it got 15 mm right |

**Every row below, and the 11:10 PM run, ran with those hints present.** They show the plumbing and the
model's behaviour, but they aren't hint-free evidence.

## Bench: passed (Sat Nov 7, 11:10 PM PST)
`20261107-231033-arm07-ollama`: the real pod, the real HC-SR04 (open space at ~445 mm), qwen3.5:4b via
the relay, v2 harness, start gate only (no arm on USB). **All 9 checks passed.**

| | |
|---|---|
| Timeline | triage on turn 1 (critical `us_mismatch`); reads source, telemetry, `src_diff`, `src_log` (turns 2–14); `note` with the root cause (15); **`fw_rollback`, verified (16, +144 s)**; **`arm_test` passed (17, +148 s)**; triage clean (19); more reading; budget notice (after 23); `notify_human` (26); report (27) |
| Totals | 27 turns, 26 tool calls (23 read, 1 flash, 1 arm, 1 notify), 0 refused, 0 errors, 212.8 s, 466k/11k tokens |
| Root cause | named the `(uint16_t)` cast on line 124 of `src/hcsr04.c`, with correct arithmetic: (uint16_t)892399 = 40431, /2000 = 20 mm vs 446 mm |
| Note | service was restored by +148 s; the 11 turns after that were extra reading and the report |

Two earlier bench attempts stopped at stage 1, as they should: `224244` (HC-SR04 no echo, wiring, since
fixed) and `230808` (something within 200 mm on known-good firmware).

**Turn speed:** median 3.4 s per model turn, p90 10.2 s (166 turns: every run below plus the
bench smoke run).

**Smoke run on the bench** (HANDOFF step 3, v0, `--tools arm --max-turns 10`): 10 turns, 10 calls, 0 invalid,
0 tool errors, stop `max_turns`. It put the real `us_no_echo` on v1.0 known-good down to hardware and didn't
flash.

| run | pod | variant | passed | root cause named | exact defect | fix | agent flashes | tool calls | turns | tokens in/out | seconds | stop | flash / arm_test, by turn |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| `20261107-224133-arm07-sim-ollama` | sim | v0 | **yes** | True | cast | rollback | 1 | 5 | 6 | 28743/2552 | 39.7 | end_turn | rollback@3, arm_test@4 |
| `20261107-224244-arm07-ollama` | bench | - | no | - | - | none | 0 | - | - | -/- | - | aborted at stage 1: HC-SR04 no_echo | - |
| `20261107-224301-arm07-sim-ollama` | sim | v0 | no | True | - | rollback | 1 | 30 | 30 | 332779/5751 | 94.2 | max_turns | rollback@30 |
| `20261107-224452-arm07-sim-ollama` | sim | v0 | **yes** | True | - | rollback | 1 | 4 | 5 | 20406/1182 | 18.0 | end_turn | rollback@2, arm_test@3 |
| `20261107-224527-arm07-sim-ollama` | sim | v0 | **yes** | True | uint16 | rollback | 1 | 9 | 10 | 59450/3924 | 57.0 | end_turn | rollback@8, arm_test@9 |
| `20261107-224707-arm07-sim-ollama` | sim | v1 | **yes** | True | uint16 | rollback | 1 | 30 | 30 | 398290/8744 | 152.7 | max_turns | arm_test@22, rollback@26, arm_test@28 |
| `20261107-224957-arm07-sim-ollama` | sim | v1 | **yes** | True | uint16 | rollback | 1 | 9 | 10 | 60972/3410 | 52.2 | end_turn | rollback@5, arm_test@7 |
| `20261107-225106-arm07-sim-ollama` | sim | v1 | **yes** | True | uint16 | rollback | 1 | 8 | 9 | 49865/2697 | 40.5 | end_turn | rollback@5, arm_test@7 |
| `20261107-225204-arm07-sim-ollama` | sim | v1 | **yes** | True | 16 bit | rollback | 1 | 20 | 21 | 194985/6942 | 108.2 | end_turn | rollback@17, arm_test@18 |
| `20261107-225507-arm07-sim-ollama` | sim | v2 | **yes** | True | cast | rollback | 1 | 7 | 8 | 42753/2934 | 43.6 | end_turn | rollback@4, arm_test@6 |
| `20261107-225607-arm07-sim-ollama` | sim | v2 | **yes** | True | uint16 | rollback | 1 | 10 | 11 | 82237/4010 | 61.9 | end_turn | rollback@8, arm_test@10 |
| `20261107-225726-arm07-sim-ollama` | sim | v2 | **yes** | True | uint16 | rollback | 1 | 9 | 10 | 58335/3105 | 46.6 | end_turn | rollback@5, arm_test@7 |
| `20261107-225830-arm07-sim-ollama` | sim | v2 | **yes** | True | uint16 | rollback | 1 | 7 | 6 | 38374/2755 | 42.4 | end_turn | rollback@4, arm_test@5 |

## Reading the runs
- **v0 (3 of 4):** the failure found the right line on turn 2, then misread the cast's precedence ("900
  fits in uint16_t"). It re-read source for 27 turns and rolled back on turn 30, the last one, leaving no
  turn for `arm_test`.
- **v1 (4 of 4):** every run named the exact defect. One still used all 30 turns: it rolled back on turn 26
  and confirmed on turn 28, a near miss.
- **v2 (4 of 4):** every run rolled back by turn 8 and finished in 6–11 turns. The budget notice never had
  to fire, so v2's runs are more evidence for v1's rule; the notice is a guard for slower runs.
- `root cause named` is a keyword check (`heal_test.ROOT_CAUSE`). `exact defect` is the stricter match for
  uint16/16-bit/truncation/overflow/cast in what the agent stated. Read `report.md` before you trust a
  borderline row.
