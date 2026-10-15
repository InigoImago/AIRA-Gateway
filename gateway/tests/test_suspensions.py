"""Incident response: a decision that stops traffic, and what it costs to be wrong (FRD-503)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import anyio
import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from aira_gateway.anomalies import AnomalyService
from aira_gateway.anomalies.suspensions import (
    AccessSuspension,
    Suspended,
    SuspensionService,
)
from aira_gateway.app import create_app
from aira_gateway.audit import Outcome
from aira_gateway.config import GatewaySettings
from aira_gateway.db.base import build_engine, build_sessionmaker, create_all
from aira_gateway.db.models import AnomalyRuleRead, RequestLog

#: The per-use-case ladder, which decides while strict data mode is off (`FRD-627`).
pytestmark = pytest.mark.usefixtures("strict_data_off")

NOW = datetime(2026, 8, 7, 12, 0, tzinfo=UTC)


@pytest_asyncio.fixture
async def sessions() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = build_engine("sqlite+aiosqlite:///:memory:")
    await create_all(engine)
    yield build_sessionmaker(engine)
    await engine.dispose()


async def _add(sessions, row) -> None:
    async with sessions() as session:
        session.add(row)
        await session.commit()


def _suspension(**over) -> AccessSuspension:
    values = {
        "use_case": "demo-uc",
        "target": "subject",
        "target_value": "ada",
        "action": "block",
        "expires_at": datetime.now(UTC) + timedelta(hours=1),
        "author": "user:itsec",
        "reason": "under investigation",
    }
    values.update(over)
    return AccessSuspension(**values)


# ---- what a suspension does -------------------------------------------------------------------


async def test_a_blocked_subject_is_stopped(sessions) -> None:
    await _add(sessions, _suspension())
    service = SuspensionService(sessions)

    with pytest.raises(Suspended) as raised:
        await service.check("demo-uc", "ada", None)

    # The message names the author, because the first question is who did this.
    assert "user:itsec" in raised.value.message
    assert int(raised.value.retry_after) > 0


async def test_somebody_else_is_not(sessions) -> None:
    await _add(sessions, _suspension())
    service = SuspensionService(sessions)

    assert await service.check("demo-uc", "grace", None) == []


async def test_a_suspension_scoped_to_one_use_case_does_not_reach_another(sessions) -> None:
    await _add(sessions, _suspension())
    service = SuspensionService(sessions)

    assert await service.check("other-uc", "ada", None) == []


async def test_a_global_suspension_reaches_every_use_case(sessions) -> None:
    """An operator stopping a leaked credential does not have to know which use cases it is bound
    to."""
    await _add(sessions, _suspension(use_case=None, target="credential", target_value="ab12cd34"))
    service = SuspensionService(sessions)

    with pytest.raises(Suspended):
        await service.check("any-uc", "anybody", "ab12cd34")


async def test_a_use_case_can_be_stopped_whole(sessions) -> None:
    await _add(sessions, _suspension(target="use_case", target_value="demo-uc"))
    service = SuspensionService(sessions)

    with pytest.raises(Suspended):
        await service.check("demo-uc", "anybody", None)


async def test_an_expired_suspension_stops_nobody(sessions) -> None:
    """Applied on read rather than by a sweeper: a row that has run out must stop refusing people
    the moment it does, without waiting for anything to tidy up."""
    await _add(sessions, _suspension(expires_at=datetime.now(UTC) - timedelta(minutes=1)))
    service = SuspensionService(sessions)

    assert await service.check("demo-uc", "ada", None) == []


async def test_a_lifted_suspension_stops_nobody(sessions) -> None:
    await _add(sessions, _suspension(lifted_at=datetime.now(UTC), lifted_by="user:itsec"))
    service = SuspensionService(sessions)

    assert await service.check("demo-uc", "ada", None) == []


async def test_one_with_no_expiry_keeps_applying(sessions) -> None:
    """A person may suspend indefinitely, because a person can also lift it."""
    await _add(sessions, _suspension(expires_at=None))
    service = SuspensionService(sessions)

    with pytest.raises(Suspended) as raised:
        await service.check("demo-uc", "ada", None)

    # Not "forever": a client told to come back in a week simply stops.
    assert raised.value.retry_after == "60"


async def test_a_throttle_returns_a_bucket_rather_than_refusing(sessions) -> None:
    await _add(sessions, _suspension(action="throttle", throttle_rpm=5))
    service = SuspensionService(sessions)

    throttles = await service.check("demo-uc", "ada", None)

    assert [t.limit_rpm for t in throttles] == [5]


async def test_enforcement_can_be_switched_off_without_deleting_the_decisions(sessions) -> None:
    await _add(sessions, _suspension())
    service = SuspensionService(sessions, enforce=False)

    assert await service.check("demo-uc", "ada", None) == []


async def test_the_cache_is_refreshed_rather_than_held_forever(sessions) -> None:
    ticks = iter([0.0, 0.0, 100.0])
    service = SuspensionService(sessions, clock=lambda: next(ticks))

    assert await service.check("demo-uc", "ada", None) == []
    await _add(sessions, _suspension())
    # Second call is inside the TTL and still sees nothing…
    assert await service.check("demo-uc", "ada", None) == []
    # …the third is past it.
    with pytest.raises(Suspended):
        await service.check("demo-uc", "ada", None)


async def test_the_writer_sees_its_own_decision_without_waiting(sessions) -> None:
    """Invalidation exists so applying a suspension is not delayed by the TTL; only *lifting* one
    is, and being slightly late to remove a restriction is the harmless direction."""
    service = SuspensionService(sessions, clock=lambda: 0.0)
    assert await service.check("demo-uc", "ada", None) == []

    await _add(sessions, _suspension())
    service.invalidate()

    with pytest.raises(Suspended):
        await service.check("demo-uc", "ada", None)


# ---- the engine creating one ------------------------------------------------------------------


def _rule(**over) -> AnomalyRuleRead:
    values = {
        "id": 1,
        "use_case": "demo-uc",
        "name": "too many refusals",
        "kind": "refusal_rate",
        "window_minutes": 15,
        "threshold": 50,
        "min_sample": 4,
        "action": "block",
        "action_minutes": 60,
        "target": "subject",
        "enabled": True,
    }
    values.update(over)
    return AnomalyRuleRead(**values)


def _log(**over) -> RequestLog:
    values = {
        "subject": "ada",
        "auth_method": "api_key",
        "use_case": "demo-uc",
        "api": "gemini",
        "operation": "generateContent",
        "model": "mock-1",
        "status": 429,
        "outcome": Outcome.RATE_LIMITED.value,
        "created_at": NOW - timedelta(minutes=1),
    }
    values.update(over)
    return RequestLog(**values)


async def _suspensions(sessions) -> list[AccessSuspension]:
    async with sessions() as session:
        return list((await session.execute(select(AccessSuspension))).scalars().all())


async def test_a_rule_that_blocks_writes_a_decision_with_an_author_and_an_expiry(
    sessions,
) -> None:
    await _add(sessions, _rule())
    async with sessions() as session:
        for _ in range(10):
            session.add(_log())
        await session.commit()

    service = AnomalyService(sessions, suspensions=SuspensionService(sessions))
    events = await service.tick(NOW)

    assert events[0].action_taken == "blocked"
    written = await _suspensions(sessions)
    assert len(written) == 1
    assert written[0].author == "rule:too many refusals"
    assert written[0].target_value == "ada"
    # A rule cannot lift its own decision, so it always sets an expiry.
    assert written[0].expires_at is not None


async def test_a_rule_that_only_alerts_writes_no_decision(sessions) -> None:
    """The whole point of the alert-first default: a rule can be watched being right before it
    takes anything away."""
    await _add(sessions, _rule(action="alert", action_minutes=None))
    async with sessions() as session:
        for _ in range(10):
            session.add(_log())
        await session.commit()

    service = AnomalyService(sessions, suspensions=SuspensionService(sessions))
    events = await service.tick(NOW)

    assert events[0].action_taken == "alert"
    assert await _suspensions(sessions) == []


@pytest.mark.parametrize("action", ["block", "throttle"])
async def test_while_suspensions_are_off_a_rule_records_and_decides_nothing(
    sessions, action: str
) -> None:
    """`AIRA_ENFORCE_SUSPENSIONS=false` is *"the setting to use while learning what your rules
    do"*: findings are recorded, nobody is refused. A rule went on writing live suspensions, each
    with an hour to run — and the moment enforcement was switched on, every one of them stopped
    its target, for traffic seen while the installation was only learning."""
    await _add(sessions, _rule(action=action, throttle_rpm=5 if action == "throttle" else None))
    async with sessions() as session:
        for _ in range(10):
            session.add(_log())
        await session.commit()

    off = SuspensionService(sessions, enforce=False)
    events = await AnomalyService(sessions, suspensions=off).tick(NOW)

    assert [event.action_taken for event in events] == ["detected_not_enforced"]
    assert await _suspensions(sessions) == []
    # And switched on afterwards, nothing from the learning phase applies.
    assert await SuspensionService(sessions).check("demo-uc", "ada", None) == []


async def test_a_throttling_rule_carries_its_rate_onto_the_decision(sessions) -> None:
    await _add(sessions, _rule(action="throttle", throttle_rpm=5))
    async with sessions() as session:
        for _ in range(10):
            session.add(_log())
        await session.commit()

    service = AnomalyService(sessions, suspensions=SuspensionService(sessions))
    events = await service.tick(NOW)

    assert events[0].action_taken == "throttled"
    assert (await _suspensions(sessions))[0].throttle_rpm == 5


# ---- the endpoints ----------------------------------------------------------------------------


def _api(**settings) -> TestClient:
    return TestClient(create_app(GatewaySettings(auth_required=False, **settings)))


def test_the_kill_switch_creates_lists_and_lifts() -> None:
    with _api() as client:
        created = client.post(
            "/v1beta/suspensions",
            json={"target": "subject", "target_value": "ada", "reason": "probing"},
        )
        assert created.status_code == 201
        body = created.json()
        assert body["author"].startswith("user:")
        assert body["action"] == "block"
        # A person's suspension may have no expiry, because a person can also lift it.
        assert body["expires_at"] is None

        listed = client.get("/v1beta/suspensions").json()["suspensions"]
        assert [row["target_value"] for row in listed] == ["ada"]

        lifted = client.delete(f"/v1beta/suspensions/{body['id']}").json()
        assert lifted["lifted_at"] is not None
        assert lifted["lifted_by"].startswith("user:")

        # Kept, not deleted: "blocked for two hours last Tuesday" is what a review asks.
        still_listed = client.get("/v1beta/suspensions").json()["suspensions"]
        assert len(still_listed) == 1


def test_lifting_something_that_is_not_there_says_so() -> None:
    with _api() as client:
        assert client.delete("/v1beta/suspensions/nope").status_code == 404


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ({"target": "elephant", "target_value": "x"}, "target"),
        ({"target": "subject", "target_value": "  "}, "target_value"),
        ({"target": "subject", "target_value": "x", "action": "ponder"}, "action"),
        ({"target": "subject", "target_value": "x", "action": "throttle"}, "throttle_rpm"),
        # A value longer than the column. Refused rather than truncated: a silently shortened
        # target matches nobody, which is a kill switch that reports success and stops nothing.
        # Unbounded it reached Postgres as a `StringDataRightTruncation` — a caller's mistake as
        # our 500, on the endpoint somebody reaches for during an incident. SQLite enforces no
        # column lengths, which is why nothing here noticed (`CLAUDE.md`, the second trap).
        ({"target": "subject", "target_value": "x" * 300}, "target_value"),
        # And the use case is a slug like every other one a caller sends. An unchecked value
        # reaches the audit trail and `_matches`, where it can only ever fail to match — a
        # suspension scoped to a use case that cannot exist stops nothing and looks active.
        ({"target": "subject", "target_value": "x", "use_case": "Not A Slug"}, "use case"),
        # **The two numbers, which this list did not reach and `int()` did not survive.** Both were
        # read as `int(body.get(...))` with nothing in front of them, so a word answered
        # `500 invalid literal for int()` and `10**30` answered
        # `500 Python int too large to convert to C int` — a caller's own value as a server error,
        # on the endpoint this test's own docstring says is used in a hurry.
        ({"target": "subject", "target_value": "x", "throttle_rpm": "many"}, "throttle_rpm"),
        ({"target": "subject", "target_value": "x", "throttle_rpm": 10**12}, "throttle_rpm"),
        ({"target": "subject", "target_value": "x", "minutes": "soon"}, "minutes"),
        ({"target": "subject", "target_value": "x", "minutes": 10**30}, "minutes"),
        # Zero and negative wrote a suspension that had expired before it was stored: `active()`
        # drops it on the very next read, so the console listed a kill switch that stopped nothing.
        # Accepted silently until now, which is the badge-wearing absent control again.
        ({"target": "subject", "target_value": "x", "minutes": 0}, "minutes"),
        ({"target": "subject", "target_value": "x", "minutes": -5}, "minutes"),
        # A bool is a number to `int()` and a client bug to everybody else. Throttling somebody to
        # one request a minute because their client sent `true` is the wrong way to find out.
        ({"target": "subject", "target_value": "x", "throttle_rpm": True}, "throttle_rpm"),
        # A use case is stopped by its slug. A display name typed in a hurry is a stop that
        # matches nothing and is listed as in force.
        ({"target": "use_case", "target_value": "Kundenservice"}, "target_value"),
        # Postgres refuses a NUL in any text column: the caller's mistake, not our 500. And a
        # target with a control character in it names nobody.
        ({"target": "subject", "target_value": "ada\u0000"}, "target_value"),
        # A whole use case stopped "only inside" another one can match no request: a stop on
        # `uc-a` applies to requests naming `uc-a`, and the scope admits only `uc-b`'s. Listed as
        # in force, stopping nothing.
        ({"target": "use_case", "target_value": "uc-a", "use_case": "uc-b"}, "use_case"),
    ],
)
def test_a_malformed_suspension_is_refused_by_name(body: dict, expected: str) -> None:
    """Never a 500, and never a silent default: this endpoint is used in a hurry."""
    with _api() as client:
        response = client.post("/v1beta/suspensions", json=body)

    assert response.status_code == 400
    assert expected in response.json()["error"]["message"]


@pytest.mark.parametrize("raw", [b"{", b"", b"not json", b"\xff\xfe", b"[1,2]"])
def test_a_body_that_is_not_a_json_object_is_refused_rather_than_crashing(raw: bytes) -> None:
    """`await request.json()` raises `JSONDecodeError`, and this route did not catch it.

    Every other route in the gateway that reads a body already does — `api/pipeline.py` and both
    API surfaces spell out the same four lines — so the rule was stated three times and held in
    three places, and the two incident endpoints were written afterwards. A stray brace answered
    `500 Internal error` on the endpoint somebody reaches for while something is going wrong.
    `:checkThinking` is the other one; `test_model_check.py` covers it, where its role fixture is.
    """
    with _api() as client:
        response = client.post(
            "/v1beta/suspensions", content=raw, headers={"content-type": "application/json"}
        )

    assert response.status_code == 400, response.text


def test_the_endpoints_need_a_credential() -> None:
    with TestClient(create_app(GatewaySettings(auth_required=True))) as client:
        assert client.get("/v1beta/suspensions").status_code == 401
        assert client.post("/v1beta/suspensions", json={}).status_code == 401


# ---- the gate ---------------------------------------------------------------------------------


def test_a_suspended_caller_is_refused_before_anything_is_spent() -> None:
    """`FRD-126`'s property, asserted for this control: the refusal happens at the one gate every
    verb takes, so a stopped caller does not pay for a classifier on the way to being told."""
    with _api(demo_mode=True) as client:
        with anyio.from_thread.start_blocking_portal() as portal:
            portal.call(
                _add,
                client.app.state.db_sessionmaker,
                _suspension(use_case=None, target_value="demo"),
            )
        client.app.state.suspensions.invalidate()

        response = client.post(
            "/v1beta/models/mock-1:generateContent",
            json={"contents": [{"parts": [{"text": "hello"}]}]},
        )

    assert response.status_code == 429
    assert response.headers["retry-after"]
    assert response.json()["error"]["status"] == "RESOURCE_EXHAUSTED"


def test_the_refusal_is_recorded_as_suspended_rather_than_rate_limited() -> None:
    """Folding it into `rate_limited` would hide "we stopped this caller on purpose" inside "this
    caller is going too fast", and those want different answers."""

    async def _outcomes(sessionmaker) -> list[str]:
        async with sessionmaker() as session:
            return list((await session.execute(select(RequestLog.outcome))).scalars().all())

    with (
        _api(demo_mode=True, log_queue_size=0) as client,
        anyio.from_thread.start_blocking_portal() as portal,
    ):
        portal.call(
            _add,
            client.app.state.db_sessionmaker,
            _suspension(use_case=None, target_value="demo"),
        )
        client.app.state.suspensions.invalidate()

        client.post(
            "/v1beta/models/mock-1:generateContent",
            json={"contents": [{"parts": [{"text": "hello"}]}]},
        )
        outcomes = portal.call(_outcomes, client.app.state.db_sessionmaker)

    assert Outcome.SUSPENDED.value in outcomes


