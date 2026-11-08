"""Every table, in one file for v0 (v0 plan §4, telemetry §4, D21).

Conventions:
- Every table has `fleet_id` (D1).
- Tables that devices write keep the request body verbatim in `raw` (BUILD §2 principle 3).
- Device-reported objects carry both `occurred_at` (device clock) and `received_at`
  (server clock). Sort by `occurred_at` (architecture §10).
- Server IDs are prefixed strings from `ids.new_id`; incident and recording IDs come
  from the device (D8). Versions are integers that go up within each fleet.
- Versions are never edited; they are created, read and retired (v0 plan §4).
"""

from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    type_annotation_map = {
        datetime: DateTime(timezone=True),
        dict[str, Any]: JSONB,
        list[Any]: JSONB,
        str: String,
    }


def _fleet() -> Mapped[str]:
    return mapped_column(String, default="default", index=True)


def _created() -> Mapped[datetime]:
    return mapped_column(server_default=func.now())


def _raw() -> Mapped[dict[str, Any] | None]:
    return mapped_column(JSONB, nullable=True)


# --- fleet control ---------------------------------------------------------------------


class FleetState(Base):
    """One row per fleet. The kill switch and the batch "run now" button live here."""

    __tablename__ = "fleet_state"

    fleet_id: Mapped[str] = mapped_column(primary_key=True)
    frozen: Mapped[bool] = mapped_column(default=False)
    frozen_by: Mapped[str | None]
    frozen_at: Mapped[datetime | None]
    frozen_reason: Mapped[str | None] = mapped_column(Text)
    batch_requested: Mapped[bool] = mapped_column(default=False)
    batch_last_run_at: Mapped[datetime | None]
    updated_at: Mapped[datetime] = _created()


# --- registry ----------------------------------------------------------------------------


class Device(Base):
    """One arm: real, sim or test."""

    __tablename__ = "devices"

    id: Mapped[str] = mapped_column(primary_key=True)  # e.g. "arm-1", chosen at registration
    fleet_id: Mapped[str] = _fleet()
    kind: Mapped[str] = mapped_column(default="real")  # real | sim | test
    device_class: Mapped[str] = mapped_column(default="so101")
    role: Mapped[str] = mapped_column(default="normal")  # normal | canary
    assigned_firmware: Mapped[int | None]
    assigned_harness: Mapped[int | None]
    running_firmware: Mapped[int | None]
    running_harness: Mapped[int | None]
    running_code_rev: Mapped[str | None]
    running_mcu_build: Mapped[str | None]  # reported, never assigned (APIs §2.3)
    last_good_firmware: Mapped[int | None]
    last_good_harness: Mapped[int | None]
    overlay_id: Mapped[str | None]
    health: Mapped[str | None]  # ok | degraded | failing
    metrics: Mapped[dict[str, Any] | None]  # latest check-in metrics
    contract_version: Mapped[str | None]
    last_seen_at: Mapped[datetime | None]
    created_at: Mapped[datetime] = _created()
    raw: Mapped[dict[str, Any] | None] = _raw()  # last check-in body


class FirmwareVersion(Base):
    """A firmware manifest (D3). Never edited."""

    __tablename__ = "firmware_versions"

    fleet_id: Mapped[str] = mapped_column(primary_key=True, default="default")
    version: Mapped[int] = mapped_column(primary_key=True)
    device_class: Mapped[str] = mapped_column(default="so101")
    manifest: Mapped[dict[str, Any]]
    sha256: Mapped[str]
    parent_version: Mapped[int | None]
    source: Mapped[str] = mapped_column(default="seed")  # seed | manual | batch
    why: Mapped[str] = mapped_column(Text, default="")
    retired: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = _created()


class HarnessVersion(Base):
    """A harness package the cloud doesn't parse beyond `harness.json` (D4). Never edited."""

    __tablename__ = "harness_versions"

    fleet_id: Mapped[str] = mapped_column(primary_key=True, default="default")
    version: Mapped[int] = mapped_column(primary_key=True)
    path: Mapped[str]  # blob path of the tarball
    sha256: Mapped[str]
    meta: Mapped[dict[str, Any] | None]  # parsed harness.json
    parent_version: Mapped[int | None]
    source: Mapped[str] = mapped_column(default="seed")
    why: Mapped[str] = mapped_column(Text, default="")
    retired: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = _created()


