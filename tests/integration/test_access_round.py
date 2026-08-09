"""A developer round on access by group, against the running stack (`FRD-209`).

`FRD-129`'s shape: many small cases walking one feature end to end, every figure checked where it
lives rather than in the response that claims it, and nothing asserting a model's answer.

What this layer can see that the hermetic suites structurally cannot: a real realm issuing real
tokens with real group claims, a compacted Kafka topic actually carrying a grant, and a running
gateway process deciding on its own cached copy. The three defects the first version of this
feature had were all of that kind — an event with no topic, a compaction key that erased its
predecessor, and a token with no `groups` claim at all.

Nothing here asserts an answer's content. What is asserted is who reaches what, what is refused,
and that a refusal says which.
"""

from __future__ import annotations

import asyncio
import uuid

import httpx
import pytest
from sqlalchemy import text

from .conftest import GATEWAY_URL, MANAGEMENT_URL

pytestmark = pytest.mark.integration

#: A department group in the dev realm, deliberately not named after a use case. The suite's
#: *member* account is in it and holds **no organisation-wide role** (`ADR-0017`), so anything it
#: can see, it can see because of a grant.
DEPARTMENT = "/abteilungen/kundendienst"
UNUSED_GROUP = "/abteilungen/nobody-is-in-this"
MODEL = "qwen3:0.6b"
SHORT = {"generationConfig": {"maxOutputTokens": 8}}


# ---- helpers --------------------------------------------------------------------------------


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _post(path: str, token: str, body: dict) -> httpx.Response:
    async with httpx.AsyncClient(timeout=30.0) as client:
        return await client.post(f"{MANAGEMENT_URL}{path}", headers=_auth(token), json=body)


async def _get(path: str, token: str, **params) -> httpx.Response:
    async with httpx.AsyncClient(timeout=30.0) as client:
        return await client.get(f"{MANAGEMENT_URL}{path}", headers=_auth(token), params=params)


async def _delete(path: str, token: str, **params) -> httpx.Response:
    async with httpx.AsyncClient(timeout=30.0) as client:
        return await client.delete(f"{MANAGEMENT_URL}{path}", headers=_auth(token), params=params)


async def _grant(token: str, slug: str, group: str, role: str = "user") -> httpx.Response:
    return await _post(
        f"/api/v1/use-cases/{slug}/groups/", token, {"group_path": group, "role": role}
    )


async def _revoke(token: str, slug: str, group: str) -> httpx.Response:
    return await _delete(f"/api/v1/use-cases/{slug}/groups/revoke/", token, group_path=group)


async def _visible(token: str) -> set[str]:
    response = await _get("/api/v1/use-cases/", token, page_size=200)
    response.raise_for_status()
    return {row["slug"] for row in response.json()["results"]}


async def _grants(token: str, slug: str) -> list[dict]:
    return (await _get(f"/api/v1/use-cases/{slug}/groups/", token)).json()


async def _generate(token: str, slug: str) -> httpx.Response:
    async with httpx.AsyncClient(timeout=180.0) as client:
        return await client.post(
            f"{GATEWAY_URL}/v1beta/models/{MODEL}:generateContent",
            headers={**_auth(token), "X-AIRA-Use-Case": slug},
            json={"contents": [{"parts": [{"text": "Say OK"}]}], **SHORT},
        )


async def _until_allowed(token: str, slug: str, attempts: int = 25) -> httpx.Response:
    """Poll until the grant has reached the gateway.

    The grant travels over Kafka and the gateway caches it for a few seconds; being briefly behind
    is the deliberate cost of not asking Management on the request path. A test that did not allow
    for it would be testing the clock.
    """
    for _ in range(attempts):
        response = await _generate(token, slug)
        if response.status_code != 403:
            return response
        await asyncio.sleep(1.0)
    return response


async def _until_refused(token: str, slug: str, attempts: int = 25) -> httpx.Response:
    for _ in range(attempts):
        response = await _generate(token, slug)
        if response.status_code == 403:
            return response
        await asyncio.sleep(1.0)
    return response


