"""The closed list of fault kinds the worker model may answer, with gold severity, the manager route,
which level-1 names count for each kind, and the words the message rubric looks for.

Source: the kind table in docs/research/worker-benchmark-data.md §1.3 (level-1 names from
armsim/level1.py and firmware/healer/healer/detect.py). Routes are the data report's (inferred).

    from workerbench import kinds
    kinds.kind_score("us_offset", {"joint_offset"}, "joint_offset")   # 0.5: right route, wrong kind
"""

# kind -> (gold severity, manager route). Route "none" means nothing to fix.
TABLE = {
    "none":            ("info",     "none"),
    "us_frozen":       ("critical", "sensor_restart"),
    "us_offset":       ("warn",     "calibration"),
    "us_scale":        ("critical", "pod_fw"),
    "us_noisy":        ("warn",     "wiring"),
    "us_no_echo":      ("warn",     "wiring"),
    "us_intermittent": ("warn",     "wiring"),
    "us_out_of_range": ("warn",     "wiring"),
    "us_mismatch":     ("critical", "pod_fw"),
    "us_blocked":      ("info",     "none"),
    "joint_stuck":     ("critical", "arm_hardware"),
    "joint_offset":    ("warn",     "calibration"),
    "joint_reversed":  ("warn",     "calibration"),
    "clock_skew":      ("critical", "pod_fw"),
    "link_garbled":    ("critical", "pod_fw"),
    "link_silent":     ("critical", "pod_fw"),
    "link_down":       ("critical", "pod_fw"),
    "crash_loop":      ("critical", "pod_fw"),
    "watchdog_loop":   ("critical", "pod_fw"),
    "acc_frozen":      ("critical", "pod_fw"),
    "acc_gravity":     ("warn",     "pod_fw"),
    "acc_error":       ("warn",     "pod_fw"),
    "tx_overrun":      ("warn",     "pod_fw"),
    "pod_health":      ("warn",     "pod_fw"),
    "grasp_missed":    ("warn",     "task_redo"),
    "other":           ("warn",     "manager"),
}
KINDS = list(TABLE)
GOLD_SEVERITY = {k: v[0] for k, v in TABLE.items()}
ROUTE = {k: v[1] for k, v in TABLE.items()}
SEVERITIES = ("info", "warn", "critical")

# One line per kind for the system prompt.
DESCRIBE = {
    "none": "nothing is wrong",
    "us_frozen": "the ultrasonic repeats one identical reading (echo time never jitters)",
    "us_offset": "distance reads a constant number of mm long or short",
    "us_scale": "distance reads a constant factor of the true distance (clock or units bug)",
    "us_noisy": "distance jitters far more than 0.4 us at rest, or has sparse large spikes",
    "us_no_echo": "almost every ping returns no_echo",
    "us_intermittent": "5-80% of pings return no_echo",
    "us_out_of_range": "readings stuck at the maximum echo time (st=out_of_range far away)",
    "us_mismatch": "mm disagrees with its own echo time us (firmware math bug)",
    "us_blocked": "something covers the sensor: a constant reading under 30 mm (info only)",
    "joint_stuck": "a joint does not follow its command (servo dead or blocked)",
    "joint_offset": "a joint's calibration is off: distance disagrees with pred by a steady gap",
    "joint_reversed": "a joint moves the wrong way: distance or no_echo contradicts pred",
    "clock_skew": "the pod's t= clock runs at the wrong rate against host time",
    "link_garbled": "bytes arrive but do not parse as telemetry",
    "link_silent": "no bytes from the pod",
    "link_down": "the serial port is not open",
    "crash_loop": "the pod firmware crashes (HardFault) and reboots repeatedly",
    "watchdog_loop": "the pod firmware hangs and the watchdog resets it repeatedly",
    "acc_frozen": "accelerometer values identical on every axis (std exactly 0)",
    "acc_gravity": "accelerometer at rest does not measure about 1000 mg",
    "acc_error": "accelerometer stops answering on I2C",
    "tx_overrun": "telemetry exceeds the UART: tx_drop rising, D rows missing",
    "pod_health": "pod loop too slow, stack low, or supply voltage out of range",
    "grasp_missed": "the gripper closed on nothing",
    "other": "a fault that fits no kind above",
}

# Level-1 finding name -> the kinds it counts for (many-to-many).
L1_ALIAS = {
    "us_frozen": {"us_frozen"}, "us_stuck": {"us_frozen"},
    "us_offset": {"us_offset"}, "us_scale": {"us_scale"}, "us_residual": {"us_offset", "us_scale"},
    "us_noisy": {"us_noisy"}, "us_no_echo": {"us_no_echo"}, "us_intermittent": {"us_intermittent"},
    "us_missing_echo": {"us_no_echo", "us_out_of_range"}, "us_mismatch": {"us_mismatch"},
    "us_blocked": {"us_blocked"}, "joint_stall": {"joint_stuck"},
    "clock_skew": {"clock_skew"}, "link_garbled": {"link_garbled"}, "link_silent": {"link_silent"},
    "link_down": {"link_down"}, "crash": {"crash_loop"}, "reboot_loop": {"crash_loop", "watchdog_loop"},
    "safe_mode": {"crash_loop", "watchdog_loop"}, "watchdog": {"watchdog_loop"},
    "acc_error": {"acc_error", "watchdog_loop"}, "acc_frozen": {"acc_frozen"}, "acc_stale": {"acc_frozen"},
    "acc_gravity": {"acc_gravity"}, "tx_overrun": {"tx_overrun"},
    "loop_slow": {"pod_health"}, "stack_low": {"pod_health"}, "supply": {"pod_health"},
    "detector_error": {"other"},
}
# The kind an l1 name is reported as when none of its aliases is accepted for the item.
L1_PRIMARY = {"us_stuck": "us_frozen", "us_residual": "us_scale", "us_missing_echo": "us_no_echo",
              "joint_stall": "joint_stuck", "crash": "crash_loop", "reboot_loop": "crash_loop",
              "safe_mode": "crash_loop", "watchdog": "watchdog_loop", "acc_stale": "acc_frozen",
              "loop_slow": "pod_health", "stack_low": "pod_health", "supply": "pod_health",
              "detector_error": "other"}


