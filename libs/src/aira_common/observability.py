"""OpenTelemetry bootstrap shared by AIRA services (FRD-001).

Sets up tracer/meter/logger providers exporting via OTLP/HTTP to the OpenTelemetry
Collector, plus helpers for W3C trace-context propagation over Kafka message headers.
Everything is gated by an ``enabled`` flag so tests and low-resource setups can run
without a collector.
"""

from __future__ import annotations

import base64
import contextlib
import json
import logging
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, cast

from opentelemetry import metrics, trace
from opentelemetry._logs import set_logger_provider
from opentelemetry.context import Context
from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter
from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.propagate import extract, inject
from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.sdk.trace.sampling import ParentBased, TraceIdRatioBased
from opentelemetry.trace import Status, StatusCode
from opentelemetry.util.types import Attributes, AttributeValue

KafkaHeaders = list[tuple[str, bytes]]

_configured = False


#: Paths that produce a span nobody wants: the health probes.
#:
#: Docker asks every 15 seconds per container and each ask produces **three** spans through the
#: ASGI instrumentation, plus the database reads underneath. Measured on 2026-09-02 over 65
#: seconds *with three real requests in the window*: 20 of 86 spans, 23 %. In a quiet minute it is
#: nearly all of them.
#:
#: That is three costs, not one: a trace backend full of probes, constant OTLP volume against
#: whatever quota the far end has, and — the one that was reported — a debug payload whose first
#: `n` items are always health checks, so the request you came to look at is never in it.
#:
#: A probe is not a request. It has no caller, no use case and no outcome worth attributing, and
#: `/readyz` already reports its own verdict in a form built for reading (`FRD-117`).
HEALTH_PATHS = "healthz,readyz"

#: The environment variable both instrumentations read, in the absence of a per-library one.
EXCLUDED_URLS_ENV = "OTEL_PYTHON_EXCLUDED_URLS"


def exclude_health_probes(paths: str = HEALTH_PATHS) -> str:
    """Keep the health probes out of the traces, for every instrumentation at once.

    Set in the **environment** rather than passed to each instrumentor, because there are three of
    them — FastAPI, Django, and whatever is added next — and `opentelemetry.util.http` reads this
    variable for all of them. A per-instrumentor argument is a fourth place to forget.

    An installation that has already decided otherwise keeps its decision: this never overwrites a
    value somebody set. Returns what is in force, so a caller can say so and a test can assert it
    without reading the environment back.
    """
    import os

    return os.environ.setdefault(EXCLUDED_URLS_ENV, paths)


def configure_observability(
    *,
    service_name: str,
    service_version: str = "0.0.0",
    environment: str = "local",
    endpoint: str | None = None,
    enabled: bool = True,
    sample_ratio: float = 1.0,
) -> bool:
    """Configure global OTel providers exporting OTLP/HTTP to ``endpoint``.

    Returns True if observability was configured, False if it was disabled or no
    endpoint was given (a no-op). Safe to call more than once (idempotent).
    """
    global _configured
    if not enabled or not endpoint:
        return False
    if _configured:
        return True

    resource = Resource.create(
        {
            "service.name": service_name,
            "service.version": service_version,
            "deployment.environment": environment,
        }
    )
    base = endpoint.rstrip("/")

    # **Before anything is instrumented.** `opentelemetry.util.http` reads the exclusion list when
    # an instrumentor is constructed, so setting it afterwards excludes nothing.
    exclude_health_probes()

    # **Before the first exporter exists.** What the SDK says about a failed export must stop
    # propagating to the root logger, because the handler installed there a few lines below is the
    # OTLP one — see :class:`SdkDiagnostics` for what that cost.
    route_sdk_diagnostics()

    traces = f"{base}/v1/traces"
    metrics_endpoint = f"{base}/v1/metrics"
    logs = f"{base}/v1/logs"

    tracer_provider = TracerProvider(
        resource=resource, sampler=ParentBased(TraceIdRatioBased(sample_ratio))
    )
    tracer_provider.add_span_processor(
        BatchSpanProcessor(watched_export(OTLPSpanExporter(endpoint=traces), "traces", traces))
    )
    trace.set_tracer_provider(tracer_provider)

    meter_provider = MeterProvider(
        resource=resource,
        metric_readers=[
            PeriodicExportingMetricReader(
                watched_export(
                    OTLPMetricExporter(endpoint=metrics_endpoint), "metrics", metrics_endpoint
                )
            )
        ],
    )
    metrics.set_meter_provider(meter_provider)

    logger_provider = LoggerProvider(resource=resource)
    logger_provider.add_log_record_processor(
        BatchLogRecordProcessor(watched_export(OTLPLogExporter(endpoint=logs), "logs", logs))
    )
    set_logger_provider(logger_provider)
    # Bridge stdlib logging (framework logs) to OTLP; structlog app logs keep their
    # trace-id/span-id correlation via the logging processor and stream to stdout.
    logging.getLogger().addHandler(LoggingHandler(logger_provider=logger_provider))

    _configured = True
    return True


