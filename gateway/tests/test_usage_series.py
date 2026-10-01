"""The usage series: a period bucket by bucket, split into bands (`FRD-626`).

Two kinds of property here, and they fail differently. The **arithmetic** ones — which bucket a
timestamp falls in, which buckets exist at all — go wrong by one and are only ever noticed at a
particular hour of a particular day. The **folding** one decides what a legend says, and it goes
wrong by showing a band whose rows are somewhere else.

The visibility rule is not retested here: the series is computed inside the one `report()` call
whose scope `test_reporting.py` pins in all three cases, from the same `_window`. What *is* tested
is that the parameter cannot reach around it (`test_the_series_is_scoped_like_every_other_figure`).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from aira_common.money import to_nanos
from aira_gateway.app import create_app
from aira_gateway.auth.dependencies import require_principal
from aira_gateway.auth.principal import Principal
from aira_gateway.config import GatewaySettings
from aira_gateway.db.base import Base
from aira_gateway.db.models import RequestLog
from aira_gateway.reporting import series as series_module
from aira_gateway.reporting.series import MAX_BUCKETS, TooManyBuckets, axis, label, resolve
from aira_gateway.reporting.service import ReportingService

AUGUST = datetime(2026, 8, 1, tzinfo=UTC)
SEPTEMBER = datetime(2026, 9, 1, tzinfo=UTC)


@pytest.fixture
async def sessionmaker():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


async def _log(
    sessionmaker,
    *,
    use_case: str | None = "uc",
    model: str = "chat-1",
    when: datetime = AUGUST,
    cost: int | None = 1000,
    prompt: int = 10,
    completion: int = 20,
    status: int = 200,
    outcome: str | None = "served",
) -> None:
    async with sessionmaker() as session:
        session.add(
            RequestLog(
                subject="alice",
                auth_method="api_key",
                use_case=use_case,
                api="gemini",
                operation="generateContent",
                model=model,
                status=status,
                prompt_tokens=prompt,
                completion_tokens=completion,
                total_tokens=prompt + completion,
                cost_nanos=cost,
                latency_ms=40,
                outcome=outcome,
                created_at=when,
            )
        )
        await session.commit()


async def _series(sessionmaker, /, **kwargs):
    report = await ReportingService(sessionmaker).report(
        kwargs.pop("scope", None),
        kwargs.pop("start", AUGUST),
        kwargs.pop("end", SEPTEMBER),
        granularity=kwargs.pop("granularity", "day"),
        **kwargs,
    )
    return report["series"]


# ---- the axis: which columns exist at all -------------------------------------------------


def test_the_axis_holds_every_day_of_the_window_including_the_quiet_ones() -> None:
    """A histogram drawn only from days that have rows puts Monday beside Friday and calls it a
    week. An empty day is a reading."""
    days = axis(AUGUST, SEPTEMBER, "day")

    assert len(days) == 31
    assert days[0] == "2026-08-01"
    assert days[-1] == "2026-08-31"


def test_the_axis_stops_before_the_exclusive_bound() -> None:
    """The window is half-open everywhere else on this surface (`FRD-601` FR-1); an axis that
    included `to` would draw a column for a day the figures deliberately exclude."""
    assert axis(AUGUST, AUGUST + timedelta(days=2), "day") == ["2026-08-01", "2026-08-02"]


def test_an_hourly_axis_is_exactly_as_long_as_the_window() -> None:
    hours = axis(AUGUST, AUGUST + timedelta(days=1), "hour")

    assert len(hours) == 24
    assert hours[0] == "2026-08-01T00"
    assert hours[-1] == "2026-08-01T23"


def test_a_window_starting_mid_bucket_still_gets_that_bucket_on_the_axis() -> None:
    """Otherwise the first column of the chart would be the one *after* the traffic the caller
    asked about, and the earliest rows would have nowhere to land."""
    assert axis(AUGUST + timedelta(hours=5), AUGUST + timedelta(days=1), "day") == ["2026-08-01"]


# ---- the granularity: asked for, or chosen from the window --------------------------------


@pytest.mark.parametrize(
    ("hours", "expected"),
    [(1, "hour"), (24, "hour"), (48, "hour"), (49, "day"), (24 * 31, "day")],
)
def test_auto_reads_the_window(hours: int, expected: str) -> None:
    assert resolve(AUGUST, AUGUST + timedelta(hours=hours), "auto") == expected


def test_a_named_granularity_is_honoured_over_what_auto_would_choose() -> None:
    """A chart whose axis silently coarsened would be answering a question nobody asked."""
    assert resolve(AUGUST, AUGUST + timedelta(hours=1), "day") == "day"


def test_an_hourly_series_too_wide_to_draw_is_refused_by_name() -> None:
    """Rather than quietly served as a day series: see above. The message names the cap and the
    granularity that would work, because a 400 that only says "no" costs a round trip to find out
    what to send instead (`FRD-206`)."""
    with pytest.raises(TooManyBuckets) as refusal:
        resolve(AUGUST, AUGUST + timedelta(hours=MAX_BUCKETS + 1), "hour")

    assert str(MAX_BUCKETS) in str(refusal.value)
    assert "'day'" in str(refusal.value)


def test_the_widest_hourly_window_is_allowed() -> None:
    """The boundary itself, because `>` and `>=` are the same typo away from each other."""
    assert resolve(AUGUST, AUGUST + timedelta(hours=MAX_BUCKETS), "hour") == "hour"


# ---- the buckets: a timestamp lands where the clock says -----------------------------------


async def test_a_request_lands_in_the_bucket_of_the_day_it_arrived(sessionmaker) -> None:
    await _log(sessionmaker, when=AUGUST + timedelta(days=3, hours=14))

    series = await _series(sessionmaker)

    assert [(p["bucket"], p["requests"]) for p in series["points"]] == [("2026-08-04", 1)]


async def test_the_last_minute_of_a_day_is_not_the_next_day(sessionmaker) -> None:
    """The reason the bucket is computed from the stored timestamp in UTC and nothing else
    (`ADR-0028`). Rendered in a zone an hour ahead, this row would be tomorrow's."""
    await _log(sessionmaker, when=AUGUST + timedelta(hours=23, minutes=59, seconds=59))

    series = await _series(sessionmaker)

    assert [p["bucket"] for p in series["points"]] == ["2026-08-01"]


