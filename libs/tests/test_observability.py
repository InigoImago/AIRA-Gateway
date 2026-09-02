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


def test_set_span_attributes_sets_non_none() -> None:
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))

    with provider.get_tracer("t").start_as_current_span("s"):
        obs.set_span_attributes({"aira.subject": "u", "aira.use_case": None, "n": 3})

    attributes = exporter.get_finished_spans()[0].attributes
    assert attributes["aira.subject"] == "u"
    assert attributes["n"] == 3
    assert "aira.use_case" not in attributes


# --- health probes are not requests -------------------------------------------------------------


def test_the_health_probes_are_excluded_from_tracing(monkeypatch) -> None:
    """Docker asks every 15 seconds per container and each ask is three spans plus its database
    reads. Measured over 65 seconds *with three real requests in the window*: 20 of 86 spans.

    Set in the environment rather than per instrumentor, because there are three of those and
    `opentelemetry.util.http` reads this one variable for all of them.
    """
    from aira_common.observability import EXCLUDED_URLS_ENV, HEALTH_PATHS, exclude_health_probes

    monkeypatch.delenv(EXCLUDED_URLS_ENV, raising=False)
    assert exclude_health_probes() == HEALTH_PATHS

    from opentelemetry.util.http import parse_excluded_urls

    excluded = parse_excluded_urls(HEALTH_PATHS)
    assert excluded.url_disabled("http://gateway:8001/healthz")
    assert excluded.url_disabled("http://gateway:8001/readyz")
    # And nothing else — a pattern that also swallowed real traffic would be the worse mistake.
    assert not excluded.url_disabled("http://gateway:8001/v1beta/models/x:generateContent")
    assert not excluded.url_disabled("http://gateway:8001/kira/api/external/chat")


def test_an_installation_that_already_decided_keeps_its_decision(monkeypatch) -> None:
    """`setdefault`, not assignment: somebody who has named their own exclusions has named them
    for a reason, and a library that overwrites them is one they have to work around."""
    from aira_common.observability import EXCLUDED_URLS_ENV, exclude_health_probes

    monkeypatch.setenv(EXCLUDED_URLS_ENV, "metrics")
    assert exclude_health_probes() == "metrics"


def test_configure_observability_is_what_excludes_them(monkeypatch) -> None:
    """**Upstream of the wire.** The test above calls `exclude_health_probes` directly, so removing
    the call from `configure_observability` left it green — the mutation said so. A test that
    builds the object under test is a test of the reader (`LESSONS.md` §1); this one drives the
    function every process actually calls.

    Asserted on the environment rather than on a span, because that is where the instrumentations
    read it and because no instrumentor is constructed here.
    """
    import aira_common.observability as observability
    from aira_common.observability import EXCLUDED_URLS_ENV, HEALTH_PATHS

    monkeypatch.delenv(EXCLUDED_URLS_ENV, raising=False)
    # `configure_observability` is idempotent by design and returns early once configured, so the
    # module-level latch is cleared — otherwise this asserts about a previous test's call.
    monkeypatch.setattr(observability, "_configured", False)

    observability.configure_observability(
        service_name="probe", endpoint="http://127.0.0.1:1", enabled=True
    )

    import os

    assert os.environ.get(EXCLUDED_URLS_ENV) == HEALTH_PATHS
