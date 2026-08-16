"""The detection engine, measured against known rows (FRD-501)."""

from __future__ import annotations

import functools
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import anyio
import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from aira_gateway.anomalies import AnomalyService, evaluate_rule
from aira_gateway.anomalies.service import NOT_ENFORCED
from aira_gateway.app import create_app
from aira_gateway.audit import Outcome
from aira_gateway.auth.dependencies import require_principal
from aira_gateway.auth.principal import Principal
from aira_gateway.config import GatewaySettings
from aira_gateway.db.base import build_engine, build_sessionmaker, create_all
from aira_gateway.db.models import AnomalyEvent, AnomalyRuleRead, RequestLog

NOW = datetime(2026, 8, 7, 12, 0, tzinfo=UTC)


@pytest_asyncio.fixture
async def sessions() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = build_engine("sqlite+aiosqlite:///:memory:")
    await create_all(engine)
    yield build_sessionmaker(engine)
    await engine.dispose()


def _rule(**over) -> AnomalyRuleRead:
    values = {
        "id": 1,
        "use_case": "demo-uc",
        "name": "rule",
        "kind": "refusal_rate",
        "window_minutes": 15,
        "threshold": 50,
        "min_sample": 4,
        "action": "alert",
        "target": "subject",
        "action_minutes": None,
        "enabled": True,
    }
    values.update(over)
    return AnomalyRuleRead(**values)


def _log(minutes_ago: float = 1, **over) -> RequestLog:
    values = {
        "subject": "ada",
        "auth_method": "api_key",
        "use_case": "demo-uc",
        "api": "gemini",
        "operation": "generateContent",
        "model": "mock-1",
        "status": 200,
        "outcome": Outcome.SERVED.value,
        "created_at": NOW - timedelta(minutes=minutes_ago),
    }
    values.update(over)
    return RequestLog(**values)


async def _seed(sessions, *rows: RequestLog) -> None:
    async with sessions() as session:
        for row in rows:
            session.add(row)
        await session.commit()


async def _evaluate(sessions, rule: AnomalyRuleRead):
    async with sessions() as session:
        return await evaluate_rule(session, rule, NOW)


# ---- rates ----------------------------------------------------------------------------------


async def test_a_refusal_rate_above_the_threshold_is_found(sessions) -> None:
    await _seed(
        sessions,
        *[_log(outcome=Outcome.RATE_LIMITED.value) for _ in range(3)],
        *[_log() for _ in range(2)],
    )

    findings = await _evaluate(sessions, _rule(threshold=50))

    assert len(findings) == 1
    assert findings[0].target_value == "ada"
    assert findings[0].observed == 60
    assert findings[0].sample == 5


async def test_a_refusal_rate_below_the_threshold_is_not(sessions) -> None:
    await _seed(
        sessions,
        *[_log(outcome=Outcome.RATE_LIMITED.value) for _ in range(2)],
        *[_log() for _ in range(8)],
    )

    assert await _evaluate(sessions, _rule(threshold=50)) == []


async def test_a_rate_over_too_few_requests_says_nothing(sessions) -> None:
    """One refusal out of one request is 100 %, and it means nothing at all."""
    await _seed(sessions, _log(outcome=Outcome.RATE_LIMITED.value))

    assert await _evaluate(sessions, _rule(threshold=50, min_sample=4)) == []


async def test_a_client_hanging_up_counts_as_a_refusal(sessions) -> None:
    """One caller hanging up is not our failure; a thousand is exactly the shape a detector
    exists to surface — so `refusal_rate` counts everything that is not `served`."""
    await _seed(
        sessions,
        *[_log(outcome=Outcome.CLIENT_GONE.value) for _ in range(4)],
        *[_log() for _ in range(1)],
    )

    assert len(await _evaluate(sessions, _rule(threshold=50))) == 1


async def test_an_error_rate_counts_only_upstream_failures(sessions) -> None:
    """Refusals are ours, not the provider's: they must not inflate a rule about the provider."""
    await _seed(
        sessions,
        *[_log(outcome=Outcome.UPSTREAM_ERROR.value) for _ in range(2)],
        *[_log(outcome=Outcome.RATE_LIMITED.value) for _ in range(8)],
    )

    # 2 of 10 upstream failures. A `refusal_rate` rule on the same rows would be at 100 %.
    assert await _evaluate(sessions, _rule(kind="error_rate", threshold=50)) == []
    assert (await _evaluate(sessions, _rule(kind="error_rate", threshold=20)))[0].observed == 20
    assert (await _evaluate(sessions, _rule(threshold=50)))[0].observed == 100


