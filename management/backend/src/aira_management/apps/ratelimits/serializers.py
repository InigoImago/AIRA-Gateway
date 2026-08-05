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
        scope = attrs.get("scope")
        subject = (attrs.get("subject") or "").strip()
        if scope == RateLimit.MEMBER and not subject:
            raise serializers.ValidationError({"subject": "Required for member-scoped limits."})
        if scope == RateLimit.USE_CASE:
            subject = ""
        attrs["subject"] = subject

        # A burst below the sustained rate is almost certainly a mistake, and a quiet one: the
        # bucket would refuse traffic the configured per-minute figure promises to allow.
        burst = attrs.get("burst") or 0
        if burst and burst < 1:
            raise serializers.ValidationError({"burst": "Must be at least 1 when set."})
        return attrs