#: The response body's `partial_success` field, per signal. The collector answers **200 with a
#: body** when it took some of a batch and dropped the rest — a full queue, an attribute limit, a
#: bad timestamp — and the Python exporter reads `resp.ok` and nothing else, so every one of those
#: is a clean `SpanExportResult.SUCCESS`.
#:
#: That is this project's own sentence turned back on it: *"no errors" and "it arrived" are
#: different statements*. `tools/lab_status.py` was written because a collector log cannot say a
#: delivery succeeded; the channel then reported `result: SUCCESS` for a batch the collector had
#: partly thrown away, which is worse than silence because somebody reads it.
_REJECTED_FIELD = {
    "traces": "rejected_spans",
    "metrics": "rejected_data_points",
    "logs": "rejected_log_records",
}


def _partial_success(signal: str, body: bytes) -> tuple[int, str]:
    """``(rejected, reason)`` from an OTLP response body. ``(0, "")`` when it took everything.

    An empty body is the ordinary full-success answer and parses to zeros, so the common path
    costs one protobuf parse of nothing. Anything unparseable is reported as *no rejection* rather
    than as a failure: this reads a response the caller already treated as a success, and a
    diagnostic that turned an unfamiliar body into an error would break the export it is watching.
    """
    if not body:
        return 0, ""
    try:
        if signal == "traces":
            from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import (
                ExportTraceServiceResponse as Response,
            )
        elif signal == "logs":
            from opentelemetry.proto.collector.logs.v1.logs_service_pb2 import (
                ExportLogsServiceResponse as Response,
            )
        else:
            from opentelemetry.proto.collector.metrics.v1.metrics_service_pb2 import (
                ExportMetricsServiceResponse as Response,
            )
        parsed = Response()
        parsed.ParseFromString(body)
        rejected = int(getattr(parsed.partial_success, _REJECTED_FIELD[signal], 0))
        return rejected, str(parsed.partial_success.error_message)
    except Exception:  # noqa: BLE001 — see the docstring: never fail the export being watched
        return 0, ""


#: How many items of a batch :func:`payload_as_json` renders. 0 is off and is the default.
#:
#: A **number** rather than a flag, because the useful request is "show me three spans", never
#: "show me the 512 this batch happens to hold". The rendered document is a real OTLP/JSON
#: document of that many items — truncated in content, faithful in shape, which is what somebody
#: hands to the team that has to parse it.
_payload_items = 0


def set_payload_rendering(items: int) -> int:
    """How many items per export to render as OTLP/JSON. Returns what is now set."""
    global _payload_items
    _payload_items = max(0, int(items))
    return _payload_items


#: The encoder per signal — the **exporter's own**, so what is printed is what would be sent and
#: not a second rendering that agrees with it until one of them changes.
_ENCODERS: dict[str, tuple[str, str]] = {
    "traces": ("opentelemetry.exporter.otlp.proto.common.trace_encoder", "encode_spans"),
    "logs": ("opentelemetry.exporter.otlp.proto.common._internal._log_encoder", "encode_logs"),
    "metrics": (
        "opentelemetry.exporter.otlp.proto.common._internal.metrics_encoder",
        "encode_metrics",
    ),
}


#: The OTLP fields that are protobuf `bytes` and are **hex** in OTLP/JSON, not base64.
#:
#: This is the whole reason `MessageToJson` is not enough on its own. Protobuf's JSON mapping turns
#: every `bytes` field into base64 — `"traceId": "TETTPxm0Rt5w6G3guyzfIA=="` — and OTLP overrides
#: that for identifiers, which a receiver expects as `"884f54bb8d85bdab950bf58afbaf110d"`.
#:
#: Reported by a reader: *the encoding is wrong, characters come through that cannot be parsed.*
#: Correct — base64 carries `+`, `/` and `=`, none of which belongs in a trace id, and an id in the
#: wrong alphabet is one nothing can be looked up by. Measured against a real collector writing the
#: same span, which is the reference this now matches.
_HEX_FIELDS = frozenset({"traceId", "spanId", "parentSpanId"})