async def test_a_blocked_prompt_rate_counts_the_pipeline_s_refusals(sessions) -> None:
    await _seed(
        sessions,
        *[_log(outcome=Outcome.BLOCKED_BY_PIPELINE.value) for _ in range(5)],
        *[_log() for _ in range(5)],
    )

    findings = await _evaluate(sessions, _rule(kind="blocked_prompt_rate", threshold=50))

    assert findings[0].observed == 50


# ---- payload size ----------------------------------------------------------------------------


async def test_large_payloads_are_measured_against_the_byte_figure(sessions) -> None:
    await _seed(
        sessions,
        *[_log(request_bytes=900_000) for _ in range(3)],
        *[_log(request_bytes=1_000) for _ in range(1)],
    )

    findings = await _evaluate(
        sessions, _rule(kind="payload_size", threshold=50, parameter=500_000)
    )

    assert findings[0].observed == 75
    assert "500000 bytes" in findings[0].detail


async def test_a_row_with_no_byte_count_is_left_out_of_both_sides(sessions) -> None:
    """Rows written before `FRD-501` have no size. Counting an unknown as small would make old
    traffic look innocent — so they are excluded from the denominator too."""
    await _seed(
        sessions,
        *[_log(request_bytes=900_000) for _ in range(3)],
        *[_log(request_bytes=None) for _ in range(20)],
    )

    findings = await _evaluate(
        sessions, _rule(kind="payload_size", threshold=90, parameter=500_000, min_sample=3)
    )

    assert findings[0].observed == 100
    assert findings[0].sample == 3


async def test_a_payload_rule_with_no_byte_figure_measures_nothing(sessions) -> None:
    await _seed(sessions, *[_log(request_bytes=900_000) for _ in range(5)])

    assert await _evaluate(sessions, _rule(kind="payload_size", threshold=1)) == []


# ---- ratios ----------------------------------------------------------------------------------


async def test_a_spend_spike_is_measured_against_the_previous_window(sessions) -> None:
    await _seed(
        sessions,
        *[_log(minutes_ago=5, cost_nanos=1_000_000) for _ in range(4)],
        *[_log(minutes_ago=20, cost_nanos=100_000) for _ in range(4)],
    )

    findings = await _evaluate(sessions, _rule(kind="spend_spike", threshold=200))

    assert findings[0].observed == 1000
    assert "against" in findings[0].detail


async def test_growth_from_nothing_is_not_a_spike(sessions) -> None:
    """Treating it as infinite would make every use case's first hour an incident, and the alert
    that fires on arrival is the one people switch off before it says anything true."""
    await _seed(sessions, *[_log(minutes_ago=5, cost_nanos=1_000_000) for _ in range(10)])

    assert await _evaluate(sessions, _rule(kind="spend_spike", threshold=200)) == []


async def test_a_request_spike_counts_rows_rather_than_money(sessions) -> None:
    await _seed(
        sessions,
        *[_log(minutes_ago=5) for _ in range(12)],
        *[_log(minutes_ago=20) for _ in range(4)],
    )

    findings = await _evaluate(sessions, _rule(kind="request_spike", threshold=200))

    assert findings[0].observed == 300


# ---- new source ------------------------------------------------------------------------------


async def test_an_address_never_seen_before_for_a_credential_is_found(sessions) -> None:
    await _seed(
        sessions,
        _log(minutes_ago=20, credential="ab12cd34", source_ip="10.0.0.1"),
        _log(minutes_ago=5, credential="ab12cd34", source_ip="10.0.0.1"),
        _log(minutes_ago=5, credential="ab12cd34", source_ip="203.0.113.9"),
    )

    findings = await _evaluate(
        sessions, _rule(kind="new_source_ip", target="credential", threshold=1, min_sample=0)
    )

    assert findings[0].target_value == "ab12cd34"
    assert "203.0.113.9" in findings[0].detail


