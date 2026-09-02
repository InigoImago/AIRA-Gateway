"""A standing-in receiver for the forwarding leg: what your SIEM would be handed, on a page.

**Why this exists.** `AIRA_OTEL_FORWARD_*` sends a second copy of the telemetry somewhere, and
until that somewhere is wired up there is nothing to point it at — so the questions a person asks
first (*is anything going out at all · what is in it · is my credential on the request · is the
filter keeping the right spans*) had no answer that did not involve standing up a SIEM. The three
places that came close each answer something else: `make otel-arrivals` is what **arrived** at the
collector before any filtering, `AIRA_OTEL_ARRIVED_FILE` is the same thing as a file, and
`make otel-status` is counters. None of them is *what left, on the leg that leaves*.

This is that: an ordinary OTLP/HTTP receiver, with a page in front of it.

    make otlp-inspector          # start it, and print where to look

**It is a debugging tool and it is shaped like one.** Everything is in memory and capped; a restart
loses it. It is in the `debug` Compose profile, so `make up` does not start it. There is no
authentication, and there is a reason it needs none *and* must never be published anywhere but a
developer's own machine: it holds `aira.subject`, `aira.source_ip` and whatever a log line carried,
which is the same content `FRD-505` puts behind a role check and a retention clock. The one thing
it deliberately does not keep is the **value** of an `Authorization` header — that is a credential,
and it is reported as present-or-absent, with its scheme and length, and never shown.

**Protobuf is accepted and not decoded.** `AIRA_OTEL_FORWARD_ENCODING=proto` is what an Azure
Monitor destination needs (`FRD-618`), so a batch may well arrive as protobuf; decoding it would
mean a schema and a dependency this file does not have. It is counted, sized, and labelled, and the
page says which variable to flip to read it. Answering *did it leave, and was it authenticated* is
the job; answering *what does field 4 say* is what `json` is for.
"""

from __future__ import annotations

import gzip
import html
import json
import os
import threading
import time
from collections import deque
from collections.abc import Mapping
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

#: The OTLP/HTTP paths a collector posts to. Anything else is a page request.
SIGNAL_PATHS = {"/v1/traces": "traces", "/v1/metrics": "metrics", "/v1/logs": "logs"}

#: How many batches are kept. A batch, not a span: `AIRA_OTEL_FORWARD_BATCH_SIZE` decides how many
#: records ride in one, so this is a bound on *arrivals* and the record count varies with it.
DEFAULT_KEEP = 200

#: And a bound on what one batch may cost in memory. A body past this is kept as its metadata only
#: — the counts stay right, and one enormous document cannot evict two hundred useful ones.
DEFAULT_MAX_BODY = 2 * 1024 * 1024

#: Substrings that make a **header name** a credential, whose value is therefore never shown.
#:
#: **A list rather than the single name `authorization`, because that is what receivers actually
#: ask for.** In the field: `x-api-key`, `api-key`, `DD-API-KEY`, `X-Honeycomb-Team`,
#: `X-Seq-ApiKey`, `Authorization: Bearer …`, `Authorization: Splunk …`. Looking only for
#: `Authorization` — which is what this file did first — reports *no credential on this request*
#: to somebody who has just configured one, which is the worst possible wrong answer here: it
#: sends them to re-check a credential that was fine.
#:
#: Matched as a substring of the lower-cased name, so the list stays short and still catches the
#: next product's spelling. Erring towards **over**-redaction on purpose: a header wrongly treated
#: as a secret costs a reader one detail, and the other mistake publishes a key.
CREDENTIAL_HINTS = ("auth", "key", "token", "secret", "password", "credential", "cookie", "team")

#: Headers not worth a row: set by the HTTP client on every request and about nothing.
BORING_HEADERS = frozenset({"host", "content-length", "accept-encoding", "connection"})


def _attributes(items: list[dict[str, Any]] | None) -> dict[str, str]:
    """OTLP's `[{key, value: {stringValue: …}}]` as a plain mapping, values rendered as text.

    The nesting is the protobuf-JSON mapping and it is the thing people are surprised by, so it is
    flattened here once rather than in each of the three renderers. A value whose type this does
    not know is JSON-encoded rather than dropped: an unfamiliar shape on the page is information,
    and an absent one reads as *the attribute was not sent*.
    """
    out: dict[str, str] = {}
    for item in items or []:
        key = item.get("key")
        if not isinstance(key, str):
            continue
        value = item.get("value")
        if not isinstance(value, dict) or not value:
            out[key] = ""
            continue
        kind, raw = next(iter(value.items()))
        if kind in ("stringValue", "intValue", "boolValue", "doubleValue"):
            out[key] = str(raw)
        else:
            out[key] = json.dumps(raw, ensure_ascii=False)
    return out