def _throttled_twice(**settings: object) -> tuple[list[int], list[str]]:
    """Two requests from a caller throttled to one a minute: their statuses, and the outcomes the
    audit trail recorded for them."""

    async def _outcomes(sessionmaker) -> list[str]:
        async with sessionmaker() as session:
            return list((await session.execute(select(RequestLog.outcome))).scalars().all())

    with (
        _api(demo_mode=True, log_queue_size=0, **settings) as client,
        anyio.from_thread.start_blocking_portal() as portal,
    ):
        portal.call(
            _add,
            client.app.state.db_sessionmaker,
            _suspension(use_case=None, target_value="demo", action="throttle", throttle_rpm=1),
        )
        client.app.state.suspensions.invalidate()
        statuses = [
            client.post(
                "/v1beta/models/mock-1:generateContent",
                json={"contents": [{"parts": [{"text": "hello"}]}]},
            ).status_code
            for _ in range(2)
        ]
        outcomes = portal.call(_outcomes, client.app.state.db_sessionmaker)
    return statuses, outcomes


def test_a_throttle_s_refusal_is_recorded_as_suspended_too() -> None:
    """`FRD-503` FR-5 names *a refusal caused by a suspension*, and a throttle is one: it ran as
    the rate limiter's bucket and was recorded as `rate_limited` — the very folding FR-5 rules out,
    and the outcome the anomaly rules read as going too fast."""
    statuses, outcomes = _throttled_twice()

    assert statuses == [200, 429]
    assert Outcome.SUSPENDED.value in outcomes
    assert Outcome.RATE_LIMITED.value not in outcomes