def l1_kinds(name):
    """Kinds a level-1 finding name counts for. Arm-side pod findings are 'pod_<detect kind>'."""
    if name.startswith("pod_") and name not in L1_ALIAS:
        name = name[4:]
    return L1_ALIAS.get(name, {"other"})


def l1_primary(name):
    if name.startswith("pod_") and name not in L1_ALIAS:
        name = name[4:]
    return L1_PRIMARY.get(name, name if name in TABLE else "other")


# Message rubric (score.message_score): words naming the part (M2) and the symptom (M3).
PART_WORDS = {
    "us": ("ultrasonic", "distance", "echo", "sensor", "rangefinder", "hc-sr04"),
    "pod": ("pod", "firmware", "image", "clock", "mcu", "stm32", "build", "serial", "link", "uart"),
    "acc": ("accelerometer", "acc", "lis3dh", "imu"),
    "joint": ("joint", "servo", "shoulder", "elbow", "wrist", "gripper", "pan", "lift", "flex", "roll"),
}
PART_OF = {k: ("us" if k.startswith("us_") else "acc" if k.startswith("acc_")
               else "joint" if k.startswith("joint_") or k == "grasp_missed" else "pod") for k in KINDS}
LEXICON = {
    "us_frozen": ("frozen", "stuck", "identical", "same", "unchanging", "constant", "stale", "zero jitter", "flat"),
    "us_offset": ("offset", "short", "long", "gap", "calibration", "constant", "off by"),
    "us_scale": ("scale", "factor", "ratio", "half", "times", "x the", "clock", "units"),
    "us_noisy": ("noisy", "noise", "jitter", "spike", "spread", "erratic", "outlier"),
    "us_no_echo": ("no echo", "no_echo", "no pulse", "dead", "missing"),
    "us_intermittent": ("intermittent", "drop", "no_echo", "no echo", "dropout"),
    "us_out_of_range": ("out of range", "out_of_range", "maximum", "max", "stuck", "swapped"),
    "us_mismatch": ("mismatch", "disagree", "truncat", "wrong", "inconsistent", "does not match", "doesn't match"),
    "us_blocked": ("blocked", "covered", "cover"),
    "joint_stuck": ("stuck", "stall", "not follow", "doesn't move", "does not move", "short of", "dead", "blocked"),
    "joint_offset": ("offset", "calibration", "gap", "short", "long", "disagree", "mismatch"),
    "joint_reversed": ("reversed", "wrong way", "wrong direction", "mirrored", "no echo", "no_echo", "calibration",
                       "disagree", "contradict"),
    "clock_skew": ("clock", "skew", "rate", "slow", "fast", "half"),
    "link_garbled": ("garbled", "garbage", "baud", "parse", "corrupt"),
    "link_silent": ("silent", "no bytes", "quiet", "hung"),
    "link_down": ("down", "not open", "unplugged", "closed"),
    "crash_loop": ("crash", "hardfault", "reboot", "boot loop", "safe mode", "reset"),
    "watchdog_loop": ("watchdog", "hang", "reset", "reboot", "safe mode", "iwdg"),
    "acc_frozen": ("frozen", "identical", "zero noise", "stale", "std 0", "constant"),
    "acc_gravity": ("gravity", "500", "half", "mg", "scale", "range", "1 g"),
    "acc_error": ("i2c", "error", "not responding", "nack", "missing"),
    "tx_overrun": ("overrun", "drop", "uart", "rate", "bandwidth", "tx_drop"),
    "pod_health": ("slow", "stack", "supply", "voltage", "loop"),
    "grasp_missed": ("missed", "empty", "nothing", "grasp"),
    "other": (),
}


def kind_score(kind, accept, gold):
    """1 for an exact kind (any in `accept`), 0.5 for a kind on the gold kind's route, else 0."""
    if kind in accept:
        return 1.0
    if gold != "none" and kind in ROUTE and ROUTE[kind] == ROUTE.get(gold) and ROUTE[kind] != "none":
        return 0.5
    return 0.0


# The manager's action for each route (docs/worker-manager-healer.md §4, manager eval).
ROUTE_ACTION = {"sensor_restart": "restart_sensor", "pod_fw": "pod_rollback", "arm_hardware": "handoff_human",
                "calibration": "handoff_human", "wiring": "handoff_human", "task_redo": "redo_step",
                "none": "no_action", "manager": "handoff_human"}
