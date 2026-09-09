"""How deeply a caller's JSON nests, measured **before** anything parses it.

Every walk over a caller's body recurses: Python's `json` decoder, `json.dumps`,
`strip_attachments`, the redactor, `storable`, and the pydantic models each surface validates
against. Recursion has a ceiling, and a caller chooses where it is met.

Measured on 2026-09-08, against a gateway whose body ceiling is 8 MB:

- **~1 000 levels (12 kB)** — the refusal was **not recorded**: `RecursionError` inside
  `strip_attachments`, and `audit_refusal_not_recorded` in a log nobody reads.
- **~1 000 levels on a request that was served** — `500` after the model had already answered:
  spend, no answer, no audit row.
- **~50 000 levels (300 kB)** — `RecursionError` out of `json.loads`, straight to `500`, on both
  API surfaces, on `:dryRun`, `:checkThinking` and `/v1beta/suspensions`, and on every endpoint of
  the control plane.

Both halves are rules this repository already has. *A caller's own value must never become a
server error — and never a missing record* (`LESSONS.md` §1), and `FRD-122`'s *the log records
what was asked*. The door that was closed was the one for **values** — `ensure_body_is_encodable`
refuses a lone surrogate or an `Infinity` because those cost the row. Nobody had closed the door
for **structure**, and it costs the same row for the same reason.

So the depth is counted here, in one place both planes read, and a body over the bound is a named
`400` rather than a stack overflow nine steps later.

**Counted without recursing and without decoding**, because a check that can be made to blow the
stack is not a check. JSON's structural characters are ASCII, and every byte of a UTF-8
continuation has the high bit set, so the bytes can be scanned as they arrived:

1. remove escaped backslashes, then escaped quotes — after which every remaining ``"`` really
   does open or close a string (order matters: ``\\\\"`` is an escaped backslash and *then* a
   closing quote);
2. split on ``"`` and keep the even segments, which are the parts outside any string — so a
   *text* field full of brackets counts for nothing, and a caller may still send source code;
3. map ``{``/``[`` to +1 and ``}``/``]`` to −1, drop everything else, and run the sum in C.

Steps 1–3 are four C-level passes. Measured: 3.5 ms for a 4 MB body of ordinary text, 113 ms for
a deliberately pathological 4 MB of nothing but brackets — the same order as parsing it would
cost, and the parse is what this makes safe.
"""

from __future__ import annotations

import itertools

#: How deep a request body may nest.
#:
#: **A shared constant rather than a setting per plane**, because the two planes must agree: a
#: body the gateway accepts is one the control plane's own endpoints may be sent, and a bound
#: stated twice is the "fact that must agree in N places" this project has met nine times.
#:
#: The figure is derived rather than chosen. The deepest structure anything here *defines* is a
#: `responseSchema` at 8 levels (`FRD-112` FR-3) inside its request wrapper, which is 14; a tool
#: declaration's `parameters` gets the same 8. What is left is caller data — a tool result, a
#: replayed `functionResponse` — and 64 is four times the deepest shape this gateway has a name
#: for. It is also an order of magnitude below where a recursive walk starts to fail (the
#: interpreter's limit is 1000 frames, and `storable` alone reaches it at 999 from an *empty*
#: stack, so less than that from inside a request), which is the property that matters: a body
#: this accepts is a body every later walk can survive.
MAX_JSON_DEPTH = 64

_STRUCTURAL = b"{[}]"
#: +1 for an opening bracket, −1 (`0xff` read as a signed byte) for a closing one.
_STEPS = bytes.maketrans(_STRUCTURAL, b"\x01\x01\xff\xff")
_DROP = bytes(byte for byte in range(256) if byte not in _STRUCTURAL)


def nests_deeper_than(raw: bytes, limit: int = MAX_JSON_DEPTH) -> bool:
    """Whether ``raw`` — a JSON document, as bytes — nests deeper than ``limit``.

    Returns a verdict rather than a depth, so that the common case costs two C-level counts: a
    document with no more than ``limit`` opening brackets *in total* cannot nest deeper than
    ``limit``, whatever their arrangement.

    Malformed input is measured rather than refused. An unterminated string swallows the brackets
    after it (they are inside it), and a document that does not parse is about to be refused as
    unparseable anyway — the two answers are both a `400`, and deciding which one is not this
    function's job.
    """
    if raw.count(b"{") + raw.count(b"[") <= limit:
        return False
    cleaned = raw.replace(b"\\\\", b"").replace(b'\\"', b"")
    outside = b"".join(cleaned.split(b'"')[0::2])
    steps = outside.translate(_STEPS, _DROP)
    return max(itertools.accumulate(memoryview(steps).cast("b")), default=0) > limit
