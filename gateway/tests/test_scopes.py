"""Who a limit or a budget applies to (`aira_gateway.scopes`).

The rule used to be written twice — once for budgets, once for rate limits — in two slightly
different shapes. These tests are on the single copy, so a third scope has one place to be added
and one place to be checked.
"""

from __future__ import annotations

from aira_gateway.scopes import Scope


def test_a_use_case_scope_binds_every_caller() -> None:
    scope = Scope.applying(scope="use_case", use_case="uc", caller="alice")
    assert scope == Scope("uc")
    assert Scope.applying(scope="use_case", use_case="uc", caller=None) == Scope("uc")


def test_an_unknown_scope_binds_nobody() -> None:
    """Forward compatibility, and now also **backward**: a scope a newer Management knows about
    must be ignored rather than applied to the wrong caller — and so must `member`, which a
    *older* Management may still be sending while its own migration has not run.

    A row that binds nobody is the right answer for a scope this gateway no longer has. Both
    planes delete such rows; they do not delete them at the same instant.
    """
    assert Scope.applying(scope="api_key", use_case="uc", caller="x") is None
    assert Scope.applying(scope="member", use_case="uc", caller="alice") is None


def test_the_usage_key_shape_is_the_one_already_in_the_database() -> None:
    """This key is stored in `budget_usage`, so changing its shape would not lose the counters —
    it would stop finding them, and every budget would read as unspent."""
    assert Scope("uc").usage_key == "uc:uc"
    assert Scope("uc", "alice").usage_key == "member:uc:alice"


def test_every_bucket_of_one_use_case_shares_a_cluster_slot() -> None:
    """The all-or-nothing rate-limit decision is one multi-key script, and Redis Cluster refuses
    a script whose keys live on different nodes. The hash tag is what keeps that possible."""
    wide = Scope("uc").bucket_key
    narrow = Scope("uc", "alice").bucket_key

    assert wide == "rl:{uc}:uc"
    assert narrow == "rl:{uc}:member:alice"
    tag = lambda key: key[key.index("{") : key.index("}") + 1]  # noqa: E731
    assert tag(wide) == tag(narrow) == "{uc}"


def test_the_two_stores_do_not_share_a_key_space() -> None:
    """A budget counter and a rate-limit bucket for the same scope must never collide."""
    assert Scope("uc").usage_key != Scope("uc").bucket_key


def test_a_scope_names_itself_for_a_refusal() -> None:
    assert Scope("uc").label == "use case"
    assert Scope("uc", "alice").label == "member"


# == each member, individually (2026-08-11) ======================================================


def test_a_per_person_row_binds_whoever_turns_up() -> None:
    """One configured row, one counter per head — the answer to "everybody, but separately", and
    what an administrator wants far more often than naming individuals. It keeps applying to
    people who join afterwards.

    Not a convenience for `use_case`: that one is a **shared pot**, where the first caller to
    arrive can spend all of it. These are different governance decisions."""
    from aira_gateway.scopes import EACH_MEMBER, Scope

    for caller in ("alice", "bob"):
        scope = Scope.applying(scope=EACH_MEMBER, use_case="uc", caller=caller)
        assert scope is not None
        assert scope.member == caller

    # The stored key shape is unchanged by the removal of the named scope: a counter written
    # before it went keeps being found, which is the whole reason `usage_key` is not free to move.
    each = Scope.applying(scope=EACH_MEMBER, use_case="uc", caller="alice")
    assert each is not None
    assert each.usage_key == "member:uc:alice"


def test_a_per_person_row_binds_nobody_when_the_request_has_no_subject() -> None:
    """A request that carries no identity cannot be bounded per identity, and pretending otherwise
    would put every anonymous caller in one bucket named after nobody."""
    from aira_gateway.scopes import EACH_MEMBER, Scope

    assert Scope.applying(scope=EACH_MEMBER, use_case="uc", caller=None) is None
