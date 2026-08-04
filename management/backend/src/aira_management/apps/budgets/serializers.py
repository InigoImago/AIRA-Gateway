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
            "limit_tokens",
            "limit_requests",
            "enabled",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        scope = attrs.get("scope")
        subject = (attrs.get("subject") or "").strip()
        if scope == Budget.MEMBER and not subject:
            raise serializers.ValidationError({"subject": "Required for member-scoped budgets."})
        if scope == Budget.USE_CASE:
            subject = ""
        attrs["subject"] = subject
        if attrs.get("limit_tokens") is None and attrs.get("limit_requests") is None:
            raise serializers.ValidationError("Set at least one of limit_tokens / limit_requests.")
        return attrs
