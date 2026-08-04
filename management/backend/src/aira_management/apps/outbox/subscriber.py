"""Subscribe use-case change events into the outbox (FRD-204).

Runs inside the request transaction (ATOMIC_REQUESTS), so an outbox row is committed
atomically with the change it describes — the event is never lost.
"""

from __future__ import annotations

from typing import Any

from aira_common.kafka import (
    API_KEY_TOPIC,
    BUDGET_TOPIC,
    MEMBERSHIP_TOPIC,
    PIPELINE_TOPIC,
    USECASE_TOPIC,
)
from aira_management.apps.outbox.models import OutboxEvent

_TOPIC_FOR = {
    "usecase.upserted": USECASE_TOPIC,
    "usecase.deleted": USECASE_TOPIC,
    "membership.upserted": MEMBERSHIP_TOPIC,
    "membership.removed": MEMBERSHIP_TOPIC,
    "api_key.created": API_KEY_TOPIC,
    "api_key.revoked": API_KEY_TOPIC,
    "pipeline.upserted": PIPELINE_TOPIC,
    "pipeline.deleted": PIPELINE_TOPIC,
    "budget.upserted": BUDGET_TOPIC,
    "budget.deleted": BUDGET_TOPIC,
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
        or payload.get("use_case", "")
    )
    OutboxEvent.objects.create(topic=topic, key=key, event_type=event_type, payload=payload)
