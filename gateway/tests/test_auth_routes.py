import pytest
from fastapi.testclient import TestClient

from aira_gateway.app import create_app
from aira_gateway.auth import keys
from aira_gateway.auth.principal import Principal
from aira_gateway.config import GatewaySettings

_BODY = {"contents": [{"role": "user", "parts": [{"text": "hi"}]}]}
_ROUTE = "/v1beta/models/mock-1:generateContent"


class _FakeOidcValidator:
    #: The caller belongs to a use case, because since 2026-08-11 an OIDC caller who belongs to
    #: none and names none is refused: serving them charges no budget, applies no use-case rate
    #: limit and consults no model release. This test is about **authentication**, so it hands over
    #: an identity that gets past attribution rather than one that proves nothing.
    def validate(self, token: str) -> Principal | None:
        return (
            Principal("oidc-user", "oidc", use_cases=("demo-uc",)) if token == "good-jwt" else None
        )


def test_open_mode_allows_unauthenticated(client) -> None:
    assert client.post(_ROUTE, json=_BODY).status_code == 200


def test_auth_required_rejects_missing_credential(authed_client) -> None:
    resp = authed_client.post(_ROUTE, json=_BODY)
    assert resp.status_code == 401
    assert resp.json()["error"]["status"] == "UNAUTHENTICATED"


def test_auth_with_goog_api_key_header(authed_client) -> None:
    resp = authed_client.post(_ROUTE, json=_BODY, headers={"x-goog-api-key": keys.DEMO_API_KEY})
    assert resp.status_code == 200


def test_auth_with_bearer_key(authed_client) -> None:
    resp = authed_client.post(
        _ROUTE, json=_BODY, headers={"authorization": f"Bearer {keys.DEMO_API_KEY}"}
    )
    assert resp.status_code == 200


def test_auth_with_query_key(authed_client) -> None:
    resp = authed_client.post(f"{_ROUTE}?key={keys.DEMO_API_KEY}", json=_BODY)
    assert resp.status_code == 200


def test_auth_rejects_bad_key(authed_client) -> None:
    resp = authed_client.post(_ROUTE, json=_BODY, headers={"x-goog-api-key": "aira_dead_beef"})
    assert resp.status_code == 401


def test_auth_rejects_jwt_when_oidc_not_configured(authed_client) -> None:
    # authed_client has OIDC disabled → a JWT bearer is rejected.
    resp = authed_client.post(
        _ROUTE, json=_BODY, headers={"authorization": "Bearer eyJhbGciOiJ.not.real"}
    )
    assert resp.status_code == 401


def test_oidc_bearer_accepted_when_validator_present() -> None:
    app = create_app(GatewaySettings(log_json=True, auth_required=True))
    app.state.oidc_validator = _FakeOidcValidator()
    with TestClient(app) as oidc_client:
        # The selector is explicit on this surface (`FRD-102`) — unlike KIRA, which picks a lone
        # membership for you. This test is about **authentication**, so it names the use case its
        # identity belongs to rather than relying on a fall-through that no longer exists.
        ok = oidc_client.post(
            _ROUTE,
            json=_BODY,
            headers={"authorization": "Bearer good-jwt", "x-aira-use-case": "demo-uc"},
        )
        bad = oidc_client.post(_ROUTE, json=_BODY, headers={"authorization": "Bearer wrong"})
    assert ok.status_code == 200
    assert bad.status_code == 401


def test_health_open_even_when_auth_required(authed_client) -> None:
    assert authed_client.get("/healthz").status_code == 200


# ---- the published demo key outside demo mode ---------------------------------------------------

#: A deployment, not a laptop: the settings `security.py` lets start outside `local`.
_DEPLOYED = {
    "environment": "production",
    "auth_required": True,
    "postgres_password": "a-real-secret",
    "kafka_security_protocol": "SASL_SSL",
    "log_queue_size": 0,
}


async def _seed_demo_row(app) -> None:  # noqa: ANN001
    """The row a demo run leaves behind in a database that later serves a deployment."""
    from aira_gateway.auth.service import ApiKeyService
    from aira_gateway.db.base import create_all

    await create_all(app.state.db_engine)
    async with app.state.db_sessionmaker() as session:
        await ApiKeyService(session, demo_mode=True).ensure_demo_key()


async def test_the_published_demo_key_opens_nothing_outside_demo_mode() -> None:
    """Its plaintext is in this repository, so it is a credential only while the deployment
    declares itself a demo (`ADR-0015`) — not for as long as a demo run's row survives."""
    app = create_app(GatewaySettings(**_DEPLOYED))
    with TestClient(app) as client:
        await _seed_demo_row(app)  # after startup, so only the check at verification stands
        listed = client.get("/v1beta/models", headers={"x-goog-api-key": keys.DEMO_API_KEY})
        readiness = client.get("/readyz", headers={"x-goog-api-key": keys.DEMO_API_KEY})

    assert listed.status_code == 401
    assert set(readiness.json()) == {"status", "degraded"}


