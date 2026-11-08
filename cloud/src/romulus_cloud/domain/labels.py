"""Automatic labels (`source = auto`) for recordings and incidents (telemetry §5).

Pure and deterministic: the same inputs always give the same set. Labels are `key:value`
strings (`intervention` has no value); `split_label` turns one into the labels table's
`key` / `value` columns.

| Label | Rule |
|---|---|
| `kind:<incident kind>` | the incident's `kind`, as sent (open string) |
| `outcome:resolved` | `outcome.resolved` is true |
| `outcome:escalated` | unresolved and a work order was created (`escalated=True`) |
| `outcome:unresolved` | unresolved and not escalated *(inferred; not in telemetry §5)* |
| `cause:software` / `cause:hardware` | `outcome.suspected_cause`; a `park` outcome is hardware |
| `cause:unknown` | any other cause, or no outcome (contracts: unknown causes read as unknown) |
| `healer:<category>` | the healer's `outcome.category`, e.g. `healer:park` *(inferred)* |
| `overlay:held` / `overlay:reverted` / `overlay:locked_in` | `overlay_status` (overlay events) |
| `episode:success` / `episode:failure` | the recording's `episode.success` (absent → no label) |
| `intervention` | `intervention=True` (any frame had `intervention = true`; the caller reads it) |
| `firmware:v<N>` | `running.firmware`, from the recording, else the incident |
"""

from romulus_contracts import IncidentReport, RecordingMeta

KNOWN_CAUSES = ("software", "hardware")
OVERLAY_STATUSES = ("held", "reverted", "locked_in")


def auto_labels(
    recording: RecordingMeta | None,
    incident: IncidentReport | None,
    *,
    overlay_status: str | None = None,
    escalated: bool = False,
    intervention: bool = False,
) -> set[str]:
    """The automatic labels for a recording and/or its incident (either may be None)."""
    labels: set[str] = set()

    if incident is not None:
        if incident.kind.strip():
            labels.add(f"kind:{incident.kind.strip()}")
        outcome = incident.outcome
        resolved = bool(outcome and outcome.resolved)
        if resolved:
            labels.add("outcome:resolved")
        elif escalated:
            labels.add("outcome:escalated")
        else:
            labels.add("outcome:unresolved")
        cause = (outcome.suspected_cause if outcome else "").strip().lower()
        category = ((outcome.category if outcome else None) or "").strip().lower()
        if category == "park":
            cause = "hardware"
        labels.add(f"cause:{cause if cause in KNOWN_CAUSES else 'unknown'}")
        if category:
            labels.add(f"healer:{category}")

    status = (overlay_status or "").strip().lower()
    if status in OVERLAY_STATUSES:
        labels.add(f"overlay:{status}")

    if recording is not None and recording.episode is not None:
        if recording.episode.success is True:
            labels.add("episode:success")
        elif recording.episode.success is False:
            labels.add("episode:failure")

    if intervention:
        labels.add("intervention")

    firmware = None
    for running in (
        recording.running if recording else None,
        incident.running if incident else None,
    ):
        if running is not None and running.firmware is not None:
            firmware = running.firmware
            break
    if firmware is not None:
        labels.add(f"firmware:v{firmware}")

    return labels


def split_label(label: str) -> tuple[str, str | None]:
    """`"kind:gripper_stall"` → `("kind", "gripper_stall")`; `"intervention"` → `(…, None)`."""
    key, sep, value = label.partition(":")
    return (key, value) if sep else (key, None)
