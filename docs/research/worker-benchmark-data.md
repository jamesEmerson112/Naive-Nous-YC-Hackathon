# Worker-model benchmark: dataset, scoring and harness

The benchmark can be built in about an hour from code we already have. Arm faults come from re-running the fault factory's recipe in a new module. Pod faults come from synthetic podd snapshots, built offline the way `arm/tests/simpod.py` already does it. Level 1 runs as the baseline on every item.

In this session I checked the cases that make it worth doing: faults level 1 misses outright, and faults level 1 catches but gives the wrong name (a joint calibration fault reported as a sensor fault).

Facts cite `path:line` at commit `e7b4062`, or a URL. Design choices are mine and marked *(inferred)*. Numbers marked *(computed)* come from two scratch prototypes I ran, or from the local `data/sim-faults/v2` and `v3-heldout` batches, which are gitignored (`.gitignore:16`, `docs/firmware-apis.md:379`). I changed no repo files.

## 0. What it measures

- **The question:** can the small local model (the "worker", `docs/healer-data-levels.md:15-19`) read one log window and tell the manager (Hermes, run by Naïve) three things?
  - what is wrong, from a closed list of kinds;
  - which log lines show it;
  - whether to escalate.
- **The bar:** it must not raise false alarms, and it must catch what plain-code level 1 misses.
- **It stays out of the control loop.**
  - The watchdog's rules still park the arm (`docs/healer-data-levels.md:6`, `prototypes/arm-sim/armsim/host.py:242-247`).
  - The worker's JSON is advisory. Plain code checks it before anything goes to Hermes *(inferred; matches `docs/healer-data-levels.md:19`)*.
- **Two tracks on the same items** *(inferred)*:
  - **Track D, detection (blind):** the window holds no rule findings and ends at or before level 1's first finding.
  - **Track E, escalation (production-like):** the same window plus `INC` lines for the level-1 findings and podd incidents open at that moment. They are rendered like the pod harness kickoff (`firmware/harness/agent.py:323-334`). This track measures whether the worker corrects the kind, escalates, and writes a good message.

## 1. What the model sees and what it must answer

### 1.1 The window

- **D and A rows cover 6.0 s, half-open `(now−6, now]`.** H rows cover the last 30 s and E rows the last 180 s. These are podd's own triage windows (`firmware/healer/healer/podd.py:268-270`), so `detect.run` scores exactly the data the model reads.
- **A shorter window would hide several faults:**
  - `clock_skew` needs at least 4 health (H) rows spanning more than 3 s of host time (`detect.py:194-206`).
  - Level 1's frozen-sensor rule needs at least 30 ok rows in 4 s (`armsim/level1.py:30-36`).
  - Real healthy data repeats one echo-time (`us`) value for up to 18 rows in a row (1.8 s), and its `mm` reads 49 on all 300 rows *(computed on `firmware/ultrasonic-sample.json`: us 286 ×239 and 285 ×61, std 0.40 µs)*. So a frozen sensor can only be told apart from healthy data after about 3 s.
- **Level 2's lesson:** ChatTS missed a frozen trace on its own but caught it next to a healthy one (`docs/healer-data-levels.md:44-53`). So the system prompt carries a fixed set of healthy facts as the reference (§1.4).

### 1.2 Line format

Each line has an ID, and those IDs are what the model cites as evidence.

```
WINDOW src=arm-sim span=6.0s kinematics=on holding=no
D05 +0.5 mm=38 us=220 st=ok pred=37.7
D17 +1.7 mm=109 us=635 st=ok pred=131.2
J01 +0.2 cmd=0.2,-93.0,88.1,56.2,90.8,1.9 pos=0.2,-92.8,87.9,56.3,90.8,2.0 load=-0.3,-2.9,2.7,-2.2,0.0,-2.9 st=0,0,0,0,0,0
A1 +0..1 n=50 x=-12.10/2.41 y=8.02/2.60 z=1001.2/2.48 mag=1001 stale=0
H01 -27.0 t=48211 mode=normal boots=3 streak=0 crashes=0 wdg=0 rst=none loop_us=850 stk_free=1184 vdda=3302 acc=ok i2c_err=0 us=ok tx_drop=0 clk=48000000 acc_hz=50
E01 -95.2 t=0 ev=boot rst=por streak=0 crc=0x… new_build=0
L link=up bytes_per_s=2480 valid=1.00
INC1 src=watchdog kind=us_offset severity=warn summary="Reads 22 mm short against the arm's kinematics (0.85x)"   (track E only)
```

