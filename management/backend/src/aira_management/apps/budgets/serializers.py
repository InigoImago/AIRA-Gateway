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
        # **No scope names a person any more** (2026-08-14), so a subject sent with one is
        # meaningless — cleared rather than stored, or the uniqueness constraint would let the same
        # rule be created twice under two different empty-ish subjects. Cleared rather than refused
        # because a client that still sends the field is asking for something harmless, and an
        # error would be about a word rather than about the rule.
        subject = ""
        attrs["subject"] = subject
        if (
            attrs.get("limit_cost") is None
            and attrs.get("limit_tokens") is None
            and attrs.get("limit_requests") is None
        ):
            raise serializers.ValidationError("Set at least one limit: cost, tokens, or requests.")
        return attrs
