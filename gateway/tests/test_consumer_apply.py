from collections.abc import AsyncIterator

import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from aira_common.kafka import EVENT_TYPE_HEADER
from aira_gateway.auth import keys
from aira_gateway.auth.service import ApiKeyService
from aira_gateway.consumer.apply import apply_event
from aira_gateway.consumer.worker import decode_event_type
from aira_gateway.db.base import build_engine, build_sessionmaker, create_all
from aira_gateway.db.models import (
    AnomalyRuleRead,
    ApiKey,
    BudgetRead,
    BudgetUsage,
    PipelineConfigRead,
    RateLimitRead,
    RequestLog,
    UseCaseMemberRead,
    UseCaseRead,
)


@pytest_asyncio.fixture
async def make_session() -> AsyncIterator[async_sessionmaker]:
    engine = build_engine("sqlite+aiosqlite:///:memory:")
    await create_all(engine)
    yield build_sessionmaker(engine)
    await engine.dispose()


async def _all(sessionmaker, model):
    async with sessionmaker() as session:
        return list((await session.execute(select(model))).scalars().all())


async def test_usecase_upsert_is_idempotent(make_session) -> None:
    async with make_session() as session:
        await apply_event(session, "usecase.upserted", {"slug": "uc", "name": "N"})
        await apply_event(session, "usecase.upserted", {"slug": "uc", "name": "N2"})
    rows = await _all(make_session, UseCaseRead)
    assert len(rows) == 1
    assert rows[0].name == "N2"


async def test_a_release_the_event_does_not_mention_is_not_a_release_of_nothing(
    make_session,
) -> None:
    """`FRD-308`, and the difference decides whether traffic flows.

    An event from a Management that predates this feature carries no `allowed_models`. Reading
    that absence as "released nothing" would stop **every** use case on a partially upgraded
    stack — a governance control arriving as an outage, which `FRD-500` records as the way a
    control gets switched off for good. Absent means *this event could not say*; an empty list
    means *somebody released nothing*, and only the second is an answer.
    """
    async with make_session() as session:
        await apply_event(session, "usecase.upserted", {"slug": "old", "name": "N"})
        await apply_event(
            session, "usecase.upserted", {"slug": "none", "name": "N", "allowed_models": []}
        )
        await apply_event(
            session, "usecase.upserted", {"slug": "some", "name": "N", "allowed_models": ["b", "a"]}
        )
        # Not a list at all: a malformed payload must not be able to stop a use case either.
        await apply_event(
            session, "usecase.upserted", {"slug": "junk", "name": "N", "allowed_models": "a,b"}
        )

    rows = {row.slug: row.allowed_models for row in await _all(make_session, UseCaseRead)}
    assert rows["old"] is None
    assert rows["none"] == []
    # Sorted and de-duplicated, so a compacted topic replaying the same release does not look like
    # a change to anybody diffing the read-model.
    assert rows["some"] == ["a", "b"]
    assert rows["junk"] is None


async def test_retiring_a_use_case_removes_the_members_and_keeps_the_row(make_session) -> None:
    """**Everything that grants goes; the row that only describes stays** (`FRD-607`).

    The members go because they grant. The `UseCaseRead` row stays because two things read it and
    neither grants anything: `retention.py` takes this use case's own prompt-retention period from
    it, and `payloads.py` uses it to tell *never stored* apart from *expired*. While the row was
    deleted, both silently changed their answer at the moment somebody pressed Delete — the
    retention promise became the installation default, and a refusal reason became a different one.
    """
    async with make_session() as session:
        await apply_event(session, "usecase.upserted", {"slug": "uc", "name": "N"})
        await apply_event(
            session, "membership.upserted", {"slug": "uc", "username": "alice", "role": "admin"}
        )
        await apply_event(session, "usecase.deleted", {"slug": "uc"})

    assert await _all(make_session, UseCaseMemberRead) == []
    rows = await _all(make_session, UseCaseRead)
    assert [row.slug for row in rows] == ["uc"]
    assert rows[0].deleted_at is not None


