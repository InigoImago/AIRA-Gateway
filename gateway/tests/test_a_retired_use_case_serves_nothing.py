"""A retired use case stops serving, on the plane where serving is decided (`FRD-607`).

The hole this closes is the one the feature would otherwise have left wide open, and it is not
obvious. Retiring a use case in Management deactivates its API keys and deletes the gateway's copy
of its members, group grants, budgets, limits, rules and pipeline — so *those* routes to it end.

But a Keycloak group of the form ``/use-cases/<slug>`` resolves **from the token alone**
(`auth/oidc.py`, the `FRD-102` convention). It touches no AIRA table, and retiring a use case does
not remove a Keycloak group. Every OIDC member of a retired use case could have gone on calling it
— with every one of its own controls deleted underneath them: no budget, no rate limit, no
pipeline, no release. Retiring a *compromised* use case has to stop the traffic, or it is a filing
action dressed as a control.

**The row survives, and the refusal says what it knows.** `_retire_usecase` used to delete it;
it keeps it as a tombstone now — positive knowledge that the use case was retired, which can only
exist after it was known — so the caller is told *retired*, and the retention promise holds. A
use case with no row at all is refused as well, as unknown
(`test_an_unknown_use_case_serves_nothing.py`).
"""

from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient

from aira_gateway.app import create_app
from aira_gateway.auth import keys
from aira_gateway.auth.principal import Principal
from aira_gateway.config import GatewaySettings
from aira_gateway.consumer.apply import apply_event
from aira_gateway.db.models import ModelRead, RateLimitRead, RequestLog, UseCaseRead

pytestmark = pytest.mark.anyio

SLUG = "kundenservice"
BODY = {"contents": [{"role": "user", "parts": [{"text": "hi"}]}]}


class _TokenNamingTheUseCase:
    """A Keycloak token whose group still names the slug — the exact case that must be refused."""

    def validate(self, token: str) -> Principal:
        return Principal(
            subject="sub-1",
            method="oidc",
            username="alice",
            use_cases=(SLUG,),
        )


def _app():  # noqa: ANN202
    app = create_app(GatewaySettings(auth_required=True, demo_mode=True, log_queue_size=0))
    app.state.oidc_validator = _TokenNamingTheUseCase()
    return app


async def _seed(app, *, retired: bool, **fields: Any) -> None:  # noqa: ANN001
    async with app.state.db_sessionmaker() as session:
        session.add(
            UseCaseRead(
                slug=SLUG,
                name=SLUG,
                allowed_models=["mock-1"],
                deleted_at=datetime.now(UTC) if retired else None,
                **fields,
            )
        )
        session.add(ModelRead(model="mock-1", capabilities=["generate"], approved=True))
        await session.commit()


def _post(client: TestClient) -> Any:
    return client.post(
        "/v1beta/models/mock-1:generateContent",
        json=BODY,
        headers={"Authorization": "Bearer t", "X-AIRA-Use-Case": SLUG},
    )


async def test_a_live_use_case_is_unaffected() -> None:
    """The control has to be about retirement and nothing else — half of what makes the test below
    mean anything is that the same request succeeds one field apart."""
    app = _app()
    with TestClient(app) as client:
        await _seed(app, retired=False)
        assert _post(client).status_code == 200


async def test_a_retired_use_case_is_refused_even_for_a_token_that_still_names_it() -> None:
    app = _app()
    with TestClient(app) as client:
        await _seed(app, retired=True)
        response = _post(client)

    assert response.status_code == 403, response.text
    # By name and with what to do about it. A caller whose credential is valid and whose group is
    # real needs to know the use case was retired, not that something was "denied".
    assert "retired" in response.text
    assert SLUG in response.text


async def test_the_refusal_comes_before_anything_is_spent() -> None:
    """**Ordering is the property.** A retired use case must not consume a rate-limit allowance,
    pay for a pipeline classifier call (`FRD-125b`), or reach a model on its way to being refused.

    Asserted through the rate limiter because it is the one control that *records* being consulted:
    a limit of one request would let the first call through if the check ran after it.
    """
    app = _app()
    with TestClient(app) as client:
        await _seed(app, retired=True)
        async with app.state.db_sessionmaker() as session:
            session.add(
                RateLimitRead(
                    id=1, use_case=SLUG, scope="use_case", subject="", limit_rpm=1, enabled=True
                )
            )
            await session.commit()

        # Twice. If the refusal ran after the limiter, the second call would be refused by the
        # limiter instead — a 429, and the allowance of a use case nobody may call would be spent.
        first, second = _post(client), _post(client)

    assert first.status_code == 403
    assert second.status_code == 403
    assert "retired" in second.text


