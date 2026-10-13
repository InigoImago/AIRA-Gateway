"""Transactional outbox for reliable event publication (FRD-204)."""

from __future__ import annotations

from django.db import models
from django.db.models import Q

#: An event still to be sent: neither published nor set aside. One condition for the relay's
#: query, its count and the index that serves both.
PENDING = Q(published_at__isnull=True, parked_at__isnull=True)

#: How long a delivered or parked event stays in the outbox: long enough to compare what was sent
#: with what the gateway applied, and no longer — it is a copy, with names in it. The privacy
#: notice states this number (`apps.privacy.notice`) and the relay applies it.
KEPT_AFTER_DAYS = 30


class OutboxEventQuerySet(models.QuerySet["OutboxEvent"]):
    def pending(self) -> OutboxEventQuerySet:
        """What the relay still has to send, oldest first."""
        return self.filter(PENDING)


class OutboxEvent(models.Model):
    topic = models.CharField(max_length=128)
    key = models.CharField(max_length=255, blank=True)
    event_type = models.CharField(max_length=64)
    payload = models.JSONField()
    #: The W3C trace context of the request that caused this event (`FRD-615`). Stored because the
    #: relay publishes from a separate process with no span of its own. 55 characters by the
    #: specification, wider for future versions; blank where no request caused the event.
    traceparent = models.CharField(max_length=128, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    published_at = models.DateTimeField(null=True, blank=True)
    #: When the relay set this event aside instead of sending it, and why. Only for an event the
    #: broker will never accept: retried at the head of an ordered queue, it would hold back every
    #: event behind it, revocations included. A parked event is never sent — the next change to the
    #: same thing emits a new one — and it is kept as long as a published one.
    parked_at = models.DateTimeField(null=True, blank=True)
    #: A database default as well, so a data migration writing through an older historical model,
    #: which knows no such column, can still insert a row.
    parked_reason = models.TextField(blank=True, default="", db_default="")

    objects = OutboxEventQuerySet.as_manager()

    class Meta:
        #: `id` as well as the timestamp, because an outbox's **order is its meaning**: two events
        #: of one transaction can share a timestamp, and on a compacted topic an upsert-then-delete
        #: is not a delete-then-upsert.
        ordering = ["created_at", "id"]
        indexes = [
            # The relay asks for the oldest pending rows every few seconds; published ones are the
            # bulk of the table and never what it wants.
            models.Index(fields=["created_at", "id"], condition=PENDING, name="outbox_pending"),
        ]

    def __str__(self) -> str:
        return f"{self.event_type} -> {self.topic}"