async def test_only_a_purge_drops_the_row(make_session) -> None:
    """The second, deliberate decision — and the only thing that removes the record.

    Management emits `usecase.purged` for a **Global Administrator** and only for a use case that
    has been retired for `PURGE_AFTER_DAYS`. Idempotent in both directions on purpose: Kafka
    delivery is at-least-once, so a redelivered retirement after a purge must not resurrect a row,
    and a redelivered purge must not fail.
    """
    async with make_session() as session:
        await apply_event(session, "usecase.upserted", {"slug": "uc", "name": "N"})
        await apply_event(session, "usecase.deleted", {"slug": "uc"})
        assert len(await _all(make_session, UseCaseRead)) == 1

        await apply_event(session, "usecase.purged", {"slug": "uc"})
    assert await _all(make_session, UseCaseRead) == []

    async with make_session() as session:
        # Redelivery, both ways round. Neither may raise, and neither may bring the row back.
        await apply_event(session, "usecase.purged", {"slug": "uc"})
        await apply_event(session, "usecase.deleted", {"slug": "uc"})
    assert await _all(make_session, UseCaseRead) == []


async def test_deleting_a_use_case_revokes_the_keys_bound_to_it(make_session) -> None:
    """A use case removed in Management must stop being usable in the gateway.

    Management cascades the deletion in its own database but publishes only ``usecase.deleted``,
    so the gateway is the only place that can clear what hung off it. Left behind, an API key
    keeps authenticating: whoever deleted the use case believes access ended, and it has not.

    The key is *deactivated* rather than removed, for the same reason revocation is terminal
    (ADR-0007): delivery is at-least-once, so a re-delivered ``api_key.created`` must not be able
    to bring it back.
    """
    async with make_session() as session:
        await apply_event(session, "usecase.upserted", {"slug": "uc", "name": "N"})
        await apply_event(
            session,
            "api_key.created",
            {"prefix": "abc", "key_hash": "h", "subject": "alice", "use_case": "uc"},
        )
        await apply_event(session, "usecase.deleted", {"slug": "uc"})

        key = (await session.execute(select(ApiKey).where(ApiKey.prefix == "abc"))).scalar_one()
        assert key.is_active is False

        # And a replayed creation must not resurrect it.
        await apply_event(
            session,
            "api_key.created",
            {"prefix": "abc", "key_hash": "h", "subject": "alice", "use_case": "uc"},
        )
        key = (await session.execute(select(ApiKey).where(ApiKey.prefix == "abc"))).scalar_one()
        assert key.is_active is False


async def test_deleting_a_use_case_clears_its_configuration(make_session) -> None:
    """Otherwise a slug created again later silently inherits the old budgets, limits, pipeline
    and consumption — from a use case whose deletion was somebody's deliberate decision."""
    async with make_session() as session:
        await apply_event(session, "usecase.upserted", {"slug": "uc", "name": "N"})
        await apply_event(
            session,
            "budget.upserted",
            {
                "id": 1,
                "use_case": "uc",
                "scope": "use_case",
                "period": "month",
                "limit_requests": 5,
            },
        )
        await apply_event(
            session,
            "ratelimit.upserted",
            {"id": 1, "use_case": "uc", "scope": "use_case", "limit_rpm": 60},
        )
        await apply_event(session, "pipeline.upserted", {"use_case": "uc", "steps": []})
        session.add(
            BudgetUsage(scope_key="uc:uc", period_key="2026-08", tokens=9, requests=9, cost_nanos=9)
        )
        await session.commit()

        await apply_event(session, "usecase.deleted", {"slug": "uc"})

    assert await _all(make_session, BudgetRead) == []
    assert await _all(make_session, RateLimitRead) == []
    assert await _all(make_session, PipelineConfigRead) == []
    assert await _all(make_session, BudgetUsage) == []


async def test_deleting_a_use_case_keeps_its_request_log(make_session) -> None:
    """The audit trail and the spend history outlive the use case on purpose: they are what a
    later question about what was spent, and by whom, is answered from (FRD-404 §4.1)."""
    async with make_session() as session:
        await apply_event(session, "usecase.upserted", {"slug": "uc", "name": "N"})
        session.add(
            RequestLog(
                subject="alice",
                auth_method="api_key",
                use_case="uc",
                api="gemini",
                operation="generateContent",
                model="mock-1",
                status=200,
                total_tokens=30,
                cost_nanos=85_000,
            )
        )
        await session.commit()

        await apply_event(session, "usecase.deleted", {"slug": "uc"})

    rows = await _all(make_session, RequestLog)
    assert len(rows) == 1
    assert rows[0].cost_nanos == 85_000