async def test_an_hourly_bucket_is_named_as_an_iso_prefix(sessionmaker) -> None:
    """`2026-08-01T14`, not the space the stores render, so a client can parse a label without
    knowing which database produced it."""
    await _log(sessionmaker, when=AUGUST + timedelta(hours=14, minutes=30))

    series = await _series(sessionmaker, end=AUGUST + timedelta(days=1), granularity="hour")

    assert [p["bucket"] for p in series["points"]] == ["2026-08-01T14"]


async def test_every_bucket_the_database_names_is_on_the_axis(sessionmaker) -> None:
    """The one property that keeps the two halves wired together: the axis is arithmetic in this
    process and the labels are text out of the store. If they ever disagree the chart has a column
    it cannot place, which is how a day's spend disappears from a screen that looks complete."""
    for hour in (0, 1, 12, 23):
        await _log(sessionmaker, when=AUGUST + timedelta(days=17, hours=hour))

    for granularity in ("day", "hour"):
        series = await _series(
            sessionmaker,
            start=AUGUST + timedelta(days=17),
            end=AUGUST + timedelta(days=18),
            granularity=granularity,
        )
        assert {p["bucket"] for p in series["points"]} <= set(series["buckets"])


async def test_the_figures_in_a_bucket_are_the_same_figures_as_the_breakdown(sessionmaker) -> None:
    """A chart that does not add up to the table under it is a chart nobody can use to argue."""
    await _log(sessionmaker, when=AUGUST + timedelta(days=1), cost=to_nanos("1.50"), completion=7)
    await _log(sessionmaker, when=AUGUST + timedelta(days=9), cost=to_nanos("2.25"), completion=3)

    report = await ReportingService(sessionmaker).report(
        None, AUGUST, SEPTEMBER, granularity="day", split="model"
    )
    points = report["series"]["points"]

    assert sum(p["cost_nanos"] for p in points) == report["totals"]["cost_nanos"]
    assert sum(p["requests"] for p in points) == report["totals"]["requests"]
    assert sum(p["completion_tokens"] for p in points) == report["totals"]["completion_tokens"]


