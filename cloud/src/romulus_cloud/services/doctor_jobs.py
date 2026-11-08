"""Off-tick jobs for the Fleet Doctor (docs/fleet-doctor/interfaces.md §6.2): a thin adapter
over M8b's shared `services/jobs.py`.

`run_tick` runs steps one after another, so an LLM, coder or sandbox call inside a step would
stall the gate and every other step. Instead `doctor.tick` submits a job under a stable key
(`diagnose:{consult_id}`, `draft:{consult_id}`, `check:{consult_id}`), returns, and polls it
on later ticks. The rules are the shared module's: a job function takes plain data and never
touches the database; `submit` is idempotent (a failed key reruns); `poll` says `Pending`,
`Done`, `Failed` or `Missing` (resubmit); `forget` once the result is applied and committed.

The Doctor gets its own named pool (`"doctor"`, DOCTOR_JOB_WORKERS threads), so a long coder
draft or sandbox check never queues behind the batch's drafts (`"coder.draft"`) or the
gate's replays (`"coder.replay"`), and those never wait on the Doctor.
"""

from romulus_cloud.services import jobs as _jobs
from romulus_cloud.services.jobs import (
    Done,
    Failed,
    InlineJobs,
    Jobs,
    JobState,
    Missing,
    Pending,
    Status,
    ThreadPoolJobs,
)
from romulus_cloud.settings import Settings

POOL = "doctor"

__all__ = [
    "POOL",
    "Done",
    "Failed",
    "InlineJobs",
    "JobState",
    "Jobs",
    "Missing",
    "Pending",
    "Status",
    "ThreadPoolJobs",
    "default_jobs",
]


def default_jobs(settings: Settings) -> Jobs:
    """The worker's process-wide `"doctor"` pool, sized by DOCTOR_JOB_WORKERS on first use."""
    return _jobs.default_jobs(max(1, settings.doctor_job_workers), POOL)
