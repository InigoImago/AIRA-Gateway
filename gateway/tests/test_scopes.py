"""Who a limit or a budget applies to (`aira_gateway.scopes`).

The rule used to be written twice — once for budgets, once for rate limits — in two slightly
different shapes. These tests are on the single copy, so a third scope has one place to be added
and one place to be checked.
"""

from __future__ import annotations

from aira_gateway.scopes import Scope


def test_a_use_case_scope_binds_every_caller() -> None:
    scope = Scope.applying(scope="use_case", use_case="uc", subject="", caller="alice")
    assert scope == Scope("uc")
    assert Scope.applying(scope="use_case", use_case="uc", subject="", caller=None) == Scope("uc")


def test_a_member_scope_binds_only_its_own_subject() -> None:
    assert Scope.applying(scope="member", use_case="uc", subject="alice", caller="alice") == Scope(
        "uc", "alice"
    )
    assert Scope.applying(scope="member", use_case="uc", subject="alice", caller="bob") is None


def test_a_member_scope_binds_nobody_when_the_request_has_no_subject() -> None:
    """An unattributed request must not accidentally match a member row — matching would apply
    somebody else's allowance to it."""
    assert Scope.applying(scope="member", use_case="uc", subject="alice", caller=None) is None
    assert Scope.applying(scope="member", use_case="uc", subject="", caller=None) is None


def test_an_unknown_scope_binds_nobody() -> None:
    """Forward compatibility: a scope a newer Management knows about must be ignored here rather
    than applied to the wrong caller."""
    assert Scope.applying(scope="api_key", use_case="uc", subject="x", caller="x") is None


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
