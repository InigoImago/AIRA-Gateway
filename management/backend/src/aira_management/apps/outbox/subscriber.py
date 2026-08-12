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
    USECASE_TOPIC,
)
from aira_management.apps.outbox.models import OutboxEvent

_TOPIC_FOR = {
    "usecase.upserted": USECASE_TOPIC,
    "usecase.deleted": USECASE_TOPIC,
    "membership.upserted": MEMBERSHIP_TOPIC,
    "membership.removed": MEMBERSHIP_TOPIC,
    # A group grant travels beside membership: it is the other half of the same question, and the
    # gateway applies both into tables it reads at the same moment (`FRD-209`).
    "use_case_group.granted": MEMBERSHIP_TOPIC,
    "use_case_group.revoked": MEMBERSHIP_TOPIC,
    "api_key.created": API_KEY_TOPIC,
    "api_key.revoked": API_KEY_TOPIC,
    "pipeline.upserted": PIPELINE_TOPIC,
    # No `pipeline.deleted`: Management has no endpoint that deletes a pipeline — clearing one is
    # a PUT with no steps, and a use case's pipeline goes with the use case. The route was here
    # and nothing ever sent it, which is dead configuration that reads as a working path. Found by
    # the reverse half of `test_outbox_routing`, added the same day for the opposite defect.
    #
    # The gateway keeps its `pipeline.deleted` handler: that branch is what forward compatibility
    # means, and removing it would make an older gateway crash on a newer Management that grows
    # the endpoint.
    "budget.upserted": BUDGET_TOPIC,
    "budget.deleted": BUDGET_TOPIC,
    "ratelimit.upserted": RATE_LIMIT_TOPIC,
    "ratelimit.deleted": RATE_LIMIT_TOPIC,
    "model.upserted": MODEL_TOPIC,
    "model.deleted": MODEL_TOPIC,
    "anomaly_rule.upserted": ANOMALY_RULE_TOPIC,
    "anomaly_rule.deleted": ANOMALY_RULE_TOPIC,
}


#: What *else* identifies an entity, where its slug or id does not identify it on its own.
#:
#: A compacted topic keeps the **last** message per key, so the key has to be the entity's whole
#: natural key. A group grant belongs to a (use case, group) pair and a membership to a (use case,
#: user); keyed on the slug alone, the second one written erases the first from the log.
#:
#: That was found for grants in a live round and fixed **for grants only**, as an `if` beside this
#: function — while `membership.upserted`, three lines above it in the same table and carrying the
#: same shape of payload, kept a slug-only key. So every member of a use case shared one key.
#:
#: It bites exactly where it is hardest to see. The live read-model is right, because each event
#: was applied as it arrived; it is a **rebuild** that loses people, when a fresh consumer group
#: reads a compacted topic and finds one member per use case. Disaster recovery is the worst place
#: to keep a latent fault.
#:
#: A table rather than a second `if`, because the third one would have been forgotten too.
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
    OutboxEvent.objects.create(topic=topic, key=key, event_type=event_type, payload=payload)
