"""The shared role definition (ADR-0009).

Both services read this one list. The tests are about what "governance" means and about the
robustness of reading it from a token claim, because a wrong answer here is an authorization
answer.
"""

from __future__ import annotations

from aira_common.roles import ALL_ROLES, GOVERNANCE_ROLES, Role, is_governance


def test_the_five_roles_are_the_ones_the_realm_defines() -> None:
    """Pinned rather than assumed: these names exist in the Keycloak realm and in the seed, so a
    rename here that is not made there silently stops matching anything."""
    assert {role.value for role in ALL_ROLES} == {
        "global-admin",
        "it-security",
        "it-steuerung",
        "use-case-admin",
        "use-case-user",
    }


def test_oversight_is_global_admin_and_it_steuerung_only() -> None:
    """IT Security is deliberately not here. Its console (FRD-502) has its own scoping with
    payload redaction; granting it the reporting view would hand it use-case figures it has not
    been given a reason to see."""
    assert frozenset({Role.GLOBAL_ADMIN, Role.IT_STEUERUNG}) == GOVERNANCE_ROLES


def test_a_governance_role_among_others_still_counts() -> None:
    assert is_governance(["use-case-user", "it-steuerung"]) is True


def test_use_case_roles_are_not_oversight() -> None:
    """A use-case admin administers their own use cases. Confusing the two would give every
    use-case admin a view of every other use case's spend."""
    assert is_governance(["use-case-admin", "use-case-user"]) is False
    assert is_governance([]) is False


def test_an_unknown_role_is_not_oversight() -> None:
    """A realm that grows a role this code has never heard of must not accidentally be treated
    as governance."""
    assert is_governance(["brand-new-role"]) is False
