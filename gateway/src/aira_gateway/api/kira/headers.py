"""The headers every KIRA response carries: the deprecation notice and the unmodelled fields."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse

from aira_gateway.api.kira import errors, schemas

#: The surface announces from day one that it is transitional (`ADR-0010` Option C). A sunset date
#: (`AIRA_KIRA_SUNSET`) is what turns "should we retire it" from an argument into a decision.
SUNSET_HEADERS = {
    "Deprecation": "true",
    "Link": '</docs/migration>; rel="deprecation"',
}

#: The header naming the fields the caller sent that this surface does not model.
UNMODELLED_HEADER = "X-AIRA-Unmodelled-Fields"

#: A field name the header may repeat. The caller chose it, a header value is Latin-1 and ends at
#: a line break, and the header is written after the model was called — so a name outside this
#: shape is counted rather than echoed, where it would have turned a served answer into a 500.
_NAMEABLE = re.compile(r"[A-Za-z0-9_.$@-]{1,64}")

#: How many names, and how many characters of them, the header carries before it counts the rest:
#: a reverse proxy refuses a response whose headers outgrow its buffer (nginx: 4 or 8 KB). The
#: request keeps the full list for the log line and the stored body.
MAX_NAMED_FIELDS = 20
MAX_NAMED_CHARACTERS = 512


def note_unmodelled(request: Request, *models: Any, ignored: Sequence[str] = ()) -> None:
    """Record the fields a caller sent that this surface does not model (`FRD-124`).

    Accepted and **named** rather than refused: real clients send fields the predecessor tolerated,
    and refusing them made the surface unusable. They are named in the response header, the log
    line and — where payloads are stored — the stored body. A near-miss of a field this surface
    *does* model is still refused (`schemas.TolerantRequest`). ``ignored`` names values sent in a
    modelled field that this surface does not use (`schemas.ignored_values`).
    """
    names = (*schemas.ignored_fields(*models), *ignored)
    if names:
        request.state.unmodelled = names


def unmodelled_header(names: Sequence[str]) -> str:
    """The header's value: the nameable fields in the order sent, then ``+<n> more``."""
    named: list[str] = []
    length = 0
    for name in names:
        if len(named) == MAX_NAMED_FIELDS:
            break
        if not _NAMEABLE.fullmatch(name):
            continue
        if length + len(name) + 2 > MAX_NAMED_CHARACTERS:
            break
        named.append(name)
        length += len(name) + 2
    hidden = len(names) - len(named)
    return ", ".join([*named, *([f"+{hidden} more"] if hidden else [])])


def surface_headers(request: Request) -> dict[str, str]:
    """The headers for every response of this surface — one function, so every exit carries them."""
    configured = getattr(request.app.state.settings, "kira_sunset", "")
    unmodelled = getattr(request.state, "unmodelled", ())
    return {
        **SUNSET_HEADERS,
        **({"Sunset": configured} if configured else {}),
        **({UNMODELLED_HEADER: unmodelled_header(unmodelled)} if unmodelled else {}),
    }


def surface_refusal(
    request: Request,
    status: int,
    code: str,
    message: str,
    details: list[Any] | None = None,
    headers: Mapping[str, str] | None = None,
) -> JSONResponse:
    """A refusal on this surface answered outside a route — by the application's handlers or the
    body ceiling — carrying what every response of the surface carries (:func:`surface_headers`)
    and what the refusal itself brought (`Retry-After`, `Allow`)."""
    response = errors.kira_error_response(status, code, message, details)
    response.headers.update({**(headers or {}), **surface_headers(request)})
    return response