def _to_otlp_json(document: Any) -> Any:
    """Turn protobuf's JSON into **OTLP's** JSON: the identifiers as hex.

    Walks the whole document rather than the paths that carry ids today, because they appear on
    spans, on span links, on log records and on metric exemplars — four places, and a fifth is a
    version away. `LESSONS.md` §1: recognise a shape, do not remember a list of names.
    """
    if isinstance(document, dict):
        return {
            key: (
                base64.b64decode(value).hex()
                if key in _HEX_FIELDS and isinstance(value, str)
                else _to_otlp_json(value)
            )
            for key, value in document.items()
        }
    if isinstance(document, list):
        return [_to_otlp_json(item) for item in document]
    return document


def payload_as_json(signal: str, args: tuple[Any, ...], items: int) -> str:
    """The batch as OTLP/JSON — what a receiver would be handed, rendered readably.

    Rendered to match a **collector's** OTLP/JSON byte for byte — hex identifiers, integer enums —
    because the point of printing it is to show what a receiver is handed. Protobuf's own JSON
    mapping gets both wrong; see :data:`_HEX_FIELDS`.

    **Not what goes over this leg.** The applications post `application/x-protobuf` and cannot be
    made to post JSON (`INTEGRATIONS.md` §6), so this is the protobuf-JSON *mapping* of the same
    content: the shape a collector produces with `encoding: json`, and the shape a SIEM has to
    parse. Said plainly because printing JSON beside a protobuf request invites the conclusion that
    the encoding is switchable, and it is not.

    Rendered through the exporter's own encoder, so it cannot drift from what is actually sent.

    Metrics are handed over whole: they arrive as a tree rather than a sequence, so there is no
    first-`n` to take and truncating one would produce a document that is not one.

    Never raises. A debug rendering that could fail an export is not worth having, and this runs
    inside the exporter.
    """
    if not args or items <= 0:
        return ""
    try:
        import importlib

        from google.protobuf.json_format import MessageToJson

        module_name, function_name = _ENCODERS[signal]
        encode = getattr(importlib.import_module(module_name), function_name)
        batch = args[0]
        # Metrics arrive as a tree rather than a sequence, so there is no first-`n` to take and
        # slicing raises — handed over whole, as the docstring says.
        with contextlib.suppress(TypeError):
            batch = batch[:items]
        # `use_integers_for_enums`, because that is what a collector emits — `"kind": 1` and not
        # `"kind": "SPAN_KIND_INTERNAL"`. Both are legal protobuf JSON; only one is what the
        # receiver on the other end will actually be handed.
        rendered = MessageToJson(encode(batch), use_integers_for_enums=True)
        # **Compact, and on one line.** Indented output was the first shape and it broke the tools
        # a log is read with: a payload spanning forty lines is no longer one line of the log, so
        # `tail -1` returns a closing brace and `grep` returns a fragment. Everything else this
        # system writes is one event per line, and pretty-printing is what the reader's `jq` is
        # for. Separators without spaces because the escaped copy sits inside another JSON string,
        # where every byte is paid for twice.
        return str(json.dumps(_to_otlp_json(json.loads(rendered)), separators=(",", ":")))
    except Exception:  # noqa: BLE001 — never the reason an export fails
        return ""


