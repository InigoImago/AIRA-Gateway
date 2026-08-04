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
