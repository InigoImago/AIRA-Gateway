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
import threading
import urllib.request
from http.server import ThreadingHTTPServer

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


def test_the_body_is_kept_verbatim_and_counted() -> None:
    """*What a receiver was handed*, byte for byte — not a parsed structure and not a summary.

    The page began as three flattening tables; they were a readable shape and the wrong one,
    because a table is this file's opinion about which fields matter. Text rather than a `dict`
    because the text is the record: a parsed object has already lost key order and formatting.
    """
    arrival = _post(Inspector(), TRACES)

    assert arrival.readable
    assert arrival.body == json.dumps(TRACES)
    assert arrival.document() == TRACES
    assert arrival.records == 1


def test_records_are_counted_per_signal_shape() -> None:
    """The one number worth deriving: *is anything going out*. Each signal keeps its records in a
    differently named triple of lists, and a count that only understood traces would report zero
    for the other two — which reads as nothing arriving."""
    assert otlp_inspector.count_records("traces", TRACES) == 1
    assert otlp_inspector.count_records("logs", LOGS) == 1
    assert otlp_inspector.count_records("metrics", METRICS) == 1
    assert otlp_inspector.count_records("traces", {}) == 0


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
    assert arrival.records == 0
    assert arrival.body == ""
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


def test_the_tree_shows_the_document_without_reshaping_it() -> None:
    """Nothing reordered, renamed, filtered or unwrapped.

    A tree that tidied `{"key": "aira.model", "value": {"stringValue": "…"}}` into
    `aira.model: …` would be showing what this file thinks OTLP means rather than what the
    receiver is handed — the exact failing of the tables it replaced.
    """
    html_out = otlp_inspector.json_tree(TRACES)

    for literal in ("resourceSpans", "scopeSpans", "spans", "key", "value", "stringValue"):
        assert literal in html_out, literal
    assert "aira.use_case" in html_out and "kundenservice" in html_out
    # The protobuf-JSON pair is still two nodes, not one collapsed convenience.
    assert html_out.count("aira.use_case") == 1


def test_the_tree_collapses_below_a_depth() -> None:
    """Forty batches of 512 spans, all expanded, is a megabyte of open subtrees nobody scrolls.
    Deep enough to reach a span; closed under that."""
    deep = {"a": {"b": {"c": {"d": {"e": {"f": {"g": "leaf"}}}}}}}
    rendered = otlp_inspector.json_tree(deep)

    assert rendered.count("<details class=node open>") == otlp_inspector.OPEN_DEPTH
    assert "<details class=node>" in rendered


def test_a_scalar_keeps_its_json_spelling() -> None:
    """`null`, `true`, a quoted string — the tree is a view of JSON, so it says what JSON says."""
    rendered = otlp_inspector.json_tree({"a": None, "b": True, "c": "x", "d": 7})

    assert ">null<" in rendered
    assert ">true<" in rendered
    assert ">7<" in rendered
    # A string keeps its JSON quotes, and they arrive HTML-escaped — the values come from a
    # caller, so escaping is the point (`OI2`) and the quoting is what says "this is a string".
    assert "&quot;x&quot;" in rendered


def test_an_empty_object_or_array_is_not_a_collapsible_nothing() -> None:
    """`{}` under a disclosure triangle is a click that reveals nothing."""
    for empty, spelling in (({}, "{}"), ([], "[]")):
        rendered = otlp_inspector.json_tree(empty, name="a")

        assert spelling in rendered
        assert "<details" not in rendered, rendered


def test_the_page_offers_the_tree_and_the_raw_bytes_and_not_the_credential() -> None:
    """The rendered page, because that is the artefact — a renderer that is right and a template
    that never calls it is a screen saying nothing happened."""
    inspector = Inspector()
    _post(inspector, TRACES, headers={"x-api-key": "super-secret-siem-token"})
    page = render_page(inspector)

    assert "aira.use_case" in page and "kundenservice" in page
    assert "<details class=batch>" in page
    assert "raw — the exact decoded bytes" in page
    assert "/batch/1/raw" in page
    assert "x-api-key" in page and "(opaque, 23 chars)" in page
    assert "super-secret-siem-token" not in page


def test_a_batch_that_could_not_be_read_says_so_instead_of_offering_a_tree() -> None:
    """A protobuf batch has no tree and no raw text — and the page must not imply otherwise."""
    inspector = Inspector()
    inspector.record(
        signal="traces",
        raw=b"\x0a\xf8\x05",
        content_type="application/x-protobuf",
        content_encoding="",
    )
    page = render_page(inspector)

    assert "AIRA_OTEL_FORWARD_ENCODING=json" in page
    assert "/batch/1/raw" not in page


def test_an_empty_page_names_the_hop_before_this_one_first() -> None:
    """**An empty page is equally consistent with all three hops, and this one can only see the
    last.**

    Reported from use: the delivery channel pointed at a real receiver, the *observability*
    endpoint pointed at nothing, and nothing arrived — while this page explained the forwarding
    configuration, which was the half that was fine. Telemetry crosses
    applications → collector → here; a screen that names only its own configuration sends the
    reader to the wrong end of the wire.

    So the upstream hop comes first, and with the one command that answers it.
    """
    page = render_page(Inspector())

    assert "make otel-status" in page, "the one command that says whether anything arrived at all"
    assert "AIRA_OTEL_ENDPOINT" in page, "the hop before this one, which this page cannot see"
    assert page.index("AIRA_OTEL_ENDPOINT") < page.index("AIRA_OTEL_FORWARD_CONFIG"), (
        "the cheapest thing to rule out is named first, or the reader checks the far end twice"
    )
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


