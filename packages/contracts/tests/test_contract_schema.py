import json
import subprocess
import sys

import pytest

from romulus_contracts.schema import MODELS, main, schemas
from romulus_contracts.version import CONTRACT_VERSION


def test_schemas_cover_every_model():
    out = schemas()
    assert out["contract_version"] == CONTRACT_VERSION
    assert set(out["models"]) == set(MODELS)
    assert out["models"]["CheckIn"]["properties"]["health"]["enum"] == ["ok", "degraded", "failing"]
    assert out["models"]["IncidentReport"]["additionalProperties"] is True


def test_unknown_model_name():
    with pytest.raises(KeyError):
        schemas(["Nope"])
    assert main(["Nope"]) == 2


def test_main_prints_one_model(capsys):
    assert main(["OverlayStatus"]) == 0
    printed = json.loads(capsys.readouterr().out)
    assert list(printed["models"]) == ["OverlayStatus"]


def test_runs_as_module():
    result = subprocess.run(
        [sys.executable, "-m", "romulus_contracts.schema", "CheckIn"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert "CheckIn" in json.loads(result.stdout)["models"]