def describe_headers(headers: Mapping[str, str]) -> tuple[dict[str, str], dict[str, str]]:
    """Split a request's headers into *credentials, described* and *everything else, verbatim*.

    A credential is described by its **scheme and length** and never by its value —
    `Bearer (204 chars)`, `(opaque, 32 chars)`. That is enough to answer the two questions somebody
    actually has while wiring a destination up: *did my credential reach the request at all*, and
    *is it the one I think it is* (a length is a fingerprint; a value is a leak). This page is one
    people leave open on a second monitor, and a page that prints keys is a place keys escape from.

    An **empty** credential header is reported as empty rather than as absent. That distinction is
    the defect this whole area began with: a `headers:` block that was always present sent
    `authorization: ''` on every request, and a viewer that could not tell the two apart could not
    have shown it.
    """
    credentials: dict[str, str] = {}
    plain: dict[str, str] = {}
    for name, value in headers.items():
        lowered = name.lower()
        if lowered in BORING_HEADERS:
            continue
        if any(hint in lowered for hint in CREDENTIAL_HINTS):
            if not value:
                credentials[lowered] = "(empty)"
                continue
            scheme, _, rest = value.partition(" ")
            credentials[lowered] = (
                f"{scheme} ({len(value)} chars)" if rest else f"(opaque, {len(value)} chars)"
            )
        else:
            plain[lowered] = value
    return credentials, plain


def _nanos(value: Any) -> float | None:
    """OTLP timestamps are nanoseconds in a *string*, because JSON has no 64-bit integer."""
    try:
        return int(value) / 1e9
    except TypeError, ValueError:
        return None


@dataclass(slots=True)
class Arrival:
    """One POST, and everything the page knows about it."""

    number: int
    at: float
    signal: str
    content_type: str
    content_encoding: str
    bytes_on_wire: int
    bytes_decoded: int
    credentials: dict[str, str]
    headers: dict[str, str]
    document: dict[str, Any] | None = None
    undecoded: str = ""
    records: list[dict[str, Any]] = field(default_factory=list)

    @property
    def readable(self) -> bool:
        return self.document is not None


def _flatten_traces(document: dict[str, Any]) -> list[dict[str, Any]]:
    """One row per span, with the resource and scope folded in.

    The row is what the SIEM question is asked in — *who called which model and how did it end* —
    so `aira.*` sits beside the span's own name rather than three levels down from it.
    """
    rows: list[dict[str, Any]] = []
    for resource_spans in document.get("resourceSpans") or []:
        resource = _attributes((resource_spans.get("resource") or {}).get("attributes"))
        for scope_spans in resource_spans.get("scopeSpans") or []:
            scope = (scope_spans.get("scope") or {}).get("name", "")
            for span in scope_spans.get("spans") or []:
                start, end = (
                    _nanos(span.get("startTimeUnixNano")),
                    _nanos(span.get("endTimeUnixNano")),
                )
                rows.append(
                    {
                        "service": resource.get("service.name", ""),
                        "scope": scope,
                        "name": span.get("name", ""),
                        "trace_id": span.get("traceId", ""),
                        "span_id": span.get("spanId", ""),
                        "parent": span.get("parentSpanId", ""),
                        "ms": round((end - start) * 1000, 1) if start and end else None,
                        "attributes": _attributes(span.get("attributes")),
                    }
                )
    return rows


def _flatten_logs(document: dict[str, Any]) -> list[dict[str, Any]]:
    """One row per log record. The body is a string for everything this system emits — structlog
    renders JSON and hands the rendered line over — so it is shown as it is rather than parsed."""
    rows: list[dict[str, Any]] = []
    for resource_logs in document.get("resourceLogs") or []:
        resource = _attributes((resource_logs.get("resource") or {}).get("attributes"))
        for scope_logs in resource_logs.get("scopeLogs") or []:
            for record in scope_logs.get("logRecords") or []:
                body = record.get("body") or {}
                rows.append(
                    {
                        "service": resource.get("service.name", ""),
                        "severity": record.get("severityText", ""),
                        "body": body.get("stringValue", json.dumps(body, ensure_ascii=False)),
                        "trace_id": record.get("traceId", ""),
                        "attributes": _attributes(record.get("attributes")),
                    }
                )
    return rows


