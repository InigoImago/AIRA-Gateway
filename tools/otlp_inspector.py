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

**The page shows the document, not a summary of it.** It began as three tables — one flattened row
per span, per log record, per metric — which is a readable shape and the wrong one for the job: a
table is this file's opinion about what matters, and somebody checking what a receiver will be
handed needs *what a receiver will be handed*. So each batch is a collapsible JSON tree over the
real document, with the exact decoded bytes one click away and at `/batch/<n>/raw`. Nothing is
reordered, renamed or dropped on the way to the screen.

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
from dataclasses import dataclass
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
    #: The decoded payload **as text**, kept verbatim — this is what `/batch/<n>/raw` returns and
    #: what the tree is parsed from on demand. Text rather than a parsed structure because the
    #: point of this page is the bytes: a `dict` has already lost key order, duplicate keys and
    #: whatever the sender's formatting was, and it is the larger of the two in memory besides.
    body: str = ""
    undecoded: str = ""
    records: int = 0

    @property
    def readable(self) -> bool:
        return bool(self.body)

    def document(self) -> Any:
        """The body parsed, for rendering. Never cached: the text is the record."""
        return json.loads(self.body)


#: Where each signal keeps its records, in the protobuf-JSON mapping: the resource list, the scope
#: list inside it, and the leaf list inside that. Counting is all this file does with the shape —
#: the page renders the document itself.
SIGNAL_SHAPE = {
    "traces": ("resourceSpans", "scopeSpans", "spans"),
    "logs": ("resourceLogs", "scopeLogs", "logRecords"),
    "metrics": ("resourceMetrics", "scopeMetrics", "metrics"),
}


