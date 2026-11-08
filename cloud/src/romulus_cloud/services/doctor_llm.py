"""The Fleet Doctor's LLM: the D22 adapter, chosen by DOCTOR_MODE (interfaces.md §6.3).

`fake` always gets FakeLlm, even if LLM_MODE=live, so a demo or test never spends money by
accident. `live` uses the configured provider (LLM_PROVIDER, LLM_BASE_URL, LLM_MODEL,
LLM_API_KEY). Per-call timeouts and token limits are passed to `Llm.complete`, not here.
Shared by the L1 ask route (services/doctor_ask.py) and the L2 consult (services/doctor.py).
"""

from __future__ import annotations

from romulus_cloud.adapters.llm import Llm, LlmConfigError, build_llm
from romulus_cloud.settings import Settings


def build_doctor_llm(settings: Settings) -> Llm:
    """FakeLlm unless DOCTOR_MODE=live; raises LlmConfigError when the Doctor is off."""
    if settings.doctor_mode == "off":
        raise LlmConfigError("DOCTOR_MODE=off: the Fleet Doctor has no LLM")
    if settings.doctor_mode == "fake":
        return build_llm("fake", "fake", None, None, None)
    return build_llm(
        "live",
        settings.llm_provider,
        settings.llm_base_url,
        settings.llm_model,
        settings.llm_api_key.get_secret_value(),
    )