async def test_a_deployment_that_is_no_longer_a_demo_revokes_the_demo_key() -> None:
    """The row is revoked at startup, terminally like any revoked key, and the start says so."""
    from sqlalchemy import select

    from aira_gateway.db.models import ApiKey

    app = create_app(GatewaySettings(**_DEPLOYED))
    await _seed_demo_row(app)
    with TestClient(app):
        async with app.state.db_sessionmaker() as session:
            row = (
                await session.execute(select(ApiKey).where(ApiKey.prefix == keys.DEMO_KEY_PREFIX))
            ).scalar_one()

    assert row.is_active is False
    assert row.revoked_at is not None


async def test_a_laptop_out_of_demo_mode_refuses_the_key_and_keeps_the_row() -> None:
    """Locally a demo is switched on and off against one database; the refusal at verification is
    the protection there, and revoking would leave the next demo run with a dead key."""
    from sqlalchemy import select

    from aira_gateway.db.models import ApiKey

    app = create_app(GatewaySettings(environment="local", auth_required=True, log_queue_size=0))
    await _seed_demo_row(app)
    with TestClient(app) as client:
        listed = client.get("/v1beta/models", headers={"x-goog-api-key": keys.DEMO_API_KEY})
        async with app.state.db_sessionmaker() as session:
            row = (
                await session.execute(select(ApiKey).where(ApiKey.prefix == keys.DEMO_KEY_PREFIX))
            ).scalar_one()

    assert listed.status_code == 401
    assert row.is_active is True


def test_in_demo_mode_the_demo_key_still_opens_the_listing(authed_client) -> None:
    listed = authed_client.get("/v1beta/models", headers={"x-goog-api-key": keys.DEMO_API_KEY})
    assert listed.status_code == 200


def _refuse_nul_like_postgres(app) -> None:  # noqa: ANN001
    """What Postgres does with a NUL in a text parameter, on the hermetic suite's SQLite."""
    import psycopg
    from sqlalchemy import event

    def _check(_conn, _cursor, _statement, parameters, _context, _many) -> None:  # noqa: ANN001
        values = parameters.values() if isinstance(parameters, dict) else (parameters or ())
        if any(isinstance(value, str) and "\x00" in value for value in values):
            raise psycopg.DataError("PostgreSQL text fields cannot contain NUL (0x00) bytes")

    event.listen(app.state.db_engine.sync_engine, "before_cursor_execute", _check)


@pytest.mark.parametrize("path", ["/v1beta/models", "/kira/api/external/models"])
def test_a_nul_in_a_query_key_is_a_401_not_a_server_error(path: str) -> None:
    app = create_app(GatewaySettings(auth_required=True, log_queue_size=0))
    _refuse_nul_like_postgres(app)
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get(f"{path}?key=aira_ab%00cd_secret")

    assert response.status_code == 401, response.text


# ---- a bearer no parser should be handed ----------------------------------------------------

_SURFACES = [
    ("/v1beta/models/mock-1:generateContent", {"contents": [{"parts": [{"text": "hi"}]}]}),
    ("/kira/api/external/chat", {"request": {"parts": [{"text": "hi"}]}, "model_id": 1}),
]


def _oidc_app(**overrides: object):  # noqa: ANN202
    values: dict[str, object] = {
        "auth_required": True,
        "oidc_enabled": True,
        "oidc_issuer": "https://kc.test/realms/aira",
        "redis_url": "",
        "log_queue_size": 0,
    }
    values.update(overrides)
    return create_app(GatewaySettings(**values))  # type: ignore[arg-type]


@pytest.mark.parametrize(("path", "body"), _SURFACES, ids=["gemini", "kira"])
def test_a_bearer_nested_past_the_json_parser_is_a_401_and_counted(path: str, body: dict) -> None:
    """`json` raises `RecursionError` on a deep enough nest, which PyJWT does not wrap: a 93 KB
    header was a 500 on both surfaces, before the failed-authentication bound was charged."""
    import base64

    nested = base64.urlsafe_b64encode(b"[" * 70_000).rstrip(b"=").decode()
    headers = {"authorization": f"Bearer {nested}.e30.c2ln"}
    with TestClient(_oidc_app(max_auth_failures_per_minute=1), raise_server_exceptions=False) as c:
        first = c.post(path, json=body, headers=headers)
        second = c.post(path, json=body, headers=headers)

    assert first.status_code == 401, first.text
    assert second.status_code == 429


def test_an_oversized_credential_is_refused_before_it_is_parsed() -> None:
    """Bounded before any parsing, and before a worker thread is spent on it."""
    from aira_common.oidc import MAX_TOKEN_LENGTH

    handed: list[str] = []

    class _Recording:
        def validate(self, token: str) -> Principal | None:
            handed.append(token)
            return None

    app = _oidc_app()
    app.state.oidc_validator = _Recording()
    with TestClient(app) as client:
        oversized = client.post(
            _ROUTE, json=_BODY, headers={"authorization": f"Bearer {'e' * (MAX_TOKEN_LENGTH + 1)}"}
        )
        ordinary = client.post(_ROUTE, json=_BODY, headers={"authorization": "Bearer e.e.e"})

    assert oversized.status_code == ordinary.status_code == 401
    assert handed == ["e.e.e"]
