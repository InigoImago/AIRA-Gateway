"""Every refusal on the KIRA surface answers in the KIRA envelope — including the ones the
routes never see.

`FRD-107`'s whole premise is that a client migrates by changing a URL, so the shape of an error
is part of the contract, not decoration: a predecessor's client switches on `code`. The routes
already honour that — they catch `KIRA_REFUSALS` and render their own envelope — and two classes
of refusal never reach a route at all:

    a request with no credential          the auth dependency raises, before the route body
    a body over the ceiling               pure ASGI, before any route matches

Both answered in **Google's** envelope, `{"error": {"code": …, "status": "UNAUTHENTICATED"}}`.
Found on 2026-08-12 by sending a KIRA request with no credential, in the first live walkthrough
this surface had received — the hermetic suite exercises the routes, and these two refusals are
precisely the ones that are not in a route.

The sharpest part is that the vocabulary was already there: `NOT_AUTHENTICATED` and
`INVALID_TOKEN` are declared in `kira/errors.py` and **nothing emitted either of them**, while the
real 401 went out in a foreign shape. A code defined and never raised is the same defect as an
enum member that is not a specification, seen from the other side.

The Gemini surface is asserted alongside on purpose: a fix that gave *both* surfaces the KIRA
envelope would satisfy every assertion about KIRA and quietly break the other contract.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from aira_gateway.app import create_app
from aira_gateway.config import GatewaySettings

KIRA = "/kira/api/external"
_BODY = {"request": {"parts": [{"text": "hallo"}]}, "model_id": 9001}


def _client() -> TestClient:
    # `auth_required` on, and **no** demo key: the point is what an unauthenticated caller sees.
    return TestClient(
        create_app(GatewaySettings(auth_required=True, environment="local", log_queue_size=0)),
        raise_server_exceptions=False,
    )


def test_an_unauthenticated_kira_request_answers_in_the_kira_envelope() -> None:
    with _client() as client:
        response = client.post(f"{KIRA}/chat", json=_BODY)

    assert response.status_code == 401
    body = response.json()
    # The predecessor's shape: a flat `code` and `message`, never Google's nested `error`.
    assert "error" not in body
    assert body["code"] == "NOT_AUTHENTICATED"
    assert body["message"]


def test_an_unauthenticated_gemini_request_still_answers_in_googles_envelope() -> None:
    """The other half of the same rule, and the reason this file asserts both: a surface's
    envelope is a contract with *its* clients, and satisfying one by breaking the other is a
    change that passes every test somebody thought to write about the surface they were fixing."""
    with _client() as client:
        response = client.post("/v1beta/models/mock-1:generateContent", json={"contents": []})

    assert response.status_code == 401
    body = response.json()
    assert body["error"]["status"] == "UNAUTHENTICATED"


def test_a_body_over_the_ceiling_answers_in_the_kira_envelope() -> None:
    """Refused in pure ASGI before any route matched, so no route could name it."""
    with _client() as client:
        response = client.post(
            f"{KIRA}/chat",
            content=b"x" * 64,
            headers={"content-length": str(64 * 1024 * 1024), "content-type": "application/json"},
        )

    assert response.status_code == 413
    body = response.json()
    assert "error" not in body
    assert body["code"] == "VALIDATION_ERROR"


def test_a_body_over_the_ceiling_on_the_gemini_surface_is_unchanged() -> None:
    with _client() as client:
        response = client.post(
            "/v1beta/models/mock-1:generateContent",
            content=b"x" * 64,
            headers={"content-length": str(64 * 1024 * 1024), "content-type": "application/json"},
        )

    assert response.status_code == 413
    assert response.json()["error"]["code"] == 413


def test_an_unmapped_status_is_named_as_an_internal_error_not_guessed() -> None:
    """A caller can act on "you are not authenticated"; they can act on nothing at all when the
    status is one this surface did not anticipate. Guessing a more specific code would tell them
    to fix something that is not theirs."""
    from aira_gateway.api.kira.errors import code_for_status

    assert code_for_status(401) == "NOT_AUTHENTICATED"
    assert code_for_status(429) == "EXTERNAL_KI_API_TOO_MANY_REQUEST"
    assert code_for_status(418) == "INTERNAL_SERVER_ERROR"


# == the third class: a route that raises without catching ======================================


def test_ki_usage_refuses_in_the_kira_envelope_rather_than_failing() -> None:
    """The defect this section was added for, and the reason the guard below walks every route.

    `/ki-usage` raises `KiraError` for a missing parameter, a backwards time range and — first —
    a caller without an oversight role. Three of the four routes wrap their body in
    `except KIRA_REFUSALS` and render the envelope there; this one did not, and there was no
    application-level handler, so **every** refusal it raises left as `500 internal_error` in
    Google's envelope. Measured against the running gateway on 2026-08-12: four expectable errors,
    four 500s.

    The worst of them is the role check. A permission refusal reported as a server error tells the
    reader the gateway is broken when the truth is that they lack a role — and in a console built
    for governance, "the system is broken" is the conclusion that spreads.
    """
    app = create_app(GatewaySettings(auth_required=False, environment="local", log_queue_size=0))
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get(f"{KIRA}/ki-usage")

    assert response.status_code == 403, response.text
    body = response.json()
    assert "error" not in body, "Google's envelope on the compatibility surface"
    # The sharpest of the four: a *permission* refusal that used to arrive as `500
    # internal_error`. The caller lacks a role and was told the server had failed.
    assert body["code"] == "ADMIN_PERMISSION_REQUIRED"
    assert body["message"]


def test_every_kira_route_renders_this_surfaces_envelope() -> None:
    """The structural half. Three classes of refusal have now been found on this surface — one
    raised before authentication, one before any route matched, and one raised *by* a route that
    did not catch it — and each was fixed for the routes that were known to be affected.

    So this asks the application instead: for every route mounted under the compatibility base,
    a `KiraError` must come back in the KIRA envelope. It cannot be satisfied by remembering, and
    a route added next year is covered the day it is written.
    """
    from gateway.tests.test_every_route_is_guarded import _routes

    from aira_gateway.api.kira.errors import KiraError

    app = create_app(GatewaySettings(auth_required=False, environment="local", log_queue_size=0))
    # Borrowed rather than rewritten: `include_router` keeps the router nested behind an
    # `_IncludedRouter`, so `app.routes` shows four documentation endpoints and two mounts. A
    # hand-rolled walk here returned an **empty** list and the guard passed by checking nothing —
    # which is why the assertion below exists and why it fired first.
    paths = sorted({route.path for route, _ in _routes() if route.path.startswith(KIRA)})
    assert paths, "no KIRA routes found — the guard would pass by walking nothing"

    handler = app.exception_handlers.get(KiraError)
    assert handler is not None, (
        "no application-level handler for KiraError, so every route is one forgotten `try` away "
        f"from answering 500 in Google's envelope. Routes that would be affected: {paths}"
    )
