"""Access by group, against the live stack (`FRD-209`).

The layer the hermetic suites cannot reach: a real Keycloak issuing a real token with real group
claims, a real Management writing a real grant, Kafka carrying it, and the running gateway
deciding on it. Both halves of the old disagreement are exercised in one place, which is the only
way to know they now give the same answer.

Nothing here asserts an answer's content. What is asserted is who reaches what.
"""

from __future__ import annotations

import asyncio
import uuid

import httpx
import pytest

from .conftest import GATEWAY_URL, MANAGEMENT_URL

pytestmark = pytest.mark.integration

#: A department group in the dev realm that is deliberately **not** named after a use case — the
#: point of the feature is that a grant names whatever the realm actually uses.
DEPARTMENT = "/abteilungen/kundendienst"
#: The suite's *member* service account is in it (see the realm file), and carries
#: `use-case-admin` — a role with **no oversight**, so anything it can see it can see because of
#: a grant. That is what makes "they can see it now" mean something.
MODEL = "qwen3:0.6b"


async def _create_use_case(token: str, slug: str) -> None:
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(
            f"{MANAGEMENT_URL}/api/v1/use-cases/",
            headers={"Authorization": f"Bearer {token}"},
            json={"slug": slug, "name": slug},
        )
    assert response.status_code == 201, response.text


async def _grant(token: str, slug: str, group: str, role: str = "user") -> httpx.Response:
    async with httpx.AsyncClient(timeout=30.0) as client:
        return await client.post(
            f"{MANAGEMENT_URL}/api/v1/use-cases/{slug}/groups/",
            headers={"Authorization": f"Bearer {token}"},
            json={"group_path": group, "role": role},
        )


async def _revoke(token: str, slug: str, group: str) -> httpx.Response:
    async with httpx.AsyncClient(timeout=30.0) as client:
        return await client.delete(
            f"{MANAGEMENT_URL}/api/v1/use-cases/{slug}/groups/revoke/",
            headers={"Authorization": f"Bearer {token}"},
            params={"group_path": group},
        )


async def _visible(token: str) -> set[str]:
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.get(
            f"{MANAGEMENT_URL}/api/v1/use-cases/",
            headers={"Authorization": f"Bearer {token}"},
            params={"page_size": 200},
        )
    response.raise_for_status()
    return {row["slug"] for row in response.json()["results"]}


async def _delete_use_case(token: str, slug: str) -> None:
    async with httpx.AsyncClient(timeout=30.0) as client:
        await client.delete(
            f"{MANAGEMENT_URL}/api/v1/use-cases/{slug}/",
            headers={"Authorization": f"Bearer {token}"},
        )


@pytest.fixture
async def slug(admin_token: str):
    """A use case nobody has been granted, cleaned up afterwards."""
    name = f"grp-{uuid.uuid4().hex[:8]}"
    await _create_use_case(admin_token, name)
    yield name
    await _delete_use_case(admin_token, name)


# ---- the control ---------------------------------------------------------------------------


async def test_a_use_case_nobody_was_granted_is_invisible_to_a_member_role(
    slug: str, member_token: str
) -> None:
    """The baseline the next test is measured against. Without it, "they can see it" proves
    nothing — they might have seen it all along."""
    assert slug not in await _visible(member_token)


# ---- granting a group ---------------------------------------------------------------------


async def test_a_granted_department_reaches_its_members_with_no_row_naming_them(
    slug: str, admin_token: str, member_token: str
) -> None:
    """The whole feature, end to end: a real token's real group claim, a real grant, and a real
    permission decision — none of which mentions this person."""
    assert (await _grant(admin_token, slug, DEPARTMENT)).status_code == 201

    assert slug in await _visible(member_token)


async def test_the_grant_is_listed_with_how_many_it_reaches(
    slug: str, admin_token: str, member_token: str
) -> None:
    await _grant(admin_token, slug, DEPARTMENT)
    # The count is of people Management has seen sign in; this makes sure `member_token`'s owner
    # has, so the figure is not zero for the wrong reason.
    await _visible(member_token)

    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.get(
            f"{MANAGEMENT_URL}/api/v1/use-cases/{slug}/groups/",
            headers={"Authorization": f"Bearer {admin_token}"},
        )

    grants = response.json()
    assert grants[0]["group_path"] == DEPARTMENT
    assert grants[0]["reaches"] >= 1


async def test_a_grant_on_a_group_nobody_is_in_reaches_nobody_and_says_so(
    slug: str, admin_token: str, member_token: str
) -> None:
    """A path matching nobody is silently inert. The console has to be able to show that."""
    await _grant(admin_token, slug, "/abteilungen/does-not-exist")

    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.get(
            f"{MANAGEMENT_URL}/api/v1/use-cases/{slug}/groups/",
            headers={"Authorization": f"Bearer {admin_token}"},
        )

    assert response.json()[0]["reaches"] == 0
    assert slug not in await _visible(member_token)


# ---- what the role means ------------------------------------------------------------------


async def test_a_user_grant_does_not_let_the_group_change_the_use_case(
    slug: str, admin_token: str, member_token: str
) -> None:
    await _grant(admin_token, slug, DEPARTMENT, "user")

    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.patch(
            f"{MANAGEMENT_URL}/api/v1/use-cases/{slug}/",
            headers={"Authorization": f"Bearer {member_token}"},
            json={"name": "renamed"},
        )

    assert response.status_code == 403


