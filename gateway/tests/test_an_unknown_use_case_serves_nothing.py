"""A use case this gateway holds no record of serves nothing (`refuse_unless_live`, `FRD-204`).

Every control a use case carries lives on its read-model row — the models released to it, whether
its content is stored, its retirement — and each reads its permissive value when there is no row:
every model, every prompt stored, never retired. A request to a use case without one was therefore
served with all three, and the row can be missing for longer than a moment. Management accepted a
description of 20 000 characters into columns of 2000, so the use case's event failed and every
later event for it failed the same way: its unreleased models answered, its prompts were stored
with storage switched off, and retiring it changed nothing. A Keycloak group `/use-cases/<slug>`
resolves from the token alone, so a group naming a slug Management never had admitted its members
to a use case with no controls at all.

So absence refuses. What that costs is a use case created a moment ago, refused until its event
has arrived — the few seconds a new membership already takes to reach the grant cache.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from aira_gateway.app import create_app
from aira_gateway.auth.principal import Principal
from aira_gateway.config import GatewaySettings
from aira_gateway.consumer.apply import apply_event
from aira_gateway.db.models import ModelRead, RateLimitRead, RequestLog

SLUG = "not-yet-arrived"
TEXT = {"contents": [{"role": "user", "parts": [{"text": "hi"}]}]}
KIRA_TEXT = {"request": {"parts": [{"text": "hi"}]}, "model_id": 1004}

#: Every verb that serves a use case, on both surfaces, as `(path, body)`. A rule that one verb
#: skips is how `:streamGenerateContent` once applied no dispatch condition at all.
VERBS: dict[str, tuple[str, dict[str, Any]]] = {
    "generateContent": ("/v1beta/models/mock-1:generateContent", TEXT),
    "streamGenerateContent": ("/v1beta/models/mock-1:streamGenerateContent", TEXT),
    "embedContent": (
        "/v1beta/models/mock-1:embedContent",
        {"content": {"parts": [{"text": "hi"}]}},
    ),
    "kira chat": ("/kira/api/external/chat", KIRA_TEXT),
    "kira streaming-chat": ("/kira/api/external/streaming-chat", KIRA_TEXT),
}


class _TokenNamingTheUseCase:
    """A Keycloak token whose group names the slug: the convention that needs no AIRA table."""

    def validate(self, token: str) -> Principal:
        return Principal(subject="sub-1", method="oidc", username="alice", use_cases=(SLUG,))


def _app():  # noqa: ANN202
    app = create_app(GatewaySettings(auth_required=True, demo_mode=True, log_queue_size=0))
    app.state.oidc_validator = _TokenNamingTheUseCase()
    return app


async def _catalogue(app) -> None:  # noqa: ANN001
    async with app.state.db_sessionmaker() as session:
        session.add(
            ModelRead(
                model="mock-1", numeric_id=1004, capabilities=["generate", "embed"], approved=True
            )
        )
        await session.commit()


async def _arrives(app) -> None:  # noqa: ANN001
    """The use case's event, applied the way the consumer applies it."""
    async with app.state.db_sessionmaker() as session:
        await apply_event(
            session,
            "usecase.upserted",
            {"slug": SLUG, "name": SLUG, "allowed_models": ["mock-1"], "store_payloads": False},
        )


def _send(client: TestClient, verb: str) -> Any:
    path, body = VERBS[verb]
    return client.post(
        path, json=body, headers={"Authorization": "Bearer t", "X-AIRA-Use-Case": SLUG}
    )


async def _rows(app) -> list[Any]:  # noqa: ANN001
    async with app.state.db_sessionmaker() as session:
        return list((await session.execute(select(RequestLog))).scalars())


@pytest.mark.parametrize("verb", sorted(VERBS))
async def test_a_use_case_the_gateway_has_never_heard_of_is_refused(verb: str) -> None:
    app = _app()
    with TestClient(app) as client:
        await _catalogue(app)
        response = _send(client, verb)

    assert response.status_code == 403, response.text
    # By name and with what to do about it: the credential and the group are real, so the caller
    # needs to know the use case has not reached this gateway, not that something was "denied".
    assert "not known to this gateway" in response.text
    assert SLUG in response.text


@pytest.mark.parametrize("verb", sorted(VERBS))
async def test_the_same_request_serves_once_the_use_case_has_arrived(verb: str) -> None:
    """The other half of the claim: the refusal is about the missing row and nothing else, so the
    request it refused is served the moment the row is there — read per request, not cached."""
    app = _app()
    with TestClient(app) as client:
        await _catalogue(app)
        assert _send(client, verb).status_code == 403
        await _arrives(app)
        response = _send(client, verb)

    assert response.status_code == 200, response.text


async def test_the_refusal_comes_before_anything_is_spent() -> None:
    """**Ordering is the property**, as for a retired use case: a limit of one request would let
    the first call through, and refuse the second with a 429, if the check ran after the limiter."""
    app = _app()
    with TestClient(app) as client:
        await _catalogue(app)
        async with app.state.db_sessionmaker() as session:
            session.add(
                RateLimitRead(
                    id=1, use_case=SLUG, scope="use_case", subject="", limit_rpm=1, enabled=True
                )
            )
            await session.commit()
        first, second = _send(client, "generateContent"), _send(client, "generateContent")

    assert (first.status_code, second.status_code) == (403, 403), second.text
    assert "not known to this gateway" in second.text


async def test_the_refusal_is_recorded_and_its_prompt_is_not() -> None:
    """A refusal that reached attribution leaves a row (`FRD-122`) — under the use case it named,
    with the outcome a retired use case gets rather than `blocked_by_pipeline`, which the
    blocked-prompt rule counts. Its prompt is not stored: nothing has said this use case may."""
    app = _app()
    with TestClient(app) as client:
        await _catalogue(app)
        _send(client, "generateContent")
        rows = await _rows(app)

    assert [(row.use_case, row.status, row.outcome) for row in rows] == [
        (SLUG, 403, "invalid_request")
    ]
    assert rows[0].request_payload is None
    assert (rows[0].total_tokens or 0) == 0


async def test_a_dry_run_for_it_is_refused_too() -> None:
    """A dry run calls real models on the use case's behalf, so it meets the same rule."""
    app = _app()
    with TestClient(app) as client:
        await _catalogue(app)
        response = client.post(
            "/v1beta/pipeline:dryRun",
            json={"use_case": SLUG, "user": "hi", "pipeline": {}},
            headers={"Authorization": "Bearer t"},
        )

    assert response.status_code == 403, response.text
    assert "not known to this gateway" in response.text


async def test_a_request_naming_no_use_case_is_unaffected() -> None:
    """The rule reads the request's attribution. Unattributed traffic — the demo, a break-glass
    key — names no use case to be unknown, and must not be caught by a rule about one."""
    app = create_app(GatewaySettings(auth_required=False, demo_mode=True, log_queue_size=0))
    with TestClient(app) as client:
        await _catalogue(app)
        response = client.post("/v1beta/models/mock-1:generateContent", json=TEXT)

    assert response.status_code == 200, response.text
