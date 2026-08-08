"""Access granted to a Keycloak group (`FRD-209`) — the Management half.

The property that matters most is that **nothing else had to change**: `scope_queryset`,
`may_admin`, `may_manage` and every viewset keep working, because a group grant assigns object
permissions to a Django group and `django-guardian` resolves user-and-group permissions in one
query. A second permission path beside guardian's would be a second chance to forget one, which is
precisely the mistake the two planes already made about membership.

So most of these assert an *effect on an ordinary request*, not the row that produced it.
"""

from __future__ import annotations

from typing import Any

import pytest
from aira_management.apps.usecases.models import UseCase, UseCaseGroupGrant
from aira_management.rbac import django_group_name, sync_user_groups, sync_user_roles
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

pytestmark = pytest.mark.django_db

BASE = "/api/v1/use-cases/"
GROUP = "/ai/kundenservice"


def _user(username: str, *roles: str, groups: tuple[str, ...] = ()) -> Any:
    """A user as an authenticated request would leave them: roles and groups synced from a token."""
    user = get_user_model().objects.create(username=username)
    sync_user_roles(user, {"realm_access": {"roles": list(roles)}})
    sync_user_groups(user, {"groups": list(groups)})
    return user


def _client(user: Any) -> APIClient:
    client = APIClient()
    client.force_authenticate(user=user)
    return client


def _create(client: APIClient, slug: str) -> None:
    assert client.post(BASE, {"slug": slug, "name": slug}, format="json").status_code == 201


# ---- granting ----------------------------------------------------------------------------


def test_an_admin_grants_a_group_and_it_appears_in_the_access_list() -> None:
    admin = _user("a", "use-case-admin")
    client = _client(admin)
    _create(client, "uc-a")

    response = client.post(
        f"{BASE}uc-a/groups/", {"group_path": GROUP, "role": "user"}, format="json"
    )

    assert response.status_code == 201
    assert response.data["group_path"] == GROUP
    assert client.get(f"{BASE}uc-a/groups/").data[0]["group_path"] == GROUP


def test_the_grant_records_who_made_it() -> None:
    """Kept for the same reason a suspension keeps its author: a review asks."""
    admin = _user("a", "use-case-admin")
    client = _client(admin)
    _create(client, "uc-a")
    client.post(f"{BASE}uc-a/groups/", {"group_path": GROUP}, format="json")

    assert client.get(f"{BASE}uc-a/groups/").data[0]["granted_by"] == "a"


def test_somebody_who_does_not_administer_the_use_case_cannot_grant() -> None:
    owner = _user("a", "use-case-admin")
    outsider = _user("b", "use-case-admin")
    _create(_client(owner), "uc-a")

    response = _client(outsider).post(f"{BASE}uc-a/groups/", {"group_path": GROUP}, format="json")

    # 404 rather than 403: the use case is not visible to them at all, and confirming it exists
    # would be a different answer from the one their permissions justify.
    assert response.status_code in (403, 404)


def test_a_group_path_must_look_like_a_path() -> None:
    admin = _user("a", "use-case-admin")
    client = _client(admin)
    _create(client, "uc-a")

    assert (
        client.post(
            f"{BASE}uc-a/groups/", {"group_path": "kundenservice"}, format="json"
        ).status_code
        == 400
    )


def test_a_group_that_does_not_exist_yet_is_still_grantable() -> None:
    """The identity provider may create it tomorrow. Refusing would make onboarding a department a
    two-step dance across two systems."""
    admin = _user("a", "use-case-admin")
    client = _client(admin)
    _create(client, "uc-a")

    assert (
        client.post(f"{BASE}uc-a/groups/", {"group_path": "/not/yet"}, format="json").status_code
        == 201
    )


def test_granting_the_same_group_twice_changes_the_role_rather_than_adding_a_row() -> None:
    admin = _user("a", "use-case-admin")
    client = _client(admin)
    _create(client, "uc-a")
    client.post(f"{BASE}uc-a/groups/", {"group_path": GROUP, "role": "user"}, format="json")
    client.post(f"{BASE}uc-a/groups/", {"group_path": GROUP, "role": "admin"}, format="json")

    grants = client.get(f"{BASE}uc-a/groups/").data
    assert len(grants) == 1
    assert grants[0]["role"] == "admin"


# ---- what a grant actually does ------------------------------------------------------------


def test_a_person_in_a_granted_group_can_see_the_use_case_without_any_row_naming_them() -> None:
    """The whole point of the feature."""
    owner = _user("a", "use-case-admin")
    _create(_client(owner), "uc-a")
    _client(owner).post(f"{BASE}uc-a/groups/", {"group_path": GROUP}, format="json")

    member = _user("b", "use-case-user", groups=(GROUP,))
    listed = _client(member).get(BASE).data["results"]

    assert [row["slug"] for row in listed] == ["uc-a"]