def test_a_throttle_holds_with_rate_limits_switched_off() -> None:
    """`AIRA_ENFORCE_RATE_LIMITS=false` switches off the configured limits. A throttle is a
    suspension, and `AIRA_ENFORCE_SUSPENSIONS` is its switch — the limiter returned before taking
    it, and a caller under investigation was served at any rate."""
    statuses, _ = _throttled_twice(enforce_rate_limits=False)

    assert statuses == [200, 429]


@pytest.mark.parametrize("first", ["short", "long"])
async def test_two_blocks_say_when_the_last_one_ends(sessions, first: str) -> None:
    """A person blocked for a minute inside a use case blocked for ten is refused for ten:
    `Retry-After` named whichever block the query returned first, and a client honouring it came
    back nine minutes early, to be refused again."""
    short = _suspension(target="subject", expires_at=datetime.now(UTC) + timedelta(minutes=1))
    long = _suspension(
        target="use_case",
        target_value="demo-uc",
        expires_at=datetime.now(UTC) + timedelta(minutes=10),
    )
    for row in (short, long) if first == "short" else (long, short):
        await _add(sessions, row)

    with pytest.raises(Suspended) as refused:
        await SuspensionService(sessions).check("demo-uc", "ada", None)

    assert int(refused.value.retry_after) > 500


