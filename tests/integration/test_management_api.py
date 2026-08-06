"""The Management API over HTTP, with tokens the realm actually issued (FRD-200/201/202/205).

Until now this API was exercised in two places, and neither of them was the API. Django's test
client runs the view functions in-process with a forced login: no server, no middleware chain, no
token, and no Keycloak. The browser suite drives the SPA, so it tests what the *SPA* happens to
call, in the shapes the SPA happens to send.

What sits between those two and had nothing: the contract. Whether a real bearer token is
accepted, whether the roles inside it decide what comes back, and whether a caller without
oversight is actually excluded — for an API whose RBAC decides who may administer which use case,
that is the part worth checking against the running service.

`conftest` provides two tokens on purpose. A visibility test with one caller can only show that
somebody sees something.
"""

from __future__ import annotations

import uuid

import httpx
import pytest

from .conftest import MANAGEMENT_URL

pytestmark = pytest.mark.integration


def _auth(token: str) -> dict[str, str]:
    return {"authorization": f"Bearer {token}"}


async def test_an_unauthenticated_call_is_refused(governance_token: str) -> None:
    """The same endpoint, with and without the credential — so the 200 below cannot be mistaken
    for an endpoint that is simply open."""
    async with httpx.AsyncClient(base_url=MANAGEMENT_URL, timeout=15.0) as client:
        without = await client.get("/api/v1/me")
        with_token = await client.get("/api/v1/me", headers=_auth(governance_token))

    assert without.status_code == 401
    assert with_token.status_code == 200


async def test_a_forged_token_is_refused() -> None:
    async with httpx.AsyncClient(base_url=MANAGEMENT_URL, timeout=15.0) as client:
        response = await client.get("/api/v1/me", headers=_auth("not.a.token"))
    assert response.status_code == 401


async def test_the_roles_in_the_token_are_the_roles_that_apply(
    governance_token: str, member_token: str
) -> None:
    """Keycloak is the source of truth (FRD-201). Management provisions the user on first sight
    and takes the roles from the token rather than from anything it stored earlier."""
    async with httpx.AsyncClient(base_url=MANAGEMENT_URL, timeout=15.0) as client:
        governance = (await client.get("/api/v1/me", headers=_auth(governance_token))).json()
        member = (await client.get("/api/v1/me", headers=_auth(member_token))).json()

    assert "it-steuerung" in governance["roles"]
    assert "it-steuerung" not in member["roles"]
    assert "use-case-admin" in member["roles"]
    assert governance["subject"] != member["subject"]


async def test_oversight_sees_a_use_case_it_did_not_create(
    governance_token: str, member_token: str
) -> None:
    """The whole point of the governance role, over the real API: the use case is created by
    somebody else, and the overseer — who is deliberately not a member — still sees it."""
    slug = f"itest-{uuid.uuid4().hex[:8]}"
    async with httpx.AsyncClient(base_url=MANAGEMENT_URL, timeout=15.0) as client:
        created = await client.post(
            "/api/v1/use-cases/",
            json={"slug": slug, "name": "Owned by the member"},
            headers=_auth(member_token),
        )
        assert created.status_code == 201, created.text

        seen_by_governance = await client.get(
            f"/api/v1/use-cases/{slug}/", headers=_auth(governance_token)
        )
        listed = await client.get("/api/v1/use-cases/", headers=_auth(governance_token))

    assert seen_by_governance.status_code == 200
    slugs = [item["slug"] for item in listed.json()]
    assert slug in slugs


