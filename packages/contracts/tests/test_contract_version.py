import romulus_contracts
from romulus_contracts.version import (
    CONTRACT_VERSION,
    PREVIOUS_CONTRACT_VERSION,
    SUPPORTED_VERSIONS,
    is_supported,
)


def test_contract_version_is_v0_1():
    assert CONTRACT_VERSION == "0.1"
    assert romulus_contracts.CONTRACT_VERSION == CONTRACT_VERSION


def test_supported_is_current_plus_previous():
    assert SUPPORTED_VERSIONS[0] == CONTRACT_VERSION
    assert len(SUPPORTED_VERSIONS) <= 2
    if PREVIOUS_CONTRACT_VERSION is None:
        assert SUPPORTED_VERSIONS == (CONTRACT_VERSION,)
    else:
        assert SUPPORTED_VERSIONS == (CONTRACT_VERSION, PREVIOUS_CONTRACT_VERSION)


def test_is_supported():
    assert is_supported("0.1")
    for bad in ["0.0", "0.2", "1.0", "0.1.0", " 0.1", "", None, 0.1, 1]:
        assert not is_supported(bad), bad
