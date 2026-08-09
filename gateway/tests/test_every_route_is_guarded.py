"""Every route is authenticated, or it is on a list somebody had to write.

FastAPI has no global default permission: a route is protected by whatever dependency its author
remembered, or by the `dependencies=[...]` argument of the `include_router` call that mounted it.
Both are invisible at the route itself. `GET /v1beta/models` has no `Depends` in its signature and
is protected only because the Gemini router happens to be mounted with one — which is the right
design (`FRD-126`: one gate for a surface, not one per verb) and an easy thing to lose. Mount a new
router without the argument, or add a route to a router that authenticates per-verb, and it is
served to anyone who can reach the port. Nothing said so.

So the list of public paths is **written down** here. Adding a route changes nothing; making one
public is a diff somebody has to justify — which is the only difference that matters between a
convention and a rule.

Deliberately a check of the dependency tree rather than of responses: a probe that called each
route without a credential would have to invent path parameters and bodies, and a `422` from
validation would read as "guarded" while proving nothing about authentication.
"""

from __future__ import annotations

from typing import Any

from fastapi.routing import APIRoute

from aira_gateway.app import create_app
from aira_gateway.config import GatewaySettings

#: Paths served without a credential, and why each one has to be.
#:
#: `/readyz` is the interesting entry: the **verdict** is public because a container platform
#: probes it before anything can hold a credential, while the body that names hosts, upstreams and
#: fallbacks is withheld unless the caller has one (`ADR-0015`). That split lives in the route; what
#: belongs here is only the fact that the path answers at all.
PUBLIC_PATHS = frozenset(
    {
        "/healthz",
        "/readyz",
        "/version-info",
        # The KIRA surface mirrors the predecessor's two unauthenticated operations (`FRD-107`).
        # A liveness check that answers 401 reports every instance as down.
        "/kira/api/external/health",
        # **A judgement, not an oversight.** Both `version-info` routes disclose build number,
        # commit and branch to anyone who can reach the port. `routes/health.py` argues the case:
        # a commit hash identifies the code, which is what somebody correlating a bug report
        # needs. The counter-argument is real — it tells an attacker exactly which release is
        # running — and making it authenticated would be a fourth documented deviation from the
        # predecessor. Listed rather than changed, so the exposure is reviewable instead of
        # accidental; `_may_see_detail` in `routes/health.py` is the pattern to reuse if the answer
        # ever changes.
        "/kira/api/external/version-info",
        # FastAPI's own, and only present when docs are enabled.
        "/openapi.json",
        "/docs",
        "/docs/oauth2-redirect",
        "/redoc",
    }
)

#: Dependencies that establish who the caller is. Any of them makes a route guarded.
AUTHENTICATING = frozenset({"require_principal", "require_attribution", "require_api_key"})


def _dependency_names(dependant: Any) -> set[str]:
    """Every dependency callable reachable from ``dependant``."""
    names: set[str] = set()
    stack = list(dependant.dependencies)
    while stack:
        current = stack.pop()
        call = getattr(current, "call", None)
        if call is not None:
            names.add(getattr(call, "__name__", ""))
        stack.extend(current.dependencies)
    return names


def _routes() -> list[tuple[APIRoute, set[str]]]:
    """Every route with the dependency names that actually apply to it.

    **A router-level dependency is not on the route.** `include_router(..., dependencies=[...])`
    used to copy them onto each route; this FastAPI keeps the router nested behind an
    `_IncludedRouter` and applies them from its `include_context` when a request matches. So the
    Gemini surface — whose *whole* authentication is that one argument — shows an empty dependency
    list at the route, and a guard reading only `route.dependant` would report the correctly
    protected routes as holes and then be "fixed" by exempting them. Inheriting the context is the
    difference between this file guarding something and excusing it.
    """
    app = create_app(GatewaySettings(auth_required=True, test_database=True))
    found: list[tuple[APIRoute, set[str]]] = []

    def walk(container: Any, inherited: set[str]) -> None:
        for route in getattr(container, "routes", []):
            if isinstance(route, APIRoute):
                found.append((route, inherited | _dependency_names(route.dependant)))
            elif hasattr(route, "original_router"):
                context = route.include_context
                names = {
                    getattr(dependency.dependency, "__name__", "")
                    for dependency in getattr(context, "dependencies", []) or []
                }
                walk(route.original_router, inherited | names)
            elif hasattr(route, "routes"):
                walk(route, inherited)

    walk(app, set())
    return found


def test_there_are_routes_to_check() -> None:
    """The guard's own failure mode: an app that exposes nothing passes every assertion below.
    This has caught a broken harness in this repository before."""
    assert len(_routes()) > 15


def test_every_route_is_authenticated_or_listed_as_public() -> None:
    unguarded = sorted(
        f"{sorted(route.methods or [])} {route.path}"
        for route, guards in _routes()
        if route.path not in PUBLIC_PATHS and not (guards & AUTHENTICATING)
    )

    assert not unguarded, (
        "these routes establish no caller identity and are not on PUBLIC_PATHS:\n  "
        + "\n  ".join(unguarded)
        + "\n\nAdd an authenticating dependency, or add the path to PUBLIC_PATHS with a comment "
        "saying why it must answer without a credential."
    )


def test_the_public_list_names_only_paths_that_exist() -> None:
    """A list that outlives its routes stops describing the app and starts excusing new ones: a
    path deleted today is an exemption waiting for a future route to reuse the name."""
    served = {route.path for route, _ in _routes()}
    # FastAPI mounts its own docs paths outside the router tree and only when enabled, so they are
    # exempt from having to exist; everything else on the list must be a route this app serves.
    frameworks_own = {"/openapi.json", "/docs", "/docs/oauth2-redirect", "/redoc"}
    stale = PUBLIC_PATHS - served - frameworks_own

    assert not stale, f"PUBLIC_PATHS names paths this app does not serve: {sorted(stale)}"