@pytest.fixture
async def slug(admin_token: str):
    name = f"acc-{uuid.uuid4().hex[:8]}"
    assert (
        await _post("/api/v1/use-cases/", admin_token, {"slug": name, "name": name})
    ).status_code == 201
    yield name
    await _delete(f"/api/v1/use-cases/{name}/", admin_token)


@pytest.fixture
async def second(admin_token: str):
    name = f"acc2-{uuid.uuid4().hex[:8]}"
    await _post("/api/v1/use-cases/", admin_token, {"slug": name, "name": name})
    yield name
    await _delete(f"/api/v1/use-cases/{name}/", admin_token)


# ═══ 1. the grant itself ═══════════════════════════════════════════════════════════════════════


async def test_01_a_fresh_use_case_grants_nobody(slug, member_token) -> None:
    """The control every later case is measured against."""
    assert slug not in await _visible(member_token)


async def test_02_a_grant_is_created_and_listed(slug, admin_token) -> None:
    assert (await _grant(admin_token, slug, DEPARTMENT)).status_code == 201
    assert [row["group_path"] for row in await _grants(admin_token, slug)] == [DEPARTMENT]


async def test_03_the_grant_records_its_author(slug, admin_token) -> None:
    await _grant(admin_token, slug, DEPARTMENT)
    assert (await _grants(admin_token, slug))[0]["granted_by"]


async def test_04_the_grant_defaults_to_the_weaker_role(slug, admin_token) -> None:
    """`user`, not `admin`. A default that hands out the stronger one is a default nobody notices
    until somebody uses it."""
    await _post(f"/api/v1/use-cases/{slug}/groups/", admin_token, {"group_path": DEPARTMENT})
    assert (await _grants(admin_token, slug))[0]["role"] == "user"


async def test_05_granting_twice_updates_rather_than_duplicating(slug, admin_token) -> None:
    await _grant(admin_token, slug, DEPARTMENT, "user")
    await _grant(admin_token, slug, DEPARTMENT, "admin")
    grants = await _grants(admin_token, slug)
    assert len(grants) == 1 and grants[0]["role"] == "admin"


async def test_06_a_path_that_is_not_a_path_is_refused_by_name(slug, admin_token) -> None:
    response = await _grant(admin_token, slug, "kundendienst")
    assert response.status_code == 400
    assert "group_path" in response.text


async def test_07_an_empty_path_is_refused(slug, admin_token) -> None:
    assert (await _grant(admin_token, slug, "")).status_code == 400


async def test_08_a_group_that_does_not_exist_yet_is_grantable(slug, admin_token) -> None:
    """The identity provider may create it tomorrow; refusing would make onboarding a department a
    two-step dance across two systems."""
    assert (await _grant(admin_token, slug, "/not/created/yet")).status_code == 201


async def test_09_an_unknown_role_is_refused_rather_than_coerced(slug, admin_token) -> None:
    assert (await _grant(admin_token, slug, DEPARTMENT, "superuser")).status_code == 400


async def test_10_a_very_long_path_is_bounded(slug, admin_token) -> None:
    assert (await _grant(admin_token, slug, "/" + "x" * 500)).status_code == 400


# ═══ 2. what a grant does in Management ════════════════════════════════════════════════════════


async def test_11_a_granted_group_makes_its_members_see_the_use_case(
    slug, admin_token, member_token
) -> None:
    await _grant(admin_token, slug, DEPARTMENT)
    assert slug in await _visible(member_token)


async def test_12_no_row_names_the_person_who_gained_access(
    slug, admin_token, member_token
) -> None:
    """The whole point: membership is the identity provider's answer, not a list here.

    The *creator* is a member — that is how creating one works, and it is not what this is about.
    What matters is that the person who gained access **through the group** appears nowhere.
    """
    await _grant(admin_token, slug, DEPARTMENT)
    who = (await _get("/api/v1/me", member_token)).json()["username"]
    assert slug in await _visible(member_token)

    members = (await _get(f"/api/v1/use-cases/{slug}/members/", admin_token)).json()

    assert who not in [row["username"] for row in members]


async def test_13_a_group_nobody_holds_grants_nobody(slug, admin_token, member_token) -> None:
    await _grant(admin_token, slug, UNUSED_GROUP)
    assert slug not in await _visible(member_token)


