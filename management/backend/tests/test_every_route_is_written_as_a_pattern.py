"""A line names the route a request matched as a pattern a reader can filter by (`FRD-631` FR-5).

Django writes a router's route as the regular expression it matches —
`api/v1/use-cases/(?P<slug>[^/.]+)/groups/$` — and a `path()` without a leading slash, while the
gateway writes `/v1beta/usage/{use_case}`. A log store's query by route needed two spellings and an
escape for every bracket. Every route this plane can match is written as the gateway writes one:
parameters as `{name}`, a leading `/`, nothing of the expression left.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from pathlib import Path

import pytest
import structlog
from aira_management.apps.api import exceptions as api_exceptions
from aira_management.apps.api.access import route_of
from django.urls import URLPattern, URLResolver, get_resolver
from rest_framework.test import APIClient

from aira_common.logging.access import readable_route

pytestmark = pytest.mark.django_db

SOURCE = Path(__file__).resolve().parents[1] / "src" / "aira_management"

#: What a route written for a reader may hold: the path's own characters and `{name}`.
READABLE = re.compile(r"/(?:[A-Za-z0-9._~-]|\{[a-z_][a-z0-9_]*\}|/)*")


def _routes(patterns: list[URLPattern | URLResolver], prefix: str = "") -> Iterator[str]:
    """Every route the resolver can answer with, joined as Django joins a match's route."""
    for pattern in patterns:
        route = URLResolver._join_route(prefix, str(pattern.pattern))  # noqa: SLF001
        if isinstance(pattern, URLResolver):
            yield from _routes(pattern.url_patterns, route)
        else:
            yield route


def test_every_registered_route_is_written_without_its_expression() -> None:
    routes = sorted(set(_routes(get_resolver().url_patterns)))
    assert any("(?P<" in route for route in routes), "the router's routes are expressions"

    unreadable = {
        route: readable_route(route)
        for route in routes
        if not READABLE.fullmatch(readable_route(route) or "")
    }

    assert not unreadable, f"routes a line would write with an expression in them: {unreadable}"


def test_a_routers_route_and_a_paths_are_written_alike() -> None:
    routes = {readable_route(route) for route in _routes(get_resolver().url_patterns)}

    assert "/api/v1/use-cases/{slug}/groups/" in routes
    assert "/api/v1/directory/search" in routes
    assert "/api/v1/use-cases/{slug}/members/{username}/" in routes


def _fresh(monkeypatch: pytest.MonkeyPatch) -> None:
    """Loggers first used now: one an earlier test first used keeps the configuration of then."""
    import aira_common.logging.access as access
    import aira_common.logging.refusals as refusals

    monkeypatch.setattr(access, "_log", structlog.get_logger("aira.access"))
    monkeypatch.setattr(refusals, "_log", structlog.get_logger("aira.security"))
    monkeypatch.setattr(api_exceptions, "_log", structlog.get_logger("aira_management"))


def test_a_refused_request_to_a_routers_route_names_its_pattern(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _fresh(monkeypatch)

    with structlog.testing.capture_logs() as logs:
        response = APIClient().get(
            "/api/v1/use-cases/uc-x/groups/", HTTP_AUTHORIZATION="Bearer whatever"
        )

    assert response.status_code == 401
    lines = [entry for entry in logs if entry["event"] in {"http_request", "auth_refused"}]
    assert {entry["event"] for entry in lines} == {"http_request", "auth_refused"}
    assert {entry["route"] for entry in lines} == {"/api/v1/use-cases/{slug}/groups/"}


def test_no_line_reads_a_route_but_through_the_one_helper() -> None:
    """A line that read `resolver_match.route` itself would write the expression again."""
    readers = [
        f"{path.relative_to(SOURCE)}:{number}"
        for path in SOURCE.rglob("*.py")
        for number, text in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if re.search(r"""["']route["']\s*,|\.route\b""", text)
        and path.name != "access.py"
        and "migrations" not in path.parts
    ]

    assert not readers, f"read a route without `route_of`: {readers}"


def test_without_a_match_there_is_no_route() -> None:
    class Unmatched:
        resolver_match = None

    assert route_of(Unmatched()) is None  # type: ignore[arg-type]
