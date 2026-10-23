"""A purged use case's keys, members and grants never come back, whatever replays the topics.

The audit's case (A-1): a use case with a key, an administrator and a group grant is retired,
purged thirty days later, and its slug used again by another team. Management's rows went with the
purge by `CASCADE`, without an event; the topics are compacted, so each child's **last** record was
still the one that granted. A rebuilt read-model — or the live one, after its consumer group's
offsets expired — then had the old key active and billed to the new use case, the old
administrator administering it, and nothing in the console to show for either.

**Driven across both planes**: Management's own `retire`, `purge` and outbox writer; the relay's
own publishing loop into a stand-in broker that keeps each record as a consumer receives it;
compaction as Kafka does it — the last record of each topic and key, in offset order — and the
gateway's own consumer applying the result, in several orders, because records on different topics
have none. Twice over: for a use case retired now, and for one retired before retirement ended its
children, which only its purge can still end.
"""

from __future__ import annotations

import asyncio
import random
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

import pytest
from aira_management.apps.anomalies.models import AnomalyRule
from aira_management.apps.anomalies.views import rule_payload
from aira_management.apps.apikeys.models import ApiKey
from aira_management.apps.budgets.models import Budget
from aira_management.apps.outbox.management.commands import relay
from aira_management.apps.outbox.models import OutboxEvent
from aira_management.apps.pipelines.models import PipelineConfig
from aira_management.apps.ratelimits.models import RateLimit
from aira_management.apps.usecases.events import emit
from aira_management.apps.usecases.models import UseCase, UseCaseGroupGrant
from aira_management.apps.usecases.views.grants import _admit
from aira_management.apps.usecases.views.lifecycle import purge, retire
from aira_management.apps.usecases.views.payloads import (
    _api_key_payload,
    _budget_payload,
    _pipeline_payload,
    _rate_limit_payload,
    _snapshot,
)
from django.contrib.auth import get_user_model
from django.utils import timezone
from sqlalchemy import select

from aira_common.apikeys import generate_api_key
from aira_common.kafka import USECASE_TOPIC, AiokafkaProducer
from aira_gateway.auth.service import ApiKeyService
from aira_gateway.consumer.worker import apply_one_message
from aira_gateway.db.base import build_engine, build_sessionmaker, create_all
from aira_gateway.db.models import (
    AnomalyRuleRead,
    BudgetRead,
    PipelineConfigRead,
    RateLimitRead,
    UseCaseGroupRead,
    UseCaseMemberRead,
    UseCaseRead,
)

pytestmark = pytest.mark.django_db


@dataclass
class Record:
    """A record as aiokafka's consumer hands it over."""

    topic: str
    key: bytes
    value: bytes
    headers: list[tuple[str, bytes | None]]
    partition: int = 0
    offset: int = 0


@dataclass
class Broker:
    """Stands in for aiokafka's producer; keeps every record as the consumer will see it."""

    records: list[Record] = field(default_factory=list)
    #: The serializer the producer is built with: what a consumer receives is its output, not the
    #: payload the relay handed over.
    serialize: Callable[[Any], bytes] | None = None

    def built(self, **kwargs: Any) -> Broker:
        self.serialize = kwargs["value_serializer"]
        return self

    async def start(self) -> None:
        return None

    async def stop(self) -> None:
        return None

    async def send_and_wait(
        self, topic: str, value: Any, key: bytes, headers: list[tuple[str, bytes]]
    ) -> None:
        assert self.serialize is not None, "sent before the client was built"
        self.records.append(
            Record(topic, key, self.serialize(value), list(headers), offset=len(self.records))
        )


@dataclass
class History:
    """What happened in Management, as the topics hold it."""

    log: list[Record]
    old_key: str
    old_ids: dict[str, int]

    def compacted(self) -> list[Record]:
        """What Kafka's log cleaner leaves: each topic and key's last record, in offset order."""
        last: dict[tuple[str, bytes], Record] = {}
        for record in self.log:
            last[(record.topic, record.key)] = record
        return sorted(last.values(), key=lambda record: record.offset)


def _published() -> list[Record]:
    broker = Broker()
    producer = AiokafkaProducer("broker:9093", factory=broker.built)
    pending = list(OutboxEvent.objects.pending())
    asyncio.run(relay._publish(producer, pending))
    return broker.records


def _use_case(slug: str) -> UseCase:
    usecase = UseCase.objects.create(slug=slug, name=slug)
    emit("usecase.upserted", _snapshot(usecase))
    return usecase


def _retired_before_retirement_ended_children(usecase: UseCase) -> None:
    """A retirement as it was written before: the use case's own record, and nothing on its
    children's keys."""
    UseCase.objects.filter(pk=usecase.pk).update(deleted_at=timezone.now(), deleted_by="mallory")
    emit("usecase.deleted", _snapshot(UseCase.objects.get(pk=usecase.pk)))