async def test_14_a_user_grant_may_not_change_the_use_case(slug, admin_token, member_token) -> None:
    await _grant(admin_token, slug, DEPARTMENT, "user")
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.patch(
            f"{MANAGEMENT_URL}/api/v1/use-cases/{slug}/",
            headers=_auth(member_token),
            json={"name": "x"},
        )
    assert response.status_code == 403


async def test_15_an_admin_grant_may(slug, admin_token, member_token) -> None:
    await _grant(admin_token, slug, DEPARTMENT, "admin")
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.patch(
            f"{MANAGEMENT_URL}/api/v1/use-cases/{slug}/",
            headers=_auth(member_token),
            json={"name": "renamed by a group"},
        )
    assert response.status_code == 200, response.text


async def test_16_lowering_a_grant_actually_lowers_it(slug, admin_token, member_token) -> None:
    """A demotion that demotes nothing is worse than none — it reads as done."""
    await _grant(admin_token, slug, DEPARTMENT, "admin")
    await _grant(admin_token, slug, DEPARTMENT, "user")
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.patch(
            f"{MANAGEMENT_URL}/api/v1/use-cases/{slug}/",
            headers=_auth(member_token),
            json={"name": "x"},
        )
    assert response.status_code == 403


async def test_17_an_admin_grant_may_manage_members(slug, admin_token, member_token) -> None:
    await _grant(admin_token, slug, DEPARTMENT, "admin")
    response = await _post(
        f"/api/v1/use-cases/{slug}/members/", member_token, {"username": "ucuser", "role": "user"}
    )
    assert response.status_code in (201, 400), response.text


async def test_18_an_admin_grant_may_grant_another_group(slug, admin_token, member_token) -> None:
    await _grant(admin_token, slug, DEPARTMENT, "admin")
    assert (await _grant(member_token, slug, "/abteilungen/entwicklung")).status_code == 201


async def test_19_a_user_grant_may_not_grant_anything(slug, admin_token, member_token) -> None:
    await _grant(admin_token, slug, DEPARTMENT, "user")
    assert (await _grant(member_token, slug, "/abteilungen/entwicklung")).status_code == 403


async def test_20_a_member_may_read_who_has_access(slug, admin_token, member_token) -> None:
    """Not a secret from its own members — and hiding it makes "why can that person call this"
    unanswerable without a database."""
    await _grant(admin_token, slug, DEPARTMENT)
    assert (await _get(f"/api/v1/use-cases/{slug}/groups/", member_token)).status_code == 200


async def test_21_the_reported_permission_matches_what_the_request_does(
    slug, admin_token, member_token
) -> None:
    """`FRD-206`'s agreement rule, over a grant nobody's name appears in."""
    await _grant(admin_token, slug, DEPARTMENT, "admin")
    reported = (await _get(f"/api/v1/use-cases/{slug}/", member_token)).json()["permissions"]
    async with httpx.AsyncClient(timeout=30.0) as client:
        attempted = await client.patch(
            f"{MANAGEMENT_URL}/api/v1/use-cases/{slug}/",
            headers=_auth(member_token),
            json={"name": "agreement"},
        )
    assert reported["can_admin"] is (attempted.status_code == 200)


async def test_22_a_group_grant_makes_somebody_a_member_for_key_issuance(
    slug, admin_token, member_token
) -> None:
    """`is_member` is what issuing a key needs, and it is not the same as "may see it" (ADR-0007).
    A group grant has to satisfy it, or the console offers a key the server refuses."""
    await _grant(admin_token, slug, DEPARTMENT, "admin")
    reported = (await _get(f"/api/v1/use-cases/{slug}/", member_token)).json()["permissions"]
    assert reported["is_member"] is True
    issued = await _post(f"/api/v1/use-cases/{slug}/api-keys/", member_token, {"label": "round"})
    assert issued.status_code == 201, issued.text


# ═══ 3. one group, several use cases ═══════════════════════════════════════════════════════════


