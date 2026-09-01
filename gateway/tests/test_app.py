from fastapi import FastAPI
from fastapi.testclient import TestClient

from aira_common.errors import ForbiddenError
from aira_gateway import __version__
from aira_gateway.app import create_app
from aira_gateway.config import GatewaySettings


def test_create_app_uses_settings() -> None:
    app = create_app(GatewaySettings(app_name="custom-gw"))
    assert isinstance(app, FastAPI)
    assert app.title == "custom-gw"
    assert app.version == __version__
    assert app.state.settings.app_name == "custom-gw"


def test_create_app_without_settings_uses_defaults() -> None:
    app = create_app()
    assert app.state.settings.app_name == "aira-gateway"


def test_create_app_instruments_when_otel_enabled(instrumentation_restored: None) -> None:
    """The fixture is the point of the signature: `create_app` now also instruments httpx and
    SQLAlchemy (`FRD-117` FR-5), and those patch **modules**. Without putting them back, this test
    left every later one in the session wrapped, queueing spans for a collector nothing answers —
    and made the test that measures those spans a no-op, because an instrumentor is a singleton
    that refuses a second `instrument()` without a word."""
    app = create_app(GatewaySettings(otel_enabled=True, otel_endpoint="http://localhost:4318"))
    client = TestClient(app)
    assert client.get("/healthz").status_code == 200


def test_aira_error_handler_returns_envelope() -> None:
    app = create_app(GatewaySettings())

    @app.get("/boom")
    async def boom() -> None:
        raise ForbiddenError("nope", details={"why": "test"})

    client = TestClient(app)
    resp = client.get("/boom")
    assert resp.status_code == 403
    assert resp.json() == {
        "error": {"code": "forbidden", "message": "nope", "details": {"why": "test"}}
    }