async def test_a_caller_does_not_see_a_use_case_it_has_nothing_to_do_with(
    member_token: str,
) -> None:
    """The other half. Without it the test above would pass just as well against an API that
    shows everything to everyone.

    The use case is inserted straight into Management's database so that *nobody* is a member of
    it — created through the API it would belong to its creator, which is the case already
    covered.
    """
    from sqlalchemy import text

    from aira_gateway.db.base import build_engine

    from .conftest import MANAGEMENT_DB

    slug = f"itest-{uuid.uuid4().hex[:8]}"
    management = build_engine(MANAGEMENT_DB)
    try:
        async with management.begin() as connection:
            await connection.execute(
                text(
                    "INSERT INTO usecases_usecase"
                    " (slug, name, description, processing_notes, store_payloads,"
                    "  retention_days, created_at, updated_at)"
                    " VALUES (:slug, :slug, '', '', true, 7, now(), now())"
                ),
                {"slug": slug},
            )
    finally:
        await management.dispose()

    async with httpx.AsyncClient(base_url=MANAGEMENT_URL, timeout=15.0) as client:
        response = await client.get(f"/api/v1/use-cases/{slug}/", headers=_auth(member_token))

    assert response.status_code == 404, "a non-member could read a use case nobody granted them"


async def test_oversight_may_look_but_not_administer(
    governance_token: str, member_token: str
) -> None:
    """ADR-0007's boundary over the real API, and it cuts harder than expected: an oversight role
    cannot even create a use case.

    That is right, and worth pinning. Oversight exists to see across the installation; giving it
    the ability to act inside — or to mint a data-plane key — would make the separation from a
    use-case admin cosmetic. Read everything, change nothing.
    """
    slug = f"itest-{uuid.uuid4().hex[:8]}"
    async with httpx.AsyncClient(base_url=MANAGEMENT_URL, timeout=15.0) as client:
        owned_by_member = await client.post(
            "/api/v1/use-cases/",
            json={"slug": slug, "name": "Owned by the member"},
            headers=_auth(member_token),
        )
        assert owned_by_member.status_code == 201, owned_by_member.text

        readable = await client.get(f"/api/v1/use-cases/{slug}/", headers=_auth(governance_token))
        may_create = await client.post(
            "/api/v1/use-cases/",
            json={"slug": f"{slug}-2", "name": "By oversight"},
            headers=_auth(governance_token),
        )
        may_mint = await client.post(
            f"/api/v1/use-cases/{slug}/api-keys/",
            json={"label": "oversight key"},
            headers=_auth(governance_token),
        )

    assert readable.status_code == 200, "oversight must see the use case"
    assert may_create.status_code == 403, "oversight must not create use cases"
    assert may_mint.status_code == 403, "oversight must not mint a data-plane key"


async def test_a_refusal_carries_the_error_envelope(member_token: str) -> None:
    """FRD-200: one error shape, so the SPA can say what went wrong instead of "something did"."""
    async with httpx.AsyncClient(base_url=MANAGEMENT_URL, timeout=15.0) as client:
        response = await client.post(
            "/api/v1/use-cases/",
            json={"slug": "Not A Slug", "name": "x"},
            headers=_auth(member_token),
        )

    assert response.status_code == 400
    body = response.json()
    assert "error" in body, f"not the documented envelope: {body}"
    assert body["error"].get("message")


async def test_a_use_case_created_here_reaches_the_gateway(member_token: str, engine) -> None:
    """The API is one end of the distribution path; this is the other. Creating over HTTP has to
    produce the same outbox row the hermetic tests assert on, and the relay has to carry it."""
    import asyncio

    from sqlalchemy import text

    slug = f"itest-{uuid.uuid4().hex[:8]}"
    async with httpx.AsyncClient(base_url=MANAGEMENT_URL, timeout=15.0) as client:
        assert (
            await client.post(
                "/api/v1/use-cases/",
                json={"slug": slug, "name": "Distributed"},
                headers=_auth(member_token),
            )
        ).status_code == 201

    deadline = asyncio.get_running_loop().time() + 30.0
    while asyncio.get_running_loop().time() < deadline:
        async with engine.connect() as connection:
            row = (
                await connection.execute(
                    text("SELECT slug FROM use_cases WHERE slug = :slug"), {"slug": slug}
                )
            ).first()
        if row is not None:
            return
        await asyncio.sleep(0.5)
    raise AssertionError("the use case never reached the gateway's read-model")
