import pytest

from romulus_cloud.adapters.llm import FakeLlm, LlmConfigError
from romulus_cloud.services.doctor_llm import build_doctor_llm


def test_off_has_no_llm(make_settings):
    with pytest.raises(LlmConfigError):
        build_doctor_llm(make_settings(doctor_mode="off"))


def test_fake_is_fake_even_when_llm_mode_is_live(make_settings):
    s = make_settings(doctor_mode="fake").model_copy(update={"llm_mode": "live"})
    assert isinstance(build_doctor_llm(s), FakeLlm)


def test_live_uses_the_configured_provider(make_settings):
    s = make_settings(doctor_mode="live").model_copy(
        update={"llm_provider": "naive_proxy", "llm_model": "gpt-oss-120b"}
    )
    llm = build_doctor_llm(s)
    assert not isinstance(llm, FakeLlm)
    assert llm.model == "gpt-oss-120b"
