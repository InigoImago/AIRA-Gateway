"""Whether a request leaves a span is this installation's decision, not the caller's (`FRD-618`).

The sampler was `ParentBased(TraceIdRatioBased(ratio))`, and the SDK's `ParentBased` follows a
**remote** parent's sampled flag. A request's remote parent is whatever `traceparent` its caller
sent, so a caller who wrote `…-00` left no exported span. For a request refused before attribution
that span is the only record there is (`FRD-616`), and the delivery channel forwards exactly one
record per span to a SIEM. The caller could therefore take its own refused attempts out of the
security feed by setting one flag.

A remote parent is now sampled like a root, by trace id and ratio. A local parent is still followed,
so a span's children stay with it.
"""

from __future__ import annotations

from opentelemetry.context import Context
from opentelemetry.sdk.trace.sampling import Decision, Sampler
from opentelemetry.trace import NonRecordingSpan, SpanContext, TraceFlags, set_span_in_context

from aira_common.observability import request_sampler

TRACE_ID = 0x0AF7651916CD43DD8448EB211C80319C
SPAN_ID = 0x00F067AA0BA902B7


def _parent(*, sampled: bool, remote: bool, trace_id: int = TRACE_ID) -> Context:
    flags = TraceFlags(TraceFlags.SAMPLED if sampled else TraceFlags.DEFAULT)
    context = SpanContext(trace_id, SPAN_ID, is_remote=remote, trace_flags=flags)
    return set_span_in_context(NonRecordingSpan(context))


def _decision(sampler: Sampler, parent: Context | None, trace_id: int = TRACE_ID) -> Decision:
    return sampler.should_sample(parent, trace_id, "POST /v1beta/models/x:generateContent").decision


def test_a_caller_that_asks_for_no_sampling_is_traced_anyway() -> None:
    """The defect: `traceparent: 00-…-00` took the request out of every trace backend and SIEM."""
    decision = _decision(request_sampler(1.0), _parent(sampled=False, remote=True))

    assert decision is Decision.RECORD_AND_SAMPLE, (
        "a caller's not-sampled flag decided that its request leaves no span — and with it no "
        "record of a refused attempt in the delivery channel"
    )


def test_a_caller_cannot_force_a_span_past_the_ratio_either() -> None:
    """The same rule from the other side: the flag decides nothing, in either direction."""
    assert _decision(request_sampler(0.0), _parent(sampled=True, remote=True)) is Decision.DROP


def test_a_remote_parent_is_decided_as_a_root_would_be() -> None:
    """Every process of an installation is given the same ratio, and `TraceIdRatioBased` decides
    by trace id. A trace continued from a caller, or across the bus (`FRD-615`), is therefore kept
    or dropped whole, whatever flag arrived with it."""
    sampler = request_sampler(0.5)
    for trace_id in (0x1, 0x7FFFFFFFFFFFFFFF, 0x8000000000000000, TRACE_ID):
        root = _decision(sampler, None, trace_id)
        for sampled in (True, False):
            parent = _parent(sampled=sampled, remote=True, trace_id=trace_id)
            assert _decision(sampler, parent, trace_id) is root


def test_a_local_parent_is_still_followed() -> None:
    """Inside one process the parent's decision stands, or a kept request would lose its children
    — the model call, the database reads — and a dropped one would leave orphans."""
    sampler = request_sampler(1.0)

    assert _decision(sampler, _parent(sampled=True, remote=False)) is Decision.RECORD_AND_SAMPLE
    assert _decision(sampler, _parent(sampled=False, remote=False)) is Decision.DROP
