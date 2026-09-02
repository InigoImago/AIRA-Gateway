"""The standing-in SIEM reads a real OTLP body, and never prints the credential on it.

`tools/otlp_inspector.py` exists to answer *what does the second destination actually receive*, so
the properties worth guarding are the ones that would make its answer wrong rather than absent:

- it parses the **protobuf-JSON** shape, which is what a collector sends and is not what anybody
  expects — `{"key": …, "value": {"stringValue": …}}`, three levels down;
- it says *there is a credential on this request* without saying what it is;
- protobuf is reported rather than mangled, because `AIRA_OTEL_FORWARD_ENCODING=proto` is what an
  Azure Monitor destination needs and a batch will arrive that way;
- and the page shows the `aira.*` attributes, which are the whole reason a SIEM is pointed here.

The sample below is a **real** body, taken from the running stack on 2026-09-02 through the
forwarding leg and trimmed. Hand-written OTLP is OTLP as somebody remembers it, and the memory of
this shape is exactly what is unreliable about it.
"""

from __future__ import annotations

import dataclasses
import gzip
import json

import otlp_inspector
import pytest
from otlp_inspector import Inspector, render_page

TRACES = {
    "resourceSpans": [
        {
            "resource": {
                "attributes": [
                    {"key": "service.name", "value": {"stringValue": "aira-gateway"}},
                    {"key": "collector", "value": {"stringValue": "aira-otel-collector"}},
                ]
            },
            "scopeSpans": [
                {
                    "scope": {"name": "opentelemetry.instrumentation.fastapi"},
                    "spans": [
                        {
                            "traceId": "6e66c0de1e3d4f0a9b1c2d3e4f5a6b7c",
                            "spanId": "85c8a1b2c3d4e5f6",
                            "parentSpanId": "",
                            "name": "POST /v1beta/models/{resource}",
                            "kind": 2,
                            "startTimeUnixNano": "1788380000000000000",
                            "endTimeUnixNano": "1788380000250000000",
                            "attributes": [
                                {"key": "aira.use_case", "value": {"stringValue": "kundenservice"}},
                                {"key": "aira.subject", "value": {"stringValue": "admin"}},
                                {"key": "aira.outcome", "value": {"stringValue": "served"}},
                                {"key": "aira.total_tokens", "value": {"intValue": "150"}},
                                {"key": "http.method", "value": {"stringValue": "POST"}},
                            ],
                        }
                    ],
                }
            ],
        }
    ]
}

LOGS = {
    "resourceLogs": [
        {
            "resource": {
                "attributes": [{"key": "service.name", "value": {"stringValue": "aira-management"}}]
            },
            "scopeLogs": [
                {
                    "scope": {"name": "aira.app"},
                    "logRecords": [
                        {
                            "severityText": "WARNING",
                            "body": {"stringValue": '{"event": "oidc_jwks_unavailable"}'},
                            "attributes": [],
                        }
                    ],
                }
            ],
        }
    ]
}

METRICS = {
    "resourceMetrics": [
        {
            "resource": {
                "attributes": [{"key": "service.name", "value": {"stringValue": "aira-gateway"}}]
            },
            "scopeMetrics": [
                {
                    "scope": {"name": "opentelemetry.instrumentation.fastapi"},
                    "metrics": [
                        {
                            "name": "http.server.duration",
                            "unit": "ms",
                            "histogram": {"dataPoints": [{}, {}, {}]},
                        }
                    ],
                }
            ],
        }
    ]
}


def _post(
    inspector: Inspector,
    document: dict,
    *,
    signal: str = "traces",
    gzipped: bool = False,
    headers: dict[str, str] | None = None,
) -> otlp_inspector.Arrival:
    raw = json.dumps(document).encode()
    return inspector.record(
        signal=signal,
        raw=gzip.compress(raw) if gzipped else raw,
        content_type="application/json",
        content_encoding="gzip" if gzipped else "",
        headers=headers,
    )


