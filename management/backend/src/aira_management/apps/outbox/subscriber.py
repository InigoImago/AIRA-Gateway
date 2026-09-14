"""Subscribe use-case change events into the outbox (FRD-204).

``emit`` is called from inside the view's ``transaction.atomic()`` block, so the outbox row is
committed atomically with the change it describes — the event is never lost, and never
published for a change that rolled back.
"""

from __future__ import annotations

from typing import Any

from aira_common.kafka import (
    ANOMALY_RULE_TOPIC,
    API_KEY_TOPIC,
    BUDGET_TOPIC,
    MEMBERSHIP_TOPIC,
    MODEL_TOPIC,
    PIPELINE_TOPIC,
    RATE_LIMIT_TOPIC,
    ROLE_TOPIC,
    USECASE_TOPIC,
)
from aira_common.observability import traceparent_from_context
from aira_management.apps.outbox.models import OutboxEvent

_TOPIC_FOR = {
    "usecase.upserted": USECASE_TOPIC,
    "usecase.deleted": USECASE_TOPIC,
    # The second half of `FRD-607`: `deleted` retires and keeps the record, `purged` removes it.
    # One topic, because they are two states of one thing and their order matters — a purge that
    # overtook its own retirement on a second topic would delete a row that was still granting.
    "usecase.purged": USECASE_TOPIC,
    "membership.upserted": MEMBERSHIP_TOPIC,
    "membership.removed": MEMBERSHIP_TOPIC,
    # A group grant travels beside membership: it is the other half of the same question, and the
    # gateway applies both into tables it reads at the same moment (`FRD-209`).
    "use_case_group.granted": MEMBERSHIP_TOPIC,
    "use_case_group.revoked": MEMBERSHIP_TOPIC,
    "api_key.created": API_KEY_TOPIC,
    "api_key.revoked": API_KEY_TOPIC,
    "pipeline.upserted": PIPELINE_TOPIC,
    # No `pipeline.deleted`: Management never deletes a pipeline (clearing one is a PUT with no
    # steps). The gateway keeps its handler for forward compatibility with a Management that will.
    "budget.upserted": BUDGET_TOPIC,
    "budget.deleted": BUDGET_TOPIC,
    "ratelimit.upserted": RATE_LIMIT_TOPIC,
    "ratelimit.deleted": RATE_LIMIT_TOPIC,
    "model.upserted": MODEL_TOPIC,
    "model.deleted": MODEL_TOPIC,
    "anomaly_rule.upserted": ANOMALY_RULE_TOPIC,
    "anomaly_rule.deleted": ANOMALY_RULE_TOPIC,
    # What a role may do and which group confers it (`FRD-614` FR-8), keyed by the role's slug.
    "role.upserted": ROLE_TOPIC,
    "role.removed": ROLE_TOPIC,
}


#: What *else* identifies an entity, where its slug or id does not identify it on its own.
#:
#: A compacted topic keeps the **last** message per key, so the key must be the entity's whole
#: natural key: a membership belongs to (use case, user) and a group grant to (use case, group).
#: Keyed on the slug alone, a **rebuild** from the topic would find one member per use case.
_ALSO_IDENTIFIED_BY = {
    "membership.upserted": "username",
    "membership.removed": "username",
    "use_case_group.granted": "group",
    "use_case_group.revoked": "group",
}


def record_to_outbox(event_type: str, payload: dict[str, Any]) -> None:
    topic = _TOPIC_FOR.get(event_type)
    if topic is None:
        return
    # Compacted topics are keyed by the entity's natural key (budget id first, then prefix/slug).
    key = str(
        payload.get("id")
        or payload.get("prefix")
        or payload.get("slug")
        or payload.get("name")
        or payload.get("use_case", "")
    )
    discriminator = _ALSO_IDENTIFIED_BY.get(event_type)
    if discriminator is not None:
        key = f"{key}|{payload.get(discriminator, '')}"
    OutboxEvent.objects.create(
        topic=topic,
        key=key,
        event_type=event_type,
        payload=payload,
        # Captured here, inside the instrumented request; the relay runs later in another process
        # with no span (`FRD-615`).
        traceparent=traceparent_from_context(),
    )