def test_a_person_in_no_granted_group_still_sees_nothing() -> None:
    owner = _user("a", "use-case-admin")
    _create(_client(owner), "uc-a")
    _client(owner).post(f"{BASE}uc-a/groups/", {"group_path": GROUP}, format="json")

    outsider = _user("b", "use-case-user", groups=("/ai/vertrieb",))

    assert _client(outsider).get(BASE).data["results"] == []


def test_an_admin_grant_lets_the_group_change_the_use_case() -> None:
    owner = _user("a", "use-case-admin")
    _create(_client(owner), "uc-a")
    _client(owner).post(
        f"{BASE}uc-a/groups/", {"group_path": GROUP, "role": "admin"}, format="json"
    )

    member = _user("b", "use-case-admin", groups=(GROUP,))
    response = _client(member).patch(f"{BASE}uc-a/", {"name": "renamed"}, format="json")

    assert response.status_code == 200


def test_a_user_grant_does_not() -> None:
    """`user` may use it and read the figures; changing it is what `admin` is for."""
    owner = _user("a", "use-case-admin")
    _create(_client(owner), "uc-a")
    _client(owner).post(f"{BASE}uc-a/groups/", {"group_path": GROUP, "role": "user"}, format="json")

    member = _user("b", "use-case-admin", groups=(GROUP,))

    assert (
        _client(member).patch(f"{BASE}uc-a/", {"name": "renamed"}, format="json").status_code == 403
    )


def test_lowering_a_grant_actually_lowers_it() -> None:
    """A demotion that demotes nothing is worse than none, because it reads as done."""
    owner = _user("a", "use-case-admin")
    _create(_client(owner), "uc-a")
    _client(owner).post(
        f"{BASE}uc-a/groups/", {"group_path": GROUP, "role": "admin"}, format="json"
    )
    _client(owner).post(f"{BASE}uc-a/groups/", {"group_path": GROUP, "role": "user"}, format="json")

    member = _user("b", "use-case-admin", groups=(GROUP,))

    assert _client(member).patch(f"{BASE}uc-a/", {"name": "x"}, format="json").status_code == 403


def test_the_console_reports_the_permission_the_server_would_enforce() -> None:
    """`FRD-206`'s agreement rule, now over a group grant: the object says what this caller may do,
    and the answer has to match what the request would return."""
    owner = _user("a", "use-case-admin")
    _create(_client(owner), "uc-a")
    _client(owner).post(
        f"{BASE}uc-a/groups/", {"group_path": GROUP, "role": "admin"}, format="json"
    )

    member = _user("b", "use-case-admin", groups=(GROUP,))
    reported = _client(member).get(f"{BASE}uc-a/").data["permissions"]

    assert reported["can_manage"] is True
    assert reported["is_member"] is True
    attempted = _client(member).patch(f"{BASE}uc-a/", {"name": "y"}, format="json")
    assert (attempted.status_code == 200) == reported["can_admin"]


# ---- revoking ------------------------------------------------------------------------------


def test_revoking_a_group_takes_its_access_away() -> None:
    owner = _user("a", "use-case-admin")
    _create(_client(owner), "uc-a")
    _client(owner).post(f"{BASE}uc-a/groups/", {"group_path": GROUP}, format="json")
    member = _user("b", "use-case-user", groups=(GROUP,))
    assert _client(member).get(BASE).data["count"] == 1

    _client(owner).delete(f"{BASE}uc-a/groups/revoke/?group_path={GROUP}")

    assert _client(member).get(BASE).data["count"] == 0


def test_revoking_a_group_leaves_a_direct_grant_intact() -> None:
    """`FRD-209` FR-5. Revoking one route must not silently close another."""
    owner = _user("a", "use-case-admin")
    _create(_client(owner), "uc-a")
    _client(owner).post(f"{BASE}uc-a/groups/", {"group_path": GROUP}, format="json")
    member = _user("b", "use-case-user", groups=(GROUP,))
    _client(owner).post(f"{BASE}uc-a/members/", {"username": "b", "role": "user"}, format="json")

    _client(owner).delete(f"{BASE}uc-a/groups/revoke/?group_path={GROUP}")

    assert _client(member).get(BASE).data["count"] == 1


def test_revoking_something_that_was_never_granted_says_so() -> None:
    owner = _user("a", "use-case-admin")
    _create(_client(owner), "uc-a")

    response = _client(owner).delete(f"{BASE}uc-a/groups/revoke/?group_path=/nope")

    assert response.status_code == 400


def test_a_reader_may_see_who_has_access_but_not_change_it() -> None:
    """Who can reach a use case is not a secret from its own members — and hiding it makes "why
    can that person call this" unanswerable without a database."""
    owner = _user("a", "use-case-admin")
    _create(_client(owner), "uc-a")
    _client(owner).post(f"{BASE}uc-a/groups/", {"group_path": GROUP}, format="json")

    member = _user("b", "use-case-user", groups=(GROUP,))
    assert _client(member).get(f"{BASE}uc-a/groups/").status_code == 200
    assert (
        _client(member).post(f"{BASE}uc-a/groups/", {"group_path": "/x"}, format="json").status_code
        == 403
    )


