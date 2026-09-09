"""Every endpoint of the control plane, swept with values a caller can actually send.

The gateway has had this since 2026-08-26, and its own docstring says why a sweep finds what a
per-field test cannot: *"the rule was stated in three places and held in three places, and the
routes written afterwards did not inherit it."* **This plane never got one** — which is
`LESSONS.md` §1's *a rule restated on a second surface*, in its cheapest form: the lesson was
applied where it was learned, and nobody carried it.

It found the thing a sweep is for. A JSON body nesting ~200 000 levels — 1.2 MB, inside Django's
own 2.5 MB upload ceiling — raised `RecursionError` out of DRF's `JSONParser` on **every**
endpoint here, and `RecursionError` is not a `ValueError`, so nothing caught it and each one
answered `500`. Not a defect of any field; a defect of the *parser* every field arrives through.
`aira_common.nesting` is the bound, read by both planes so they cannot drift, and
`apps.api.parsers.BoundedJSONParser` is what applies it here.

**A sweep is a floor, not a specification.** What each endpoint *should* answer is pinned where it
lives — `test_usecases.py`, `test_catalog.py`, `test_budgets.py`. This says only that nothing here
answers `500` to something somebody typed, and it is deliberately derived from the URL
configuration so the endpoint written next is covered on the day it is added rather than on the
day somebody remembers.

Two traps were walked into writing it, both of the shape this project calls *a test whose setup
never reaches the path it is named after*, and both are why the fixture below is what it is:

- **the throttle.** `UserRateThrottle` is 600/minute, and a sweep is thousands of requests, so the
  first version measured `429` on 4 112 of 5 000 calls and reported green about the parts it never
  reached.
- **the fixture the sweep deletes.** `DELETE /use-cases/<slug>/` retires the use case, after which
  every sub-resource answers `404` — so a run that swept the parent before its children was
  sweeping nothing. :func:`Fixture.ensure` puts it back between requests.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from collections.abc import Iterator
from typing import Any

import pytest
from aira_management.apps.usecases.models import UseCase
from aira_management.rbac import sync_user_roles
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.urls import get_resolver
from rest_framework.test import APIClient

from aira_common.nesting import MAX_JSON_DEPTH

from .conftest import role_claims

pytestmark = pytest.mark.django_db

SLUG = "sweep-uc"

#: Values that are legal JSON, legal in a URL, and wrong. Short on purpose: what breaks a boundary
#: is a *kind* of value — empty, out of range, the wrong type, unparseable, percent-encoded
#: nonsense — and one representative of each finds as much as a hundred variations of one kind.
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
    # Percent-encoded, because a lone surrogate cannot travel in a URL any other way — and the
    # test client refuses to encode one, which is a fact about the client and not about the
    # server.
    "%ED%A0%80",
    "%FF",
    "%2e%2e",
    "0x10",
    "1.5",
    "true",
    "9999999999999999999999",
]

#: Query parameters these views read. Derived by grepping `query_params` would be prettier and
#: would miss the ones DRF reads for the view (`page`, `format`); this list is checked against the
#: source by :func:`test_every_query_parameter_the_source_reads_is_swept`.
QUERY = ["q", "may_call", "group_path", "use_case", "model", "page", "page_size", "format", "mine"]

#: Bodies a console form could plausibly produce, each wrong in one way. Sent as **raw bytes**:
#: `format="json"` routes them through the client's own encoder, which refuses `Infinity` and a
#: lone surrogate — so the two values most likely to break a boundary would never leave the test.
BAD_BODIES: list[Any] = [
    {},
    [1, 2],
    "text",
    None,
    {"x": "y"},
    {"slug": "\ud800"},
    {"slug": "x" * 5000},
    {"name": None},
    {"limit_cost": "Infinity"},
    {"limit_cost": "NaN"},
    {"limit_cost": float("inf")},
    {"limit_cost": "1e400"},
    {"limit_cost": "0.000000000000000000001"},
    {"limit_tokens": 10**30},
    {"limit_requests": -1},
    {"threshold": 10**30},
    {"threshold": "1e400"},
    {"window_minutes": 10**30},
    {"min_sample": "many"},
    {"retention_days": 10**30},
    {"period": "aeon"},
    {"scope": "everything"},
    {"kind": "token_spike"},
    {"action": "nuke"},
    {"input_price_per_million": "1e400"},
    {"regions": "not-a-list"},
    {"capabilities": {"a": 1}},
    {"allowed_models": [{"a": 1}]},
    {"steps": "x"},
    {"expires_at": "2026-13-45T99:99:99"},
    {"role": "root"},
    {"username": ""},
    {"group_path": "x" * 5000},
    {"scope": "installation", "period": "day", "limit_cost": "-1"},
    {"scope": "each_member", "period": "month", "limit_tokens": "abc"},
    {"steps": [{"type": "injection_filter", "config": {"patterns": ["("]}}]},
    {"steps": [{"type": "model_route", "config": {"categories": [{"name": "c++"}]}}]},
]

#: Not JSON at all, plus the one that is JSON and still killed the parser.
UNPARSEABLE: list[bytes] = [
    b"{",
    b"not json",
    b"\xff\xfe",
    b'{"a": Infinity}',
    b"",
    (b'{"a":' * (MAX_JSON_DEPTH + 1)) + b"1" + (b"}" * (MAX_JSON_DEPTH + 1)),
    (b'{"a":' * 200_000) + b"1" + (b"}" * 200_000),
]


@pytest.fixture(autouse=True)
def _unthrottled(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """A sweep is thousands of requests and the throttle is 600 a minute.

    Left in place, it answered `429` to four in five and the sweep reported green about the routes
    it never reached — the trap named in this file's docstring. The throttle has its own test
    (`test_the_api_is_rate_limited.py`); what is under examination here is what the *views* do.
    """
    from rest_framework.throttling import SimpleRateThrottle

    monkeypatch.setattr(SimpleRateThrottle, "allow_request", lambda self, request, view: True)
    cache.clear()
    yield
    cache.clear()


def _routes() -> list[tuple[str, list[str]]]:
    """Every route the URL configuration publishes, with the names of its path parameters.

    Read from the resolver rather than from a list in this file, for the reason the gateway's
    sweep had to learn: *a hand-written list with no counterpart* cannot cover the endpoint added
    tomorrow, which is the one thing a sweep is supposed to do.
    """
    found: list[tuple[str, list[str]]] = []

    def walk(patterns: Any, prefix: str) -> None:
        for entry in patterns:
            if hasattr(entry, "url_patterns"):
                walk(entry.url_patterns, prefix + str(entry.pattern))
            else:
                found.append((prefix + str(entry.pattern), sorted(entry.pattern.regex.groupindex)))

    walk(get_resolver().url_patterns, "")
    return found


def _fill(pattern: str, values: dict[str, str]) -> str | None:
    """``pattern`` with every path parameter filled in, or ``None`` where it cannot address a route.

    DRF publishes a ``.json``-suffixed twin of every route; sweeping both doubles the run and asks
    the same question twice, so the suffixed ones are skipped. An empty or blank segment does not
    address the route under test at all — it 404s on a different path — so it is skipped rather
    than reported as a pass.
    """
    path = pattern.replace("^", "").replace("$", "")
    if ".(?P<format>" in path or "<drf_format_suffix:format>" in path:
        return None
    for name, value in values.items():
        if not value.strip() or "/" in value:
            return None
        path = re.sub(r"\(\?P<" + name + r">[^)]*\)", value, path)
    if "(?P<" in path or "<" in path:
        return None
    return "/" + path


class Fixture:
    """A caller who may do everything, and one of every object the routes address.

    Created **through the API**, not through the ORM: a use case written directly carries none of
    the guardian object permissions the viewset assigns, so every sub-resource answers `404` and
    the sweep measures the permission layer instead of the views.
    """

    def __init__(self) -> None:
        user = get_user_model().objects.create(username="sweeper")
        sync_user_roles(user, role_claims("global-admin", "it-security", "it-steuerung"))
        self.client = APIClient()
        self.client.force_authenticate(user=user)
        self.valid: dict[str, str] = {"slug": SLUG, "username": "sweeper", "format": "json"}
        self._create()

    def _post(self, path: str, body: Any) -> Any:
        response = self.client.post(path, body, format="json")
        assert response.status_code in (200, 201), (path, response.status_code, response.content)
        return response.json()

    def _create(self) -> None:
        self.ensure()
        base = f"/api/v1/use-cases/{SLUG}"
        self.valid["budget_id"] = str(
            self._post(
                f"{base}/budgets/", {"scope": "use_case", "period": "day", "limit_cost": "5"}
            )["id"]
        )
        self.valid["limit_id"] = str(
            self._post(f"{base}/rate-limits/", {"scope": "use_case", "limit_rpm": 60})["id"]
        )
        self.valid["rule_id"] = str(
            self._post(
                f"{base}/anomaly-rules/",
                {
                    "name": "r",
                    "kind": "refusal_rate",
                    "window_minutes": 15,
                    "threshold": "50",
                    "min_sample": 10,
                    "action": "alert",
                    "target": "use_case",
                },
            )["id"]
        )
        self.valid["prefix"] = self._post(f"{base}/api-keys/", {"label": "k"})["prefix"]
        self._post(
            "/api/v1/models/",
            {
                "name": "sweep-model",
                "provider": "mock",
                "publisher": "mock",
                "capabilities": ["generate"],
            },
        )
        self.valid["name"] = "sweep-model"
        self.valid["pk"] = str(
            self._post(
                "/api/v1/installation-budgets/",
                {"scope": "installation", "period": "day", "limit_cost": "5"},
            )["id"]
        )

    def ensure(self) -> None:
        """Put the use case back if a swept `DELETE` retired it.

        Not defensive tidiness: without this, everything swept *after* the parent detail route
        answers `404`, and a run of 404s is a sweep that asked nothing.
        """
        row = UseCase.objects.filter(slug=SLUG).first()
        if row is None:
            response = self.client.post(
                "/api/v1/use-cases/", {"slug": SLUG, "name": "Sweep"}, format="json"
            )
            assert response.status_code == 201, response.content
        elif row.deleted_at is not None:
            UseCase.objects.filter(slug=SLUG).update(deleted_at=None, deleted_by="")

    def paths(self) -> Iterator[tuple[str, list[str]]]:
        """Every route, filled in with values that address something real."""
        for pattern, names in _routes():
            path = _fill(pattern, {name: self.valid.get(name, "1") for name in names})
            if path is not None:
                yield path, names


def test_the_sweep_reaches_more_than_a_handful_of_routes() -> None:
    """A guard on the guard, in the only form worth having: a resolver that came back empty would
    make every assertion below pass by asking nothing (`LESSONS.md` §7)."""
    fixture = Fixture()
    paths = [path for path, _names in fixture.paths()]

    assert len(paths) > 20, paths
    assert "/api/v1/use-cases/" in paths
    assert f"/api/v1/use-cases/{SLUG}/budgets/" in paths, "a sub-resource anchor"


def test_the_sweep_actually_reaches_the_views() -> None:
    """Not a 404 sweep and not a 429 sweep, asserted rather than assumed.

    Both were true of earlier drafts of this file, and neither is visible from a green run: a
    sweep that never leaves the router reports exactly the same "no 500s" as one that covers
    everything.
    """
    fixture = Fixture()
    statuses: Counter[int] = Counter()
    for path, _names in fixture.paths():
        fixture.ensure()
        statuses[fixture.client.get(path).status_code] += 1

    assert statuses[429] == 0, "the throttle is answering for the views"
    assert statuses[200] >= 10, dict(statuses)


def test_no_path_parameter_answers_with_a_server_error() -> None:
    """One parameter at a time, the rest addressing something that exists — or a wrong value in
    the *first* segment would 404 before the one under test was ever read."""
    fixture = Fixture()
    failures: list[tuple[str, str, str, int]] = []
    for pattern, names in _routes():
        for target in (name for name in names if name != "format"):
            for value in WEIRD:
                filled = {name: fixture.valid.get(name, "1") for name in names}
                filled[target] = value
                path = _fill(pattern, filled)
                if path is None:
                    continue
                for method in ("get", "delete"):
                    fixture.ensure()
                    response = getattr(fixture.client, method)(path)
                    if response.status_code >= 500:
                        failures.append((method, path, target, response.status_code))
    assert not failures, failures


def test_every_query_parameter_the_source_reads_is_swept() -> None:
    """The counterpart to a hand-written list, which is the only thing that keeps one honest.

    `QUERY` cannot be derived — DRF's own `page` and `format` are read by the framework and appear
    in no view — so it stays written out, and this compares it against what the source actually
    asks for. A parameter added to a view and not to that list is a parameter nobody sends a wrong
    value to, and nothing would say so (`LESSONS.md` §1).
    """
    from pathlib import Path

    source = Path(__file__).resolve().parents[1] / "src" / "aira_management"
    read = {
        match.group(1)
        for path in source.rglob("*.py")
        for match in re.finditer(r"query_params\.get\(\s*[\"']([a-z_]+)[\"']", path.read_text())
    }

    assert read, "the pattern matched nothing — the sweep would be comparing against an empty set"
    missing = sorted(read - set(QUERY))
    assert not missing, f"these query parameters are read by a view and swept by nobody: {missing}"


def test_no_query_parameter_answers_with_a_server_error() -> None:
    # No `ensure()` inside the loop: a `GET` retires nothing, so the fixture cannot go away —
    # and a database round trip per request is most of what this test would otherwise cost.
    fixture = Fixture()
    failures: list[tuple[str, str, str, int]] = []
    for path, _names in fixture.paths():
        for name in QUERY:
            for value in WEIRD:
                response = fixture.client.get(path, {name: value})
                if response.status_code >= 500:
                    failures.append((path, name, value, response.status_code))
    assert not failures, failures


def test_no_body_answers_with_a_server_error() -> None:
    fixture = Fixture()
    failures: list[tuple[str, str, Any, int, bytes]] = []
    for path, _names in fixture.paths():
        if path in ("/healthz", "/readyz"):
            continue
        for method in ("post", "patch", "put"):
            for body in BAD_BODIES:
                fixture.ensure()
                # `surrogatepass`, because a lone surrogate is exactly the value under test and
                # the ordinary encoder refuses to carry it.
                raw = json.dumps(body, allow_nan=True).encode("utf-8", "surrogatepass")
                response = getattr(fixture.client, method)(
                    path, raw, content_type="application/json"
                )
                if response.status_code >= 500:
                    failures.append(
                        (method, path, body, response.status_code, response.content[:300])
                    )
            for raw_bytes in UNPARSEABLE:
                fixture.ensure()
                response = getattr(fixture.client, method)(
                    path, raw_bytes, content_type="application/json"
                )
                if response.status_code >= 500:
                    failures.append(
                        (method, path, raw_bytes[:40], response.status_code, response.content[:300])
                    )
    assert not failures, failures


def test_a_body_that_nests_too_deeply_is_refused_by_name() -> None:
    """The finding, stated as its own property rather than left inside the sweep.

    A sweep says *nothing answered 500*; it does not say **why** a particular body was refused,
    and "the parser stopped recursing" and "the field was wrong" are different answers that a
    reader has to be able to tell apart. Before `BoundedJSONParser` this body was a `500` on every
    endpoint on this plane.
    """
    fixture = Fixture()
    deep = (b'{"a":' * 200_000) + b"1" + (b"}" * 200_000)

    response = fixture.client.post("/api/v1/use-cases/", deep, content_type="application/json")

    assert response.status_code == 400, response.content
    assert b"nests deeper than" in response.content


def test_a_body_within_the_bound_is_still_read() -> None:
    """The comparison — a bound that refuses everything is not a bound.

    Exactly `MAX_JSON_DEPTH` levels: refused for what it *says* (no `slug`, no `name`) rather than
    for how deeply it nests.
    """
    fixture = Fixture()
    at_the_bound = (b'{"a":' * MAX_JSON_DEPTH) + b"1" + (b"}" * MAX_JSON_DEPTH)

    response = fixture.client.post(
        "/api/v1/use-cases/", at_the_bound, content_type="application/json"
    )

    assert response.status_code == 400
    assert b"nests deeper than" not in response.content
    assert b"slug" in response.content


def test_only_json_is_parsed() -> None:
    """The parser list is one entry, and swapping the entry did not quietly widen it.

    JSON-only was a decision before this change (`config/settings.py`), and a fix about *depth*
    that also started accepting form posts would be the shape `LESSONS.md` §1 calls *carrying
    something across during a move is not the same as it belonging there* — the first draft did
    exactly that, by declaring the setting a second time instead of replacing it.
    """
    fixture = Fixture()

    response = fixture.client.post(
        "/api/v1/use-cases/", {"slug": "form-uc", "name": "From a form"}, format="multipart"
    )

    assert response.status_code == 415, response.content
