"""`docs/API.md` names every route both planes serve, and no route that neither serves.

**Why this exists.** A reference is read in place of the code — by whoever scripts the control
plane, wires a monitor to a probe or reviews who may call what — and it is the copy nobody opens
until it matters, which is the worst moment to find it short (the reason
`test_configuration_reference_names_every_setting.py` exists). So it is compared with the routes the
code serves, in both directions, as every hand-written list in this repository is:

1. every route of either plane is named in the reference — and every route of a model surface in
   that surface's guide as well, which is the page a client developer reads;
2. every route the reference names is one a plane serves. A documented route that answers `404` is
   worse than a missing one: somebody scripts it, and the script fails in somebody else's terminal.

**Enumerated from the code, never from a list typed here.** The gateway's routes come from the
application `create_app` builds — locally *and* deployed, because the schema routes exist only in
the first — and Management's from Django's URL resolver. A route is a method and a path whose
parameters are all written `{}`, so `{slug}`, `<slug>`, `{model:path}` and a resolver's
`(?P<slug>[^/.]+)` compare equal. What the frameworks add on their own is left out by rule rather
than by name: `HEAD` and `OPTIONS`, and DRF's `.json` twin of every router route. The Gemini
surface's one verb route is counted once per verb it dispatches, because a client calls verbs.
"""

from __future__ import annotations

import functools
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from aira_gateway.config import GatewaySettings

ROOT = Path(__file__).resolve().parents[2]
REFERENCE = ROOT / "docs" / "API.md"

#: The reference's section for each plane. A route is looked for in its own plane's section, since
#: both planes serve `/healthz` and `/readyz`, and a page listing a probe once would otherwise pass
#: for both.
GATEWAY_SECTION = "## 2. The gateway"
MANAGEMENT_SECTION = "## 3. Management"

#: Each model surface — the package its routes live in — and the guide that documents it for a
#: client. Every route served from the package must be named in that guide as well.
GUIDES = {
    "aira_gateway.api.gemini": ROOT / "docs" / "MIGRATION-GEMINI.md",
    "aira_gateway.api.kira": ROOT / "docs" / "MIGRATION-KIRA.md",
}

#: Methods a framework answers by itself beside the ones a route declares: Starlette and Django add
#: `HEAD` to a `GET`, and DRF answers `OPTIONS` on every view. Nobody calls them as operations.
IMPLIED = frozenset({"HEAD", "OPTIONS"})

#: A gateway as a laptop builds it, and as a deployment does. The schema routes exist only in the
#: first (`create_app`); a route that existed only in the second would be missed by enumerating one.
#: Neither is started, so the deployed one's PostgreSQL is never dialled — and it carries none of
#: the conveniences a deployment refuses to start with (`security.unsafe_settings`).
LOCAL: dict[str, Any] = {"environment": "local", "test_database": True}
DEPLOYED: dict[str, Any] = {
    "environment": "production",
    "postgres_password": "not-the-published-one",
    "kafka_security_protocol": "SASL_SSL",
}

#: `(METHOD, path)`, the path normalised by `normalised`.
Route = tuple[str, str]

#: A path parameter however a framework or a page spells it: `{slug}`, `{model:path}`, `<slug>`.
_PARAMETER = re.compile(r"\{[^{}/]*\}|<[^<>/]*>")

#: A code span naming an operation in full: `` `GET /v1beta/traces` ``.
_OPERATION = re.compile(r"^(GET|POST|PUT|PATCH|DELETE) (/\S*)$")

#: A code span continuing the operation before it in the guides' shorthand —
#: `` `POST …:embedContent` · `:batchEmbedContents` `` and `` `GET …/health`, `/version-info` ``:
#: one verb, or one last path segment, and nothing between the two spans but separators.
_CONTINUATION = re.compile(r"^(:[A-Za-z]+|/[^/\s]+)$")
_SEPARATORS = re.compile(r"[\s,·]*")


def normalised(path: str) -> str:
    """``path`` as one spelling: every parameter `{}`, the query dropped."""
    return _PARAMETER.sub("{}", path.split("?", 1)[0])