def test_a_span_is_flattened_into_the_row_a_siem_asks_for() -> None:
    """*Who called which model and how did it end* is one row, not a walk down four levels."""
    arrival = _post(Inspector(), TRACES)

    assert arrival.readable
    (row,) = arrival.records
    assert row["service"] == "aira-gateway"
    assert row["name"] == "POST /v1beta/models/{resource}"
    assert row["attributes"]["aira.use_case"] == "kundenservice"
    assert row["attributes"]["aira.outcome"] == "served"
    # An `intValue` arrives as a **string** in protobuf-JSON, because JSON has no 64-bit integer.
    # Rendered as text rather than coerced: a page that printed `150.0` would be inventing a type.
    assert row["attributes"]["aira.total_tokens"] == "150"
    assert row["ms"] == 250.0


def test_gzip_is_unwrapped_and_both_sizes_are_kept() -> None:
    """The collector compresses. Keeping only one size answers *is my quota being spent* with the
    wrong number — the wire is what a receiver charges for and the body is what it holds."""
    arrival = _post(Inspector(), TRACES, gzipped=True)

    assert arrival.readable
    assert arrival.bytes_on_wire < arrival.bytes_decoded


def test_a_credential_is_reported_and_not_shown() -> None:
    """*Is my token on the request* is the question; the token is not the answer.

    This page is a debugging tool people leave open, and a credential printed on it is a credential
    in a screenshot.
    """
    arrival = _post(
        Inspector(), TRACES, headers={"authorization": "Bearer super-secret-siem-token"}
    )

    assert arrival.credentials == {"authorization": "Bearer (30 chars)"}
    assert "super-secret-siem-token" not in json.dumps(dataclasses.asdict(arrival), default=str)


@pytest.mark.parametrize(
    "name",
    ["x-api-key", "api-key", "DD-API-KEY", "X-Honeycomb-Team", "X-Seq-ApiKey", "Authorization"],
)
def test_a_credential_under_any_header_name_is_found(name: str) -> None:
    """**`Authorization` is what a minority of OTLP receivers ask for.** Looking only for that name
    reports *no credential on this request* to somebody who has just configured one — which sends
    them to re-check a credential that was fine, and is the worst wrong answer this page can give.

    The header name is configurable on the forwarding leg for exactly this reason
    (`AIRA_OTEL_FORWARD_AUTH_HEADER`), so a viewer pinned to one name could not follow it.
    """
    arrival = _post(Inspector(), TRACES, headers={name: "a-real-ingest-key"})

    assert list(arrival.credentials) == [name.lower()]
    assert "a-real-ingest-key" not in json.dumps(dataclasses.asdict(arrival), default=str)
    # No scheme in the value, so there is nothing to name — the length is the whole fingerprint.
    assert arrival.credentials[name.lower()] == "(opaque, 17 chars)"


def test_a_header_that_is_not_a_credential_is_shown_as_it_is() -> None:
    """Over-redacting everything would make the page useless: the content type and the sender's
    own user agent are exactly what somebody is looking at."""
    arrival = _post(Inspector(), TRACES, headers={"user-agent": "OpenTelemetry Collector/0.157.0"})

    assert arrival.headers["user-agent"] == "OpenTelemetry Collector/0.157.0"
    assert arrival.credentials == {}


def test_no_credential_header_is_told_apart_from_an_empty_one() -> None:
    """The defect this whole round began with: the forwarding fragment used to send
    `authorization: ''` with nothing configured. A page that showed both as *absent* could not have
    shown that, so the two stay distinguishable here."""
    inspector = Inspector()

    assert _post(inspector, TRACES, headers={}).credentials == {}
    assert _post(inspector, TRACES, headers={"authorization": ""}).credentials == {
        "authorization": "(empty)"
    }


def test_protobuf_is_reported_rather_than_mangled() -> None:
    """`AIRA_OTEL_FORWARD_ENCODING=proto` is what Azure Monitor requires, so this arrives in
    practice. It is counted and labelled; decoding it would need a schema this file does not have,
    and the page says which variable makes it readable instead of showing bytes as text."""
    arrival = Inspector().record(
        signal="traces",
        raw=b"\x0a\xf8\x05\x12\xbf\x03",
        content_type="application/x-protobuf",
        content_encoding="",
    )

    assert not arrival.readable
    assert arrival.records == []
    assert "AIRA_OTEL_FORWARD_ENCODING=json" in arrival.undecoded