def _flatten_metrics(document: dict[str, Any]) -> list[dict[str, Any]]:
    """One row per metric, with how many points it carried.

    Not one row per point: a histogram arrives with a bucket list, and a page listing those is a
    page nobody reads. The count is what answers *is this signal moving at all*.
    """
    rows: list[dict[str, Any]] = []
    for resource_metrics in document.get("resourceMetrics") or []:
        resource = _attributes((resource_metrics.get("resource") or {}).get("attributes"))
        for scope_metrics in resource_metrics.get("scopeMetrics") or []:
            for metric in scope_metrics.get("metrics") or []:
                kinds = ("sum", "gauge", "histogram", "exponentialHistogram")
                kind = next((k for k in kinds if k in metric), "")
                points = (metric.get(kind) or {}).get("dataPoints") or [] if kind else []
                rows.append(
                    {
                        "service": resource.get("service.name", ""),
                        "name": metric.get("name", ""),
                        "kind": kind,
                        "unit": metric.get("unit", ""),
                        "points": len(points),
                    }
                )
    return rows


FLATTEN = {"traces": _flatten_traces, "logs": _flatten_logs, "metrics": _flatten_metrics}


class Inspector:
    """The ring buffer and the counters, with a lock, because the server is threaded."""

    def __init__(self, keep: int = DEFAULT_KEEP, max_body: int = DEFAULT_MAX_BODY) -> None:
        self.keep = keep
        self.max_body = max_body
        self._lock = threading.Lock()
        self._arrivals: deque[Arrival] = deque(maxlen=keep)
        self._next = 1
        self.totals: dict[str, int] = {"traces": 0, "logs": 0, "metrics": 0}
        self.records: dict[str, int] = {"traces": 0, "logs": 0, "metrics": 0}
        self.started = time.time()

    def record(
        self,
        *,
        signal: str,
        raw: bytes,
        content_type: str,
        content_encoding: str,
        headers: Mapping[str, str] | None = None,
    ) -> Arrival:
        body = raw
        if "gzip" in content_encoding:
            try:
                body = gzip.decompress(raw)
            except OSError:
                body = raw

        credentials, plain = describe_headers(headers or {})

        arrival = Arrival(
            number=0,
            at=time.time(),
            signal=signal,
            content_type=content_type,
            content_encoding=content_encoding or "identity",
            bytes_on_wire=len(raw),
            bytes_decoded=len(body),
            credentials=credentials,
            headers=plain,
        )

        if len(body) > self.max_body:
            arrival.undecoded = f"{len(body)} bytes, over the {self.max_body}-byte keep limit"
        elif "json" in content_type:
            try:
                document = json.loads(body)
            except (ValueError, UnicodeDecodeError) as exc:
                arrival.undecoded = f"{type(exc).__name__}: {exc}"
            else:
                if isinstance(document, dict):
                    arrival.document = document
                    arrival.records = FLATTEN[signal](document)
                else:
                    arrival.undecoded = f"not an OTLP document: {type(document).__name__}"
        else:
            arrival.undecoded = (
                f"{content_type or 'no content-type'} — set AIRA_OTEL_FORWARD_ENCODING=json "
                "to read the contents here"
            )

        with self._lock:
            arrival.number = self._next
            self._next += 1
            self.totals[signal] += 1
            self.records[signal] += len(arrival.records)
            self._arrivals.append(arrival)
        return arrival

    def arrivals(self) -> list[Arrival]:
        """Newest first, which is the order somebody watching a stack wants."""
        with self._lock:
            return list(reversed(self._arrivals))

    def find(self, number: int) -> Arrival | None:
        with self._lock:
            return next((a for a in self._arrivals if a.number == number), None)

    def summary(self) -> dict[str, Any]:
        with self._lock:
            last = self._arrivals[-1].at if self._arrivals else None
            return {
                "kept": len(self._arrivals),
                "keep_limit": self.keep,
                "batches": dict(self.totals),
                "records": dict(self.records),
                "last_arrival": last,
                "uptime_seconds": round(time.time() - self.started, 1),
            }


# --- the page ------------------------------------------------------------------------------------