async def test_purging_a_use_case_keeps_its_request_log_too(make_session) -> None:
    """The promise `_purge_usecase` makes in its own docstring, which nothing checked.

    Retirement keeping the audit trail was tested above; the **purge** — the second, deliberate
    decision that removes the record itself — was not, and it is the one that matters more. Its
    docstring says *"`request_logs` still stay. They outlive the use case on purpose (`FRD-404`
    §4.1) and outlive its record too"*, and a mutation that made the purge sweep them survived the
    whole suite: nothing anywhere would have noticed the rows going.

    That is precisely the shape `FRD-607` exists against — the threat it names is somebody using a
    use case for the wrong purposes and then deleting it. If the purge could take the evidence with
    it, the two-step design would be a longer path to the same erasure.
    """
    async with make_session() as session:
        await apply_event(session, "usecase.upserted", {"slug": "uc", "name": "N"})
        session.add(
            RequestLog(
                subject="alice",
                auth_method="api_key",
                use_case="uc",
                api="gemini",
                operation="generateContent",
                model="mock-1",
                status=200,
                total_tokens=30,
                cost_nanos=85_000,
            )
        )
        await session.commit()

        await apply_event(session, "usecase.deleted", {"slug": "uc"})
        await apply_event(session, "usecase.purged", {"slug": "uc"})

    assert await _all(make_session, UseCaseRead) == [], "the purge is what removes the record"
    rows = await _all(make_session, RequestLog)
    assert len(rows) == 1, "the audit trail outlives the record of what the use case was"
    assert rows[0].cost_nanos == 85_000


async def test_a_member_scoped_counter_goes_with_the_use_case(make_session) -> None:
    async with make_session() as session:
        await apply_event(session, "usecase.upserted", {"slug": "uc", "name": "N"})
        session.add(
            BudgetUsage(
                scope_key="member:uc:alice",
                period_key="2026-08",
                tokens=1,
                requests=1,
                cost_nanos=1,
            )
        )
        session.add(
            BudgetUsage(
                scope_key="member:other:bob",
                period_key="2026-08",
                tokens=1,
                requests=1,
                cost_nanos=1,
            )
        )
        await session.commit()

        await apply_event(session, "usecase.deleted", {"slug": "uc"})

    remaining = [row.scope_key for row in await _all(make_session, BudgetUsage)]
    assert remaining == ["member:other:bob"]  # another use case's counter is untouched


async def test_membership_upsert_updates_role_then_remove(make_session) -> None:
    async with make_session() as session:
        await apply_event(session, "usecase.upserted", {"slug": "uc", "name": "N"})
        await apply_event(session, "membership.upserted", {"slug": "uc", "username": "alice"})
        await apply_event(
            session, "membership.upserted", {"slug": "uc", "username": "alice", "role": "admin"}
        )
    members = await _all(make_session, UseCaseMemberRead)
    assert len(members) == 1
    assert members[0].role == "admin"

    async with make_session() as session:
        await apply_event(session, "membership.removed", {"slug": "uc", "username": "alice"})
    assert await _all(make_session, UseCaseMemberRead) == []


async def test_unknown_event_is_ignored(make_session) -> None:
    async with make_session() as session:
        await apply_event(session, "something.else", {"x": 1})
    assert await _all(make_session, UseCaseRead) == []


# ---- api keys (FRD-205) -----------------------------------------------------------------


def _created_event(prefix: str, key_hash: str, **over: str) -> dict:
    payload = {
        "prefix": prefix,
        "key_hash": key_hash,
        "subject": "alice",
        "use_case": "demo-uc",
        "label": "cli",
        "status": "active",
    }
    payload.update(over)
    return payload


async def test_api_key_created_then_verify_carries_use_case(make_session) -> None:
    full, prefix, key_hash = keys.generate_api_key()
    async with make_session() as session:
        await apply_event(session, "api_key.created", _created_event(prefix, key_hash))

    async with make_session() as session:
        principal = await ApiKeyService(session).verify(full)
    assert principal is not None
    assert principal.subject == "alice"
    assert principal.method == "api_key"
    assert principal.use_cases == ("demo-uc",)


async def test_the_issuer_travels_beside_the_owner(make_session) -> None:
    """`FRD-604` FR-5. `subject` is who *answers for* the credential and is what lands on every
    audit row; `issued_by` is the human who created it, which for a team's shared key is the fact
    that would otherwise exist only in Management. Carried so an incident can be worked entirely
    from what the gateway holds."""
    _, prefix, key_hash = keys.generate_api_key()
    async with make_session() as session:
        await apply_event(
            session,
            "api_key.created",
            _created_event(prefix, key_hash, subject="svc-chatbot", issued_by="vadim"),
        )

    rows = await _all(make_session, ApiKey)
    assert rows[0].subject == "svc-chatbot"
    assert rows[0].issued_by == "vadim"


