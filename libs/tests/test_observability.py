from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider

import aira_common.observability as obs


def test_disabled_returns_false() -> None:
    assert (
        obs.configure_observability(service_name="t", endpoint="http://x", enabled=False) is False
    )


def test_empty_endpoint_returns_false() -> None:
    assert obs.configure_observability(service_name="t", endpoint=None, enabled=True) is False


def test_enabled_configures_providers(monkeypatch) -> None:
    monkeypatch.setattr(obs, "_configured", False)
    ok = obs.configure_observability(
        service_name="t",
        service_version="1.2.3",
        endpoint="http://localhost:4318",
        enabled=True,
    )
    assert ok is True
    assert isinstance(trace.get_tracer_provider(), TracerProvider)
    # idempotent second call short-circuits via the guard
    assert obs.configure_observability(service_name="t", endpoint="http://localhost:4318") is True


def test_trace_context_fields_empty_without_span() -> None:
    assert obs.trace_context_fields() == {}


def test_trace_context_fields_with_active_span() -> None:
    tracer = TracerProvider().get_tracer("test")
    with tracer.start_as_current_span("s") as span:
        fields = obs.trace_context_fields()
        expected = trace.format_trace_id(span.get_span_context().trace_id)
    assert fields["trace_id"] == expected
    assert "span_id" in fields


def test_kafka_context_roundtrip() -> None:
    tracer = TracerProvider().get_tracer("test")
    with tracer.start_as_current_span("producer") as span:
        headers = obs.kafka_headers_from_context()
        expected = span.get_span_context().trace_id

    assert any(key == "traceparent" for key, _ in headers)
    assert all(isinstance(value, bytes) for _, value in headers)

    ctx = obs.context_from_kafka_headers(headers)
    assert trace.get_current_span(ctx).get_span_context().trace_id == expected


def test_context_from_headers_accepts_str_values() -> None:
    tp = "00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01"
    ctx = obs.context_from_kafka_headers([("traceparent", tp)])
    span_ctx = trace.get_current_span(ctx).get_span_context()
    assert span_ctx.is_valid
    assert trace.format_trace_id(span_ctx.trace_id) == "0af7651916cd43dd8448eb211c80319c"


def test_context_from_empty_headers_is_invalid() -> None:
    ctx = obs.context_from_kafka_headers(None)
    assert trace.get_current_span(ctx).get_span_context().is_valid is False


def test_build_resource_attributes() -> None:
    assert obs.build_resource_attributes("svc", "local") == {
        "service.name": "svc",
        "deployment.environment": "local",
    }