class CodeVersion(Base):
    """A `.tar.gz` of `device/` at one revision (D21, APIs §2.10). Never edited."""

    __tablename__ = "code_versions"

    fleet_id: Mapped[str] = mapped_column(primary_key=True, default="default")
    rev: Mapped[str] = mapped_column(primary_key=True)  # what manifests call `code_rev`
    path: Mapped[str]  # blob path of the tarball
    sha256: Mapped[str]
    size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    parent_rev: Mapped[str | None]
    source: Mapped[str] = mapped_column(default="seed")  # seed | manual | coder | passthrough
    why: Mapped[str] = mapped_column(Text, default="")
    retired: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = _created()


class InstallReport(Base):
    """One install result from a device (APIs §2.4). Kept as history next to the device row."""

    __tablename__ = "install_reports"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    fleet_id: Mapped[str] = _fleet()
    device_id: Mapped[str] = mapped_column(ForeignKey("devices.id"), index=True)
    type: Mapped[str]  # firmware | harness | code
    version: Mapped[str]  # int for firmware/harness, rev for code; stored as text
    result: Mapped[str]  # installed | rolled_back | hash_mismatch
    detail: Mapped[str | None] = mapped_column(Text)
    received_at: Mapped[datetime]
    raw: Mapped[dict[str, Any] | None] = _raw()


# --- healing loop ------------------------------------------------------------------------


class Incident(Base):
    """One fault report. The id is a UUID made on the device, so retries are harmless (D8)."""

    __tablename__ = "incidents"

    id: Mapped[str] = mapped_column(primary_key=True)
    fleet_id: Mapped[str] = _fleet()
    device_id: Mapped[str] = mapped_column(ForeignKey("devices.id"), index=True)
    occurred_at: Mapped[datetime] = mapped_column(index=True)
    received_at: Mapped[datetime]
    kind: Mapped[str] = mapped_column(index=True)
    summary: Mapped[str] = mapped_column(Text, default="")
    running: Mapped[dict[str, Any] | None]
    agent_reasoning: Mapped[str | None] = mapped_column(Text)
    actions: Mapped[list[Any] | None]
    overlay_id: Mapped[str | None]
    resolved: Mapped[bool | None]
    resolving_action: Mapped[str | None]
    suspected_cause: Mapped[str | None]  # software | hardware | unknown
    signals: Mapped[dict[str, Any] | None]
    recording_ids: Mapped[list[Any] | None]
    raw: Mapped[dict[str, Any] | None] = _raw()


class Overlay(Base):
    """A local patch on one device (D5a).

    The device makes the id (APIs §1), and ids like `ov-1` are per-device counters, so the
    key is `(device_id, id)`. Anything that points at an overlay carries both: event refs
    use {"device": ..., "overlay": ...}; `candidates.from_overlays` holds
    [{"device_id": ..., "overlay_id": ...}].
    """

    __tablename__ = "overlays"

    device_id: Mapped[str] = mapped_column(ForeignKey("devices.id"), primary_key=True)
    id: Mapped[str] = mapped_column(primary_key=True)
    fleet_id: Mapped[str] = _fleet()
    incident_id: Mapped[str | None] = mapped_column(index=True)
    base_firmware: Mapped[int | None]
    base_harness: Mapped[int | None]
    base_code_rev: Mapped[str | None]
    diff: Mapped[dict[str, Any] | None]  # rules/params diff
    code_patch: Mapped[str | None] = mapped_column(Text)  # unified diff against base_code_rev
    touches_safety: Mapped[bool] = mapped_column(default=False)  # D5a, K17
    status: Mapped[str] = mapped_column(default="active")  # active|held|reverted|locked_in|dropped
    status_reason: Mapped[str | None] = mapped_column(Text)
    candidate_id: Mapped[str | None]  # set when locked in
    occurred_at: Mapped[datetime | None]
    received_at: Mapped[datetime]
    updated_at: Mapped[datetime | None]
    raw: Mapped[dict[str, Any] | None] = _raw()


