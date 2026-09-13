"""W3C trace context across Kafka, producer side and consumer side (`FRD-001`, `FRD-615`)."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

from opentelemetry import trace
from opentelemetry.context import Context
from opentelemetry.propagate import extract, inject
from opentelemetry.trace import Status, StatusCode

from aira_common.observability.spans import _primitives

#: Kafka-style message headers.
KafkaHeaders = list[tuple[str, bytes]]


def kafka_headers_from_context() -> KafkaHeaders:
    """Inject the current trace context into Kafka-style headers (``list[(str, bytes)]``)."""
    carrier: dict[str, str] = {}
    inject(carrier)
    return [(key, value.encode()) for key, value in carrier.items()]


def traceparent_from_context() -> str:
    """The W3C ``traceparent`` for the current span, or ``""`` when none is active.

    For a context that is stored rather than sent: an outbox publishes from a separate process with
    no ambient span, so the causing request's context is captured here, carried in the row and
    restored at publish (`FRD-615`).
    """
    carrier: dict[str, str] = {}
    inject(carrier)
    return carrier.get("traceparent", "")


def kafka_headers_for(traceparent: str = "") -> KafkaHeaders:
    """Trace headers for a message: the **stored** context if given, else the current span's.

    One function rather than a branch at each producer, so neither answer can be forgotten.
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

        The consumer catches every exception so one bad event cannot stop it
        (`worker.apply_one_message`), so nothing propagates for the span to notice — without this
        the failure is a log line under a green trace.
        """
        self._span.record_exception(exc)
        self._span.set_status(Status(StatusCode.ERROR, type(exc).__name__))


@contextmanager
def consuming(
    destination: str, headers: KafkaHeaders | None, attributes: Mapping[str, object] | None = None
) -> Iterator[Processing]:
    """Continue the producer's trace while one message is processed.

    Named by the messaging conventions (`"<destination> process"`, kind `CONSUMER`) so a trace
    backend groups these with every other queue consumer. A no-op when observability is off: the
    API's tracer is then non-recording.
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
        for key, value in _primitives(attributes or {}):
            span.set_attribute(key, value)
        yield Processing(span)