# --- Splunk HEC, the transport that is not OTLP (`FRD-621`) ---------------------------------------

#: Two real HEC events, as the `splunk_hec` exporter of collector-contrib 0.157.0 sent them through
#: the delivery leg on 2026-09-13, trimmed: a model-call span and a log record. The span's
#: attributes are a flat object — the conversion the exporter exists for — and the resource
#: attributes ride in `fields`.
HEC_SPAN = {
    "event": {
        "trace_id": "ea9703c3ecaad238bf937019507909c1",
        "span_id": "e2502050d997d239",
        "parent_span_id": "b717c2d780b0d7b5",
        "name": "POST",
        "attributes": {
            "aira.model": "gemini-2.5-flash",
            "aira.model_call.purpose": "serve",
            "aira.use_case": "kundenservice",
            "http.status_code": 200,
        },
        "end_time": 1789293897339949452,
        "kind": "SPAN_KIND_CLIENT",
        "start_time": 1789293896708285333,
    },
    "fields": {"collector": "aira-otel-collector", "service.name": "aira-gateway"},
    "host": "unknown",
    "source": "aira",
    "sourcetype": "aira:otel",
    "time": 1789293896.7082853,
}
HEC_LOG = {
    "event": '{"system": "postgres", "operation": "connect", "outcome": "ok"}',
    "fields": {"otel.log.severity.text": "INFO", "service.name": "aira-management"},
    "host": "unknown",
    "source": "aira",
    "sourcetype": "aira:otel",
    "time": 1789293790.179175,
}
HEC_BODY = json.dumps(HEC_SPAN) + json.dumps(HEC_LOG)


def _post_hec(
    inspector: Inspector, body: str, *, headers: dict[str, str] | None = None
) -> otlp_inspector.Arrival:
    return inspector.record(
        signal=otlp_inspector.HEC_SIGNAL,
        raw=gzip.compress(body.encode()),
        content_type="application/json",
        content_encoding="gzip",
        headers=headers,
    )


@pytest.mark.parametrize("separator", ["", "\n", "\r\n  "])
def test_a_hec_body_is_counted_by_the_events_in_it(separator: str) -> None:
    """**HEC takes JSON objects one after another, with no array around them**, so `json.loads`
    refuses every body of more than one event. Counting a batch as one record would answer *how
    much is going to Splunk* with the number of requests rather than the number of events."""
    body = json.dumps(HEC_SPAN) + separator + json.dumps(HEC_LOG)
    arrival = _post_hec(Inspector(), body)

    assert arrival.readable
    assert arrival.records == 2
    assert arrival.body == body, "the bytes the sender posted, not a re-serialisation"
    assert arrival.document() == [HEC_SPAN, HEC_LOG]


def test_hec_is_counted_apart_and_shown_on_the_page() -> None:
    """A fourth line in the counts rather than folded into `traces`: one HEC batch carries spans,
    logs and metrics together, so it is not any one of the three."""
    inspector = Inspector()
    _post_hec(inspector, HEC_BODY)
    page = render_page(inspector)

    assert inspector.summary()["batches"]["hec"] == 1
    assert inspector.summary()["records"]["hec"] == 2
    assert "hec 1 batches / 2 records" in page
    assert "aira.model_call.purpose" in page


def test_a_splunk_token_is_described_and_not_shown() -> None:
    """HEC's credential is `Authorization: Splunk <token>` — the scheme is worth showing, the token
    is not."""
    token = "11111111-2222-3333-4444-555555555555"
    arrival = _post_hec(Inspector(), HEC_BODY, headers={"authorization": f"Splunk {token}"})

    assert arrival.credentials == {"authorization": "Splunk (43 chars)"}
    assert token not in json.dumps(dataclasses.asdict(arrival), default=str)


@pytest.mark.parametrize("body", ["[1, 2]", "{not json", '{"event": 1} 7'])
def test_a_hec_body_that_is_not_a_run_of_events_is_counted_and_not_read(body: str) -> None:
    """Reported as unreadable and kept as an arrival — the counters are still the truth about
    *did anything reach here*."""
    inspector = Inspector()
    arrival = _post_hec(inspector, body)

    assert not arrival.readable
    assert arrival.records == 0
    assert inspector.summary()["batches"]["hec"] == 1


def test_the_server_takes_hec_where_a_sender_posts_it_and_answers_as_hec_does() -> None:
    """Over HTTP, because the path and the reply are the server's: a sender pointed at
    `/services/collector` that got a `404` would retry until its queue filled, and one that parses
    the reply expects HEC's body rather than OTLP's `{}`. OTLP keeps its own answer."""
    inspector = Inspector()
    handler = type("BoundHandler", (otlp_inspector.Handler,), {"inspector": inspector})
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    # No proxy: the environment may name one, and it cannot reach this process's loopback.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    base = f"http://127.0.0.1:{server.server_address[1]}"

    def post(path: str, body: bytes) -> bytes:
        request = urllib.request.Request(
            base + path, data=body, headers={"content-type": "application/json"}, method="POST"
        )
        with opener.open(request) as reply:
            assert reply.status == 200
            return reply.read()

    try:
        for path in sorted(otlp_inspector.HEC_PATHS):
            assert json.loads(post(path, HEC_BODY.encode())) == {"text": "Success", "code": 0}
        assert post("/v1/traces", json.dumps(TRACES).encode()) == b"{}"
    finally:
        server.shutdown()
        server.server_close()

    assert inspector.summary()["records"]["hec"] == 4
    assert inspector.summary()["records"]["traces"] == 1
