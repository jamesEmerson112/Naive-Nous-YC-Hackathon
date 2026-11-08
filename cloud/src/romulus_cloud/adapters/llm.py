"""Llm: the batch job's prose writer (D22).

Absorbs which model provider is behind it: one OpenAI-compatible client serves both the Naïve
model proxy and the team's RunPod vLLM endpoint; FakeLlm is the offline default. The LLM writes
prose and harness skills only, never merged config (PRD principle 7), so the methods return
plain strings that callers store as text.
"""

from __future__ import annotations

import json
from typing import Any, Protocol

import httpx
import openai

NAIVE_PROXY_BASE_URL = "https://api.vetta.sh/v1/proxy/openai/v1"
DEFAULT_TIMEOUT_S = 60.0

FAKE_BANNER = "[templated by FakeLlm: no model was called]"


class LlmError(Exception):
    """A live LLM call failed; the caller should fall back (e.g. to FakeLlm text)."""


class Llm(Protocol):
    def explain(self, incident: dict[str, Any]) -> str:
        """A plain-English explanation of an incident and its fix."""
        ...

    def write_skill(self, incident: dict[str, Any]) -> str:
        """A harness skill (markdown) distilled from an incident."""
        ...


def _summary(incident: dict[str, Any]) -> tuple[str, str, str]:
    return (
        str(incident.get("id", "unknown")),
        str(incident.get("device_id", "unknown device")),
        str(incident.get("fault_kind") or incident.get("kind") or "unknown fault"),
    )


class FakeLlm:
    """Deterministic templated text that says it's templated."""

    def explain(self, incident: dict[str, Any]) -> str:
        iid, device, fault = _summary(incident)
        return f"{FAKE_BANNER} Incident {iid} on {device}: {fault}. The device agent's fix held."

    def write_skill(self, incident: dict[str, Any]) -> str:
        iid, device, fault = _summary(incident)
        return (
            f"# Skill: handle {fault}\n\n"
            f"{FAKE_BANNER}\n\n"
            f"Learned from incident {iid} on {device}. "
            "When this fault appears, park the arm, check health, and apply the known fix.\n"
        )


EXPLAIN_SYSTEM = (
    "You explain robot-arm incidents to an operator in 2-4 plain sentences: what failed, "
    "what the on-device agent did, and whether it held. No markdown, no speculation."
)
SKILL_SYSTEM = (
    "You write a short markdown skill for the robot's on-device agent from one incident: "
    "a title line, when it applies, and numbered steps. Do not write config or code."
)


class OpenAICompatLlm:
    """Chat Completions against any OpenAI-compatible base URL (Naïve proxy or RunPod vLLM)."""

    def __init__(
        self,
        base_url: str,
        model: str,
        api_key: str,
        timeout_s: float = DEFAULT_TIMEOUT_S,
        http_client: httpx.Client | None = None,
    ) -> None:
        self.model = model
        self._client = openai.OpenAI(
            base_url=base_url,
            api_key=api_key or "unset",
            timeout=timeout_s,
            max_retries=1,
            http_client=http_client,
        )

    def _chat(self, system: str, incident: dict[str, Any]) -> str:
        user = json.dumps(incident, default=str, sort_keys=True)
        try:
            resp = self._client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
            )
        except openai.OpenAIError as exc:
            raise LlmError(f"LLM call to {self.model} failed: {exc}") from exc
        text = resp.choices[0].message.content if resp.choices else None
        if not text or not text.strip():
            raise LlmError(f"LLM {self.model} returned an empty answer")
        return text.strip()

    def explain(self, incident: dict[str, Any]) -> str:
        return self._chat(EXPLAIN_SYSTEM, incident)

    def write_skill(self, incident: dict[str, Any]) -> str:
        return self._chat(SKILL_SYSTEM, incident)


def build_llm(
    mode: str,
    provider: str,
    base_url: str | None,
    model: str | None,
    api_key: str | None,
    timeout_s: float = DEFAULT_TIMEOUT_S,
) -> Llm:
    """Factory called with LLM_MODE, LLM_PROVIDER, LLM_BASE_URL, LLM_MODEL, LLM_API_KEY."""
    if mode == "fake" or provider == "fake":
        return FakeLlm()
    if mode != "live":
        raise ValueError(f"unknown LLM_MODE {mode!r} (expected fake or live)")
    if provider == "naive_proxy":
        base_url = base_url or NAIVE_PROXY_BASE_URL
    elif provider == "runpod":
        if not base_url:
            raise ValueError("LLM_PROVIDER=runpod needs LLM_BASE_URL (the vLLM endpoint)")
    else:
        raise ValueError(f"unknown LLM_PROVIDER {provider!r}")
    if not model:
        raise ValueError("live LLM needs LLM_MODEL")
    return OpenAICompatLlm(base_url, model, api_key or "", timeout_s)