@pytest.mark.parametrize(
    "path",
    [
        "/v1beta/suspensions?use_case=%00",
        "/v1beta/anomalies?use_case=%00",
        "/v1beta/anomalies?cursor=2026-10-14T00:00:00%2B00:00%7C%00",
    ],
    ids=["suspensions use case", "anomalies use case", "anomalies cursor"],
)
def test_a_control_character_in_a_parameter_is_the_caller_s_400(path: str) -> None:
    """PostgreSQL refuses a NUL in any text it is asked about, so a NUL in a query parameter was
    answered with this gateway's 500. SQLite takes it without a word — which is why the refusal is
    asserted here, by name, rather than the crash."""
    with _api() as client:
        response = client.get(path)

    assert response.status_code == 400, response.text


def test_lifting_an_id_with_a_control_character_lifts_nothing_by_name() -> None:
    with _api() as client:
        response = client.delete("/v1beta/suspensions/%00")

    assert response.status_code == 404, response.text


def test_a_block_does_not_carry_a_rate() -> None:
    """`throttle_rpm` is required for a throttle and refused otherwise (`FRD-503` §4.4), as
    Management refuses it on a rule. A block stored with a rate reads, on the list, as a stop that
    lets some traffic through."""
    with _api() as client:
        response = client.post(
            "/v1beta/suspensions",
            json={"target": "subject", "target_value": "ada", "action": "block", "throttle_rpm": 5},
        )

    assert response.status_code == 400
    assert "throttle_rpm" in response.json()["error"]["message"]