# == the gateway ===================================================================================


@functools.cache
def _built(configuration: tuple[tuple[str, Any], ...]) -> dict[Route, Callable[..., Any]]:
    """Every route a gateway built with ``configuration`` serves, and the endpoint serving it.

    Read through `fastapi.routing.iter_route_contexts`, the framework's own walk: an included
    router stays nested behind an `_IncludedRouter` in `app.routes`, so a hand-rolled walk sees
    four documentation routes and nothing else — which `test_both_planes_have_routes_to_compare`
    would catch, and which `test_kira_envelope_everywhere.py` records happening once already.
    """
    from fastapi.routing import iter_route_contexts

    from aira_gateway.app import create_app

    app = create_app(GatewaySettings(**dict(configuration)))
    found: dict[Route, Callable[..., Any]] = {}
    for route in iter_route_contexts(app.routes):
        if route.path is None or route.endpoint is None:
            continue
        for method in set(route.methods or ()) - IMPLIED:
            found[(method, normalised(route.path))] = route.endpoint
    return found


def _gateway(settings: dict[str, Any]) -> dict[Route, Callable[..., Any]]:
    return _built(tuple(sorted(settings.items())))


def _gateway_routes() -> dict[Route, Callable[..., Any]]:
    """The routes of a local gateway and of a deployed one, together."""
    return {**_gateway(DEPLOYED), **_gateway(LOCAL)}


def _gateway_operations() -> dict[Route, Callable[..., Any]]:
    """The gateway's routes as a client calls them: the Gemini verb route once per verb.

    `POST /v1beta/models/{resource}` is one route that splits `model:verb` at the last colon and
    dispatches on the verb, so the operations a reader looks for are the verbs. They are read from
    the two sets the route dispatches on, not typed here.
    """
    from aira_gateway.api.gemini.routes import GENERATION_METHODS, generate
    from aira_gateway.api.serving import EMBEDDING_METHODS

    operations: dict[Route, Callable[..., Any]] = {}
    for (method, path), endpoint in _gateway_routes().items():
        if endpoint is generate:
            for verb in sorted(GENERATION_METHODS | EMBEDDING_METHODS):
                operations[(method, f"{path}:{verb}")] = endpoint
        else:
            operations[(method, path)] = endpoint
    return operations


# == Management ====================================================================================


def _django_path(pattern: str) -> str:
    """A resolver's pattern as the path a client sends.

    A named group, which may hold a character class with a `)` in it, becomes `{}`; the anchors
    every included regex carries go, and an escaped character stands for itself.
    """
    out: list[str] = []
    index = 0
    while index < len(pattern):
        if pattern.startswith("(?P<", index):
            depth, cursor = 0, index
            while cursor < len(pattern):
                char = pattern[cursor]
                if char == "\\":
                    cursor += 2
                    continue
                if char == "[":
                    cursor = pattern.index("]", cursor + 2)
                elif char == "(":
                    depth += 1
                elif char == ")":
                    depth -= 1
                    if depth == 0:
                        break
                cursor += 1
            out.append("{}")
            index = cursor + 1
            continue
        char = pattern[index]
        if char in "^$":
            index += 1
            continue
        if char == "\\" and index + 1 < len(pattern):
            out.append(pattern[index + 1])
            index += 2
            continue
        out.append(char)
        index += 1
    return "/" + "".join(out)


def _methods(callback: Any) -> set[str]:
    """What a view answers, as DRF's own schema generator decides it.

    A viewset answers the router's method map less what its class refuses at dispatch
    (`http_method_names` — runs and results have no `DELETE`); any other class-based view the
    handlers it defines. A plain function view answers every method; the two this code has are
    probes, and are the `GET` they are documented as.
    """
    view = getattr(callback, "cls", None) or getattr(callback, "view_class", None)
    actions = getattr(callback, "actions", None)
    if view is not None and actions is not None:
        allowed = {method for method in actions if method in view.http_method_names}
    elif view is not None:
        allowed = {method for method in view.http_method_names if hasattr(view, method)}
    else:
        allowed = {"get"}
    return {method.upper() for method in allowed} - IMPLIED