async def test_a_credential_with_no_history_is_not_reported_as_new(sessions) -> None:
    """On the first evaluation after deployment every address is new, and reporting that would be
    reporting the deployment."""
    await _seed(sessions, _log(minutes_ago=5, credential="fresh", source_ip="203.0.113.9"))

    assert (
        await _evaluate(
            sessions, _rule(kind="new_source_ip", target="credential", threshold=1, min_sample=0)
        )
        == []
    )


# ---- scope -----------------------------------------------------------------------------------


async def test_a_rule_ignores_traffic_of_other_use_cases(sessions) -> None:
    await _seed(
        sessions,
        *[_log(use_case="other-uc", outcome=Outcome.RATE_LIMITED.value) for _ in range(10)],
    )

    assert await _evaluate(sessions, _rule(threshold=50)) == []


async def test_a_global_rule_sees_every_use_case(sessions) -> None:
    await _seed(
        sessions,
        *[
            _log(use_case=slug, subject=slug, outcome=Outcome.RATE_LIMITED.value)
            for slug in ("a-uc", "b-uc")
            for _ in range(5)
        ],
    )

    findings = await _evaluate(sessions, _rule(use_case=None, threshold=50))

    assert {f.target_value for f in findings} == {"a-uc", "b-uc"}


async def test_a_target_groups_the_measurement_by_what_the_action_lands_on(sessions) -> None:
    """A refusal rate averaged over a whole use case says nothing about the one caller producing
    it — so the measurement is per target, not per scope."""
    await _seed(
        sessions,
        *[_log(subject="noisy", outcome=Outcome.RATE_LIMITED.value) for _ in range(5)],
        *[_log(subject="quiet") for _ in range(20)],
    )

    findings = await _evaluate(sessions, _rule(threshold=50, min_sample=4))

    assert [f.target_value for f in findings] == ["noisy"]


async def test_an_unknown_kind_measures_nothing_rather_than_passing(sessions) -> None:
    await _seed(sessions, *[_log(outcome=Outcome.RATE_LIMITED.value) for _ in range(10)])

    assert await _evaluate(sessions, _rule(kind="not_a_kind", threshold=1)) == []


# ---- the service ------------------------------------------------------------------------------


async def _rule_row(sessions, **over) -> None:
    async with sessions() as session:
        session.add(_rule(**over))
        await session.commit()


async def _events(sessions) -> list[AnomalyEvent]:
    async with sessions() as session:
        return list((await session.execute(select(AnomalyEvent))).scalars().all())


async def test_a_tick_with_no_traffic_does_nothing(sessions) -> None:
    """A quiet installation with 200 use cases should not run 200 queries a minute forever."""
    await _rule_row(sessions)
    service = AnomalyService(sessions)

    assert await service.tick(NOW) == []
    assert await _events(sessions) == []


async def test_a_tick_writes_what_it_found(sessions) -> None:
    await _rule_row(sessions)
    await _seed(sessions, *[_log(outcome=Outcome.RATE_LIMITED.value) for _ in range(10)])
    service = AnomalyService(sessions)
    service.touch("demo-uc")

    await service.tick(NOW)

    events = await _events(sessions)
    assert len(events) == 1
    assert events[0].rule_name == "rule"
    assert events[0].target_value == "ada"
    assert events[0].observed == 100
    assert events[0].threshold == 50
    assert events[0].sample == 10
    assert events[0].action_taken == "alert"


async def test_the_same_finding_is_not_written_again_inside_its_window(sessions) -> None:
    """A 15-minute window evaluated every minute would fire fifteen times about the same fifteen
    minutes, and each event would describe traffic the previous one already described."""
    await _rule_row(sessions)
    await _seed(sessions, *[_log(outcome=Outcome.RATE_LIMITED.value) for _ in range(10)])
    service = AnomalyService(sessions)

    service.touch("demo-uc")
    await service.tick(NOW)
    service.touch("demo-uc")
    await service.tick(NOW + timedelta(minutes=1))

    assert len(await _events(sessions)) == 1


