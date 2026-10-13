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

from . import installation, keycloak
from .conftest import (
    GATEWAY_URL,
    MANAGEMENT_URL,
    MEMBER_ACCOUNT,
    MEMBER_CLIENT_ID,
    MEMBER_CLIENT_SECRET,
    MEMBER_DEPARTMENT,
    MEMBER_DEPARTMENT_NAME,
    _token,
    wait_for_row,
)

pytestmark = pytest.mark.integration

#: A department group that is deliberately **not** named after a use case — the point of the
#: feature is that a grant names whatever the realm actually uses. The suite's *member* service
#: account is in it and holds **no organisation-wide role** (`ADR-0017`) — so anything it can see,
#: it can see because of a grant. That is what makes "they can see it now" mean something. The
#: session's own department, which nobody else is in: a grant must never reach a demo user.
DEPARTMENT = MEMBER_DEPARTMENT
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
    installation.remember("use_case", slug)
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


async def _visible(token: str, slug: str) -> bool:
    """Whether this caller sees ``slug`` — **searched for**, not looked for on page one, where a
    slug that sorts late is missing for every caller and "cannot see it" passes about nothing."""
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.get(
            f"{MANAGEMENT_URL}/api/v1/use-cases/",
            headers={"Authorization": f"Bearer {token}"},
            params={"q": slug},
        )
    response.raise_for_status()
    return slug in {row["slug"] for row in response.json()["results"]}


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


async def test_the_creator_reaches_the_gateway(slug: str, admin_token: str) -> None:
    """The creator is the use case's first administrator (`ADR-0017`), and that membership has to
    reach the gateway like any other membership — not only Management's own list. Polled, because
    it travels over Kafka."""
    body = {
        "contents": [{"role": "user", "parts": [{"text": "Say OK."}]}],
        "generationConfig": {"maxOutputTokens": 8},
    }
    async with httpx.AsyncClient(timeout=60.0) as client:
        for _ in range(40):
            response = await client.post(
                f"{GATEWAY_URL}/uc/{slug}/v1beta/models/{MODEL}:generateContent",
                headers={"Authorization": f"Bearer {admin_token}"},
                json=body,
            )
            if response.status_code == 200:
                break
            await asyncio.sleep(1.5)

    assert response.status_code == 200, response.text


# ---- the control ---------------------------------------------------------------------------


async def test_a_use_case_nobody_was_granted_is_invisible_to_a_member_role(
    slug: str, member_token: str
) -> None:
    """The baseline the next test is measured against. Without it, "they can see it" proves
    nothing — they might have seen it all along."""
    assert not await _visible(member_token, slug)


# ---- granting a group ---------------------------------------------------------------------


async def test_a_granted_department_reaches_its_members_with_no_row_naming_them(
    slug: str, admin_token: str, member_token: str
) -> None:
    """The whole feature, end to end: a real token's real group claim, a real grant, and a real
    permission decision — none of which mentions this person."""
    assert (await _grant(admin_token, slug, DEPARTMENT)).status_code == 201

    assert await _visible(member_token, slug)


async def test_the_grant_is_listed_with_how_many_it_reaches(
    slug: str, admin_token: str, member_token: str
) -> None:
    await _grant(admin_token, slug, DEPARTMENT)
    # The count is of people Management has seen sign in; this makes sure `member_token`'s owner
    # has, so the figure is not zero for the wrong reason.
    await _visible(member_token, slug)

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
    assert not await _visible(member_token, slug)


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
    assert await _visible(member_token, slug)

    # `200` with `revoked_keys`, not `204`: revoking a grant also revokes every key of this
    # use case whose owner no longer holds one (`FRD-613`), and a removal that silently
    # deactivated a credential would be a control whose effect the screen cannot state.
    revoked = await _revoke(admin_token, slug, DEPARTMENT)
    assert revoked.status_code == 200, revoked.text
    assert "revoked_keys" in revoked.json()

    assert not await _visible(member_token, slug)


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

    assert await _visible(member_token, slug)


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


