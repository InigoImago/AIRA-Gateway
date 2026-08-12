"""Transactional outbox for reliable event publication (FRD-204)."""

from __future__ import annotations

from django.db import models


class OutboxEvent(models.Model):
    topic = models.CharField(max_length=128)
    key = models.CharField(max_length=255, blank=True)
    event_type = models.CharField(max_length=64)
    payload = models.JSONField()
    created_at = models.DateTimeField(auto_now_add=True)
    published_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        #: `id` as well as the timestamp, because an outbox's **order is its meaning** and
        #: `created_at` alone does not determine one. Two events written in the same transaction —
        #: a use case created and the first grant on it, a budget replaced — can share a timestamp,
        #: and the relay would then publish them in whatever order the database happened to return.
        #: On a compacted topic that is the difference between an upsert followed by a delete and a
        #: delete followed by an upsert.
        #:
        #: The same rule `FRD-502` wrote down for cursor paging ("two rows share a millisecond") and
        #: `FRD-208` for a paged list ("an unordered queryset repeats and skips rows"). Here it
        #: decides what the read-model ends up containing rather than what a page shows.
        ordering = ["created_at", "id"]

    def __str__(self) -> str:
        return f"{self.event_type} -> {self.topic}"
