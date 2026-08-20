"""The usage report as a spreadsheet (FRD-602).

§5.3 names the test that matters and it is the last section here: a caller without oversight asks
for CSV, and **the file's bytes** contain exactly the use cases their JSON contains. Asserted
against the bytes rather than against a service call, because the failure being guarded against is
an export that returns more than the screen — and that failure lives in the rendering path, not in
the query.

Everything above it is the smaller half: the format is a format people open in Excel, and the
things that make it unreadable there are not obscure.
"""

from __future__ import annotations

import csv
import io
from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient

from aira_gateway.app import create_app
from aira_gateway.auth.principal import Principal
from aira_gateway.config import GatewaySettings
from aira_gateway.reporting.csv_export import BOM, UnknownBreakdown, filename, render

REPORT: dict[str, Any] = {
    "by_use_case": [
        {
            "key": "kundenservice",
            "requests": 12,
            "failed": 2,
            "prompt_tokens": 100,
            "completion_tokens": 50,
            "total_tokens": 150,
            "cost_nanos": 1_500_000_000,
            "unpriced_requests": 0,
            "avg_latency_ms": 120,
            "max_latency_ms": 900,
        },
        {
            "key": "vertrieb, süd",  # a comma and an umlaut, both on purpose
            "requests": 3,
            "failed": 0,
            "prompt_tokens": 10,
            "completion_tokens": 5,
            "total_tokens": 15,
            "cost_nanos": 0,
            "unpriced_requests": 3,
            "avg_latency_ms": 80,
            "max_latency_ms": 90,
        },
    ],
    "by_model": [{"key": "mock-1", "requests": 15, "cost_nanos": 1_500_000_000}],
    "by_member": [{"key": "alice", "requests": 15, "cost_nanos": 1_500_000_000}],
}


def _rows(body: str) -> list[list[str]]:
    return list(csv.reader(io.StringIO(body.lstrip(BOM))))


# == the format people actually open =============================================================


def test_the_file_starts_with_a_byte_order_mark() -> None:
    """Without it, Excel reads a UTF-8 file as the local code page and `süd` becomes `sÃ¼d`. It is
    invisible to every other consumer, which is why it costs nothing to add and everything to
    forget."""
    assert render(REPORT, "use_case", "EUR").startswith(BOM)


def test_an_umlaut_survives_the_round_trip() -> None:
    body = render(REPORT, "use_case", "EUR")
    assert "süd" in body


def test_a_key_containing_the_delimiter_is_quoted_rather_than_splitting_the_row() -> None:
    """A use case named `vertrieb, süd` would otherwise become two columns, silently shifting every
    figure on that row one place to the left — a spreadsheet that is wrong rather than broken."""
    rows = _rows(render(REPORT, "use_case", "EUR"))
    assert rows[2][0] == "vertrieb, süd"
    assert len(rows[1]) == len(rows[2])


@pytest.mark.parametrize("formula", ["=1+1", "+1", "-2+3", "@SUM(A1)", "\tSUM(A1)"])
def test_a_key_a_spreadsheet_would_evaluate_is_written_as_text(formula: str) -> None:
    """**A cell beginning with `=` is a formula, and the `key` column is caller content.**

    `AuditTrail.served_model` falls back to `requested_model` for a request that never reached a
    model, so a `404 model_not_found` row carries the string out of the URL. Measured against the
    hermetic app: one refused request for a model named `=1+1`, and the month's export by model
    carries `=1+1,1,0,…` as its first data row — a file every oversight role can download.

    Prefixed rather than stripped or refused: an export has to say what the audit trail holds, and
    the marker is not displayed by any spreadsheet that honours it.
    """
    report = {"by_model": [{**REPORT["by_use_case"][0], "key": formula}]}

    key = _rows(render(report, "model", "EUR"))[1][0]

    assert key == "'" + formula, key
    assert not key.startswith(("=", "+", "-", "@", "\t", "\r"))


def test_an_ordinary_key_is_not_decorated() -> None:
    """The narrowing the guard above must not do: a name that is not a formula is written as it is,
    because a reader comparing an export against a screen must find the same string."""
    assert _rows(render(REPORT, "use_case", "EUR"))[1][0] == "kundenservice"


def test_the_header_names_the_currency_rather_than_leaving_cost_ambiguous() -> None:
    """One installation, one currency (`FRD-403`) — but a column called `cost` in a file that gets
    forwarded is a number without a unit."""
    header = _rows(render(REPORT, "use_case", "EUR"))[0]
    assert "cost_eur" in header