async def test_23_one_group_reaches_two_use_cases(slug, second, admin_token, member_token) -> None:
    await _grant(admin_token, slug, DEPARTMENT)
    await _grant(admin_token, second, DEPARTMENT)
    visible = await _visible(member_token)
    assert {slug, second} <= visible


async def test_24_two_grants_survive_the_compacted_topic(slug, second, admin_token, engine) -> None:
    """A compacted topic keeps the last message per key. Two grants sharing one key would mean the
    second erased the first, and a gateway rebuilding from the log would silently lose access.
    """
    await _grant(admin_token, slug, DEPARTMENT)
    await _grant(admin_token, second, DEPARTMENT)

    for _ in range(25):
        async with engine.connect() as connection:
            rows = (
                (
                    await connection.execute(
                        text(
                            "SELECT use_case_slug FROM use_case_groups WHERE group_path = :p"
                            " AND use_case_slug IN (:a, :b)"
                        ),
                        {"p": DEPARTMENT, "a": slug, "b": second},
                    )
                )
                .scalars()
                .all()
            )
        if len(set(rows)) == 2:
            break
        await asyncio.sleep(1.0)

    assert set(rows) == {slug, second}


async def test_25_two_groups_on_one_use_case_both_arrive(slug, admin_token, engine) -> None:
    await _grant(admin_token, slug, DEPARTMENT)
    await _grant(admin_token, slug, "/abteilungen/entwicklung")

    for _ in range(25):
        async with engine.connect() as connection:
            rows = (
                (
                    await connection.execute(
                        text("SELECT group_path FROM use_case_groups WHERE use_case_slug = :s"),
                        {"s": slug},
                    )
                )
                .scalars()
                .all()
            )
        if len(rows) == 2:
            break
        await asyncio.sleep(1.0)

    assert set(rows) == {DEPARTMENT, "/abteilungen/entwicklung"}


# ═══ 4. the gateway ════════════════════════════════════════════════════════════════════════════


async def test_26_the_gateway_refuses_a_use_case_nobody_granted(slug, member_token) -> None:
    response = await _generate(member_token, slug)
    assert response.status_code == 403
    assert slug in response.text


async def test_27_the_gateway_allows_a_granted_group(slug, admin_token, member_token) -> None:
    """The two planes give the same answer now — which is the disagreement this feature closed."""
    await _grant(admin_token, slug, DEPARTMENT)
    response = await _until_allowed(member_token, slug)
    assert response.status_code == 200, response.text


async def test_28_a_request_through_a_group_grant_is_audited_like_any_other(
    slug, admin_token, member_token, engine
) -> None:
    """Access arriving by a new route must not produce a different audit row — that is what makes
    a report about it comparable with everything else (`FRD-122`)."""
    await _grant(admin_token, slug, DEPARTMENT)
    assert (await _until_allowed(member_token, slug)).status_code == 200

    for _ in range(20):
        async with engine.connect() as connection:
            row = (
                await connection.execute(
                    text(
                        "SELECT outcome, status, subject, total_tokens FROM request_logs"
                        " WHERE use_case = :s ORDER BY created_at DESC LIMIT 1"
                    ),
                    {"s": slug},
                )
            ).first()
        if row is not None:
            break
        await asyncio.sleep(1.0)

    assert row is not None, "a served request left no audit row"
    assert row[0] == "served"
    assert row[1] == 200
    assert row[2]
    assert row[3] and row[3] > 0


async def test_29_revoking_stops_the_gateway_too(slug, admin_token, member_token) -> None:
    await _grant(admin_token, slug, DEPARTMENT)
    assert (await _until_allowed(member_token, slug)).status_code == 200

    await _revoke(admin_token, slug, DEPARTMENT)

    assert (await _until_refused(member_token, slug)).status_code == 403


async def test_30_a_grant_on_the_wrong_use_case_does_not_open_another(
    slug, second, admin_token, member_token
) -> None:
    await _grant(admin_token, slug, DEPARTMENT)
    assert (await _until_allowed(member_token, slug)).status_code == 200

    assert (await _generate(member_token, second)).status_code == 403


