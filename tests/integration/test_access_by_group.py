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
import stack_addresses

from .conftest import (
    GATEWAY_URL,
    MANAGEMENT_URL,
    MEMBER_CLIENT_ID,
    MEMBER_CLIENT_SECRET,
    REALM,
    _token,
)

pytestmark = pytest.mark.integration

#: A department group in the dev realm that is deliberately **not** named after a use case — the
#: point of the feature is that a grant names whatever the realm actually uses.
DEPARTMENT = "/abteilungen/kundendienst"
#: The suite's *member* service account is in it (see the realm file) and holds **no
#: organisation-wide role** (`ADR-0017`) — so anything it can see, it can see because of a grant.
#: That is what makes "they can see it now" mean something.
MODEL = "qwen3:0.6b"


async def _create_use_case(token: str, slug: str) -> None:
    """A use case that may call :data:`MODEL`, which is two steps rather than one.

    **Creating a use case does not let it call anything** (`FRD-308`, 2026-08-11): a release is
    empty until somebody makes one, and empty means none. That is deliberate, and it is what these
    tests forgot — every gateway call in this file was answered `400 … has no model released to
    it`, which is the release gate working and reads nothing like the access question being asked.

    Released here, in the fixture, because it is a *precondition* of this file rather than its
    subject: what is under test is who reaches a use case, not which models it may call. Stating it
    once means a new test cannot forget it, and the failure it prevents does not look like an
    access failure.
    """
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(
            f"{MANAGEMENT_URL}/api/v1/use-cases/",
            headers={"Authorization": f"Bearer {token}"},
            json={"slug": slug, "name": slug},
        )
        assert response.status_code == 201, response.text
        released = await client.patch(
            f"{MANAGEMENT_URL}/api/v1/use-cases/{slug}/",
            headers={"Authorization": f"Bearer {token}"},
            json={"allowed_models": [MODEL]},
        )
    assert released.status_code == 200, released.text


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

    # `200` with `revoked_keys`, not `204`: revoking a grant also revokes every key of this
    # use case whose owner no longer holds one (`FRD-613`), and a removal that silently
    # deactivated a credential would be a control whose effect the screen cannot state.
    revoked = await _revoke(admin_token, slug, DEPARTMENT)
    assert revoked.status_code == 200, revoked.text
    assert "revoked_keys" in revoked.json()

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


# == a group filled after the grant, and both kinds of credential =================================
#
# What the tests above establish is that a service account **already in** a department reaches a
# use case granted to it, with a bearer token. Two halves of the promise were untested, and they
# are the two an administrator actually performs:
#
#   1. **Somebody is added to the group afterwards.** AIRA never writes to the directory — who is
#      in a group stays the identity provider's answer — so a grant made today has to reach a
#      person added tomorrow, with nothing changing here. Every test above used a membership
#      written into the realm file, which cannot show that.
#   2. **That person issues an API key.** `is_member` counts a group grant deliberately
#      (`FRD-209`), so a member with no row naming them may mint a key — and the key is what a
#      client actually uses. Nothing asserted that the key then *works*.
#
# The realm is written to here, which nothing else in AIRA does. The group is removed again at the
# end of the test for exactly that reason: a suite that leaves grants behind in somebody's
# directory is doing the thing this system refuses to do.

KEYCLOAK_ADMIN = stack_addresses.url("keycloak")


async def _kc_admin_token() -> str:
    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.post(
            f"{KEYCLOAK_ADMIN}/realms/master/protocol/openid-connect/token",
            data={
                "client_id": "admin-cli",
                "username": "admin",
                "password": "admin",
                "grant_type": "password",
            },
        )
    assert response.status_code == 200, response.text
    return str(response.json()["access_token"])


async def _child_group(admin: str, parent: str, name: str) -> str:
    """Create (or find) `/<parent>/<name>` and return its id."""
    headers = {"Authorization": f"Bearer {admin}"}
    async with httpx.AsyncClient(timeout=30.0) as client:
        found = await client.get(
            f"{KEYCLOAK_ADMIN}/admin/realms/{REALM}/groups",
            headers=headers,
            params={"search": parent},
        )
        parent_id = found.json()[0]["id"]
        await client.post(
            f"{KEYCLOAK_ADMIN}/admin/realms/{REALM}/groups/{parent_id}/children",
            headers=headers,
            json={"name": name},
        )
        children = await client.get(
            f"{KEYCLOAK_ADMIN}/admin/realms/{REALM}/groups/{parent_id}/children", headers=headers
        )
    return str(next(g["id"] for g in children.json() if g["name"] == name))


async def _member_of(admin: str, group_id: str, username: str, *, join: bool) -> None:
    headers = {"Authorization": f"Bearer {admin}"}
    async with httpx.AsyncClient(timeout=30.0) as client:
        users = await client.get(
            f"{KEYCLOAK_ADMIN}/admin/realms/{REALM}/users",
            headers=headers,
            params={"username": username, "exact": "true"},
        )
        user_id = users.json()[0]["id"]
        url = f"{KEYCLOAK_ADMIN}/admin/realms/{REALM}/users/{user_id}/groups/{group_id}"
        response = await (
            client.put(url, headers=headers) if join else client.delete(url, headers=headers)
        )
    assert response.status_code in (204, 200), response.text


async def _delete_group(admin: str, group_id: str) -> None:
    async with httpx.AsyncClient(timeout=30.0) as client:
        await client.delete(
            f"{KEYCLOAK_ADMIN}/admin/realms/{REALM}/groups/{group_id}",
            headers={"Authorization": f"Bearer {admin}"},
        )


