"""A request's trace headers cost what their length costs, and only the ones used are read.

The server instrumentation of both planes extracts the trace context from every request **before
the application sees it**, and so before anything is authenticated. Whatever a propagator spends
on a header is therefore spent for anybody who can reach the port.

- **`tracestate` is bounded before it is parsed.** OpenTelemetry's API before 1.45.1 split it with
  a pattern that backtracks over a run of blanks, unbounded: 32 KB of tabs held one worker's event
  loop for almost two seconds (GHSA-xcq7-p3pf-p88h). The release that fixed it drops a `tracestate`
  over 8192 characters unread, and the trace id beside it still continues.
- **`baggage` is not read at all.** Its parser uses the same pattern inside its own 8 KB bound,
  nothing in AIRA reads baggage, and the HTTP client instrumentation would carry a caller's on to
  every upstream. `propagate_trace_context_only` leaves the W3C trace context and nothing else.

Asserted through `opentelemetry.propagate`, the functions the instrumentations call, and never on a
clock: what the fix changed is *what is parsed*, which a test can see on any machine.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from opentelemetry import baggage, propagate, trace
from opentelemetry.context import Context
from opentelemetry.trace import TraceState

from aira_common.observability import PROPAGATORS_ENV, propagate_trace_context_only

TRACE_ID = 0x0AF7651916CD43DD8448EB211C80319C
TRACEPARENT = "00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01"

#: The longest `tracestate` the propagator reads, as the W3C recommendation sizes a header.
TRACESTATE_BOUND = 8192


@pytest.fixture
def restricted(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """The propagators as `configure_observability` leaves them; the process's own afterwards."""
    before = propagate.get_global_textmap()
    monkeypatch.delenv(PROPAGATORS_ENV, raising=False)
    assert propagate_trace_context_only() is True
    yield
    propagate.set_global_textmap(before)


def _span_context(carrier: dict[str, list[str]]) -> trace.SpanContext:
    return trace.get_current_span(propagate.extract(carrier)).get_span_context()


def test_an_over_long_tracestate_is_dropped_unparsed_and_the_trace_still_continues(
    restricted: None,
) -> None:
    """**The regression.** A member followed by a run of blanks is the shape the old split took
    quadratic time over; one this long was parsed whole, and `a=b` came out of it. Now nothing
    past the bound is parsed — and the request still continues its caller's trace."""
    header = "a=b" + " " * TRACESTATE_BOUND

    context = _span_context({"traceparent": [TRACEPARENT], "tracestate": [header]})

    assert context.trace_id == TRACE_ID, "the caller's trace id was lost with its tracestate"
    assert context.trace_state == TraceState(), (
        "a tracestate longer than the bound was parsed; the parse a caller can make expensive runs "
        "before authentication"
    )


def test_a_tracestate_within_the_bound_is_still_read(restricted: None) -> None:
    """The other half, so the test above cannot pass by reading nothing at all."""
    context = _span_context({"traceparent": [TRACEPARENT], "tracestate": ["a=b,c=d"]})

    assert dict(context.trace_state) == {"a": "b", "c": "d"}


def test_baggage_is_neither_read_nor_carried_on(restricted: None) -> None:
    """Nothing here reads baggage. Read, it is a parse a caller can make expensive before
    authentication; carried on, it is whatever a caller wrote, sent to every upstream model."""
    assert propagate.get_global_textmap().fields == {"traceparent", "tracestate"}

    extracted = propagate.extract({"traceparent": [TRACEPARENT], "baggage": ["user=alice"]})
    assert baggage.get_all(extracted) == {}, "a caller's baggage was read into the context"

    outgoing: dict[str, str] = {}
    propagate.inject(outgoing, context=baggage.set_baggage("user", "alice", Context()))
    assert "baggage" not in outgoing, "baggage in the context would go out with every call"


def test_an_installation_that_named_its_propagators_keeps_them(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`OTEL_PROPAGATORS` is read by OpenTelemetry at import, so a process given one has been
    configured on purpose; a library that overwrote it would be one to work around."""
    before = propagate.get_global_textmap()
    monkeypatch.setenv(PROPAGATORS_ENV, "tracecontext,baggage")
    try:
        assert propagate_trace_context_only() is False
        assert propagate.get_global_textmap() is before
    finally:
        propagate.set_global_textmap(before)