async def test_a_request_outside_the_window_is_in_no_bucket(sessionmaker) -> None:
    await _log(sessionmaker, when=SEPTEMBER)
    await _log(sessionmaker, when=AUGUST - timedelta(seconds=1))

    series = await _series(sessionmaker)

    assert series["points"] == []


# ---- the bands: what the legend says ------------------------------------------------------


async def test_a_bucket_carries_one_point_per_band(sessionmaker) -> None:
    """Which is the whole feature: *which model*, when, cost what."""
    day = AUGUST + timedelta(days=2)
    await _log(sessionmaker, when=day, model="chat-1", cost=to_nanos("3.00"))
    await _log(sessionmaker, when=day, model="embed-1", cost=to_nanos("0.10"))

    series = await _series(sessionmaker)

    by_key = {p["key"]: p for p in series["points"]}
    assert set(by_key) == {"chat-1", "embed-1"}
    assert by_key["chat-1"]["cost"] == "3.00"
    assert by_key["embed-1"]["cost"] == "0.10"
    assert all(p["bucket"] == "2026-08-03" for p in series["points"])


@pytest.mark.parametrize(
    ("split", "expected"),
    [("model", {"chat-1", "embed-1"}), ("use_case", {"alpha", "beta"}), ("outcome", {"served"})],
)
async def test_each_split_bands_by_its_own_column(sessionmaker, split, expected) -> None:
    await _log(sessionmaker, use_case="alpha", model="chat-1")
    await _log(sessionmaker, use_case="beta", model="embed-1")

    series = await _series(sessionmaker, split=split)

    assert {p["key"] for p in series["points"]} == expected
    assert series["split"] == split


async def test_the_bands_are_ordered_biggest_first_so_a_colour_belongs_to_a_band(
    sessionmaker,
) -> None:
    """Colour follows the entity, not its rank on screen — which is only possible if the order is
    computed from the period rather than from whatever the database returned first."""
    await _log(sessionmaker, model="cheap", cost=to_nanos("0.01"))
    await _log(sessionmaker, model="dear", cost=to_nanos("9.00"))
    await _log(sessionmaker, model="middling", cost=to_nanos("1.00"))

    series = await _series(sessionmaker)

    assert series["keys"] == ["dear", "middling", "cheap"]


async def test_an_unpriced_period_still_has_a_stable_band_order(sessionmaker) -> None:
    """Every cost is zero here, so ordering by cost alone leaves the order to the store. Traffic
    then decides, and the name breaks the last tie — because "whatever came back first" is an
    order that changes between two loads of the same screen."""
    await _log(sessionmaker, model="busy", cost=None)
    await _log(sessionmaker, model="busy", cost=None)
    await _log(sessionmaker, model="quiet", cost=None)

    series = await _series(sessionmaker)

    assert series["keys"] == ["busy", "quiet"]


async def test_two_bands_alike_in_every_figure_are_ordered_by_name(sessionmaker) -> None:
    """The last tie, and it has to break somewhere. A to Z, because the alternative is whatever the
    store returned first — a legend that reorders itself between two loads of one screen."""
    await _log(sessionmaker, model="zulu", cost=1000)
    await _log(sessionmaker, model="alpha", cost=1000)

    assert (await _series(sessionmaker))["keys"] == ["alpha", "zulu"]