async def _issue_key(token: str, slug: str, owner: str) -> httpx.Response:
    async with httpx.AsyncClient(timeout=30.0) as client:
        return await client.post(
            f"{MANAGEMENT_URL}/api/v1/use-cases/{slug}/api-keys/",
            headers={"Authorization": f"Bearer {token}"},
            json={"label": "granted-by-group", "owner": owner},
        )


async def _call(credential: dict[str, str], slug: str) -> httpx.Response:
    """One ordinary generation, however the caller authenticates."""
    async with httpx.AsyncClient(timeout=120.0) as client:
        for _ in range(20):
            response = await client.post(
                f"{GATEWAY_URL}/uc/{slug}/v1beta/models/{MODEL}:generateContent",
                headers={**credential, "content-type": "application/json"},
                json={
                    "contents": [{"parts": [{"text": "Say OK"}]}],
                    "generationConfig": {"maxOutputTokens": 8},
                },
            )
            # A grant travels over Kafka and a key is distributed the same way; being briefly
            # behind is the deliberate cost (`FRD-204`), and a test that did not allow for it
            # would be testing the clock.
            if response.status_code not in (401, 403):
                return response
            await asyncio.sleep(1.0)
        return response


MEMBER_ACCOUNT = "service-account-aira-integration-tests-member"


async def test_a_person_added_to_a_granted_group_reaches_it_with_both_credentials(
    slug: str, admin_token: str
) -> None:
    """The whole chain an administrator actually performs, in order.

    Grant a department that reaches nobody, **then** put somebody in it, then call — with a bearer
    token and with an API key that person issued for themselves. Each step is asserted before the
    next, so a failure says which link broke rather than that "access does not work".
    """
    admin = await _kc_admin_token()
    name = f"vertrieb-{uuid.uuid4().hex[:6]}"
    group_id = await _child_group(admin, "abteilungen", name)
    group_path = f"/abteilungen/{name}"

    try:
        assert (await _grant(admin_token, slug, group_path)).status_code == 201

        # Nobody is in it yet, so the grant reaches nobody — and a token minted now carries no
        # such group. This is the "before" the rest of the test is measured against.
        before = await _token(MEMBER_CLIENT_ID, MEMBER_CLIENT_SECRET)
        assert slug not in await _visible(before)

        await _member_of(admin, group_id, MEMBER_ACCOUNT, join=True)

        # **A new token.** Group claims are baked in when a token is issued, so the one obtained
        # above is still correct about the past — the mirror of the hermetic rule that leaving a
        # group takes access away *on the next token*.
        after = await _token(MEMBER_CLIENT_ID, MEMBER_CLIENT_SECRET)
        assert slug in await _visible(after), "the grant did not reach a person added afterwards"

        served = await _call({"Authorization": f"Bearer {after}"}, slug)
        assert served.status_code == 200, served.text[:300]

        # The second credential: a member by group and by nothing else mints a key for themselves.
        issued = await _issue_key(after, slug, MEMBER_ACCOUNT)
        assert issued.status_code == 201, issued.text[:300]
        key = issued.json()["api_key"]

        with_key = await _call({"x-goog-api-key": key}, slug)
        assert with_key.status_code == 200, with_key.text[:300]
    finally:
        # The realm is left as it was found. AIRA never writes to a directory; this test does, and
        # a suite that leaves grants behind in one is doing what the system refuses to.
        await _member_of(admin, group_id, MEMBER_ACCOUNT, join=False)
        await _delete_group(admin, group_id)


async def test_taking_the_person_out_of_the_group_takes_both_credentials_with_it(
    slug: str, admin_token: str
) -> None:
    """Removal, on both halves — and they are **not** symmetrical, which is the point.

    A bearer token stops working on the next token, because the claim is gone. An API key is bound
    to the use case rather than to the group, so it keeps working until somebody revokes it: the
    key is a *credential of the use case*, issued by a member, and losing the right to issue one
    is not the same event as the ones already issued becoming invalid.

    Written down because the opposite is the intuitive expectation, and because an administrator
    removing somebody from a department will assume their keys went too. `FRD-604` is where that
    accountability lives: the key names its owner, so the trail says whose it was.
    """
    admin = await _kc_admin_token()
    name = f"vertrieb-{uuid.uuid4().hex[:6]}"
    group_id = await _child_group(admin, "abteilungen", name)
    group_path = f"/abteilungen/{name}"

    try:
        assert (await _grant(admin_token, slug, group_path)).status_code == 201
        await _member_of(admin, group_id, MEMBER_ACCOUNT, join=True)
        token = await _token(MEMBER_CLIENT_ID, MEMBER_CLIENT_SECRET)
        assert slug in await _visible(token)

        issued = await _issue_key(token, slug, MEMBER_ACCOUNT)
        assert issued.status_code == 201, issued.text[:300]
        key = issued.json()["api_key"]

        await _member_of(admin, group_id, MEMBER_ACCOUNT, join=False)
        after = await _token(MEMBER_CLIENT_ID, MEMBER_CLIENT_SECRET)

        assert slug not in await _visible(after)
        refused = await _call({"Authorization": f"Bearer {after}"}, slug)
        assert refused.status_code == 403, refused.text[:300]

        # And the key, deliberately, still serves.
        with_key = await _call({"x-goog-api-key": key}, slug)
        assert with_key.status_code == 200, with_key.text[:300]
    finally:
        await _delete_group(admin, group_id)