async def test_an_event_without_an_issuer_leaves_the_column_empty(make_session) -> None:
    """An older Management sends no such field, and an ordinary key has no second person. Absent
    must stay absent rather than become the owner's name, which would make every key look as
    though somebody issued it for somebody else."""
    _, prefix, key_hash = keys.generate_api_key()
    async with make_session() as session:
        await apply_event(session, "api_key.created", _created_event(prefix, key_hash))

    rows = await _all(make_session, ApiKey)
    assert rows[0].issued_by is None


async def test_api_key_created_is_idempotent_and_updates(make_session) -> None:
    full, prefix, key_hash = keys.generate_api_key()
    async with make_session() as session:
        await apply_event(session, "api_key.created", _created_event(prefix, key_hash))
        await apply_event(
            session,
            "api_key.created",
            _created_event(prefix, key_hash, use_case="other-uc", label="new"),
        )
    rows = await _all(make_session, ApiKey)
    assert len(rows) == 1
    assert rows[0].use_case == "other-uc"
    assert rows[0].label == "new"


async def test_api_key_revoked_stops_verification(make_session) -> None:
    full, prefix, key_hash = keys.generate_api_key()
    async with make_session() as session:
        await apply_event(session, "api_key.created", _created_event(prefix, key_hash))
        await apply_event(session, "api_key.revoked", {"prefix": prefix})
    async with make_session() as session:
        assert await ApiKeyService(session).verify(full) is None


async def test_a_revocation_records_when_it_happened(make_session) -> None:
    """`is_active` is what authentication reads; `revoked_at` is what a review reads.

    Two paths revoke a key. `ApiKeyService.revoke` — the gateway-side one, used by the CLI — sets
    both. This one, which is how **every** revocation from Management arrives, set only the flag,
    so on any deployed system `revoked_at` was NULL for every key that had in fact been revoked: a
    column saying "never revoked" about exactly the ones that were.

    No credential was ever wrongly accepted, because `verify` reads `is_active`. What was broken is
    the record, and the record is the point — "when was this credential revoked" is an incident
    question, and the field that answers it was empty. Found by asking it during a showcase check
    and drawing the wrong conclusion, which is what any reader would have done.
    """
    _full, prefix, key_hash = keys.generate_api_key()
    async with make_session() as session:
        await apply_event(session, "api_key.created", _created_event(prefix, key_hash))
        await apply_event(session, "api_key.revoked", {"prefix": prefix})

    rows = await _all(make_session, ApiKey)

    assert rows[0].is_active is False
    assert rows[0].revoked_at is not None, (
        "a revoked key carries no revocation time, so the audit trail cannot say when access ended"
    )


async def test_an_active_key_carries_no_revocation_time(make_session) -> None:
    """The paired case. An assertion that a field is filled in is defended only by one showing it
    is normally empty — otherwise a column stamped on creation would pass the case above while
    saying every key had been revoked the moment it was issued."""
    _full, prefix, key_hash = keys.generate_api_key()
    async with make_session() as session:
        await apply_event(session, "api_key.created", _created_event(prefix, key_hash))

    rows = await _all(make_session, ApiKey)

    assert rows[0].is_active is True
    assert rows[0].revoked_at is None


async def test_replayed_created_event_does_not_resurrect_a_revoked_key(make_session) -> None:
    """Delivery is at-least-once: a re-delivered `created` must not undo a revocation."""
    full, prefix, key_hash = keys.generate_api_key()
    async with make_session() as session:
        await apply_event(session, "api_key.created", _created_event(prefix, key_hash))
        await apply_event(session, "api_key.revoked", {"prefix": prefix})
        await apply_event(session, "api_key.created", _created_event(prefix, key_hash))
    async with make_session() as session:
        assert await ApiKeyService(session).verify(full) is None


async def test_api_key_revoked_unknown_prefix_is_noop(make_session) -> None:
    async with make_session() as session:
        await apply_event(session, "api_key.revoked", {"prefix": "deadbeef"})
    assert await _all(make_session, ApiKey) == []


# ---- pipelines (FRD-300) ----------------------------------------------------------------


async def test_pipeline_upsert_is_idempotent_and_updates(make_session) -> None:
    async with make_session() as session:
        await apply_event(
            session,
            "pipeline.upserted",
            {"use_case": "uc", "steps": [{"type": "injection_filter"}], "fallback_models": ["a"]},
        )
        await apply_event(
            session,
            "pipeline.upserted",
            {"use_case": "uc", "steps": [], "fallback_models": ["b", "c"]},
        )
    rows = await _all(make_session, PipelineConfigRead)
    assert len(rows) == 1
    assert rows[0].fallback_models == ["b", "c"]
    assert rows[0].steps == []