def test_every_column_of_the_report_row_is_present() -> None:
    header = _rows(render(REPORT, "use_case", "EUR"))[0]
    for column in ("key", "requests", "failed", "total_tokens", "unpriced_requests"):
        assert column in header
    assert "avg_latency_ms" in header and "max_latency_ms" in header


def test_money_is_rendered_for_people_not_in_nano_units() -> None:
    """A spreadsheet is read by people, and a column of integers in billionths is one nobody can
    sum in their head. The exact integer stays in the JSON, which is what a script should read."""
    rows = _rows(render(REPORT, "use_case", "EUR"))
    assert rows[1][6] == "1.50"


def test_the_line_terminator_is_the_one_the_format_specifies() -> None:
    """RFC 4180 says CRLF, and Excel expects it from a file it did not write."""
    assert "\r\n" in render(REPORT, "use_case", "EUR")


# == the breakdown is chosen, never guessed ======================================================


@pytest.mark.parametrize(
    ("breakdown", "expected"),
    [
        ("use_case", "kundenservice"),
        ("model", "mock-1"),
        ("member", "alice"),
    ],
)
def test_each_breakdown_renders_its_own_table(breakdown: str, expected: str) -> None:
    """A CSV is one table. Silently picking one of three would be a guess presented as a
    document."""
    assert expected in render(REPORT, breakdown, "EUR")


def test_an_unknown_breakdown_is_named_rather_than_defaulted() -> None:
    with pytest.raises(UnknownBreakdown, match="quarterly"):
        render(REPORT, "quarterly", "EUR")


def test_an_empty_breakdown_still_produces_a_header() -> None:
    """A file with no rows and no header is indistinguishable from a failed download."""
    rows = _rows(render({"by_use_case": []}, "use_case", "EUR"))
    assert rows[0][0] == "key"


# == unpriced traffic stays visible (FR-6) =======================================================


def test_a_period_containing_unpriced_traffic_carries_the_caveat() -> None:
    """The screen says the spend is a lower bound. A spreadsheet that dropped the caveat would
    understate spend in exactly the document where that matters most — the one that gets
    forwarded to somebody who was not in the room."""
    body = render(REPORT, "use_case", "EUR")
    assert "lower bound" in body
    assert "3 request(s)" in body


def test_a_fully_priced_period_carries_no_caveat() -> None:
    """A warning that is always present is one nobody reads."""
    priced = {"by_use_case": [{**REPORT["by_use_case"][0], "unpriced_requests": 0}]}
    assert "lower bound" not in render(priced, "use_case", "EUR")


def test_unpriced_requests_have_their_own_column_as_well() -> None:
    """The comment row is for a human skimming; the column is for whoever sums it."""
    rows = _rows(render(REPORT, "use_case", "EUR"))
    assert rows[2][7] == "3"


# == the filename ================================================================================


def test_the_filename_says_what_the_file_contains_and_sorts() -> None:
    name = filename("model", "2026-08-01T00:00:00+00:00", "2026-09-01T00:00:00+00:00")
    assert name == "aira-usage_model_2026-08-01_2026-09-01.csv"


# == negotiation over the endpoint ===============================================================


def _client() -> TestClient:
    return TestClient(create_app(GatewaySettings(auth_required=False, log_queue_size=0)))


def test_the_default_is_json() -> None:
    with _client() as client:
        response = client.get("/v1beta/reporting")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")


def test_csv_is_served_as_an_attachment_with_a_charset() -> None:
    with _client() as client:
        response = client.get("/v1beta/reporting", headers={"Accept": "text/csv"})

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    assert "charset=utf-8" in response.headers["content-type"]
    assert response.headers["content-disposition"].startswith("attachment;")
    assert "aira-usage_use_case_" in response.headers["content-disposition"]