class WatchedExport:
    """An OTLP exporter that says what each export attempt did (`FRD-617` §3.1).

    The SDK hands a batch to an exporter on a background thread and keeps the answer to itself: a
    success is reported nowhere at any log level, and a failure is reported on a stdlib logger
    whose only handler — with OTLP logging on — is the exporter that just failed. So the question
    *"did anything actually get through"* had no answer inside the process at all, and
    `tools/lab_status.py` answers only the leg **after** this one.

    Written by delegation rather than by subclassing, because the three signals do not share a
    base: `SpanExporter.export(spans)`, `LogExporter.export(batch)` and
    `MetricExporter.export(metrics_data, timeout_millis, **kwargs)` differ in signature, and
    `PeriodicExportingMetricReader` reaches past the interface entirely to read
    `_preferred_temporality` and `_preferred_aggregation` off whatever it is given. `__getattr__`
    forwarding satisfies all of that without this module knowing which of the three it holds.

    **Always wrapped, gated per call.** The alternative — wrap only when the channel is on — makes
    the wiring itself conditional on a setting read at start-up, and this project has paid for
    exactly that shape often enough to name it (`LESSONS.md` §1). `watch` costs one set membership
    test when nobody is watching, and an export happens on a timer, not per request.
    """

    __slots__ = ("_endpoint", "_exporter", "_last", "_signal")

    def __init__(self, exporter: Any, signal: str, endpoint: str) -> None:
        self._exporter = exporter
        self._signal = signal
        self._endpoint = endpoint
        #: The last HTTP response the wrapped exporter received, captured by `_watch_transport`.
        self._last: Any = None
        self._watch_transport()

    def _watch_transport(self) -> None:
        """Keep the HTTP response the exporter is about to throw away.

        `export()` reduces the whole exchange to a three-valued enum: `_export` posts and returns a
        `requests.Response`, and the caller reads `resp.ok`. The status code and the
        `partial_success` body are gone by the time anything outside can look, so this replaces
        `_export` **on the instance** — not on the class, which every exporter in the process
        shares — with a wrapper that records the response and returns it unchanged.

        Reaching for a private method is a real cost and is taken deliberately: the alternatives
        are a `requests` adapter (which would see every call the process makes, not this one) or
        reimplementing the exporter. `test_the_exporter_still_has_the_seam_we_reach_through` fails
        on the upgrade that renames it, so this degrades **loudly** rather than going quiet — the
        `LESSONS.md` §7 shape, a control that goes vacuous on an upgrade with nothing failing.
        """
        inner = getattr(self._exporter, "_export", None)
        if not callable(inner):
            return

        def capture(*args: Any, **kwargs: Any) -> Any:
            response = inner(*args, **kwargs)
            self._last = response
            return response

        with contextlib.suppress(AttributeError, TypeError):
            self._exporter._export = capture  # noqa: SLF001 — the seam, see the docstring

    def __getattr__(self, name: str) -> Any:
        # `_exporter` itself is a slot, so it is found by normal lookup and never arrives here —
        # except before `__init__` has run, where forwarding would recurse until the stack ends.
        if name == "_exporter":
            raise AttributeError(name)
        return getattr(self._exporter, name)

    def _show_payload(self, args: tuple[Any, ...]) -> None:
        """Print the batch as OTLP/JSON, when somebody asked for it.

        **Before the export**, so the payload is on the terminal even when the export then fails —
        which is the case somebody turns this on for.

        Marked local-only for the reason every `otel` line is (`FRD-617` §3.2): a rendering of a
        log batch, logged, would join the next log batch. That is not a slow leak here but a
        doubling per export.
        """
        if _payload_items <= 0:
            return
        rendered = payload_as_json(self._signal, args, _payload_items)
        if rendered:
            # Imported here, like `SdkDiagnostics` below: `aira_common.logging` imports this
            # module for the access-log redaction, and a second edge the other way is a cycle.
            from aira_common.logging import get_logger

            get_logger("aira_common.observability").info(
                "otel_payload",
                signal=self._signal,
                shown=_payload_items,
                payload=rendered,
                local_only=True,
            )

    def export(self, *args: Any, **kwargs: Any) -> Any:
        from aira_common.integration_debug import watch

        self._last = None
        fields: dict[str, Any] = {
            "signal": self._signal,
            "target": self._endpoint,
            "items": _batch_size(args),
        }
        if self._signal == "traces":
            # **Not the batch size — the number of requests in it.** See `_distinct_traces`: this
            # is the field that answers "did mine get out", which the batch size cannot. Added
            # only for the signal where it is a question: a `traces: null` beside a metrics export
            # is noise, and `items` already carries the "we did not count" case for that batch.
            fields["traces"] = _distinct_traces(args)
        with watch("otel", "export", **fields) as call:
            self._show_payload(args)
            result = self._exporter.export(*args, **kwargs)
            # **The failure that raises nothing.** An OTLP exporter answers `FAILURE` as a *value*
            # after it has exhausted its own retries, so a channel watching only for exceptions
            # would report every unsuccessful export as a success that took a while — which is the
            # exact reassurance this feature exists to stop giving.
            name = getattr(result, "name", str(result))
            call.note(result=name)
            # **The status this `SUCCESS` is actually about.** `export()` returns SUCCESS on any
            # 2xx, so the enum attests one thing: the next hop answered. Reported as its own field
            # rather than folded into the word, because "the collector took it" and "the collector
            # answered" are the two statements this whole feature exists to keep apart.
            status = getattr(self._last, "status_code", None)
            if status is not None:
                call.note(http_status=status)
            rejected, reason = _partial_success(
                self._signal, getattr(self._last, "content", b"") or b""
            )
            if rejected:
                # A batch the collector partly threw away is not a success, whatever the enum says.
                call.note(rejected=rejected)
                call.failed(f"the collector rejected {rejected} of them: {reason or 'no reason'}")
            elif name != "SUCCESS":
                call.failed(f"exporter returned {name}")
            return result