- **Two clocks:**
  - `+s` is host seconds since the window start.
  - `t=` is the pod's own millisecond counter, on pod lines only. Clock skew only shows as these two disagreeing (`detect.py:200-206`).
- **D (distance, 10 Hz):** `mm us st` exactly as the pod sends them (`docs/firmware-apis.md:47`), with `pred=` only when kinematics are on.
- **J (joints):**
  - Cut from 50 Hz to 5 Hz.
  - In each 0.2 s bin, keep the row with the largest commanded-vs-measured error on joints 0–4, or stalls disappear *(inferred)*.
  - Fields come from `arm-run/v1` (`armsim/record.py:35-39`).
- **A (accelerometer):**
  - About 300 raw lines per window, so summarized as one line per second: mean and std per axis, mean |a|, stale count. These are the statistics `detect.check_accel` computes (`detect.py:136-157`).
  - Print the std with 2 decimals, and as `0` only when it is exactly zero, so real jitter never rounds into a fake frozen reading.
- **H, E, L:**
  - H: only the fields `detect.py` reads. At most 10 lines: one every 3 s, plus any line where a field changes.
  - E: the 12 newest events.
  - L: the serial link state.
- **Header:**
  - `holding` is recomputed from the last J row with the sim's own rule: gripper position > 4.0 and gripper load > 0.25 (`armsim/arm.py:266-271`). Level 1 suspends its residual and noise rules while holding (`level1.py:50,123`).
  - `kinematics=off` drops `pred`. That is the real arm until it is calibrated (`prototypes/arm-sim/README.md:41-44`).
- **Size** *(computed with a prototype renderer on v2 runs)*:
  - About 61 D lines (2.3k characters) and 31 J lines (4.1k characters), roughly 1.5–2.2k tokens. The harness records the real `usage.prompt_tokens`, since digit-heavy text may tokenize worse.
  - A `lite` profile (D lines plus one `JS` summary line per joint) is about 1k tokens *(inferred)*.
  - The system prompt adds about 0.6k.

### 1.3 The answer: exactly six keys, nothing else in `content`

```json
{"status":"fault","kind":"joint_offset","severity":"warn","evidence":["D17-D30"],"escalate":true,
 "message":"Wrist distance reads 22 mm short of the pose prediction from D17 on while echo and mm agree, so suspect shoulder_lift calibration, not the sensor."}
```

- **`status`:** `healthy` or `fault`.
- **`kind`:** from the closed list below; `none` exactly when healthy.
- **`severity`:** `info`, `warn` or `critical`; `info` when healthy.
- **`evidence`:** up to 8 line IDs or same-letter ranges (`D17-D30`); `[]` when healthy. Every ID must exist in the window.
- **`escalate`:** true or false.
- **`message`:** one sentence of at most 40 words.
- **What counts as an alarm:** a schema-valid answer with `status: "fault"` and severity `warn` or `critical`. So an `info` answer such as a covered sensor (`us_blocked`, `detect.py:267-275`) is not a false alarm.
- **Constrained decoding:** ship a JSON Schema for servers that can enforce it, but always score with our own strict parser.

**Closed kind list**, with gold severity, the manager route *(inferred)*, and the level-1 names that count for each:

| kind | Gold severity | Route | Level-1 names that count |
|---|---|---|---|
| none | info | – | – |
| us_frozen | critical | sensor_restart | us_frozen, us_stuck (`level1.py:37,64`) |
| us_offset | warn | calibration | us_offset, us_residual (`level1.py:82,114,181`) |
| us_scale | critical | pod_fw | us_scale, us_residual (`level1.py:110`) |
| us_noisy | warn | wiring | us_noisy (`level1.py:135`) |
| us_no_echo | warn | wiring | us_no_echo, us_missing_echo (`detect.py:226`, `level1.py:154`) |
| us_intermittent | warn | wiring | us_intermittent (`detect.py:234`) |
| us_out_of_range (stuck at max: TRIG/ECHO swapped, fault-catalog U3) | warn | wiring | us_missing_echo, kinematics on only |
| us_mismatch (mm disagrees with its own echo time; images 07, 08) | critical | pod_fw | us_mismatch (`detect.py:254`) |
| us_blocked | info | – | us_blocked |
| joint_stuck | critical | arm_hardware | joint_stall (`level1.py:169`) |
| joint_offset, joint_reversed | warn *(inferred)* | calibration | none (level 1 calls them sensor faults) |
| clock_skew | critical | pod_fw | clock_skew (`detect.py:207`) |
| link_garbled, link_silent, link_down | critical | pod_fw | the same names (`detect.py:43-63`) |
| crash_loop (image 01) | critical | pod_fw | crash, reboot_loop, safe_mode (`detect.py:84-112`) |
| watchdog_loop (image 06) | critical | pod_fw + person | watchdog, reboot_loop, safe_mode, acc_error |
| acc_frozen (03), acc_gravity (05), acc_error, tx_overrun (04) | critical, warn, warn, warn | pod_fw | the same names (`detect.py:126-193`) |
| pod_health (loop_slow, stack_low, supply) | warn | pod_fw | the same names (`detect.py:285-295`) |
| grasp_missed (category 2, optional) | warn | task_redo | none |
| other | warn | manager | – |

- **Several kinds can count as exact for one item.** Each item carries `label.accept`, for example:
  - image 02 on the real board accepts {link_garbled, clock_skew};
  - image 02 in sim accepts {clock_skew, us_scale};
  - image 08 accepts {us_mismatch}, with us_frozen worth route credit only.
- **The route is how several Hermes agents with different jobs plug in** *(inferred)*:
  - `sensor_restart` is the host's restart tool (`host.py:249,322-338`);
  - `pod_fw` is Olir's pod healer with rollback (`firmware/healer/tools.json`);
  - `arm_hardware` and `calibration` mean park and hand to a person (`host.py:372-379`);
  - `task_redo` is category 2 (`docs/healer-data-levels.md:23`).

### 1.4 System prompt

Frozen during a model comparison, as in `docs/healer-benchmark-progress.md:56-57,92`. The prompt body:

> You watch the logs of an SO-101 arm and its STM32 sensor pod. You never move the arm or fix anything; you report to the manager. D = wrist ultrasonic at 10 Hz: mm, echo time us, st ok|out_of_range|no_echo, pred = the distance the pose predicts (only when kinematics=on); healthy firmware has mm=(us·343+1000)/2000. J = shoulder_pan, shoulder_lift, elbow_flex, wrist_flex, wrist_roll, gripper (deg; gripper 0–100, 0=closed): cmd, pos, load N·m, servo status bits (32=overload). A = one second of accelerometer; at rest |a|≈1000 mg and the std is never 0. H pod health, E pod events, L link, INC an alarm a rule already raised. +s = host seconds; t= = the pod's ms clock. Healthy facts: echo time jitters about 0.4 µs, so us never stays identical for 3 s, but mm can stay constant for a long time; at home the jaw is in the beam, so out_of_range is normal there; a held block is in the beam; one odd reading as the beam crosses an edge is normal. Kinds: <the list, one line each>. Reply with one JSON object only. Healthy → kind none, severity info, evidence [], escalate false, message "". Fault → cite at most 8 line ids; escalate unless severity is info; the message names the part, the symptom and one number from the window. Never tell anyone to move a joint to an angle.

- **Where the healthy facts come from:**
  - `firmware/ultrasonic-sample.json`;
  - `armsim/arm.py:30`;
  - `level1.py:40-41,50,128-129`;
  - the factory's home-pose rows (`pred 7.3`, `out_of_range`).
- **Few-shot examples**, if used, come from dev only.

## 2. Generating labeled items

### 2.1 Item record

Stored in `data/workerbench/<source>-<split>.jsonl`.

```
{"id","split","source":"sim-factory|synth-pod|vpod-live|real-sample|real-podd|real-arm","kinematics",
 "snapshot":{"now","D","J"(10 Hz),"A","H","E","link","skills":[{name,t0,t1,result}],"incidents":[{kind,severity,summary,sim_t}]},
 "label":{"status","kind","accept","route","severity","escalate","observable","hard_negative",
          "evidence_rows":{"D":[ts…],"J":[ts…],"H":[…],"E":[…]},"facts":{…},"onset_s","since_onset_s","params","label_source"},
 "meta":{"seed","task","variant","run_id"}}
```