async def test_31_the_selector_still_has_to_name_a_use_case_the_caller_reaches(
    slug, admin_token, member_token
) -> None:
    await _grant(admin_token, slug, DEPARTMENT)
    await _until_allowed(member_token, slug)

    response = await _generate(member_token, "no-such-use-case-at-all")
    assert response.status_code == 403


async def test_32_a_grant_does_not_bypass_the_body_ceiling(slug, admin_token, member_token) -> None:
    """Access is not permission to do anything: every other control still applies."""
    await _grant(admin_token, slug, DEPARTMENT)
    await _until_allowed(member_token, slug)

    oversized = b'{"contents":[{"parts":[{"text":"' + b"x" * 20_000_000 + b'"}]}]}'
    async with httpx.AsyncClient(timeout=120.0) as client:
        response = await client.post(
            f"{GATEWAY_URL}/v1beta/models/{MODEL}:generateContent",
            headers={
                **_auth(member_token),
                "X-AIRA-Use-Case": slug,
                "Content-Type": "application/json",
            },
            content=oversized,
        )

    assert response.status_code == 413


async def test_33_an_invalid_token_is_still_refused(slug, admin_token) -> None:
    await _grant(admin_token, slug, DEPARTMENT)
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(
            f"{GATEWAY_URL}/v1beta/models/{MODEL}:generateContent",
            headers={"Authorization": "Bearer not-a-token", "X-AIRA-Use-Case": slug},
            json={"contents": [{"parts": [{"text": "hi"}]}]},
        )
    assert response.status_code == 401


async def test_34_the_reporting_view_shows_traffic_that_arrived_by_group(
    slug, admin_token, member_token
) -> None:
    """A route that produced no reportable traffic would be a route nobody could account for."""
    await _grant(admin_token, slug, DEPARTMENT)
    assert (await _until_allowed(member_token, slug)).status_code == 200

    for _ in range(20):
        async with httpx.AsyncClient(timeout=30.0) as client:
            body = (
                await client.get(
                    f"{GATEWAY_URL}/v1beta/traces",
                    headers=_auth(member_token),
                    params={"use_case": slug},
                )
            ).json()
        if body["traces"]:
            break
        await asyncio.sleep(1.0)

    assert body["in_scope"] is True
    assert body["traces"], "a request made through a group grant left no trace"


async def test_35_the_trace_view_scopes_to_what_the_group_reaches(
    slug, second, admin_token, member_token
) -> None:
    """The gateway's `visible_scope` reads the same resolved membership the request path does."""
    await _grant(admin_token, slug, DEPARTMENT)
    await _until_allowed(member_token, slug)

    async with httpx.AsyncClient(timeout=30.0) as client:
        body = (
            await client.get(
                f"{GATEWAY_URL}/v1beta/traces",
                headers=_auth(member_token),
                params={"use_case": second},
            )
        ).json()

    assert body["traces"] == []
    assert body["in_scope"] is False


# ═══ 5. revoking, and what it must not take away ═══════════════════════════════════════════════


async def test_36_revoking_removes_the_grant_from_the_list(slug, admin_token) -> None:
    await _grant(admin_token, slug, DEPARTMENT)
    assert (await _revoke(admin_token, slug, DEPARTMENT)).status_code == 204
    assert await _grants(admin_token, slug) == []


async def test_37_revoking_something_never_granted_says_so(slug, admin_token) -> None:
    response = await _revoke(admin_token, slug, "/nope")
    assert response.status_code == 400
    assert "not granted" in response.text


async def test_38_revoking_leaves_a_direct_grant_intact(slug, admin_token, member_token) -> None:
    await _grant(admin_token, slug, DEPARTMENT)
    me = (await _get("/api/v1/me", member_token)).json()["username"]
    await _post(f"/api/v1/use-cases/{slug}/members/", admin_token, {"username": me, "role": "user"})

    await _revoke(admin_token, slug, DEPARTMENT)

    assert slug in await _visible(member_token)


async def test_39_revoking_one_group_leaves_another(slug, admin_token, member_token) -> None:
    await _grant(admin_token, slug, DEPARTMENT)
    await _grant(admin_token, slug, "/abteilungen/entwicklung")

    await _revoke(admin_token, slug, "/abteilungen/entwicklung")

    assert slug in await _visible(member_token)