def watched_export[Exporter](exporter: Exporter, signal: str, endpoint: str) -> Exporter:
    """Wrap ``exporter`` in :class:`WatchedExport`, keeping its type for the SDK's signature.

    The cast is the one place this file is less than honest with the type checker, and it is a true
    statement about run time: `WatchedExport` forwards every attribute it does not define, so it is
    a structural stand-in for whichever of the three exporter protocols it holds. The three
    protocols have no common base — `SpanExporter`, `MetricExporter` and `LogExporter` are
    unrelated classes with different `export` signatures — so the alternative is three casts at
    three call sites, and a cast repeated three times is one that is eventually written a fourth
    time around something that is *not* a stand-in.
    """
    return cast(Exporter, WatchedExport(exporter, signal, endpoint))


def _distinct_traces(args: tuple[Any, ...]) -> int | None:
    """How many **requests** this batch of spans came from. `None` where that is not a question.

    The field that makes the line legible, reported after somebody watched a request go through
    the pipeline and could not tell whether its telemetry had left. It cannot: an OTLP export is a
    **timer**, not a step in a request. `BatchSpanProcessor` flushes every few seconds carrying
    whatever accumulated, on the SDK's own thread — so the line has no `trace_id`, and beside
    `redis/script` and `postgres/connect`, which do, it reads as belonging to nothing at all.

    `items=8 traces=1` says the thing a person actually wants: *one request's worth went out.*
    Counted from spans already in memory, so it costs a set of the batch and no I/O.
    """
    if not args:
        return None
    try:
        return len({span.context.trace_id for span in args[0] if span.context is not None})
    except AttributeError, TypeError:
        # Metrics arrive as a tree with no spans in it, and a log batch carries records rather
        # than spans. Both are "not a question here", which is `None` and never `0`.
        return None


def _batch_size(args: tuple[Any, ...]) -> int | None:
    """How many items this export carried, where that is a countable thing.

    Spans and log records arrive as a sequence; metrics arrive as a `MetricsData` tree that has no
    meaningful single count. `None` rather than `0` for the third: "we did not count" and "there
    was nothing" are different statements, the same distinction `FRD-117`'s prober draws between
    unprobed and healthy.
    """
    if not args:
        return None
    try:
        return len(args[0])
    except TypeError:
        return None


class SdkDiagnostics(logging.Handler):
    """Print what the OpenTelemetry SDK says about itself, instead of posting it to itself.

    **The mechanism behind a whole day of not being able to see why OTLP pushes failed.** The SDK
    reports an export failure — with the endpoint and the underlying error — on a stdlib logger
    under `opentelemetry.*`. That record propagates to the root logger, and with
    `AIRA_OTEL_ENABLED=true` the only handler there is :class:`LoggingHandler`, the OTLP one. So
    the sentence explaining why the exporter could not post was queued for export **through the
    exporter that could not post**, and reached no terminal, no file and no collector.
    `logging.lastResort` is no help: it prints only when a record finds *no* handler, and this one
    found the broken one.

    So the `opentelemetry` tree stops propagating and comes here instead, where it is written to
    stdout like every other line and marked local-only so it cannot re-enter the pipeline it is
    about.

    Its level is left alone deliberately — inherited, so `WARNING` and above, which is what the SDK
    uses for the things worth reading. Lowering it to see successful exports would buy every
    instrumentation library's `DEBUG` chatter as well, and successful exports are already reported,
    with a duration, by :class:`WatchedExport`.
    """

    def emit(self, record: logging.LogRecord) -> None:
        try:
            # Imported here, not at module scope: `aira_common.logging` imports this module for
            # the access-log redaction, and a second edge the other way is an import cycle.
            from aira_common.logging import get_logger

            log = get_logger("opentelemetry.sdk")
            say = log.warning if record.levelno >= logging.WARNING else log.info
            say(
                "otel_sdk",
                level=record.levelname,
                logger=record.name,
                message=redact_url_query(record.getMessage())[:400],
                local_only=True,
            )
        except Exception:  # noqa: BLE001 — a diagnostic must never be the reason something fails
            return


def route_sdk_diagnostics() -> bool:
    """Send the `opentelemetry` logger tree to stdout rather than into OTLP. Idempotent.

    Returns whether it installed the handler, so a caller can say so and a test can assert it
    without reading the logging module's private state.
    """
    logger = logging.getLogger("opentelemetry")
    if any(isinstance(handler, SdkDiagnostics) for handler in logger.handlers):
        return False
    logger.addHandler(SdkDiagnostics())
    # Nothing under `opentelemetry` may reach the root logger, because the root logger is where the
    # OTLP handler lives. This is the whole fix; the handler above is what keeps the records
    # readable once they stop going there.
    logger.propagate = False
    return True


