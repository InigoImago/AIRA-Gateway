"""How deeply a caller's JSON nests, and how many values it holds, measured **before** anything
parses it.

Every walk over a body recurses — the `json` decoder, `json.dumps`, `strip_attachments`, the
redactor, `storable`, the pydantic models — so a caller who chooses the depth chooses where a
`RecursionError` happens: a `500`, or a refusal that is never recorded. A caller's own value must
never become a server error or a missing record (`LESSONS.md` §1, `FRD-122`), so the depth is
bounded here, in one place both planes read, and an over-deep body is a named `400`.

**And what a body costs is its structure, not its bytes.** Every value is an object to the decoder
and, where a surface models it, a model to the parse; the audit writer walks each of them four
times more. A body of minimal parts packs a value into every six bytes, so the byte ceiling alone
would let a caller choose a parse of over a million values — seconds of the event loop every use
case shares, and most of a gigabyte — before the rate limit or a suspension could refuse anything.
So the values are bounded here as well (`MAX_JSON_VALUES`), in the same scan, and a body past
either bound is refused unread.

**Counted without recursing and without decoding**, since a check that can blow the stack is not a
check. The bytes must be **UTF-8**, and both planes decode a body as UTF-8 before measuring it:
JSON's structural characters are ASCII and every UTF-8 continuation byte has the high bit set, so
the raw bytes can be scanned — where in UTF-16 or UTF-32 a character's bytes can include ``0x22``,
which this would read as a quote. The scan:

1. remove escaped backslashes, then escaped quotes — after which every remaining ``"`` opens or
   closes a string (order matters: ``\\\\"`` is an escaped backslash and *then* a closing quote);
2. drop every byte but the quotes, the brackets and the commas;
3. split on ``"`` and keep the parts outside any string — so a bracket or a comma inside a text
   field counts for nothing, and a caller may still send source code. A window at a time, carrying
   whether the next window starts inside a string: a split makes an object per segment, and the
   window, not the body, bounds how many exist at once;
4. map ``{``/``[`` to +1 and ``}``/``]`` to −1 and take the running maximum in C, carrying the
   depth from window to window and stopping at the first one past the limit;
5. count the values as it goes: one for the document, one per comma — every value in an object or
   an array but its first follows one — and one per opening bracket for that first value. An empty
   object or array has none and is counted all the same, so the figure is an upper bound, exact for
   a document with no empty object or array, and never an undercount.

**Linear in the body, in time and in memory** — at most two copies of it, plus a window. Not as
cheap as the parse it guards on every input: a body of nothing but brackets costs a C-level step
per bracket where `json.loads` refuses it at the second character, which is what the request-size
limit in front of this bounds. The common case costs a few C-level counts and no scan at all.
"""

from __future__ import annotations

import itertools
import sys
from enum import StrEnum

#: How deep a request body may nest.
#:
#: Shared rather than a setting per plane, because a body the gateway accepts may be sent to the
#: control plane's endpoints too. Derived: the deepest structure anything here defines is a
#: `responseSchema` at 8 levels inside its wrapper, 14 (`FRD-112` FR-3); 64 is four times that,
#: and far below the interpreter's 1000 frames where a recursive walk fails — so every later walk
#: survives a body this accepts.
MAX_JSON_DEPTH = 64

#: How many values a request body may hold — every object, array, string, number, boolean and
#: null, wherever it sits, the function results and schemas a caller writes freely included.
#:
#: Derived from what a legitimate request carries, generously: 128 functions with the schemas
#: generators write — a dozen documented properties is about 100 values a function, 13 000; a long
#: agent conversation, a thousand turns at about ten values each, 10 000; a structured tool result
#: of a few thousand records, tens of thousands; an embedding batch of 256 texts, 1 300; 200 records
#: to rank, 800. Together under 60 000, and this is four times that, so no client of either surface
#: comes near it — where the byte ceiling alone admits seventeen times as many. Not a setting,
#: and independent of the byte ceiling, which an installation raises for large attachments: an
#: attachment is one value, however long. Shared with the control plane for the reason the depth is.
MAX_JSON_VALUES = 250_000

#: How many bytes of the quotes and brackets are split at once. It bounds the objects a split makes,
#: and is large enough that the loop over windows costs nothing beside the work inside it.
SCAN_WINDOW = 1 << 16

_STRUCTURAL = b"{[}]"
#: +1 for an opening bracket, −1 (`0xff` read as a signed byte) for a closing one.
_STEPS = bytes.maketrans(_STRUCTURAL, b"\x01\x01\xff\xff")
_DROP = bytes(byte for byte in range(256) if byte not in _STRUCTURAL)
#: Every byte the scan has no use for once the escapes are gone: all but a quote, the brackets and
#: the comma that separates two values.
_UNREAD = bytes(byte for byte in range(256) if byte not in b'",' + _STRUCTURAL)


class Exceeded(StrEnum):
    """Which bound a body breaks, so each caller can name it in its refusal."""

    DEPTH = "depth"
    VALUES = "values"


def exceeded(
    raw: bytes, *, depth: int = MAX_JSON_DEPTH, values: int = MAX_JSON_VALUES
) -> Exceeded | None:
    """Which bound ``raw`` — a JSON document, as UTF-8 bytes — breaks, or ``None``: nesting deeper
    than ``depth``, or holding more than ``values`` values. One scan answers both.

    The common case costs a few C-level counts: a document with no more than ``depth`` opening
    brackets cannot nest deeper, and one with fewer commas and brackets than ``values`` — counting
    those inside strings too — cannot hold more values; nor can one shorter than ``values`` bytes,
    since the count below is at most one more than the bytes it reads.

    **Depth first**, wherever it is found: a body past both bounds is refused as too deep, so the
    verdict cannot depend on which window happened to be read first. The depth stops the scan at
    the first window past it; the values are a total, read at the end.

    Malformed input is measured rather than refused: a document that does not parse is about to be
    refused as unparseable anyway, and both answers are a `400`.
    """
    brackets = raw.count(b"{") + raw.count(b"[")
    if brackets <= depth and (values > len(raw) or brackets + raw.count(b",") < values):
        return None
    cleaned = raw.replace(b"\\\\", b"").replace(b'\\"', b"")
    scanned = cleaned.translate(None, _UNREAD)
    level = 0
    held = 1
    in_string = False
    counted = False
    for start in range(0, len(scanned), SCAN_WINDOW):
        segments = scanned[start : start + SCAN_WINDOW].split(b'"')
        outside = b"".join(segments[in_string::2])
        steps = outside.translate(_STEPS, _DROP)
        # An odd number of quotes leaves the next window on the other side of a string boundary.
        in_string ^= len(segments) % 2 == 0
        held += outside.count(b",")
        if steps:
            counted = True
            if level + max(itertools.accumulate(memoryview(steps).cast("b"))) > depth:
                return Exceeded.DEPTH
            opened = steps.count(1)
            level += 2 * opened - len(steps)
            held += opened
    # No prefix went past the limit. With no bracket to count, the document is at depth 0.
    if not counted and depth < 0:
        return Exceeded.DEPTH
    return Exceeded.VALUES if held > values else None


def nests_deeper_than(raw: bytes, limit: int = MAX_JSON_DEPTH) -> bool:
    """Whether ``raw`` — a JSON document, as UTF-8 bytes — nests deeper than ``limit``.

    The depth half of :func:`exceeded`, for a caller that bounds nothing else.
    """
    return exceeded(raw, depth=limit, values=sys.maxsize) is Exceeded.DEPTH
