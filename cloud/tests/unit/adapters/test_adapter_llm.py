import json

import httpx
import pytest
from romulus_cloud.adapters.llm import (
    FAKE_BANNER,
    NAIVE_PROXY_BASE_URL,
    FakeLlm,
    LlmConfigError,
    LlmError,
    OpenAICompatLlm,
    build_llm,
)

INCIDENT = {"id": "inc-1", "device_id": "arm-1", "fault_kind": "gripper_stall"}


def _completion(text):
    return {
        "id": "chatcmpl-1",
        "object": "chat.completion",
        "created": 0,
        "model": "m",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": text},
                "finish_reason": "stop",
            }
        ],
    }


def _llm(handler):
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return OpenAICompatLlm("https://llm.test/v1", "test-model", "sk-test", http_client=client)


def test_fake_is_deterministic_and_announces_itself():
    fake = FakeLlm()
    assert fake.model == "fake-templated"
    assert fake.explain(INCIDENT) == fake.explain(INCIDENT)
    assert FAKE_BANNER in fake.explain(INCIDENT)
    skill = fake.write_skill(INCIDENT)
    assert FAKE_BANNER in skill
    assert "gripper_stall" in skill and skill.startswith("# Skill")


def test_fake_tolerates_sparse_incident():
    assert "unknown" in FakeLlm().explain({})


def test_openai_compat_sends_chat_completion():
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        seen["auth"] = request.headers["authorization"]
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json=_completion("  The gripper stalled.  "))

    assert _llm(handler).explain(INCIDENT) == "The gripper stalled."
    assert seen["url"] == "https://llm.test/v1/chat/completions"
    assert seen["auth"] == "Bearer sk-test"
    assert seen["body"]["model"] == "test-model"
    assert seen["body"]["max_tokens"] == 800
    roles = [m["role"] for m in seen["body"]["messages"]]
    assert roles == ["system", "user"]
    assert json.loads(seen["body"]["messages"][1]["content"]) == INCIDENT


def test_openai_compat_error_raises_llm_error():
    llm = _llm(lambda r: httpx.Response(500, json={"error": {"message": "boom"}}))
    with pytest.raises(LlmError):
        llm.write_skill(INCIDENT)


def test_openai_compat_transport_error_raises_llm_error():
    def handler(request):
        raise httpx.ConnectError("no wifi", request=request)

    with pytest.raises(LlmError):
        _llm(handler).explain(INCIDENT)


def test_openai_compat_empty_answer_raises_llm_error():
    with pytest.raises(LlmError):
        _llm(lambda r: httpx.Response(200, json=_completion(""))).explain(INCIDENT)


def test_factory():
    assert isinstance(build_llm("fake", "naive_proxy", None, None, None), FakeLlm)
    proxy = build_llm("live", "naive_proxy", None, "gpt-x", "sk")
    assert isinstance(proxy, OpenAICompatLlm)
    assert str(proxy._client.base_url).rstrip("/") == NAIVE_PROXY_BASE_URL
    runpod = build_llm("live", "runpod", "https://pod.test/v1", "qwen", "rp")
    assert str(runpod._client.base_url).rstrip("/") == "https://pod.test/v1"
    with pytest.raises(ValueError):
        build_llm("live", "runpod", None, "qwen", "rp")
    with pytest.raises(ValueError):
        build_llm("live", "naive_proxy", None, None, "sk")
    with pytest.raises(ValueError):
        build_llm("live", "claude", None, "m", "sk")
    with pytest.raises(ValueError):
        build_llm("bogus", "naive_proxy", None, "m", "sk")


def test_live_mode_with_fake_provider_is_a_config_error():
    with pytest.raises(LlmConfigError, match="LLM_PROVIDER"):
        build_llm("live", "fake", None, None, None)


def test_factory_passes_timeout_retries_and_max_tokens():
    llm = build_llm(
        "live",
        "runpod",
        "https://pod.test/v1",
        "qwen",
        "rp",
        timeout_s=5,
        max_retries=0,
        max_tokens=123,
    )
    assert llm.model == "qwen"
    assert llm.max_tokens == 123
    assert llm._client.timeout == 5
    assert llm._client.max_retries == 0
    default = build_llm("live", "naive_proxy", None, "openai/gpt-oss-120b", "sk")
    assert default._client.timeout == 30 and default._client.max_retries == 1