def set_span_attributes(attributes: Mapping[str, object]) -> None:
    """Set primitive (non-None) attributes on the current span; no-op if none is recording."""
    span = trace.get_current_span()
    for key, value in attributes.items():
        if isinstance(value, str | int | float | bool):
            span.set_attribute(key, value)


# ---------------------------------------------------------------------------
# Who a **model call** was made for (`FRD-619`)
# ---------------------------------------------------------------------------
#
# The gateway's outgoing calls are traced by `opentelemetry-instrumentation-httpx`, which knows
# what any HTTP client library knows: a method, a URL, a status. Measured on 2026-09-04 against a
# live delivery feed, an upstream model call carried exactly three attributes —
#
#     http.method=POST  http.status_code=200  http.url=http://ollama:11434/v1/chat/completions
#
# — while the request span above it carried twenty-nine, including every fact anybody asks a model
# access about: `aira.subject`, `aira.use_case`, `aira.credential`, `aira.model`. The two are the
# same trace, so a **tracing** backend joins them by `parentSpanId` and shows one picture.
#
# A SIEM is not a tracing backend. It ingests flat events and correlates by field, and the delivery
# channel (`FRD-618`) exists to feed exactly that: `collector-forward.yaml` keeps the child spans
# that have a URL precisely so that *"one record per API call and per model call"* holds. The
# record was there and it did not say who made it, which model it went to, or on whose behalf —
# and the join that answers those is a join a flat consumer cannot perform.
#
# So the identifying half of a request is carried here, in a context variable, and stamped onto the
# client span by the gateway's httpx hook. Two variables rather than one, because they have
# different lifetimes and different truth:
#
#   * `_caller` is set once, when the request is attributed, and holds for everything that follows.
#   * `_model_call` is set only around an actual upstream model call, and is what tells the hook
#     that *this* outgoing request is a model access rather than a JWKS refresh or a Vault read.
#
# The consequence of that split is the property worth having: a call the gateway makes for its own
# reasons keeps the three HTTP attributes it had and gains nothing, so no span outside a model call
# is labelled with a caller who did not cause it.
_caller: ContextVar[tuple[tuple[str, AttributeValue], ...]] = ContextVar(
    "aira_model_call_caller", default=()
)
_model_call: ContextVar[tuple[tuple[str, AttributeValue], ...] | None] = ContextVar(
    "aira_model_call", default=None
)


def _primitives(attributes: Mapping[str, object]) -> tuple[tuple[str, AttributeValue], ...]:
    """The same filter `set_span_attributes` applies: primitives only, no ``None``."""
    return tuple(
        (key, value)
        for key, value in attributes.items()
        if isinstance(value, str | int | float | bool)
    )


def attribute_model_calls_to(attributes: Mapping[str, object]) -> None:
    """Remember who this request is for, so a model call it makes can say so.

    Called from **one** place — `gateway.auth.attribution.set_attribution`, the function that
    already owns *"a fact about who is calling reaches the span"* and is already guarded by
    `test_every_attribution_reaches_the_span.py`. That is deliberate: the same fact recorded at a
    second site is the fact that differs at one of them (`LESSONS.md` §1).

    Not reset afterwards. The context variable is per-task and a request is a task, so the value
    dies with the request that set it; a `reset` would need a token threaded through the middleware
    to buy nothing.
    """
    _caller.set(_primitives(attributes))


@contextmanager
def model_call(attributes: Mapping[str, object]) -> Iterator[None]:
    """Mark the block that talks to a model, and say which model.

    Everything inside gets the caller's identity plus ``attributes`` stamped onto its client span.
    Entered per **attempt**, not per request: a fallback chain that tries three models must produce
    three records naming three models, and the model that answered is not the model that timed out.

    Nested calls replace rather than merge — a classifier call made *inside* a serving request is
    its own model access with its own model, and reporting the outer model on it would be a lie in
    the one field a reader trusts.
    """
    token = _model_call.set(_primitives(attributes))
    try:
        yield
    finally:
        _model_call.reset(token)


def model_call_attributes() -> tuple[tuple[str, AttributeValue], ...] | None:
    """What to stamp on the current outgoing span, or ``None`` if it is not a model call."""
    call = _model_call.get()
    if call is None:
        return None
    return (*_caller.get(), *call)


# Query parameters that may carry a credential (the Gemini wire protocol passes API keys as
# ``?key=``). They must never reach a span attribute, a log line, or an exported trace.
REDACTED = "REDACTED"
SENSITIVE_QUERY_PARAMS = frozenset(
    {"key", "api_key", "apikey", "access_token", "token", "password"}
)


