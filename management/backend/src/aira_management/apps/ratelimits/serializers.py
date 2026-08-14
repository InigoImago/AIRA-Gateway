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
        # **No scope names a person any more** (2026-08-14), so a subject sent with one is
        # meaningless — cleared rather than stored, or the uniqueness constraint would let the same
        # rule be created twice under two different empty-ish subjects. Cleared rather than refused
        # because a client that still sends the field is asking for something harmless, and an
        # error would be about a word rather than about the rule.
        subject = ""
        attrs["subject"] = subject

        # A burst below the sustained rate is almost certainly a mistake, and a quiet one: the
        # bucket would refuse traffic the configured per-minute figure promises to allow.
        burst = attrs.get("burst") or 0
        if burst and burst < 1:
            raise serializers.ValidationError({"burst": "Must be at least 1 when set."})
        return attrs