- **Rendering happens at run time,** so the profile (full or lite) and the track (D or E) never change labels.
- **Gold evidence is stored as row timestamps, not line IDs.** Line IDs change with the profile. `render()` returns an ID-to-row map and the scorer translates through it. In `lite`, a gold J row maps to its joint's `JS` line.
- **The runner recomputes level 1 on every item.** If Olir adds the frozen-ultrasonic rule (`docs/firmware-apis.md` §15 item 4), the "level-1 missed" subset updates itself.

### 2.2 Arm faults: `gen_arm.py`, a new module (`factory.py` stays unchanged)

- **The existing factory files can't simply be sliced:**
  - Factory runs park the arm after the first finding (`factory.py:69-71`), so a window that includes the park gives the answer away.
  - `factory.py:78` records findings without their time.
- **What the new module does:** `run_capture(seed)` is a fork of `factory.one_run` (`factory.py:38-80`). It imports `sample_fault` and `PLANS`, `SimArm`, `Skills` and `Watchdog`, and `RunRecorder`, and it also captures:
  - each skill's result: `arrived`, `worst_joint`, `max_err_deg`, `grasp`, `aborted`, `halted`;
  - each incident with its `sim_t` (`host.py:310-311`).
- **Seeds follow the factory's scheme** (`seed*100000+i`, `factory.py:92`):
  - dev is seed 1, the same runs as v2;
  - test is seed 99, the same runs as v3-heldout.

  That keeps the comparison with the level-3 classifier like for like (`docs/healer-data-levels.md:70-86`). A run takes about 0.1 s on 8 workers (`prototypes/arm-sim/README.md:89`), in the arm-sim `.venv`.
- **Window ends for fault runs:**
  - onset + 1.5, 3 and 5 s, kept only if at or before the first incident;
  - plus the window ending at the first incident's `sim_t`, which is always kept even if level 1 fires within 1 s of onset;
  - drop any end within 0.5 s of another.

  The incident-time window doubles as the track E item. One pre-onset window, labeled healthy, is added when onset ≥ 6.5 s.
- **Healthy runs get three windows,** one per hard-negative phase:
  - holding during the lift;
  - at home, where every row is `out_of_range`;
  - peak joint speed during the wave.
- **Timestamp quirk to normalize.** Use `ts`, else `t`:
  - Factory D rows have `t` in sim seconds and no `ts` (`arm.py:154-158`).
  - In pod mode, podd's MCU `t` overwrites the arm's time, because `record.py:41-43` spreads the row after `"t"` (see `podsource.py:67-71` and `runs/20261108-110519-01d45c.jsonl`).
- **Observability check, per kind, on rows after onset.**
  - A planted fault with no visible signature in the window is "latent": it counts neither toward recall nor as a false alarm, and is reported separately.
  - This matters: 55 of the 122 v2 frozen-sensor runs that level 1 missed froze on a `no_echo` row, and 35 more had fewer than 30 rows after onset *(computed)*.

| kind | Observable when (kinematics on) | Kinematics off |
|---|---|---|
| us_frozen | ≥30 identical rows after onset; if not `ok`, ≥12 of them with pred 30–400 mm | ≥30 identical ok rows |
| us_offset, us_scale, joint_offset | ≥12 ok rows with pred 30–400 and median \|mm−pred\| ≥ 8 (`level1.py:81`) | latent |
| joint_reversed | ≥12 rows with pred 30–400 and (not ok, or \|mm−pred\| ≥ 8) | latent |
| us_noisy | ≥12 ok rows, robust std of (us − pred·2000/343) ≥ 4 µs (`level1.py:128-132`) | ≥12 ok rows while J shows no motion |
| joint_stuck | the stuck joint off its command by ≥3° for ≥0.5 s | same |

- **Why joint faults only show in distance:** the encoders report the angle the software believes (`arm.py:113-121`). So joint offset and reversal show up only as distance disagreeing with `pred`. That is why level 1 calls them sensor faults.
- **Gold "facts" for the message rubric:** stuck_mm and stuck_us, gap_mm, ratio, std_us, the joint name, short_by_deg, no_echo_n, pred_mm.

### 2.3 Pod faults: `gen_pod.py`, offline podd snapshots (no MuJoCo, no podd)