def redact_query_string(query: str) -> str:
    """Return ``query`` with the values of credential-bearing parameters replaced.

    Order and unknown parameters are preserved so the redacted string stays useful for
    debugging (e.g. ``alt=sse&key=REDACTED``).
    """
    if not query:
        return query
    parts: list[str] = []
    for pair in query.split("&"):
        name, separator, _value = pair.partition("=")
        if separator and name.lower() in SENSITIVE_QUERY_PARAMS:
            parts.append(f"{name}={REDACTED}")
        else:
            parts.append(pair)
    return "&".join(parts)


class AccessLogRedaction(logging.Filter):
    """Keep credentials out of the *access* log, not only out of exported spans.

    `redact_span_query` has kept `?key=<api key>` out of OpenTelemetry since `ADR-0007`, and the
    web server's own access log — the one that goes to stdout, is collected by whatever ships
    container logs, and is readable by everyone who can read logs — recorded the request line
    verbatim. A Gemini client authenticating the documented way therefore wrote its API key into
    the log of every request it made. A trace backend is usually the *better*-guarded of the two.

    The filter rewrites the record's arguments rather than the formatted message, because uvicorn
    formats the line itself and the message does not exist yet when a filter runs. It redacts any
    string argument that carries a query string, rather than the one positional index uvicorn
    happens to use today — a filter pinned to `args[2]` is one silently disabled by an upgrade.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args
        if isinstance(args, tuple):
            record.args = tuple(_redact_arg(arg) for arg in args)
        elif isinstance(args, dict):
            record.args = {key: _redact_arg(value) for key, value in args.items()}
        return True


def redact_url_query(value: str) -> str:
    """Return ``value`` — a URL or a request line — with credential-bearing parameters replaced.

    The one definition, read by three places that each have a URL and must not export it as
    written: the access log (above), the **inbound** server span (`gateway/app.redact_span_query`)
    and the **outbound** client span (`gateway/telemetry.redact_client_span_url`, `FRD-117` FR-5).
    The third arrived on 2026-08-31 with httpx instrumentation, and it is the one where the
    credential is *ours*: `upstreams/gemini.py` authenticates with ``?key=<api key>`` on every
    call. A fourth reader spelling this out again is how one of them comes to redact a shorter
    list than the others.
    """
    if "?" not in value:
        return value
    path, _, query = value.partition("?")
    return f"{path}?{redact_query_string(query)}"


def redact_url_credentials(value: str) -> str:
    """Replace a ``user:password@`` in a URL's authority with the user alone.

    The **other** place a credential hides in a URL, and the one `redact_url_query` cannot see. A
    Gemini key rides in the query string; a Redis, AMQP or Postgres URL carries its password in the
    authority — `redis://aira:s3cret@redis:6379/0` is the ordinary spelling, and it is the value of
    `AIRA_REDIS_URL` in any deployment that authenticates.

    Written against the `scheme://` marker rather than with `urlsplit`, because two of the three
    readers of this family are handed a **request line** (`GET /v1/x?y HTTP/1.1`) rather than a
    URL, and a parser confident enough to find an authority in that is one that will eventually
    mangle a path. No marker, no authority, nothing to do.
    """
    marker = "://"
    if marker not in value:
        return value
    scheme, _, rest = value.partition(marker)
    authority, slash, path = rest.partition("/")
    if "@" not in authority:
        return value
    userinfo, _, host = authority.rpartition("@")
    # The user is kept: "which account did we connect as" is exactly the question an integration
    # is debugging, and it is not the secret half.
    user = userinfo.partition(":")[0]
    return f"{scheme}{marker}{user}:{REDACTED}@{host}{slash}{path}"


def redact_target(value: str) -> str:
    """Both halves, for an address that is about to be written to a log line.

    `aira_common.integration_debug` reports *where* a call went, and the addresses it is handed
    come from six different clients — an OTLP endpoint, a broker list, a JWKS URI, a Vault path, a
    Redis URL. Between them they use both hiding places, so the one function that takes a target
    applies both rules rather than leaving each call site to know which kind it holds.
    """
    return redact_url_credentials(redact_url_query(value))


def _redact_arg(value: object) -> object:
    return redact_url_query(value) if isinstance(value, str) else value


#: The loggers that emit a request line. Named rather than filtered at the root, because a filter
#: on the root logger does not run for records emitted through a child logger with its own handler
#: — which is exactly how uvicorn is configured.
ACCESS_LOGGERS = ("uvicorn.access", "gunicorn.access", "django.server")


def install_access_log_redaction() -> None:
    """Attach :class:`AccessLogRedaction` to the access loggers, once."""
    for name in ACCESS_LOGGERS:
        logger = logging.getLogger(name)
        if not any(isinstance(existing, AccessLogRedaction) for existing in logger.filters):
            logger.addFilter(AccessLogRedaction())


def trace_context_fields() -> dict[str, str]:
    """Return ``trace_id``/``span_id`` (hex) for the current span, if one is active."""
    ctx = trace.get_current_span().get_span_context()
    if not ctx.is_valid:
        return {}
    return {
        "trace_id": trace.format_trace_id(ctx.trace_id),
        "span_id": trace.format_span_id(ctx.span_id),
    }


def kafka_headers_from_context() -> KafkaHeaders:
    """Inject the current trace context into Kafka-style headers (``list[(str, bytes)]``)."""
    carrier: dict[str, str] = {}
    inject(carrier)
    return [(key, value.encode()) for key, value in carrier.items()]


def traceparent_from_context() -> str:
    """The W3C ``traceparent`` for the current span, or ``""`` when none is active.

    **For a context that has to be stored rather than sent.** A transactional outbox breaks the
    causal chain on purpose: the console's request writes a row and returns, and a *separate
    process* publishes it seconds later. `kafka_headers_from_context` reads the ambient span, and
    in that publisher there is none — so it injected nothing, on every event, in every deployment
    (`FRD-615`). Captured where the span exists, carried in the row, restored at publish.
    """
    carrier: dict[str, str] = {}
    inject(carrier)
    return carrier.get("traceparent", "")


def kafka_headers_for(traceparent: str = "") -> KafkaHeaders:
    """Trace headers for a message: the **stored** context, or the current span's.

    One function rather than a branch at the call site, because the two cases are one question —
    *which trace does this message belong to* — and a producer that had to choose would be a
    producer where one of the two answers is eventually forgotten.
    """
    if traceparent:
        return [("traceparent", traceparent.encode())]
    return kafka_headers_from_context()


def context_from_kafka_headers(headers: KafkaHeaders | None) -> Context:
    """Extract an OTel context from Kafka-style headers for the consumer side."""
    carrier = {
        key: (value.decode() if isinstance(value, bytes) else value)
        for key, value in (headers or [])
    }
    return extract(carrier)


@dataclass(slots=True)
class Processing:
    """The handle a consumer marks its outcome on. Yielded by :func:`consuming`."""

    _span: Any

    def failed(self, exc: BaseException) -> None:
        """Record that this message could not be applied.

        The consumer catches every exception on purpose — one bad event must not take the whole
        consumer down (`worker.apply_one_message`) — so nothing propagates out of the `with` block
        for the span to notice. Without this the failure is a log line and the trace is green,
        which is the state that teaches somebody to trust the trace view least.
        """
        self._span.record_exception(exc)
        self._span.set_status(Status(StatusCode.ERROR, type(exc).__name__))


@contextmanager
def consuming(
    destination: str, headers: KafkaHeaders | None, attributes: Mapping[str, object] | None = None
) -> Iterator[Processing]:
    """Continue the producer's trace while one message is processed.

    **The other end of a wire that had only one.** `kafka_headers_from_context` has put a
    `traceparent` on every message since `FRD-001`, and `context_from_kafka_headers` — the function
    that takes it off again — was read by nothing but its own test. So a configuration change made
    in the console produced a span in Management, the message carried the context correctly, and
    the gateway's application of it appeared as nothing at all: the one thing tracing across two
    planes is *for* did not hold. Two correct halves and no wire, the shape `LESSONS.md` §1 lists.

    Named for the messaging conventions rather than for us: `"<destination> process"` is what a
    trace backend groups on, so these spans sit beside every other queue consumer somebody
    operates rather than in a shape only this project uses.

    A no-op when observability is off — `get_tracer` then returns the API's non-recording
    implementation, so this costs a function call and changes nothing.
    """
    tracer = trace.get_tracer("aira_common.messaging")
    with tracer.start_as_current_span(
        f"{destination} process",
        context=context_from_kafka_headers(headers),
        kind=trace.SpanKind.CONSUMER,
    ) as span:
        span.set_attribute("messaging.system", "kafka")
        span.set_attribute("messaging.destination.name", destination)
        span.set_attribute("messaging.operation", "process")
        for key, value in (attributes or {}).items():
            if isinstance(value, str | int | float | bool):
                span.set_attribute(key, value)
        yield Processing(span)


def build_resource_attributes(service_name: str, environment: str) -> Attributes:
    """Helper exposing the standard resource attributes (used in tests/tools)."""
    return {"service.name": service_name, "deployment.environment": environment}