def test_a_caller_cannot_put_a_formula_into_somebody_elses_export() -> None:
    """**The whole path, because the unit test above only proves the renderer.**

    A caller names a model that does not exist; `FRD-122` records the refusal, and
    `AuditTrail.served_model` falls back to what they asked for — so the string out of the URL
    becomes a `by_model` key. Measured before the fix: the month's export carried `=1+1,1,0,…` as
    its first data row, in a file every oversight role can download.

    The model name is slash-free on purpose: a `/` would not match the route's path segment at all,
    and a probe that 404s before any row is written proves nothing (it passed, first try).
    """
    with _client() as client:
        refused = client.post(
            "/v1beta/models/=1+1:generateContent",
            json={"contents": [{"role": "user", "parts": [{"text": "hi"}]}]},
            headers={"X-AIRA-Use-Case": "demo-uc"},
        )
        assert refused.status_code == 404, refused.text

        export = client.get(
            "/v1beta/reporting",
            params={"breakdown": "model"},
            headers={"Accept": "text/csv"},
        )

    keys = [row[0] for row in _rows(export.text)[1:] if row]

    assert "=1+1" not in keys, "a spreadsheet would evaluate this cell"
    assert "'=1+1" in keys, "and the row still has to be in the file"


@pytest.mark.parametrize("accept", ["application/xml", "text/html", "application/pdf"])
def test_a_format_this_endpoint_does_not_serve_is_a_406(accept: str) -> None:
    """A caller asking for XML is better told no than handed JSON: the second answer *looks* like
    it worked, and the mismatch surfaces in their parser rather than in ours."""
    with _client() as client:
        response = client.get("/v1beta/reporting", headers={"Accept": accept})
    assert response.status_code == 406


@pytest.mark.parametrize("accept", ["", "*/*", "application/json", "text/csv, */*;q=0.1"])
def test_the_headers_a_real_client_sends_are_all_understood(accept: str) -> None:
    """Browsers and HTTP libraries send `*/*` or nothing. An endpoint that 406'd on those would be
    one nobody could call from a browser at all."""
    with _client() as client:
        response = client.get("/v1beta/reporting", headers={"Accept": accept} if accept else {})
    assert response.status_code == 200


def test_an_unknown_breakdown_is_refused_before_the_query_runs() -> None:
    with _client() as client:
        response = client.get(
            "/v1beta/reporting", headers={"Accept": "text/csv"}, params={"breakdown": "nonsense"}
        )
    assert response.status_code == 400
    assert "nonsense" in response.json()["error"]["message"]


def test_the_window_bounds_apply_to_csv_exactly_as_to_json() -> None:
    """The format is chosen *after* every other rule has run, so nothing gets its own dialect of
    validation — which is the same argument as the scope rule below, one severity down."""
    with _client() as client:
        response = client.get(
            "/v1beta/reporting",
            headers={"Accept": "text/csv"},
            params={"from": "2020-01-01", "to": "2026-01-01"},
        )
    assert response.status_code == 400
    assert "at most" in response.json()["error"]["message"]


# == the scope rule, asserted on the file's bytes (§5.3) =========================================


@pytest.fixture
async def stack():
    """The endpoint over a **real** reporting service holding two use cases' traffic.

    Assembled rather than stubbed, and driven through HTTP rather than by calling the pieces,
    because the mutation that survived the first version of this test proved the gap: `FRD-601`'s
    scope tests check `visible_scope` in isolation and the service tests check the query in
    isolation, and *nothing* checked that the endpoint connects them. Two correct halves and no
    wire between them is precisely the shape of an export that returns more than the screen.
    """
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from sqlalchemy.pool import StaticPool

    from aira_gateway.db.base import Base
    from aira_gateway.db.models import RequestLog
    from aira_gateway.reporting.service import ReportingService

    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)

    async with sessionmaker() as session:
        for use_case, subject in (("kundenservice", "alice"), ("vertrieb", "bob")):
            session.add(
                RequestLog(
                    subject=subject,
                    auth_method="api_key",
                    use_case=use_case,
                    api="gemini",
                    operation="generateContent",
                    model=f"model-for-{use_case}",
                    status=200,
                    prompt_tokens=10,
                    completion_tokens=20,
                    total_tokens=30,
                    cost_nanos=1_000_000_000,
                    latency_ms=40,
                    created_at=datetime.now(UTC),
                )
            )
        await session.commit()

    app = create_app(GatewaySettings(auth_required=False, log_queue_size=0))
    app.state.reporting = ReportingService(sessionmaker)
    yield app
    await engine.dispose()


def _as(app: Any, principal: Any) -> TestClient:
    """Drive the endpoint as a specific caller, leaving every other rule in place."""
    from aira_gateway.auth.dependencies import require_principal

    app.dependency_overrides[require_principal] = lambda: principal
    return TestClient(app)