@functools.cache
def _management_operations() -> frozenset[Route]:
    """Every operation Management's URL resolver routes, `/healthz` to the last viewset action."""
    from django.urls import URLResolver, get_resolver

    found: set[Route] = set()

    def walk(patterns: Any, prefix: str) -> None:
        for entry in patterns:
            if isinstance(entry, URLResolver):
                walk(entry.url_patterns, prefix + str(entry.pattern))
                continue
            if "format" in entry.pattern.regex.groupindex:
                # DRF's `format_suffix_patterns` twin: the same view, with `.json` for the slash.
                continue
            path = normalised(_django_path(prefix + str(entry.pattern)))
            found.update((method, path) for method in _methods(entry.callback))

    walk(get_resolver().url_patterns, "")
    return frozenset(found)


# == the pages =====================================================================================


def _lines(page: Path, section: str | None = None) -> list[str]:
    """``page``, or the part of it from the heading ``section`` to the next heading of its level."""
    lines = page.read_text(encoding="utf-8").splitlines()
    if section is None:
        return lines
    starts = [index for index, line in enumerate(lines) if line.strip() == section]
    assert starts, f"{page.name} has no section headed {section!r}"
    level = section.split(" ", 1)[0] + " "
    end = next(
        (index for index in range(starts[0] + 1, len(lines)) if lines[index].startswith(level)),
        len(lines),
    )
    return lines[starts[0] : end]


def _named_in(page: Path, section: str | None = None) -> set[Route]:
    """Every operation ``page`` names in a code span, the guides' shorthand expanded — in one
    ``section`` of it when one is given."""
    named: set[Route] = set()
    for line in _lines(page, section):
        last: tuple[str, str] | None = None
        last_end = 0
        for span in re.finditer(r"`([^`]+)`", line):
            text = span.group(1).strip()
            operation = _OPERATION.match(text)
            if operation:
                last = (operation.group(1), operation.group(2).split("?", 1)[0])
            elif (
                last is not None
                and _CONTINUATION.match(text)
                and _SEPARATORS.fullmatch(line[last_end : span.start()])
            ):
                method, path = last
                if text.startswith(":"):
                    stem = path.rsplit(":", 1)[0] if ":" in path.rsplit("/", 1)[-1] else path
                else:
                    stem = path.rsplit("/", 1)[0]
                last = (method, stem + text)
            else:
                last = None
            if last is not None:
                named.add((last[0], normalised(last[1])))
            last_end = span.end()
    return named


def _the_gateway_serves(route: Route) -> bool:
    """Whether the gateway answers ``route`` — behind a `/uc/<slug>` prefix included, which its
    middleware strips from any path, and as the one verb route or one of its verbs."""
    method, path = route
    served = set(_gateway_routes()) | set(_gateway_operations())
    if route in served:
        return True
    return path.startswith("/uc/{}/") and (method, path.removeprefix("/uc/{}")) in served


def _a_plane_serves(route: Route) -> bool:
    return _the_gateway_serves(route) or route in _management_operations()


def _listed(routes: set[Route]) -> str:
    return "\n  ".join(f"{method} {path}" for method, path in sorted(routes, key=lambda r: r[::-1]))


# == the checks ====================================================================================


def test_both_planes_have_routes_to_compare() -> None:
    """A guard on the guard: an enumeration that found nothing would make every comparison below
    pass by describing nothing — the shape a hand-rolled walk of `app.routes` already took once."""
    from aira_gateway.api.gemini.routes import generate

    gateway = _gateway_operations()
    verbs = [route for route, endpoint in gateway.items() if endpoint is generate]

    assert len(gateway) >= 30, _listed(set(gateway))
    assert len(verbs) >= 4, f"the Gemini verb route was not expanded: {verbs}"
    assert len(_management_operations()) >= 60, _listed(set(_management_operations()))
    assert len(_named_in(REFERENCE, GATEWAY_SECTION)) >= 30, "§2 names almost no operation"
    assert len(_named_in(REFERENCE, MANAGEMENT_SECTION)) >= 60, "§3 names almost no operation"


