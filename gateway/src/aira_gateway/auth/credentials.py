"""Extract the caller's credential from a request (FRD-101 FR-6).

Precedence: ``Authorization: Bearer <token>`` → ``x-goog-api-key`` header → ``?key=``.
The Gemini-style header/query forms let existing Gemini clients authenticate unchanged.

**``?key=`` on the Gemini surface only.** It is Google's convention, which Gemini clients follow,
and a key in a URL is written into the access log of every proxy it passes. The KIRA contract
authenticates with a bearer token and nothing else, so no client of it sends a key there: on that
surface the URL is not read, and a request that put its key there is told so by name.
"""

from __future__ import annotations

from fastapi import Request

from aira_gateway.api.kira import is_kira_path

_BEARER = "bearer "

#: What a KIRA caller who put the key in the URL is told.
KEY_IN_URL = (
    "This API does not read a credential from the URL. Send the key in the Authorization header "
    "(Bearer) or in x-goog-api-key: a key in a URL is written into the log of every proxy it "
    "passes, so treat this one as seen."
)


def extract_token(request: Request) -> str | None:
    """Return the presented credential (API key or JWT), or None if absent."""
    authorization = request.headers.get("authorization")
    if authorization and authorization[: len(_BEARER)].lower() == _BEARER:
        return authorization[len(_BEARER) :].strip() or None

    header_key = request.headers.get("x-goog-api-key")
    if header_key:
        return header_key.strip() or None

    query_key = request.query_params.get("key")
    if query_key and not is_kira_path(request.url.path):
        return query_key.strip() or None

    return None


def key_in_url_not_read(request: Request) -> bool:
    """Whether the request carries a key in its URL on a surface that does not read one there."""
    return bool(request.query_params.get("key")) and is_kira_path(request.url.path)