async def test_an_admin_grant_does(slug: str, admin_token: str, member_token: str) -> None:
    await _grant(admin_token, slug, DEPARTMENT, "admin")

    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.patch(
            f"{MANAGEMENT_URL}/api/v1/use-cases/{slug}/",
            headers={"Authorization": f"Bearer {member_token}"},
            json={"name": "renamed by a group"},
        )

    assert response.status_code == 200, response.text


async def test_the_console_and_the_server_agree_about_what_a_group_may_do(
    slug: str, admin_token: str, member_token: str
) -> None:
    """`FRD-206`'s agreement rule, now over a grant nobody's name appears in."""
    await _grant(admin_token, slug, DEPARTMENT, "admin")

    async with httpx.AsyncClient(timeout=30.0) as client:
        reported = (
            await client.get(
                f"{MANAGEMENT_URL}/api/v1/use-cases/{slug}/",
                headers={"Authorization": f"Bearer {member_token}"},
            )
        ).json()["permissions"]
        attempted = await client.patch(
            f"{MANAGEMENT_URL}/api/v1/use-cases/{slug}/",
            headers={"Authorization": f"Bearer {member_token}"},
            json={"name": "agreement"},
        )

    assert reported["can_admin"] is (attempted.status_code == 200)
    assert reported["is_member"] is True


# ---- revoking -----------------------------------------------------------------------------


async def test_revoking_the_group_takes_the_access_away(
    slug: str, admin_token: str, member_token: str
) -> None:
    await _grant(admin_token, slug, DEPARTMENT)
    assert slug in await _visible(member_token)

    assert (await _revoke(admin_token, slug, DEPARTMENT)).status_code == 204

    assert slug not in await _visible(member_token)


async def test_revoking_a_group_leaves_a_direct_grant_intact(
    slug: str, admin_token: str, member_token: str
) -> None:
    """`FRD-209` FR-5. Revoking one route must not silently close another."""
    await _grant(admin_token, slug, DEPARTMENT)
    # …and the same person, by name.
    async with httpx.AsyncClient(timeout=30.0) as client:
        me = (
            await client.get(
                f"{MANAGEMENT_URL}/api/v1/me",
                headers={"Authorization": f"Bearer {member_token}"},
            )
        ).json()["username"]
        await client.post(
            f"{MANAGEMENT_URL}/api/v1/use-cases/{slug}/members/",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={"username": me, "role": "user"},
        )

    await _revoke(admin_token, slug, DEPARTMENT)

    assert slug in await _visible(member_token)


# ---- the gateway --------------------------------------------------------------------------


async def test_the_gateway_lets_a_granted_group_call_as_the_use_case(
    slug: str, admin_token: str, member_token: str
) -> None:
    """The two planes give the same answer now — which is the disagreement this feature closed.

    Retried, because the grant travels over Kafka and the gateway caches it for a few seconds:
    being briefly behind is the deliberate cost, and a test that did not allow for it would be
    testing the clock.
    """
    await _grant(admin_token, slug, DEPARTMENT)

    async with httpx.AsyncClient(timeout=120.0) as client:
        for _ in range(20):
            response = await client.post(
                f"{GATEWAY_URL}/v1beta/models/{MODEL}:generateContent",
                headers={
                    "Authorization": f"Bearer {member_token}",
                    "X-AIRA-Use-Case": slug,
                },
                json={
                    "contents": [{"parts": [{"text": "Say OK"}]}],
                    "generationConfig": {"maxOutputTokens": 8},
                },
            )
            if response.status_code != 403:
                break
            await asyncio.sleep(1.0)

    assert response.status_code == 200, response.text


async def test_the_gateway_refuses_a_use_case_nobody_granted(slug: str, member_token: str) -> None:
    """The other half: without a grant, a valid token is still not a member."""
    async with httpx.AsyncClient(timeout=60.0) as client:
        response = await client.post(
            f"{GATEWAY_URL}/v1beta/models/{MODEL}:generateContent",
            headers={"Authorization": f"Bearer {member_token}", "X-AIRA-Use-Case": slug},
            json={"contents": [{"parts": [{"text": "Say OK"}]}]},
        )

    assert response.status_code == 403
    assert slug in response.text


async def test_the_old_convention_still_resolves_from_the_token_alone(
    slug: str, admin_token: str, member_token: str
) -> None:
    """`FRD-102`'s `/use-cases/<slug>` route is one way in, not the only one — and the dev realm
    and the demo depend on it.

    Asserted through Management rather than the gateway, because it is the *resolution* that is
    under test and the suite's account is not in any `/use-cases/…` group. Granting the department
    and reading back what the caller may see exercises both routes side by side.
    """
    await _grant(admin_token, slug, DEPARTMENT)

    visible = await _visible(member_token)

    assert slug in visible


# ---- the directory ------------------------------------------------------------------------


async def test_the_directory_offers_a_group_already_granted_somewhere(
    slug: str, admin_token: str
) -> None:
    """No admin client is configured in this stack, so this exercises the honest fallback: a real
    subset of what exists, labelled as such."""
    await _grant(admin_token, slug, DEPARTMENT)

    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.get(
            f"{MANAGEMENT_URL}/api/v1/directory/",
            headers={"Authorization": f"Bearer {admin_token}"},
            params={"q": "kundendienst"},
        )

    body = response.json()
    assert body["source"] in ("local", "keycloak")
    assert any(row["id"] == DEPARTMENT for row in body["results"])


async def test_the_directory_never_answers_a_one_letter_query_with_the_whole_realm(
    admin_token: str,
) -> None:
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.get(
            f"{MANAGEMENT_URL}/api/v1/directory/",
            headers={"Authorization": f"Bearer {admin_token}"},
            params={"q": "a"},
        )

    assert response.json()["results"] == []