def test_stopping_traffic_by_hand_needs_an_oversight_role() -> None:
    """A hand-made suspension is a global rule's effect without the rule (`FRD-503` FR-6), so it
    takes the same roles.

    Driven with a **real principal** rather than with authentication switched off. The suite's
    other endpoint tests run with `auth_required=False`, which produces the demo principal — and
    that path returns before the role check ever runs, so every one of them passed while the check
    itself was untested. The mutation harness said so: `N19` survived.
    """
    from aira_gateway.auth.dependencies import require_principal
    from aira_gateway.auth.principal import Principal

    app = create_app(GatewaySettings(auth_required=False))

    def _as(*roles: str):
        return lambda: Principal(subject="somebody", method="oidc", roles=roles)

    with TestClient(app) as client:
        app.dependency_overrides[require_principal] = _as("use-case-admin")
        refused = client.post(
            "/v1beta/suspensions", json={"target": "subject", "target_value": "ada"}
        )
        assert refused.status_code == 403
        assert client.get("/v1beta/suspensions").status_code == 403

        app.dependency_overrides[require_principal] = _as("it-security")
        allowed = client.post(
            "/v1beta/suspensions", json={"target": "subject", "target_value": "ada"}
        )
        assert allowed.status_code == 201
        assert allowed.json()["author"] == "user:somebody"

        # And lifting follows the same rule as applying.
        app.dependency_overrides[require_principal] = _as("use-case-user")
        assert client.delete(f"/v1beta/suspensions/{allowed.json()['id']}").status_code == 403

    app.dependency_overrides.clear()


