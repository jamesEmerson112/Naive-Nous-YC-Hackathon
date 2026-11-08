from romulus_contracts import CONTRACT_VERSION


def test_contract_version_is_v0_1():
    assert CONTRACT_VERSION == "0.1"