async def test_the_finding_is_written_again_once_the_window_has_passed(sessions) -> None:
    await _rule_row(sessions)
    await _seed(sessions, *[_log(outcome=Outcome.RATE_LIMITED.value) for _ in range(10)])
    service = AnomalyService(sessions)

    service.touch("demo-uc")
    await service.tick(NOW)

    # The condition is still there a window later — fresh traffic, not the rows already reported.
    later = NOW + timedelta(minutes=16)
    await _seed(
        sessions,
        *[
            _log(minutes_ago=-15, outcome=Outcome.RATE_LIMITED.value)  # i.e. after NOW
            for _ in range(10)
        ],
    )
    service.touch("demo-uc")
    await service.tick(later)

    assert len(await _events(sessions)) == 2


async def test_a_rule_for_an_untouched_use_case_is_not_evaluated(sessions) -> None:
    await _rule_row(sessions, use_case="quiet-uc")
    await _seed(sessions, *[_log(use_case="quiet-uc", outcome="rate_limited") for _ in range(10)])
    service = AnomalyService(sessions)

    service.touch("busy-uc")
    await service.tick(NOW)

    assert await _events(sessions) == []


async def test_a_disabled_rule_is_not_evaluated(sessions) -> None:
    await _rule_row(sessions, enabled=False)
    await _seed(sessions, *[_log(outcome=Outcome.RATE_LIMITED.value) for _ in range(10)])
    service = AnomalyService(sessions)

    service.touch("demo-uc")
    await service.tick(NOW)

    assert await _events(sessions) == []


async def test_a_rule_asking_for_more_than_this_stage_can_do_says_so_in_the_row(sessions) -> None:
    """A control displayed as active and doing nothing is the defect `FRD-125` exists to prevent.
    Until `FRD-503`, the row says it was detected and not enforced — in those words."""
    await _rule_row(sessions, action="block", action_minutes=60)
    await _seed(sessions, *[_log(outcome=Outcome.RATE_LIMITED.value) for _ in range(10)])
    service = AnomalyService(sessions)

    service.touch("demo-uc")
    await service.tick(NOW)

    assert (await _events(sessions))[0].action_taken == NOT_ENFORCED


async def test_the_touched_set_is_bounded(sessions) -> None:
    """A bounded loss delays a finding by one tick; an unbounded set is a memory leak in the
    component whose job is to still be running when something goes wrong."""
    from aira_gateway.anomalies.service import MAX_TOUCHED

    service = AnomalyService(sessions)
    for index in range(MAX_TOUCHED + 50):
        service.touch(f"uc-{index}")

    assert len(service._touched) == MAX_TOUCHED


async def test_the_cooldown_map_is_bounded_too(sessions) -> None:
    """**The same argument as above, and it had been made for one of the two sets only.**

    A cooldown is keyed by `(rule, target)`, and a `subject`-targeted rule has one target per
    caller — so this grew by a row per person per rule, for the life of the process, two lines
    below the set that is explicitly bounded with the reason written on it.

    Expired entries go first, and dropping one changes no decision: an entry older than its own
    rule's window suppresses nothing. What is left is trimmed oldest-first, which costs at worst a
    duplicate finding.
    """
    from aira_gateway.anomalies.service import MAX_COOLDOWNS

    service = AnomalyService(sessions)
    stale = NOW - timedelta(hours=2)
    for index in range(MAX_COOLDOWNS + 100):
        service._last_fired[(1, f"caller-{index}")] = stale

    service._forget_stale_cooldowns(NOW, window_minutes=15)

    assert service._last_fired == {}, "everything older than its window suppresses nothing"


async def test_a_live_cooldown_survives_the_pruning(sessions) -> None:
    """The half that must not be lost: a rule that fired a moment ago still suppresses the same
    finding, or a 15-minute window fires fifteen times about the same fifteen minutes."""
    from aira_gateway.anomalies.service import MAX_COOLDOWNS

    service = AnomalyService(sessions)
    for index in range(MAX_COOLDOWNS + 100):
        service._last_fired[(1, f"caller-{index}")] = NOW

    service._forget_stale_cooldowns(NOW, window_minutes=15)

    assert len(service._last_fired) == MAX_COOLDOWNS
    assert all(fired == NOW for fired in service._last_fired.values())


async def test_pruning_does_nothing_while_the_map_is_small(sessions) -> None:
    """The ordinary path stays a dict write. A prune on every finding would be a sort on every
    finding, which is the cost this bound exists to avoid rather than to introduce."""
    service = AnomalyService(sessions)
    service._last_fired[(1, "caller")] = NOW - timedelta(days=1)

    service._forget_stale_cooldowns(NOW, window_minutes=15)

    assert service._last_fired == {(1, "caller"): NOW - timedelta(days=1)}


