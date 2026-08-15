"""One human, one allowance — whichever credential and whichever surface (`ADR-0019`).

An API key's subject **is** its owner's username; an OIDC token's subject is the directory's user
id. Every per-head budget and every per-head rate limit was keyed on that subject, so one person
holding both got **two** allowances: a limit of ten meant twenty, and the console had to warn about
it on two screens. The owner asked for one pot, and `FRD-606` had already made it possible — the
name is recorded beside the subject, and both credentials carry it.

`aira_gateway.scopes.person` is that rule, in one place. This file is the matrix the owner asked
for around it: **{Gemini, KIRA} × {API key, bearer}**, four ways in, and consumption landing in one
place from all of them.

Written as one parametrised journey rather than four tests, because the property is a *comparison*:
what matters is not that each combination is counted, it is that they are counted **together**. A
per-combination test would pass just as happily against the defect, which is exactly how the defect
survived this long.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from aira_gateway.app import create_app
from aira_gateway.auth.principal import Principal
from aira_gateway.config import GatewaySettings
from aira_gateway.db.models import BudgetRead, BudgetUsage, ModelRead, RequestLog

GEMINI = "/v1beta/models/mock-1:generateContent"
KIRA = "/kira/api/external/chat"

#: The two alphabets, for one person called `erika`.
#:
#: The API key's subject already *is* the username (`FRD-604`); the token's is a directory id that
#: looks nothing like it. That difference is the whole defect, so the fixture keeps it rather than
#: making the two look alike and proving nothing.
KEYCLOAK_SUBJECT = "f81d4fae-7dec-11d0-a765-00a0c91e6bf6"
USERNAME = "erika"


def _slug() -> str:
    """A use case of this test's own.

    The database is per-app and in memory, but the **budget counter is not**: `ADR-0008` keeps it
    in a shared store, and where one is reachable — a developer's machine, this sandbox — a key
    written by one run is still there for the next. A fixed slug therefore made the first assertion
    depend on how many times the file had been run, which is the kind of flake that gets a real
    finding dismissed as "the suite is flaky".
    """
    return f"uc-{uuid.uuid4().hex[:8]}"


class _Erika:
    """Stand-in OIDC validator: every token is Erika, signed in."""

    def __init__(self, use_case: str) -> None:
        self._use_case = use_case

    def validate(self, token: str) -> Principal:
        return Principal(
            subject=KEYCLOAK_SUBJECT,
            method="oidc",
            username=USERNAME,
            credential="console",
            use_cases=(self._use_case,),
        )


def _app():  # noqa: ANN202
    # `log_queue_size=0` writes the audit row on the request path, which the assertions on
    # `request_logs` need — `FRD-405` moved that write off it, so a row is otherwise merely queued
    # when the response returns.
    return create_app(GatewaySettings(auth_required=True, demo_mode=True, log_queue_size=0))


async def _use_case(app, slug: str) -> None:  # noqa: ANN001
    from aira_gateway.db.models import UseCaseRead

    async with app.state.db_sessionmaker() as session:
        session.add(UseCaseRead(slug=slug, name=slug, allowed_models=["mock-1"]))
        session.add(ModelRead(model="mock-1", numeric_id=1004, capabilities=["generate"]))
        await session.commit()


async def _budget(app, slug: str, **fields: Any) -> None:  # noqa: ANN001
    defaults: dict[str, Any] = {
        "id": 1,
        "use_case": slug,
        "scope": "each_member",
        "subject": "",
        "period": "month",
        "enabled": True,
    }
    async with app.state.db_sessionmaker() as session:
        session.add(BudgetRead(**{**defaults, **fields}))
        await session.commit()


async def _key_for(app, slug: str, subject: str = USERNAME) -> str:  # noqa: ANN001
    """A key bound to ``slug`` and owned by ``subject`` — the second alphabet."""
    from aira_gateway.auth.keys import generate_api_key
    from aira_gateway.db.models import ApiKey

    token, prefix, key_hash = generate_api_key()
    async with app.state.db_sessionmaker() as session:
        session.add(
            ApiKey(
                prefix=prefix,
                key_hash=key_hash,
                subject=subject,
                use_case=slug,
                is_active=True,
            )
        )
        await session.commit()
    return token


def _call(client: TestClient, surface: str, headers: dict[str, str]) -> Any:
    """One request on one surface, as the same person."""
    if surface == "gemini":
        return client.post(
            GEMINI, json={"contents": [{"parts": [{"text": "hi"}]}]}, headers=headers
        )
    return client.post(
        KIRA,
        json={"request": {"parts": [{"text": "hi"}]}, "model_id": 1004},
        headers=headers,
    )


async def _usage_keys(app) -> dict[str, int]:  # noqa: ANN001
    async with app.state.db_sessionmaker() as session:
        rows = list((await session.execute(select(BudgetUsage))).scalars())
    return {row.scope_key: row.requests for row in rows}


# ---- the matrix ----------------------------------------------------------------------------


@pytest.mark.parametrize("surfaces", [("gemini", "kira"), ("kira", "gemini")])
async def test_a_key_and_a_sign_in_spend_one_allowance(surfaces: tuple[str, str]) -> None:
    """The property, stated as a comparison: **four calls, one counter.**

    Two surfaces × two credentials, in both orders — because a per-combination assertion passes
    against the defect. What it must not be is four calls under two keys, one named for a person
    and one for a directory id that nobody reading a budget would recognise as the same human.
    """
    slug = _slug()
    app = _app()
    with TestClient(app) as client:
        await _use_case(app, slug)
        await _budget(app, slug, limit_requests=100)
        app.state.oidc_validator = _Erika(slug)
        token = await _key_for(app, slug)

        for surface in surfaces:
            assert _call(client, surface, {"x-goog-api-key": token}).status_code == 200, surface
            assert (
                _call(
                    client,
                    surface,
                    {"authorization": "Bearer jwt", "x-aira-use-case": slug},
                ).status_code
                == 200
            ), surface

        keys = await _usage_keys(app)

    assert keys == {f"member:{slug}:{USERNAME}": 4}, (
        "one person's four calls did not land in one allowance"
    )
    assert f"member:{slug}:{KEYCLOAK_SUBJECT}" not in keys


async def test_the_audit_trail_still_says_which_credential_it_was() -> None:
    """The pot is shared; the **record** is not.

    Merging the two allowances must not merge the two facts: `FRD-604` answers "who is accountable
    for this credential" from the row, and an investigation asking *which key* leaked has to be
    able to tell the browser traffic from the key's. So the row keeps the subject and the
    credential it always kept, and only the **counter** is keyed by person.
    """
    slug = _slug()
    app = _app()
    with TestClient(app) as client:
        await _use_case(app, slug)
        app.state.oidc_validator = _Erika(slug)
        token = await _key_for(app, slug)

        _call(client, "gemini", {"x-goog-api-key": token})
        _call(client, "gemini", {"authorization": "Bearer jwt", "x-aira-use-case": slug})

        async with app.state.db_sessionmaker() as session:
            rows = list((await session.execute(select(RequestLog))).scalars())

    by_method = {row.auth_method: row for row in rows}
    assert set(by_method) == {"api_key", "oidc"}
    assert by_method["oidc"].subject == KEYCLOAK_SUBJECT
    assert by_method["api_key"].subject == USERNAME
    # …and both name the same person, which is what makes the shared counter legible afterwards.
    assert {row.username for row in rows} == {USERNAME}


async def test_a_request_budget_is_shared_across_the_two_credentials() -> None:
    """Enforcement, not just accounting. A limit of one is spent by the key and **refuses the
    sign-in** — the behaviour the console used to have to warn about, in reverse."""
    slug = _slug()
    app = _app()
    with TestClient(app) as client:
        await _use_case(app, slug)
        await _budget(app, slug, limit_requests=1)
        app.state.oidc_validator = _Erika(slug)
        token = await _key_for(app, slug)

        first = _call(client, "gemini", {"x-goog-api-key": token})
        second = _call(client, "kira", {"authorization": "Bearer jwt", "x-aira-use-case": slug})

    assert first.status_code == 200, first.text
    assert second.status_code == 429, second.text


async def test_a_rate_limit_is_shared_across_the_two_credentials() -> None:
    """The same for how fast, and on the other surface first, so neither is the special case."""
    from aira_gateway.db.models import RateLimitRead

    slug = _slug()
    app = _app()
    with TestClient(app) as client:
        await _use_case(app, slug)
        async with app.state.db_sessionmaker() as session:
            session.add(
                RateLimitRead(id=1, use_case=slug, scope="each_member", limit_rpm=1, enabled=True)
            )
            await session.commit()
        app.state.oidc_validator = _Erika(slug)
        token = await _key_for(app, slug)

        first = _call(client, "kira", {"authorization": "Bearer jwt", "x-aira-use-case": slug})
        second = _call(client, "gemini", {"x-goog-api-key": token})

    assert first.status_code == 200, first.text
    assert second.status_code == 429, second.text


async def test_two_different_people_still_have_two_allowances() -> None:
    """The half that must not have been widened. "One pot per person" is not "one pot", and a
    fold that reached one name too far would make a per-head budget a shared one — the exact
    distinction `each_member` exists for (`FRD-402`)."""
    slug = _slug()
    app = _app()
    with TestClient(app) as client:
        await _use_case(app, slug)
        await _budget(app, slug, limit_requests=100)
        mine = await _key_for(app, slug, subject="erika")
        theirs = await _key_for(app, slug, subject="ahmed")

        _call(client, "gemini", {"x-goog-api-key": mine})
        _call(client, "gemini", {"x-goog-api-key": theirs})

        keys = await _usage_keys(app)

    assert keys == {f"member:{slug}:erika": 1, f"member:{slug}:ahmed": 1}


async def test_a_credential_that_names_nobody_keys_on_its_own_subject() -> None:
    """A service account, or a realm that maps no `preferred_username`.

    Falling back to *nothing* would put every nameless caller in one shared pot — the opposite
    failure and a much worse one, since it is the case a reader is least likely to check.
    """

    class _Nameless(_Erika):
        def validate(self, token: str) -> Principal:
            return Principal(
                subject="svc-1", method="oidc", username=None, use_cases=(self._use_case,)
            )

    slug = _slug()
    app = _app()
    with TestClient(app) as client:
        await _use_case(app, slug)
        await _budget(app, slug, limit_requests=100)
        app.state.oidc_validator = _Nameless(slug)

        _call(client, "gemini", {"authorization": "Bearer jwt", "x-aira-use-case": slug})

        keys = await _usage_keys(app)

    assert keys == {f"member:{slug}:svc-1": 1}
