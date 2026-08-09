"""The shared role definition (ADR-0009).

Both services read this one list. The tests are about what "governance" means and about the
robustness of reading it from a token claim, because a wrong answer here is an authorization
answer.
"""

from __future__ import annotations

import pytest

from aira_common.roles import (
    ALL_ROLES,
    GOVERNANCE_ROLES,
    Role,
    RoleMappingError,
    is_governance,
    parse_role_groups,
    roles_from_groups,
)


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


def test_stopping_traffic_is_a_narrower_permission_than_seeing_it() -> None:
    """Found by a live round asking both planes the same question and getting different answers.

    The gateway guarded its kill switch with `has_oversight` — a *visibility* predicate — so
    `it-steuerung` could stop traffic there while Management refused it a global rule. PRD §154
    gives that role every figure and **no write anywhere**.
    """
    from aira_common.roles import has_oversight, may_act_on_incidents

    assert has_oversight(["it-steuerung"])
    assert not may_act_on_incidents(["it-steuerung"]), "a role that may only look can stop traffic"

    for role in ("it-security", "global-admin"):
        assert may_act_on_incidents([role]), role

    for role in ("use-case-admin", "use-case-user"):
        assert not may_act_on_incidents([role]), role


def test_the_incident_set_is_a_subset_of_the_oversight_set() -> None:
    """Anybody who may stop traffic may also see it. The reverse is what was wrong."""
    from aira_common.roles import INCIDENT_ROLES, OVERSIGHT_ROLES

    assert INCIDENT_ROLES < OVERSIGHT_ROLES


# ---- where a role comes from (ADR-0017) ---------------------------------------------------


def test_a_group_confers_the_role_it_is_mapped_to() -> None:
    mapping = parse_role_groups("global-admin=/aira/global-admins")
    assert roles_from_groups(["/aira/global-admins", "/use-cases/x"], mapping) == ("global-admin",)


def test_a_caller_in_none_of_the_groups_holds_no_role() -> None:
    """The whole point of the change: a role is held through a group and through nothing else.
    A realm role on the same token is not consulted and cannot appear here."""
    mapping = parse_role_groups("global-admin=/aira/global-admins")
    assert roles_from_groups(["/use-cases/x"], mapping) == ()


def test_a_role_the_configuration_does_not_name_is_held_by_nobody() -> None:
    mapping = parse_role_groups("global-admin=/aira/global-admins")
    assert roles_from_groups(["/aira/it-security"], mapping) == ()


def test_several_groups_may_confer_one_role() -> None:
    mapping = parse_role_groups("it-security=/aira/sec,/it/security-team")
    assert roles_from_groups(["/it/security-team"], mapping) == ("it-security",)


def test_two_entries_for_one_role_are_merged_rather_than_the_second_winning() -> None:
    mapping = parse_role_groups("it-security=/a/one;it-security=/a/two")
    assert mapping[Role.IT_SECURITY] == ("/a/one", "/a/two")


def test_the_match_is_exact_and_never_a_prefix() -> None:
    """`/aira/global-admins-readonly` starts with the configured path and must confer nothing.
    A prefix match here would hand an installation's highest role to a group named nearby."""
    mapping = parse_role_groups("global-admin=/aira/global-admins")
    assert roles_from_groups(["/aira/global-admins-readonly"], mapping) == ()
    assert roles_from_groups(["/aira/global-admins/sub"], mapping) == ()


def test_an_unknown_role_name_refuses_rather_than_granting_nothing_quietly() -> None:
    with pytest.raises(RoleMappingError) as excinfo:
        parse_role_groups("gloabl-admin=/aira/global-admins")
    # Names the offending value: a mapping that fails without saying which entry is one somebody
    # fixes by bisection.
    assert "gloabl-admin" in str(excinfo.value)


def test_a_use_case_role_cannot_be_conferred_by_a_group() -> None:
    """Mapping `use-case-admin` to a group would grant every use case at once, which is the
    blanket authority the object grants exist to avoid (`FRD-209`)."""
    with pytest.raises(RoleMappingError) as excinfo:
        parse_role_groups("use-case-admin=/aira/uc-admins")
    assert "single use case" in str(excinfo.value)


def test_a_relative_path_and_the_realm_root_are_refused() -> None:
    """Keycloak emits full paths. `/` matches nothing any token carries, so a mapping naming it
    is a rule that can never fire — the same reason `FRD-209` refuses it as a grant."""
    with pytest.raises(RoleMappingError):
        parse_role_groups("global-admin=aira/global-admins")
    with pytest.raises(RoleMappingError):
        parse_role_groups("global-admin=/")


def test_an_entry_without_a_group_is_refused() -> None:
    with pytest.raises(RoleMappingError):
        parse_role_groups("global-admin=")
    with pytest.raises(RoleMappingError):
        parse_role_groups("global-admin")


def test_an_empty_mapping_is_empty_rather_than_an_error() -> None:
    """Parsing nothing is not a failure — refusing to *boot* without a global-admin group is a
    decision the service makes, and it belongs where the service starts, not in a parser."""
    assert parse_role_groups("") == {}
    assert parse_role_groups("  ;  ") == {}