async def test_pipeline_delete_removes_config(make_session) -> None:
    async with make_session() as session:
        await apply_event(
            session, "pipeline.upserted", {"use_case": "uc", "steps": [], "fallback_models": []}
        )
        await apply_event(session, "pipeline.deleted", {"use_case": "uc"})
    assert await _all(make_session, PipelineConfigRead) == []


# ---- budgets (FRD-400) ------------------------------------------------------------------


def _budget_event(**over: object) -> dict:
    payload = {
        "id": 1,
        "use_case": "uc",
        "scope": "use_case",
        "subject": "",
        "period": "month",
        "limit_tokens": 1000,
        "limit_requests": None,
        "enabled": True,
    }
    payload.update(over)
    return payload


async def test_budget_upsert_is_idempotent_and_updates(make_session) -> None:
    async with make_session() as session:
        await apply_event(session, "budget.upserted", _budget_event())
        await apply_event(session, "budget.upserted", _budget_event(limit_tokens=5000))
    rows = await _all(make_session, BudgetRead)
    assert len(rows) == 1
    assert rows[0].limit_tokens == 5000
    assert rows[0].use_case == "uc"


async def test_budget_delete_removes_row(make_session) -> None:
    async with make_session() as session:
        await apply_event(session, "budget.upserted", _budget_event())
        await apply_event(session, "budget.deleted", {"id": 1})
    assert await _all(make_session, BudgetRead) == []


def test_decode_event_type() -> None:
    assert decode_event_type([(EVENT_TYPE_HEADER, b"usecase.upserted")]) == "usecase.upserted"
    assert decode_event_type([("other", b"x")]) is None
    assert decode_event_type(None) is None


# ---- anomaly rules (FRD-500) -----------------------------------------------------------------


def _rule_event(rule_id: int, **over):
    payload = {
        "id": rule_id,
        "use_case": "demo-uc",
        "name": f"rule-{rule_id}",
        "kind": "refusal_rate",
        "window_minutes": 15,
        "threshold": 40,
        "min_sample": 20,
        "action": "alert",
        "target": "subject",
        "action_minutes": None,
        "enabled": True,
    }
    payload.update(over)
    return payload


async def test_an_anomaly_rule_arrives_and_is_replaced_in_place(make_session) -> None:
    async with make_session() as session:
        await apply_event(session, "anomaly_rule.upserted", _rule_event(7))
        await apply_event(
            session,
            "anomaly_rule.upserted",
            _rule_event(7, threshold=55, action="block", target="credential", action_minutes=60),
        )

    rows = await _all(make_session, AnomalyRuleRead)
    assert len(rows) == 1
    assert rows[0].threshold == 55
    assert rows[0].action == "block"
    assert rows[0].action_minutes == 60


async def test_a_global_rule_stores_no_use_case_at_all(make_session) -> None:
    """NULL means everywhere. An empty string would be a use case named "" — matching nothing
    while looking like it matched everything."""
    async with make_session() as session:
        await apply_event(
            session, "anomaly_rule.upserted", _rule_event(8, use_case=None, kind="new_source_ip")
        )

    rows = await _all(make_session, AnomalyRuleRead)
    assert [row.use_case for row in rows] == [None]


async def test_an_event_without_a_scope_is_skipped_rather_than_made_global(make_session) -> None:
    """An older Management sending no `use_case` key at all would be read as "everywhere" if the
    default were `None` — and widening the reach of a rule that can block traffic is the wrong way
    to be forgiving about a malformed event."""
    async with make_session() as session:
        await apply_event(
            session,
            "anomaly_rule.upserted",
            {"id": 9, "name": "half an event", "kind": "refusal_rate", "threshold": 40},
        )

    assert await _all(make_session, AnomalyRuleRead) == []


async def test_deleting_a_use_case_removes_its_rules_and_leaves_the_global_ones(
    make_session,
) -> None:
    async with make_session() as session:
        await apply_event(session, "anomaly_rule.upserted", _rule_event(10, use_case="doomed-uc"))
        await apply_event(session, "anomaly_rule.upserted", _rule_event(11, use_case=None))
        await apply_event(session, "usecase.deleted", {"slug": "doomed-uc"})

    rows = await _all(make_session, AnomalyRuleRead)
    # A cascade that swept the global rules away would let deleting one use case switch off
    # detection for every other.
    assert [row.id for row in rows] == [11]


async def test_a_deleted_rule_is_gone(make_session) -> None:
    async with make_session() as session:
        await apply_event(session, "anomaly_rule.upserted", _rule_event(12))
        await apply_event(session, "anomaly_rule.deleted", {"id": 12})

    assert await _all(make_session, AnomalyRuleRead) == []
