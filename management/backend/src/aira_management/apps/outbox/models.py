"""Transactional outbox for reliable event publication (FRD-204)."""

from __future__ import annotations

from django.db import models


class OutboxEvent(models.Model):
    topic = models.CharField(max_length=128)
    key = models.CharField(max_length=255, blank=True)
    event_type = models.CharField(max_length=64)
    payload = models.JSONField()
    #: The W3C trace context of the request that caused this event (`FRD-615`).
    #:
    #: **An outbox breaks the causal chain on purpose**, and that is what broke the trace. The
    #: console's request writes this row inside its own transaction and returns; a *separate
    #: process* publishes it seconds later, with no span of its own — so the producer read an
    #: empty ambient context and put no `traceparent` on the message at all, on every event, in
    #: every deployment. The producer looked correct, the consumer looked correct, and nothing
    #: joined the two planes.
    #:
    #: 55 characters by the specification; the column is wider so a future version of the format
    #: is a stored string rather than a truncated one that resolves to nothing. Blank where there
    #: was no request behind the event — the seed, a management command — which is honest rather
    #: than missing: there was no caller to attribute it to.
    traceparent = models.CharField(max_length=128, blank=True)
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
