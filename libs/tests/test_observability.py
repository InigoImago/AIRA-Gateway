import logging
import os
from collections.abc import Iterator

import pytest
from opentelemetry import trace
from opentelemetry._logs import _internal as logs_api
from opentelemetry.metrics import _internal as metrics_api
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.util._once import Once

import aira_common.observability as obs

#: The process-wide providers `configure_observability` sets, each with the latch that lets it be
#: set once: (module, provider attribute, latch attribute).
_GLOBAL_PROVIDERS = (
    (trace, "_TRACER_PROVIDER", "_TRACER_PROVIDER_SET_ONCE"),
    (metrics_api, "_METER_PROVIDER", "_METER_PROVIDER_SET_ONCE"),
    (logs_api, "_LOGGER_PROVIDER", "_LOGGER_PROVIDER_SET_ONCE"),
)


@pytest.fixture
def exclusion_unset() -> Iterator[None]:
    """No probe exclusion in the environment, and afterwards whatever was there before.

    `exclude_health_probes` sets the variable with `setdefault`, and `monkeypatch.delenv` on a
    variable that is absent records nothing to undo — so the value would outlive the test.
    """
    before = os.environ.pop(obs.EXCLUDED_URLS_ENV, None)
    yield
    if before is None:
        os.environ.pop(obs.EXCLUDED_URLS_ENV, None)
    else:
        os.environ[obs.EXCLUDED_URLS_ENV] = before


@pytest.fixture
def providers_put_back(monkeypatch: pytest.MonkeyPatch, exclusion_unset: None) -> Iterator[None]:
    """For a test that calls `configure_observability` for real, which changes the whole process.

    It sets three global providers, adds an OTLP handler to the root logger and the probe exclusion
    to the environment, all exporting to an address nothing answers from threads of their own. Left
    in place they serve every later test in the session — each log line queued for an export that
    cannot succeed. So the test gets fresh latches, its providers are shut down afterwards, and what
    was there before is put back.
    """
    # Short, so a shutdown still holding a batch gives up at once rather than retrying a dead
    # address for the exporter's default ten seconds.
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_TIMEOUT", "0.1")
    handlers = list(logging.getLogger().handlers)
    previous = []
    for module, provider, latch in _GLOBAL_PROVIDERS:
        previous.append(getattr(module, provider))
        monkeypatch.setattr(module, provider, getattr(module, provider))
        monkeypatch.setattr(module, latch, Once())

    yield

    root = logging.getLogger()
    for handler in [h for h in root.handlers if h not in handlers]:
        root.removeHandler(handler)
    for (module, provider, _latch), before in zip(_GLOBAL_PROVIDERS, previous, strict=True):
        installed = getattr(module, provider)
        if installed is not None and installed is not before:
            installed.shutdown()


def test_disabled_returns_false() -> None:
    assert (
        obs.configure_observability(service_name="t", endpoint="http://x", enabled=False) is False
    )


def test_empty_endpoint_returns_false() -> None:
    assert obs.configure_observability(service_name="t", endpoint=None, enabled=True) is False


def test_enabled_configures_providers(monkeypatch, providers_put_back: None) -> None:
    monkeypatch.setattr("aira_common.observability.providers._configured", False)
    ok = obs.configure_observability(
        service_name="t",
        service_version="1.2.3",
        endpoint="http://127.0.0.1:1",
        enabled=True,
    )
    assert ok is True
    assert isinstance(trace.get_tracer_provider(), TracerProvider)
    # idempotent second call short-circuits via the guard
    assert obs.configure_observability(service_name="t", endpoint="http://127.0.0.1:1") is True


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


# --- a header that is not ours never stops a consumer --------------------------------------------

TRACEPARENT = "00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01"


def test_a_header_that_is_not_text_does_not_hide_the_trace_beside_it() -> None:
    """A proxy, a connector or a tool may put a binary header on any record. Decoding it raised out
    of `consuming()`, ahead of the consumer's catch-all — and a consumer stopped on a record meets
    the same record after a restart, so revocations stop reaching the gateway for good."""
    headers = [
        ("x-binary", b"\xff\xfe"),
        ("x-null", None),
        ("traceparent", TRACEPARENT.encode()),
    ]

    context = obs.context_from_kafka_headers(headers)  # type: ignore[arg-type]

    span_context = trace.get_current_span(context).get_span_context()
    assert trace.format_trace_id(span_context.trace_id) == "0af7651916cd43dd8448eb211c80319c"


def test_a_traceparent_that_is_not_text_is_no_context_rather_than_an_error() -> None:
    context = obs.context_from_kafka_headers([("traceparent", b"\xff\xfe\x00")])
    assert trace.get_current_span(context).get_span_context().is_valid is False


