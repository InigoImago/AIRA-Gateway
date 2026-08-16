"""`/readyz` gives the verdict to everybody and the diagnosis to an operator (2026-08-15).

The module's own docstring already drew the line and the code asked a different question:

> the full body names the database host, the Kafka host, every configured upstream and which
> fallbacks are currently in force — a map of the deployment and its weak spot. A probe needs the
> status code; **an operator presents the credential they already have.**

`_may_see_detail` asked `principal is not None`. So a use-case-scoped API key — the weakest
credential this system issues, held by whichever team asked for one — was handed the topology, the
current degradation state and the names of every secret loaded (`secrets_state()`).

"An operator" has a definition here already, and it is two things: an **incident role**, and the
**unbound break-glass key** `ADR-0015` describes as the credential minted for the moment the
control plane is unavailable — which is exactly when somebody needs to read this.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from aira_gateway.app import create_app
from aira_gateway.auth.keys import generate_api_key
from aira_gateway.config import GatewaySettings
from aira_gateway.db.models import ApiKey

#: What only an operator may read. Each is a fact about the deployment rather than about traffic.
DIAGNOSIS = ("checks", "upstreams", "fallbacks", "secrets")
#: What every prober gets, and must keep getting: a Kubernetes probe carries no credential, and a
#: readiness endpoint that answers 401 reports every pod as unhealthy.
VERDICT = ("status", "degraded")


def _app():  # noqa: ANN201
    """A **deployment**, not a laptop. Locally the whole body is public on purpose — a laptop has
    no topology to protect, and an endpoint less useful in development is one people stop reading.
    """
    return create_app(
        GatewaySettings(
            environment="production",
            auth_required=True,
            postgres_password="a-real-secret",  # noqa: S106
            kafka_security_protocol="SASL_SSL",
            log_queue_size=0,
        )
    )


async def _key(app, *, subject: str, use_case: str | None) -> str:  # noqa: ANN001
    token, prefix, key_hash = generate_api_key()
    async with app.state.db_sessionmaker() as session:
        session.add(ApiKey(prefix=prefix, key_hash=key_hash, subject=subject, use_case=use_case))
        await session.commit()
    return token


def _body(client: TestClient, token: str | None = None) -> dict[str, Any]:
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    return client.get("/readyz", headers=headers).json()


def test_an_unauthenticated_prober_gets_the_verdict_and_nothing_else() -> None:
    with TestClient(_app()) as client:
        body = _body(client)

    assert set(body) == set(VERDICT)


async def test_a_use_case_key_is_not_an_operator() -> None:
    """The finding. A key issued by Management for one team is the weakest credential here, and it
    was reading the database host and the loaded secret names."""
    app = _app()
    with TestClient(app) as client:
        token = await _key(app, subject="ada", use_case="uc-a")
        body = _body(client, token)

    assert set(body) == set(VERDICT)
    for field in DIAGNOSIS:
        assert field not in body, f"a use-case key was shown {field}"


async def test_the_break_glass_key_is_an_operators() -> None:
    """`ADR-0015`: an **unbound** key is minted by an operator with database access, for the moment
    the control plane is unavailable. That moment is exactly when this body is worth reading, and
    the distinction from the one above is the binding rather than the credential type."""
    app = _app()
    with TestClient(app) as client:
        token = await _key(app, subject="operator", use_case=None)
        body = _body(client, token)

    for field in DIAGNOSIS:
        assert field in body, f"the break-glass key was not shown {field}"


@pytest.mark.parametrize(
    ("roles", "sees_detail"),
    [
        (("global-admin",), True),
        (("it-security",), True),
        # PRD §154 gives IT Steuerung every *figure* and no write anywhere. A deployment's topology
        # is not a figure, which is why this asks `may_act_on_incidents` and not `is_oversight` —
        # the same split `api/incidents.py` makes for the kill switch.
        (("it-steuerung",), False),
        ((), False),
    ],
    ids=["global-admin", "it-security", "it-steuerung", "no-role"],
)
def test_which_roles_are_shown_the_diagnosis(roles: tuple[str, ...], sees_detail: bool) -> None:
    from aira_gateway.auth.principal import Principal

    app = _app()
    principal = Principal(subject="who", method="oidc", roles=roles)

    async def _resolved(request: object) -> Principal:
        del request
        return principal

    # Patched **where health.py looked it up**, not on the router: `/readyz` is unauthenticated
    # by design, so it calls `resolve_principal` itself rather than depending on it — and a
    # `dependency_overrides` entry here would be an override nothing consults, which is a test
    # that passes by not testing.
    with (
        TestClient(app) as client,
        pytest.MonkeyPatch.context() as patch,
    ):
        patch.setattr("aira_gateway.routes.health.resolve_principal", _resolved)
        body = _body(client, token="unused")

    assert ("checks" in body) is sees_detail


def test_the_probe_still_answers_when_a_credential_is_broken() -> None:
    """A readiness endpoint that fails on a malformed header is a readiness endpoint that reports
    every pod unhealthy the first time somebody points a bad client at it."""
    with TestClient(_app()) as client:
        response = client.get("/readyz", headers={"Authorization": "Bearer not-a-real-token"})

    assert response.status_code in (200, 503)
    assert set(response.json()) == set(VERDICT)
