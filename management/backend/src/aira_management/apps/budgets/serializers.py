"""Serializer + validation for budgets (FRD-400)."""

from __future__ import annotations

from typing import Any

from rest_framework import serializers

from aira_management.apps.budgets.models import Budget


class BudgetSerializer(serializers.ModelSerializer[Budget]):
    class Meta:
        model = Budget
        fields = [
            "id",
            "scope",
            "subject",
            "period",
            "limit_cost",
            "limit_tokens",
            "limit_requests",
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
        if (
            attrs.get("limit_cost") is None
            and attrs.get("limit_tokens") is None
            and attrs.get("limit_requests") is None
        ):
            raise serializers.ValidationError("Set at least one limit: cost, tokens, or requests.")
        return attrs
