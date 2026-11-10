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


def test_configure_process_passes_each_signal_its_own_url(monkeypatch) -> None:
    """The settings reach the function that builds the exporters, in the gateway and its workers."""
    import aira_gateway.config as config_module

    seen: dict[str, object] = {}
    monkeypatch.setattr(
        config_module, "configure_observability", lambda **kwargs: seen.update(kwargs) or True
    )
    config_module.configure_process(
        GatewaySettings(
            otel_enabled=True,
            otel_traces_endpoint="http://tempo/v1/traces",
            otel_metrics_endpoint="http://mimir/otlp/v1/metrics",
            otel_logs_endpoint="http://loki/otlp/v1/logs",
        )
    )

    assert seen["endpoint"] == "http://localhost:4318"
    assert (
        seen["traces_endpoint"],
        seen["metrics_endpoint"],
        seen["logs_endpoint"],
    ) == ("http://tempo/v1/traces", "http://mimir/otlp/v1/metrics", "http://loki/otlp/v1/logs")


def test_a_gateway_with_metrics_and_logs_but_no_traces_serves_requests(
    monkeypatch, instrumentation_restored: None
) -> None:
    """Traces exported nowhere — no base, no URL of their own — while metrics and logs are. The
    instrumentation still wraps every request, against OpenTelemetry's no-op tracer, and the
    gateway answers, refuses and starts up exactly as with all three signals exported."""
    from opentelemetry import trace
    from opentelemetry.sdk.trace import TracerProvider

    monkeypatch.setenv("OTEL_EXPORTER_OTLP_TIMEOUT", "0.1")
    monkeypatch.setattr("aira_common.observability.providers._configured", False)
    settings = GatewaySettings(
        otel_enabled=True,
        otel_endpoint="",
        otel_metrics_endpoint="http://127.0.0.1:1/otlp/v1/metrics",
        otel_logs_endpoint="http://127.0.0.1:1/otlp/v1/logs",
    )

    with TestClient(create_app(settings)) as client:
        assert client.get("/healthz").status_code == 200
        refused = client.get("/v1beta/models")
        assert refused.status_code == 401
        assert refused.json()["error"]["code"] == 401

    assert not isinstance(trace.get_tracer_provider(), TracerProvider)


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
