"""Reading a caller's body: bounded in structure, and refused if it cannot be stored or forwarded.

Both checks run before any control is spent. A body that fails later — inside the writer, or when
httpx encodes the upstream request — would already have consumed a rate-limit allowance and a
budget reservation, and would leave no audit row, because recording covers requests that reached
an upstream (`FRD-122`, `FRD-124`).
"""

from __future__ import annotations

import json
from typing import Any

from fastapi import Request

from aira_common.nesting import MAX_JSON_DEPTH, MAX_JSON_VALUES, Exceeded, exceeded
from aira_gateway.api.gemini.errors import GeminiHTTPError


class BodyTooComplex(GeminiHTTPError):
    """A body refused for its structure before it was parsed: nested too deep, or holding too many
    values (`aira_common.nesting`). Its own type, so a surface words it in its own vocabulary — on
    the KIRA surface, the `422 VALIDATION_ERROR` of a body that is not a valid request."""


async def json_body(request: Request) -> Any:
    """The caller's body, parsed — refused, unread, when its structure is past what the path can
    afford: deeper than anything on it can walk, or more values than a request carries.

    Every walk over a body recurses (decoder, redactor, writer), and the caller picks the depth;
    every value is work for the decoder, the parse and the writer, and the caller picks how many.
    Both are measured before `json.loads`, which is where the cost starts, and before the rate limit
    and a suspension, which come after the parse; `aira_common.nesting` holds the bounds. Every
    route reads its body through here — `test_a_body_cannot_be_nested_out_of_the_audit_trail.py`
    fails on a `request.json()` call.

    **UTF-8 only** (RFC 8259 §8.1), and parsed from the text that was measured. `json.loads` reads
    UTF-16 and UTF-32 too, which the scan cannot: an escaped quote there is not the bytes it looks
    for, so a body passed the bound and still took the decoder down. A leading byte order mark is
    ignored, as the RFC allows.

    Raises `BodyTooComplex` for a body past a bound (every surface renders it), and `ValueError`,
    like `request.json()`, for one that is not JSON or not UTF-8: each surface words that refusal
    itself.
    """
    raw = await request.body()
    text = raw.decode("utf-8-sig")
    bound = exceeded(raw)
    if bound is not None:
        raise too_complex(bound)
    try:
        return json.loads(text)
    except RecursionError:
        # Not reachable for a body the scan measured; kept so a walk the bound did not foresee
        # is still the caller's named `400` rather than a server error with no row.
        raise too_complex(Exceeded.DEPTH) from None


def too_complex(bound: Exceeded) -> BodyTooComplex:
    """The refusal for a body past ``bound``, naming the bound and the figure."""
    if bound is Exceeded.VALUES:
        message = (
            f"The request body holds more than {MAX_JSON_VALUES} JSON values. Every value is work "
            "for the parser and the writer that records the request, and no request this gateway "
            "serves needs as many, so it is refused unread. A large result or document travels as "
            "text: a string is one value, however long."
        )
    else:
        message = (
            f"The request body nests deeper than {MAX_JSON_DEPTH} levels. Nothing on this path can "
            "walk a structure that deep — including the writer that records the request — so it "
            "is refused here rather than half-processed."
        )
    return BodyTooComplex(400, message, "INVALID_ARGUMENT")


def ensure_body_is_encodable(body: Any) -> None:
    """Refuse text that cannot be written as UTF-8, and numbers JSON cannot represent.

    JSON may escape half a surrogate pair (`"\\ud800"`), which parses into a Python string no UTF-8
    encoder accepts. Python's parser also accepts `Infinity`/`NaN`, which RFC 8259 does not have
    and Postgres will not store in a `json` column. Either would fail far downstream as a 500 with
    no audit row, so it is a `400` here. `ensure_ascii=False` is what makes the encoder object.
    """
    try:
        json.dumps(body, ensure_ascii=False, allow_nan=False).encode("utf-8")
    except UnicodeEncodeError as exc:
        raise GeminiHTTPError(
            400,
            "The request body contains a character that is not valid UTF-8 — an unpaired "
            f"surrogate ({exc.object[exc.start : exc.end]!r}). JSON can escape one, and nothing "
            "can send it.",
            "INVALID_ARGUMENT",
        ) from exc
    except ValueError as exc:
        raise GeminiHTTPError(
            400,
            "The request body contains a number JSON cannot represent — `Infinity`, `-Infinity` "
            "or `NaN`. Python's parser accepts them and the standard does not, so nothing "
            "downstream can store or forward one.",
            "INVALID_ARGUMENT",
        ) from exc
    except TypeError as exc:  # pragma: no cover - a body that parsed will serialise
        raise GeminiHTTPError(
            400, "The request body is not representable as JSON.", "INVALID_ARGUMENT"
        ) from exc
