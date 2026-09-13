"""Transactional outbox for reliable event publication (FRD-204)."""

from __future__ import annotations

from django.db import models


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

    class Meta:
        #: `id` as well as the timestamp, because an outbox's **order is its meaning**: two events
        #: of one transaction can share a timestamp, and on a compacted topic an upsert-then-delete
        #: is not a delete-then-upsert.
        ordering = ["created_at", "id"]

    def __str__(self) -> str:
        return f"{self.event_type} -> {self.topic}"