@pytest.mark.anyio
async def test_the_loop_starts_and_stops_cleanly(sessions) -> None:
    service = AnomalyService(sessions, interval_seconds=0.01)
    await service.start()
    await service.start()  # idempotent: a second start must not leave a second task running
    await service.stop()
    await service.stop()

    assert service._task is None


async def test_detection_can_be_switched_off_without_deleting_the_rules(sessions) -> None:
    """ "Switched off" and "deleted" are different states, and an operator needs the first."""
    service = AnomalyService(sessions, enabled=False)
    await service.start()

    assert service._task is None


# ---- the endpoint -----------------------------------------------------------------------------
#
# Driven through the route rather than against the query, which is the gap `FRD-124` and `FRD-602`
# both left the first time: two correct halves and no wire between them, invisible to coverage.


def _api(**settings) -> TestClient:
    return TestClient(create_app(GatewaySettings(auth_required=False, **settings)))


async def _event(sessionmaker, **over) -> None:
    values = {
        "rule_id": 1,
        "rule_name": "rule",
        "kind": "refusal_rate",
        "use_case": "demo-uc",
        "target": "subject",
        "target_value": "ada",
        "observed": 90,
        "threshold": 50,
        "sample": 10,
        "window_minutes": 15,
        "action_taken": "alert",
        "detail": "90% refusals",
    }
    values.update(over)
    async with sessionmaker() as session:
        session.add(AnomalyEvent(**values))
        await session.commit()


def test_the_endpoint_lists_what_was_found() -> None:
    with _api() as client:
        # The app owns its sessionmaker; a portal is how a sync test reaches an async helper
        # without a second engine that would see a different in-memory database.
        with anyio.from_thread.start_blocking_portal() as portal:
            portal.call(_event, client.app.state.db_sessionmaker)

        body = client.get("/v1beta/anomalies").json()

    assert body["scope"] == "all"
    assert body["events"][0]["rule"] == "rule"
    assert body["events"][0]["observed"] == 90
    # The row says what was *done*, which `ADR-0014` §3 keeps separate from what was detected.
    assert body["events"][0]["action_taken"] == "alert"


def test_the_endpoint_answers_about_one_use_case_when_asked() -> None:
    """Filtered at the server, not in the browser.

    A console that fetched the newest hundred findings and kept the matching ones would show a
    quiet use case nothing on a busy installation — its own findings pushed off the end by
    somebody else's, and the screen saying "nothing has crossed a threshold" about it.
    """
    with _api() as client:
        sessions = client.app.state.db_sessionmaker
        with anyio.from_thread.start_blocking_portal() as portal:
            portal.call(_event, sessions)
            portal.call(functools.partial(_event, use_case="other-uc"), sessions)

        mine = client.get("/v1beta/anomalies?use_case=demo-uc").json()

    assert [event["use_case"] for event in mine["events"]] == ["demo-uc"]
    assert mine["in_scope"] is True


def test_asking_about_an_invisible_use_case_is_an_empty_that_says_so() -> None:
    """The same two-kinds-of-empty distinction the trace view makes, for the same reason: a screen
    that prints "nothing found" when it means "you can see nothing here" sends its reader looking
    for a bug in the detector."""
    caller = Principal(subject="alice", method="oidc", use_cases=("demo-uc",))
    app = create_app(GatewaySettings(auth_required=False))
    app.dependency_overrides[require_principal] = lambda: caller
    with TestClient(app) as client:
        sessions = client.app.state.db_sessionmaker
        with anyio.from_thread.start_blocking_portal() as portal:
            portal.call(functools.partial(_event, use_case="other-uc"), sessions)

        body = client.get("/v1beta/anomalies?use_case=other-uc").json()

    assert body["events"] == []
    assert body["in_scope"] is False


def test_the_endpoint_requires_a_credential() -> None:
    """A finding names a caller and an address. It is not public."""
    with TestClient(create_app(GatewaySettings(auth_required=True))) as client:
        assert client.get("/v1beta/anomalies").status_code == 401