def test_every_route_is_named_in_the_reference() -> None:
    """Each plane's routes in that plane's section: a reader looks a route up where its plane is."""
    missing = {
        section: routes
        for section, routes in (
            (GATEWAY_SECTION, set(_gateway_operations()) - _named_in(REFERENCE, GATEWAY_SECTION)),
            (
                MANAGEMENT_SECTION,
                set(_management_operations()) - _named_in(REFERENCE, MANAGEMENT_SECTION),
            ),
        )
        if routes
    }

    assert not missing, (
        "These operations are served and docs/API.md does not name them in their plane's section:"
        + "".join(f"\n{section}\n  {_listed(routes)}" for section, routes in missing.items())
        + "\n\nAdd a row there — method and path in one code span, `GET /api/v1/…`, with who may "
        "call it and what it answers — so the reference a reader scripts against still lists "
        "everything they can call."
    )


def test_the_reference_names_no_route_that_does_not_exist() -> None:
    """The other direction: a route on the page that no plane serves is one somebody will call.

    Every operation the page names counts, prose included — a sentence that tells a reader to call
    something makes the same promise a table row does — and one named in a plane's section must be
    that plane's.
    """
    invented = {
        f"{method} {path}"
        for method, path in _named_in(REFERENCE)
        if not _a_plane_serves((method, path))
    }
    invented |= {
        f"{method} {path} (in {GATEWAY_SECTION!r}, not a gateway route)"
        for method, path in _named_in(REFERENCE, GATEWAY_SECTION)
        if not _the_gateway_serves((method, path))
    }
    invented |= {
        f"{method} {path} (in {MANAGEMENT_SECTION!r}, not a Management route)"
        for method, path in _named_in(REFERENCE, MANAGEMENT_SECTION)
        if (method, path) not in _management_operations()
    }

    assert not invented, (
        "docs/API.md names these operations and no plane serves them as named:\n  "
        + "\n  ".join(sorted(invented))
        + "\n\nEither the route moved or was removed and the page was not, or the method or a "
        "trailing slash is wrong — Management's paths are exact (`APPEND_SLASH` is off)."
    )


@pytest.mark.parametrize("package", sorted(GUIDES))
def test_each_model_surface_is_named_in_its_guide(package: str) -> None:
    """A client developer reads the surface's guide, not the reference; a verb the gateway serves
    and the guide never names is one nobody migrating a client learns about."""
    guide = GUIDES[package]
    surface = {
        route
        for route, endpoint in _gateway_operations().items()
        if endpoint.__module__.startswith(package + ".")
    }
    missing = surface - _named_in(guide)

    assert surface, f"no route is served from {package} — has the surface moved?"
    assert not missing, (
        f"{guide.name} does not name these routes of its surface:\n  "
        + _listed(missing)
        + "\n\nName each in the guide's route table, as `POST /path` in a code span."
    )


def test_the_reference_says_where_the_schema_is_served() -> None:
    """§1.5 says the schema routes exist only locally and in demo mode, and both halves are asked.

    The code: a deployed gateway still serves fewer routes than a local one. The page: it still
    names the condition. A sentence that outlived the condition would send an operator looking for
    `/docs` on a production gateway; one that lost it would leave a reader to believe the map of
    every incident and content-read route is published wherever the gateway runs.
    """
    withheld = set(_gateway(LOCAL)) - set(_gateway(DEPLOYED))
    text = REFERENCE.read_text(encoding="utf-8")

    assert withheld, (
        "a deployed gateway now serves every route a local one does, so §1.5 of docs/API.md is no "
        "longer true: say where the schema is served now"
    )
    assert withheld <= _named_in(REFERENCE), _listed(withheld - _named_in(REFERENCE))
    assert "AIRA_ENVIRONMENT=local" in text and "AIRA_DEMO_MODE" in text, (
        "docs/API.md no longer says under which settings the gateway serves its schema"
    )