async def test_the_tail_beyond_the_colours_folds_into_one_band(sessionmaker) -> None:
    """A ninth hue is indistinguishable from an existing one under colour-vision deficiency, so
    the ninth band is not a hue — it is `(other)` (`FRD-626` FR-6)."""
    for index in range(10):
        await _log(sessionmaker, model=f"model-{index}", cost=to_nanos(f"{10 - index}.00"))

    series = await _series(sessionmaker, top=3)

    assert series["keys"] == ["model-0", "model-1", "model-2", series_module.OTHER]
    assert series["folded"] is True
    folded = next(p for p in series["points"] if p["key"] == series_module.OTHER)
    # 7 + 6 + … + 1
    assert folded["cost"] == "28.00"
    assert folded["requests"] == 7


async def test_nothing_is_folded_when_every_band_has_a_colour(sessionmaker) -> None:
    """`(other)` on a legend with nothing behind it is a band a reader goes looking for."""
    await _log(sessionmaker, model="only-one")

    series = await _series(sessionmaker, top=3)

    assert series["keys"] == ["only-one"]
    assert series["folded"] is False


async def test_the_folded_tail_does_not_change_the_total(sessionmaker) -> None:
    """The failure this guards is folding by *dropping*: a chart that adds up to less than the
    figure above it, with no row admitting where the rest went."""
    for index in range(10):
        await _log(sessionmaker, model=f"model-{index}", cost=to_nanos("1.00"))

    report = await ReportingService(sessionmaker).report(
        None, AUGUST, SEPTEMBER, granularity="day", top=2
    )

    assert sum(p["cost_nanos"] for p in report["series"]["points"]) == 10 * to_nanos("1.00")
    assert sum(p["requests"] for p in report["series"]["points"]) == 10


async def test_a_band_with_no_name_is_itself_and_not_the_tail(sessionmaker) -> None:
    """A request that names no use case is a real group (break-glass traffic, the console's own
    model checks). Folding it into `(other)` would hide it behind a label that means
    "several small ones"."""
    await _log(sessionmaker, use_case=None, cost=to_nanos("5.00"))
    await _log(sessionmaker, use_case="named", cost=to_nanos("1.00"))

    series = await _series(sessionmaker, split="use_case", top=7)

    assert series["keys"] == ["(none)", "named"]
    assert {p["key"] for p in series["points"]} == {"(none)", "named"}


async def test_a_refused_request_is_in_the_series_like_every_other(sessionmaker) -> None:
    """`FRD-601` FR-6: it took a rate-limit slot and possibly an upstream call. A chart that drops
    refusals makes a use case grinding against its limit look like a quiet one."""
    await _log(sessionmaker, status=429, outcome="budget_exceeded", cost=None)

    series = await _series(sessionmaker, split="outcome")

    assert [(p["key"], p["requests"]) for p in series["points"]] == [("budget_exceeded", 1)]


async def test_unpriced_traffic_is_counted_apart_in_the_series_too(sessionmaker) -> None:
    """The caveat has to travel with the figure wherever the figure goes (`FRD-403` §4.4), and a
    column drawn at zero height for traffic that genuinely ran is the shape of that mistake."""
    await _log(sessionmaker, cost=None)

    point = (await _series(sessionmaker))["points"][0]

    assert point["unpriced_requests"] == 1
    assert point["cost_nanos"] == 0


async def test_the_series_is_scoped_like_every_other_figure(sessionmaker) -> None:
    """The series is computed inside the one `report()` whose scope is already pinned, from the same
    `_window`. Asserted anyway: it is a new `select`, and a new select is a new chance to build the
    window by hand."""
    await _log(sessionmaker, use_case="mine", cost=to_nanos("1.00"))
    await _log(sessionmaker, use_case="theirs", cost=to_nanos("99.00"))

    series = await _series(sessionmaker, scope=("mine",), split="use_case")

    assert {p["key"] for p in series["points"]} == {"mine"}
    assert sum(p["cost_nanos"] for p in series["points"]) == to_nanos("1.00")


