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

QUERIES: list[tuple[str, str]] = [
    ("/v1beta/reporting", "from"),
    ("/v1beta/reporting", "to"),
    ("/v1beta/reporting", "breakdown"),
    ("/v1beta/reporting", "use_case"),
    ("/v1beta/anomalies", "cursor"),
    ("/v1beta/anomalies", "use_case"),
    ("/v1beta/traces", "cursor"),
    ("/v1beta/traces", "use_case"),
    ("/v1beta/traces", "outcome"),
    ("/v1beta/traces", "credential"),
    ("/v1beta/traces", "source_ip"),
    ("/v1beta/traces", "subject"),
    ("/v1beta/traces", "refusals_only"),
    ("/v1beta/traces", "limit"),
    ("/v1beta/traces", "mine"),
]

PATHS: list[str] = [
    "/v1beta/usage/{}",
    "/v1beta/traces/{}/payload",
    "/v1beta/providers/{}/offerings",
    "/v1beta/models/{}:check",
    "/v1beta/suspensions/{}",
]

#: Bodies for the two POST routes that take a hand-written JSON object. The generation surfaces
#: have their own vocabularies and their own files; these are the ones a console form fills in.
BODIES: dict[str, list[Any]] = {
    "/v1beta/suspensions": [
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
    "/v1beta/models/mock-1:checkThinking": [
        {},
        {"levels": "not-a-list"},
        {"levels": [1, 2, 3]},
        {"levels": ["x" * 5000]},
        {"modes": {"a": 1}},
        [1, 2],
        "text",
    ],
    "/v1beta/pipeline:dryRun": [
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
}

#: Not JSON at all. `await request.json()` raises a `ValueError`, which is a 400 everywhere it is
#: caught and a 500 everywhere it is not.
UNPARSEABLE: list[bytes] = [b"{", b"not json", b"\xff\xfe", b'{"a": Infinity}']


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


def test_the_sweep_reaches_the_endpoints_it_names() -> None:
    """A guard on the guard: a sweep whose requests all miss their route passes by testing nothing,
    and this repository has shipped two guards that could not fail (`LESSONS.md` §1).

    Asked of the **published document** rather than of a response, because a plain request to any
    of these can legitimately answer 404 for a reason of its own — a model that is not catalogued,
    a suspension that does not exist. What must not happen is that the path is not served at all.
    `/openapi.json` is the artefact the router produces, which is the guard this project prefers
    over one that reads the source (`LESSONS.md` §1).
    """
    with _client() as client:
        served = set(client.get("/openapi.json").json()["paths"])

    named = {path for path, _ in QUERIES}
    # The two POST paths carry a resource in them, so they are matched by their template.
    named |= {"/v1beta/suspensions", "/v1beta/pipeline:dryRun"}
    unrouted = {path for path in named if path not in served}

    assert not unrouted, f"the sweep asks about routes that do not exist: {sorted(unrouted)}"
    assert any(":checkThinking" in path for path in served), "the thinking check moved or went"


def test_no_query_parameter_answers_with_a_server_error() -> None:
    failures: list[tuple[str, str, str, int]] = []
    with _client() as client:
        for path, parameter in QUERIES:
            for value in WEIRD:
                response = client.get(path, params={parameter: value})
                if response.status_code >= 500:
                    failures.append((path, parameter, value, response.status_code))
    assert not failures, failures


def test_no_path_parameter_answers_with_a_server_error() -> None:
    failures: list[tuple[str, str, int]] = []
    with _client() as client:
        for template in PATHS:
            for value in WEIRD:
                if not value.strip():
                    continue
                response = client.get(template.format(value))
                if response.status_code >= 500:
                    failures.append((template, value, response.status_code))
    assert not failures, failures


async def test_no_hand_written_body_answers_with_a_server_error() -> None:
    failures: list[tuple[str, Any, int, str]] = []
    with _client() as client:
        await _catalogue(client)
        for path, bodies in BODIES.items():
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
        for path in BODIES:
            response = client.post(path, content=raw, headers={"content-type": "application/json"})
            if response.status_code >= 500:
                failures.append((path, response.status_code, response.text[:200]))
    assert not failures, failures
