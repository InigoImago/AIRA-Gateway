"""What `AIRA_DEBUG_OTEL_PAYLOAD` prints is what a receiver would take (`FRD-617` §3.10).

**The test that would have caught the defect a reader found instead.** `payload_as_json` was
documented as rendering "through the exporter's own encoder, so it cannot drift from what is sent",
and the unit tests asked whether the document parsed and named the right span — questions about the
renderer. Both passed while the identifiers came out **base64** (`TETTPxm0Rt5w6G3guyzfIA==`) where
OTLP specifies hex, and enums came out as names where a collector sends numbers. Protobuf's generic
JSON mapping is not OTLP's, and no amount of asking the renderer about itself can see that.

`LESSONS.md`: a rendering meant to show what the far end receives is only tested by comparing with
the far end. So this one hands the output to a **real collector** over OTLP/JSON and asks whether
it took it — the question the feature exists to answer, and one that needs a collector, which is
why it lives in this layer rather than beside the unit tests.

A `partial_success` with a non-zero rejected count is the failure this catches: the collector
answers `200` either way, and only the body says whether it kept anything.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request

import pytest
import stack_addresses
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from aira_common.observability import payload_as_json

pytestmark = pytest.mark.integration

COLLECTOR = stack_addresses.url("otlp_http")


def _rendered_payload() -> str:
    """A batch of real spans, rendered the way the debug switch renders them.

    Deliberately carries a parent/child pair, a non-ASCII attribute and an `aira.*` attribute:
    `parentSpanId` is a fourth identifier field on its own key, and text is where an encoding
    problem shows up second.
    """
    memory = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(memory))
    tracer = provider.get_tracer("integration")
    with tracer.start_as_current_span("generateContent") as span:
        span.set_attribute("aira.use_case", "kundenservice")
        span.set_attribute("aira.status", 200)
        span.set_attribute("text", "Grüße — „Anführung“ ✓")
        tracer.start_span("upstream").end()

    return payload_as_json("traces", (memory.get_finished_spans(),), 10)


def _post(payload: str) -> tuple[int, dict]:
    request = urllib.request.Request(  # noqa: S310 - fixed scheme, local stack
        f"{COLLECTOR}/v1/traces",
        data=payload.encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=15) as response:  # noqa: S310
        body = response.read().decode("utf-8") or "{}"
    return response.status, json.loads(body)


def test_the_collector_accepts_the_rendered_payload_whole() -> None:
    status, body = _post(_rendered_payload())

    assert status == 200, status
    rejected = int(body.get("partialSuccess", {}).get("rejectedSpans", 0) or 0)
    assert rejected == 0, (
        f"the collector rejected {rejected} spans of what the debug switch prints: "
        f"{body.get('partialSuccess')}. What is printed is meant to be what a receiver is handed."
    )


def test_a_payload_with_protobufs_own_encoding_is_refused() -> None:
    """The guard on the guard: without it, the test above would pass on anything shaped like OTLP.

    Base64 identifiers are exactly what the defect produced, so this asserts the collector really
    does distinguish them rather than accepting whatever it is sent.
    """
    document = json.loads(_rendered_payload())
    spans = document["resourceSpans"][0]["scopeSpans"][0]["spans"]
    for span in spans:
        span["traceId"] = "TETTPxm0Rt5w6G3guyzfIA=="  # the base64 form, as protobuf renders it

    with pytest.raises(urllib.error.HTTPError) as refused:
        _post(json.dumps(document))
    assert refused.value.code == 400, refused.value.code


def test_the_identifiers_in_what_is_printed_are_hex() -> None:
    """Stated here as well as in the unit tests, because this file is where somebody looks when a
    receiver complains about an id it cannot resolve."""
    span = json.loads(_rendered_payload())["resourceSpans"][0]["scopeSpans"][0]["spans"][0]

    assert len(span["traceId"]) == 32 and int(span["traceId"], 16) >= 0
    assert len(span["spanId"]) == 16 and int(span["spanId"], 16) >= 0