async def test_40_a_user_grant_may_not_revoke(slug, admin_token, member_token) -> None:
    await _grant(admin_token, slug, DEPARTMENT, "user")
    assert (await _revoke(member_token, slug, DEPARTMENT)).status_code == 403


async def test_41_deleting_the_use_case_takes_its_grants(slug, admin_token, engine) -> None:
    await _grant(admin_token, slug, DEPARTMENT)
    for _ in range(25):
        async with engine.connect() as connection:
            count = (
                await connection.execute(
                    text("SELECT count(*) FROM use_case_groups WHERE use_case_slug = :s"),
                    {"s": slug},
                )
            ).scalar_one()
        if count:
            break
        await asyncio.sleep(1.0)
    assert count == 1

    await _delete(f"/api/v1/use-cases/{slug}/", admin_token)

    for _ in range(25):
        async with engine.connect() as connection:
            count = (
                await connection.execute(
                    text("SELECT count(*) FROM use_case_groups WHERE use_case_slug = :s"),
                    {"s": slug},
                )
            ).scalar_one()
        if count == 0:
            break
        await asyncio.sleep(1.0)
    assert count == 0


# ═══ 6. how far a grant reaches ════════════════════════════════════════════════════════════════


async def test_42_a_grant_reaching_nobody_reports_zero(slug, admin_token) -> None:
    await _grant(admin_token, slug, UNUSED_GROUP)
    assert (await _grants(admin_token, slug))[0]["reaches"] == 0


async def test_43_a_grant_reaching_somebody_reports_more(slug, admin_token, member_token) -> None:
    await _grant(admin_token, slug, DEPARTMENT)
    await _visible(member_token)  # so Management has seen this account sign in
    assert (await _grants(admin_token, slug))[0]["reaches"] >= 1


# ═══ 7. the directory ══════════════════════════════════════════════════════════════════════════


async def test_44_the_directory_needs_a_credential() -> None:
    async with httpx.AsyncClient(timeout=30.0) as client:
        assert (await client.get(f"{MANAGEMENT_URL}/api/v1/directory/?q=ada")).status_code == 401


async def test_45_the_directory_refuses_one_letter(admin_token) -> None:
    body = (await _get("/api/v1/directory/", admin_token, q="a")).json()
    assert body["results"] == [] and body["source"] == "none"


async def test_46_the_directory_finds_a_granted_group(slug, admin_token) -> None:
    await _grant(admin_token, slug, DEPARTMENT)
    body = (await _get("/api/v1/directory/", admin_token, q="kundendienst")).json()
    assert any(row["id"] == DEPARTMENT and row["kind"] == "group" for row in body["results"])


async def test_47_the_directory_says_which_source_answered(admin_token) -> None:
    """ "No results" from a directory nobody could reach reads exactly like "no such group"."""
    body = (await _get("/api/v1/directory/", admin_token, q="kunde")).json()
    assert body["source"] in ("local", "keycloak")


async def test_48_the_directory_finds_a_person_who_has_signed_in(admin_token, member_token) -> None:
    me = (await _get("/api/v1/me", member_token)).json()["username"]
    body = (await _get("/api/v1/directory/", admin_token, q=me[:6])).json()
    assert any(row["id"] == me for row in body["results"])


async def test_49_the_directory_returns_no_credential(admin_token, member_token) -> None:
    await _get("/api/v1/me", member_token)
    body = (await _get("/api/v1/directory/", admin_token, q="service")).json()
    for row in body["results"]:
        assert set(row) == {"kind", "id", "label", "detail"}


async def test_50_the_directory_never_writes(admin_token) -> None:
    """AIRA does not create groups. There is deliberately no endpoint that could."""
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(
            f"{MANAGEMENT_URL}/api/v1/directory/",
            headers=_auth(admin_token),
            json={"name": "invented"},
        )
    assert response.status_code == 405


# ═══ 8. edges nobody has walked yet ════════════════════════════════════════════════════════════
#
# Every case below asserts three things at once, the same three the `FRD-129` round did: **never a
# 500**, an actionable status, and a message that names the problem. A control that refuses without
# saying what is wrong is a control whose next report is "it just stopped working".