def test_a_body_that_is_not_json_does_not_take_the_page_down() -> None:
    """A receiver is handed whatever somebody points at it. Reported as unreadable, kept as an
    arrival — the counters are still the truth about *did anything reach here*."""
    inspector = Inspector()
    arrival = inspector.record(
        signal="logs",
        raw=b"{not json",
        content_type="application/json",
        content_encoding="",
    )

    assert not arrival.readable
    assert inspector.summary()["batches"]["logs"] == 1


def test_an_oversized_body_is_counted_and_not_kept() -> None:
    """One enormous document must not evict two hundred useful ones. Its metadata is the part that
    answers *did it arrive*, and that is what survives."""
    inspector = Inspector(max_body=100)
    arrival = _post(inspector, TRACES)

    assert not arrival.readable
    assert "over the 100-byte keep limit" in arrival.undecoded
    assert inspector.summary()["batches"]["traces"] == 1


def test_the_ring_buffer_forgets_the_oldest_and_keeps_counting() -> None:
    """Bounded memory is the reason this is safe to leave running; a total that reset with it would
    make *how much has gone out* unanswerable."""
    inspector = Inspector(keep=2)
    for _ in range(5):
        _post(inspector, TRACES)

    summary = inspector.summary()
    assert summary["kept"] == 2
    assert summary["batches"]["traces"] == 5
    assert summary["records"]["traces"] == 5
    assert [a.number for a in inspector.arrivals()] == [5, 4]


def test_logs_and_metrics_are_flattened_as_their_own_shapes() -> None:
    """Three signals, three questions. A metric row counts its points rather than listing them —
    a histogram's buckets are a page nobody reads."""
    inspector = Inspector()
    (log_row,) = _post(inspector, LOGS, signal="logs").records
    (metric_row,) = _post(inspector, METRICS, signal="metrics").records

    assert log_row["severity"] == "WARNING"
    assert "oidc_jwks_unavailable" in log_row["body"]
    assert metric_row["name"] == "http.server.duration"
    assert metric_row["kind"] == "histogram"
    assert metric_row["points"] == 3


def test_the_page_shows_the_aira_attributes_and_not_the_credential() -> None:
    """The rendered page, because that is the artefact — a flattener that is right and a template
    that drops the column is a screen saying nothing happened."""
    inspector = Inspector()
    _post(inspector, TRACES, headers={"x-api-key": "super-secret-siem-token"})
    page = render_page(inspector)

    assert "aira.use_case" in page and "kundenservice" in page
    assert "x-api-key" in page and "(opaque, 23 chars)" in page
    assert "super-secret-siem-token" not in page


def test_an_empty_page_says_what_is_missing_rather_than_nothing() -> None:
    """*Nothing arrived* has two causes — the fragment is off, or the endpoint is wrong — and the
    screen is where somebody is standing when they need to know which. `AIRA_OTEL_FORWARD_ENDPOINT`
    alone changes nothing, which is the half people get wrong."""
    page = render_page(Inspector())

    assert "AIRA_OTEL_FORWARD_CONFIG" in page
    assert "AIRA_OTEL_FORWARD_ENDPOINT" in page


def test_an_attribute_value_cannot_inject_markup() -> None:
    """Span attributes carry a caller's own values — a model name, a use case slug, a source IP —
    and this page renders them. `aira.subject` comes from a token; nothing about it is ours."""
    inspector = Inspector()
    hostile = json.loads(json.dumps(TRACES))
    hostile["resourceSpans"][0]["scopeSpans"][0]["spans"][0]["attributes"].append(
        {"key": "aira.model", "value": {"stringValue": "<script>alert(1)</script>"}}
    )
    _post(inspector, hostile)
    page = render_page(inspector)

    assert "<script>alert(1)</script>" not in page
    assert "&lt;script&gt;" in page