def count_records(signal: str, document: dict[str, Any]) -> int:
    """How many spans / log records / metrics one batch carried.

    The one number worth deriving. *"21 records in 6 batches"* answers **is anything going out**
    without deciding for the reader which of a record's fields matter — which is what the three
    flattening tables this replaced were doing, and why they were the wrong shape for a page whose
    whole job is showing what a receiver receives.
    """
    resources, scopes, leaves = SIGNAL_SHAPE[signal]
    return sum(
        len(scope.get(leaves) or [])
        for resource in document.get(resources) or []
        for scope in resource.get(scopes) or []
    )


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
                text = body.decode()
                document = json.loads(text)
            except (ValueError, UnicodeDecodeError) as exc:
                arrival.undecoded = f"{type(exc).__name__}: {exc}"
            else:
                if isinstance(document, dict):
                    arrival.body = text
                    arrival.records = count_records(signal, document)
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
            self.records[signal] += arrival.records
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
:root { color-scheme: light dark; --line: #8883; --dim: #7b7b8b; --accent: #2f6feb;
        --key: #8250df; --str: #0a7c3e; --num: #b3541e; --lit: #0550ae; }
@media (prefers-color-scheme: dark) {
  :root { --key: #d2a8ff; --str: #7ee787; --num: #ffa657; --lit: #79c0ff; }
}
* { box-sizing: border-box; }
body { margin: 0; font: 13px/1.55 ui-monospace, SFMono-Regular, Menlo, monospace; }
header { padding: 14px 18px; border-bottom: 1px solid var(--line); position: sticky; top: 0;
         background: Canvas; z-index: 1; }
h1 { font: 600 15px/1.3 system-ui, sans-serif; margin: 0 0 4px; }
.sub { color: var(--dim); font: 12px/1.5 system-ui, sans-serif; }
main { padding: 12px 18px 60px; }
a { color: var(--accent); }

/* one batch */
.batch { border: 1px solid var(--line); border-radius: 5px; margin-bottom: 8px; }
.batch > summary { padding: 7px 10px; cursor: pointer; list-style: none; }
.batch > summary::-webkit-details-marker { display: none; }
.batch > summary::before { content: "▸ "; color: var(--dim); }
.batch[open] > summary::before { content: "▾ "; }
.batch[open] > summary { border-bottom: 1px solid var(--line); }
.body { padding: 8px 10px 10px; }
.tag { display: inline-block; padding: 0 6px; margin-right: 4px; border: 1px solid var(--line);
       border-radius: 3px; font-size: 11px; }
.dim { color: var(--dim); }

/* the json tree */
details.node { margin: 0; }
details.node > summary { cursor: pointer; list-style: none; }
details.node > summary::-webkit-details-marker { display: none; }
details.node > summary::before { content: "▸ "; color: var(--dim); }
details.node[open] > summary::before { content: "▾ "; }
.tree, .tree ul { list-style: none; margin: 0; padding-left: 1.35em; }
.tree { padding-left: 0; overflow-x: auto; }
.tree li { border-left: 1px solid var(--line); padding-left: .6em; margin-left: .15em; }
.k { color: var(--key); }
.s { color: var(--str); }
.n { color: var(--num); }
.l { color: var(--lit); }
.count { color: var(--dim); font-size: 11px; }

pre { background: #8881; padding: 10px; overflow-x: auto; border-radius: 4px; margin: 6px 0 0;
      white-space: pre-wrap; word-break: break-all; }
.empty { color: var(--dim); padding: 24px 0; max-width: 72ch;
         font: 13px/1.6 system-ui, sans-serif; }
"""


def _ago(when: float | None) -> str:
    if not when:
        return "never"
    seconds = time.time() - when
    return f"{seconds:.0f}s ago" if seconds < 90 else f"{seconds / 60:.0f}min ago"


def _esc(value: Any) -> str:
    return html.escape("" if value is None else str(value))


#: How deep the tree is expanded when a batch is opened. Deep enough to reach a span
#: (`resourceSpans` → `[0]` → `scopeSpans` → `[0]` → `spans`), shallow enough that opening a batch
#: of 512 does not paint 512 open subtrees.
OPEN_DEPTH = 5


def _scalar(value: Any) -> str:
    """One JSON scalar, coloured by type and escaped. `None`/`True` keep their JSON spelling."""
    if isinstance(value, str):
        return f"<span class=s>{_esc(json.dumps(value))}</span>"
    if isinstance(value, bool) or value is None:
        return f"<span class=l>{'null' if value is None else str(value).lower()}</span>"
    if isinstance(value, int | float):
        return f"<span class=n>{_esc(value)}</span>"
    return _esc(value)  # pragma: no cover - json has no other scalar


def json_tree(value: Any, *, name: str | None = None, depth: int = 0) -> str:
    """A JSON value as nested `<details>`, expanded to :data:`OPEN_DEPTH`.

    **No JavaScript, and that is not a limitation being worked around.** `<details>` collapses in
    every browser without a script, so this page keeps working under a content policy that blocks
    inline script — and the collector's payloads are read by people whose job is being careful
    about what runs in their browser.

    Nothing is reordered, renamed, filtered or unwrapped. A tree that tidied
    `{"key": "aira.model", "value": {"stringValue": "…"}}` into `aira.model: …` would be showing
    what this file thinks OTLP means rather than what the receiver is handed — which is the whole
    reason the flattened tables this replaced were the wrong shape.
    """
    label = f"<span class=k>{_esc(name)}</span>: " if name is not None else ""

    if isinstance(value, dict):
        if not value:
            return f"<li>{label}<span class=l>{{}}</span>"
        kids = "".join(json_tree(v, name=k, depth=depth + 1) for k, v in value.items())
        count = f"<span class=count> {len(value)} key{'s' if len(value) != 1 else ''}</span>"
        opened = " open" if depth < OPEN_DEPTH else ""
        return (
            f"<li><details class=node{opened}><summary>{label}<span class=l>{{…}}</span>{count}"
            f"</summary><ul>{kids}</ul></details>"
        )

    if isinstance(value, list):
        if not value:
            return f"<li>{label}<span class=l>[]</span>"
        kids = "".join(json_tree(v, name=str(i), depth=depth + 1) for i, v in enumerate(value))
        count = f"<span class=count> {len(value)} item{'s' if len(value) != 1 else ''}</span>"
        opened = " open" if depth < OPEN_DEPTH else ""
        return (
            f"<li><details class=node{opened}><summary>{label}<span class=l>[…]</span>{count}"
            f"</summary><ul>{kids}</ul></details>"
        )

    return f"<li>{label}{_scalar(value)}"


def _batch_html(arrival: Arrival) -> str:
    """One arrival: a collapsed header, then the tree and the exact bytes beneath it."""
    credentials = (
        " ".join(
            f"<span class=tag>{_esc(n)}: {_esc(d)}</span>"
            for n, d in sorted(arrival.credentials.items())
        )
        or "<span class='tag dim'>no credential header</span>"
    )
    summary = (
        f"<span class=tag>#{arrival.number}</span>"
        f"<span class=tag>{_esc(arrival.signal)}</span>"
        f"<span class=tag>{arrival.records} record{'s' if arrival.records != 1 else ''}</span>"
        f"<span class=tag>{_esc(arrival.content_type)}</span>"
        f"<span class=tag>{_esc(arrival.content_encoding)}</span>"
        f"<span class=tag>{arrival.bytes_on_wire}&rarr;{arrival.bytes_decoded} B</span>"
        f"{credentials}"
        f"<span class=dim>{_ago(arrival.at)}</span>"
    )

    if not arrival.readable:
        inner = f"<p class=empty>{_esc(arrival.undecoded)}</p>"
    else:
        tree = f"<ul class=tree>{json_tree(arrival.document())}</ul>"
        raw = (
            "<details><summary class=dim>raw — the exact decoded bytes</summary>"
            f"<pre>{_esc(arrival.body)}</pre></details>"
        )
        inner = (
            f"{tree}<p class=dim style='margin:10px 0 0'>"
            f"<a href='/batch/{arrival.number}'>document</a> · "
            f"<a href='/batch/{arrival.number}/raw'>raw</a></p>{raw}"
        )
    return (
        f"<details class=batch><summary>{summary}</summary><div class=body>{inner}</div></details>"
    )


def render_page(inspector: Inspector, *, refresh: int = 15) -> str:
    """The whole page.

    **The refresh is slower than it was, because the page is now something you open.** A five-second
    meta-refresh collapses every `<details>` a reader has just expanded, which makes a tree useless
    — the browser reloads the document, not the DOM state. Fifteen seconds is a compromise; the
    honest fix is a reader who reloads when they want to, and the header says how stale the view is.
    """
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
        # **Newest first, and a bounded number of them.** Forty collapsed batches is a page; forty
        # expanded documents is a megabyte of HTML nobody scrolls. The rest are still counted in
        # the header and still readable at `/batch/<n>`.
        body = "".join(_batch_html(arrival) for arrival in arrivals[:40])

    return f"""<!doctype html><html lang=en><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<meta http-equiv=refresh content="{refresh}">
<title>AIRA — what the second destination receives</title>
<style>{STYLE}</style>
<header>
  <h1>What the second destination receives</h1>
  <div class=sub>{_esc(counts)} · last {_esc(_ago(summary["last_arrival"]))} ·
    keeping {summary["kept"]} of {summary["keep_limit"]} batches ·
    <span class=dim>reloads every {refresh}s, which closes what you have opened</span> ·
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
            # `/batch/12` pretty-prints the document; `/batch/12/raw` is the **exact decoded
            # bytes** — what the receiver was handed, byte for byte, after gunzip and nothing else.
            # Two routes because they answer different questions: one is for reading, the other is
            # for `curl … | jq` and for diffing against whatever your receiver logged.
            parts = [segment for segment in path.split("/") if segment]
            raw = len(parts) == 3 and parts[2] == "raw"
            try:
                number = int(parts[1])
            except IndexError, ValueError:
                self._send(404, b'{"error":"no such batch"}', "application/json")
                return
            arrival = self.inspector.find(number)
            if arrival is None or not arrival.readable:
                self._send(404, b'{"error":"no such readable batch"}', "application/json")
                return
            if raw:
                self._send(200, arrival.body.encode(), "text/plain; charset=utf-8")
            else:
                payload = json.dumps(arrival.document(), indent=2).encode()
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
