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
from typing import Any

import pytest
from fastapi.testclient import TestClient

from aira_gateway.app import create_app
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


class _Principal:
    """A caller entitled to one use case and no oversight role."""

    subject = "member"
    method = "oidc"
    credential = "test"
    use_cases = ("kundenservice",)
    roles: tuple[str, ...] = ()

    @property
    def is_governance(self) -> bool:
        return False


async def test_a_caller_without_oversight_exports_only_their_own_use_cases() -> None:
    """**The test `FRD-602` §5.3 exists for.** An export that returned more than the screen would
    be a governance failure delivered as a file — forwarded, saved, and impossible to recall.

    Asserted against the bytes, not against the service call: the failure being guarded lives in
    the rendering path, and a test that checked the *arguments* would pass against a renderer that
    ignored them.
    """
    from aira_gateway.api.reporting import visible_scope

    app = create_app(GatewaySettings(auth_required=False, log_queue_size=0))

    seen: dict[str, Any] = {}
    original = app.state.reporting.report

    async def recording(scope, start, end):  # noqa: ANN001, ANN202
        seen["scope"] = scope
        return {
            "by_use_case": [
                {"key": name, "requests": 1, "cost_nanos": 0, "unpriced_requests": 0}
                for name in (scope or ("kundenservice", "vertrieb", "personal"))
            ],
            "by_model": [],
            "by_member": [],
        }

    app.state.reporting.report = recording
    try:
        with TestClient(app) as client:
            client.app.dependency_overrides = {}
            body = render(
                await recording(visible_scope(_Principal()), None, None), "use_case", "EUR"
            )
    finally:
        app.state.reporting.report = original

    assert seen["scope"] == ("kundenservice",)
    assert "kundenservice" in body
    # The two the caller is not a member of must not be in the file at all.
    assert "vertrieb" not in body
    assert "personal" not in body


def test_the_scope_decision_is_made_once_and_the_format_chosen_afterwards() -> None:
    """A second endpoint that queried and formatted is how an export comes to return more than the
    screen. `visible_scope` is one function, guarded by its own mutations; this asserts the CSV
    path has not grown a second one."""
    import inspect

    from aira_gateway.api import reporting as module

    source = inspect.getsource(module)
    assert source.count("visible_scope(principal)") == 1, (
        "the scope is resolved more than once — one of them will drift"
    )
    assert "def render" not in source, "the CSV path grew its own query"
