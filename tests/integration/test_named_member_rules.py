"""A rule written about a person **by name**, against a real OIDC token.

The layer this belongs to, and the reason it was missing. An administrator writing a budget or a
rate limit about one person types a **name**. The two credentials answer "who is this" in two
different alphabets: an API key's subject *is* its owner's username (`FRD-604`), while an OIDC
token's is the directory's user id. So the rule bound API-key traffic and, for the same person's
browser or service-account traffic, bound **nothing at all** — visible in the console as an active
limit, absent in the gateway. `FRD-125`'s badge-wearing absent control, one identity system over.

Measured on the live stack before the repair: a request limit of one, four calls, four 200s.

The hermetic suites cannot see it, because the defect *is* the difference between two credentials
and a hermetic test mints neither. The browser suite cannot see it either — it authenticates the
gateway with an API key, which is exactly the half that always worked. Only a real Keycloak token
carries a `sub` that differs from the name somebody would type.
"""

from __future__ import annotations

import asyncio
import uuid

import httpx
import pytest

from .conftest import GATEWAY_URL, MANAGEMENT_URL

pytestmark = pytest.mark.integration

DEPARTMENT = "/abteilungen/kundendienst"
#: The *name* the suite's member account is known by — what an administrator would type. Its `sub`
#: is a directory uuid and appears nowhere in this file, which is the point.
MEMBER_USERNAME = "service-account-aira-integration-tests-member"
MODEL = "mock-1"


async def _mgmt(token: str) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        timeout=30.0,
        base_url=MANAGEMENT_URL,
        headers={"Authorization": f"Bearer {token}"},
    )


async def _reachable_use_case(admin_token: str, slug: str) -> None:
    """A use case the member account reaches **only** through a group grant."""
    async with await _mgmt(admin_token) as client:
        created = await client.post("/api/v1/use-cases/", json={"slug": slug, "name": slug})
        assert created.status_code == 201, created.text
        granted = await client.post(
            f"/api/v1/use-cases/{slug}/groups/",
            json={"group_path": DEPARTMENT, "role": "user"},
        )
        assert granted.status_code == 201, granted.text
        catalog = (await client.get("/api/v1/models/")).json()
        rows = catalog["results"] if isinstance(catalog, dict) else catalog
        approved = [m["name"] for m in rows if m.get("approved") is not False]
        released = await client.patch(
            f"/api/v1/use-cases/{slug}/", json={"allowed_models": approved}
        )
        assert released.status_code == 200, released.text


async def _delete(admin_token: str, slug: str) -> None:
    async with await _mgmt(admin_token) as client:
        await client.delete(f"/api/v1/use-cases/{slug}/")


async def _ask(token: str, slug: str, text: str) -> httpx.Response:
    """One request, retried while the grant and the configuration travel over Kafka.

    Retried on **403 only**: that is the shape of "the grant has not arrived yet". A 429 is the
    answer this test is looking for and must never be waited out.
    """
    async with httpx.AsyncClient(timeout=30.0) as client:
        for _ in range(25):
            response = await client.post(
                f"{GATEWAY_URL}/v1beta/models/{MODEL}:generateContent",
                headers={"Authorization": f"Bearer {token}", "X-AIRA-Use-Case": slug},
                json={"contents": [{"role": "user", "parts": [{"text": text}]}]},
            )
            if response.status_code != 403:
                return response
            await asyncio.sleep(1)
        return response


async def test_a_budget_named_after_a_person_binds_their_oidc_traffic(
    admin_token: str, member_token: str
) -> None:
    slug = f"named-budget-{uuid.uuid4().hex[:8]}"
    await _reachable_use_case(admin_token, slug)
    try:
        async with await _mgmt(admin_token) as client:
            created = await client.post(
                f"/api/v1/use-cases/{slug}/budgets/",
                json={
                    "scope": "member",
                    "subject": MEMBER_USERNAME,
                    "period": "day",
                    "limit_requests": 1,
                },
            )
            assert created.status_code == 201, created.text

        first = await _ask(member_token, slug, "first")
        second = await _ask(member_token, slug, "second")

        assert first.status_code == 200, first.text
        assert second.status_code == 429, (
            "the budget named this caller and did not find them: "
            f"{second.status_code} {second.text}"
        )
        assert "budget" in second.text.lower()
    finally:
        await _delete(admin_token, slug)


async def test_a_rate_limit_named_after_a_person_binds_their_oidc_traffic(
    admin_token: str, member_token: str
) -> None:
    """The same rule in the other service, which needed no repair of its own — it resolves each
    row against the caller on every request and only ever lacked the name. Asserted anyway,
    because *needed no change* is a claim.
    """
    slug = f"named-limit-{uuid.uuid4().hex[:8]}"
    await _reachable_use_case(admin_token, slug)
    try:
        async with await _mgmt(admin_token) as client:
            created = await client.post(
                f"/api/v1/use-cases/{slug}/rate-limits/",
                json={"scope": "member", "subject": MEMBER_USERNAME, "limit_rpm": 60, "burst": 1},
            )
            assert created.status_code == 201, created.text

        first = await _ask(member_token, slug, "first")
        second = await _ask(member_token, slug, "second")

        assert first.status_code == 200, first.text
        assert second.status_code == 429, (
            f"the limit named this caller and did not find them: {second.text}"
        )
        assert second.headers.get("retry-after"), "a refusal must say when to come back"
    finally:
        await _delete(admin_token, slug)


async def test_the_rule_still_binds_nobody_else(admin_token: str) -> None:
    """The half that must not have been widened: matching a *name* is not matching anyone.

    The admin account reaches the same use case through its own group, and the rule is about
    somebody else — so it must be served however many times it asks.
    """
    slug = f"named-other-{uuid.uuid4().hex[:8]}"
    await _reachable_use_case(admin_token, slug)
    try:
        async with await _mgmt(admin_token) as client:
            await client.post(
                f"/api/v1/use-cases/{slug}/groups/",
                json={"group_path": "/aira/global-admins", "role": "user"},
            )
            created = await client.post(
                f"/api/v1/use-cases/{slug}/budgets/",
                json={
                    "scope": "member",
                    "subject": MEMBER_USERNAME,
                    "period": "day",
                    "limit_requests": 1,
                },
            )
            assert created.status_code == 201, created.text

        for attempt in range(3):
            response = await _ask(admin_token, slug, f"other {attempt}")
            assert response.status_code == 200, (
                f"a budget about somebody else refused this caller: {response.text}"
            )
    finally:
        await _delete(admin_token, slug)