- **Precedent:** `arm/tests/simpod.py:70-116` already does this with the firmware's own arithmetic (`mm_v10`, `mm_v17`) and `detect.run`.
- **Noise comes from real and virtual-pod data:**
  - distance jitter from the real sample (std 0.4 µs);
  - accelerometer noise and health fields from the virtual pod (`armsim/vpod.py:130-134,143-145`);
  - boot fields from `vpod.py:115-116` and `docs/firmware-apis.md:56-60`.
- **Image 04 can only be benchmarked this way,** because `firmware/harness/evaluate.py:12,213` refuses it on the bench.
- **Each variant declares the level-1 result it expects, and the generator asserts it,** so drift in `detect.py` gets caught.

| Variant | Snapshot | Gold kind (also accepted) | Level 1 |
|---|---|---|---|
| 01 | boot and crash ×3 (streak 1–3), safe_mode event, H mode=safe, no D/A | crash_loop | caught |
| 02-real | link garbled, valid ratio 0–0.3 (scenario: "serial output is garbage") | link_garbled (clock_skew) | caught |
| 02-sim | H `t` at 0.5× host time, H every 2 s, echo halved (`vpod.py:98-103,123`) | clock_skew (us_scale) | caught |
| 02-early | 02-sim less than 6 s after boot (fewer than 4 H rows) | clock_skew | missed *(inferred from `detect.py:199-204`)* |
| 03 | accelerometer rows identical | acc_frozen | caught |
| 04 | `acc_hz=400`, `tx_drop` rising, distance at 3–6 Hz | tx_overrun | caught |
| 05 | \|a\| ≈ 500 mg at rest | acc_gravity | caught |
| 06 | acc_err events, watchdog resets within 1 s of boots with `rst=iwdg` and streak ≥1, then safe_mode | watchdog_loop | caught (reboot_loop through the streak, since detect matches `wwdg`, not `iwdg`: `docs/firmware-apis.md:341`) |
| 07 | `mm=mm_v17(us)`, target 200–1500 mm | us_mismatch | caught |
| 08-dormant | distances right (scenario: "Dormant from boot") | none | – |
| 08-held | hand at 50–150 mm for 1 s; then the echo shows the background while mm stays held (`bug.patch` `near_hold`) | us_mismatch | missed up to 1.5 s after the hand leaves, caught from 2.5 s *(computed; detect needs over 50% of 4 s wrong, `detect.py:252`)* |
| frozen-early | 25 healthy rows, then 35 identical | us_frozen | missed by both *(computed; `level1.py:32-36` needs the whole 4 s frozen; detect has no such rule, `docs/firmware-apis.md:171`)* |
| frozen-at-no_echo | the freeze lands on a `no_echo` row | us_frozen | caught with the wrong kind (us_no_echo, `detect.py:225-229`, or us_missing_echo): a kind-correction item |
| frozen-at-out_of_range, kinematics off | the freeze lands on an `out_of_range` row | us_frozen | missed (`level1.py:32` keeps only ok rows; no out_of_range rule) |
| spikes | 3–6 of 60 rows +180–320 µs on a still target (`level2/chatts_test.py:23-25`) | us_noisy | missed by both *(computed; the median-based spread ignores sparse outliers by design, `level1.py:128-132`)*. ChatTS caught it |
| stuck-max | about 23,800 µs `out_of_range`, kinematics off | us_out_of_range | missed *(computed)* |

- **Pod hard negatives, all labeled healthy:**
  - windows from the real sample (nothing fires, 4 of 4 *(computed)*);
  - a covered sensor (`us_blocked`), pod side only: with kinematics on it rightly trips the residual rule;
  - a deliberate reflash boot with `rst=pin new_build=1` (ignored by `detect.py:71-78`);
  - 1–2 of 40 `no_echo`, below the 5% intermittent threshold (`detect.py:230`);
  - podd timestamp batching, the same `ts` on consecutive rows (`runs/20261108-110519-01d45c.jsonl` lines 2–4);
  - image 08 while dormant.

### 2.4 Live and real slices

- **Live virtual pod plus podd, 20–40 items, as an integration check only:**
  - Plant faults through the host's `inject` op: us_frozen, us_no_echo, pod_image07 and pod_image02 (`host.py:523,683-690`, `soak.py:72-81`).
  - This only proves the renderer reads real podd output; it doesn't add volume.