class Candidate(Base):
    """A proposed version moving through the gate (D6)."""

    __tablename__ = "candidates"

    id: Mapped[str] = mapped_column(primary_key=True)  # cand_…
    fleet_id: Mapped[str] = _fleet()
    type: Mapped[str]  # firmware | harness | code
    version: Mapped[int | None]  # firmware/harness version it proposes
    code_rev: Mapped[str | None]  # for code candidates
    device_class: Mapped[str] = mapped_column(default="so101")
    # [{"device_id": "sim-1", "overlay_id": "ov-1"}, ...]; overlay ids are per device
    from_overlays: Mapped[list[Any]] = mapped_column(JSONB, default=list)
    status: Mapped[str] = mapped_column(default="replaying", index=True)
    # replaying | awaiting_canary | canary | promoted | rolled_back | frozen
    canary_device: Mapped[str | None]
    canary_started_at: Mapped[datetime | None]
    replay: Mapped[dict[str, Any] | None]  # cases checked and passed
    verdict: Mapped[dict[str, Any] | None]
    why: Mapped[str] = mapped_column(Text, default="")
    touches_safety: Mapped[bool] = mapped_column(default=False)
    stub_stages: Mapped[list[Any]] = mapped_column(JSONB, default=list)  # e.g. ["draft_code"]
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime | None]


class WorkOrder(Base):
    """A hardware fix handed to Naïve. This table is also the outbox (naive-integration §3.1)."""

    __tablename__ = "work_orders"

    id: Mapped[str] = mapped_column(primary_key=True)  # wo_…
    fleet_id: Mapped[str] = _fleet()
    # One order per incident, so a re-run or racing triage can never post two cards.
    incident_id: Mapped[str] = mapped_column(unique=True, index=True)
    device_id: Mapped[str] = mapped_column(index=True)
    part: Mapped[str]
    max_usd: Mapped[float] = mapped_column(Numeric(10, 2, asdecimal=False))  # reads as float
    status: Mapped[str] = mapped_column(default="pending", index=True)
    # pending | posted | in_progress | done | stopped | needs_human | blocked_over_cap
    naive_card_id: Mapped[str | None]
    naive_updated_at: Mapped[str | None]  # Naïve's updated_at, compared as given
    result: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime | None]
    raw: Mapped[dict[str, Any] | None] = _raw()  # last card as Naïve returned it


# --- telemetry and datasets (telemetry §4) ----------------------------------------------


class Recording(Base):
    """One uploaded evidence file's metadata. The id is a UUID made on the device."""

    __tablename__ = "recordings"

    id: Mapped[str] = mapped_column(primary_key=True)
    fleet_id: Mapped[str] = _fleet()
    device_id: Mapped[str] = mapped_column(ForeignKey("devices.id"), index=True)
    kind: Mapped[str] = mapped_column(index=True)  # open string (BUILD §2 principle 3)
    format: Mapped[str]  # open string
    sha256: Mapped[str] = mapped_column(index=True)
    size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    started_at: Mapped[datetime | None] = mapped_column(index=True)
    ended_at: Mapped[datetime | None]
    received_at: Mapped[datetime]
    firmware: Mapped[int | None]
    harness: Mapped[int | None]
    overlay_id: Mapped[str | None]
    incident_id: Mapped[str | None] = mapped_column(index=True)
    episode: Mapped[dict[str, Any] | None]
    streams: Mapped[list[Any] | None]
    schema_version: Mapped[str | None]
    raw: Mapped[dict[str, Any] | None] = _raw()


class Blob(Base):
    """A content-addressed file on disk. Shared by every recording with the same sha256."""

    __tablename__ = "blobs"

    sha256: Mapped[str] = mapped_column(primary_key=True)
    fleet_id: Mapped[str] = _fleet()
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    media_type: Mapped[str | None]
    path: Mapped[str]
    created_at: Mapped[datetime] = _created()


