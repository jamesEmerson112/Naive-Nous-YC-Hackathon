"""Llm: the batch job's prose writer (D22).

Absorbs which model provider is behind it: one OpenAI-compatible client serves both the Naïve
model proxy and the team's RunPod vLLM endpoint; FakeLlm is the offline default. The LLM writes
prose and harness skills only, never merged config (PRD principle 7), so the methods return
plain strings that callers store as text.

Notes from the live spike (Sat Nov 7):
- The Naïve proxy's `/chat/completions` works with a plain bearer key; no `Vetta-Version`.
- `GET <proxy>/models` returns 404, so never list models through the proxy; pick model ids
  from Naïve's catalogue instead.
- `openai/gpt-oss-120b` answers well in about 2 s. `zai-org/GLM-5.2-FP8` reasons until the
  token limit and returns empty content, which is why an empty answer raises LlmError.
- `max_tokens` is always sent: without it the proxy holds the model's full output limit
  against the org balance for the call.

`complete` (Fleet Doctor, docs/fleet-doctor/interfaces.md §6.3) is the one generic call: a
system and a user message in, an `LlmReply` with the token counts out, so callers can price
it. No `response_format` is sent (not every provider has JSON mode); the prompt asks for
JSON and `domain.doctor.parse_hypotheses` tolerates prose.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Protocol

import httpx
import openai

NAIVE_PROXY_BASE_URL = "https://api.vetta.sh/v1/proxy/openai/v1"
DEFAULT_TIMEOUT_S = 30.0
DEFAULT_MAX_RETRIES = 1
DEFAULT_MAX_TOKENS = 800
FAKE_MODEL = "fake-templated"

FAKE_BANNER = "[templated by FakeLlm: no model was called]"


class LlmError(Exception):
    """A live LLM call failed; the caller should fall back (e.g. to FakeLlm text)."""


class LlmTimeout(LlmError):
    """The call ran past its timeout (the Doctor's ask answers 504 instead of 502)."""


@dataclass(frozen=True)
class LlmReply:
    text: str
    model: str
    prompt_tokens: int
    completion_tokens: int


class LlmConfigError(ValueError):
    """The LLM settings don't describe a usable provider."""


class Llm(Protocol):
    model: str  # recorded on events so the timeline says which model wrote the prose

    def explain(self, incident: dict[str, Any]) -> str:
        """A plain-English explanation of an incident and its fix."""
        ...

    def write_skill(self, incident: dict[str, Any]) -> str:
        """A harness skill (markdown) distilled from an incident."""
        ...

    def complete(
        self,
        system: str,
        user: str,
        *,
        max_tokens: int | None = None,
        timeout_s: float | None = None,
    ) -> LlmReply:
        """One chat call: the reply's text and token counts. Raises LlmError (LlmTimeout
        when it ran out of time)."""
        ...


def _summary(incident: dict[str, Any]) -> tuple[str, str, str]:
    return (
        str(incident.get("id", "unknown")),
        str(incident.get("device_id", "unknown device")),
        str(incident.get("fault_kind") or incident.get("kind") or "unknown fault"),
    )


class FakeLlm:
    """Deterministic templated text that says it's templated."""

    model = FAKE_MODEL

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

    def complete(
        self,
        system: str,
        user: str,
        *,
        max_tokens: int | None = None,
        timeout_s: float | None = None,
    ) -> LlmReply:
        """The banner plus prose echoing the first 80 characters of `user`. Tokens are
        `len // 4` (cut to `max_tokens`), so caps and spend can be tested."""
        text = f"{FAKE_BANNER} Likely cause, from the request: {user[:80]!r}."
        if max_tokens is not None:
            text = text[: max(max_tokens, 0) * 4]
        return LlmReply(
            text=text,
            model=self.model,
            prompt_tokens=(len(system) + len(user)) // 4,
            completion_tokens=len(text) // 4,
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
        max_retries: int = DEFAULT_MAX_RETRIES,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        http_client: httpx.Client | None = None,
    ) -> None:
        self.model = model
        self.max_tokens = max_tokens
        self._client = openai.OpenAI(
            base_url=base_url,
            api_key=api_key or "unset",
            timeout=timeout_s,
            max_retries=max_retries,
            http_client=http_client,
        )

    def _chat(self, system: str, incident: dict[str, Any]) -> str:
        user = json.dumps(incident, default=str, sort_keys=True)
        try:
            resp = self._client.chat.completions.create(
                model=self.model,
                max_tokens=self.max_tokens,
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

    def complete(
        self,
        system: str,
        user: str,
        *,
        max_tokens: int | None = None,
        timeout_s: float | None = None,
    ) -> LlmReply:
        """One chat call. A per-call `timeout_s` is a hard budget, so that call isn't retried.
        Usage comes from `resp.usage` (0 when the provider leaves it out)."""
        client = self._client
        if timeout_s is not None:
            client = client.with_options(timeout=timeout_s, max_retries=0)
        try:
            resp = client.chat.completions.create(
                model=self.model,
                max_tokens=max_tokens if max_tokens is not None else self.max_tokens,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
            )
        except openai.APITimeoutError as exc:
            raise LlmTimeout(f"LLM call to {self.model} timed out: {exc}") from exc
        except openai.OpenAIError as exc:
            raise LlmError(f"LLM call to {self.model} failed: {exc}") from exc
        text = resp.choices[0].message.content if resp.choices else None
        if not text or not text.strip():
            raise LlmError(f"LLM {self.model} returned an empty answer")
        usage = resp.usage
        return LlmReply(
            text=text.strip(),
            model=self.model,
            prompt_tokens=int(getattr(usage, "prompt_tokens", 0) or 0),
            completion_tokens=int(getattr(usage, "completion_tokens", 0) or 0),
        )


def build_llm(
    mode: str,
    provider: str,
    base_url: str | None,
    model: str | None,
    api_key: str | None,
    timeout_s: float = DEFAULT_TIMEOUT_S,
    max_retries: int = DEFAULT_MAX_RETRIES,
    max_tokens: int = DEFAULT_MAX_TOKENS,
) -> Llm:
    """Factory called with LLM_MODE, LLM_PROVIDER, LLM_BASE_URL, LLM_MODEL, LLM_API_KEY."""
    if mode == "fake":
        return FakeLlm()
    if mode != "live":
        raise LlmConfigError(f"unknown LLM_MODE {mode!r} (expected fake or live)")
    if provider == "fake":
        raise LlmConfigError(
            "LLM_MODE=live with LLM_PROVIDER=fake: set LLM_PROVIDER to naive_proxy or runpod, "
            "or set LLM_MODE=fake"
        )
    if provider == "naive_proxy":
        base_url = base_url or NAIVE_PROXY_BASE_URL
    elif provider == "runpod":
        if not base_url:
            raise LlmConfigError("LLM_PROVIDER=runpod needs LLM_BASE_URL (the vLLM endpoint)")
    else:
        raise LlmConfigError(f"unknown LLM_PROVIDER {provider!r}")
    if not model:
        raise LlmConfigError("live LLM needs LLM_MODEL")
    return OpenAICompatLlm(
        base_url,
        model,
        api_key or "",
        timeout_s=timeout_s,
        max_retries=max_retries,
        max_tokens=max_tokens,
    )