- **Real data goes in test only:**
  - 5 non-overlapping 60-row windows of `firmware/ultrasonic-sample.json`;
  - Olir's overnight podd logs, once they land in `samples/pod/` (`docs/firmware-apis.md:382,388,392`);
  - bench transcripts (`docs/firmware-apis.md:383`);
  - the first real arm runs in `arm-run/v1` (`record.py:1`), with kinematics off.

### 2.5 Splits and sizes *(inferred)*

- **No overlap:** whole runs and synthetic seeds never cross splits (`docs/healer-data-levels.md:67`).
- **Dev:** seed 1, pod targets 40–600 mm.
- **Test:** seed 99, pod targets 600–2000 mm, plus all real data.
- **Held out of dev entirely,** as unseen scenarios: image 08 and `us_out_of_range`.
- **Test size:**
  - about 60 observable windows per arm kind, at least half of them missed by level 1;
  - 30 per pod variant;
  - at least 300 healthy items, at least 150 of them hard negatives;
  - about 1,200 items in all.

  The healthy count matters: with 0 alarms out of N, the 95% upper bound on the false-alarm rate is about 3/N, so claiming "≤1%" needs N ≥ 300.
- **`--quick` subset:** 8 per kind plus 60 healthy, about 250 items, for screening models before a full confirming run (as in `docs/healer-benchmark-progress.md:88-106`).

### 2.6 Where level 1 falls short today

Tally of v2, 2,000 runs *(computed)*. "Exact" means level 1 used the exact name. With the alias map, us_scale rises to 96 (+17 us_residual) and us_offset to 95 (+8).

| Planted fault | Level 1 alarmed | Named exactly |
|---|---|---|
| none | 0/280 | – |
| joint_stuck | 230/230 | 226 (as joint_stall) |
| us_frozen | 132/254 | 132 |
| us_scale | 110/216 | 79 |
| us_offset | 95/264 | 87 |
| us_noisy | 76/254 | 76 |
| joint_offset | 95/265 | **0** (as us_offset 65, us_scale 25, us_residual 4) |
| joint_reversed | 59/237 | **0** (all us_missing_echo) |

- **Why us_noisy is missed so often:** the noise rule only runs while the arm is still (pred spread ≤ 2 mm, `level1.py:126`), so it is blind during motion.
- **Why the wrong name matters:** a joint calibration fault sent down the restart-the-sensor route fails verification and goes to a person (`host.py:340-379`).

## 3. Metrics

- **JSON validity:**
  - `strict`: the whole content is one JSON object.
  - `lenient`: the first `{…}` block parses.
  - `schema_ok`: the six keys, valid values, and evidence IDs that exist.
  - A reply cut off for length is invalid. JSON that appears only in the thinking text is invalid.
  - An invalid answer counts as no alarm, since level 1 still guards.
- **Detection:**
  - Recall per kind on observable items, and macro recall.
  - **Headline, "gap recall":** recall on observable items where level 1, as it stands now, fires nothing on the same window.
  - Combined recall of level 1 and the worker.
  - Lead time versus level 1's `sim_t`, from the earliest alarming window end in each run.
- **False alarms:**
  - Rate per group: clean, each hard-negative type, and real data.
  - Each rate with its exact 95% upper bound (3/N at zero), plus the combined rate.
- **Kind:**
  - Score: exact (in `accept`) = 1, same route = 0.5. Also report exact-only, a confusion matrix, and route accuracy.
  - **Kind corrections:** level 1 fired with a wrong kind and the worker got it exact.
- **Severity, escalation, evidence:**
  - Severity: exact match.
  - Escalation: right when `escalate` is true exactly for warn and critical faults.
  - Evidence: hit = any cited line is gold; precision = cited gold lines ÷ cited lines. It must be `[]` when healthy.
- **Cost:**
  - Latency p50, p95 and max; time to first token with `--stream`.
  - Prompt and completion tokens per check; tokens per second.
  - The Pi can't be measured off-device. Estimate Pi time as prompt processing plus output tokens ÷ the Pi's tokens per second, as in `docs/healer-benchmark-progress.md:105-106`.