class Label(Base):
    """Append-only. Removing a label writes a tombstone row (`removed = true`)."""

    __tablename__ = "labels"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    fleet_id: Mapped[str] = _fleet()
    target_type: Mapped[str]  # recording | incident
    target_id: Mapped[str]
    key: Mapped[str]
    value: Mapped[str | None]
    source: Mapped[str]  # auto | agent | human | robometer
    author: Mapped[str | None]
    confidence: Mapped[float | None] = mapped_column(Float)
    removed: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = _created()

    __table_args__ = (Index("ix_labels_target", "target_type", "target_id"),)


class MetricSample(Base):
    """Environmental history: one row per check-in's `metrics` (APIs §2.3)."""

    __tablename__ = "metric_samples"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    fleet_id: Mapped[str] = _fleet()
    device_id: Mapped[str] = mapped_column(ForeignKey("devices.id"))
    ts: Mapped[datetime]  # server receive time; check-ins carry no device time
    metrics: Mapped[dict[str, Any]]

    __table_args__ = (Index("ix_metric_samples_device_ts", "device_id", "ts"),)


class Dataset(Base):
    """A named, saved query over recordings and incidents."""

    __tablename__ = "datasets"

    id: Mapped[str] = mapped_column(primary_key=True)  # ds_…
    fleet_id: Mapped[str] = _fleet()
    name: Mapped[str]
    purpose: Mapped[str]  # firmware | harness | policy
    filter: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = _created()


class DatasetVersion(Base):
    """An immutable snapshot of a dataset's matches. Later label changes don't touch it."""

    __tablename__ = "dataset_versions"

    dataset_id: Mapped[str] = mapped_column(ForeignKey("datasets.id"), primary_key=True)
    n: Mapped[int] = mapped_column(Integer, primary_key=True)
    fleet_id: Mapped[str] = _fleet()
    recording_ids: Mapped[list[Any]] = mapped_column(JSONB, default=list)
    incident_ids: Mapped[list[Any]] = mapped_column(JSONB, default=list)
    manifest_sha256: Mapped[str | None]
    created_at: Mapped[datetime] = _created()


class Export(Base):
    """An export file the worker builds from a dataset version."""

    __tablename__ = "exports"

    id: Mapped[str] = mapped_column(primary_key=True)  # exp_…
    fleet_id: Mapped[str] = _fleet()
    dataset_id: Mapped[str] = mapped_column(ForeignKey("datasets.id"))
    n: Mapped[int]
    format: Mapped[str]  # regression_corpus | traces_jsonl | lerobot
    status: Mapped[str] = mapped_column(default="requested", index=True)
    path: Mapped[str | None]
    sha256: Mapped[str | None]
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime | None]


class RegressionCase(Base):
    """One per resolved incident. Feeds the gate's replay (telemetry §6.1)."""

    __tablename__ = "regression_cases"

    incident_id: Mapped[str] = mapped_column(primary_key=True)
    fleet_id: Mapped[str] = _fleet()
    kind: Mapped[str]
    signals: Mapped[dict[str, Any]]
    expected_action: Mapped[str]
    added_at: Mapped[datetime] = _created()
    last_result: Mapped[dict[str, Any] | None]


# --- the spine ---------------------------------------------------------------------------


class Event(Base):
    """Append-only log of every step (D9). Write it only through `events.record`."""

    __tablename__ = "events"

    id: Mapped[str] = mapped_column(primary_key=True)  # evt_…
    fleet_id: Mapped[str] = _fleet()
    ts: Mapped[datetime] = mapped_column(index=True)  # when it happened
    recorded_at: Mapped[datetime] = _created()  # when the server wrote it
    actor: Mapped[str]  # device:arm-1 | agent:batch | gate | naive:ses_… | human:kent
    actor_class: Mapped[str] = mapped_column(index=True)  # agent | automation | human
    kind: Mapped[str] = mapped_column(index=True)  # APIs §5
    refs: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    why: Mapped[str] = mapped_column(Text)
    data: Mapped[dict[str, Any] | None]

    __table_args__ = (Index("ix_events_refs", "refs", postgresql_using="gin"),)
