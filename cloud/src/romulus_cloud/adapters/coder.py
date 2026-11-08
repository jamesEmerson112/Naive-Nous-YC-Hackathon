"""Coder: turns a device's code patch into a clean, tested change (D21, D22; M8 seam).

Absorbs which coding agent does the work: an open-source agent in the `coder` sandbox
(`local_agent`) or a Naïve managed session (`naive_session`), both later. FakeCoder passes the
device's patch through unchanged and says so, so the batch path runs end to end today.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True)
class CodeDraft:
    diff: str
    test_output: str
    passed: bool
    notes: str


class Coder(Protocol):
    def draft(self, patch: str, incident: dict[str, Any], code_rev: str) -> CodeDraft:
        """Draft a clean change from `patch` (unified diff against `code_rev`) and test it."""
        ...


class FakeCoder:
    """Returns the device's patch unchanged; runs no tests."""

    def draft(self, patch: str, incident: dict[str, Any], code_rev: str) -> CodeDraft:
        return CodeDraft(
            diff=patch,
            test_output="(no tests run: fake coder)",
            passed=True,
            notes=(
                f"pass-through: coding agent not wired. Patch against {code_rev} "
                f"from incident {incident.get('id', 'unknown')} returned unchanged."
            ),
        )


def build_coder(provider: str, agent: str | None = None, image: str | None = None) -> Coder:
    """Factory called with CODER_PROVIDER, CODER_AGENT, CODER_IMAGE."""
    if provider == "fake":
        return FakeCoder()
    if provider == "local_agent":
        raise NotImplementedError(
            "CODER_PROVIDER=local_agent is an M8 stub: the coder sandbox agent is not built yet"
        )
    if provider == "naive_session":
        raise NotImplementedError(
            "CODER_PROVIDER=naive_session is an M8 stub: Naïve managed sessions are not wired yet"
        )
    raise ValueError(f"unknown CODER_PROVIDER {provider!r}")