- **Stability:** `--repeat 3` on `--quick`, reporting how often the alarm or the kind flips.
- **Message score, 0–5, judged by code** (track E, alarmed fault items):
  - **M1 form:** one sentence, 8–40 words.
  - **M2 part:** names the right component: ultrasonic, distance or echo; the gold joint's name; pod, firmware, image or clock; accelerometer.
  - **M3 symptom:** symptom words that fit the kind (frozen, stuck, identical, offset, calibration, mismatch, truncated…).
  - **M4 grounded:** at least one number within tolerance of a gold fact (mm ±3 or ±10%, ratio ±0.05, degrees ±25%, µs ±5).
  - **M5 honest:** every number in the message matches something in the window or the facts (±2 or ±2%). No "move/set … to N deg", and no "I restarted/fixed/flashed".
  - **Reference bar:** score level 1's own summaries with the same rubric.
  - **Optional:** an LLM-judge check on 50 items, reporting rank correlation with the code score. Never the headline.
- **Gates, then ranking** (like `docs/healer-benchmark-progress.md:95-98`) *(inferred)*:
  - **Gates:** schema_ok ≥ 98%; false-alarm upper bound on healthy ≤ 1%; zero alarms on real data; p95 latency within the chosen check cadence.
  - **Ranking:** gap macro recall, then kind score, then message score, then mean output tokens.

## 4. The harness: `prototypes/arm-sim/workerbench/`

A new folder using only the standard library and what is already in the repo.

- **Imports:**
  - Add `firmware/healer` to `sys.path` for `healer.detect`, as `armsim/podsource.py:21-27` does.
  - Load `armsim/level1.py` by file path: `armsim/__init__.py:2` imports MuJoCo, so `from armsim import level1` fails without it *(verified)*.
  - Only `gen_arm.py` needs the `.venv`.
- **`kinds.py`:** `KINDS`, `GOLD_SEVERITY`, `ROUTE`, `L1_ALIAS: dict[str, set[str]]`, `LEXICON`, `kind_score(kind, accept, gold) -> float`.
- **`render.py`:**
  - `render(item, *, profile="full"|"lite", track="D"|"E") -> tuple[str, dict[str, dict]]`, returning the text and the ID-to-row map.
  - Helpers: `holding_from_j(row)`, `j_bins(J, t0, now, hz=5)`, `a_seconds(A, t0, now)`, `h_lines(H, now, every_s=3)`, `e_lines(E, now, cap=12)`.
- **`l1.py`:**
  - `l1_findings(snapshot, *, which="all"|"pod"|"arm") -> list[dict]`, running `detect.run`, `level1.run({"now","D","holding"})`, and `level1.check_arrival` on skills that ended in the window (`level1.py:162-174`).
  - `l1_answer(snapshot) -> dict`: the most severe finding as an answer, with its kind mapped through the alias table, `evidence: []`, and the finding's summary as the message.
- **`gen_arm.py`:**
  - `run_capture(seed) -> dict`
  - `window_ends(run) -> list[float]`
  - `observable(kind, params, D, J, onset, kinematics) -> tuple[bool, dict]`
  - `facts(kind, params, rows) -> dict`
  - `make_items(run, split, kinematics) -> list[dict]`
  - CLI: `--seed --n --workers --split --per-kind 60 --healthy 300 --kinematics on,off --out`
- **`gen_pod.py`:**
  - `VARIANTS: dict[str, Callable]`
  - `healthy_d(rng, n, target_mm, now)`
  - `synth(variant, rng, *, target_mm, now) -> dict`, returning the snapshot, the label and `expect_l1`
  - CLI: `--seed --split --per-variant 30 --out`
- **`gen_real.py`** (optional):
  - `windows_from_podd_logs(var_dir, step_s=30)` over `logs/telemetry|health|events|incidents.jsonl` (`docs/firmware-apis.md:133-136`)
  - `windows_from_sample(path)`
- **`prompt.py`:** `SYSTEM`, `ANSWER_SCHEMA`, `messages(text, shots=()) -> list[dict]`.
- **`score.py`:** `parse_answer(content, idmap) -> dict`, `gold_ids(item, idmap) -> set`, `expand_ids(refs) -> set`, `message_score(msg, item, text) -> dict`, `score_item(item, parsed, idmap) -> dict`, `upper95(k, n) -> float`, `summarize(rows) -> dict`.
- **`run.py`:**
  - CLI: `--items <globs> --split test [--quick] (--endpoint URL --model NAME | --baseline l1|l1-pod|l1-arm|oracle|healthy|alarm) --profile full|lite --track D|E --shots 0|2 --repeat 1 --json-mode none|object|schema --max-tokens 512 --temperature <unset = model default> --extra-body JSON --stream --timeout 300 --label NAME --out runs/wb/<label>/`
  - `call(endpoint, model, messages, **opts) -> {content, reasoning, finish_reason, prompt_tokens, completion_tokens, latency_s, ttft_s}`, via urllib POST to `{endpoint}/chat/completions`. Read thinking from `message.reasoning` or `message.reasoning_content`.
  - Writes `answers.jsonl`, `report.json` and a markdown table. Runs one item at a time by default (`docs/healer-benchmark-progress.md:92`).
