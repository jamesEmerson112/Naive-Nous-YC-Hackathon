import json

import httpx
import pytest

from romulus_cloud.adapters.llm import (
    FAKE_BANNER,
    NAIVE_PROXY_BASE_URL,
    FakeLlm,
    LlmConfigError,
    LlmError,
    LlmReply,
    LlmTimeout,
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


# --- complete (Fleet Doctor, interfaces.md §6.3) ------------------------------------------

SYSTEM = "Answer with JSON only."
USER = "The gripper on arm-1 stalls when closing on a soft object; what should the healer check?"


def test_fake_complete_is_deterministic_and_counts_tokens():
    fake = FakeLlm()
    reply = fake.complete(SYSTEM, USER)
    assert reply == fake.complete(SYSTEM, USER)
    assert isinstance(reply, LlmReply) and reply.model == "fake-templated"
    assert reply.text.startswith(FAKE_BANNER) and USER[:80] in reply.text
    assert USER[80:] not in reply.text
    assert reply.prompt_tokens == (len(SYSTEM) + len(USER)) // 4
    assert reply.completion_tokens == len(reply.text) // 4
    assert reply.usage_estimated is True  # no provider counted these


def test_fake_complete_respects_max_tokens():
    reply = FakeLlm().complete(SYSTEM, USER, max_tokens=5, timeout_s=1)
    assert len(reply.text) <= 20 and reply.completion_tokens <= 5


def _usage(prompt, completion):
    return {"prompt_tokens": prompt, "completion_tokens": completion, "total_tokens": 0}


def test_openai_compat_complete_sends_one_chat_call_and_reads_usage():
    seen = {}

    def handler(request):
        seen["body"] = json.loads(request.content)
        seen["timeout"] = request.extensions.get("timeout")
        body = _completion('  {"hypotheses": []}  ') | {"usage": _usage(120, 7)}
        return httpx.Response(200, json=body)

    reply = _llm(handler).complete(SYSTEM, USER, max_tokens=400, timeout_s=3.5)
    assert reply == LlmReply('{"hypotheses": []}', "test-model", 120, 7)
    body = seen["body"]
    assert body["model"] == "test-model" and body["max_tokens"] == 400
    assert body["messages"] == [
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content": USER},
    ]
    assert "response_format" not in body
    assert seen["timeout"]["read"] == 3.5


def test_openai_compat_complete_defaults_and_missing_usage():
    seen = {}

    def handler(request):
        seen["body"] = json.loads(request.content)
        seen["timeout"] = request.extensions.get("timeout")
        return httpx.Response(200, json=_completion("A loose cable."))

    reply = _llm(handler).complete(SYSTEM, USER)
    # No usage: estimated from the text, so the cost never reads as $0.
    assert reply == LlmReply(
        "A loose cable.",
        "test-model",
        prompt_tokens=(len(SYSTEM) + len(USER)) // 4,
        completion_tokens=len("A loose cable.") // 4,
        usage_estimated=True,
    )
    assert reply.prompt_tokens > 0 and reply.completion_tokens > 0
    assert seen["body"]["max_tokens"] == 800  # the client's default
    assert seen["timeout"]["read"] == 30  # the client's default


def test_openai_compat_complete_estimates_only_the_missing_count():
    body = _completion("A loose cable.") | {"usage": {"prompt_tokens": 50}}
    reply = _llm(lambda r: httpx.Response(200, json=body)).complete(SYSTEM, USER)
    assert (reply.prompt_tokens, reply.completion_tokens) == (50, len("A loose cable.") // 4)
    assert reply.usage_estimated is True
    full = _completion("x") | {"usage": _usage(50, 9)}
    reply = _llm(lambda r: httpx.Response(200, json=full)).complete(SYSTEM, USER)
    assert (reply.prompt_tokens, reply.completion_tokens, reply.usage_estimated) == (50, 9, False)


def test_openai_compat_complete_errors_raise_llm_error():
    with pytest.raises(LlmError):
        _llm(lambda r: httpx.Response(500, json={"error": {"message": "boom"}})).complete("s", "u")
    with pytest.raises(LlmError):
        _llm(lambda r: httpx.Response(200, json=_completion("  "))).complete("s", "u")


def test_openai_compat_complete_timeout_is_not_retried_and_says_so():
    calls = []

    def handler(request):
        calls.append(1)
        raise httpx.ReadTimeout("slow model", request=request)

    with pytest.raises(LlmTimeout) as err:
        _llm(handler).complete("s", "u", timeout_s=0.5)
    assert isinstance(err.value, LlmError)
    assert len(calls) == 1  # the client retries once by default; a timed call doesn't