# ---- how many people it reaches --------------------------------------------------------------


def test_a_grant_reaching_nobody_is_visible_as_such() -> None:
    """A path that matches nobody is silently inert; an access list that showed it identically to
    a working one could not be audited (`FRD-209` FR-8)."""
    owner = _user("a", "use-case-admin")
    _create(_client(owner), "uc-a")
    _client(owner).post(f"{BASE}uc-a/groups/", {"group_path": "/nobody/here"}, format="json")

    assert _client(owner).get(f"{BASE}uc-a/groups/").data[0]["reaches"] == 0


def test_the_count_is_of_people_management_has_seen() -> None:
    owner = _user("a", "use-case-admin")
    _create(_client(owner), "uc-a")
    _client(owner).post(f"{BASE}uc-a/groups/", {"group_path": GROUP}, format="json")
    _user("b", "use-case-user", groups=(GROUP,))
    _user("c", "use-case-user", groups=(GROUP,))

    assert _client(owner).get(f"{BASE}uc-a/groups/").data[0]["reaches"] == 2


# ---- the group sync itself ---------------------------------------------------------------


def test_leaving_a_group_in_keycloak_takes_the_access_away_on_the_next_token() -> None:
    """The identity provider stays the source of truth — this is the failure the feature exists to
    prevent: an access list that only ever grows."""
    owner = _user("a", "use-case-admin")
    _create(_client(owner), "uc-a")
    _client(owner).post(f"{BASE}uc-a/groups/", {"group_path": GROUP}, format="json")
    member = _user("b", "use-case-user", groups=(GROUP,))
    assert _client(member).get(BASE).data["count"] == 1

    sync_user_groups(member, {"groups": []})

    assert _client(member).get(BASE).data["count"] == 0


def test_a_realm_group_named_after_a_role_does_not_hand_out_the_role() -> None:
    """Mirror groups are prefixed precisely so a realm can have a group called `it-security`."""
    user = _user("a", groups=("/it-security",))

    assert not user.groups.filter(name="it-security").exists()
    assert user.groups.filter(name=django_group_name("/it-security")).exists()
    # …and the role predicate does not see it.
    from aira_management.rbac import role_slugs

    assert role_slugs(user) == set()


def test_a_malformed_groups_claim_grants_nothing_rather_than_failing() -> None:
    """A token whose claim is the wrong shape is a token with no group access, which is the safe
    reading. Failing authentication over it would let a realm misconfiguration lock everyone out."""
    user = _user("a")
    sync_user_groups(user, {"groups": "not-a-list"})
    sync_user_groups(user, {"groups": [None, 42, "/ok"]})

    assert user.groups.filter(name=django_group_name("/ok")).exists()


def test_adding_a_person_grants_the_permission_it_promises() -> None:
    """A direct grant still works, and still means what it says.

    Re-covered here after the console's member form moved into the access panel and took its tests
    with it — the mutation harness noticed the property had stopped being defended, which is
    exactly what it is for. Both kinds of grant go through the same `_grant`, so one of them
    silently stopping would take the other with it.
    """
    owner = _user("a", "use-case-admin")
    client = _client(owner)
    _create(client, "uc-a")
    added = _user("b", "use-case-user")

    response = client.post(f"{BASE}uc-a/members/", {"username": "b", "role": "user"}, format="json")

    assert response.status_code == 201
    assert [row["slug"] for row in _client(added).get(BASE).data["results"]] == ["uc-a"]


def test_a_direct_admin_grant_may_change_the_use_case() -> None:
    """The other half of what `_grant` promises: `admin` is not the same as `user`."""
    owner = _user("a", "use-case-admin")
    client = _client(owner)
    _create(client, "uc-a")
    added = _user("b", "use-case-admin")
    client.post(f"{BASE}uc-a/members/", {"username": "b", "role": "admin"}, format="json")

    assert _client(added).patch(f"{BASE}uc-a/", {"name": "x"}, format="json").status_code == 200


def test_removing_a_person_takes_the_permission_away() -> None:
    owner = _user("a", "use-case-admin")
    client = _client(owner)
    _create(client, "uc-a")
    added = _user("b", "use-case-user")
    client.post(f"{BASE}uc-a/members/", {"username": "b", "role": "user"}, format="json")
    assert _client(added).get(BASE).data["count"] == 1

    client.delete(f"{BASE}uc-a/members/b/")

    assert _client(added).get(BASE).data["count"] == 0


def test_deleting_the_use_case_takes_its_grants_with_it() -> None:
    owner = _user("a", "use-case-admin")
    client = _client(owner)
    _create(client, "uc-a")
    client.post(f"{BASE}uc-a/groups/", {"group_path": GROUP}, format="json")

    client.delete(f"{BASE}uc-a/")

    assert not UseCaseGroupGrant.objects.filter(use_case__slug="uc-a").exists()
    assert not UseCase.objects.filter(slug="uc-a").exists()
