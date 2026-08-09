"""Can this model actually be reached (`FRD-506`)?

The question the console could not answer, asked from the running system: *"wie kann ich neue
Modelle definieren von dem Provider, wenn ich keinen key habe, oder einen einfachen Test
durchführen ob es überhaupt ansprechbar wäre?"*

Both halves matter and they are different facts. A catalog entry is a **declaration** — it needs no
credential and proves nothing about reachability. Without a key no adapter is registered, so the
model sits in the catalog looking healthy while every request for it returns `model_not_found`,
which reads to the caller as a typo rather than as a missing credential.

Three answers, never collapsed into one: **declared**, **served**, **reachable**.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from aira_gateway.app import create_app
from aira_gateway.auth.dependencies import require_principal
from aira_gateway.auth.principal import Principal
from aira_gateway.config import GatewaySettings
from aira_gateway.db.models import ModelRead

IT_SECURITY = Principal(subject="sec", method="oidc", roles=("it-security",))
GLOBAL_ADMIN = Principal(subject="root", method="oidc", roles=("global-admin",))
IT_STEUERUNG = Principal(subject="gov", method="oidc", roles=("it-steuerung",))
UC_ADMIN = Principal(subject="boss", method="oidc", roles=("use-case-admin",), use_cases=("uc-a",))


class _Reachable:
    async def ping(self) -> str:
        return "3 models listed"


class _Unreachable:
    async def ping(self) -> str:
        raise ConnectionError("https://api.example/v1?key=super-secret-value")


class _Silent:
    """An adapter with nothing cheap to ask."""


def _client(principal: Principal, provider: Any = None) -> TestClient:
    app = create_app(GatewaySettings(auth_required=False))
    app.dependency_overrides[require_principal] = lambda: principal
    if provider is not None:
        app.state.providers.provider_for = lambda _model: provider  # type: ignore[method-assign]
    else:
        app.state.providers.provider_for = lambda _model: None  # type: ignore[method-assign]
    return app


async def _declare(app, model: str = "gemini-2.0-flash") -> None:
    async with app.state.db_sessionmaker() as session:
        session.add(ModelRead(model=model, capabilities=["generate"], publisher="google"))
        await session.commit()


@pytest.mark.anyio
async def test_a_declared_model_with_no_provider_says_it_is_not_served() -> None:
    """**The case a missing credential produces.** An adapter is registered only when its
    credential is configured, so "declared but not served" is almost always "nobody gave this
    installation a key for it" — and that sentence is what the reader needs, not a green tick."""
    app = _client(IT_SECURITY)
    with TestClient(app) as client:
        await _declare(app)
        body = client.get("/v1beta/models/gemini-2.0-flash:check").json()

    assert body["declared"] is True
    assert body["served"] is False
    assert body["reachable"] is None, "nothing was contacted, so this is not False"
    assert "credential" in body["detail"]


@pytest.mark.anyio
async def test_a_served_model_that_answers_is_reachable() -> None:
    app = _client(IT_SECURITY, _Reachable())
    with TestClient(app) as client:
        await _declare(app)
        body = client.get("/v1beta/models/gemini-2.0-flash:check").json()

    assert body == {
        "model": "gemini-2.0-flash",
        "declared": True,
        "served": True,
        "reachable": True,
        "detail": "3 models listed",
    }


@pytest.mark.anyio
async def test_an_upstream_that_fails_never_repeats_its_error_text() -> None:
    """A provider's error can carry the URL it was called with, and that URL can carry the key.
    The **type** is diagnostic enough; the message is somebody else's to log."""
    app = _client(IT_SECURITY, _Unreachable())
    with TestClient(app) as client:
        await _declare(app)
        response = client.get("/v1beta/models/gemini-2.0-flash:check")

    body = response.json()
    assert body["served"] is True
    assert body["reachable"] is False
    assert "ConnectionError" in body["detail"]
    assert "super-secret-value" not in response.text


@pytest.mark.anyio
async def test_an_adapter_with_nothing_cheap_to_ask_is_not_reported_green() -> None:
    """`FRD-117`'s rule, one endpoint over: "we did not look" and "it is fine" are different
    answers, and only one of them is safe to act on."""
    app = _client(IT_SECURITY, _Silent())
    with TestClient(app) as client:
        await _declare(app)
        body = client.get("/v1beta/models/gemini-2.0-flash:check").json()

    assert body["reachable"] is None
    assert "not contacted" in body["detail"]


@pytest.mark.anyio
async def test_an_undeclared_model_is_reported_as_undeclared_rather_than_missing() -> None:
    """Serving an undeclared model is legitimate — it gets the baseline (`FRD-114` FR-7). What the
    check must not do is imply somebody has said what it can do."""
    app = _client(IT_SECURITY, _Reachable())
    with TestClient(app) as client:
        body = client.get("/v1beta/models/never-declared:check").json()

    assert body["declared"] is False
    assert body["served"] is True


@pytest.mark.parametrize(
    ("principal", "expected"),
    [(GLOBAL_ADMIN, 200), (IT_SECURITY, 200), (IT_STEUERUNG, 403), (UC_ADMIN, 403)],
    ids=["global-admin", "it-security", "it-steuerung", "use-case-admin"],
)
def test_who_may_ask(principal: Principal, expected: int) -> None:
    """It describes the **installation**, not anybody's traffic: the people who need it are the
    ones who declare models and the ones who investigate why a use case cannot reach one."""
    app = _client(principal, _Reachable())
    with TestClient(app) as client:
        response = client.get("/v1beta/models/gemini-2.0-flash:check")

    assert response.status_code == expected
    if expected == 403:
        assert "IT Security" in response.json()["error"]["message"]
