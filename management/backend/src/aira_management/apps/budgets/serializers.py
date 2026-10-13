"""Serializer + validation for budgets (FRD-400)."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from rest_framework import serializers

from aira_management.apps.budgets.models import Budget

#: Said for each limit that is zero. The gateway's ledger refuses at `usage >= limit`, so zero
#: refuses every request — the accident the rate limit refuses too.
ZERO_LIMIT = (
    "A limit of zero refuses every request. Leave the field empty for no limit of this kind, or "
    "switch the budget off."
)


class BudgetSerializer(serializers.ModelSerializer[Budget]):
    """A use case's budget, as `/use-cases/<slug>/budgets/` writes it (`FRD-400`)."""

    #: Declared rather than generated: a model default makes DRF treat the field as optional and
    #: leave it **out** of the validated data, and both upserts key on it. Omitted means the
    #: model's own default.
    period = serializers.ChoiceField(choices=Budget.PERIOD_CHOICES, default=Budget.MONTH)

    #: The scopes this route writes, and what to do instead with any other.
    SCOPES: tuple[str, ...] = (Budget.USE_CASE, Budget.EACH_MEMBER)
    OTHER_SCOPE = (
        "An installation budget belongs to no use case: set it on /api/v1/installation-budgets/."
    )

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

    def validate_scope(self, scope: str) -> str:
        """The check constraint holds the same rule underneath; this says it while somebody can
        still act on it, rather than as an `IntegrityError`."""
        if scope not in self.SCOPES:
            raise serializers.ValidationError(self.OTHER_SCOPE)
        return scope

    def validate_limit_cost(self, value: Decimal | None) -> Decimal | None:
        return _not_zero(value)

    def validate_limit_tokens(self, value: int | None) -> int | None:
        return _not_zero(value)

    def validate_limit_requests(self, value: int | None) -> int | None:
        return _not_zero(value)

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


class InstallationBudgetSerializer(BudgetSerializer):
    """The installation's own budget (`FRD-610`), on its own route: the one scope that belongs to
    no use case."""

    SCOPES = (Budget.INSTALLATION,)
    OTHER_SCOPE = "This route sets the installation's own budget, and nothing else."


def _not_zero[N: (int, Decimal)](value: N | None) -> N | None:
    if value is not None and value <= 0:
        raise serializers.ValidationError(ZERO_LIMIT)
    return value
