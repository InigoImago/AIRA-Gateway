"""Extract the caller's credential from a request (FRD-101 FR-6).

Precedence: ``Authorization: Bearer <token>`` → ``x-goog-api-key`` header → ``?key=``.
The Gemini-style header/query forms let existing Gemini clients authenticate unchanged.
"""

from __future__ import annotations

from fastapi import Request

_BEARER = "bearer "


def extract_token(request: Request) -> str | None:
    """Return the presented credential (API key or JWT), or None if absent."""
    authorization = request.headers.get("authorization")
    if authorization and authorization[: len(_BEARER)].lower() == _BEARER:
        return authorization[len(_BEARER) :].strip() or None

    header_key = request.headers.get("x-goog-api-key")
    if header_key:
        return header_key.strip() or None

    query_key = request.query_params.get("key")
    if query_key:
        return query_key.strip() or None

    return None
