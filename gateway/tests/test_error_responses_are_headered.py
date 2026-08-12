"""The failures carry the headers too (`FRD-117` FR-4, `SecurityHeadersMiddleware`).

Both middlewares state their reach in their own docstrings — *"every response, including the
failures"*, *"the requests that most need correlating are exactly the ones that went wrong"* — and
neither reached the two responses that never get to a route:

    413, refused on its declared size, before any route ran
    500, produced by Starlette past the whole user middleware stack

Measured: no `x-content-type-options`, no `cache-control`, no `x-frame-options`, no
`referrer-policy` on either. A declaration that is silently inert, which is the shape this project
has now found in a nav marker that styled nothing and an info hint that showed nothing.

Two different causes, and they need two different fixes, which is why both are asserted here:

- the **413** came out bare because the body limit was mounted *outside* the header middleware.
  The ordering argument for that ("the ceiling must be first") was sound and the conclusion wrong:
  the ceiling wraps `receive`, and nothing above it calls `receive`.
- the **500** cannot be reached by any middleware at all. `ServerErrorMiddleware` wraps the entire
  user stack, so its response is written past every one of them, and the headers have to be
  applied by the handler itself.

`nosniff` is the one that carries weight on a JSON API: it is what stops a browser second-guessing
`application/json` on a response whose message can quote what the caller sent.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from aira_gateway.app import create_app
from aira_gateway.config import GatewaySettings
from aira_gateway.middleware import SecurityHeadersMiddleware

#: Read off the middleware rather than restated: a test that keeps its own copy of the list stops
#: noticing the moment somebody adds a fifth header.
EXPECTED = {
    name.decode("ascii"): value.decode("ascii") for name, value in SecurityHeadersMiddleware.HEADERS
}


def _app() -> object:
    return create_app(
        GatewaySettings(
            auth_required=False,
            demo_mode=True,
            environment="local",
            redis_url="",
            log_queue_size=0,
        )
    )


def _assert_headered(response: object, where: str) -> None:
    for name, value in EXPECTED.items():
        assert response.headers.get(name) == value, f"{where}: {name} missing"  # type: ignore[attr-defined]


def test_a_served_response_carries_them() -> None:
    """The case that already worked, kept so a regression in the ordering is visible as three
    failures rather than as two."""
    app = _app()
    with TestClient(app) as client:  # type: ignore[arg-type]
        _assert_headered(client.get("/healthz"), "200")


def test_a_body_over_the_ceiling_carries_them() -> None:
    """Refused in pure ASGI before any route: the response the ordering used to skip."""
    app = _app()
    with TestClient(app) as client:  # type: ignore[arg-type]
        response = client.post(
            "/v1beta/models/mock-1:generateContent",
            content=b"x" * 64,
            headers={"content-length": str(64 * 1024 * 1024)},
        )

    assert response.status_code == 413
    _assert_headered(response, "413")


def test_an_unhandled_error_carries_them() -> None:
    """Written past every middleware there is, so the handler applies them itself."""
    app = _app()

    @app.get("/boom")  # type: ignore[attr-defined]
    async def _boom() -> None:
        raise RuntimeError("kaboom")

    with TestClient(app, raise_server_exceptions=False) as client:  # type: ignore[arg-type]
        response = client.get("/boom")

    assert response.status_code == 500
    # The body still says nothing about the fault: the headers are the change, not the disclosure.
    assert "kaboom" not in response.text
    _assert_headered(response, "500")