async def test_a_report_nobody_asked_a_series_of_does_not_carry_one(sessionmaker) -> None:
    """The use-case overview loads this report twice per page and draws no chart on it."""
    await _log(sessionmaker)

    report = await ReportingService(sessionmaker).report(None, AUGUST, SEPTEMBER)

    assert "series" not in report


# ---- the endpoint ------------------------------------------------------------------------


@pytest.fixture
def client(sessionmaker, monkeypatch):
    monkeypatch.setenv("AIRA_DEMO_MODE", "false")
    app = create_app(GatewaySettings(oidc_enabled=False))
    app.state.reporting = ReportingService(sessionmaker)
    app.dependency_overrides[require_principal] = lambda: Principal(
        subject="s", method="oidc", roles=("it-steuerung",)
    )
    with TestClient(app) as test_client:
        yield test_client


def test_the_endpoint_serves_no_series_unless_one_is_asked_for(client) -> None:
    assert "series" not in client.get("/v1beta/reporting").json()


def test_the_endpoint_serves_a_series_when_asked(client) -> None:
    body = client.get("/v1beta/reporting", params={"series": "day"}).json()

    assert body["series"]["granularity"] == "day"
    assert body["series"]["split"] == "model"


def test_a_granularity_this_endpoint_does_not_have_is_named(client) -> None:
    response = client.get("/v1beta/reporting", params={"series": "weekly"})

    assert response.status_code == 400
    assert "weekly" in response.json()["error"]["message"]
    assert "day" in response.json()["error"]["message"]


def test_a_split_this_endpoint_does_not_have_is_named(client) -> None:
    """A 422 from the framework would carry a different error envelope from every other refusal on
    this surface, so a client would have to parse two (`FRD-206`)."""
    response = client.get("/v1beta/reporting", params={"split": "subject"})

    assert response.status_code == 400
    assert "subject" in response.json()["error"]["message"]
    assert "model" in response.json()["error"]["message"]


def test_an_hourly_series_over_a_year_is_refused_with_the_surfaces_own_envelope(client) -> None:
    response = client.get(
        "/v1beta/reporting",
        params={"from": "2026-01-01", "to": "2026-12-01", "series": "hour"},
    )

    assert response.status_code == 400
    assert response.json()["error"]["status"] == "INVALID_ARGUMENT"


# ---- the label helpers, directly ----------------------------------------------------------


@pytest.mark.parametrize(
    ("rendered", "granularity", "expected"),
    [
        # What each store actually hands back for the same instant.
        ("2026-08-01 14:30:00.000000", "hour", "2026-08-01T14"),
        ("2026-08-01 14:30:00+00", "hour", "2026-08-01T14"),
        ("2026-08-01 14:30:00.000000", "day", "2026-08-01"),
        ("2026-08-01 14:30:00+00", "day", "2026-08-01"),
    ],
)
def test_both_stores_renderings_normalise_to_one_label(rendered, granularity, expected) -> None:
    """The bucket is a text prefix precisely because both dialects render a timestamp the same way
    (`ADR-0028`). These are the two renderings, side by side, so a change to either is visible."""
    assert series_module.normalise(rendered, granularity) == expected


def test_a_label_round_trips_through_the_axis() -> None:
    """`label` writes the axis and `normalise` reads the rows; if they ever disagree, every point
    lands off-axis at once."""
    moment = datetime(2026, 8, 1, 14, 30, tzinfo=UTC)

    for granularity in ("day", "hour"):
        assert series_module.normalise(
            moment.strftime("%Y-%m-%d %H:%M:%S.%f"), granularity
        ) == label(moment, granularity)


# ---- one person's own series (`FRD-626` FR-15) ---------------------------------------------
#
# What a member opens their own use case to see. The card beside it on that page already says what
# *they* used (`FRD-606`); this is the same subject, over time.


