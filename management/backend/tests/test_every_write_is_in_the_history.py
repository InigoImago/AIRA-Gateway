"""Every write Management's API offers is in the configuration history, or says why not (`FRD-632`).

The history records the configuration events every change sends to the gateway. A write that sends
none — a new endpoint whose author forgot the event, or one that changes something only Management
reads — would change configuration without a trace. So the routes are read from Django's resolver,
and every write method of every one must be in :data:`RECORDED` or in :data:`EXEMPT` with its
reason: a route in neither fails here, by name.

A hand-written list with no counterpart is a list that goes stale; this one has the resolver.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from django.urls import URLPattern, URLResolver, get_resolver

WRITES = frozenset({"post", "put", "patch", "delete"})

#: `(view, action)` whose every change sends a configuration event, and so is a history row.
RECORDED = frozenset(
    {
        ("UseCaseViewSet", "create"),
        ("UseCaseViewSet", "update"),
        ("UseCaseViewSet", "partial_update"),
        ("UseCaseViewSet", "destroy"),
        ("UseCaseViewSet", "purge"),
        ("UseCaseViewSet", "members"),
        ("UseCaseViewSet", "remove_member"),
        ("UseCaseViewSet", "group_grants"),
        ("UseCaseViewSet", "revoke_group_grant"),
        ("UseCaseViewSet", "api_keys"),
        ("UseCaseViewSet", "revoke_api_key"),
        ("UseCaseViewSet", "budgets"),
        ("UseCaseViewSet", "delete_budget"),
        ("UseCaseViewSet", "rate_limits"),
        ("UseCaseViewSet", "delete_rate_limit"),
        ("UseCaseViewSet", "anomaly_rules"),
        ("UseCaseViewSet", "delete_anomaly_rule"),
        ("UseCaseViewSet", "pipeline"),
        ("ModelViewSet", "create"),
        ("ModelViewSet", "update"),
        ("ModelViewSet", "partial_update"),
        ("ModelViewSet", "destroy"),
        ("InstallationBudgetViewSet", "create"),
        ("InstallationBudgetViewSet", "destroy"),
        ("AnomalyRuleViewSet", "create"),
        ("AnomalyRuleViewSet", "update"),
        ("AnomalyRuleViewSet", "partial_update"),
        ("AnomalyRuleViewSet", "destroy"),
        ("RoleViewSet", "create"),
        ("RoleViewSet", "partial_update"),
        ("RoleViewSet", "destroy"),
    }
)

_TESTS = (
    "a test question, a run or a rating: content about how models answer, kept with its author "
    "on its own row; it configures nothing the gateway enforces"
)

#: `(view, action)` that writes and is no configuration change, and why.
EXEMPT: dict[tuple[str, str], str] = {
    ("AcknowledgementView", "post"): "a person's own acknowledgement of the privacy notice",
    ("DirectorySearchView", "post"): (
        "a search, sent as POST so the term stays out of the address; it changes nothing"
    ),
    ("RoleViewSet", "check_group"): "asks whether a group could be bound; it changes nothing",
    **{
        (view, action): _TESTS
        for view in ("TestCaseViewSet", "TestRunViewSet", "TestResultViewSet")
        for action in ("create", "update", "partial_update", "destroy")
    },
    ("TestRunViewSet", "finish"): _TESTS,
}


def _writes(patterns: list[Any], prefix: str = "") -> Iterator[tuple[str, str, str]]:
    """``(route, view, action)`` for every write method the URL configuration routes."""
    for pattern in patterns:
        if isinstance(pattern, URLResolver):
            yield from _writes(pattern.url_patterns, prefix + str(pattern.pattern))
            continue
        if not isinstance(pattern, URLPattern):
            continue
        callback = pattern.callback
        view = getattr(callback, "cls", None) or getattr(callback, "view_class", None)
        if view is None:
            continue
        actions = getattr(callback, "actions", None)
        if actions:
            names = {method: name for method, name in actions.items() if method in WRITES}
        else:
            names = {method: method for method in WRITES if hasattr(view, method)}
        for name in names.values():
            yield prefix + str(pattern.pattern), view.__name__, name


@pytest.fixture(scope="module")
def writes() -> set[tuple[str, str]]:
    return {(view, action) for _route, view, action in _writes(get_resolver().url_patterns)}


def test_every_write_is_recorded_or_says_why_not(writes: set[tuple[str, str]]) -> None:
    unaccounted = sorted(writes - RECORDED - set(EXEMPT))
    assert not unaccounted, (
        f"{unaccounted} write and are neither recorded nor exempt. A change to configuration sends "
        "its event through `usecases.events.emit`, which is what puts it in the history — then "
        "add it to RECORDED; if it configures nothing, add it to EXEMPT with the reason."
    )


def test_nothing_listed_is_a_route_that_is_gone(writes: set[tuple[str, str]]) -> None:
    """An entry for a route that no longer exists is a promise about nothing."""
    assert sorted((RECORDED | set(EXEMPT)) - writes) == []


def test_no_write_is_both(writes: set[tuple[str, str]]) -> None:
    del writes
    assert not (RECORDED & set(EXEMPT))
