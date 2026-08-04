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
        ordering = ["created_at"]

    def __str__(self) -> str:
        return f"{self.event_type} -> {self.topic}"
