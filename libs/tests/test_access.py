"""The vocabulary of who may reach a use case (`FRD-209`).

One definition, read by both planes. These are the rules that would otherwise be restated in two
places and drift — which is exactly what happened to membership before this feature existed.
"""

from __future__ import annotations

import pytest

from aira_common.access import (
    GrantRole,
    SubjectKind,
    resolve,
    strongest,
    usecases_from_group_paths,
)

# ---- the strongest role wins -------------------------------------------------------------


def test_admin_beats_user() -> None:
    """A caller granted both ways is an admin.

    The alternative is an access decision that depends on which row happened to be read first,
    which is not a decision anybody can review.
    """
    assert strongest(["user", "admin"]) == "admin"
    assert strongest(["admin", "user"]) == "admin"


def test_one_role_is_that_role() -> None:
    assert strongest(["user"]) == "user"
    assert strongest(["admin"]) == "admin"


def test_nothing_at_all_is_the_weakest() -> None:
    assert strongest([]) == "user"


def test_a_role_this_version_has_never_heard_of_is_not_assumed_to_be_powerful() -> None:
    """ "Absence of information is not permission" — the same rule the model catalog keeps.

    A future role arriving from a newer Management must not be read as stronger than the ones
    this version understands, because it might be weaker.
    """
    assert strongest(["superuser"]) == "user"
    assert strongest(["superuser", "admin"]) == "admin"
    assert strongest(["superuser", "user"]) == "user"


# ---- the /use-cases/<slug> convention ----------------------------------------------------


def test_the_old_convention_still_grants() -> None:
    """`FRD-102`'s naming convention keeps working — it is one route in, not the only one.

    The dev realm and the demo depend on it, and it is a perfectly good way to run a small
    installation: nothing to distribute, resolvable from the token alone.
    """
    assert usecases_from_group_paths(["/use-cases/demo-uc"]) == ("demo-uc",)


def test_a_group_that_is_not_a_use_case_grants_nothing_by_convention() -> None:
    assert usecases_from_group_paths(["/ai/kundenservice", "/abteilungen/vertrieb"]) == ()


def test_a_trailing_slash_and_a_nested_path_still_resolve() -> None:
    # Keycloak reports a subgroup member as carrying the parent path too, and a realm that nests
    # one level deeper should not silently stop granting.
    assert usecases_from_group_paths(["/use-cases/demo-uc/"]) == ("demo-uc",)
    assert usecases_from_group_paths(["/use-cases/team/demo-uc"]) == ("demo-uc",)


def test_the_same_use_case_twice_is_one_use_case() -> None:
    assert usecases_from_group_paths(["/use-cases/a", "/use-cases/a"]) == ("a",)


def test_the_bare_prefix_names_no_use_case() -> None:
    """`/use-cases/` is the parent group, not a grant.

    A realm that puts every use case under one parent reports the parent path too, so this arrives
    on ordinary tokens rather than only on malformed ones — and a use case whose slug were the
    empty string is a request nothing could attribute. Kept from the gateway's own copy of this
    function when that copy was removed: the case was only ever asserted there.
    """
    assert usecases_from_group_paths(["/use-cases/"]) == ()
    assert usecases_from_group_paths([]) == ()


# ---- resolution --------------------------------------------------------------------------


def test_a_group_grant_reaches_whoever_is_in_the_group() -> None:
    """The point of the whole feature: no row names this person."""
    assert resolve(["/ai/kundenservice"], [("/ai/kundenservice", "uc-a", "user")]) == {
        "uc-a": "user"
    }


def test_a_grant_on_a_group_nobody_holds_reaches_nobody() -> None:
    assert resolve(["/ai/other"], [("/ai/kundenservice", "uc-a", "admin")]) == {}


def test_the_two_routes_are_a_union_not_a_precedence() -> None:
    """Being a member twice over is being a member."""
    resolved = resolve(
        ["/use-cases/uc-a", "/ai/kundenservice"],
        [("/ai/kundenservice", "uc-b", "user")],
    )
    assert resolved == {"uc-a": "user", "uc-b": "user"}


def test_the_strongest_route_decides_the_role() -> None:
    # The convention grants ordinary membership; an explicit grant can raise it. Reading them in
    # the other order would silently demote an administrator.
    resolved = resolve(
        ["/use-cases/uc-a", "/ai/leads"],
        [("/ai/leads", "uc-a", "admin")],
    )
    assert resolved == {"uc-a": "admin"}


def test_a_direct_grant_counts_too() -> None:
    assert resolve([], [], [("uc-a", "admin")]) == {"uc-a": "admin"}


def test_a_direct_grant_and_a_group_grant_take_the_stronger() -> None:
    resolved = resolve(
        ["/ai/kundenservice"],
        [("/ai/kundenservice", "uc-a", "user")],
        [("uc-a", "admin")],
    )
    assert resolved == {"uc-a": "admin"}


def test_no_groups_and_no_grants_is_no_access() -> None:
    assert resolve([], []) == {}


def test_one_group_can_grant_several_use_cases() -> None:
    """A department reaching three use cases is one group and three grants, not three groups."""
    resolved = resolve(
        ["/ai/kundenservice"],
        [
            ("/ai/kundenservice", "uc-a", "user"),
            ("/ai/kundenservice", "uc-b", "admin"),
        ],
    )
    assert resolved == {"uc-a": "user", "uc-b": "admin"}


@pytest.mark.parametrize("role", list(GrantRole))
def test_every_role_survives_a_round_trip(role: GrantRole) -> None:
    assert strongest([str(role)]) == str(role)


def test_the_two_kinds_are_named_the_same_on_both_planes() -> None:
    # A slug typed twice is a slug that disagrees with itself eventually.
    assert str(SubjectKind.GROUP) == "group"
    assert str(SubjectKind.USER) == "user"


def test_a_group_that_is_not_a_path_is_skipped_rather_than_raising() -> None:
    """A `groups` claim is not a type (`FRD-613`).

    A signed token is trustworthy about who issued it and says nothing about the shape of its
    claims: a realm mapper configured to emit group *objects*, or a numeric id, is a
    misconfiguration — and this answered it with `AttributeError`, raised inside token validation,
    which is a **500 on every request that caller makes** rather than a role they do not get.

    Asked of this function directly and not through a validator. Both planes narrow the claim
    before calling, so a test one layer up passes with the guard removed — which is exactly what
    `make mutants` reported: the property looked defended and the defence was somebody else's.
    """
    assert usecases_from_group_paths([7, None, {"path": "/use-cases/uc-b"}, "/use-cases/uc-a"]) == (
        "uc-a",
    )


def test_a_grant_row_is_matched_by_the_paths_a_token_carries() -> None:
    """The other half of the same tolerance: `resolve` builds a set from whatever it is given, and
    a non-string in it simply matches no grant."""
    assert resolve([7, "/ai/x"], [("/ai/x", "uc-a", "admin")]) == {"uc-a": "admin"}