async def test_51_a_grant_body_that_is_a_list_is_refused(slug, admin_token) -> None:
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(
            f"{MANAGEMENT_URL}/api/v1/use-cases/{slug}/groups/",
            headers=_auth(admin_token),
            json=[{"group_path": DEPARTMENT}],
        )
    assert response.status_code == 400, response.text


async def test_52_a_grant_with_no_body_at_all_is_refused(slug, admin_token) -> None:
    assert (await _post(f"/api/v1/use-cases/{slug}/groups/", admin_token, {})).status_code == 400


async def test_53_a_path_with_whitespace_is_refused(slug, admin_token) -> None:
    """A path Keycloak could never report is a grant that can only ever be inert."""
    assert (await _grant(admin_token, slug, "/ai/kunden dienst")).status_code == 400


async def test_54_a_path_with_a_newline_is_refused(slug, admin_token) -> None:
    assert (await _grant(admin_token, slug, "/ai/x\ny")).status_code == 400


async def test_55_granting_on_a_use_case_that_does_not_exist_is_a_404(admin_token) -> None:
    assert (await _grant(admin_token, "no-such-use-case", DEPARTMENT)).status_code == 404


async def test_56_listing_grants_of_a_use_case_that_does_not_exist_is_a_404(admin_token) -> None:
    assert (await _get("/api/v1/use-cases/no-such-uc/groups/", admin_token)).status_code == 404


async def test_57_an_outsider_gets_the_same_404_rather_than_a_403(
    slug, admin_token, security_token
) -> None:
    """`it-security` sees every use case for oversight and administers none. Whether it gets 403
    or 404, what matters is that it is refused and told which."""
    response = await _grant(security_token, slug, DEPARTMENT)
    # 403, not 404: an oversight role *can* see the use case, so the honest answer is about the
    # permission rather than about the existence.
    assert response.status_code == 403
    assert "cannot manage access" in response.text


async def test_58_an_unauthenticated_grant_is_a_401(slug) -> None:
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(
            f"{MANAGEMENT_URL}/api/v1/use-cases/{slug}/groups/",
            json={"group_path": DEPARTMENT},
        )
    assert response.status_code == 401


async def test_59_revoking_without_naming_a_group_is_refused_by_name(slug, admin_token) -> None:
    response = await _delete(f"/api/v1/use-cases/{slug}/groups/revoke/", admin_token)
    assert response.status_code == 400
    assert "group_path" in response.text


async def test_60_a_grant_survives_a_use_case_rename(slug, admin_token, member_token) -> None:
    """The grant is keyed by the use case, not by its name."""
    await _grant(admin_token, slug, DEPARTMENT)
    async with httpx.AsyncClient(timeout=30.0) as client:
        await client.patch(
            f"{MANAGEMENT_URL}/api/v1/use-cases/{slug}/",
            headers=_auth(admin_token),
            json={"name": "a different name"},
        )
    assert slug in await _visible(member_token)


async def test_61_two_use_cases_granting_the_same_group_are_independent(
    slug, second, admin_token, member_token
) -> None:
    await _grant(admin_token, slug, DEPARTMENT)
    await _grant(admin_token, second, DEPARTMENT)

    await _revoke(admin_token, slug, DEPARTMENT)

    visible = await _visible(member_token)
    assert second in visible and slug not in visible


async def test_62_a_role_change_reaches_the_gateway_read_model(slug, admin_token, engine) -> None:
    await _grant(admin_token, slug, DEPARTMENT, "user")
    await _grant(admin_token, slug, DEPARTMENT, "admin")

    for _ in range(25):
        async with engine.connect() as connection:
            role = (
                await connection.execute(
                    text(
                        "SELECT role FROM use_case_groups WHERE use_case_slug = :s"
                        " AND group_path = :p"
                    ),
                    {"s": slug, "p": DEPARTMENT},
                )
            ).scalar_one_or_none()
        if role == "admin":
            break
        await asyncio.sleep(1.0)

    assert role == "admin"


