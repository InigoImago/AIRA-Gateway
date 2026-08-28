"""A developer round on anomaly detection and incident response, against the live stack.

`FRD-500`/`501`/`503` were built with hermetic tests, mutation checks and a green gate. This is the
layer those cannot reach: a real Postgres with a real dialect, a real gateway process reading its
own cache, real rows written by real requests to a real model, and the two planes actually talking
over Kafka.

Two rules the earlier live rounds established and this one keeps:

- **Nothing asserts an answer's content.** That tests the model and flakes. What is asserted is
  that traffic is measured, findings are recorded with the numbers they were drawn from, and a
  decision to stop somebody actually stops them.
- **Every figure is checked where it lives** — the database — not in the response that claims it.

Detection is driven **in-process against the live database** rather than by waiting for the
container's timer: the timer is a `sleep` and proving it is the unit suite's job, while what needs a
real stack is the SQL, the dialect and the interaction with rows the gateway itself wrote.
Enforcement is observed the other way round — written to Postgres, then seen through the *running*
gateway, because the cache that reads it only exists in that process.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from aira_gateway.anomalies import AnomalyService
from aira_gateway.anomalies.suspensions import SuspensionService
from aira_gateway.db.base import build_sessionmaker
from aira_gateway.db.models import AnomalyRuleRead

from .conftest import GATEWAY_URL, LOCAL_CHAT_MODEL_ID, MANAGEMENT_URL, wait_for_row

pytestmark = pytest.mark.integration

#: The chat model the demo stack serves. Small on purpose: what is under test is the gateway.
MODEL = "qwen3:0.6b"
#: The same model as the predecessor addresses it: by integer id, from the catalog.
#:
#: **Imported, not typed.** This was `KIRA_MODEL_ID = 9001`, and the seed moved the chat model onto
#: the predecessor's own id — `tools/seed_local_catalog.py` says why: *"every document and every
#: example said `1004`, and the one runnable command said something else."* The literal stayed
#: here, so fourteen tests addressed a number no model answers to and the gateway refused them
#: `422 MODEL_NOT_FOUND` — correctly, and reported as a broken KIRA surface.
#:
#: `conftest.LOCAL_CHAT_MODEL_ID` exists for exactly this and says so: *"Six tests carried `9001`
#: as a literal, and moving the demo onto the predecessor's own id would have left every one of
#: them addressing a model that no longer answers."* Six were corrected; this was the seventh.
KIRA_MODEL_ID = LOCAL_CHAT_MODEL_ID
#: Enough output for a sentence, few enough tokens that sixty requests stay quick.
SHORT = {"generationConfig": {"maxOutputTokens": 16}}


def _body(text_in: str = "Say OK", **extra) -> dict:
    return {"contents": [{"parts": [{"text": text_in}]}], **SHORT, **extra}


async def _generate(client: httpx.AsyncClient, fixture, **kw) -> httpx.Response:
    return await client.post(
        f"{GATEWAY_URL}/v1beta/models/{MODEL}:generateContent",
        headers=fixture.headers(),
        json=_body(**kw) if kw else _body(),
        timeout=180.0,
    )


async def _rows_of(engine: AsyncEngine, slug: str) -> int:
    async with engine.connect() as connection:
        result = await connection.execute(
            text("SELECT count(*) FROM request_logs WHERE use_case = :slug"), {"slug": slug}
        )
        return int(result.scalar_one())


async def _seed_rows(engine: AsyncEngine, slug: str, count: int, **over) -> None:
    """Write audit rows directly.

    Detection reads the request log, and the *shape* of the log is what the rules measure — so a
    test about a refusal rate needs refusals, not a model. The rows real traffic produces are
    checked separately, and the two are compared where it matters (`test_the_engine_measures_the
    _rows_a_real_request_wrote`).
    """
    values = {
        "subject": "probe",
        "auth_method": "api_key",
        "use_case": slug,
        "api": "gemini",
        "operation": "generateContent",
        "model": MODEL,
        "status": 200,
        "outcome": "served",
        "credential": "abcd1234",
        "source_ip": "10.0.0.1",
        "cost_nanos": None,
        "request_bytes": None,
        "minutes_ago": 1,
    }
    values.update(over)
    minutes = values.pop("minutes_ago")
    async with engine.begin() as connection:
        for _ in range(count):
            await connection.execute(
                text(
                    "INSERT INTO request_logs (id, created_at, subject, auth_method, use_case,"
                    " api, operation, model, status, outcome, credential, source_ip, cost_nanos,"
                    " request_bytes) VALUES (:id, :created_at, :subject, :auth_method, :use_case,"
                    " :api, :operation, :model, :status, :outcome, :credential, :source_ip,"
                    " :cost_nanos, :request_bytes)"
                ),
                {
                    **values,
                    "id": str(uuid.uuid4()),
                    "created_at": datetime.now(UTC) - timedelta(minutes=minutes),
                },
            )


#: How far back a round in these tests looks for traffic.
#:
#: The evaluator reads which scopes saw traffic from `request_logs` rather than from a set this
#: process filled — that is what makes it correct with more than one gateway instance
#: (`FRD-127`). The rows here are seeded minutes into the past to sit inside a rule's window, so a
#: round with the production one-minute lookback would see none of them and every case would pass
#: for the wrong reason: nothing touched, nothing evaluated, no event.
#:
#: An hour covers any window these tests use. It also means a round evaluates every *other* scope
#: with recent traffic in this shared database, which is exactly what production does — and is why
#: `_by_rule` and `fixture.events()` have always scoped their assertions to this test's own rule
#: and slug rather than to `events[0]`.
LOOKBACK_SECONDS = 3600.0


async def _tick(engine: AsyncEngine, slug: str) -> list:
    """Run one evaluation round against the live database, for the given scope."""
    service = AnomalyService(
        build_sessionmaker(engine), suspensions=None, interval_seconds=LOOKBACK_SECONDS
    )
    return await service.tick()


async def _tick_enforcing(engine: AsyncEngine, slug: str) -> list:
    sessions = build_sessionmaker(engine)
    service = AnomalyService(
        sessions, suspensions=SuspensionService(sessions), interval_seconds=LOOKBACK_SECONDS
    )
    return await service.tick()


def _by_rule(events: list, rule_id: int):
    """The event **this test's** rule produced.

    Not `events[0]`. A tick evaluates every rule that applies to the scope, and a **global** rule
    (`use_case IS NULL`) applies to every scope there is — so a shared database accumulates other
    runs' global rules and one of them answers first. Found on 2026-08-08 with sixteen `alert`
    rules left behind by earlier e2e runs: three assertions read `alert` where they expected
    `blocked`, `throttled` and `detected_not_enforced`, and the product was doing exactly the right
    thing. Latent until there was enough junk, which is the worst kind of test to leave standing.
    """
    mine = [event for event in events if getattr(event, "rule_id", None) == rule_id]
    assert mine, (
        f"rule {rule_id} produced no finding; the tick returned "
        f"{[getattr(e, 'rule_name', '?') for e in events]}"
    )
    return mine[0]


async def _rule_row(engine: AsyncEngine, rule_id: int) -> AnomalyRuleRead:
    async with build_sessionmaker(engine)() as session:
        row = await session.get(AnomalyRuleRead, rule_id)
        assert row is not None
        return row


# =================================================================================================
# A. The configuration path: authored in Management, evaluated in the gateway
# =================================================================================================


@pytest.fixture
async def authored(admin_token: str):
    """A use case made through Management, so rules are authored the way people author them.

    Created by the **global-admin** service account (`ADR-0017`): creating a use case is that
    role's act, and the creator is granted administration of it, which is what authoring its rules
    needs. `it-steuerung` cannot create — it sees everything and writes nothing (PRD §154) — and
    the account that used to do this held `use-case-admin`, which is no longer a role at all.
    """
    slug = f"anom-{uuid.uuid4().hex[:8]}"
    headers = {"Authorization": f"Bearer {admin_token}"}
    async with httpx.AsyncClient(timeout=30.0) as client:
        created = await client.post(
            f"{MANAGEMENT_URL}/api/v1/use-cases/",
            headers=headers,
            json={"slug": slug, "name": "Anomaly probe"},
        )
        assert created.status_code in (201, 400), created.text
        yield slug, headers, client
        await client.delete(f"{MANAGEMENT_URL}/api/v1/use-cases/{slug}/", headers=headers)


async def test_a1_a_rule_authored_in_management_reaches_the_gateway(authored, engine) -> None:
    slug, headers, client = authored
    created = await client.post(
        f"{MANAGEMENT_URL}/api/v1/use-cases/{slug}/anomaly-rules/",
        headers=headers,
        json={
            "name": "refusals",
            "kind": "refusal_rate",
            "window_minutes": 15,
            "threshold": 40,
            "min_sample": 20,
        },
    )
    assert created.status_code == 201, created.text

    row = await wait_for_row(
        engine,
        "SELECT kind, threshold, window_minutes, min_sample FROM anomaly_rules"
        " WHERE use_case = :slug",
        {"slug": slug},
        timeout=30.0,
    )
    assert row.kind == "refusal_rate"
    assert row.threshold == 40
    assert row.window_minutes == 15
    assert row.min_sample == 20


async def test_a2_a_global_rule_travels_with_a_null_scope(security_token: str, engine) -> None:
    """`NULL` means everywhere. An empty string would be a use case named "" — matching nothing
    while looking like it matched everything."""
    headers = {"Authorization": f"Bearer {security_token}"}
    name = f"global-{uuid.uuid4().hex[:6]}"
    async with httpx.AsyncClient(timeout=30.0) as client:
        created = await client.post(
            f"{MANAGEMENT_URL}/api/v1/anomaly-rules/",
            headers=headers,
            json={
                "name": name,
                "kind": "new_source_ip",
                "window_minutes": 60,
                "threshold": 1,
                "min_sample": 0,
            },
        )
        assert created.status_code == 201, created.text
        rule_id = created.json()["id"]
        try:
            row = await wait_for_row(
                engine,
                "SELECT use_case, kind FROM anomaly_rules WHERE name = :name",
                {"name": name},
                timeout=30.0,
            )
            assert row.use_case is None
            assert row.kind == "new_source_ip"
        finally:
            await client.delete(
                f"{MANAGEMENT_URL}/api/v1/anomaly-rules/{rule_id}/", headers=headers
            )


async def test_a3_a_deleted_rule_disappears_from_the_gateway(authored, engine) -> None:
    slug, headers, client = authored
    created = await client.post(
        f"{MANAGEMENT_URL}/api/v1/use-cases/{slug}/anomaly-rules/",
        headers=headers,
        json={"name": "doomed", "kind": "error_rate", "threshold": 50, "min_sample": 5},
    )
    rule_id = created.json()["id"]
    await wait_for_row(
        engine, "SELECT id FROM anomaly_rules WHERE id = :id", {"id": rule_id}, timeout=30.0
    )

    deleted = await client.delete(
        f"{MANAGEMENT_URL}/api/v1/use-cases/{slug}/anomaly-rules/{rule_id}/", headers=headers
    )
    assert deleted.status_code == 204

    async def gone() -> bool:
        async with engine.connect() as connection:
            result = await connection.execute(
                text("SELECT count(*) FROM anomaly_rules WHERE id = :id"), {"id": rule_id}
            )
            return int(result.scalar_one()) == 0

    for _ in range(60):
        if await gone():
            break
        await asyncio.sleep(0.5)
    assert await gone(), "a deleted rule kept evaluating in the gateway"


async def test_a4_a_payload_rule_carries_its_byte_figure(authored, engine) -> None:
    """The gap `FRD-501` §4.4 found: the threshold is a share, and the byte figure it is measured
    against needs its own column."""
    slug, headers, client = authored
    created = await client.post(
        f"{MANAGEMENT_URL}/api/v1/use-cases/{slug}/anomaly-rules/",
        headers=headers,
        json={
            "name": "bulk",
            "kind": "payload_size",
            "threshold": 20,
            "parameter": 500_000,
            "min_sample": 5,
        },
    )
    assert created.status_code == 201, created.text

    row = await wait_for_row(
        engine,
        "SELECT parameter FROM anomaly_rules WHERE use_case = :slug AND kind = 'payload_size'",
        {"slug": slug},
        timeout=30.0,
    )
    assert row.parameter == 500_000


async def test_a5_a_throttling_rule_carries_its_rate(authored, engine) -> None:
    slug, headers, client = authored
    created = await client.post(
        f"{MANAGEMENT_URL}/api/v1/use-cases/{slug}/anomaly-rules/",
        headers=headers,
        json={
            "name": "slow them",
            "kind": "request_spike",
            "threshold": 300,
            "min_sample": 10,
            "action": "throttle",
            "action_minutes": 30,
            "throttle_rpm": 5,
        },
    )
    assert created.status_code == 201, created.text

    row = await wait_for_row(
        engine,
        "SELECT action, throttle_rpm, action_minutes FROM anomaly_rules WHERE use_case = :slug",
        {"slug": slug},
        timeout=30.0,
    )
    assert row.action == "throttle"
    assert row.throttle_rpm == 5
    assert row.action_minutes == 30


async def test_a6_deleting_a_use_case_takes_its_rules_and_leaves_the_global_ones(
    admin_token: str, security_token: str, engine
) -> None:
    # Created and deleted by the role that may (`ADR-0017`); what is under test is the cascade,
    # not who performs it.
    headers = {"Authorization": f"Bearer {admin_token}"}
    security = {"Authorization": f"Bearer {security_token}"}
    slug = f"anom-{uuid.uuid4().hex[:8]}"
    global_name = f"survivor-{uuid.uuid4().hex[:6]}"
    async with httpx.AsyncClient(timeout=30.0) as client:
        made = await client.post(
            f"{MANAGEMENT_URL}/api/v1/use-cases/",
            headers=headers,
            json={"slug": slug, "name": "Cascade probe"},
        )
        assert made.status_code == 201, made.text
        scoped_rule = await client.post(
            f"{MANAGEMENT_URL}/api/v1/use-cases/{slug}/anomaly-rules/",
            headers=headers,
            json={"name": "theirs", "kind": "error_rate", "threshold": 50, "min_sample": 5},
        )
        assert scoped_rule.status_code == 201, scoped_rule.text
        survivor = await client.post(
            f"{MANAGEMENT_URL}/api/v1/anomaly-rules/",
            headers=security,
            json={
                "name": global_name,
                "kind": "new_source_ip",
                "threshold": 1,
                "min_sample": 0,
            },
        )
        assert survivor.status_code == 201, survivor.text
        await wait_for_row(
            engine,
            "SELECT id FROM anomaly_rules WHERE use_case = :slug",
            {"slug": slug},
            timeout=30.0,
        )

        try:
            await client.delete(f"{MANAGEMENT_URL}/api/v1/use-cases/{slug}/", headers=headers)

            async def counts() -> tuple[int, int]:
                async with engine.connect() as connection:
                    scoped = await connection.execute(
                        text("SELECT count(*) FROM anomaly_rules WHERE use_case = :slug"),
                        {"slug": slug},
                    )
                    everywhere = await connection.execute(
                        text("SELECT count(*) FROM anomaly_rules WHERE name = :name"),
                        {"name": global_name},
                    )
                    return int(scoped.scalar_one()), int(everywhere.scalar_one())

            for _ in range(60):
                scoped, everywhere = await counts()
                if scoped == 0:
                    break
                await asyncio.sleep(0.5)
            scoped, everywhere = await counts()
            assert scoped == 0, "a deleted use case left its rules evaluating"
            # A cascade that swept the global rules away would let deleting one use case switch
            # off detection for every other.
            assert everywhere == 1
        finally:
            await client.delete(
                f"{MANAGEMENT_URL}/api/v1/anomaly-rules/{survivor.json()['id']}/", headers=security
            )


async def test_a7_a_use_case_admin_cannot_author_a_global_rule(member_token: str) -> None:
    """Its effects land on use cases its author may not be able to see (`FRD-500` FR-8)."""
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(
            f"{MANAGEMENT_URL}/api/v1/anomaly-rules/",
            headers={"Authorization": f"Bearer {member_token}"},
            json={"name": "sneaky", "kind": "error_rate", "threshold": 50, "min_sample": 5},
        )
    assert response.status_code == 403


async def test_a8_the_validation_refuses_what_the_engine_could_not_evaluate(authored) -> None:
    """Validation lives where the rule is written. Each of these would look configured and do
    nothing."""
    slug, headers, client = authored
    cases = [
        ({"kind": "refusal_rate", "threshold": 140}, "threshold"),
        ({"kind": "spend_spike", "threshold": 100}, "threshold"),
        ({"kind": "payload_size", "threshold": 20}, "parameter"),
        ({"kind": "refusal_rate", "threshold": 50, "parameter": 10}, "parameter"),
        ({"kind": "refusal_rate", "threshold": 50, "action": "block"}, "action_minutes"),
        (
            {"kind": "refusal_rate", "threshold": 50, "action": "throttle", "action_minutes": 10},
            "throttle_rpm",
        ),
        ({"kind": "refusal_rate", "threshold": 50, "min_sample": 0}, "min_sample"),
    ]
    for extra, field in cases:
        response = await client.post(
            f"{MANAGEMENT_URL}/api/v1/use-cases/{slug}/anomaly-rules/",
            headers=headers,
            json={"name": f"bad-{field}-{uuid.uuid4().hex[:4]}", "min_sample": 5, **extra},
        )
        assert response.status_code == 400, (extra, response.text)
        assert field in response.text, (extra, response.text)


# =================================================================================================
# B. Every kind, measured against rows a real Postgres holds
# =================================================================================================


async def test_b1_a_refusal_rate_is_found(fixture) -> None:
    await fixture.rule(kind="refusal_rate", threshold=50, min_sample=4)
    await _seed_rows(fixture.engine, fixture.slug, 6, outcome="rate_limited", status=429)
    await _seed_rows(fixture.engine, fixture.slug, 4)

    await _tick(fixture.engine, fixture.slug)

    events = await fixture.events()
    assert len(events) == 1
    assert events[0]["observed"] == 60
    assert events[0]["sample"] == 10
    assert events[0]["target_value"] == "probe"


async def test_b2_a_refusal_rate_below_the_threshold_is_not(fixture) -> None:
    await fixture.rule(kind="refusal_rate", threshold=80, min_sample=4)
    await _seed_rows(fixture.engine, fixture.slug, 6, outcome="rate_limited", status=429)
    await _seed_rows(fixture.engine, fixture.slug, 4)

    await _tick(fixture.engine, fixture.slug)

    assert await fixture.events() == []


async def test_b3_an_error_rate_counts_only_the_provider_s_failures(fixture) -> None:
    await fixture.rule(kind="error_rate", threshold=50, min_sample=4)
    await _seed_rows(fixture.engine, fixture.slug, 3, outcome="upstream_error", status=502)
    await _seed_rows(fixture.engine, fixture.slug, 7, outcome="rate_limited", status=429)

    await _tick(fixture.engine, fixture.slug)

    # 3 of 10 are the provider's; a `refusal_rate` rule on the same rows would read 100 %.
    assert await fixture.events() == []


async def test_b4_an_error_rate_above_the_threshold_is_found(fixture) -> None:
    await fixture.rule(kind="error_rate", threshold=50, min_sample=4)
    await _seed_rows(fixture.engine, fixture.slug, 6, outcome="upstream_error", status=502)
    await _seed_rows(fixture.engine, fixture.slug, 4)

    await _tick(fixture.engine, fixture.slug)

    assert (await fixture.events())[0]["observed"] == 60


async def test_b5_a_blocked_prompt_rate_is_found(fixture) -> None:
    await fixture.rule(kind="blocked_prompt_rate", threshold=40, min_sample=4)
    await _seed_rows(fixture.engine, fixture.slug, 5, outcome="blocked_by_pipeline", status=403)
    await _seed_rows(fixture.engine, fixture.slug, 5)

    await _tick(fixture.engine, fixture.slug)

    assert (await fixture.events())[0]["observed"] == 50


async def test_b6_a_client_hanging_up_counts_as_a_refusal(fixture) -> None:
    """One caller hanging up is not our failure; a thousand is the shape a detector exists for."""
    await fixture.rule(kind="refusal_rate", threshold=50, min_sample=4)
    await _seed_rows(fixture.engine, fixture.slug, 8, outcome="client_gone", status=499)
    await _seed_rows(fixture.engine, fixture.slug, 2)

    await _tick(fixture.engine, fixture.slug)

    assert (await fixture.events())[0]["observed"] == 80


async def test_b7_a_suspension_refusal_is_visible_to_a_refusal_rule(fixture) -> None:
    """`suspended` is a new outcome (`FRD-503`). A detector that only knew the old ones would go
    quiet exactly while a caller was being stopped."""
    await fixture.rule(kind="refusal_rate", threshold=50, min_sample=4)
    await _seed_rows(fixture.engine, fixture.slug, 9, outcome="suspended", status=429)
    await _seed_rows(fixture.engine, fixture.slug, 1)

    await _tick(fixture.engine, fixture.slug)

    assert (await fixture.events())[0]["observed"] == 90


async def test_b8_a_spend_spike_is_measured_against_the_previous_window(fixture) -> None:
    await fixture.rule(kind="spend_spike", threshold=300, min_sample=3, window_minutes=10)
    await _seed_rows(fixture.engine, fixture.slug, 4, cost_nanos=1_000_000, minutes_ago=2)
    await _seed_rows(fixture.engine, fixture.slug, 4, cost_nanos=100_000, minutes_ago=15)

    await _tick(fixture.engine, fixture.slug)

    events = await fixture.events()
    assert events[0]["observed"] == 1000
    assert "against" in str(events[0]["detail"])


async def test_b9_a_request_spike_counts_rows_rather_than_money(fixture) -> None:
    await fixture.rule(kind="request_spike", threshold=250, min_sample=3, window_minutes=10)
    await _seed_rows(fixture.engine, fixture.slug, 12, minutes_ago=2)
    await _seed_rows(fixture.engine, fixture.slug, 4, minutes_ago=15)

    await _tick(fixture.engine, fixture.slug)

    assert (await fixture.events())[0]["observed"] == 300


async def test_b10_a_large_payload_share_is_found(fixture) -> None:
    await fixture.rule(kind="payload_size", threshold=50, parameter=500_000, min_sample=3)
    await _seed_rows(fixture.engine, fixture.slug, 3, request_bytes=900_000)
    await _seed_rows(fixture.engine, fixture.slug, 1, request_bytes=1_000)

    await _tick(fixture.engine, fixture.slug)

    assert (await fixture.events())[0]["observed"] == 75


async def test_b11_a_new_source_address_is_found(fixture) -> None:
    await fixture.rule(
        kind="new_source_ip", target="credential", threshold=1, min_sample=0, window_minutes=10
    )
    await _seed_rows(fixture.engine, fixture.slug, 2, source_ip="10.0.0.1", minutes_ago=15)
    await _seed_rows(fixture.engine, fixture.slug, 2, source_ip="10.0.0.1", minutes_ago=2)
    await _seed_rows(fixture.engine, fixture.slug, 1, source_ip="203.0.113.9", minutes_ago=2)

    await _tick(fixture.engine, fixture.slug)

    events = await fixture.events()
    assert events[0]["target_value"] == "abcd1234"
    assert "203.0.113.9" in str(events[0]["detail"])


async def test_b12_a_credential_with_no_history_is_not_reported_as_new(fixture) -> None:
    await fixture.rule(
        kind="new_source_ip", target="credential", threshold=1, min_sample=0, window_minutes=10
    )
    await _seed_rows(fixture.engine, fixture.slug, 3, source_ip="203.0.113.9", minutes_ago=2)

    await _tick(fixture.engine, fixture.slug)

    assert await fixture.events() == []


async def test_b13_the_measurement_groups_by_the_target_the_action_lands_on(fixture) -> None:
    """A refusal rate averaged over a whole use case says nothing about the one caller producing
    it."""
    await fixture.rule(kind="refusal_rate", threshold=50, min_sample=4)
    await _seed_rows(
        fixture.engine, fixture.slug, 5, subject="noisy", outcome="rate_limited", status=429
    )
    await _seed_rows(fixture.engine, fixture.slug, 20, subject="quiet")

    await _tick(fixture.engine, fixture.slug)

    assert [e["target_value"] for e in await fixture.events()] == ["noisy"]


async def test_b14_a_use_case_target_reports_the_use_case(fixture) -> None:
    await fixture.rule(kind="refusal_rate", threshold=50, min_sample=4, target="use_case")
    await _seed_rows(fixture.engine, fixture.slug, 8, outcome="rate_limited", status=429)
    await _seed_rows(fixture.engine, fixture.slug, 2)

    await _tick(fixture.engine, fixture.slug)

    assert (await fixture.events())[0]["target_value"] == fixture.slug


# =================================================================================================
# C. The guards that stop a measurement from lying
# =================================================================================================


async def test_c1_a_rate_over_too_few_rows_is_not_evaluated(fixture) -> None:
    """One refusal out of one request is 100 %, and means nothing."""
    await fixture.rule(kind="refusal_rate", threshold=50, min_sample=20)
    await _seed_rows(fixture.engine, fixture.slug, 3, outcome="rate_limited", status=429)

    await _tick(fixture.engine, fixture.slug)

    assert await fixture.events() == []


async def test_c2_growth_from_nothing_is_not_a_spike(fixture) -> None:
    await fixture.rule(kind="request_spike", threshold=200, min_sample=3, window_minutes=10)
    await _seed_rows(fixture.engine, fixture.slug, 10, minutes_ago=2)

    await _tick(fixture.engine, fixture.slug)

    assert await fixture.events() == []


async def test_c3_a_request_of_unknown_size_is_left_out_of_both_sides(fixture) -> None:
    """Counting an unknown as small would make old traffic look innocent."""
    await fixture.rule(kind="payload_size", threshold=90, parameter=500_000, min_sample=3)
    await _seed_rows(fixture.engine, fixture.slug, 3, request_bytes=900_000)
    await _seed_rows(fixture.engine, fixture.slug, 20, request_bytes=None)

    await _tick(fixture.engine, fixture.slug)

    events = await fixture.events()
    assert events[0]["observed"] == 100
    assert events[0]["sample"] == 3


async def test_c4_traffic_outside_the_window_is_not_measured(fixture) -> None:
    await fixture.rule(kind="refusal_rate", threshold=50, min_sample=4, window_minutes=5)
    await _seed_rows(fixture.engine, fixture.slug, 10, outcome="rate_limited", minutes_ago=60)

    await _tick(fixture.engine, fixture.slug)

    assert await fixture.events() == []


async def test_c5_a_rule_ignores_another_use_case_s_traffic(fixture, engine) -> None:
    other = f"itest-other-{uuid.uuid4().hex[:6]}"
    async with engine.begin() as connection:
        await connection.execute(
            text("INSERT INTO use_cases (slug, name) VALUES (:slug, :slug)"), {"slug": other}
        )
    try:
        await fixture.rule(kind="refusal_rate", threshold=50, min_sample=4)
        await _seed_rows(engine, other, 10, outcome="rate_limited", status=429)

        await _tick(fixture.engine, fixture.slug)

        assert await fixture.events() == []
    finally:
        async with engine.begin() as connection:
            await connection.execute(
                text("DELETE FROM request_logs WHERE use_case = :slug"), {"slug": other}
            )
            await connection.execute(
                text("DELETE FROM use_cases WHERE slug = :slug"), {"slug": other}
            )


async def test_c6_a_disabled_rule_is_not_evaluated(fixture) -> None:
    await fixture.rule(kind="refusal_rate", threshold=50, min_sample=4, enabled=False)
    await _seed_rows(fixture.engine, fixture.slug, 10, outcome="rate_limited", status=429)

    await _tick(fixture.engine, fixture.slug)

    assert await fixture.events() == []


async def test_c7_a_kind_this_gateway_does_not_know_measures_nothing(fixture) -> None:
    """A newer Management can publish a kind this version does not implement. Passing would report
    a rule as having found nothing, which is a statement about traffic rather than about a
    version."""
    await fixture.rule(kind="prompt_similarity", threshold=50, min_sample=1)
    await _seed_rows(fixture.engine, fixture.slug, 10, outcome="rate_limited", status=429)

    await _tick(fixture.engine, fixture.slug)

    assert await fixture.events() == []


async def test_c8_a_payload_rule_with_no_byte_figure_measures_nothing(fixture) -> None:
    await fixture.rule(kind="payload_size", threshold=1, parameter=None, min_sample=1)
    await _seed_rows(fixture.engine, fixture.slug, 10, request_bytes=900_000)

    await _tick(fixture.engine, fixture.slug)

    assert await fixture.events() == []


# =================================================================================================
# D. Scheduling: only what changed, and only once
# =================================================================================================


async def test_d1_a_tick_with_no_touched_scope_does_nothing(fixture) -> None:
    await fixture.rule(kind="refusal_rate", threshold=50, min_sample=4)
    await _seed_rows(fixture.engine, fixture.slug, 10, outcome="rate_limited", status=429)

    service = AnomalyService(build_sessionmaker(fixture.engine))
    assert await service.tick() == []
    assert await fixture.events() == []


async def test_d2_a_rule_whose_use_case_saw_no_traffic_is_not_evaluated(fixture) -> None:
    """A quiet installation with 200 use cases should not run 200 queries a minute forever.

    **Both halves of the setup are load-bearing.** The refusals below would fire this rule if it
    were evaluated — they are inside its 60-minute window — and they sit *outside* the round's
    lookback, which is what makes the scope untouched. Seeding no rows at all would leave the rule
    with nothing to find, so deleting the filter would change nothing and this would pass against
    a version that had lost it.

    The old shape of this test asked for a scope by name (`touch("somebody-else")`). Touched scopes
    are read from the audit rows now, so a use case with rows in the window is touched by
    definition and naming a different one proves nothing (`FRD-127`).
    """
    await fixture.rule(kind="refusal_rate", threshold=50, min_sample=4)
    await _seed_rows(
        fixture.engine, fixture.slug, 10, outcome="rate_limited", status=429, minutes_ago=10
    )
    # Recent traffic elsewhere, so the round has a scope to evaluate and is not simply idle.
    await _seed_rows(fixture.engine, f"anom-busy-{uuid.uuid4().hex[:6]}", 2)

    # The production lookback, deliberately: one minute reaches the row above and not the rule's.
    service = AnomalyService(
        build_sessionmaker(fixture.engine), suspensions=None, interval_seconds=60.0
    )
    await service.tick()

    assert await fixture.events() == []


async def test_d3_a_global_rule_is_evaluated_whatever_was_touched(fixture, engine) -> None:
    """It applies everywhere, so "did *its* use case see traffic" is not a question about it."""
    rule_id = await fixture.rule(kind="refusal_rate", threshold=50, min_sample=4)
    async with engine.begin() as connection:
        await connection.execute(
            text("UPDATE anomaly_rules SET use_case = NULL WHERE id = :id"), {"id": rule_id}
        )
    await _seed_rows(fixture.engine, fixture.slug, 10, outcome="rate_limited", status=429)

    try:
        await _tick(fixture.engine, fixture.slug)

        async with engine.connect() as connection:
            result = await connection.execute(
                text("SELECT count(*) FROM anomaly_events WHERE rule_id = :id"), {"id": rule_id}
            )
            assert int(result.scalar_one()) >= 1
    finally:
        async with engine.begin() as connection:
            await connection.execute(
                text("DELETE FROM anomaly_events WHERE rule_id = :id"), {"id": rule_id}
            )
            await connection.execute(
                text("DELETE FROM anomaly_rules WHERE id = :id"), {"id": rule_id}
            )


async def test_d4_the_same_finding_is_not_written_twice_inside_its_window(fixture) -> None:
    await fixture.rule(kind="refusal_rate", threshold=50, min_sample=4)
    await _seed_rows(fixture.engine, fixture.slug, 10, outcome="rate_limited", status=429)

    sessions = build_sessionmaker(fixture.engine)
    service = AnomalyService(sessions, interval_seconds=LOOKBACK_SECONDS)
    await service.tick()
    await service.tick()

    assert len(await fixture.events()) == 1


async def test_d5_two_targets_crossing_at_once_are_two_findings(fixture) -> None:
    await fixture.rule(kind="refusal_rate", threshold=50, min_sample=4)
    for who in ("alice", "bob"):
        await _seed_rows(
            fixture.engine, fixture.slug, 8, subject=who, outcome="rate_limited", status=429
        )
        await _seed_rows(fixture.engine, fixture.slug, 2, subject=who)

    await _tick(fixture.engine, fixture.slug)

    assert sorted(e["target_value"] for e in await fixture.events()) == ["alice", "bob"]


async def test_d6_the_event_records_the_numbers_it_was_drawn_from(fixture) -> None:
    """A finding nobody can check is a finding nobody acts on."""
    await fixture.rule(kind="refusal_rate", threshold=50, min_sample=4, window_minutes=30)
    await _seed_rows(fixture.engine, fixture.slug, 7, outcome="rate_limited", status=429)
    await _seed_rows(fixture.engine, fixture.slug, 3)

    await _tick(fixture.engine, fixture.slug)

    event = (await fixture.events())[0]
    assert event["observed"] == 70
    assert event["threshold"] == 50
    assert event["sample"] == 10
    assert event["kind"] == "refusal_rate"
    assert "10 requests" in str(event["detail"])


# =================================================================================================
# E. Enforcement: the decision actually stops the traffic
# =================================================================================================


async def _wait_until_blocked(fixture, blocked: bool, attempts: int = 14) -> httpx.Response:
    """Poll the running gateway until its suspension cache has caught up.

    The cache is deliberately a few seconds behind (`FRD-503` §4.1); a test that read once would be
    testing the TTL rather than the control.
    """
    async with httpx.AsyncClient(timeout=180.0) as client:
        last = await _generate(client, fixture)
        for _ in range(attempts):
            if (last.status_code == 429) == blocked:
                return last
            await asyncio.sleep(1.0)
            last = await _generate(client, fixture)
        return last


async def test_e1_a_blocked_use_case_is_refused_by_the_running_gateway(fixture) -> None:
    await fixture.suspend(target="use_case", target_value=fixture.slug)

    response = await _wait_until_blocked(fixture, blocked=True)

    assert response.status_code == 429, response.text
    assert response.json()["error"]["status"] == "RESOURCE_EXHAUSTED"


async def test_e2_the_refusal_names_who_stopped_them(fixture) -> None:
    await fixture.suspend(author="user:itsec", reason="under investigation")

    response = await _wait_until_blocked(fixture, blocked=True)

    assert "user:itsec" in response.json()["error"]["message"]


async def test_e3_the_refusal_carries_a_retry_after(fixture) -> None:
    await fixture.suspend(expires_at=datetime.now(UTC) + timedelta(minutes=30))

    response = await _wait_until_blocked(fixture, blocked=True)

    assert int(response.headers["retry-after"]) > 0


async def test_e4_the_refusal_is_recorded_as_suspended(fixture) -> None:
    await fixture.suspend()
    await _wait_until_blocked(fixture, blocked=True)

    await wait_for_row(
        fixture.engine,
        "SELECT outcome FROM request_logs WHERE use_case = :slug AND outcome = 'suspended'",
        {"slug": fixture.slug},
        timeout=20.0,
    )
    outcomes = {row["outcome"] for row in await fixture.rows()}
    # Its own value: "we stopped this caller" and "this caller is going too fast" want different
    # answers from whoever reads the report.
    assert "suspended" in outcomes
    assert "rate_limited" not in outcomes


async def test_e5_an_expired_suspension_stops_nobody(fixture) -> None:
    await fixture.suspend(expires_at=datetime.now(UTC) - timedelta(minutes=1))

    response = await _wait_until_blocked(fixture, blocked=False, attempts=3)

    assert response.status_code == 200, response.text


async def test_e6_a_lifted_suspension_stops_nobody(fixture) -> None:
    await fixture.suspend(lifted_at=datetime.now(UTC), lifted_by="user:itsec", expires_at=None)

    response = await _wait_until_blocked(fixture, blocked=False, attempts=3)

    assert response.status_code == 200, response.text


async def test_e7_lifting_one_restores_service(fixture) -> None:
    row_id = await fixture.suspend()
    await _wait_until_blocked(fixture, blocked=True)

    async with fixture.engine.begin() as connection:
        await connection.execute(
            text(
                "UPDATE access_suspensions SET lifted_at = now(), lifted_by = 'user:itsec'"
                " WHERE id = :id"
            ),
            {"id": row_id},
        )

    response = await _wait_until_blocked(fixture, blocked=False)

    assert response.status_code == 200, response.text


async def test_e8_a_suspension_of_somebody_else_does_not_stop_this_caller(fixture) -> None:
    await fixture.suspend(target="subject", target_value="somebody-else")

    response = await _wait_until_blocked(fixture, blocked=False, attempts=3)

    assert response.status_code == 200, response.text


async def test_e9_a_suspension_scoped_to_another_use_case_does_not_reach_here(fixture) -> None:
    await fixture.suspend(use_case="a-different-use-case", target="subject", target_value="probe")

    response = await _wait_until_blocked(fixture, blocked=False, attempts=3)

    assert response.status_code == 200, response.text


async def test_e10_a_blocked_caller_is_refused_before_the_pipeline_runs(fixture) -> None:
    """`FRD-126`'s property, for this control: a stopped caller must not pay for a classifier on
    the way to being told."""
    async with fixture.engine.begin() as connection:
        await connection.execute(
            text(
                "INSERT INTO pipeline_configs (use_case, steps, fallback_models, updated_at)"
                " VALUES (:slug, :steps, '[]', now())"
                " ON CONFLICT (use_case) DO UPDATE SET steps = EXCLUDED.steps"
            ),
            {
                "slug": fixture.slug,
                "steps": (
                    '[{"type": "injection_filter", "config": {"mode": "llm", "action": "block",'
                    f' "model": "{MODEL}"}}}}]'
                ),
            },
        )
    await fixture.suspend()
    await _wait_until_blocked(fixture, blocked=True)
    await asyncio.sleep(2.0)

    async def classifier_calls() -> int:
        # A pipeline model call leaves a `pipeline:` row of its own (`FRD-125b`).
        rows = await fixture.rows()
        return len([r for r in rows if str(r["operation"]).startswith("pipeline:")])

    # Counted from **after** the block took effect: the requests served while the cache caught up
    # ran the filter quite correctly, and what is under test is the ones that were refused.
    before = await classifier_calls()
    async with httpx.AsyncClient(timeout=180.0) as client:
        for _ in range(3):
            response = await _generate(client, fixture)
            assert response.status_code == 429, response.text
    await asyncio.sleep(3.0)

    assert await classifier_calls() == before, (
        "a suspended caller paid for a classifier on the way to being told"
    )

    async with fixture.engine.begin() as connection:
        await connection.execute(
            text("DELETE FROM pipeline_configs WHERE use_case = :slug"), {"slug": fixture.slug}
        )


async def test_e11_the_kira_surface_refuses_a_suspended_caller_too(fixture) -> None:
    """A control that protects one surface and not the other is the `:embedContent` failure with a
    whole API to hide in."""
    await fixture.suspend()
    await _wait_until_blocked(fixture, blocked=True)

    async with httpx.AsyncClient(timeout=180.0) as client:
        response = await client.post(
            f"{GATEWAY_URL}/kira/api/external/chat",
            headers={"x-goog-api-key": fixture.key, "content-type": "application/json"},
            json={
                "model_id": KIRA_MODEL_ID,
                "request": {"parts": [{"text": "hi"}]},
                "maxTokens": 16,
            },
        )

    assert response.status_code == 429, response.text
    assert "TOO_MANY_REQUEST" in response.text


async def test_e12_an_embedding_is_refused_too(fixture) -> None:
    """Every verb takes the one gate — the lesson `FRD-405` B3 paid for."""
    await fixture.suspend()
    await _wait_until_blocked(fixture, blocked=True)

    async with httpx.AsyncClient(timeout=120.0) as client:
        response = await client.post(
            f"{GATEWAY_URL}/v1beta/models/all-minilm:embedContent",
            headers=fixture.headers(),
            json={"content": {"parts": [{"text": "hello"}]}},
        )

    assert response.status_code == 429, response.text


# =================================================================================================
# F. The kill switch, through the API people would use
# =================================================================================================


async def test_f1_an_incident_role_can_stop_and_restore(security_token: str) -> None:
    headers = {"Authorization": f"Bearer {security_token}", "content-type": "application/json"}
    async with httpx.AsyncClient(timeout=30.0) as client:
        created = await client.post(
            f"{GATEWAY_URL}/v1beta/suspensions",
            headers=headers,
            json={"target": "subject", "target_value": f"probe-{uuid.uuid4().hex[:6]}"},
        )
        assert created.status_code == 201, created.text
        body = created.json()
        assert body["author"].startswith("user:")
        assert body["expires_at"] is None

        listed = await client.get(f"{GATEWAY_URL}/v1beta/suspensions", headers=headers)
        assert any(row["id"] == body["id"] for row in listed.json()["suspensions"])

        lifted = await client.delete(
            f"{GATEWAY_URL}/v1beta/suspensions/{body['id']}", headers=headers
        )
        assert lifted.status_code == 200
        assert lifted.json()["lifted_by"].startswith("user:")

        # Kept, not deleted: "blocked for two hours last Tuesday" is what a review asks.
        again = await client.get(f"{GATEWAY_URL}/v1beta/suspensions", headers=headers)
        assert any(row["id"] == body["id"] for row in again.json()["suspensions"])


async def test_f2_a_use_case_admin_cannot_stop_anybody(member_token: str) -> None:
    headers = {"Authorization": f"Bearer {member_token}", "content-type": "application/json"}
    async with httpx.AsyncClient(timeout=30.0) as client:
        created = await client.post(
            f"{GATEWAY_URL}/v1beta/suspensions",
            headers=headers,
            json={"target": "subject", "target_value": "somebody"},
        )
        listed = await client.get(f"{GATEWAY_URL}/v1beta/suspensions", headers=headers)

    assert created.status_code == 403, created.text
    assert listed.status_code == 403


async def test_f2b_a_read_only_governance_role_cannot_stop_anybody(governance_token: str) -> None:
    """The finding this round produced. `it-steuerung` sees every use case and every figure, and
    PRD §154 gives it **no write anywhere** — but the gateway guarded its kill switch with a
    *visibility* predicate, so it could stop traffic there while Management refused it a global
    rule. Two planes, one question, two answers.
    """
    headers = {"Authorization": f"Bearer {governance_token}", "content-type": "application/json"}
    async with httpx.AsyncClient(timeout=30.0) as client:
        created = await client.post(
            f"{GATEWAY_URL}/v1beta/suspensions",
            headers=headers,
            json={"target": "subject", "target_value": "somebody"},
        )
    assert created.status_code == 403, created.text


async def test_f3_an_unauthenticated_caller_cannot_stop_anybody() -> None:
    async with httpx.AsyncClient(timeout=30.0) as client:
        created = await client.post(
            f"{GATEWAY_URL}/v1beta/suspensions",
            json={"target": "subject", "target_value": "somebody"},
        )
    assert created.status_code == 401


@pytest.mark.parametrize(
    ("body", "field"),
    [
        ({"target": "elephant", "target_value": "x"}, "target"),
        ({"target": "subject", "target_value": "   "}, "target_value"),
        ({"target": "subject", "target_value": "x", "action": "ponder"}, "action"),
        ({"target": "subject", "target_value": "x", "action": "throttle"}, "throttle_rpm"),
    ],
)
async def test_f4_a_malformed_suspension_names_the_field(
    security_token: str, body: dict, field: str
) -> None:
    """This endpoint is used in a hurry. Never a 500, never a silent default."""
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(
            f"{GATEWAY_URL}/v1beta/suspensions",
            headers={"Authorization": f"Bearer {security_token}"},
            json=body,
        )
    assert response.status_code == 400, response.text
    assert field in response.json()["error"]["message"]


async def test_f5_lifting_something_that_is_not_there_is_a_404(security_token: str) -> None:
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.delete(
            f"{GATEWAY_URL}/v1beta/suspensions/{uuid.uuid4()}",
            headers={"Authorization": f"Bearer {security_token}"},
        )
    assert response.status_code == 404


async def test_f6_a_hand_made_suspension_stops_the_real_gateway(
    security_token: str, fixture
) -> None:
    """The kill switch, end to end: created through the API a person would use, and the very next
    request through the data plane is refused."""
    headers = {"Authorization": f"Bearer {security_token}", "content-type": "application/json"}
    async with httpx.AsyncClient(timeout=30.0) as client:
        created = await client.post(
            f"{GATEWAY_URL}/v1beta/suspensions",
            headers=headers,
            json={
                "target": "use_case",
                "target_value": fixture.slug,
                "reason": "integration kill switch",
                "minutes": 30,
            },
        )
        assert created.status_code == 201, created.text
        try:
            response = await _wait_until_blocked(fixture, blocked=True)
            assert response.status_code == 429, response.text
        finally:
            await client.delete(
                f"{GATEWAY_URL}/v1beta/suspensions/{created.json()['id']}", headers=headers
            )


async def test_f7_a_throttle_created_by_hand_is_stored_with_its_rate(
    security_token: str,
) -> None:
    headers = {"Authorization": f"Bearer {security_token}", "content-type": "application/json"}
    async with httpx.AsyncClient(timeout=30.0) as client:
        created = await client.post(
            f"{GATEWAY_URL}/v1beta/suspensions",
            headers=headers,
            json={
                "target": "subject",
                "target_value": f"slow-{uuid.uuid4().hex[:6]}",
                "action": "throttle",
                "throttle_rpm": 3,
                "minutes": 10,
            },
        )
        assert created.status_code == 201, created.text
        assert created.json()["throttle_rpm"] == 3
        assert created.json()["expires_at"] is not None
        await client.delete(
            f"{GATEWAY_URL}/v1beta/suspensions/{created.json()['id']}", headers=headers
        )


async def test_f8_the_anomaly_list_is_scoped_like_the_report(
    governance_token: str, member_token: str, fixture
) -> None:
    """One `visible_scope`, not two that happen to agree."""
    await fixture.rule(kind="refusal_rate", threshold=50, min_sample=4)
    await _seed_rows(fixture.engine, fixture.slug, 10, outcome="rate_limited", status=429)
    await _tick(fixture.engine, fixture.slug)

    async with httpx.AsyncClient(timeout=30.0) as client:
        # **Asked about this use case**, not fished out of the global list.
        #
        # This test used to read the unfiltered list and look for its own finding in it, and it
        # failed intermittently for a reason worth writing down: one evaluator tick writes a
        # finding for *every* scope that saw traffic — 57 in the same instant on this database —
        # while the endpoint returns the newest 50, ties broken by `id`, which is a random UUID.
        # So whether a particular finding landed on page one was decided by chance.
        #
        # It also tested the wrong thing. What this case is about is **scope**: the same
        # `visible_scope` for the report and the findings. Filtering says that, and says it
        # deterministically.
        oversight = await client.get(
            f"{GATEWAY_URL}/v1beta/anomalies",
            headers={"Authorization": f"Bearer {governance_token}"},
            params={"use_case": fixture.slug},
        )
        scoped = await client.get(
            f"{GATEWAY_URL}/v1beta/anomalies",
            headers={"Authorization": f"Bearer {member_token}"},
            params={"use_case": fixture.slug},
        )

    assert oversight.status_code == 200
    assert oversight.json()["scope"] == "all"
    assert any(e["use_case"] == fixture.slug for e in oversight.json()["events"]), (
        "an oversight role could not see a finding for a use case it is entitled to see"
    )

    assert scoped.status_code == 200
    assert scoped.json()["scope"] == "use_cases"
    # A caller with no use cases gets an empty list rather than a refusal — the `FRD-601` rule.
    assert not [e for e in scoped.json()["events"] if e["use_case"] == fixture.slug]


# =================================================================================================
# G. The engine against traffic the gateway itself produced
# =================================================================================================


async def test_g1_the_engine_measures_the_rows_a_real_request_wrote(fixture) -> None:
    """The seam every other test in section B assumes: rows written by the real request path are
    the rows the evaluator reads."""
    async with httpx.AsyncClient(timeout=180.0) as client:
        response = await _generate(client, fixture)
    assert response.status_code == 200, response.text

    await wait_for_row(
        fixture.engine,
        "SELECT id FROM request_logs WHERE use_case = :slug",
        {"slug": fixture.slug},
        timeout=20.0,
    )
    await fixture.rule(kind="refusal_rate", threshold=1, min_sample=1)
    # The same subject the API key carries: the measurement groups by target, so seeding a
    # different name would make two groups of one rather than one group of two.
    await _seed_rows(
        fixture.engine,
        fixture.slug,
        1,
        subject="integration-probe",
        outcome="rate_limited",
        status=429,
    )

    await _tick(fixture.engine, fixture.slug)

    event = (await fixture.events())[0]
    # Two rows in the window: the one the model served and the one seeded. The sample proves the
    # evaluator saw the real one.
    assert event["sample"] >= 2


async def test_g2_a_real_request_records_the_bytes_the_payload_rule_needs(fixture) -> None:
    """`request_bytes` is what `payload_size` measures against, and it comes from the middleware
    that was already counting to enforce the ceiling."""
    async with httpx.AsyncClient(timeout=180.0) as client:
        response = await _generate(client, fixture, text_in="x" * 4000)
    assert response.status_code == 200, response.text

    row = await wait_for_row(
        fixture.engine,
        "SELECT request_bytes FROM request_logs WHERE use_case = :slug",
        {"slug": fixture.slug},
        timeout=20.0,
    )
    assert row.request_bytes is not None
    assert row.request_bytes > 4000


async def test_g3_a_real_refusal_is_measured_as_one(fixture) -> None:
    """The audit trail records what was *asked*, not only what was served (`FRD-122`) — which is
    what makes a refusal rate measurable at all."""
    await fixture.budget(limit_requests=1)
    async with httpx.AsyncClient(timeout=180.0) as client:
        first = await _generate(client, fixture)
        second = await _generate(client, fixture)
        third = await _generate(client, fixture)

    assert first.status_code == 200, first.text
    assert 429 in (second.status_code, third.status_code)

    await wait_for_row(
        fixture.engine,
        "SELECT id FROM request_logs WHERE use_case = :slug AND outcome = 'budget_exceeded'",
        {"slug": fixture.slug},
        timeout=20.0,
    )
    await fixture.rule(kind="refusal_rate", threshold=30, min_sample=2)

    await _tick(fixture.engine, fixture.slug)

    events = await fixture.events()
    assert events, "a real budget refusal was invisible to a refusal-rate rule"
    assert events[0]["observed"] >= 30


async def test_g4_a_rule_that_blocks_writes_a_decision_that_the_gateway_then_honours(
    fixture,
) -> None:
    """The whole chain in one test: traffic, a finding, a written decision, a refused request."""
    rule_id = await fixture.rule(
        kind="refusal_rate",
        threshold=50,
        min_sample=4,
        target="use_case",
        action="block",
        action_minutes=30,
    )
    await _seed_rows(fixture.engine, fixture.slug, 9, outcome="rate_limited", status=429)
    await _seed_rows(fixture.engine, fixture.slug, 1)

    events = await _tick_enforcing(fixture.engine, fixture.slug)
    assert events, "the rule found nothing"
    assert _by_rule(events, rule_id).action_taken == "blocked"

    written = await fixture.suspensions()
    assert len(written) == 1
    assert written[0]["author"].startswith("rule:")
    assert written[0]["expires_at"] is not None
    assert written[0]["reason"]

    response = await _wait_until_blocked(fixture, blocked=True)
    assert response.status_code == 429, response.text


async def test_g5_a_rule_that_only_alerts_leaves_the_traffic_alone(fixture) -> None:
    rule_id = await fixture.rule(kind="refusal_rate", threshold=50, min_sample=4, target="use_case")
    await _seed_rows(fixture.engine, fixture.slug, 9, outcome="rate_limited", status=429)
    await _seed_rows(fixture.engine, fixture.slug, 1)

    events = await _tick_enforcing(fixture.engine, fixture.slug)

    assert _by_rule(events, rule_id).action_taken == "alert"
    assert await fixture.suspensions() == []
    async with httpx.AsyncClient(timeout=180.0) as client:
        response = await _generate(client, fixture)
    assert response.status_code == 200, response.text


async def test_g6_a_throttling_rule_writes_its_rate(fixture) -> None:
    rule_id = await fixture.rule(
        kind="refusal_rate",
        threshold=50,
        min_sample=4,
        target="use_case",
        action="throttle",
        action_minutes=10,
        throttle_rpm=2,
    )
    await _seed_rows(fixture.engine, fixture.slug, 9, outcome="rate_limited", status=429)
    await _seed_rows(fixture.engine, fixture.slug, 1)

    events = await _tick_enforcing(fixture.engine, fixture.slug)

    assert _by_rule(events, rule_id).action_taken == "throttled"
    assert (await fixture.suspensions())[0]["throttle_rpm"] == 2


async def test_g7_a_rule_that_cannot_be_carried_out_says_so_on_the_row(fixture, engine) -> None:
    """A control displayed as active and doing nothing is the defect `FRD-125` exists to prevent."""
    rule_id = await fixture.rule(
        kind="refusal_rate", threshold=50, min_sample=4, target="use_case", action="block"
    )
    async with engine.begin() as connection:
        await connection.execute(
            text("UPDATE anomaly_rules SET action_minutes = NULL WHERE id = :id"), {"id": rule_id}
        )
    await _seed_rows(fixture.engine, fixture.slug, 9, outcome="rate_limited", status=429)
    await _seed_rows(fixture.engine, fixture.slug, 1)

    events = await _tick_enforcing(fixture.engine, fixture.slug)

    assert _by_rule(events, rule_id).action_taken == "detected_not_enforced"
    assert await fixture.suspensions() == []


async def test_g8_the_evaluator_survives_a_rule_row_it_cannot_parse(fixture, engine) -> None:
    """A detector that dies on one bad rule is a detector that is off when it matters."""
    await fixture.rule(kind="not_a_kind", threshold=50, min_sample=1)
    await fixture.rule(kind="refusal_rate", threshold=50, min_sample=4)
    await _seed_rows(fixture.engine, fixture.slug, 9, outcome="rate_limited", status=429)
    await _seed_rows(fixture.engine, fixture.slug, 1)

    await _tick(fixture.engine, fixture.slug)

    # The good rule still fired.
    assert [e["kind"] for e in await fixture.events()] == ["refusal_rate"]


# =================================================================================================
# H. What the rest of the system sees
# =================================================================================================


async def test_h1_a_suspension_does_not_consume_budget(fixture) -> None:
    """A refused request must not spend what it was refused for.

    Measured **after** the block has taken effect, not from zero: the cache is deliberately a few
    seconds behind, so the requests served during that window legitimately consume budget. What is
    under test is that a *refused* one does not.
    """
    await fixture.budget(limit_requests=100)
    await fixture.suspend()
    await _wait_until_blocked(fixture, blocked=True)

    async def used() -> int:
        async with fixture.engine.connect() as connection:
            result = await connection.execute(
                text("SELECT requests FROM budget_usage WHERE scope_key = :key"),
                {"key": f"uc:{fixture.slug}"},
            )
            row = result.first()
            return int(row.requests) if row else 0

    before = await used()
    async with httpx.AsyncClient(timeout=180.0) as client:
        for _ in range(4):
            response = await _generate(client, fixture)
            assert response.status_code == 429, response.text
    await asyncio.sleep(2.0)

    assert await used() == before


async def test_h2_a_suspended_request_leaves_no_upstream_call(fixture) -> None:
    await fixture.suspend()
    await _wait_until_blocked(fixture, blocked=True)
    await asyncio.sleep(2.0)

    rows = await fixture.rows()
    suspended = [row for row in rows if row["outcome"] == "suspended"]
    assert suspended
    # Nothing was served, so nothing was billed.
    assert all(row["prompt_tokens"] in (None, 0) for row in suspended)


async def test_h3_the_reporting_endpoint_counts_a_suspension_as_a_refusal(
    governance_token: str, fixture
) -> None:
    await fixture.suspend()
    await _wait_until_blocked(fixture, blocked=True)
    await wait_for_row(
        fixture.engine,
        "SELECT id FROM request_logs WHERE use_case = :slug AND outcome = 'suspended'",
        {"slug": fixture.slug},
        timeout=20.0,
    )

    today = datetime.now(UTC).date().isoformat()
    tomorrow = (datetime.now(UTC) + timedelta(days=1)).date().isoformat()
    async with httpx.AsyncClient(timeout=30.0) as client:
        report = await client.get(
            f"{GATEWAY_URL}/v1beta/reporting?from={today}&to={tomorrow}",
            headers={"Authorization": f"Bearer {governance_token}"},
        )

    assert report.status_code == 200, report.text
    body = report.json()
    outcomes = {row["key"]: row for row in body["by_outcome"]}
    # `suspended` is its own bucket, so an incident is separable from a caller going too fast.
    assert "suspended" in outcomes, list(outcomes)
    assert outcomes["suspended"]["requests"] >= 1
    # And a refused request is **not** unpriced: nothing ran, so its cost is a genuine zero
    # rather than an unknown. Counting it made the "spend is a lower bound" caveat permanent.
    assert outcomes["suspended"]["unpriced_requests"] == 0


async def test_h4_an_anomaly_event_is_readable_through_the_api(
    governance_token: str, fixture
) -> None:
    await fixture.rule(kind="refusal_rate", threshold=50, min_sample=4)
    await _seed_rows(fixture.engine, fixture.slug, 9, outcome="rate_limited", status=429)
    await _seed_rows(fixture.engine, fixture.slug, 1)
    await _tick(fixture.engine, fixture.slug)

    async with httpx.AsyncClient(timeout=30.0) as client:
        listed = await client.get(
            f"{GATEWAY_URL}/v1beta/anomalies",
            headers={"Authorization": f"Bearer {governance_token}"},
        )

    assert listed.status_code == 200
    mine = [e for e in listed.json()["events"] if e["use_case"] == fixture.slug]
    assert mine, "a finding was written and could not be read back"
    assert mine[0]["observed"] == 90
    assert mine[0]["sample"] == 10
    assert mine[0]["action_taken"] == "alert"


async def test_h5_the_anomaly_list_needs_a_credential() -> None:
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.get(f"{GATEWAY_URL}/v1beta/anomalies")
    assert response.status_code == 401


async def test_h6_readiness_is_unaffected_by_a_suspension(fixture) -> None:
    """Stopping one caller is not a degradation of the service."""
    await fixture.suspend()
    await _wait_until_blocked(fixture, blocked=True)

    async with httpx.AsyncClient(timeout=30.0) as client:
        ready = await client.get(f"{GATEWAY_URL}/readyz")

    assert ready.status_code == 200
    assert ready.json()["status"] == "ready"


async def test_h7_the_gateway_still_serves_everybody_else(fixture, engine) -> None:
    """The blast radius of a suspension is what it names, and nothing more."""
    other_slug = f"itest-free-{uuid.uuid4().hex[:6]}"
    from aira_common.apikeys import generate_api_key

    full_key, prefix, key_hash = generate_api_key()
    async with engine.begin() as connection:
        await connection.execute(
            text("INSERT INTO use_cases (slug, name) VALUES (:slug, :slug)"), {"slug": other_slug}
        )
        await connection.execute(
            text(
                "INSERT INTO api_keys (id, prefix, key_hash, subject, use_case, label, is_active)"
                " VALUES (:id, :prefix, :hash, 'free-probe', :slug, 'free', true)"
            ),
            {"id": str(uuid.uuid4()), "prefix": prefix, "hash": key_hash, "slug": other_slug},
        )
    try:
        await fixture.suspend()
        await _wait_until_blocked(fixture, blocked=True)

        async with httpx.AsyncClient(timeout=180.0) as client:
            free = await client.post(
                f"{GATEWAY_URL}/v1beta/models/{MODEL}:generateContent",
                headers={"x-goog-api-key": full_key, "content-type": "application/json"},
                json=_body(),
            )
        assert free.status_code == 200, free.text
    finally:
        async with engine.begin() as connection:
            for statement in (
                "DELETE FROM request_logs WHERE use_case = :slug",
                "DELETE FROM api_keys WHERE use_case = :slug",
                "DELETE FROM use_cases WHERE slug = :slug",
            ):
                await connection.execute(text(statement), {"slug": other_slug})


async def test_h8_two_rules_on_one_use_case_each_report_their_own_numbers(fixture) -> None:
    """Rules are independent statements. One firing must not consume, suppress or borrow from the
    other — the cooldown is keyed by rule and target, not by scope."""
    await fixture.rule(kind="refusal_rate", threshold=50, min_sample=4, target="use_case")
    await fixture.rule(kind="error_rate", threshold=20, min_sample=4, target="use_case")
    await _seed_rows(fixture.engine, fixture.slug, 5, outcome="rate_limited", status=429)
    await _seed_rows(fixture.engine, fixture.slug, 4, outcome="upstream_error", status=502)
    await _seed_rows(fixture.engine, fixture.slug, 1)

    await _tick(fixture.engine, fixture.slug)

    events = {e["kind"]: e for e in await fixture.events()}
    assert set(events) == {"refusal_rate", "error_rate"}
    # 9 of 10 are not `served`; 4 of 10 are the provider's.
    assert events["refusal_rate"]["observed"] == 90
    assert events["error_rate"]["observed"] == 40


async def test_h9_the_audit_row_of_a_suspended_request_still_names_the_caller(fixture) -> None:
    """A refusal that records nothing about who was refused is a log line, not evidence
    (`FRD-122`)."""
    await fixture.suspend()
    await _wait_until_blocked(fixture, blocked=True)
    await wait_for_row(
        fixture.engine,
        "SELECT id FROM request_logs WHERE use_case = :slug AND outcome = 'suspended'",
        {"slug": fixture.slug},
        timeout=20.0,
    )

    row = [r for r in await fixture.rows() if r["outcome"] == "suspended"][0]

    assert row["subject"] == "integration-probe"
    assert row["use_case"] == fixture.slug
    # Which *system* called, so a leaked key's blast radius can be assessed (`FRD-122` FR-5).
    assert row["credential"]


# =================================================================================================
# I. What this round found, kept as tests
# =================================================================================================


async def test_i1_a_refused_request_is_not_counted_as_unpriced(
    governance_token: str, fixture
) -> None:
    """Found by reading the reporting figure after a round that produced refusals: the console
    said **105** unpriced requests where **5** had actually run on an unpriced model.

    A refused row has a NULL cost for the opposite reason to an unpriced one — nothing was spent
    because nothing ran. Counting both made the "spend is a lower bound" caveat permanent, and a
    warning that is always present is one nobody reads. Unknown is not zero, and zero is not
    unknown.
    """
    await fixture.budget(limit_requests=1)
    async with httpx.AsyncClient(timeout=180.0) as client:
        await _generate(client, fixture)
        await _generate(client, fixture)
        await _generate(client, fixture)

    await wait_for_row(
        fixture.engine,
        "SELECT id FROM request_logs WHERE use_case = :slug AND outcome = 'budget_exceeded'",
        {"slug": fixture.slug},
        timeout=20.0,
    )

    today = datetime.now(UTC).date().isoformat()
    tomorrow = (datetime.now(UTC) + timedelta(days=1)).date().isoformat()
    async with httpx.AsyncClient(timeout=30.0) as client:
        report = await client.get(
            f"{GATEWAY_URL}/v1beta/reporting?from={today}&to={tomorrow}",
            headers={"Authorization": f"Bearer {governance_token}"},
        )

    by_use_case = {row["key"]: row for row in report.json()["by_use_case"]}
    mine = by_use_case[fixture.slug]
    assert mine["requests"] >= 3
    # The local model is priced by the seed, so the served request is priced too — and the
    # refusals must not be counted as anything.
    assert mine["unpriced_requests"] == 0, "a refusal was counted as unpriced traffic"


async def test_i2_every_topic_the_relay_publishes_to_exists(engine) -> None:
    """Found by authoring a rule that never arrived: `aira.anomaly-rules` was created by nothing.

    The failure is silent by construction — Management writes its outbox, the relay publishes, the
    broker drops it, and no error reaches anybody. This is the **second** time (`FRD-405` shipped
    `aira.rate-limits` the same way), so `tools/tests/test_kafka_topics_are_created.py` now keeps
    the three hand-written lists in step with the constants. This case is the other half: the
    topics actually exist in the broker this stack is running.
    """
    from aira_common import kafka

    process = await asyncio.create_subprocess_exec(
        "docker",
        "exec",
        "aira-kafka",
        "/opt/kafka/bin/kafka-topics.sh",
        "--bootstrap-server",
        "localhost:9092",
        "--list",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    stdout, _ = await process.communicate()
    if process.returncode != 0:
        pytest.skip("the broker is not reachable from here")

    present = set(stdout.decode().split())
    declared = {
        value
        for name, value in vars(kafka).items()
        if name.endswith("_TOPIC") and isinstance(value, str) and value.startswith("aira.")
    }
    assert declared <= present, f"never created: {sorted(declared - present)}"


async def test_i3_a_read_only_role_is_refused_by_both_planes_alike(
    governance_token: str,
) -> None:
    """The inconsistency this round found, asserted from both sides at once.

    `it-steuerung` sees every use case and every figure and writes nothing (PRD §154). The gateway
    guarded its kill switch with a *visibility* predicate, so it could stop traffic there while
    Management refused it a global rule — one question, two planes, two answers.
    """
    headers = {"Authorization": f"Bearer {governance_token}"}
    async with httpx.AsyncClient(timeout=30.0) as client:
        gateway = await client.post(
            f"{GATEWAY_URL}/v1beta/suspensions",
            headers=headers,
            json={"target": "subject", "target_value": "somebody"},
        )
        management = await client.post(
            f"{MANAGEMENT_URL}/api/v1/anomaly-rules/",
            headers=headers,
            json={"name": "nope", "kind": "error_rate", "threshold": 50, "min_sample": 5},
        )

    assert gateway.status_code == 403, gateway.text
    assert management.status_code == 403, management.text


async def test_i4_a_security_role_is_allowed_by_both_planes_alike(security_token: str) -> None:
    """The other half of `i3`: the role whose job this is may do both."""
    headers = {"Authorization": f"Bearer {security_token}"}
    name = f"allowed-{uuid.uuid4().hex[:6]}"
    async with httpx.AsyncClient(timeout=30.0) as client:
        gateway = await client.post(
            f"{GATEWAY_URL}/v1beta/suspensions",
            headers=headers,
            json={"target": "subject", "target_value": f"probe-{uuid.uuid4().hex[:6]}"},
        )
        management = await client.post(
            f"{MANAGEMENT_URL}/api/v1/anomaly-rules/",
            headers=headers,
            json={"name": name, "kind": "error_rate", "threshold": 50, "min_sample": 5},
        )
        try:
            assert gateway.status_code == 201, gateway.text
            assert management.status_code == 201, management.text
        finally:
            if gateway.status_code == 201:
                await client.delete(
                    f"{GATEWAY_URL}/v1beta/suspensions/{gateway.json()['id']}", headers=headers
                )
            if management.status_code == 201:
                await client.delete(
                    f"{MANAGEMENT_URL}/api/v1/anomaly-rules/{management.json()['id']}/",
                    headers=headers,
                )


async def test_i5_a_real_request_s_byte_count_reaches_a_payload_rule(fixture) -> None:
    """The whole `payload_size` kind measured a column nothing wrote: the middleware counted the
    bytes, the column existed, and no wire ran between them. The hermetic tests seeded the column
    directly and were green."""
    await fixture.rule(kind="payload_size", threshold=50, parameter=2_000, min_sample=1)
    async with httpx.AsyncClient(timeout=180.0) as client:
        response = await _generate(client, fixture, text_in="y" * 5_000)
    assert response.status_code == 200, response.text

    await wait_for_row(
        fixture.engine,
        "SELECT id FROM request_logs WHERE use_case = :slug AND request_bytes IS NOT NULL",
        {"slug": fixture.slug},
        timeout=20.0,
    )

    await _tick(fixture.engine, fixture.slug)

    events = await fixture.events()
    assert events, "a large real request was invisible to a payload_size rule"
    assert events[0]["observed"] == 100


async def test_i6_a_small_real_request_does_not_trip_a_payload_rule(fixture) -> None:
    """The other direction, so `i5` is not passing because everything fires."""
    await fixture.rule(kind="payload_size", threshold=50, parameter=1_000_000, min_sample=1)
    async with httpx.AsyncClient(timeout=180.0) as client:
        response = await _generate(client, fixture)
    assert response.status_code == 200, response.text

    await wait_for_row(
        fixture.engine,
        "SELECT id FROM request_logs WHERE use_case = :slug AND request_bytes IS NOT NULL",
        {"slug": fixture.slug},
        timeout=20.0,
    )

    await _tick(fixture.engine, fixture.slug)

    assert await fixture.events() == []


async def test_i7_a_refusal_records_the_bytes_it_refused(fixture) -> None:
    """A 413 is recorded (`FRD-122` §12) — and the size is exactly the interesting thing about it,
    since "somebody keeps posting 20 MB" is what a `payload_size` rule is for."""
    await fixture.budget(limit_requests=0)
    async with httpx.AsyncClient(timeout=180.0) as client:
        response = await _generate(client, fixture, text_in="z" * 3_000)

    assert response.status_code == 429, response.text
    row = await wait_for_row(
        fixture.engine,
        "SELECT request_bytes FROM request_logs WHERE use_case = :slug AND outcome ="
        " 'budget_exceeded'",
        {"slug": fixture.slug},
        timeout=20.0,
    )
    assert row.request_bytes is not None
    assert row.request_bytes > 3_000