async def test_the_gateway_refuses_a_use_case_nobody_granted(
    slug: str, member_token: str, engine
) -> None:
    """The other half: without a grant, a valid token is still not a member.

    Asked once the gateway **knows** the use case — before that the refusal is the unknown-slug
    one, and "known but not granted" is never reached.
    """
    await wait_for_row(
        engine,
        "SELECT 1 FROM use_cases WHERE slug = :slug AND allowed_models IS NOT NULL",
        {"slug": slug},
        timeout=45.0,
    )
    async with httpx.AsyncClient(timeout=60.0) as client:
        response = await client.post(
            f"{GATEWAY_URL}/v1beta/models/{MODEL}:generateContent",
            headers={"Authorization": f"Bearer {member_token}", "X-AIRA-Use-Case": slug},
            json={"contents": [{"parts": [{"text": "Say OK"}]}]},
        )

    assert response.status_code == 403
    assert slug in response.text


async def test_the_old_convention_still_resolves_from_the_token_alone(slug: str) -> None:
    """`FRD-102`'s `/use-cases/<slug>` route is one way in, not the only one — and the dev realm
    and the demo depend on it.

    **No grant at all**: the member account is put into `/use-cases/<slug>` in the realm, and a
    token minted afterwards is all that names the use case. The route is the gateway's — a caller
    in that group may call as the use case — and Management states the resolution in `/me`, which
    is where the console reads it. Both are asked before and after, so neither can be true all
    along.
    """

    async def resolved(token: str) -> bool:
        async with httpx.AsyncClient(timeout=30.0) as client:
            me = await client.get(
                f"{MANAGEMENT_URL}/api/v1/me", headers={"Authorization": f"Bearer {token}"}
            )
        assert me.status_code == 200, me.text
        return slug in me.json()["use_cases"]

    before = await _token(MEMBER_CLIENT_ID, MEMBER_CLIENT_SECRET)
    assert not await resolved(before), "named before the account was in its group"
    refused = await _call({"Authorization": f"Bearer {before}"}, slug, until=frozenset({403}))
    assert refused.status_code == 403, refused.text[:300]

    async with httpx.AsyncClient(timeout=30.0) as client:
        group = await keycloak.create_group(client, slug, parent="/use-cases")
        try:
            await keycloak.set_membership(
                client, await keycloak.user_id(client, MEMBER_ACCOUNT), group, member=True
            )
            after = await _token(MEMBER_CLIENT_ID, MEMBER_CLIENT_SECRET)
            assert await resolved(after), "the convention's group did not name the use case"
            served = await _call({"Authorization": f"Bearer {after}"}, slug)
            assert served.status_code == 200, served.text[:300]
        finally:
            await keycloak.delete_group(client, group)


# ---- the directory ------------------------------------------------------------------------


async def test_the_directory_offers_a_group_already_granted_somewhere(
    slug: str, admin_token: str
) -> None:
    """This stack has a directory client (`aira-directory`), so the realm answers — and says it
    did. The fallback without one, a real subset labelled as such, is `test_directory.py`'s."""
    await _grant(admin_token, slug, DEPARTMENT)

    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.get(
            f"{MANAGEMENT_URL}/api/v1/directory/",
            headers={"Authorization": f"Bearer {admin_token}"},
            params={"q": MEMBER_DEPARTMENT_NAME},
        )

    body = response.json()
    assert body["source"] == "keycloak"
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
# The realm is written to here, as `test_the_permission_matrix.py` does. The group is removed at the
# end of the test for exactly that reason: a suite that leaves grants behind in somebody's
# directory is doing the thing this system refuses to do.


async def _issue_key(token: str, slug: str, owner: str) -> httpx.Response:
    async with httpx.AsyncClient(timeout=30.0) as client:
        return await client.post(
            f"{MANAGEMENT_URL}/api/v1/use-cases/{slug}/api-keys/",
            headers={"Authorization": f"Bearer {token}"},
            json={"label": "granted-by-group", "owner": owner},
        )