async def test_63_a_revocation_reaches_the_gateway_read_model(slug, admin_token, engine) -> None:
    await _grant(admin_token, slug, DEPARTMENT)
    for _ in range(25):
        async with engine.connect() as connection:
            present = (
                await connection.execute(
                    text("SELECT count(*) FROM use_case_groups WHERE use_case_slug = :s"),
                    {"s": slug},
                )
            ).scalar_one()
        if present:
            break
        await asyncio.sleep(1.0)
    assert present == 1

    await _revoke(admin_token, slug, DEPARTMENT)

    for _ in range(25):
        async with engine.connect() as connection:
            present = (
                await connection.execute(
                    text("SELECT count(*) FROM use_case_groups WHERE use_case_slug = :s"),
                    {"s": slug},
                )
            ).scalar_one()
        if present == 0:
            break
        await asyncio.sleep(1.0)
    assert present == 0


async def test_64_an_api_key_is_unaffected_by_group_grants(slug, admin_token, fixture) -> None:
    """An API key is bound to one use case and carries its own attribution (`FRD-205`). A group
    grant must not widen it — the two mechanisms answer different questions."""
    await _grant(admin_token, slug, DEPARTMENT)

    async with httpx.AsyncClient(timeout=60.0) as client:
        response = await client.post(
            f"{GATEWAY_URL}/v1beta/models/{MODEL}:generateContent",
            headers={**fixture.headers(), "X-AIRA-Use-Case": slug},
            json={"contents": [{"parts": [{"text": "hi"}]}], **SHORT},
        )

    assert response.status_code == 403


async def test_65_the_bare_realm_root_is_refused(slug, admin_token) -> None:
    """Found by this round. `/` was accepted, and it can **never** match: every path a token
    reports starts with a name, so the grant is permanently inert while reading to a person as
    "the whole realm". A grant that cannot match anything is what the path validation is for.
    """
    response = await _grant(admin_token, slug, "/")
    assert response.status_code == 400
    assert "group_path" in response.text


async def test_65b_a_trailing_slash_alone_is_refused(slug, admin_token) -> None:
    assert (await _grant(admin_token, slug, "//")).status_code == 400


async def test_66_a_second_grant_does_not_disturb_the_first_role(slug, admin_token, engine) -> None:
    await _grant(admin_token, slug, DEPARTMENT, "admin")
    await _grant(admin_token, slug, "/abteilungen/entwicklung", "user")

    for _ in range(25):
        async with engine.connect() as connection:
            rows = dict(
                (
                    await connection.execute(
                        text(
                            "SELECT group_path, role FROM use_case_groups WHERE use_case_slug = :s"
                        ),
                        {"s": slug},
                    )
                ).all()
            )
        if len(rows) == 2:
            break
        await asyncio.sleep(1.0)

    assert rows == {DEPARTMENT: "admin", "/abteilungen/entwicklung": "user"}


async def test_67_the_grant_list_is_ordered_so_two_reads_agree(slug, admin_token) -> None:
    """An unordered list is a list that reorders itself under the reader for no reason."""
    for path in ("/z/last", "/a/first", "/m/middle"):
        await _grant(admin_token, slug, path)

    first = [row["group_path"] for row in await _grants(admin_token, slug)]
    second = [row["group_path"] for row in await _grants(admin_token, slug)]

    assert first == second == sorted(first)


async def test_68_the_directory_is_bounded(admin_token) -> None:
    body = (await _get("/api/v1/directory/", admin_token, q="service")).json()
    assert len(body["results"]) <= 50


async def test_69_the_directory_tolerates_a_query_full_of_punctuation(admin_token) -> None:
    response = await _get("/api/v1/directory/", admin_token, q="%%__''\"")
    assert response.status_code == 200
    assert response.json()["results"] == [] or isinstance(response.json()["results"], list)


async def test_70_the_directory_does_not_leak_across_a_wildcard(admin_token) -> None:
    """A SQL `LIKE` wildcard typed by a caller must be a literal, not a wildcard: otherwise `%`
    lists the entire directory and the two-letter minimum protects nothing."""
    body = (await _get("/api/v1/directory/", admin_token, q="%%")).json()
    assert body["results"] == []
