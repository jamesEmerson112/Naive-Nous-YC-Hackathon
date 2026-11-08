import json

from fastapi.testclient import TestClient

from romulus_cloud.errors import ApiError
from romulus_cloud.main import create_app
from romulus_contracts import CONTRACT_VERSION


def test_healthz_checks_the_test_db(client):
    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.json() == {"status": "ok", "db": "ok", "contract_version": CONTRACT_VERSION}


def test_healthz_returns_503_in_the_error_shape_when_db_is_down(settings):
    app = create_app(settings)  # points at a port nothing listens on; lifespan not run
    r = TestClient(app).get("/healthz")
    assert r.status_code == 503
    body = r.json()
    assert body["error"] == "db_unavailable" and set(body) == {"error", "detail"}


def test_unknown_route_uses_the_error_shape(client):
    r = client.get("/v1/nope")
    assert r.status_code == 404
    assert r.json() == {"error": "not_found", "detail": "Not Found"}


def test_api_error_and_crash_use_the_error_shape(app):
    @app.get("/boom/api")
    def _api():
        raise ApiError(409, "sha256_mismatch", "The blob's hash didn't match X-Sha256.")

    @app.get("/boom/crash")
    def _crash():
        raise RuntimeError("bug")

    c = TestClient(app, raise_server_exceptions=False)
    r = c.get("/boom/api")
    assert r.status_code == 409
    assert r.json() == {
        "error": "sha256_mismatch",
        "detail": "The blob's hash didn't match X-Sha256.",
    }
    r = c.get("/boom/crash")
    assert r.status_code == 500 and r.json()["error"] == "internal_error"


def test_validation_error_uses_the_error_shape(app):
    @app.get("/typed/{n}")
    def _typed(n: int):
        return {"n": n}

    r = TestClient(app).get("/typed/abc")
    assert r.status_code == 422
    assert r.json()["error"] == "invalid_request" and "n" in r.json()["detail"]


def test_one_json_log_line_per_request(client, capsys):
    capsys.readouterr()
    client.get("/healthz")
    lines = [json.loads(x) for x in capsys.readouterr().out.splitlines() if x.startswith("{")]
    requests = [x for x in lines if x["msg"] == "request"]
    assert len(requests) == 1
    assert requests[0]["path"] == "/healthz" and requests[0]["status"] == 200
