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
    service.touch("demo-uc")
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
    service.touch("demo-uc")
    events = await service.tick(NOW)

    assert events[0].action_taken == "alert"
    assert await _suspensions(sessions) == []


async def test_a_throttling_rule_carries_its_rate_onto_the_decision(sessions) -> None:
    await _add(sessions, _rule(action="throttle", throttle_rpm=5))
    async with sessions() as session:
        for _ in range(10):
            session.add(_log())
        await session.commit()

    service = AnomalyService(sessions, suspensions=SuspensionService(sessions))
    service.touch("demo-uc")
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
    ],
)
def test_a_malformed_suspension_is_refused_by_name(body: dict, expected: str) -> None:
    """Never a 500, and never a silent default: this endpoint is used in a hurry."""
    with _api() as client:
        response = client.post("/v1beta/suspensions", json=body)

    assert response.status_code == 400
    assert expected in response.json()["error"]["message"]


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
