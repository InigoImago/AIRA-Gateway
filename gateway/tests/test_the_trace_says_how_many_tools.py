"""How many functions a request offered, and how many the model asked for (`FRD-615` §9).

Both numbers have been in the audit row since `FRD-131` and neither was on the span, so the
question an agent deployment is actually judged by — **this thing is handed forty tools and uses
two** — could be asked of one request in a database and not of the traffic in a trace view.

The case that matters most is the one that is easiest to leave out: a request that **offered**
functions and was then **refused**. `offered` is set before anything can refuse precisely so that
*offered ten, asked for none* stays distinguishable from *offered none*, and a refusal is exactly
when that difference is worth having.
"""

from __future__ import annotations

from typing import Any

import pytest

from aira_gateway.persistence.recorder import _tool_attributes


@pytest.mark.parametrize(
    ("summary", "expected"),
    [
        (None, {}),
        ({}, {}),
        (
            {"declared": 0, "called": []},
            {"aira.tools.offered": 0, "aira.tools.called": 0},
        ),
        (
            {"declared": 40, "called": []},
            {"aira.tools.offered": 40, "aira.tools.called": 0},
        ),
        (
            {"declared": 40, "called": ["read_file", "run_tests"]},
            {
                "aira.tools.offered": 40,
                "aira.tools.called": 2,
                "aira.tools.names": "read_file, run_tests",
            },
        ),
        (
            {"declared": 1, "called": ["read_file", "read_file"]},
            {
                "aira.tools.offered": 1,
                "aira.tools.called": 2,
                "aira.tools.names": "read_file, read_file",
            },
        ),
    ],
    ids=[
        "no tools at all",
        "an empty summary",
        "offered none, called none",
        "offered forty, called none",
        "offered forty, called two",
        "called twice in one turn",
    ],
)
def test_the_figures_a_span_carries(
    summary: dict[str, Any] | None, expected: dict[str, Any]
) -> None:
    assert _tool_attributes(summary) == expected


def test_a_request_with_no_tools_carries_no_tool_attributes() -> None:
    """Absent rather than zero, and it is a decision rather than a saving.

    `aira.tools.offered = 0` on every ordinary chat request would put attributes on the
    overwhelming majority of spans to say nothing — and it would turn *"which traffic uses tools"*
    from an existence check into a comparison, which is the slower and the easier one to get wrong.
    """
    assert _tool_attributes(None) == {}


def test_the_names_are_names_and_never_arguments() -> None:
    """The line the audit column has drawn since it was written, held one surface out.

    A function name is declared by the client application; an argument is the caller's content and
    belongs under `store_payloads`, inside the retention clock and behind `FRD-406`. A span
    attribute has **no** retention clock at all, so the line matters more here, not less.
    """
    attributes = _tool_attributes(
        {"declared": 2, "called": ["send_email"], "arguments": {"to": "ada@example.org"}}
    )
    assert attributes == {
        "aira.tools.offered": 2,
        "aira.tools.called": 1,
        "aira.tools.names": "send_email",
    }
    assert "ada@example.org" not in str(attributes)


def test_a_refused_request_still_says_what_it_offered() -> None:
    """`offered` is set in `prepare_for_dispatch` before any control can refuse, so a request
    stopped by a budget or an unreleased model still records that it wanted tools. *"Somebody keeps
    trying to use tools here"* is a question about refused traffic more often than served."""
    assert _tool_attributes({"declared": 12, "called": []}) == {
        "aira.tools.offered": 12,
        "aira.tools.called": 0,
    }


# ═══ the wire ═══════════════════════════════════════════════════════════════════════════════════


async def test_the_figures_reach_the_span_through_the_recorder() -> None:
    """Driven through `record_request`, because everything above tests the helper.

    A helper with its own tests and a call site that stopped using it is the shape this repository
    keeps paying for — and did twice this week, in the two changes either side of this one. So this
    one starts at the function a route calls and asks the *span* what it carries.
    """
    from opentelemetry import trace
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    from aira_gateway.auth.attribution import Attribution
    from aira_gateway.persistence.recorder import record_request
    from gateway.tests.test_persistence_recorder import _request

    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))

    class _Writer:
        async def submit(self, entry: object) -> None:
            return None

    request = _request(client=("10.0.0.1", 1234))
    request.state.attribution = Attribution(
        subject="ada", method="api_key", username="ada", use_case="uc", credential="ab12"
    )
    request.app.state.log_writer = _Writer()

    previous = trace._TRACER_PROVIDER
    trace._TRACER_PROVIDER = provider
    try:
        with provider.get_tracer("test").start_as_current_span("POST :generateContent"):
            await record_request(
                request,
                operation="generateContent",
                model="mock-1",
                status=200,
                usage=None,
                latency_ms=1,
                request_payload=None,
                response_payload=None,
                api="gemini",
                tool_calls={"declared": 40, "called": ["read_file", "run_tests"]},
            )
    finally:
        trace._TRACER_PROVIDER = previous

    span = exporter.get_finished_spans()[0]
    assert span.attributes["aira.tools.offered"] == 40
    assert span.attributes["aira.tools.called"] == 2
    assert span.attributes["aira.tools.names"] == "read_file, run_tests"


async def test_an_ordinary_request_leaves_the_span_alone() -> None:
    """The other half of the same wire: no tools means no attributes, asserted where it is
    produced rather than on the helper that decides it."""
    from opentelemetry import trace
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    from aira_gateway.auth.attribution import Attribution
    from aira_gateway.persistence.recorder import record_request
    from gateway.tests.test_persistence_recorder import _request

    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))

    class _Writer:
        async def submit(self, entry: object) -> None:
            return None

    request = _request(client=("10.0.0.1", 1234))
    request.state.attribution = Attribution(
        subject="ada", method="api_key", username="ada", use_case="uc", credential="ab12"
    )
    request.app.state.log_writer = _Writer()

    previous = trace._TRACER_PROVIDER
    trace._TRACER_PROVIDER = provider
    try:
        with provider.get_tracer("test").start_as_current_span("POST :generateContent"):
            await record_request(
                request,
                operation="generateContent",
                model="mock-1",
                status=200,
                usage=None,
                latency_ms=1,
                request_payload=None,
                response_payload=None,
                api="gemini",
            )
    finally:
        trace._TRACER_PROVIDER = previous

    span = exporter.get_finished_spans()[0]
    assert not [key for key in span.attributes if key.startswith("aira.tools")]
