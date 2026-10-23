"""Subscribe use-case change events into the outbox (FRD-204).

``emit`` is called from inside the view's ``transaction.atomic()`` block, so the outbox row is
committed atomically with the change it describes — the event is never lost, and never
published for a change that rolled back.

**An event the channel cannot carry is refused here, with the change.** The producer sends at most
`MAX_EVENT_BYTES` of value; a larger event would be written, answered `200`, and then refused by
the broker on every relay run. Raised inside the view's transaction, the refusal rolls the change
back and answers `400` — the one moment somebody can still shorten what they wrote.

**An event with no topic is an error, not a no-op.** Every literal ``emit(...)`` is held to a route
by `test_outbox_routing.py`; one that slipped past it would otherwise be written nowhere, and the
change would reach the console and never the gateway.
"""

from __future__ import annotations

from typing import Any

from django.conf import settings
from rest_framework.exceptions import ValidationError

from aira_common.kafka import (
    ANOMALY_RULE_TOPIC,
    API_KEY_TOPIC,
    BUDGET_TOPIC,
    MAX_EVENT_BYTES,
    MEMBERSHIP_TOPIC,
    MODEL_TOPIC,
    PIPELINE_TOPIC,
    RATE_LIMIT_TOPIC,
    ROLE_TOPIC,
    USECASE_TOPIC,
    encode_event,
    staged,
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
    # Sent when a use case is retired or purged, ending its pipeline on the pipeline's own key
    # (`FRD-607` §4.1b). No view deletes a pipeline: clearing one is a PUT with no steps.
    "pipeline.deleted": PIPELINE_TOPIC,
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


class EventTooLarge(ValidationError):
    """A change whose event the channel to the gateway cannot carry — refused with the change."""

    def __init__(self, event_type: str, size: int) -> None:
        super().__init__(
            {
                "detail": (
                    f"This change cannot be passed on to the gateway: its '{event_type}' event "
                    f"would be {size:,} bytes, and the event channel carries at most "
                    f"{MAX_EVENT_BYTES:,}. Shorten the longest text in it and save again."
                )
            }
        )


class UnroutedEvent(LookupError):
    """An event type no topic carries — a programming error, said where it happens."""


def record_to_outbox(event_type: str, payload: dict[str, Any]) -> None:
    base = _TOPIC_FOR.get(event_type)
    if base is None:
        raise UnroutedEvent(
            f"No topic carries '{event_type}'. Route it in `_TOPIC_FOR`, or the change it "
            "describes would never reach the gateway."
        )
    size = len(encode_event(payload))
    if size > MAX_EVENT_BYTES:
        raise EventTooLarge(event_type, size)
    # Named for this installation's stage when it has one: `aira.t.usecases` on a shared cluster.
    topic = staged(base, getattr(settings, "AIRA_KAFKA_STAGE", "") or "")
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