def test_consuming_a_record_with_a_binary_header_hands_back_its_handle() -> None:
    """The function the consumer actually enters, rather than the one it calls."""
    headers = [("event_type", b"usecase.upserted"), ("x-binary", b"\xff")]
    with obs.consuming("aira.use-cases", headers) as processing:
        assert isinstance(processing, obs.Processing)


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

#: The URLs the instrumentations match a probe by. FastAPI's is built from the scheme, the caller's
#: `Host` header and the path; Django's is `request.build_absolute_uri("?")`, which keeps the `?` of
#: an empty query.
PROBES = (
    "http://gateway:8001/healthz",
    "http://gateway:8001/readyz",
    "http://127.0.0.1:8002/healthz?",
    "http://management:8002/readyz?",
)

#: Requests that must still be traced — above all the ones whose host or path merely *contains* a
#: probe's name, because the host is whatever the caller wrote into its `Host` header.
TRAFFIC = (
    "http://gateway:8001/v1beta/models/x:generateContent",
    "http://gateway:8001/kira/api/external/chat",
    "http://readyz.attacker/v1beta/models/x:generateContent",
    "http://gateway:8001/v1beta/models/healthz-tuned:generateContent",
    "http://management:8002/api/v1/use-cases/readyz-bot/?",
    "http://gateway:8001/healthz/more",
    # `Host: x/healthz` on a request for `/`.
    "http://x/healthz/",
)


def test_the_health_probes_are_excluded_from_tracing(exclusion_unset: None) -> None:
    """Docker asks every 15 seconds per container and each ask is three spans plus its database
    reads. Measured over 65 seconds *with three real requests in the window*: 20 of 86 spans.

    Set in the environment rather than per instrumentor, because there are three of those and
    `opentelemetry.util.http` reads this one variable for all of them.
    """
    from aira_common.observability import HEALTH_PATHS, exclude_health_probes

    assert exclude_health_probes() == HEALTH_PATHS

    from opentelemetry.util.http import parse_excluded_urls

    excluded = parse_excluded_urls(HEALTH_PATHS)
    for probe in PROBES:
        assert excluded.url_disabled(probe), probe
    # And nothing else — a pattern that also swallowed real traffic would be the worse mistake.
    for request in TRAFFIC:
        assert not excluded.url_disabled(request), request


def test_a_caller_cannot_name_its_way_out_of_the_traces() -> None:
    """Through the real ASGI instrumentation, because the URL it matches is built from the caller's
    own `Host` header. A refused credential's span is what `FRD-618` delivers to a SIEM, so a
    client that could make its attempts untraced by sending `Host: readyz.example` would be
    invisible exactly where it is looked for.
    """
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    from aira_common.observability import HEALTH_PATHS

    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    app = FastAPI()

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/v1beta/models/{model}")
    def generate(model: str) -> dict[str, str]:
        return {"model": model}

    FastAPIInstrumentor.instrument_app(app, tracer_provider=provider, excluded_urls=HEALTH_PATHS)
    try:
        client = TestClient(app)

        def spans(method: str, path: str, host: str) -> int:
            exporter.clear()
            client.request(method, path, headers={"host": host})
            return len(exporter.get_finished_spans())

        assert spans("GET", "/healthz", "gateway:8001") == 0
        assert spans("POST", "/v1beta/models/m:generateContent", "gateway:8001") > 0
        assert spans("POST", "/v1beta/models/m:generateContent", "readyz.example") > 0
        assert spans("POST", "/v1beta/models/healthz:generateContent", "gateway:8001") > 0
    finally:
        FastAPIInstrumentor.uninstrument_app(app)


def test_an_installation_that_already_decided_keeps_its_decision(monkeypatch) -> None:
    """`setdefault`, not assignment: somebody who has named their own exclusions has named them
    for a reason, and a library that overwrites them is one they have to work around."""
    from aira_common.observability import EXCLUDED_URLS_ENV, exclude_health_probes

    monkeypatch.setenv(EXCLUDED_URLS_ENV, "metrics")
    assert exclude_health_probes() == "metrics"


def test_configure_observability_is_what_excludes_them(
    monkeypatch, providers_put_back: None
) -> None:
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
    monkeypatch.setattr("aira_common.observability.providers._configured", False)

    observability.configure_observability(
        service_name="probe", endpoint="http://127.0.0.1:1", enabled=True
    )

    import os

    assert os.environ.get(EXCLUDED_URLS_ENV) == HEALTH_PATHS
