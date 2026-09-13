"""An OTLP exporter that says what each export attempt did (`FRD-617` §3.1).

The SDK exports on a background thread and reports a success nowhere, and a failure only on a
stdlib logger whose handler, with OTLP logging on, is the exporter that failed. This wrapper emits
one `integration_debug` line per export, with the HTTP status and any partial rejection.
"""

from __future__ import annotations

import contextlib
from typing import Any, cast

from aira_common.observability.payload import show_payload

#: The response body's `partial_success` field, per signal. A collector answers **200 with a body**
#: when it dropped part of a batch, and the Python exporter reads only `resp.ok` — so without this a
#: partly rejected batch reports `SUCCESS`.
_REJECTED_FIELD = {
    "traces": "rejected_spans",
    "metrics": "rejected_data_points",
    "logs": "rejected_log_records",
}


def _partial_success(signal: str, body: bytes) -> tuple[int, str]:
    """``(rejected, reason)`` from an OTLP response body. ``(0, "")`` when it took everything.

    An unparseable body is reported as no rejection: this reads a response the exporter already
    treated as a success, and a diagnostic must never fail the export it is watching.
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


def _distinct_traces(args: tuple[Any, ...]) -> int | None:
    """How many **requests** a batch of spans came from. ``None`` where that is not a question.

    An export is a timer, not a step in a request, so its line carries no `trace_id`;
    `items=8 traces=1` is what tells a reader that one request's worth went out.
    """
    if not args:
        return None
    try:
        return len({span.context.trace_id for span in args[0] if span.context is not None})
    except AttributeError, TypeError:
        # Metrics (a tree) and log records carry no spans: not a question, so `None`, never `0`.
        return None


def _batch_size(args: tuple[Any, ...]) -> int | None:
    """How many items an export carried. ``None`` for metrics, which arrive as a tree.

    ``None`` rather than ``0``: "we did not count" and "there was nothing" are different statements.
    """
    if not args:
        return None
    try:
        return len(args[0])
    except TypeError:
        return None


class WatchedExport:
    """An OTLP exporter wrapper that reports every export attempt.

    Delegation rather than a subclass: the three signal exporters share no base, their `export`
    signatures differ, and `PeriodicExportingMetricReader` reads `_preferred_temporality` and
    `_preferred_aggregation` off whatever it is given — `__getattr__` forwarding covers all of it.
    Always wrapped and gated per call (`watch` is one set lookup when off), so the wiring never
    depends on a setting read at start-up (`LESSONS.md` §1).
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

        `export()` reduces the exchange to an enum, so this replaces `_export` **on the instance**
        (never on the class, which every exporter shares) with a wrapper that records the response
        and returns it unchanged. A private seam, taken deliberately:
        `test_the_exporter_still_has_the_seam_we_reach_through` fails on the upgrade that renames
        it, so this degrades loudly rather than going quiet.
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
        # `_exporter` is a slot and arrives here only before `__init__` has run, where forwarding
        # would recurse until the stack ends.
        if name == "_exporter":
            raise AttributeError(name)
        return getattr(self._exporter, name)

    def export(self, *args: Any, **kwargs: Any) -> Any:
        # Imported here: `integration_debug` imports this package for `redact_target`.
        from aira_common.integration_debug import watch

        self._last = None
        fields: dict[str, Any] = {
            "signal": self._signal,
            "target": self._endpoint,
            "items": _batch_size(args),
        }
        if self._signal == "traces":
            # The number of requests in the batch — what answers "did mine get out".
            fields["traces"] = _distinct_traces(args)
        with watch("otel", "export", **fields) as call:
            # Before the export, so the payload is shown even when the export then fails.
            show_payload(self._signal, args)
            result = self._exporter.export(*args, **kwargs)
            # An exporter answers `FAILURE` as a value after its own retries, and raises nothing.
            name = getattr(result, "name", str(result))
            call.note(result=name)
            # `SUCCESS` means only that the next hop answered 2xx; the status is its own field so
            # "the collector answered" and "the collector took it" stay apart.
            status = getattr(self._last, "status_code", None)
            if status is not None:
                call.note(http_status=status)
            rejected, reason = _partial_success(
                self._signal, getattr(self._last, "content", b"") or b""
            )
            if rejected:
                call.note(rejected=rejected)
                call.failed(f"the collector rejected {rejected} of them: {reason or 'no reason'}")
            elif name != "SUCCESS":
                call.failed(f"exporter returned {name}")
            return result


def watched_export[Exporter](exporter: Exporter, signal: str, endpoint: str) -> Exporter:
    """Wrap ``exporter`` in :class:`WatchedExport`, keeping its type for the SDK's signature.

    The cast is true at run time: `WatchedExport` forwards every attribute it does not define, and
    the three exporter protocols share no base — one cast here rather than one per call site.
    """
    return cast(Exporter, WatchedExport(exporter, signal, endpoint))