STYLE = """
:root { color-scheme: light dark; --line: #8883; --dim: #7b7b8b; --accent: #2f6feb; }
* { box-sizing: border-box; }
body { margin: 0; font: 13px/1.5 ui-monospace, SFMono-Regular, Menlo, monospace; }
header { padding: 14px 18px; border-bottom: 1px solid var(--line); }
h1 { font: 600 15px/1.3 system-ui, sans-serif; margin: 0 0 4px; }
.sub { color: var(--dim); font: 12px/1.5 system-ui, sans-serif; }
main { padding: 14px 18px 40px; }
table { border-collapse: collapse; width: 100%; margin-bottom: 22px; }
th, td { text-align: left; padding: 4px 10px 4px 0; border-bottom: 1px solid var(--line);
         vertical-align: top; }
th { font: 600 11px/1.5 system-ui, sans-serif; text-transform: uppercase; color: var(--dim); }
td.num { text-align: right; font-variant-numeric: tabular-nums; }
.tag { display: inline-block; padding: 0 6px; border: 1px solid var(--line); border-radius: 3px;
       font-size: 11px; }
.dim { color: var(--dim); }
.wrap { max-width: 62ch; overflow-wrap: anywhere; }
a { color: var(--accent); }
.empty { color: var(--dim); padding: 30px 0; max-width: 70ch;
         font: 13px/1.6 system-ui, sans-serif; }
pre { background: #8881; padding: 12px; overflow-x: auto; border-radius: 4px; }
"""


def _ago(when: float | None) -> str:
    if not when:
        return "never"
    seconds = time.time() - when
    return f"{seconds:.0f}s ago" if seconds < 90 else f"{seconds / 60:.0f}min ago"


def _esc(value: Any) -> str:
    return html.escape("" if value is None else str(value))


def _attribute_cell(attributes: dict[str, str], prefix: str = "aira.") -> str:
    """The `aira.*` attributes, which are the ones this system put there, then the rest.

    Ordered rather than filtered: the point of the page is to check what a SIEM receives, and
    hiding the framework's attributes would answer a different question than the one being asked.
    """
    ours = {k: v for k, v in attributes.items() if k.startswith(prefix)}
    rest = {k: v for k, v in attributes.items() if not k.startswith(prefix)}
    parts = [f"<b>{_esc(k)}</b>={_esc(v)}" for k, v in sorted(ours.items())]
    parts += [f"<span class=dim>{_esc(k)}={_esc(v)}</span>" for k, v in sorted(rest.items())]
    return " ".join(parts)


def _rows_html(arrival: Arrival) -> str:
    if arrival.signal == "traces":
        head = "<tr><th>service<th>span<th>ms<th>attributes"
        body = "".join(
            f"<tr><td>{_esc(r['service'])}<td class=wrap>{_esc(r['name'])}"
            f"<td class=num>{'' if r['ms'] is None else r['ms']}"
            f"<td class=wrap>{_attribute_cell(r['attributes'])}"
            for r in arrival.records
        )
    elif arrival.signal == "logs":
        head = "<tr><th>service<th>severity<th>body"
        body = "".join(
            f"<tr><td>{_esc(r['service'])}<td>{_esc(r['severity'])}<td class=wrap>{_esc(r['body'])}"
            for r in arrival.records
        )
    else:
        head = "<tr><th>service<th>metric<th>kind<th>points"
        body = "".join(
            f"<tr><td>{_esc(r['service'])}<td>{_esc(r['name'])}<td>{_esc(r['kind'])}"
            f"<td class=num>{r['points']}"
            for r in arrival.records
        )
    return f"<table>{head}{body}</table>"


