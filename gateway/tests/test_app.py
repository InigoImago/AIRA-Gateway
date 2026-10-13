from fastapi import FastAPI
from fastapi.testclient import TestClient

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


# ---- what a deployment shows of itself ------------------------------------------------------

_DOCUMENTS = ("/docs", "/redoc", "/openapi.json")


def test_a_deployment_does_not_publish_its_own_map() -> None:
    """`/openapi.json` names every route — the incident, payload and content-read endpoints with
    their parameters — and `/docs` renders it, for anyone who can reach the port."""
    deployed = GatewaySettings(
        environment="production",
        postgres_password="a-real-secret",  # noqa: S106
        kafka_security_protocol="SASL_SSL",
    )
    with TestClient(create_app(deployed)) as client:
        statuses = {path: client.get(path).status_code for path in _DOCUMENTS}

    assert statuses == dict.fromkeys(_DOCUMENTS, 404)


def test_a_laptop_and_a_declared_demo_still_have_their_documentation() -> None:
    demo = GatewaySettings(
        environment="production", demo_mode=True, kafka_security_protocol="SASL_SSL"
    )
    for settings in (GatewaySettings(), demo):
        with TestClient(create_app(settings)) as client:
            statuses = {path: client.get(path).status_code for path in _DOCUMENTS}
        assert statuses == dict.fromkeys(_DOCUMENTS, 200), settings.environment


def test_local_is_one_decision_however_it_is_spelled() -> None:
    """`AIRA_ENVIRONMENT=Local` waived every production check (`security.is_local_environment`
    normalises) and did not register the mock upstream (`app` compared the raw string) — two
    readings of one setting."""
    from aira_gateway.upstreams.mock import MockProvider

    app = create_app(GatewaySettings(environment="Local"))

    assert any(isinstance(provider, MockProvider) for provider in app.state.providers.each())
