"""A route is written as the pattern a reader filters by, whichever framework matched it (`FRD-631`
FR-5): parameters as `{name}`, a leading `/`, nothing of a regular expression."""

from __future__ import annotations

import pytest

from aira_common.logging.access import readable_route, route_of, write_access_line


@pytest.mark.parametrize(
    ("route", "written"),
    [
        # A router's, as Django joins it below `api/v1/`.
        (
            "api/v1/use-cases/(?P<slug>[^/.]+)/groups/$",
            "/api/v1/use-cases/{slug}/groups/",
        ),
        # Its format suffix: an escaped dot, a second parameter, an optional slash.
        (
            "api/v1/use-cases/(?P<slug>[^/.]+)\\.(?P<format>[a-z0-9]+)/?$",
            "/api/v1/use-cases/{slug}.{format}",
        ),
        # A `path()` without a leading slash, and one with converters.
        ("api/v1/directory/search", "/api/v1/directory/search"),
        (
            "api/v1/use-cases/<slug:slug>/members/<str:username>/",
            "/api/v1/use-cases/{slug}/members/{username}/",
        ),
        ("members/<username>/", "/members/{username}/"),
        # A top-level expression keeps its anchor in Django's join; it is not written.
        ("^healthz$", "/healthz"),
        # The gateway's own spelling is already this one, and a converter becomes its name.
        ("/v1beta/usage/{use_case}", "/v1beta/usage/{use_case}"),
        ("/files/{path:path}", "/files/{path}"),
    ],
)
def test_a_route_is_written_as_its_pattern(route: str, written: str) -> None:
    assert readable_route(route) == written


@pytest.mark.parametrize(
    "group",
    [
        # A `)` in a class closes nothing, nor one behind a backslash, nor a class's first `]`.
        "(?P<name>[^)]+)",
        "(?P<name>a\\)b)",
        "(?P<name>[]a])",
        "(?P<name>[^]a])",
        # A group inside the parameter's is the parameter's.
        "(?P<name>(?:x|y)+)",
    ],
)
def test_whatever_a_parameter_matches_it_is_written_as_its_name(group: str) -> None:
    assert readable_route(f"api/{group}/tail/$") == "/api/{name}/tail/"


def test_a_route_written_once_is_written_the_same_again() -> None:
    once = readable_route("api/v1/roles/(?P<slug>[-a-z0-9_]+)/$")

    assert readable_route(once) == once == "/api/v1/roles/{slug}/"


def test_no_route_is_no_route() -> None:
    assert readable_route(None) is None
    assert readable_route("") is None


def test_an_unfinished_expression_is_written_as_far_as_it_goes() -> None:
    """A pattern the scanner cannot close must not cost the line, nor loop."""
    assert readable_route("api/(?P<name") == "/api/(?P<name"
    assert readable_route("api/(?P<name>[a-z") == "/api/{name}"


def test_the_access_line_carries_the_route_as_given(monkeypatch: pytest.MonkeyPatch) -> None:
    """The writer takes a route as the plane hands it over: each plane's `route_of` spells it."""
    import structlog

    import aira_common.logging.access as access

    # A logger first used now: one an earlier test first used keeps the configuration of then.
    monkeypatch.setattr(access, "_log", structlog.get_logger("aira.access"))
    with structlog.testing.capture_logs() as logs:
        write_access_line(method="GET", status=200, duration_ms=1.0, route="/a/{b}", path="/a/c")

    (line,) = logs
    assert line["route"] == "/a/{b}" and "path" not in line


def test_a_matched_starlette_route_is_written_the_same_way() -> None:
    class Matched:
        path_format = "/files/{path:path}"

    assert route_of({"route": Matched()}) == "/files/{path}"
    assert route_of({}) is None