- **`tests/test_workerbench.py`**, the same kind of sanity checks as Ishaan's bench (`docs/healer-benchmark-progress.md:40-42`):
  - the oracle scores 100%;
  - always-healthy gets recall 0 and no false alarms;
  - always-alarm gets 100% false alarms;
  - `l1_answer` matches each variant's `expect_l1`;
  - line IDs are unique and every gold row renders;
  - the schema accepts the oracle's answers.

**Server settings** (check each one rather than hardcoding):

- **Ollama `/v1/chat/completions`:**
  - Accepts `response_format`, `max_tokens`, `stream_options.include_usage`, `seed`, and `reasoning_effort` or `reasoning.effort` for thinking.
  - Does not accept `tool_choice` or `n`.
  - The usage and reasoning fields in the reply aren't documented, so fall back to the native counts as `firmware/harness/agent.py:626` does.
- **vLLM:**
  - Thinking comes back in `reasoning` (formerly `reasoning_content`) with `--reasoning-parser qwen3`.
  - Turn Qwen3 thinking off per request with `chat_template_kwargs: {"enable_thinking": false}`.
- **llama.cpp server:**
  - `--reasoning-format deepseek` puts thinking in `message.reasoning_content`.
  - `-rea on|off|auto` or `--reasoning-budget 0` controls thinking.
  - Schema-constrained JSON is a listed feature.
- **Example:** `--endpoint http://192.168.50.1:11434/v1 --model qwen3.5:4b` (the laptop's Ollama, `agent.py:10`).

**Build order for the hour** *(inferred)*:

1. `kinds`, `render` and `l1` (15 min).
2. `gen_pod` (15 min).
3. `score` and `run` with the baselines (15 min).
4. `gen_arm` (15 min).

Run `--baseline oracle` and `--baseline l1` first, then the models.

## 5. Decisions for you

1. **Which track is the headline:** D (blind) or E (production, with level-1 findings in the window)?
2. **Should `escalate` be false when the host repairs a fault by itself** (`restart_sensor`, `host.py:249`)? I assumed the worker tells the manager about every warn or critical fault, as the user described it.
3. **Gold severity for kinds level 1 never emits** (joint_offset, joint_reversed, spikes): I picked warn.
4. **Category 2, a wrong action** (`docs/healer-data-levels.md:23`): this needs a new planted fault, such as moving the block before the approach. I listed `grasp_missed` but left it out of the hour.
5. **An extra baseline:** level 3's feature function works on any window (`armsim/level3.py:51-84`). Trained per window on dev, it would show whether the LLM should do detection at all, or only explain what the classifier found *(inferred)*.

## 6. Format problems found along the way

1. `record.py:41-43` lets podd's `t` overwrite the arm time in pod mode.
2. `factory.py:78` drops incident times, which is why the generator has to re-run the sim.
3. The virtual pod's image 02 only halves time and leaves the link clean (`vpod.py:98-103`). The real image garbles the link. Hence the separate sim and real variants.
4. The virtual pod has no images 03–06 or 08. Offline synthesis covers them.

Sources:
- [Ollama OpenAI compatibility](https://docs.ollama.com/api/openai-compatibility)
- [vLLM reasoning outputs](https://docs.vllm.ai/en/latest/features/reasoning_outputs.html)
- [llama.cpp server README](https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md)
- [Hugging Face mirror of Ollama's openai.md](https://huggingface.co/spaces/oldg9516X/ollama/blob/main/docs/openai.md)

Prototype scripts, scratch only:
- `/tmp/claude-0/-home-user-Naive-Nous-YC-Hackathon/40da8899-cc0b-54bd-b44e-30bb039d8e71/scratchpad/proto_render.py`
- `/tmp/claude-0/-home-user-Naive-Nous-YC-Hackathon/40da8899-cc0b-54bd-b44e-30bb039d8e71/scratchpad/proto_pod.py`