async def test_the_refusal_is_not_a_model_call() -> None:
    """Nothing reached an upstream, so nothing is billed and no audit row claims otherwise."""
    app = _app()
    with TestClient(app) as client:
        await _seed(app, retired=True)
        _post(client)
        async with app.state.db_sessionmaker() as session:
            rows = (await session.execute(RequestLog.__table__.select())).fetchall()

    assert [row.status for row in rows if row.use_case == SLUG] in ([], [403])
    assert all((row.total_tokens or 0) == 0 for row in rows)


async def test_the_row_files_it_as_a_retired_use_case_not_a_pipeline_block() -> None:
    """The refusal comes before the pipeline, so `blocked_by_pipeline` was a false statement — and
    the outcome the blocked-prompt anomaly rule counts, so a retired use case's leftover traffic
    read as an injection campaign. The closed vocabulary has no word for retirement; the request
    names a use case that no longer serves, which is the caller's to correct."""
    app = _app()
    with TestClient(app) as client:
        await _seed(app, retired=True)
        assert _post(client).status_code == 403
        async with app.state.db_sessionmaker() as session:
            rows = (await session.execute(RequestLog.__table__.select())).fetchall()

    assert [(row.status, row.outcome) for row in rows if row.use_case == SLUG] == [
        (403, "invalid_request")
    ]


async def test_a_read_model_rebuilt_from_the_topics_still_refuses_it() -> None:
    """A rebuild applies what compaction kept: the retirement — the last event on the slug's key —
    and the use case's key and membership, which nothing ever deleted (`FRD-204` FR-6).

    Without a tombstone written from the event itself, every retired use case would serve again
    on a new stage or after a restore, to its keys and to its token members alike.
    """
    app = _app()
    full, prefix, key_hash = keys.generate_api_key()
    with TestClient(app) as client:
        async with app.state.db_sessionmaker() as session:
            session.add(ModelRead(model="mock-1", capabilities=["generate"], approved=True))
            await session.commit()
            await apply_event(session, "usecase.deleted", {"slug": SLUG, "name": SLUG})
            await apply_event(
                session,
                "api_key.created",
                {"prefix": prefix, "key_hash": key_hash, "subject": "alice", "use_case": SLUG},
            )
            await apply_event(
                session, "membership.upserted", {"slug": SLUG, "username": "alice", "role": "admin"}
            )

        by_token = _post(client)
        by_key = client.post(
            "/v1beta/models/mock-1:generateContent", json=BODY, headers={"x-goog-api-key": full}
        )

    assert by_token.status_code == 403, by_token.text
    assert "retired" in by_token.text
    assert by_key.status_code in (401, 403), by_key.text


async def test_a_dry_run_of_a_retired_use_case_is_refused_too() -> None:
    """A dry run calls real models on the use case's behalf, and asked only whether the caller was
    a member — which a token naming the slug still says. It meets the request's rule now."""
    app = _app()
    with TestClient(app) as client:
        await _seed(app, retired=True)
        response = client.post(
            "/v1beta/pipeline:dryRun",
            json={"use_case": SLUG, "user": "hi", "pipeline": {}},
            headers={"Authorization": "Bearer t"},
        )

    assert response.status_code == 403, response.text
    assert "retired" in response.text


async def test_a_request_naming_no_use_case_is_unaffected() -> None:
    """The check reads this request's attribution and nothing else. Unbound traffic — break-glass
    keys, the demo — has no use case to be retired, and must not be caught by a filter meant for
    one."""
    app = create_app(GatewaySettings(auth_required=False, demo_mode=True, log_queue_size=0))
    with TestClient(app) as client:
        await _seed(app, retired=True)
        response = client.post("/v1beta/models/mock-1:generateContent", json=BODY)

    assert response.status_code == 200, response.text