async def _logged_as(sessionmaker, *, subject: str, username: str | None, **kwargs) -> None:
    async with sessionmaker() as session:
        session.add(
            RequestLog(
                subject=subject,
                username=username,
                auth_method=kwargs.pop("auth", "api_key"),
                use_case=kwargs.pop("use_case", "uc"),
                api="gemini",
                operation="generateContent",
                model=kwargs.pop("model", "chat-1"),
                status=200,
                prompt_tokens=10,
                completion_tokens=20,
                total_tokens=30,
                cost_nanos=kwargs.pop("cost", 1000),
                latency_ms=40,
                outcome=kwargs.pop("outcome", "served"),
                created_at=kwargs.pop("when", AUGUST),
            )
        )
        await session.commit()


async def test_a_person_filter_leaves_only_that_person_in_the_series(sessionmaker) -> None:
    await _logged_as(sessionmaker, subject="erika", username="erika", cost=to_nanos("3.00"))
    await _logged_as(sessionmaker, subject="hans", username="hans", cost=to_nanos("99.00"))

    series = await _series(sessionmaker, person="erika")

    assert sum(point["cost_nanos"] for point in series["points"]) == to_nanos("3.00")


async def test_the_same_person_through_two_credentials_is_one_series(sessionmaker) -> None:
    """The join `FRD-606` exists for: an OIDC subject is a directory id and an API key's is its
    owner's username, so filtering on `subject` alone would show a person half their own traffic."""
    await _logged_as(
        sessionmaker, subject="kc-uuid-1", username="erika", auth="oidc", cost=to_nanos("1.00")
    )
    await _logged_as(sessionmaker, subject="erika", username="erika", cost=to_nanos("0.50"))

    series = await _series(sessionmaker, person="erika")

    assert sum(point["cost_nanos"] for point in series["points"]) == to_nanos("1.50")


async def test_a_row_the_credential_named_nobody_for_is_found_under_its_subject(
    sessionmaker,
) -> None:
    """`_PERSON` is `coalesce(username, subject)` everywhere, and the filter has to agree with the
    grouping — otherwise a person's own card shows a figure their own row is missing from."""
    await _logged_as(sessionmaker, subject="kc-uuid-9", username=None, cost=to_nanos("2.00"))

    series = await _series(sessionmaker, person="kc-uuid-9")

    assert sum(point["cost_nanos"] for point in series["points"]) == to_nanos("2.00")


async def test_a_person_filter_narrows_and_cannot_widen(sessionmaker) -> None:
    """**The security-relevant line.** One predicate added to the window the caller's scope has
    already bounded, so naming somebody in a use case this caller may not see reaches nothing."""
    await _logged_as(
        sessionmaker, subject="hans", username="hans", use_case="theirs", cost=to_nanos("99.00")
    )
    await _logged_as(
        sessionmaker, subject="erika", username="erika", use_case="mine", cost=to_nanos("1.00")
    )

    series = await _series(sessionmaker, scope=("mine",), person="hans")

    assert series["points"] == []


async def test_a_person_filter_narrows_the_whole_report_and_not_only_the_chart(
    sessionmaker,
) -> None:
    """One predicate on the one window (`_window`), so the figure above the chart and the chart say
    the same thing. Two filtering paths would be two answers to *whose traffic is this*."""
    await _logged_as(sessionmaker, subject="erika", username="erika", cost=to_nanos("3.00"))
    await _logged_as(sessionmaker, subject="hans", username="hans", cost=to_nanos("99.00"))

    report = await ReportingService(sessionmaker).report(
        None, AUGUST, SEPTEMBER, granularity="day", person="erika"
    )

    assert report["totals"]["cost"] == "3.00"
    assert [row["key"] for row in report["by_person"]] == ["erika"]
    assert sum(point["cost_nanos"] for point in report["series"]["points"]) == to_nanos("3.00")


async def test_a_report_with_no_person_is_about_everybody(sessionmaker) -> None:
    """The empty string is *no filter*, not a person called "". Confused, every unfiltered report
    on this surface would answer zero."""
    await _logged_as(sessionmaker, subject="erika", username="erika", cost=to_nanos("3.00"))
    await _logged_as(sessionmaker, subject="hans", username="hans", cost=to_nanos("2.00"))

    report = await ReportingService(sessionmaker).report(None, AUGUST, SEPTEMBER, person=None)

    assert report["totals"]["cost"] == "5.00"