#: **Real `Principal`s, not stand-ins** — corrected 2026-08-08.
#:
#: These were hand-written classes carrying `is_governance` and the fields the endpoint reads. The
#: day `visible_scope` started asking `is_oversight` instead, they raised `AttributeError` and five
#: exports failed — not because the code was wrong, but because a stand-in cannot follow a
#: predicate it does not have.
#:
#: CLAUDE.md §3 already names this trap from the other direction ("a stand-in that is more
#: permissive than the thing it replaces"); this is the same trap with the sign flipped, and the
#: same answer: use the real class, which then cannot drift from it.


def _member() -> Principal:
    """A caller entitled to one use case and holding no oversight role."""
    return Principal(
        subject="member", method="oidc", credential="test", use_cases=("kundenservice",)
    )


def _governance() -> Principal:
    return Principal(subject="auditor", method="oidc", credential="test", roles=("it-steuerung",))


@pytest.mark.parametrize("breakdown", ["use_case", "model", "member"])
async def test_a_caller_without_oversight_exports_only_their_own_use_cases(
    stack, breakdown: str
) -> None:
    """**The test `FRD-602` §5.3 exists for.** An export that returned more than the screen would
    be a governance failure delivered as a file — forwarded, saved, and impossible to recall.

    Asserted on the bytes of the downloaded file, over a real query, through the real endpoint. And
    on **every** breakdown: the scope is applied once to the report, so a breakdown that leaked
    would leak the model and member names rather than the use case's — the same disclosure wearing
    a different column heading.
    """
    with _as(stack, _member()) as client:
        response = client.get(
            "/v1beta/reporting", headers={"Accept": "text/csv"}, params={"breakdown": breakdown}
        )

    assert response.status_code == 200
    body = response.content.decode("utf-8")
    assert "vertrieb" not in body, "the export returned a use case the caller cannot see"
    assert "bob" not in body
    assert ("kundenservice" in body) or ("alice" in body)


async def test_the_same_caller_sees_the_same_use_cases_in_json_and_in_csv(stack) -> None:
    """The formats are two renderings of one answer. If they could disagree, the safe one would be
    the one everybody reads and the leaky one the one that gets forwarded."""
    with _as(stack, _member()) as client:
        as_json = client.get("/v1beta/reporting").json()
        as_csv = client.get("/v1beta/reporting", headers={"Accept": "text/csv"}).content.decode()

    keys = {row["key"] for row in as_json["by_use_case"]}
    assert keys == {"kundenservice"}
    for key in keys:
        assert key in as_csv
    assert "vertrieb" not in as_csv


async def test_oversight_exports_every_use_case(stack) -> None:
    """The other half of the rule. A test that only showed somebody being *excluded* would pass
    against an export that returned nothing at all to anyone."""
    with _as(stack, _governance()) as client:
        body = client.get("/v1beta/reporting", headers={"Accept": "text/csv"}).content.decode()

    assert "kundenservice" in body
    assert "vertrieb" in body


def test_every_endpoint_here_resolves_the_visible_scope_exactly_once() -> None:
    """A second endpoint that queried and formatted is how an export comes to return more than the
    screen it was exported from. `visible_scope` is one function, guarded by its own mutations.

    Originally this counted the calls in the *module* and meant "the CSV path has not grown its
    own". `FRD-501` added a second, legitimate endpoint here — the anomaly list, which is scoped by
    the very same function — and the assertion went red for the right reason and the wrong
    statement. Now it says what it meant: **each** endpoint in this module resolves the scope
    exactly once. Which is the stronger property, because it also catches an endpoint that resolves
    it *zero* times — and it did, immediately: `FRD-503`'s suspension endpoints are bounded by
    **role** rather than by use case, so they belong in `api/incidents.py` and not behind this
    module's heading. Two different ways of being safe do not share a file.
    """
    import inspect

    from aira_gateway.api import reporting as module

    endpoints = {route.endpoint for route in module.router.routes}
    assert endpoints, "no routes found — the assertion would pass by describing nothing"
    for endpoint in endpoints:
        calls = inspect.getsource(endpoint).count("visible_scope(principal)")
        assert calls == 1, (
            f"{endpoint.__name__} resolves the visible scope {calls} times; "
            "zero shows more than it should, twice will drift"
        )

    assert "def render" not in inspect.getsource(module), "the CSV path grew its own query"