def test_a_member_sees_what_stops_their_use_case_or_them_and_nobody_elses() -> None:
    """The Warnings tab tells a member first whether their use case is stopped; the list used to
    refuse every member, so it never could. What a member sees is what applies to them — a stop on
    a colleague stays with the incident roles."""
    from aira_gateway.auth.dependencies import require_principal
    from aira_gateway.auth.principal import Principal

    app = create_app(GatewaySettings(auth_required=False))
    rows = [
        _suspension(use_case=None, target="use_case", target_value="demo-uc", reason="whole"),
        _suspension(use_case="demo-uc", target="subject", target_value="ada", reason="me here"),
        _suspension(use_case=None, target="subject", target_value="ada", reason="me everywhere"),
        _suspension(use_case="demo-uc", target="subject", target_value="bob", reason="colleague"),
        _suspension(use_case="other-uc", target="subject", target_value="ada", reason="elsewhere"),
        _suspension(use_case=None, target="use_case", target_value="other-uc", reason="other"),
        _suspension(use_case="demo-uc", target="credential", target_value="aira_ada", reason="key"),
    ]

    def _member(**over):
        values = {"subject": "ada", "method": "oidc", "roles": (), "use_cases": ("demo-uc",)}
        values.update(over)
        return lambda: Principal(**values)

    with TestClient(app) as client, anyio.from_thread.start_blocking_portal() as portal:
        for row in rows:
            portal.call(_add, app.state.db_sessionmaker, row)

        app.dependency_overrides[require_principal] = _member()
        seen = client.get("/v1beta/suspensions?use_case=demo-uc").json()
        assert seen["in_scope"] is True
        assert sorted(row["reason"] for row in seen["suspensions"]) == [
            "me everywhere",
            "me here",
            "whole",
        ]

        # Their own key, when they call with one.
        app.dependency_overrides[require_principal] = _member(
            subject="ada", method="api_key", credential="aira_ada"
        )
        with_key = client.get("/v1beta/suspensions?use_case=demo-uc").json()["suspensions"]
        assert "key" in {row["reason"] for row in with_key}

        # A use case they are not in answers empty, and says which empty.
        app.dependency_overrides[require_principal] = _member()
        outside = client.get("/v1beta/suspensions?use_case=other-uc").json()
        assert outside == {"suspensions": [], "scope": "own", "in_scope": False}

        # And without naming one, a member still gets nothing: the whole list is not theirs.
        assert client.get("/v1beta/suspensions").status_code == 403

        # Whoever reads every finding reads every stop.
        app.dependency_overrides[require_principal] = lambda: Principal(
            subject="gov", method="oidc", roles=("it-steuerung",)
        )
        everything = client.get("/v1beta/suspensions").json()
        assert everything["scope"] == "all"
        assert len(everything["suspensions"]) == len(rows)
        in_demo = client.get("/v1beta/suspensions?use_case=demo-uc").json()["suspensions"]
        assert "colleague" in {row["reason"] for row in in_demo}
        assert "other" not in {row["reason"] for row in in_demo}

    app.dependency_overrides.clear()


