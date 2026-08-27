"""Every endpoint, swept with values a caller can actually send (`LESSONS.md` §1).

The rule this file asserts is already written down — *"a caller's own value must never become a
server error"* — and it is written down because it has been broken three times in this repository
by three different mechanisms: a lone surrogate that died inside the HTTP client, `1e309` that no
`json` column would take, and an `int` wider than the column it was compared against. Each of those
was found by *asking about one field*.

This asks about all of them at once, which is a different question and finds a different thing.
Both API surfaces came through it clean; the two **incident** endpoints did not, and the reason
they did not is instructive: they read their body with a bare `await request.json()` and their two
numbers with a bare `int(...)`, while every other route in the gateway already spells out the
guarded form. The rule was stated in three places and held in three places, and the routes written
afterwards did not inherit it. That is the shape a per-field test cannot see and a sweep can.

**A sweep is a floor, not a specification.** What each field *should* answer is pinned where that
field lives — `test_suspensions.py`, `test_model_check.py`, `test_traces.py`. This only says that
nothing here answers `500` to a value somebody typed, and it is deliberately cheap so that the next
endpoint is covered by it on the day it is added rather than on the day somebody remembers.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from aira_gateway.app import create_app
from aira_gateway.auth.dependencies import require_principal
from aira_gateway.auth.principal import Principal
from aira_gateway.config import GatewaySettings
from aira_gateway.db.models import ModelRead

#: Both incident roles at once, so nothing is skipped for want of authority. A 403 is a perfectly
#: good answer to a bad value; what is under test is that a *permitted* caller cannot produce a 500.
ADMIN = Principal(
    subject="root",
    method="oidc",
    roles=("global-admin", "it-security"),
    use_cases=("demo-uc",),
    username="root",
)

#: Values that are legal JSON, legal in a URL, and wrong. The list is short on purpose: what breaks
#: a boundary is a *kind* of value — empty, out of range, the wrong type, unparseable — and one
#: representative of each finds as much as a hundred variations of the same kind.
WEIRD: list[str] = [
    "",
    " ",
    "0",
    "-1",
    "abc",
    "9" * 40,
    "2026-13-45T99:99:99",
    "|",
    "x|",
    "-",
    "a,b",
    "%",
    "'",
    "1e400",
]

#: Paths whose **body** is swept elsewhere, with the file that does it. The two generation
#: surfaces have vocabularies of their own and a file each; naming them here is what lets the
#: completeness check below be an assertion rather than a comment.
BODIES_ELSEWHERE: dict[str, str] = {
    "/v1beta/models/{resource}": "test_no_silent_drop.py, test_edge_cases.py",
    "/kira/api/external/chat": "test_kira_surface.py",
    "/kira/api/external/embed": "test_kira_surface.py",
    "/kira/api/external/streaming-chat": "test_kira_surface.py",
}


#: Bodies for the two POST routes that take a hand-written JSON object. The generation surfaces
#: have their own vocabularies and their own files; these are the ones a console form fills in.
#: Hand-written bodies, keyed by the **template** the router publishes and carrying the concrete
#: path to send them to. Keyed by the template so the completeness check above can compare this
#: list against the document; a concrete path would never match `{model}` and the comparison would
#: report every entry as stale.
BODIES: dict[str, tuple[str, list[Any]]] = {
    "/v1beta/suspensions": (
        "/v1beta/suspensions",
        [
            {},
            {"target": "nope", "target_value": "x"},
            {"target": "subject"},
            {"target": "subject", "target_value": "x", "action": "quarantine"},
            {"target": "subject", "target_value": "x", "action": "throttle"},
            {"target": "subject", "target_value": "x", "minutes": -5},
            {"target": "subject", "target_value": "x", "minutes": 10**30},
            {"target": "subject", "target_value": "x", "minutes": True},
            {"target": "subject", "target_value": "x", "throttle_rpm": "many"},
            {"target": "subject", "target_value": "x", "throttle_rpm": 10**12},
            {"target": "use_case", "target_value": "x" * 5000},
            [1, 2],
            "text",
        ],
    ),
    "/v1beta/models/{model}:checkThinking": (
        "/v1beta/models/mock-1:checkThinking",
        [
            {},
            {"levels": "not-a-list"},
            {"levels": [1, 2, 3]},
            {"levels": ["x" * 5000]},
            {"modes": {"a": 1}},
            [1, 2],
            "text",
        ],
    ),
    "/v1beta/pipeline:dryRun": (
        "/v1beta/pipeline:dryRun",
        [
            {},
            {"pipeline": {}},
            {"pipeline": {"steps": "not-a-list"}},
            {"pipeline": {"steps": [{"type": "unknown_step"}]}},
            {"pipeline": {"steps": [{"type": "injection_filter", "config": {"patterns": ["("]}}]}},
            {"pipeline": {"steps": [{"type": "model_route", "config": {"categories": []}}]}},
            {"pipeline": {"fallback_models": ["x"] * 500}},
            {"use_case": "x" * 500},
            [1, 2],
            "text",
        ],
    ),
}

#: Not JSON at all. `await request.json()` raises a `ValueError`, which is a 400 everywhere it is
#: caught and a 500 everywhere it is not.
UNPARSEABLE: list[bytes] = [b"{", b"not json", b"\xff\xfe", b'{"a": Infinity}']


#: Methods a sweep may send blind. A `POST` carries a body whose vocabulary decides what is wrong,
#: which is what `BODIES` is for; a `GET` or a `DELETE` is fully described by its parameters.
_READ_METHODS = frozenset({"GET", "DELETE"})


def _concrete(path: str, names: list[str], value: str) -> str | None:
    """``path`` with every path parameter filled in, or ``None`` where the value cannot be one.

    An empty or blank segment does not address the route at all — the request 404s on a path that
    is not the one under test — so it is skipped rather than reported as a pass.
    """
    if not names:
        return path
    if not value.strip() or "/" in value:
        return None
    for name in names:
        path = path.replace("{" + name + "}", value)
    return path


def _client() -> TestClient:
    app = create_app(GatewaySettings(auth_required=False, log_queue_size=0))
    app.dependency_overrides[require_principal] = lambda: ADMIN
    # `raise_server_exceptions=False` is what makes this a sweep rather than a crash: an unhandled
    # exception has to arrive as the 500 a real client would see, so every case is reported
    # together instead of the first one ending the run.
    return TestClient(app, raise_server_exceptions=False)


async def _catalogue(client: TestClient) -> None:
    async with client.app.state.db_sessionmaker() as session:  # type: ignore[attr-defined]
        session.add(ModelRead(model="mock-1", capabilities=["generate"], publisher="google"))
        await session.commit()


def _served(client: TestClient) -> dict[str, dict[str, Any]]:
    """The routes the application actually publishes, from the document the router produces.

    Read from `/openapi.json` rather than from a list in this file, and that is the correction this
    sweep needed. It carried three hand-written lists of paths and parameters under a docstring
    promising that *"the next endpoint is covered by it on the day it is added rather than on the
    day somebody remembers"* — which was the one thing it could not do. Checked on 2026-08-26
    against the served document: `/v1beta/register` was not swept at all, nor `traces`'
    `flagged_only` and `tools_only`, nor `anomalies`' `limit`, nor the `provider`/`publisher`/
    `region` trio that both model checks take, nor `GET /v1beta/models/{model}`, nor the `DELETE`
    on a suspension. Seven gaps, in the file whose whole argument is that a per-field test cannot
    see what a sweep can — **a hand-written list with no counterpart**, which is the failure
    `LESSONS.md` §1 records six instances of.

    Deriving it also removes the guard-on-the-guard this file used to need: a sweep that asks the
    router which routes exist cannot ask about a route that does not.
    """
    document: dict[str, dict[str, Any]] = client.get("/openapi.json").json()["paths"]
    return document


def _parameters(spec: dict[str, Any], where: str) -> list[str]:
    return [p["name"] for p in (spec.get("parameters") or []) if p.get("in") == where]


def test_the_sweep_reaches_more_than_a_handful_of_routes() -> None:
    """A guard on the guard, in the only form still worth having: a document that came back empty
    would make every sweep below pass by asking nothing, and this repository has shipped two
    guards that could not fail (`LESSONS.md` §1)."""
    with _client() as client:
        served = _served(client)

    assert len(served) > 20, f"the router published {len(served)} paths; the sweep asks about those"
    assert "/v1beta/register" in served, "a sanity anchor on a route added after this file"


def test_every_post_body_is_swept_here_or_named_somewhere_else() -> None:
    """The counterpart, in both directions — the answer this project has reached six times.

    A body this file does not sweep and no other file claims is a body nobody sends a wrong value
    to; a name here for a route that no longer exists is a claim about a path that is gone. Bodies
    cannot be derived from the document the way a parameter can — what is *wrong* for one depends
    on its vocabulary — so this is the list that stays hand-written, and it is the list that gets
    the comparison.
    """
    with _client() as client:
        served = _served(client)

    # **Every** `POST`, not only those the document gives a `requestBody`. A route that reads
    # `Request` directly — which is how every hand-written body on this surface is read, and the
    # reason this file exists — declares no schema, so keying on `requestBody` would have matched
    # nothing and passed while claiming to compare two lists.
    posts = {path for path, methods in served.items() if "post" in methods}
    claimed = set(BODIES) | set(BODIES_ELSEWHERE)

    assert not posts - claimed, (
        f"these routes take a body nobody sweeps: {sorted(posts - claimed)}. Add cases to "
        "`BODIES`, or name the file that already does it in `BODIES_ELSEWHERE`."
    )
    assert not claimed - posts, (
        f"these are claimed to take a body and the router publishes none: {sorted(claimed - posts)}"
    )


def test_no_query_parameter_answers_with_a_server_error() -> None:
    failures: list[tuple[str, str, str, str, int]] = []
    with _client() as client:
        for path, methods in sorted(_served(client).items()):
            for method, spec in methods.items():
                if method.upper() not in _READ_METHODS:
                    continue
                query = _parameters(spec, "query")
                if not query:
                    continue
                for value in WEIRD:
                    concrete = _concrete(path, _parameters(spec, "path"), value)
                    if concrete is None:
                        continue
                    for name in query:
                        response = client.request(method.upper(), concrete, params={name: value})
                        if response.status_code >= 500:
                            failures.append((method, path, name, value, response.status_code))
    assert not failures, failures


def test_no_path_parameter_answers_with_a_server_error() -> None:
    failures: list[tuple[str, str, str, int]] = []
    with _client() as client:
        for path, methods in sorted(_served(client).items()):
            for method, spec in methods.items():
                if method.upper() not in _READ_METHODS:
                    continue
                names = _parameters(spec, "path")
                if not names:
                    continue
                for value in WEIRD:
                    concrete = _concrete(path, names, value)
                    if concrete is None:
                        continue
                    response = client.request(method.upper(), concrete)
                    if response.status_code >= 500:
                        failures.append((method, path, value, concrete, response.status_code))
    assert not failures, failures


async def test_no_hand_written_body_answers_with_a_server_error() -> None:
    failures: list[tuple[str, Any, int, str]] = []
    with _client() as client:
        await _catalogue(client)
        for path, bodies in BODIES.values():
            for body in bodies:
                response = client.post(path, json=body)
                if response.status_code >= 500:
                    failures.append((path, body, response.status_code, response.text[:200]))
    assert not failures, failures


@pytest.mark.parametrize("raw", UNPARSEABLE)
async def test_a_body_that_is_not_json_answers_with_a_refusal(raw: bytes) -> None:
    failures: list[tuple[str, int, str]] = []
    with _client() as client:
        await _catalogue(client)
        for path, _bodies in BODIES.values():
            response = client.post(path, content=raw, headers={"content-type": "application/json"})
            if response.status_code >= 500:
                failures.append((path, response.status_code, response.text[:200]))
    assert not failures, failures
