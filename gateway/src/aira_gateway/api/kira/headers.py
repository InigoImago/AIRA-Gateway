"""The headers every KIRA response carries: the deprecation notice and the unmodelled fields."""

from __future__ import annotations

from typing import Any

from fastapi import Request

from aira_gateway.api.kira import schemas

#: The surface announces from day one that it is transitional (`ADR-0010` Option C). A sunset date
#: (`AIRA_KIRA_SUNSET`) is what turns "should we retire it" from an argument into a decision.
SUNSET_HEADERS = {
    "Deprecation": "true",
    "Link": '</docs/migration>; rel="deprecation"',
}

#: The header naming every field the caller sent that this surface does not model.
UNMODELLED_HEADER = "X-AIRA-Unmodelled-Fields"


def note_unmodelled(request: Request, *models: Any) -> None:
    """Record the fields a caller sent that this surface does not model (`FRD-124`).

    Accepted and **named** rather than refused: real clients send fields the predecessor tolerated,
    and refusing them made the surface unusable. They are named in the response header, the log
    line and — where payloads are stored — the stored body. A near-miss of a field this surface
    *does* model is still refused (`schemas.TolerantRequest`).
    """
    names = schemas.ignored_fields(*models)
    if names:
        request.state.unmodelled = names


def surface_headers(request: Request) -> dict[str, str]:
    """The headers for every response of this surface — one function, so every exit carries them."""
    configured = getattr(request.app.state.settings, "kira_sunset", "")
    unmodelled = getattr(request.state, "unmodelled", ())
    return {
        **SUNSET_HEADERS,
        **({"Sunset": configured} if configured else {}),
        **({UNMODELLED_HEADER: ", ".join(unmodelled)} if unmodelled else {}),
    }