def render_page(inspector: Inspector, *, refresh: int = 5) -> str:
    summary = inspector.summary()
    arrivals = inspector.arrivals()

    counts = " · ".join(
        f"{signal} {summary['batches'][signal]} batches / {summary['records'][signal]} records"
        for signal in ("traces", "logs", "metrics")
    )

    if not arrivals:
        body = (
            "<p class=empty>Nothing has arrived yet.<br><br>"
            "The collector forwards here only when <code>AIRA_OTEL_FORWARD_CONFIG</code> names the "
            "forwarding fragment <em>and</em> <code>AIRA_OTEL_FORWARD_ENDPOINT</code> points at "
            "this container. Setting the endpoint alone changes nothing — the fragment is the "
            "switch. Then recreate the collector and send a request through the gateway.</p>"
        )
    else:
        sections = []
        for arrival in arrivals[:40]:
            auth = (
                " ".join(
                    f"<span class=tag>{_esc(name)}: {_esc(description)}</span>"
                    for name, description in sorted(arrival.credentials.items())
                )
                if arrival.credentials
                else "<span class='tag dim'>no credential header</span>"
            )
            meta = (
                f"<span class=tag>#{arrival.number}</span> "
                f"<span class=tag>{_esc(arrival.signal)}</span> "
                f"<span class=tag>{_esc(arrival.content_type)}</span> "
                f"<span class=tag>{_esc(arrival.content_encoding)}</span> "
                f"<span class=tag>{arrival.bytes_on_wire}&rarr;{arrival.bytes_decoded} B</span> "
                f"{auth} "
                f"<span class=dim>{_ago(arrival.at)}</span>"
            )
            if arrival.readable:
                detail = _rows_html(arrival) + (
                    f"<p class=dim><a href='/batch/{arrival.number}'>the document as it "
                    "arrived</a></p>"
                )
            else:
                detail = f"<p class=empty>{_esc(arrival.undecoded)}</p>"
            sections.append(f"<p>{meta}</p>{detail}")
        body = "".join(sections)

    return f"""<!doctype html><html lang=en><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<meta http-equiv=refresh content="{refresh}">
<title>AIRA — what the second destination receives</title>
<style>{STYLE}</style>
<header>
  <h1>What the second destination receives</h1>
  <div class=sub>{_esc(counts)} · last {_esc(_ago(summary["last_arrival"]))} ·
    keeping {summary["kept"]} of {summary["keep_limit"]} batches · refreshes every {refresh}s ·
    <a href="/api/summary">/api/summary</a></div>
</header>
<main>{body}</main>
</html>"""


# --- the server ----------------------------------------------------------------------------------

#: What OTLP calls a successful export: an empty `ExportXServiceResponse`. A receiver may put a
#: `partialSuccess` in here to say it dropped some of the batch — which the Python exporter throws
#: away and `FRD-617` §3.8 is about, one hop upstream of this one.
OK_BODY = b"{}"


class Handler(BaseHTTPRequestHandler):
    inspector: Inspector

    protocol_version = "HTTP/1.1"

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("content-type", content_type)
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:  # noqa: N802 — the stdlib's spelling
        signal = SIGNAL_PATHS.get(self.path.split("?", 1)[0])
        if signal is None:
            self._send(404, b'{"error":"not an OTLP path"}', "application/json")
            return
        length = int(self.headers.get("content-length") or 0)
        raw = self.rfile.read(length) if length else b""
        self.inspector.record(
            signal=signal,
            raw=raw,
            content_type=(self.headers.get("content-type") or "").lower(),
            content_encoding=(self.headers.get("content-encoding") or "").lower(),
            headers={key.lower(): value for key, value in self.headers.items()},
        )
        self._send(200, OK_BODY, "application/json")

    def do_GET(self) -> None:  # noqa: N802 — the stdlib's spelling
        path = self.path.split("?", 1)[0]
        if path in ("/", "/index.html"):
            self._send(200, render_page(self.inspector).encode(), "text/html; charset=utf-8")
        elif path == "/api/summary":
            payload = json.dumps(self.inspector.summary(), indent=2).encode()
            self._send(200, payload, "application/json")
        elif path == "/healthz":
            self._send(200, b'{"status":"ok"}', "application/json")
        elif path.startswith("/batch/"):
            arrival = self.inspector.find(int(path.rsplit("/", 1)[-1] or 0))
            if arrival is None or not arrival.readable:
                self._send(404, b'{"error":"no such readable batch"}', "application/json")
                return
            payload = json.dumps(arrival.document, indent=2).encode()
            self._send(200, payload, "application/json")
        else:
            self._send(404, b'{"error":"no such page"}', "application/json")

    def log_message(self, *_args: Any) -> None:
        """Silent. The page is the log, and one line per batch would bury it."""


def serve(host: str = "0.0.0.0", port: int = 4318, keep: int = DEFAULT_KEEP) -> None:  # noqa: S104
    handler = type("BoundHandler", (Handler,), {"inspector": Inspector(keep=keep)})
    server = ThreadingHTTPServer((host, port), handler)
    print(  # noqa: T201 — this is a command-line tool and this is its only output
        f"otlp-inspector listening on {host}:{port} — OTLP at /v1/{{traces,logs,metrics}}, "
        f"the page at /",
        flush=True,
    )
    server.serve_forever()


if __name__ == "__main__":  # pragma: no cover — the container's entry point
    serve(
        host=os.environ.get("AIRA_OTLP_INSPECTOR_HOST", "0.0.0.0"),  # noqa: S104
        port=int(os.environ.get("AIRA_OTLP_INSPECTOR_PORT", "4318")),
        keep=int(os.environ.get("AIRA_OTLP_INSPECTOR_KEEP", str(DEFAULT_KEEP))),
    )