@pytest.fixture(params=["retired now", "retired before"])
def history(request: pytest.FixtureRequest) -> History:
    """Create, govern, retire, purge, and create again — what the topics then hold."""
    people = get_user_model().objects
    mallory, newcomer = people.create(username="mallory"), people.create(username="newcomer")

    old = _use_case("chatbot")
    _admit(old, mallory, "admin")
    UseCaseGroupGrant.objects.create(use_case=old, group_path="/abteilungen/alt", role="admin")
    emit(
        "use_case_group.granted", {"slug": "chatbot", "group": "/abteilungen/alt", "role": "admin"}
    )
    full, prefix, key_hash = generate_api_key()
    key = ApiKey.objects.create(
        use_case=old, owner=mallory, prefix=prefix, key_hash=key_hash, label="old team key",
        expires_at=timezone.now() + timedelta(days=150),
    )  # fmt: skip
    emit("api_key.created", _api_key_payload(key))
    budget = Budget.objects.create(use_case=old, scope="use_case", limit_tokens=1_000_000)
    emit("budget.upserted", _budget_payload(budget, "chatbot"))
    limit = RateLimit.objects.create(use_case=old, scope="use_case", limit_rpm=600)
    emit("ratelimit.upserted", _rate_limit_payload(limit, "chatbot"))
    rule = AnomalyRule.objects.create(use_case=old, name="old", kind="error_rate", threshold=90)
    emit("anomaly_rule.upserted", rule_payload(rule))
    pipeline = PipelineConfig.objects.create(use_case=old, steps=[], fallback_models=["m-old"])
    emit("pipeline.upserted", _pipeline_payload("chatbot", pipeline))
    ids = {"budget": budget.pk, "limit": limit.pk, "rule": rule.pk}

    if request.param == "retired now":
        assert retire(old, by="mallory")
    else:
        _retired_before_retirement_ended_children(old)
    purge(UseCase.objects.get(pk=old.pk))
    new = _use_case("chatbot")
    _admit(new, newcomer, "admin")

    return History(log=_published(), old_key=full, old_ids=ids)


def _verdict(history: History, *passes: list[Record]) -> dict[str, Any]:
    """Apply each pass in turn to one read-model, as the consumer would, and say what it grants
    in the terms the audit asked about."""

    async def run() -> dict[str, Any]:
        engine = build_engine("sqlite+aiosqlite:///:memory:")
        await create_all(engine)
        sessions = build_sessionmaker(engine)
        try:
            for records in passes:
                for record in records:
                    await apply_one_message(sessions, record)
            async with sessions() as session:

                async def rows(model: Any) -> list[Any]:
                    return list((await session.execute(select(model))).scalars())

                live = await session.get(UseCaseRead, "chatbot")
                return {
                    "live": live is not None and live.deleted_at is None,
                    "old_key_authenticates": await ApiKeyService(session).verify(history.old_key)
                    is not None,
                    "members": {(m.subject, m.role) for m in await rows(UseCaseMemberRead)},
                    "groups": {g.group_path for g in await rows(UseCaseGroupRead)},
                    "budgets": {b.id for b in await rows(BudgetRead)},
                    "limits": {r.id for r in await rows(RateLimitRead)},
                    "rules": {r.id for r in await rows(AnomalyRuleRead)},
                    "pipelines": {p.use_case for p in await rows(PipelineConfigRead)},
                }
        finally:
            await engine.dispose()

    return asyncio.run(run())


def _nothing_of_the_old_team(history: History, verdict: dict[str, Any]) -> None:
    assert verdict["live"], "the new use case is served"
    assert verdict["old_key_authenticates"] is False, "the purged use case's key is dead"
    assert verdict["members"] == {("newcomer", "admin")}, "only the new team"
    assert verdict["groups"] == set(), "the purged use case's group grant is gone"
    assert history.old_ids["budget"] not in verdict["budgets"]
    assert history.old_ids["limit"] not in verdict["limits"]
    assert history.old_ids["rule"] not in verdict["rules"]
    assert verdict["pipelines"] == set(), "the new use case has saved no pipeline"


def _orders(records: list[Record]) -> dict[str, list[Record]]:
    """Every order a consumer may meet them in — records on different topics have none."""
    children = [record for record in records if record.topic != USECASE_TOPIC]
    use_cases = [record for record in records if record.topic == USECASE_TOPIC]
    shuffled = list(records)
    random.Random(607).shuffle(shuffled)
    return {
        "as published": records,
        "reversed": list(reversed(records)),
        "children first": [*children, *use_cases],
        "use cases first": [*use_cases, *children],
        "shuffled": shuffled,
    }


@pytest.mark.parametrize(
    "order", ["as published", "reversed", "children first", "use cases first", "shuffled"]
)
def test_a_rebuild_after_purge_and_recreate_brings_nothing_of_the_old_team_back(
    history: History, order: str
) -> None:
    """A fresh read-model built from the compacted topics, the slug used again."""
    replay = _orders(history.compacted())[order]

    _nothing_of_the_old_team(history, _verdict(history, replay))


def test_an_offset_reset_after_purge_brings_nothing_of_the_old_team_back(history: History) -> None:
    """The live read-model, then the consumer reading the topics again from the beginning — once
    compacted, and once before the log cleaner has run, when every old record is still there."""
    _nothing_of_the_old_team(history, _verdict(history, history.log, history.compacted()))
    _nothing_of_the_old_team(history, _verdict(history, history.log, history.log))


def test_the_purge_leaves_each_child_ending_on_its_own_key(history: History) -> None:
    """Why the replays above converge: once compacted, no record of the old team still grants."""
    granting = {
        "api_key.created",
        "membership.upserted",
        "use_case_group.granted",
        "budget.upserted",
        "ratelimit.upserted",
        "anomaly_rule.upserted",
        "pipeline.upserted",
    }
    survivors = [
        (record.topic, record.key.decode())
        for record in history.compacted()
        if dict(record.headers)["event_type"].decode() in granting
        and record.key.decode() != "chatbot|newcomer"
    ]

    assert survivors == []