async def _call(
    credential: dict[str, str], slug: str, *, until: frozenset[int] = frozenset({200})
) -> httpx.Response:
    """One ordinary generation, however the caller authenticates — repeated until the answer is
    one of ``until`` or the attempts run out, and then the last answer either way.

    A grant travels over Kafka and a key is distributed the same way; being briefly behind is the
    deliberate cost (`FRD-204`), and a test that did not allow for it would be testing the clock.
    Waiting for a refusal is asked for as such, not by waiting for an allowance that never comes.
    """
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
            if response.status_code in until:
                return response
            await asyncio.sleep(1.0)
        return response


async def test_a_person_added_to_a_granted_group_reaches_it_with_both_credentials(
    slug: str, admin_token: str
) -> None:
    """The whole chain an administrator actually performs, in order.

    Grant a department that reaches nobody, **then** put somebody in it, then call — with a bearer
    token and with an API key that person issued for themselves. Each step is asserted before the
    next, so a failure says which link broke rather than that "access does not work".
    """
    name = f"vertrieb-{uuid.uuid4().hex[:6]}"
    group_path = f"/abteilungen/{name}"
    realm = httpx.AsyncClient(timeout=30.0)
    group_id = await keycloak.create_group(realm, name, parent="/abteilungen")
    member = await keycloak.user_id(realm, MEMBER_ACCOUNT)

    try:
        assert (await _grant(admin_token, slug, group_path)).status_code == 201

        # Nobody is in it yet, so the grant reaches nobody — and a token minted now carries no
        # such group. This is the "before" the rest of the test is measured against.
        before = await _token(MEMBER_CLIENT_ID, MEMBER_CLIENT_SECRET)
        assert not await _visible(before, slug)

        await keycloak.set_membership(realm, member, group_id, member=True)

        # **A new token.** Group claims are baked in when a token is issued, so the one obtained
        # above is still correct about the past — the mirror of the hermetic rule that leaving a
        # group takes access away *on the next token*.
        after = await _token(MEMBER_CLIENT_ID, MEMBER_CLIENT_SECRET)
        assert await _visible(after, slug), "the grant did not reach a person added afterwards"

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
        # a suite that leaves grants behind in one is doing what the system refuses to. Removing
        # the group ends the membership with it; each call asks for a token of its own.
        await keycloak.delete_group(realm, group_id)
        await realm.aclose()


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
    name = f"vertrieb-{uuid.uuid4().hex[:6]}"
    group_path = f"/abteilungen/{name}"
    realm = httpx.AsyncClient(timeout=30.0)
    group_id = await keycloak.create_group(realm, name, parent="/abteilungen")
    member = await keycloak.user_id(realm, MEMBER_ACCOUNT)

    try:
        assert (await _grant(admin_token, slug, group_path)).status_code == 201
        await keycloak.set_membership(realm, member, group_id, member=True)
        token = await _token(MEMBER_CLIENT_ID, MEMBER_CLIENT_SECRET)
        assert await _visible(token, slug)

        issued = await _issue_key(token, slug, MEMBER_ACCOUNT)
        assert issued.status_code == 201, issued.text[:300]
        key = issued.json()["api_key"]

        await keycloak.set_membership(realm, member, group_id, member=False)
        after = await _token(MEMBER_CLIENT_ID, MEMBER_CLIENT_SECRET)

        assert not await _visible(after, slug)
        refused = await _call({"Authorization": f"Bearer {after}"}, slug, until=frozenset({403}))
        assert refused.status_code == 403, refused.text[:300]

        # And the key, deliberately, still serves.
        with_key = await _call({"x-goog-api-key": key}, slug)
        assert with_key.status_code == 200, with_key.text[:300]
    finally:
        await keycloak.delete_group(realm, group_id)
        await realm.aclose()