@pytest.mark.parametrize(
    ("roles", "use_case", "sees_addresses"),
    [
        pytest.param((), "demo-uc", False, id="a-member"),
        pytest.param(("it-steuerung",), "", False, id="every-figure-and-no-incident"),
        pytest.param(("it-security",), "", True, id="an-incident-role"),
    ],
)
def test_a_rule_made_stop_names_addresses_only_to_an_incident_role(
    roles: tuple[str, ...], use_case: str, sees_addresses: bool
) -> None:
    """A rule writes its finding's description as the reason for the stop it makes, and a finding
    about new addresses names them — so the reason carries them to every member the stop concerns.
    Cut for every reader but an incident role, as on the finding itself (`FRD-502` FR-12)."""
    from aira_gateway.auth.dependencies import require_principal
    from aira_gateway.auth.principal import Principal

    app = create_app(GatewaySettings(auth_required=False))
    stop = _suspension(
        use_case=None,
        target="use_case",
        target_value="demo-uc",
        author="rule:new addresses",
        reason="used from 1 address(es) not seen before: 198.51.100.23",
    )
    app.dependency_overrides[require_principal] = lambda: Principal(
        subject="alice", method="oidc", roles=roles, use_cases=("demo-uc",)
    )
    with TestClient(app) as client, anyio.from_thread.start_blocking_portal() as portal:
        portal.call(_add, app.state.db_sessionmaker, stop)
        listed = client.get(
            "/v1beta/suspensions", params={"use_case": use_case} if use_case else {}
        )
        (row,) = listed.json()["suspensions"]

    assert ("198.51.100.23" in row["reason"]) is sees_addresses
    assert row["reason"].startswith("used from 1 address(es) not seen before")