def test_the_endpoint_says_who_the_report_was_narrowed_to(client) -> None:
    body = client.get("/v1beta/reporting", params={"person": "erika"}).json()

    assert body["person"] == "erika"
    assert client.get("/v1beta/reporting").json()["person"] is None


def test_an_empty_person_parameter_is_no_filter(client) -> None:
    """A client that sends `person=` because its own value was absent must get the whole report,
    not an empty one."""
    assert client.get("/v1beta/reporting", params={"person": ""}).json()["person"] is None


# ---- unknown has two causes, and they need different things done (`FRD-626` FR-18) ----------


async def _unmetered(sessionmaker, **kwargs) -> None:  # noqa: D417
    """A served request the upstream reported **no usage** for: no tokens, so nothing to price.

    This is what a local embedding runtime produces, and it is the row that was being shown as
    `0.00` on a model whose price is on file.
    """
    async with sessionmaker() as session:
        session.add(
            RequestLog(
                subject="alice",
                auth_method="api_key",
                use_case="uc",
                api="gemini",
                operation="batchEmbedContents",
                model=kwargs.pop("model", "embed-1"),
                status=kwargs.pop("status", 200),
                prompt_tokens=None,
                completion_tokens=None,
                total_tokens=None,
                cost_nanos=None,
                latency_ms=40,
                outcome=kwargs.pop("outcome", "served"),
                created_at=kwargs.pop("when", AUGUST),
            )
        )
        await session.commit()


async def test_a_request_nothing_was_reported_for_is_counted_apart(sessionmaker) -> None:
    """Both are "cost unknown" and they are not the same fact. One is fixed by adding a price; the
    other cannot be, because the upstream does not meter what it serves."""
    await _unmetered(sessionmaker, model="embed-1")
    await _log(sessionmaker, model="no-price", cost=None)

    report = await ReportingService(sessionmaker).report(None, AUGUST, SEPTEMBER)

    assert report["totals"]["unpriced_requests"] == 2
    assert report["totals"]["unmetered_requests"] == 1
    by_model = {row["key"]: row for row in report["by_model"]}
    assert by_model["embed-1"]["unmetered_requests"] == 1
    assert by_model["no-price"]["unmetered_requests"] == 0
    # …and the one with no price did report tokens, so it is unpriced and not unmetered.
    assert by_model["no-price"]["unpriced_requests"] == 1


async def test_a_priced_request_is_neither(sessionmaker) -> None:
    """The narrowing the counter must do: a request that *was* priced is in neither column, or the
    caveat would be permanent and a warning that is always there is one nobody reads."""
    await _log(sessionmaker, cost=to_nanos("1.00"))

    totals = (await ReportingService(sessionmaker).report(None, AUGUST, SEPTEMBER))["totals"]

    assert totals["unpriced_requests"] == 0
    assert totals["unmetered_requests"] == 0


async def test_a_refusal_is_not_counted_as_unmetered(sessionmaker) -> None:
    """Nothing ran, so there was nothing to meter. Counting it would make the caveat permanent for
    any use case with a rate limit — the same rule `unpriced_requests` already follows."""
    await _log(
        sessionmaker, status=429, outcome="budget_exceeded", cost=None, prompt=0, completion=0
    )

    totals = (await ReportingService(sessionmaker).report(None, AUGUST, SEPTEMBER))["totals"]

    assert totals["unmetered_requests"] == 0


async def test_the_series_carries_the_distinction_per_bucket(sessionmaker) -> None:
    """Because the chart's caveat is about the period on screen, and the day list shows a band whose
    spend is entirely unknown as unknown rather than as zero."""
    await _unmetered(sessionmaker, when=AUGUST + timedelta(days=2))

    series = await _series(sessionmaker)

    assert [p["unmetered_requests"] for p in series["points"]] == [1]
    assert [p["unpriced_requests"] for p in series["points"]] == [1]
