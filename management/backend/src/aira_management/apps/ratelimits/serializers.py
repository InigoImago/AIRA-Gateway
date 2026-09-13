"""Serializer + validation for rate limits (FRD-405)."""

from __future__ import annotations

from typing import Any

from rest_framework import serializers

from aira_management.apps.ratelimits.models import RateLimit


class RateLimitSerializer(serializers.ModelSerializer[RateLimit]):
    class Meta:
        model = RateLimit
        fields = [
            "id",
            "scope",
            "subject",
            "limit_rpm",
            "burst",
            "enabled",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        # No scope names a person, so a subject is meaningless: cleared rather than stored (the
        # uniqueness constraint would otherwise admit the same rule twice) and not refused, because
        # a client still sending it asks for nothing harmful.
        attrs["subject"] = ""
        # Nothing more is checked here. The bounds live on the model's validators, and a burst
        # **below** the per-minute rate is ordinary traffic shaping (600/min, at most 5 at once),
        # not a mistake.
        return attrs