async def test_a_rule_made_block_applies_as_soon_as_the_tick_returns(tmp_path) -> None:
    """**A new suspension is not delayed** (`CACHE_TTL_SECONDS`). The cache must be dropped after
    the decision is committed, not when it is added: a request arriving while later rules are
    still evaluated reloads the set without the uncommitted block and keeps that for the TTL.

    Isolated as Postgres isolates it, which takes two things on SQLite: a file with a pool of its
    own, so each session has its own connection (`build_engine` shares one, and the request would
    see the tick's uncommitted row), and transactions the driver begins explicitly — pysqlite does
    not begin one for a `SELECT`, so the tick's per-rule savepoint would be the outermost
    transaction and its release a commit (SQLAlchemy's documented recipe).
    """
    from sqlalchemy import event
    from sqlalchemy.ext.asyncio import create_async_engine

    import aira_gateway.anomalies.service as service_module

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'tick.db'}")

    @event.listens_for(engine.sync_engine, "connect")
    def _no_implicit_transactions(dbapi_connection, _record) -> None:  # noqa: ANN001
        dbapi_connection.isolation_level = None

    @event.listens_for(engine.sync_engine, "begin")
    def _begin_explicitly(connection) -> None:  # noqa: ANN001
        connection.exec_driver_sql("BEGIN")

    await create_all(engine)
    sessions = build_sessionmaker(engine)
    # The real clock, because the block it writes is checked against the real clock.
    moment = datetime.now(UTC)
    await _add(sessions, _rule(id=1, name="block storm", action="block", action_minutes=30))
    await _add(sessions, _rule(id=2, name="second rule", kind="error_rate", action="alert"))
    for _ in range(6):
        await _add(sessions, _log(created_at=moment - timedelta(minutes=1)))

    suspensions = SuspensionService(sessions)
    evaluate = service_module.evaluate_rule

    async def a_request_arrives_meanwhile(session, rule, now=None):  # noqa: ANN001, ANN202
        if rule.id == 2:
            await suspensions.active()
        return await evaluate(session, rule, now)

    service_module.evaluate_rule = a_request_arrives_meanwhile
    try:
        await AnomalyService(sessions, suspensions=suspensions).tick(moment)
    finally:
        service_module.evaluate_rule = evaluate

    try:
        with pytest.raises(Suspended):
            await suspensions.check("demo-uc", "ada", None)
    finally:
        await engine.dispose()


def test_a_block_in_force_is_listed_however_much_history_is_newer() -> None:
    """Rule-made suspensions expire and are never lifted, so a noisy rule leaves hundreds of them.
    The list was the newest `LISTED` rows, so an older block still in force — a person's
    indefinite one — dropped off it: the console could neither show it nor lift it (lifting takes
    its id), while it went on refusing traffic. Every stop in force is listed, then the history."""
    from aira_gateway.api.incidents.suspensions import LISTED

    now = datetime.now(UTC)
    in_force = _suspension(
        target_value="team", expires_at=None, reason="held", created_at=now - timedelta(days=21)
    )
    history = [
        _suspension(
            target_value=f"noisy-{n}",
            author="rule:noisy",
            expires_at=now - timedelta(days=1),
            created_at=now - timedelta(days=1, minutes=n),
        )
        for n in range(LISTED)
    ]
    with _api() as client, anyio.from_thread.start_blocking_portal() as portal:
        for row in (in_force, *history):
            portal.call(_add, client.app.state.db_sessionmaker, row)
        listed = client.get("/v1beta/suspensions").json()["suspensions"]

    assert listed[0]["target_value"] == "team", "the block in force is not listed first"
    assert len(listed) == LISTED + 1


async def test_what_has_expired_is_not_loaded_again_every_few_seconds(sessions) -> None:
    """Every instance reloads its suspensions every few seconds and every request walks them, so
    the load reads what can still apply — not every rule-made row ever written."""
    from aira_gateway.anomalies.suspensions import _load

    await _add(
        sessions,
        _suspension(target_value="expired", expires_at=datetime.now(UTC) - timedelta(minutes=1)),
    )
    await _add(sessions, _suspension(target_value="held", expires_at=None))

    async with sessions() as session:
        loaded = await _load(session)

    assert [row.target_value for row in loaded] == ["held"]


def test_a_reason_keeps_its_words_and_loses_what_no_column_can_hold() -> None:
    """A reason is free text, so it is cleaned rather than refused: a NUL is what Postgres refuses,
    and losing the stop over it would be the worst answer during an incident."""
    with _api() as client:
        created = client.post(
            "/v1beta/suspensions",
            json={"target": "subject", "target_value": "ada", "reason": "probing\u0000 again"},
        )

    assert created.status_code == 201, created.text
    assert created.json()["reason"] == "probing again"


def test_a_whole_use_case_may_be_scoped_to_itself() -> None:
    """The paired case: naming the use case twice is redundant, and it is what it says."""
    with _api() as client:
        created = client.post(
            "/v1beta/suspensions",
            json={"target": "use_case", "target_value": "uc-a", "use_case": "uc-a"},
        )

    assert created.status_code == 201, created.text
